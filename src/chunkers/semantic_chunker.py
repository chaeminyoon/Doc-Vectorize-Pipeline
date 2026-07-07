"""
시맨틱 청킹 모듈
문서를 의미 단위로 분할하며, 표는 분리하지 않고 하나의 청크로 유지
"""
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

# LangChain 호환성: 여러 import 경로 시도
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
    except ImportError:
        # LangChain 없이 자체 구현 사용
        RecursiveCharacterTextSplitter = None

from src.parsers.odt_parser import ODTContent, ContentSection


@dataclass
class DocumentChunk:
    """문서 청크"""
    chunk_id: str
    doc_id: str
    chunk_num: int
    chunk_type: str  # 'text' or 'table'
    text: str
    page_num: int = 1  # ODT는 페이지 구분이 어려워 기본값 1
    section_order: int = 0  # 원본 섹션 순서
    embedding: Optional[list[float]] = None

    @property
    def token_count(self) -> int:
        """대략적인 토큰 수 추정 (한글 기준)"""
        # 한글: 글자당 약 1.5토큰, 영문: 단어당 약 1토큰
        korean_chars = len(re.findall(r'[가-힣]', self.text))
        other_chars = len(self.text) - korean_chars
        return int(korean_chars * 1.5 + other_chars * 0.25)


