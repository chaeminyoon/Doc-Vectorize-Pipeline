"""
ODT 파서 모듈
ODT 파일에서 텍스트와 표를 추출하여 구조화된 형태로 반환
표는 Markdown 테이블 형식으로 변환하여 구조 유지
"""
import zipfile
import hashlib
import logging
import re
import html
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from lxml import etree

from src.quality import safe_path_name, sanitize_path_text


# ODT XML 네임스페이스 정의
NAMESPACES = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
}

logger = logging.getLogger(__name__)
MAX_CONTENT_XML_SIZE = 20 * 1024 * 1024  # 20MB
MAX_XML_COMPRESSION_RATIO = 200.0


@dataclass
class ContentSection:
    """문서 섹션 (텍스트 또는 테이블)"""
    section_type: str  # 'text' or 'table'
    content: str
    order: int  # 원본 문서에서의 순서

    def __repr__(self):
        preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"ContentSection(type={self.section_type}, order={self.order}, content='{preview}')"


@dataclass
class AttachmentInfo:
    """붙임 파일 정보"""
    file_name: str
    file_path: str
    file_order: Optional[int]
    is_main_pdf: bool


@dataclass
class ODTContent:
    """ODT 문서 파싱 결과"""
    doc_id: str
    file_path: str
    title: str
    sections: list[ContentSection] = field(default_factory=list)
    file_hash: str = ""
    attachments: list[AttachmentInfo] = field(default_factory=list)  # 연결된 붙임 파일 목록
    # content.xml에서 추출한 메타데이터
    author_name: Optional[str] = None  # 작성자명 (PO_T16)
    dept_name: Optional[str] = None  # 부서명 (PO_T3에서 '-' 앞부분)
    report_date: Optional[str] = None  # 전결날짜 (PO_T15)

    @property
    def text_sections(self) -> list[str]:
        """텍스트 섹션만 반환"""
        return [s.content for s in self.sections if s.section_type == 'text']

    @property
    def tables(self) -> list[str]:
        """테이블 섹션만 반환 (Markdown 형식)"""
        return [s.content for s in self.sections if s.section_type == 'table']

    @property
    def raw_text(self) -> str:
        """전체 내용을 순서대로 연결하여 반환"""
        return "\n\n".join(s.content for s in sorted(self.sections, key=lambda x: x.order))

    @property
    def full_content_with_markers(self) -> str:
        """섹션 타입 마커와 함께 전체 내용 반환"""
        parts = []
        for section in sorted(self.sections, key=lambda x: x.order):
            if section.section_type == 'table':
                parts.append(f"[TABLE_START]\n{section.content}\n[TABLE_END]")
            else:
                parts.append(section.content)
        return "\n\n".join(parts)


