"""
벡터 저장소 모듈
pgVector를 사용한 문서 및 청크 CRUD 작업
"""
import logging
from typing import Optional
from datetime import datetime
import numpy as np
from sqlalchemy import create_engine, text, cast, Integer
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Document,
    DocumentChunk,
    Attachment,
    PermitExtraction,
    initialize_vector_schema,
    create_ivfflat_index,
)

logger = logging.getLogger(__name__)


class VectorRepository:
    """벡터 저장소 (pgVector)"""
    MAX_TOP_K = 1000
    MAX_KEYWORD_LENGTH = 100
    MAX_IVFFLAT_LISTS = 10000

    def __init__(self, database_url: str, ivfflat_probes: int = 200):
        """
        Args:
            database_url: PostgreSQL 연결 URL
            ivfflat_probes: IVFFlat 검색 시 probes 값
        """
        self.database_url = database_url
        self.ivfflat_probes = self._normalize_positive_int(ivfflat_probes, "ivfflat_probes")
        self.engine = create_engine(database_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)

    @staticmethod
    def _normalize_positive_int(value: int, field_name: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{field_name} must be an integer") from e
        if parsed < 1:
            raise ValueError(f"{field_name} must be >= 1")
        return parsed

    @classmethod
    def _normalize_lists(cls, lists: int) -> int:
        parsed = cls._normalize_positive_int(lists, "lists")
        if parsed > cls.MAX_IVFFLAT_LISTS:
            raise ValueError(f"lists must be <= {cls.MAX_IVFFLAT_LISTS}")
        return parsed

    @classmethod
    def _normalize_top_k(cls, top_k: int) -> int:
        parsed = cls._normalize_positive_int(top_k, "top_k")
        return min(parsed, cls.MAX_TOP_K)

    @staticmethod
    def _escape_like_pattern(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _apply_ivfflat_probes(self, session: Session):
        """현재 세션에 IVFFlat probes 값을 적용"""
        session.execute(text(f"SET ivfflat.probes = {int(self.ivfflat_probes)}"))

    def init_db(self, drop_existing: bool = False):
        """데이터베이스 초기화"""
        initialize_vector_schema(
            self.engine,
            database_url=self.database_url,
            drop_existing=drop_existing,
        )

        logger.info("Database initialized successfully")
        print("Database initialized successfully")

    def create_vector_index(self, lists: int = 100):
        """IVFFlat 벡터 인덱스 생성"""
        safe_lists = self._normalize_lists(lists)
        create_ivfflat_index(
            self.engine,
            database_url=self.database_url,
            lists=safe_lists,
            recreate=True,
        )

        print(f"Vector index created with {safe_lists} lists")

    def get_session(self) -> Session:
        """세션 반환"""
        return self.SessionLocal()

    # ===== 문서 CRUD =====

    def get_document(self, doc_id: str) -> Optional[Document]:
        """문서 조회"""
        with self.get_session() as session:
            return session.query(Document).filter(Document.doc_id == doc_id).first()

    def get_document_hash(self, doc_id: str) -> Optional[str]:
        """문서 파일 해시 조회"""
        with self.get_session() as session:
            result = session.query(Document.file_hash).filter(
                Document.doc_id == doc_id
            ).first()
            return result[0] if result else None

    def document_exists(self, doc_id: str) -> bool:
        """문서 존재 여부 확인"""
        with self.get_session() as session:
            return session.query(Document).filter(
                Document.doc_id == doc_id
            ).count() > 0

    def upsert_document(self, document: Document) -> Document:
        """문서 삽입 또는 업데이트"""
        with self.get_session() as session:
            existing = session.query(Document).filter(
                Document.doc_id == document.doc_id
            ).first()

            if existing:
                # 업데이트
                for key, value in document.__dict__.items():
                    if not key.startswith('_') and key != 'doc_id':
                        setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                session.commit()
                return existing
            else:
                # 삽입
                session.add(document)
                session.commit()
                session.refresh(document)
                return document

    def delete_document(self, doc_id: str) -> bool:
        """문서 및 관련 청크 삭제"""
        with self.get_session() as session:
            result = session.query(Document).filter(
                Document.doc_id == doc_id
            ).delete()
            session.commit()
            return result > 0

    # ===== 허가 추출 정보 CRUD =====

    def upsert_permit_extraction(self, extraction: PermitExtraction) -> PermitExtraction:
        """문서별 허가 추출 정보를 삽입 또는 업데이트."""
        with self.get_session() as session:
            existing = session.query(PermitExtraction).filter(
                PermitExtraction.doc_id == extraction.doc_id
            ).first()

            if existing:
                for key, value in extraction.__dict__.items():
                    if not key.startswith('_') and key != 'doc_id':
                        setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                session.commit()
                return existing

            session.add(extraction)
            session.commit()
            session.refresh(extraction)
            return extraction

    # ===== 붙임 파일 CRUD =====

    def get_attachments(self, doc_id: str) -> list[Attachment]:
        """문서의 붙임 파일 목록 조회"""
        with self.get_session() as session:
            return session.query(Attachment).filter(
                Attachment.doc_id == doc_id
            ).order_by(Attachment.file_order, Attachment.file_name).all()

    def upsert_attachments(self, doc_id: str, attachments: list[Attachment]):
        """문서의 붙임 파일 삽입 또는 업데이트 (기존 삭제 후 새로 삽입)"""
        with self.get_session() as session:
            # 기존 붙임 파일 삭제
            session.query(Attachment).filter(
                Attachment.doc_id == doc_id
            ).delete()

            # 새 붙임 파일 삽입
            if attachments:
                session.bulk_save_objects(attachments)

            session.commit()

    def delete_attachments(self, doc_id: str) -> int:
        """문서의 모든 붙임 파일 삭제"""
        with self.get_session() as session:
            result = session.query(Attachment).filter(
                Attachment.doc_id == doc_id
            ).delete()
            session.commit()
            return result

    # ===== 청크 CRUD =====

    def get_chunks_by_doc(self, doc_id: str) -> list[DocumentChunk]:
        """문서의 모든 청크 조회"""
        with self.get_session() as session:
            return session.query(DocumentChunk).filter(
                DocumentChunk.doc_id == doc_id
            ).order_by(DocumentChunk.chunk_num).all()

    def delete_chunks_by_doc(self, doc_id: str) -> int:
        """문서의 모든 청크 삭제"""
        with self.get_session() as session:
            result = session.query(DocumentChunk).filter(
                DocumentChunk.doc_id == doc_id
            ).delete()
            session.commit()
            return result

    def bulk_insert_chunks(self, chunks: list[DocumentChunk]):
        """청크 대량 삽입"""
        with self.get_session() as session:
            session.bulk_save_objects(chunks)
            session.commit()

    def upsert_chunks(self, chunks: list[DocumentChunk]):
        """청크 삽입 또는 업데이트 (기존 청크 삭제 후 새로 삽입)"""
        if not chunks:
            return

        doc_id = chunks[0].doc_id

        with self.get_session() as session:
            # 기존 청크 삭제
            session.query(DocumentChunk).filter(
                DocumentChunk.doc_id == doc_id
            ).delete()

            # 새 청크 삽입
            session.bulk_save_objects(chunks)
            session.commit()

    # ===== 벡터 검색 =====

    def similarity_search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        threshold: float = 0.0,
        filters: Optional[dict] = None
    ) -> list[tuple[DocumentChunk, float]]:
        """
        벡터 유사도 검색

        Args:
            query_embedding: 쿼리 임베딩 벡터
            top_k: 반환할 결과 수
            threshold: 최소 유사도 임계값
            filters: 필터 조건 (biz_cate, doc_year 등)

        Returns:
            (청크, 유사도) 튜플 리스트
        """
        top_k = self._normalize_top_k(top_k)
        with self.get_session() as session:
            self._apply_ivfflat_probes(session)
            # 기본 쿼리
            query = session.query(
                DocumentChunk,
                (1 - DocumentChunk.embedding.cosine_distance(query_embedding.tolist())).label('similarity')
            )

            # 필터 적용
            if filters:
                # biz_cate 필터는 Document 테이블에서 JOIN
                if 'biz_cate' in filters:
                    query = query.join(Document, DocumentChunk.doc_id == Document.doc_id)
                    query = query.filter(Document.biz_cate == filters['biz_cate'])
                if 'doc_id' in filters:
                    query = query.filter(DocumentChunk.doc_id == filters['doc_id'])
                if 'chunk_type' in filters:
                    query = query.filter(DocumentChunk.chunk_type == filters['chunk_type'])

            # 유사도 임계값 필터
            if threshold > 0:
                query = query.filter(
                    (1 - DocumentChunk.embedding.cosine_distance(query_embedding.tolist())) >= threshold
                )

            # 정렬 및 제한
            results = query.order_by(
                DocumentChunk.embedding.cosine_distance(query_embedding.tolist())
            ).limit(top_k).all()

            return [(chunk, similarity) for chunk, similarity in results]

    def hybrid_search(
        self,
        query_embedding: np.ndarray,
        keyword: str,
        top_k: int = 10,
        vector_weight: float = 0.7
    ) -> list[tuple[DocumentChunk, float]]:
        """
        하이브리드 검색 (벡터 + 키워드)

        Args:
            query_embedding: 쿼리 임베딩
            keyword: 검색 키워드
            top_k: 반환할 결과 수
            vector_weight: 벡터 유사도 가중치 (0-1)

        Returns:
            (청크, 점수) 튜플 리스트
        """
        top_k = self._normalize_top_k(top_k)
        if not 0.0 <= float(vector_weight) <= 1.0:
            raise ValueError("vector_weight must be between 0 and 1")

        keyword = (keyword or "").strip()
        if not keyword:
            return self.similarity_search(query_embedding=query_embedding, top_k=top_k)
        keyword = keyword[: self.MAX_KEYWORD_LENGTH]
        escaped_keyword = self._escape_like_pattern(keyword)

        # ✅ .ilike()는 값을 바인딩 파라미터로 처리 → SQL Injection 불가
        keyword_match = cast(
            DocumentChunk.text.ilike(f"%{escaped_keyword}%", escape="\\"),
            Integer
        )
        vector_score = (
            1 - DocumentChunk.embedding.cosine_distance(query_embedding.tolist())
        )
        score = (
            vector_weight * vector_score +
            (1 - vector_weight) * keyword_match
        ).label('score')

        with self.get_session() as session:
            self._apply_ivfflat_probes(session)
            # 벡터 유사도 + 텍스트 매칭 점수 결합
            results = session.query(DocumentChunk, score) \
                .order_by(score.desc()) \
                .limit(top_k).all()

            return [(chunk, float(s)) for chunk, s in results]

    # ===== 통계 =====

    def get_document_count(self) -> int:
        """총 문서 수"""
        with self.get_session() as session:
            return session.query(Document).count()

    def get_chunk_count(self) -> int:
        """총 청크 수"""
        with self.get_session() as session:
            return session.query(DocumentChunk).count()

    def get_stats(self) -> dict:
        """저장소 통계"""
        with self.get_session() as session:
            doc_count = session.query(Document).count()
            chunk_count = session.query(DocumentChunk).count()

            # 청크 타입별 개수
            from sqlalchemy import func
            type_counts = session.query(
                DocumentChunk.chunk_type,
                func.count(DocumentChunk.chunk_id)
            ).group_by(DocumentChunk.chunk_type).all()

            return {
                'document_count': doc_count,
                'chunk_count': chunk_count,
                'chunks_by_type': dict(type_counts)
            }


# 증분 업데이트 헬퍼
class IncrementalUpdater:
    """증분 업데이트 관리자"""

    def __init__(self, repository: VectorRepository):
        self.repository = repository

    def needs_update(self, doc_id: str, file_hash: str) -> bool:
        """
        문서 업데이트 필요 여부 확인

        Args:
            doc_id: 문서 ID
            file_hash: 현재 파일 해시

        Returns:
            업데이트 필요시 True
        """
        stored_hash = self.repository.get_document_hash(doc_id)
        if stored_hash is None:
            return True  # 새 문서
        return stored_hash != file_hash  # 해시 불일치시 업데이트 필요

    def get_documents_to_process(
        self,
        file_hashes: dict[str, str]
    ) -> tuple[list[str], list[str]]:
        """
        처리할 문서 분류

        Args:
            file_hashes: {doc_id: file_hash} 딕셔너리

        Returns:
            (새 문서 ID 목록, 업데이트 필요 문서 ID 목록)
        """
        new_docs = []
        update_docs = []

        for doc_id, file_hash in file_hashes.items():
            stored_hash = self.repository.get_document_hash(doc_id)
            if stored_hash is None:
                new_docs.append(doc_id)
            elif stored_hash != file_hash:
                update_docs.append(doc_id)

        return new_docs, update_docs