class SemanticDocumentChunker:
    """
    시맨틱 문서 청커

    표(table)는 분리하지 않고 하나의 청크로 유지하고,
    텍스트는 의미 단위로 분할합니다.
    """

    def __init__(
        self,
        chunk_size: int = 1024,
        chunk_overlap: int = 128,
        separators: Optional[list[str]] = None
    ):
        """
        Args:
            chunk_size: 청크 최대 크기 (문자 수)
            chunk_overlap: 청크 오버랩 크기
            separators: 분할 구분자 목록
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # 한국어 문서에 적합한 구분자
        self.separators = separators or [
            "\n\n\n",  # 큰 섹션 구분
            "\n\n",    # 문단 구분
            "\n",      # 줄바꿈
            "다.",     # 문장 끝 (한국어)
            "요.",     # 문장 끝 (한국어)
            "니다.",   # 문장 끝 (한국어)
            ". ",      # 문장 끝 (영어)
            ".",       # 마침표
            " ",       # 공백
            ""         # 문자
        ]

        # LangChain 사용 가능한 경우
        if RecursiveCharacterTextSplitter is not None:
            self._text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=self.separators,
                length_function=self._estimate_tokens,
                is_separator_regex=False
            )
            self._use_langchain = True
        else:
            self._text_splitter = None
            self._use_langchain = False

    def _estimate_tokens(self, text: str) -> int:
        """텍스트의 토큰 수 추정"""
        korean_chars = len(re.findall(r'[가-힣]', text))
        other_chars = len(text) - korean_chars
        return int(korean_chars * 1.5 + other_chars * 0.25)

    def chunk(self, content: ODTContent) -> list[DocumentChunk]:
        """
        ODT 문서를 청크로 분할

        Args:
            content: ODT 파싱 결과

        Returns:
            DocumentChunk 리스트
        """
        chunks = []
        chunk_num = 0

        for section in sorted(content.sections, key=lambda x: x.order):
            if section.section_type == 'table':
                # 테이블은 분리하지 않고 하나의 청크로
                chunks.append(self._create_table_chunk(
                    content.doc_id,
                    section,
                    chunk_num
                ))
                chunk_num += 1
            else:
                # 텍스트는 시맨틱 청킹
                text_chunks = self._chunk_text(
                    content.doc_id,
                    section,
                    chunk_num
                )
                chunks.extend(text_chunks)
                chunk_num += len(text_chunks)

        return chunks

    def _create_table_chunk(
        self,
        doc_id: str,
        section: ContentSection,
        chunk_num: int
    ) -> DocumentChunk:
        """테이블 청크 생성"""
        return DocumentChunk(
            chunk_id=self._generate_chunk_id(doc_id, chunk_num),
            doc_id=doc_id,
            chunk_num=chunk_num,
            chunk_type='table',
            text=section.content,
            section_order=section.order
        )

    def _chunk_text(
        self,
        doc_id: str,
        section: ContentSection,
        start_chunk_num: int
    ) -> list[DocumentChunk]:
        """텍스트 청킹"""
        chunks = []

        # LangChain 또는 자체 구현으로 분할
        if self._use_langchain and self._text_splitter:
            text_chunks = self._text_splitter.split_text(section.content)
        else:
            text_chunks = self._simple_split_text(section.content)

        for i, text in enumerate(text_chunks):
            if not text.strip():
                continue

            chunks.append(DocumentChunk(
                chunk_id=self._generate_chunk_id(doc_id, start_chunk_num + i),
                doc_id=doc_id,
                chunk_num=start_chunk_num + i,
                chunk_type='text',
                text=text.strip(),
                section_order=section.order
            ))

        return chunks

    def _simple_split_text(self, text: str) -> list[str]:
        """LangChain 없이 간단한 텍스트 분할 (fallback)"""
        chunks = []
        current_chunk = ""

        # 문단 단위로 분할
        paragraphs = text.split('\n\n')

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 현재 청크에 추가했을 때 크기 확인
            test_chunk = current_chunk + ("\n\n" if current_chunk else "") + para
            test_size = self._estimate_tokens(test_chunk)

            if test_size <= self.chunk_size:
                current_chunk = test_chunk
            else:
                # 현재 청크 저장
                if current_chunk:
                    chunks.append(current_chunk)

                # 문단이 너무 길면 문장 단위로 분할
                if self._estimate_tokens(para) > self.chunk_size:
                    sentence_chunks = self._split_by_sentences(para)
                    chunks.extend(sentence_chunks[:-1])
                    current_chunk = sentence_chunks[-1] if sentence_chunks else ""
                else:
                    current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _split_by_sentences(self, text: str) -> list[str]:
        """문장 단위로 분할"""
        chunks = []
        current_chunk = ""

        # 한국어/영어 문장 종결 패턴
        sentences = re.split(r'(?<=[.!?다요])\s+', text)

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            test_chunk = current_chunk + (" " if current_chunk else "") + sent
            test_size = self._estimate_tokens(test_chunk)

            if test_size <= self.chunk_size:
                current_chunk = test_chunk
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sent

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _generate_chunk_id(self, doc_id: str, chunk_num: int) -> str:
        """청크 ID 생성"""
        return f"CH-{doc_id[:16]}-{chunk_num:03d}"

    def chunk_with_context(
        self,
        content: ODTContent,
        include_title: bool = True,
        include_doc_info: bool = False
    ) -> list[DocumentChunk]:
        """
        컨텍스트 정보를 포함하여 청킹

        Args:
            content: ODT 파싱 결과
            include_title: 각 청크에 문서 제목 포함 여부
            include_doc_info: 문서 정보 포함 여부

        Returns:
            DocumentChunk 리스트
        """
        chunks = self.chunk(content)

        if include_title or include_doc_info:
            prefix_parts = []

            if include_doc_info:
                prefix_parts.append(f"[문서ID: {content.doc_id}]")

            if include_title:
                prefix_parts.append(f"[제목: {content.title}]")

            prefix = " ".join(prefix_parts)

            for chunk in chunks:
                if chunk.chunk_type == 'text':
                    chunk.text = f"{prefix}\n\n{chunk.text}"

        return chunks


if __name__ == "__main__":
    # 테스트
    from src.parsers.odt_parser import ODTParser

    parser = ODTParser()
    content = parser.parse("../2023/DCT08034D36229EA188C9B0B290F94E0625/attachments/000_공유수면 매립기본계획 반영요청(국가어항 개발계획)에 대한 심의결과 알림.odt")

    chunker = SemanticDocumentChunker(chunk_size=500, chunk_overlap=50)
    chunks = chunker.chunk(content)

    print(f"총 청크 수: {len(chunks)}")
    for i, chunk in enumerate(chunks):
        print(f"\n[{i}] {chunk.chunk_type} (토큰: ~{chunk.token_count})")
        print(f"    {chunk.text[:100]}...")
