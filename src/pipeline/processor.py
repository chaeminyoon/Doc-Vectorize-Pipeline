"""
메인 파이프라인 모듈
ODT 문서 파싱 → 메타데이터 로드 → 시맨틱 청킹 → 임베딩 → DB 저장
"""
import logging
import re
import html
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Union
from datetime import date, datetime
from tqdm import tqdm
from lxml.etree import XMLSyntaxError
from sqlalchemy.exc import SQLAlchemyError

from src.parsers.odt_parser import ODTParser, ODTContent, find_odt_files
from src.chunkers.semantic_chunker import SemanticDocumentChunker, DocumentChunk as ChunkerDocumentChunk
from src.embeddings.bge_embedder import BGEM3Embedder, MockEmbedder
from src.extractors.permit_field_extractor import PermitFieldExtractor, PermitExtractedFields
from src.metadata.loader import MetadataLoader, FullMetadata
from src.quality import safe_path_name, sanitize_path_text
from src.vectordb.models import Document, DocumentChunk, Attachment, PermitExtraction
from src.vectordb.repository import VectorRepository, IncrementalUpdater

from config import settings


# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

RECOVERABLE_PIPELINE_ERRORS = (
    OSError,
    ValueError,
    TypeError,
    RuntimeError,
    KeyError,
    SQLAlchemyError,
    XMLSyntaxError,
)