class ODTParser:
    """ODT 문서 파서"""

    AUTHOR_APPROVAL_MARKERS = (
        '주무관', '사무관', '행정사무관', '행정주무관', '서기관', '과장',
        '국장', '부장', '실장', '팀장', '청장', '본부장',
        '전결', '대결', '결재', '협조자',
    )

    AUTHOR_EXCLUDE_WORDS = {
        # 직위/직책 (공무원)
        '담당', '과장', '부장', '국장', '차관', '장관', '실장', '팀장',
        '주무관', '사무관', '서기관', '정책관', '전문위', '조정관',
        '행정사무관', '행정주무관', '행정사무', '행정주무', '본부장', '청장',
        # 군 직위 (장성)
        '대장', '중장', '소장', '준장',
        # 군 직위 (영관)
        '대령', '중령', '소령',
        # 군 직위 (위관)
        '대위', '중위', '소위',
        # 군 직위 (부사관)
        '원사', '상사', '중사', '하사',
        # 군 직위 (병)
        '병장', '상병', '일병', '이병',
        # 결재 관련
        '검토', '협조', '결재', '기안', '승인', '대결', '전결',
        '내부결재', '내부', '협조자',
        # 문서 관련
        '수신', '수신자', '제목', '참조', '기안자', '검토자',
        '등록번호', '등록일자', '문서번호', '시행일자', '접수일자', '결재일자',
        '공개구분', '보존기간', '분류기호', '비공개', '공개여부',
        '시행', '접수', '붙임', '전화', '팩스', '전화번호', '팩스번호',
        # 상태/일반 용어
        '향후', '가결', '대한', '수면', '매립', '계획', '알림',
        '반영', '요청', '심의', '결과', '통보', '회신',
        '행정사항', '행정', '시간', '검토를', '관련', '사항',
        '해양산업', '해양수산', '해양', '산업', '어업', '항만',
        '공유수면', '공유', '면허', '취득',
        # 휴가/상태
        '휴가', '병가', '연가', '출장', '교육',
        # 본문 표/일반 명사 오탐 방지
        '관계', '청장', '보통', '낮음', '높음', '신청한', '허가', '전송', '제대상',
        '하여', '기존', '부분', '결정', '추가허가', '검토사항', '매립면허', '채취허가',
        '검토배경', '매립공사', '구분', '장소', '면적', '목적', '기간', '변경전', '변경후',
        '변경', '간접점용', '조선소', '선박접안', '부선거와', '의장안벽', '면제대상',
        '시설', '관로의', '군산', '신청인', '다음', '호의', '모든', '기준이',
        '고시일자', '변경고시',
    }

    def __init__(self):
        pass

    @staticmethod
    def _secure_xml_parser() -> etree.XMLParser:
        return etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
            recover=False,
        )

    @classmethod
    def _looks_like_author_context(cls, text: str) -> bool:
        return any(marker in text for marker in cls.AUTHOR_APPROVAL_MARKERS)

    @classmethod
    def _normalize_author_token(cls, token: str) -> str:
        normalized = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', token)
        normalized = re.sub(r'[^가-힣]', '', normalized)

        title_markers = (
            '주무관', '사무관', '행정사무관', '행정주무관', '서기관', '과장',
            '국장', '부장', '청장', '본부장', '담당', '팀장', '실장',
            '결재', '전결', '검토', '기안', '협조',
        )
        changed = True
        while changed and normalized:
            changed = False
            for suffix in title_markers:
                if normalized.endswith(suffix) and len(normalized) > len(suffix):
                    normalized = normalized[:-len(suffix)]
                    changed = True
            for prefix in title_markers:
                if normalized.startswith(prefix) and len(normalized) > len(prefix):
                    normalized = normalized[len(prefix):]
                    changed = True

        return normalized

    @classmethod
    def _is_plausible_author_name(cls, name: str) -> bool:
        if not re.fullmatch(r'[가-힣]{2,4}', name):
            return False
        if name in cls.AUTHOR_EXCLUDE_WORDS:
            return False

        org_keywords = (
            '지방', '항만', '해양', '해사', '선원', '물류', '건설', '표지',
            '환경', '계획', '조사', '개발', '정비', '안전', '행정',
            '외교', '국제', '법규', '통상', '산업', '자원', '안보',
            '공사', '사장', '사무소', '센터', '본부',
        )
        if any(keyword in name for keyword in org_keywords):
            return False

        return True

    @classmethod
    def _filter_author_candidates(cls, names: list[str]) -> list[str]:
        filtered_names: list[str] = []
        for name in names:
            if name in cls.AUTHOR_EXCLUDE_WORDS:
                continue
            if name.endswith(('장관', '차관')):
                continue
            if name.endswith(('과', '부', '실', '팀', '국', '청', '처', '원', '센터')) and len(name) > 2:
                continue
            if name.endswith(('공간', '정책', '관리', '계획', '지원', '행정')):
                continue
            filtered_names.append(name)

        seen = set()
        unique_names = []
        for name in filtered_names:
            if name in seen:
                continue
            unique_names.append(name)
            seen.add(name)
        return unique_names[:5]

    @classmethod
    def _extract_names_from_text(cls, text: str) -> list[str]:
        return cls._filter_author_candidates(re.findall(r'[가-힣]{2,4}', text))

    def _extract_author_name_from_tables(self, root) -> Optional[str]:
        tables = root.findall('.//table:table', NAMESPACES)
        for table in reversed(tables):
            rows = table.findall('.//table:table-row', NAMESPACES)
            for idx, row in enumerate(rows):
                row_text = self._extract_row_text(row)
                if not self._looks_like_author_context(row_text):
                    continue

                candidate_texts = []
                if idx + 1 < len(rows):
                    candidate_texts.append(self._extract_row_text(rows[idx + 1]))
                if idx + 2 < len(rows):
                    candidate_texts.append(self._extract_row_text(rows[idx + 2]))

                names = self._extract_names_from_text(' | '.join(text for text in candidate_texts if text))
                if names:
                    return ', '.join(names)

                names = self._extract_names_from_text(row_text)
                if names:
                    return ', '.join(names)
        return None

    def _extract_author_name_from_paragraphs(self, root) -> Optional[str]:
        paragraphs = root.findall('.//text:p', NAMESPACES)
        paragraph_texts = [''.join(p.itertext()).strip() for p in paragraphs]
        paragraph_texts = [text for text in paragraph_texts if text]

        for idx, paragraph_text in enumerate(paragraph_texts):
            if not self._looks_like_author_context(paragraph_text):
                continue

            candidate_texts = []
            if idx + 1 < len(paragraph_texts):
                candidate_texts.append(paragraph_texts[idx + 1])
            names = self._extract_names_from_text(' | '.join(text for text in candidate_texts if text))
            if names:
                return ', '.join(names)

            names = self._extract_names_from_text(paragraph_text)
            if names:
                return ', '.join(names)
        return None

    @staticmethod
    def _sanitize_invalid_xml_ids(content_xml: bytes) -> bytes:
        """xml:id values that violate NCName are rewritten to parse safely."""
        xml_id_pattern = re.compile(rb'xml:id="([^"]+)"')
        ncn_name_pattern = re.compile(rb'^[A-Za-z_][A-Za-z0-9._-]*$')
        changed = False

        def _replace(match: re.Match[bytes]) -> bytes:
            nonlocal changed
            value = match.group(1)
            if ncn_name_pattern.match(value):
                return match.group(0)

            normalized = re.sub(rb"[^A-Za-z0-9._-]", b"_", value)
            if not normalized or not re.match(rb"^[A-Za-z_]", normalized):
                normalized = b"id_" + normalized

            changed = True
            return b'xml:id="' + normalized + b'"'

        sanitized = xml_id_pattern.sub(_replace, content_xml)
        return sanitized if changed else content_xml

    def _parse_xml_root(self, content_xml: bytes, file_path: Path):
        """Try strict parse first, then retry with sanitized xml:id values."""
        parser = self._secure_xml_parser()
        try:
            return etree.fromstring(content_xml, parser=parser)
        except etree.XMLSyntaxError as original_error:
            sanitized = self._sanitize_invalid_xml_ids(content_xml)
            if sanitized == content_xml:
                raise

            try:
                logger.warning("Recovered invalid xml:id in ODT: %s", safe_path_name(file_path))
                return etree.fromstring(sanitized, parser=parser)
            except etree.XMLSyntaxError:
                raise original_error

    def _read_content_xml(self, file_path: Path) -> bytes:
        safe_name = safe_path_name(file_path)
        with zipfile.ZipFile(file_path, 'r') as z:
            try:
                info = z.getinfo('content.xml')
            except KeyError as e:
                raise ValueError(f"content.xml not found in ODT: {safe_name}") from e

            if info.file_size <= 0:
                raise ValueError(f"Invalid content.xml size in ODT: {safe_name}")
            if info.file_size > MAX_CONTENT_XML_SIZE:
                raise ValueError(
                    f"content.xml too large ({info.file_size} bytes > {MAX_CONTENT_XML_SIZE}) in ODT: {safe_name}"
                )
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_XML_COMPRESSION_RATIO:
                    raise ValueError(
                        f"Suspicious content.xml compression ratio ({ratio:.1f}) in ODT: {safe_name}"
                    )

            return z.read(info)

    def parse(self, file_path: str | Path) -> ODTContent:
        """
        ODT 파일을 파싱하여 ODTContent 객체 반환

        Args:
            file_path: ODT 파일 경로

        Returns:
            ODTContent: 파싱된 문서 내용
        """
        file_path = Path(file_path)

        # 문서 ID 추출 (폴더명에서)
        doc_id = self._extract_doc_id(file_path)

        # 파일 해시 계산
        file_hash = self._calculate_file_hash(file_path)

        # 제목 추출 (파일명에서)
        title = self._extract_title(file_path)

        # 같은 폴더의 PDF 파일 목록 수집
        attachments = self._find_pdf_attachments(file_path)

        # ODT 내용 파싱
        sections = self._parse_odt_content(file_path)

        # content.xml에서 메타데이터 추출
        author_name, dept_name, report_date = self._extract_metadata_from_content(file_path)

        return ODTContent(
            doc_id=doc_id,
            file_path=str(file_path),
            title=title,
            sections=sections,
            file_hash=file_hash,
            attachments=attachments,
            author_name=author_name,
            dept_name=dept_name,
            report_date=report_date
        )

    def _extract_doc_id(self, file_path: Path) -> str:
        """파일 경로에서 문서 ID 추출"""
        # 경로 구조: .../DCT.../attachments/000_문서명.odt 또는 .../ENF.../attachments/000_문서명.odt
        parts = file_path.parts
        for part in parts:
            if part.startswith('DCT') or part.startswith('ENF'):
                return part
        return file_path.stem

    def _extract_title(self, file_path: Path) -> str:
        """파일명에서 제목 추출"""
        # 파일명 형식:
        # - 000_제목.odt  -> 앞의 숫자 prefix 제거
        # - 제목_세부.odt -> 제목 전체 유지 (내부 underscore 보존)
        filename = file_path.stem
        if re.match(r"^\d+_", filename):
            raw_title = filename.split("_", 1)[1]
        else:
            raw_title = filename

        # 파일명에 포함된 HTML entity/유사 중점 문자를 정규화
        normalized = html.unescape(raw_title)
        normalized = (
            normalized
            .replace("・", "·")
            .replace("∙", "·")
            .replace("⋅", "·")
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    @staticmethod
    def _extract_file_order_prefix(pdf_basename: str) -> Optional[int]:
        """파일명 prefix가 NNN_ 형태면 NNN을 순서값으로 사용."""
        match = re.match(r"^(\d+)_", pdf_basename)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _assign_missing_file_orders(attachments: list[AttachmentInfo]) -> list[AttachmentInfo]:
        """
        file_order 규칙:
        1) 같은 doc_id 내 본문 PDF(is_main_pdf)는 항상 0
        2) 그 외 PDF는 숫자 prefix가 있으면 그 값을 우선 정렬키로 사용
        3) prefix가 없으면 파일명(ㄱㄴㄷ/유니코드 사전순)으로 뒤에 정렬
        4) 정렬 결과 기준으로 1..N 재부여
        """
        if not attachments:
            return attachments

        main_attachments = [a for a in attachments if a.is_main_pdf]
        other_attachments = [a for a in attachments if not a.is_main_pdf]

        for item in main_attachments:
            item.file_order = 0

        def _other_sort_key(item: AttachmentInfo) -> tuple[int, int, str]:
            stem = Path(item.file_name).stem
            prefix = ODTParser._extract_file_order_prefix(stem)
            if prefix is not None:
                return (0, prefix, stem)
            return (1, 10**9, stem)

        other_attachments.sort(key=_other_sort_key)
        for index, item in enumerate(other_attachments, start=1):
            item.file_order = index

        ordered = main_attachments + other_attachments
        ordered.sort(key=lambda x: (x.file_order, Path(x.file_name).stem))
        return ordered

    def _find_pdf_attachments(self, odt_file_path: Path) -> list[AttachmentInfo]:
        """같은 폴더의 붙임 파일 목록 수집"""
        attachments = []
        parent_dir = odt_file_path.parent
        odt_basename = odt_file_path.stem  # 000_문서명
        sibling_files = sorted(path for path in parent_dir.iterdir() if path.is_file())
        sibling_suffixes_by_stem: dict[str, set[str]] = {}

        for sibling_file in sibling_files:
            sibling_suffixes_by_stem.setdefault(sibling_file.stem, set()).add(
                sibling_file.suffix.lower()
            )

        # 같은 폴더의 PDF와 중복 없는 HWP/HWPX만 수집
        for attachment_file in sibling_files:
            if attachment_file == odt_file_path:
                continue

            suffix = attachment_file.suffix.lower()
            if suffix not in {".pdf", ".hwp", ".hwpx"}:
                continue

            attachment_basename = attachment_file.stem
            sibling_suffixes = sibling_suffixes_by_stem.get(attachment_basename, set())

            if suffix in {".hwp", ".hwpx"} and sibling_suffixes.intersection({".pdf", ".odt"}):
                continue

            # 순서 번호 추출 (NNN_ prefix만 인정)
            file_order = self._extract_file_order_prefix(attachment_basename)

            # ODT와 같은 이름의 PDF만 본문 PDF로 간주
            is_main = (
                suffix == ".pdf"
                and attachment_basename == odt_basename
            )

            attachments.append(AttachmentInfo(
                file_name=attachment_file.name,
                file_path=str(attachment_file),
                file_order=file_order,
                is_main_pdf=is_main
            ))

        return self._assign_missing_file_orders(attachments)

    def _calculate_file_hash(self, file_path: Path) -> str:
        """파일 SHA-256 해시 계산"""
        hash_sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    def _parse_odt_content(self, file_path: Path) -> list[ContentSection]:
        """ODT 파일 내용 파싱"""
        sections = []
        order = 0

        content_xml = self._read_content_xml(file_path)

        root = self._parse_xml_root(content_xml, file_path)

        # office:body/office:text 찾기
        body = root.find('.//office:body/office:text', NAMESPACES)
        if body is None:
            return sections

        # 자식 요소들을 순회하며 텍스트와 테이블 추출
        current_text_parts = []

        for element in body:
            tag = etree.QName(element).localname

            if tag == 'table':
                # 현재까지 누적된 텍스트가 있으면 섹션으로 추가
                if current_text_parts:
                    text_content = '\n'.join(current_text_parts).strip()
                    if text_content:
                        sections.append(ContentSection(
                            section_type='text',
                            content=text_content,
                            order=order
                        ))
                        order += 1
                    current_text_parts = []

                # 테이블을 Markdown으로 변환
                table_md = self._table_to_markdown(element)
                if table_md:
                    sections.append(ContentSection(
                        section_type='table',
                        content=table_md,
                        order=order
                    ))
                    order += 1

            elif tag == 'list':
                # 리스트 처리 (특별한 포맷팅 유지)
                list_text = self._extract_list_text(element)
                if list_text:
                    current_text_parts.append(list_text)

            elif tag == 'section':
                # 섹션 내부 재귀 처리
                # 섹션은 구조적 컨테이너이므로 내부 컨텐츠를 평탄화해서 가져옴
                section_content = self._parse_section_element(element)
                current_text_parts.extend(section_content)

            else:
                # [변경] 그 외 모든 태그 (p, h, draw:frame 등)에서 텍스트 추출
                # Catch-all 전략: 태그 종류에 상관없이 텍스트가 있으면 무조건 가져옴
                text = self._extract_text_from_element(element)
                if text.strip():
                    current_text_parts.append(text)

        # 남은 텍스트 처리
        if current_text_parts:
            text_content = '\n'.join(current_text_parts).strip()
            if text_content:
                sections.append(ContentSection(
                    section_type='text',
                    content=text_content,
                    order=order
                ))

        return sections

    def _extract_text_from_element(self, element) -> str:
        """요소에서 모든 텍스트 추출 (하위 요소 포함)"""
        texts = []

        if element.text:
            texts.append(element.text)

        for child in element:
            # 줄바꿈 처리
            if etree.QName(child).localname == 'line-break':
                texts.append('\n')
            # 탭 처리
            elif etree.QName(child).localname == 'tab':
                texts.append('\t')
            # 공백 처리
            elif etree.QName(child).localname == 's':
                count = int(child.get('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}c', 1))
                texts.append(' ' * count)
            else:
                # 재귀적으로 텍스트 추출
                texts.append(self._extract_text_from_element(child))

            if child.tail:
                texts.append(child.tail)

        return ''.join(texts)

    def _extract_list_text(self, list_element, level: int = 0) -> str:
        """리스트 요소에서 텍스트 추출"""
        items = []
        prefix = '  ' * level

        for item in list_element.findall('.//text:list-item', NAMESPACES):
            # 리스트 아이템 내의 문단
            for p in item.findall('text:p', NAMESPACES):
                text = self._extract_text_from_element(p)
                if text.strip():
                    items.append(f"{prefix}- {text.strip()}")

            # 중첩 리스트
            for nested_list in item.findall('text:list', NAMESPACES):
                nested_text = self._extract_list_text(nested_list, level + 1)
                if nested_text:
                    items.append(nested_text)

        return '\n'.join(items)

    def _parse_section_element(self, section_element) -> list[str]:
        """섹션 요소 내부 텍스트 추출"""
        texts = []

        for child in section_element:
            tag = etree.QName(child).localname

            if tag == 'list':
                list_text = self._extract_list_text(child)
                if list_text:
                    texts.append(list_text)
            elif tag == 'section':
                texts.extend(self._parse_section_element(child))
            else:
                # [변경] 섹션 내부에서도 모든 태그에 대해 텍스트 추출
                text = self._extract_text_from_element(child)
                if text.strip():
                    texts.append(text)

        return texts

    def _table_to_markdown(self, table_element) -> str:
        """테이블 요소를 Markdown 테이블로 변환"""
        rows = []

        # 테이블 행 추출
        for row in table_element.findall('.//table:table-row', NAMESPACES):
            cells = []

            for cell in row.findall('table:table-cell', NAMESPACES):
                # 셀이 반복되는 경우 처리
                repeat_count = int(cell.get(
                    '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated', 1
                ))

                # 셀 내용 추출
                cell_texts = []
                for p in cell.findall('.//text:p', NAMESPACES):
                    text = self._extract_text_from_element(p)
                    if text.strip():
                        cell_texts.append(text.strip())

                cell_content = ' '.join(cell_texts)

                # 마크다운 테이블에서 | 문자 이스케이프
                cell_content = cell_content.replace('|', '\\|')

                # 반복되는 셀 처리
                for _ in range(repeat_count):
                    cells.append(cell_content)

            if cells:
                rows.append(cells)

        if not rows:
            return ""

        # Markdown 테이블 생성
        md_lines = []

        # 헤더 행
        if rows:
            md_lines.append('| ' + ' | '.join(rows[0]) + ' |')
            # 구분선
            md_lines.append('| ' + ' | '.join(['---'] * len(rows[0])) + ' |')

            # 데이터 행
            for row in rows[1:]:
                # 열 개수 맞추기
                while len(row) < len(rows[0]):
                    row.append('')
                md_lines.append('| ' + ' | '.join(row[:len(rows[0])]) + ' |')

        return '\n'.join(md_lines)

    def _extract_metadata_from_content(self, file_path: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        content.xml에서 작성자, 부서명, 날짜 추출

        전략 1: 표 구조 기반 (마지막 표의 특정 행에서 추출)
        전략 2: 텍스트 패턴 기반 (fallback)

        Returns:
            (author_name, dept_name, report_date) 튜플
        """
        import re

        author_name = None
        dept_name = None
        report_date = None

        try:
            content_xml = self._read_content_xml(file_path)
            root = self._parse_xml_root(content_xml, file_path)

            # === 전략 1: 표 구조 기반 추출 ===
            tables = root.findall('.//table:table', NAMESPACES)
            if tables:
                last_table = tables[-1]
                rows = last_table.findall('.//table:table-row', NAMESPACES)

                # 행5: 전결/승인/대결 날짜 (0-indexed로 rows[4])
                # 일부 문서는 행4가 비어있고 행5에 정보가 있으므로 행4와 행5 모두 검색
                for row_idx in [4, 5]:  # 행4, 행5 순서로 검색
                    if report_date:  # 이미 찾았으면 중단
                        break

                    if len(rows) > row_idx:
                        row_text = self._extract_row_text(rows[row_idx])

                        # 패턴 1: 전결/승인/대결 + 날짜
                        # "전결", "국장전결", "승인", "대결" 등 다양한 패턴 처리
                        match = re.search(r'(전결|승인|대결)[^\d]*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})', row_text)
                        if match:
                            # match.groups()는 (키워드, year, month, day) 형태
                            year, month, day = match.groups()[1:4]
                            report_date = f"{year}-{int(month):02d}-{int(day):02d}"
                        else:
                            # Fallback: 키워드 없이 행의 날짜 추출
                            dates = re.findall(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})', row_text)
                            if dates:
                                year, month, day = dates[-1]  # 마지막 날짜
                                report_date = f"{year}-{int(month):02d}-{int(day):02d}"

                if not author_name:
                    author_name = self._extract_author_name_from_tables(root)

                # 행8: 부서-문서번호 (0-indexed로 rows[7])
                if len(rows) > 7:
                    row_text = self._extract_row_text(rows[7])
                    match = re.search(r'([가-힣]+(과|부|팀|실))-\d+', row_text)
                    if match:
                        dept_name = match.group(1)

            # === 전략 2: Fallback - 텍스트 패턴 기반 ===
            # 표 구조에서 추출 실패한 항목만 텍스트 패턴으로 재시도
            if not report_date or not author_name or not dept_name:
                all_spans = root.findall('.//text:span', NAMESPACES)

                for span in all_spans:
                    if not span.text:
                        continue
                    text = span.text.strip()

                    # 전결/승인/대결 날짜
                    if not report_date:
                        if '전결' in text or '승인' in text or '대결' in text:
                            match = re.search(r'(전결|승인|대결)[^\d]*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})', text)
                            if match:
                                year, month, day = match.groups()[1:4]
                                report_date = f"{year}-{int(month):02d}-{int(day):02d}"

                    # 부서명
                    if not dept_name and '-' in text:
                        match = re.search(r'([가-힣]+(과|부|팀|실))-\d+', text)
                        if match:
                            dept_name = match.group(1)

                if not author_name:
                    author_name = self._extract_author_name_from_paragraphs(root)

        except (AttributeError, TypeError, ValueError, etree.XMLSyntaxError, re.error) as e:
            # 메타데이터 추출 실패는 치명적이지 않으므로 기본값으로 반환
            logger.warning("메타데이터 추출 실패: %s", e)

        return author_name, dept_name, report_date

    def _extract_row_text(self, row) -> str:
        """표의 행에서 모든 텍스트 추출"""
        cells = row.findall('table:table-cell', NAMESPACES)
        cell_texts = []
        for cell in cells:
            paragraphs = cell.findall('.//text:p', NAMESPACES)
            for p in paragraphs:
                text = ''.join(p.itertext()).strip()
                if text:
                    cell_texts.append(text)
        return ' | '.join(cell_texts)

def _patched_normalize_author_token(cls, token: str) -> str:
    normalized = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', token)
    normalized = re.sub(r'[^\uAC00-\uD7A3]', '', normalized)

    title_markers = (
        '\uC8FC\uBB34\uAD00', '\uC0AC\uBB34\uAD00', '\uD589\uC815\uC0AC\uBB34\uAD00',
        '\uD589\uC815\uC8FC\uBB34\uAD00', '\uC11C\uAE30\uAD00', '\uACFC\uC7A5',
        '\uAD6D\uC7A5', '\uBD80\uC7A5', '\uCCAD\uC7A5', '\uBCF8\uBD80\uC7A5',
        '\uB2F4\uB2F9', '\uD300\uC7A5', '\uC2E4\uC7A5', '\uACB0\uC7AC',
        '\uC804\uACB0', '\uAC80\uD1A0', '\uAE30\uC548', '\uD611\uC870',
    )
    changed = True
    while changed and normalized:
        changed = False
        for suffix in title_markers:
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                normalized = normalized[:-len(suffix)]
                changed = True
        for prefix in title_markers:
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                normalized = normalized[len(prefix):]
                changed = True

    return normalized


def _patched_is_plausible_author_name(cls, name: str) -> bool:
    if not re.fullmatch(r'[\uAC00-\uD7A3]{2,4}', name):
        return False
    if name in cls.AUTHOR_EXCLUDE_WORDS:
        return False

    exact_non_person_tokens = {
        '\uADC0\uD558',
        '\uD68C\uC7A5',
        '\uC6B4\uC601\uC9C0\uC6D0',
        '\uBB38\uD654\uC7AC',
        '\uC5B4\uCD0C\uC5B4\uD56D',
        '\uC758\uACAC',
        '\uB0B4\uC6A9',
        '\uC8FC\uC694',
        '\uC9C1\uC778',
        '\uC2B9\uC778\uC758',
        '\uC810\uC6A9\uC0AC\uC6A9',
        '\uBCC0\uACBD\uD5C8\uAC00',
        '\uBC95\uB960',
        '\uC124\uCE58',
        '\uC5C6\uC74C',
        '\uACC4\uB958\uC2DC\uC124',
        '\uBC30\uACBD',
        '\uBCC0\uACBD\uC2B9\uC778',
        '\uBD80\uC2DD\uC2DC\uD5D8',
        '\uC0AC\uD558\uAD6C\uCCAD',
        '\uC6B4\uC601',
        '\uC810\uC0AC\uC6A9',
        '\uC900\uC124',
        '\uD0C0\uB2F9\uC131',
        '\uBCF4\uACE0',
        '\uBD80\uC0B0\uC870\uC120',
        '\uC131\uCC3D\uAE30\uC5C5',
        '\uC2E4\uC2DC\uAC04',
        '\uC5B8\uB85C\uB529\uC120',
        '\uD5C8\uAC00\uCDE8\uC18C',
        '\uB3D9\uD574\uC804\uB825',
        '\uD1A0\uAC74',
        '\uC5F0\uC7A5\uC5D0',
        '\uB300\uD574',
        '\uB0B4\uC6A9\uC744',
        '\uD569\uB2C8\uB2E4',
        '\uD3C9\uD0DD\uB300\uD45C',
        '\uAE40\uCCAD\uC218\uC5D0',
        '\uB300\uD558\uC5EC',
        '\uB4DC\uB9BD\uB2C8\uB2E4',
    }
    if name in exact_non_person_tokens:
        return False

    title_like_suffixes = (
        '\uC2DC\uC7A5',
        '\uAD70\uC218',
        '\uAD6C\uCCAD\uC7A5',
        '\uB3C4\uC9C0\uC0AC',
        '\uD68C\uC7A5',
    )
    if any(name.endswith(suffix) for suffix in title_like_suffixes):
        return False

    org_keywords = (
        '\uC9C0\uBC29', '\uD56D\uB9CC', '\uD574\uC591', '\uD574\uC0AC',
        '\uC120\uC6D0', '\uBB3C\uB958', '\uAC74\uC124', '\uD45C\uC9C0',
        '\uD658\uACBD', '\uACC4\uD68D', '\uC870\uC0AC', '\uAC1C\uBC1C',
        '\uC815\uBE44', '\uC548\uC804', '\uD589\uC815', '\uC678\uAD50',
        '\uAD6D\uC81C', '\uBC95\uADDC', '\uD1B5\uC0C1', '\uC0B0\uC5C5',
        '\uC790\uC6D0', '\uC548\uBCF4', '\uACF5\uC0AC', '\uC0AC\uC7A5',
        '\uC0AC\uBB34\uC18C', '\uC13C\uD130', '\uBCF8\uBD80',
    )
    if any(keyword in name for keyword in org_keywords):
        return False

    return True


def _patched_looks_like_author_context(cls, text: str) -> bool:
    normalized = re.sub(r'\s+', ' ', text or '').strip()
    if not normalized:
        return False

    receiver_markers = (
        '\uC218\uC2E0\uC790',
        '\uC218\uC2E0',
        '\uBC1B\uB294\uBD84',
        '\uCC38\uC870',
        '\uACBD\uC720',
    )
    strong_markers = (
        '\uC8FC\uBB34\uAD00',
        '\uC0AC\uBB34\uAD00',
        '\uD589\uC815\uC0AC\uBB34\uAD00',
        '\uD589\uC815\uC8FC\uBB34\uAD00',
        '\uC11C\uAE30\uAD00',
        '\uC804\uACB0',
        '\uB300\uACB0',
        '\uACB0\uC7AC',
        '\uD611\uC870\uC790',
        '\uAE30\uC548',
        '\uAC80\uD1A0',
    )
    role_markers = (
        '\uACFC\uC7A5',
        '\uAD6D\uC7A5',
        '\uBD80\uC7A5',
        '\uC2E4\uC7A5',
        '\uD300\uC7A5',
        '\uCCAD\uC7A5',
        '\uBCF8\uBD80\uC7A5',
    )

    if any(marker in normalized for marker in receiver_markers):
        return any(marker in normalized for marker in strong_markers)

    if any(marker in normalized for marker in strong_markers):
        return True

    return sum(marker in normalized for marker in role_markers) >= 2


def _patched_filter_author_candidates(cls, names: list[str]) -> list[str]:
    filtered_names: list[str] = []
    for name in names:
        if not cls._is_plausible_author_name(name):
            continue
        if name in cls.AUTHOR_EXCLUDE_WORDS:
            continue
        filtered_names.append(name)

    seen = set()
    unique_names = []
    for name in filtered_names:
        if name in seen:
            continue
        unique_names.append(name)
        seen.add(name)
    return unique_names[:5]


def _patched_extract_names_from_text(cls, text: str) -> list[str]:
    raw_tokens = re.split(r'[\s,|/;:]+', text)
    candidate_names: list[str] = []
    for token in raw_tokens:
        normalized = cls._normalize_author_token(token)
        if cls._is_plausible_author_name(normalized):
            candidate_names.append(normalized)
    return cls._filter_author_candidates(candidate_names)


ODTParser._normalize_author_token = classmethod(_patched_normalize_author_token)
ODTParser._is_plausible_author_name = classmethod(_patched_is_plausible_author_name)
ODTParser._looks_like_author_context = classmethod(_patched_looks_like_author_context)
ODTParser._filter_author_candidates = classmethod(_patched_filter_author_candidates)
ODTParser._extract_names_from_text = classmethod(_patched_extract_names_from_text)


def find_odt_files(base_dir: str | Path) -> list[Path]:
    """
    기본 디렉토리에서 모든 ODT 파일 찾기

    Args:
        base_dir: 검색 시작 디렉토리

    Returns:
        ODT 파일 경로 목록
    """
    base_path = Path(base_dir)
    return list(base_path.rglob('*.odt'))


if __name__ == "__main__":
    # 테스트
    import sys

    if len(sys.argv) > 1:
        odt_path = sys.argv[1]
    else:
        odt_path = "../2023/DCT08034D36229EA188C9B0B290F94E0625/attachments/000_공유수면 매립기본계획 반영요청(국가어항 개발계획)에 대한 심의결과 알림.odt"

    parser = ODTParser()
    content = parser.parse(odt_path)

    print(f"문서 ID: {content.doc_id}")
    print(f"제목: {content.title}")
    print(f"파일 해시: {content.file_hash}")
    print(f"섹션 수: {len(content.sections)}")
    print(f"테이블 수: {len(content.tables)}")
    print("\n--- 전체 내용 ---")
    print(content.raw_text[:2000])