@dataclass
class PipelineResult:
    """파이프라인 실행 결과"""
    total_files: int = 0
    processed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    total_chunks: int = 0
    excluded_boilerplate_chunks: int = 0
    errors: list[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0

    def __str__(self):
        return (
            f"PipelineResult(\n"
            f"  total_files={self.total_files},\n"
            f"  processed={self.processed_files},\n"
            f"  skipped={self.skipped_files},\n"
            f"  failed={self.failed_files},\n"
            f"  total_chunks={self.total_chunks},\n"
            f"  excluded_boilerplate_chunks={self.excluded_boilerplate_chunks},\n"
            f"  duration={self.duration_seconds:.2f}s\n"
            f")"
        )


class DocumentPipeline:
    """문서 처리 파이프라인"""

    def __init__(
        self,
        database_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
        use_mock_embedder: bool = False,
        metadata_doc_list: Optional[str] = None,
        chunk_size: int = 1024,
        chunk_overlap: int = 128,
        batch_size: int = 32,
        exclude_boilerplate_table: Optional[bool] = None,
    ):
        """
        Args:
            database_url: PostgreSQL 연결 URL
            embedding_model: 임베딩 모델 이름
            use_mock_embedder: Mock 임베더 사용 여부 (테스트용)
            metadata_doc_list: 문서 목록 Excel 경로 (분류 정보 포함)
            chunk_size: 청크 크기
            chunk_overlap: 청크 오버랩
            batch_size: 배치 크기
            exclude_boilerplate_table: boilerplate 테이블 청크 제외 여부
        """
        self.database_url = database_url or settings.database_url
        self.embedding_model = embedding_model or settings.embedding_model
        self.batch_size = batch_size
        self.exclude_boilerplate_table = (
            settings.exclude_boilerplate_table
            if exclude_boilerplate_table is None
            else exclude_boilerplate_table
        )

        # 컴포넌트 초기화
        self.parser = ODTParser()
        self.permit_extractor = PermitFieldExtractor()
        self.chunker = SemanticDocumentChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        # 임베더 초기화
        if use_mock_embedder:
            self.embedder = MockEmbedder(dimension=1024)
            logger.info("Using MockEmbedder for testing")
        else:
            self.embedder = BGEM3Embedder(
                model_name=self.embedding_model,
                device=settings.embedding_device,
                batch_size=batch_size
            )

        # 메타데이터 로더 초기화 (단일 엑셀 파일)
        self.metadata_loader = MetadataLoader(
            doc_list_path=metadata_doc_list or settings.metadata_doc_list
        )

        # 저장소 초기화
        self.repository = VectorRepository(
            self.database_url,
            ivfflat_probes=settings.ivfflat_probes,
        )
        self.incremental_updater = IncrementalUpdater(self.repository)

        # 메타데이터 로드
        self._metadata_loaded = False

    def _ensure_metadata_loaded(self):
        """메타데이터 로드 확인"""
        if not self._metadata_loaded:
            try:
                self.metadata_loader.load()
                self._metadata_loaded = True
                logger.info("Metadata loaded successfully")
            except (FileNotFoundError, OSError, ValueError, ImportError) as e:
                logger.warning("Failed to load metadata: %s", sanitize_path_text(e))

    def init_database(self, drop_existing: bool = False):
        """데이터베이스 초기화"""
        self.repository.init_db(drop_existing=drop_existing)
        logger.info("Database initialized")

    def process_directory(
        self,
        directory: Union[str, Path],
        incremental: bool = True,
        show_progress: bool = True,
        max_workers: int = 4
    ) -> PipelineResult:
        """
        디렉토리 내 모든 ODT 파일 처리

        Args:
            directory: 처리할 디렉토리 경로
            incremental: 증분 업데이트 모드
            show_progress: 진행률 표시 여부

        Returns:
            PipelineResult
        """
        result = PipelineResult()
        result.start_time = datetime.now()

        self._ensure_metadata_loaded()

        # ODT 파일 찾기
        odt_files = find_odt_files(directory)
        result.total_files = len(odt_files)

        logger.info(f"Found {len(odt_files)} ODT files in {directory}. Processing with {max_workers} workers...")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _worker(odt_path: Path):
            try:
                # ODT 파싱 (CPU Bound)
                content = self.parser.parse(odt_path)

                # 증분 업데이트 확인
                if incremental and not self.incremental_updater.needs_update(
                    content.doc_id, content.file_hash
                ):
                    return {"status": "skipped", "path": odt_path}

                # 파일 처리 (DB Insert, Embedding - IO/GPU Bound)
                chunk_count, excluded_count = self._process_file(content)
                return {
                    "status": "processed",
                    "path": odt_path,
                    "chunk_count": chunk_count,
                    "excluded_count": excluded_count
                }
            except RECOVERABLE_PIPELINE_ERRORS as e:
                return {"status": "error", "path": odt_path, "error": str(e)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_worker, path) for path in odt_files]
            
            # 진행률 표시
            iterator = tqdm(as_completed(futures), total=len(futures), desc="Processing (Parallel)") if show_progress else as_completed(futures)
            
            for future in iterator:
                res = future.result()
                if res["status"] == "skipped":
                    result.skipped_files += 1
                elif res["status"] == "processed":
                    result.processed_files += 1
                    result.total_chunks += res["chunk_count"]
                    result.excluded_boilerplate_chunks += res["excluded_count"]
                elif res["status"] == "error":
                    result.failed_files += 1
                    error_msg = (
                        f"Error processing {safe_path_name(res['path'])}: "
                        f"{sanitize_path_text(res['error'])}"
                    )
                    result.errors.append(error_msg)
                    logger.error(error_msg)

        result.end_time = datetime.now()
        return result

    def process_directories(
        self,
        directories: list[Union[str, Path]],
        incremental: bool = True,
        show_progress: bool = True,
        max_workers: int = 4
    ) -> PipelineResult:
        """여러 디렉토리 처리"""
        combined_result = PipelineResult()
        combined_result.start_time = datetime.now()

        self._ensure_metadata_loaded()
        self.validate_metadata_consistency(directories)

        for directory in directories:
            result = self.process_directory(
                directory,
                incremental=incremental,
                show_progress=show_progress,
                max_workers=max_workers
            )
            combined_result.total_files += result.total_files
            combined_result.processed_files += result.processed_files
            combined_result.skipped_files += result.skipped_files
            combined_result.failed_files += result.failed_files
            combined_result.total_chunks += result.total_chunks
            combined_result.excluded_boilerplate_chunks += result.excluded_boilerplate_chunks
            combined_result.errors.extend(result.errors)

        combined_result.end_time = datetime.now()
        return combined_result

    def validate_metadata_consistency(
        self,
        directories: list[Union[str, Path]],
    ) -> dict[str, set[str]]:
        """Compare ODT doc_ids in data directories with loaded metadata doc_ids."""
        self._ensure_metadata_loaded()

        data_doc_ids: set[str] = set()
        for directory in directories:
            for odt_path in find_odt_files(directory):
                data_doc_ids.add(self.parser._extract_doc_id(Path(odt_path)))

        metadata_doc_ids = self.metadata_loader.get_loaded_doc_ids()
        matched_doc_ids = data_doc_ids & metadata_doc_ids
        missing_in_metadata = data_doc_ids - metadata_doc_ids
        missing_in_data = metadata_doc_ids - data_doc_ids

        logger.info(
            "Metadata consistency check: data_doc_ids=%d, metadata_doc_ids=%d, matched=%d, missing_in_metadata=%d, missing_in_data=%d",
            len(data_doc_ids),
            len(metadata_doc_ids),
            len(matched_doc_ids),
            len(missing_in_metadata),
            len(missing_in_data),
        )
        if missing_in_metadata:
            logger.warning(
                "Missing metadata for %d doc_ids. Examples: %s",
                len(missing_in_metadata),
                ", ".join(sorted(missing_in_metadata)[:10]),
            )
        if missing_in_data:
            logger.warning(
                "Metadata contains %d doc_ids without matching ODT files. Examples: %s",
                len(missing_in_data),
                ", ".join(sorted(missing_in_data)[:10]),
            )

        return {
            "data_doc_ids": data_doc_ids,
            "metadata_doc_ids": metadata_doc_ids,
            "matched_doc_ids": matched_doc_ids,
            "missing_in_metadata": missing_in_metadata,
            "missing_in_data": missing_in_data,
        }

    def process_file(self, file_path: Union[str, Path]) -> PipelineResult:
        """단일 파일 처리"""
        result = PipelineResult()
        result.start_time = datetime.now()
        result.total_files = 1

        self._ensure_metadata_loaded()

        try:
            content = self.parser.parse(file_path)
            chunk_count, excluded_count = self._process_file(content)
            result.processed_files = 1
            result.total_chunks = chunk_count
            result.excluded_boilerplate_chunks = excluded_count
        except RECOVERABLE_PIPELINE_ERRORS as e:
            result.failed_files = 1
            safe_error = sanitize_path_text(e)
            result.errors.append(f"Error: {safe_error}")
            logger.error(
                "Error processing %s: %s",
                safe_path_name(file_path),
                safe_error,
            )

        result.end_time = datetime.now()
        return result

    @staticmethod
    def _is_boilerplate_table_text(text: str) -> bool:
        """공문 템플릿 헤더형 테이블(수신/경유/제목)을 boilerplate로 판단."""
        if not text:
            return False

        normalized = re.sub(r"\s+", " ", text)
        has_header_cells = (
            "| 수신 |" in normalized
            and "| (경유) |" in normalized
            and "| 제목 |" in normalized
        )
        if not has_header_cells:
            return False

        line_count = len([line for line in text.splitlines() if line.strip().startswith("|")])
        return line_count <= 12

    @staticmethod
    def _clean_optional_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @classmethod
    def _normalize_title_text(cls, value: Optional[str]) -> Optional[str]:
        cleaned = cls._clean_optional_text(value)
        if cleaned is None:
            return None

        normalized = html.unescape(cleaned)
        normalized = (
            normalized
            .replace("・", "·")
            .replace("∙", "·")
            .replace("⋅", "·")
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()

        if not normalized:
            return None
        if re.fullmatch(r"[_\-.()\[\]\s]+", normalized):
            return None
        return normalized

    def _resolve_title(self, content: ODTContent, metadata: FullMetadata) -> str:
        content_title = self._normalize_title_text(content.title)
        metadata_title = self._normalize_title_text(metadata.document.title)

        if content_title:
            return content_title
        if metadata_title:
            return metadata_title
        return ""

    @staticmethod
    def _parse_date_value(value: Optional[str]) -> Optional[date]:
        """문자열/날짜 값을 date 객체로 안전하게 변환."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value

        text = str(value).strip()
        if not text:
            return None

        patterns = (
            re.compile(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})"),
            re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
        )
        for pattern in patterns:
            match = pattern.search(text)
            if not match:
                continue
            try:
                year, month, day = match.groups()
                return date(int(year), int(month), int(day))
            except ValueError:
                continue
        return None

    @classmethod
    def _extract_dates_from_text(cls, text: str) -> list[date]:
        """텍스트 내 날짜를 등장 순서대로 추출."""
        if not text:
            return []

        dates: list[date] = []
        patterns = (
            re.compile(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})"),
            re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
        )
        for pattern in patterns:
            for year, month, day in pattern.findall(text):
                try:
                    dates.append(date(int(year), int(month), int(day)))
                except ValueError:
                    continue
        return dates

    @classmethod
    def _guess_report_date_from_text(cls, text: str) -> Optional[date]:
        """
        결재 관련 키워드 라인의 날짜를 우선 사용하고,
        없으면 문서 내 마지막 날짜를 fallback으로 사용.
        """
        if not text:
            return None

        keyword_pattern = re.compile(
            r"(?:결재|전결|대결|확인|기안|결재일|결재일자)"
        )
        keyword_dates: list[date] = []
        all_dates: list[date] = []

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            line_dates = cls._extract_dates_from_text(line)
            if not line_dates:
                continue

            all_dates.extend(line_dates)
            if keyword_pattern.search(line):
                keyword_dates.extend(line_dates)

        if keyword_dates:
            return keyword_dates[-1]
        if all_dates:
            return all_dates[-1]
        return None

    def _resolve_dept_name(self, content: ODTContent, metadata: FullMetadata) -> Optional[str]:
        doc_meta = metadata.document
        for candidate in (
            content.dept_name,
            doc_meta.processor_dept_name,
            doc_meta.sender_dept_name,
            doc_meta.receiver_dept_name,
        ):
            cleaned = self._clean_optional_text(candidate)
            if cleaned:
                return cleaned
        return None

    def _resolve_report_date(self, content: ODTContent, metadata: FullMetadata) -> Optional[date]:
        doc_meta = metadata.document

        resolved = self._parse_date_value(content.report_date)
        if resolved:
            return resolved

        resolved = self._guess_report_date_from_text(content.raw_text)
        if resolved:
            return resolved

        resolved = self._parse_date_value(doc_meta.process_date)
        if resolved:
            return resolved

        return self._parse_date_value(doc_meta.receive_date)

    def _exclude_boilerplate_table_chunks(
        self,
        chunks: list[ChunkerDocumentChunk],
    ) -> list[ChunkerDocumentChunk]:
        """옵션 ON 시 boilerplate table chunk 제거 후 chunk 번호를 재정렬."""
        if not self.exclude_boilerplate_table:
            return chunks

        filtered: list[ChunkerDocumentChunk] = []
        for chunk in chunks:
            if chunk.chunk_type == "table" and self._is_boilerplate_table_text(chunk.text):
                continue
            filtered.append(chunk)

        if not filtered:
            return filtered

        doc_id = filtered[0].doc_id
        for i, chunk in enumerate(filtered):
            chunk.chunk_num = i
            chunk.chunk_id = f"CH-{doc_id[:16]}-{i:03d}"

        return filtered

    def _process_file(self, content: ODTContent) -> tuple[int, int]:
        """
        단일 파일 내부 처리

        Args:
            content: 파싱된 ODT 내용

        Returns:
            (생성된 청크 수, 제외된 boilerplate chunk 수)
        """
        # 메타데이터 조회
        metadata = self.metadata_loader.get_full_metadata(
            content.doc_id, content.title
        )

        # 문서 엔티티 생성
        document = self._create_document_entity(content, metadata)

        # 청킹
        raw_chunks = self.chunker.chunk(content)
        original_chunk_count = len(raw_chunks)
        raw_chunks = self._exclude_boilerplate_table_chunks(raw_chunks)
        excluded_count = original_chunk_count - len(raw_chunks)

        if not raw_chunks:
            logger.warning(f"No chunks created for {content.doc_id}")
            return 0, excluded_count

        # 임베딩 생성
        texts = [chunk.text for chunk in raw_chunks]
        embeddings = self.embedder.embed(texts)

        # 청크 엔티티 생성
        chunk_entities = []
        for i, (chunk, embedding) in enumerate(zip(raw_chunks, embeddings)):
            chunk_entity = DocumentChunk(
                chunk_id=chunk.chunk_id,
                doc_id=content.doc_id,
                page_num=chunk.page_num,
                chunk_num=chunk.chunk_num,
                chunk_type=chunk.chunk_type,
                text=chunk.text,
                embedding=embedding.tolist()
            )
            chunk_entities.append(chunk_entity)

        # DB 저장
        self.repository.upsert_document(document)
        permit_extraction = self._create_permit_extraction_entity(content)
        self.repository.upsert_permit_extraction(permit_extraction)
        self.repository.upsert_chunks(chunk_entities)

        # 붙임 파일 정보 저장
        attachment_entities = None
        if content.attachments:
            attachment_entities = [
                Attachment(
                    doc_id=content.doc_id,
                    file_name=att.file_name,
                    file_path=att.file_path,
                    file_order=att.file_order,
                    is_main_pdf=att.is_main_pdf
                )
                for att in content.attachments
            ]
            self.repository.upsert_attachments(content.doc_id, attachment_entities)
            logger.info(f"Saved {len(attachment_entities)} attachments for {content.doc_id}")

        logger.info(
            f"Processed {content.doc_id}: {len(chunk_entities)} chunks "
            f"(excluded boilerplate: {excluded_count})"
        )
        return len(chunk_entities), excluded_count

    def _create_permit_extraction_entity(self, content: ODTContent) -> PermitExtraction:
        """Create a DB entity from rule-based permit field extraction."""
        fields = self.permit_extractor.extract(content.full_content_with_markers)
        return self._permit_fields_to_entity(content.doc_id, fields)

    @staticmethod
    def _permit_fields_to_entity(doc_id: str, fields: PermitExtractedFields) -> PermitExtraction:
        return PermitExtraction(
            doc_id=doc_id,
            org_nm=fields.org_name,
            ask_org_nm=fields.permittee_name,
            addr=fields.location,
            xtn=fields.area,
            purps_cn=fields.purpose,
            collection_volume=fields.collection_volume,
            permit_start_date=fields.permit_start_date,
            permit_end_date=fields.permit_end_date,
            amount=fields.amount,
            confidence=fields.confidence,
            source_text=fields.source_text,
        )

    def _create_document_entity(
        self,
        content: ODTContent,
        metadata: FullMetadata
    ) -> Document:
        """문서 엔티티 생성"""
        doc_meta = metadata.document
        classification = metadata.classification
        resolved_title = self._resolve_title(content, metadata)
        resolved_dept_name = self._resolve_dept_name(content, metadata)
        resolved_report_date = self._resolve_report_date(content, metadata)

        return Document(
            doc_id=content.doc_id,
            doc_no=doc_meta.doc_no,
            title=resolved_title,
            author_name=content.author_name,  # ODT content.xml에서 추출
            dept_name=resolved_dept_name,
            report_date=resolved_report_date,
            state_code=doc_meta.state_code,
            state_name=doc_meta.state_name,
            open_code=doc_meta.open_code,
            open_name=doc_meta.open_name,
            close_reason=doc_meta.close_reason,
            doc_year=doc_meta.doc_year,
            file_hash=content.file_hash,
            file_path=content.file_path,
            # 분류 정보
            biz_cate=classification.biz_cate,
            biz_sub_cate=classification.biz_sub_cate,
            biz_detail_cate=classification.biz_detail_cate,
            biz_full_path=classification.biz_full_path
        )

    # 고유명사 감지용 접미사 목록
    _PROPER_NOUN_SUFFIXES = (
        # 회사
        '산업', '건설', '중공업', '해운', '조선', '기업', '공사', '에너지',
        '화학', '전력', '항만', '물산', '엔지니어링', '개발', '솔루션',
        '철강', '시멘트', '정유', '석유', '가스', '통신', '전자',
        # 기관
        '시청', '구청', '군청', '도청', '공단', '협회', '재단', '조합',
    )

    # 한국어 조사 패턴 (접미사 감지 전 제거)
    _JOSA_PATTERN = re.compile(
        r'(?:에서|에게|께서|로부터|으로부터|이랑|랑|부터|까지|한테|에게|'
        r'으로|로서|으로서|에서|에는|에도|에만|이|가|을|를|은|는|의|에|로|'
        r'와|과|도|만|서)$'
    )

    @staticmethod
    def _extract_keyword(query: str) -> Optional[str]:
        """
        쿼리에서 고유명사(회사명·기관명) 후보를 추출.
        - 괄호 법인 표기 제거: ㈜·(주)·㈔ 등
        - 한국어 조사 제거 후 접미사 패턴 매칭
        """
        # 법인 표기 정규화
        cleaned = re.sub(r'[㈜㈔]\s*|[\(\（][Jj]주[\)\）]\s*|[\(\（]주[\)\）]\s*', '', query)
        tokens = cleaned.split()
        for token in tokens:
            # 특수문자 제거
            t = re.sub(r'[^\w]', '', token)
            # 조사 제거 (반복 — 중첩 조사 대비)
            for _ in range(3):
                t2 = DocumentPipeline._JOSA_PATTERN.sub('', t)
                if t2 == t:
                    break
                t = t2
            for suffix in DocumentPipeline._PROPER_NOUN_SUFFIXES:
                if t.endswith(suffix) and len(t) > len(suffix):
                    return t
        return None

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: Optional[dict] = None,
        keyword: Optional[str] = None,
        vector_weight: float = 0.7,
    ) -> list[dict]:
        """
        벡터 검색 (고유명사 감지 시 자동으로 hybrid 검색 전환)

        Args:
            query: 검색 쿼리
            top_k: 반환할 결과 수
            filters: 필터 조건
            keyword: 명시적 키워드 (None이면 쿼리에서 자동 추출)
            vector_weight: hybrid 검색 시 벡터 가중치 (0~1)

        Returns:
            검색 결과 리스트 (search_mode 필드 포함)
        """
        # 쿼리 임베딩
        query_embedding = self.embedder.embed_query(query)

        # 키워드 결정: 명시 > 자동 추출
        detected_keyword = keyword or self._extract_keyword(query)

        # 검색 모드 선택
        if detected_keyword:
            logger.info(f"Hybrid search — keyword: '{detected_keyword}'")
            results = self.repository.hybrid_search(
                query_embedding,
                keyword=detected_keyword,
                top_k=top_k,
                vector_weight=vector_weight,
            )
            search_mode = f"hybrid (keyword='{detected_keyword}')"
        else:
            results = self.repository.similarity_search(
                query_embedding,
                top_k=top_k,
                filters=filters,
            )
            search_mode = "vector"

        # DetachedInstanceError 방지를 위해 doc_id로 문서 메타데이터를 별도 조회
        doc_ids = {chunk.doc_id for chunk, _ in results}
        doc_meta_by_id: dict[str, dict] = {}
        if doc_ids:
            with self.repository.get_session() as session:
                doc_rows = session.query(
                    Document.doc_id,
                    Document.title,
                    Document.doc_no,
                    Document.author_name,
                    Document.dept_name,
                    Document.report_date,
                    Document.biz_full_path,
                ).filter(Document.doc_id.in_(doc_ids)).all()
                for row in doc_rows:
                    doc_meta_by_id[row.doc_id] = {
                        'title': row.title,
                        'doc_no': row.doc_no,
                        'author_name': row.author_name,
                        'dept_name': row.dept_name,
                        'report_date': str(row.report_date) if row.report_date else None,
                        'biz_full_path': row.biz_full_path,
                    }

        # 결과 포맷팅
        formatted_results = []
        for chunk, similarity in results:
            if chunk is None:
                logger.warning("Search result contains empty chunk. Skipping result row.")
                continue
            chunk_doc_id = getattr(chunk, "doc_id", None)
            if not chunk_doc_id:
                logger.warning("Search result chunk has no doc_id. Skipping result row.")
                continue
            meta = doc_meta_by_id.get(chunk_doc_id)
            if meta is None:
                meta = {}
            formatted_results.append({
                'chunk_id': chunk.chunk_id,
                'doc_id': chunk_doc_id,
                'title': meta.get('title'),
                'doc_no': meta.get('doc_no'),
                'author_name': meta.get('author_name'),
                'dept_name': meta.get('dept_name'),
                'report_date': meta.get('report_date'),
                'text': chunk.text,
                'chunk_type': chunk.chunk_type,
                'biz_full_path': meta.get('biz_full_path'),
                'similarity': float(similarity),
                'search_mode': search_mode,
            })

        return formatted_results

    def get_stats(self) -> dict:
        """저장소 통계 조회"""
        return self.repository.get_stats()


def create_pipeline(
    database_url: Optional[str] = None,
    use_mock_embedder: bool = False,
    exclude_boilerplate_table: Optional[bool] = None,
) -> DocumentPipeline:
    """파이프라인 팩토리 함수"""
    return DocumentPipeline(
        database_url=database_url,
        use_mock_embedder=use_mock_embedder,
        exclude_boilerplate_table=exclude_boilerplate_table,
    )


if __name__ == "__main__":
    # 테스트
    pipeline = DocumentPipeline(use_mock_embedder=True)

    # 단일 파일 처리 테스트
    result = pipeline.process_file(
        "../2023/DCT08034D36229EA188C9B0B290F94E0625/attachments/000_공유수면 매립기본계획 반영요청(국가어항 개발계획)에 대한 심의결과 알림.odt"
    )
    print(result)
