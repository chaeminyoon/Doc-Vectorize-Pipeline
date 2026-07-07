"""
SQLAlchemy 모델 정의
pgVector 확장을 사용한 벡터 저장
"""
from datetime import datetime
from typing import Optional
import logging
from sqlalchemy import (
    Column, String, Text, Integer, Date, DateTime,
    ForeignKey, Boolean, Float, create_engine, text
)
from sqlalchemy.exc import SQLAlchemyError, ArgumentError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, relationship
from pgvector.sqlalchemy import Vector

Base = declarative_base()
logger = logging.getLogger(__name__)


def _mask_database_url(database_url: str) -> str:
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except (TypeError, ValueError, ArgumentError):
        return "<invalid-database-url>"


def _get_engine_dialect_name(engine) -> str:
    dialect = getattr(engine, "dialect", None)
    return str(getattr(dialect, "name", "")).strip().lower()


def ensure_postgresql_engine(engine, database_url: str) -> None:
    """Fail closed when an unsupported database backend is supplied."""
    dialect_name = _get_engine_dialect_name(engine)
    if dialect_name != "postgresql":
        masked_url = _mask_database_url(database_url)
        raise ValueError(
            "PostgreSQL with pgvector is required for vector storage. "
            f"Got dialect '{dialect_name or 'unknown'}' for {masked_url}"
        )


def ensure_vector_extension(engine, database_url: str) -> None:
    """Create the pgvector extension before any schema operations use Vector."""
    ensure_postgresql_engine(engine, database_url)
    masked_url = _mask_database_url(database_url)

    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
    except SQLAlchemyError as exc:
        raise RuntimeError(
            f"Failed to enable pgvector extension for {masked_url}"
        ) from exc


def initialize_vector_schema(engine, database_url: str, drop_existing: bool = False) -> None:
    """Initialize schema only after pgvector is guaranteed to exist."""
    ensure_vector_extension(engine, database_url)

    if drop_existing:
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)


def create_ivfflat_index(
    engine,
    database_url: str,
    *,
    lists: int = 100,
    recreate: bool = False,
    if_not_exists: bool = False,
) -> None:
    """Create the IVFFlat index after validating the backend."""
    ensure_postgresql_engine(engine, database_url)

    with engine.connect() as conn:
        if recreate:
            conn.execute(text("DROP INDEX IF EXISTS idx_chunks_embedding"))

        if if_not_exists:
            conn.execute(text(f"""
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding
                ON document_chunks
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = {int(lists)})
            """))
        else:
            conn.execute(text(f"""
                CREATE INDEX idx_chunks_embedding
                ON document_chunks
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = {int(lists)})
            """))
        conn.commit()


class Document(Base):
    """문서 테이블"""
    __tablename__ = "documents"

    doc_id = Column(String(64), primary_key=True)
    doc_no = Column(String(100), nullable=True)
    title = Column(Text, nullable=True)
    author_name = Column(String(100), nullable=True)
    dept_name = Column(String(100), nullable=True)
    report_date = Column(Date, nullable=True)
    doc_year = Column(Integer, nullable=True)

    # 상태 정보
    state_code = Column(String(10), nullable=True)
    state_name = Column(String(50), nullable=True)
    open_code = Column(String(10), nullable=True)
    open_name = Column(String(100), nullable=True)
    close_reason = Column(Text, nullable=True)

    # 증분 업데이트용
    file_hash = Column(String(64), nullable=True)
    file_path = Column(Text, nullable=True)

    # 분류 정보
    biz_cate = Column(String(50), nullable=True)
    biz_sub_cate = Column(String(50), nullable=True)
    biz_detail_cate = Column(String(100), nullable=True)
    biz_full_path = Column(String(200), nullable=True)

    # 타임스탬프
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 관계
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="document", cascade="all, delete-orphan")
    permit_extraction = relationship(
        "PermitExtraction",
        back_populates="document",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self):
        return f"<Document(doc_id='{self.doc_id}', title='{self.title[:30] if self.title else ''}...')>"


class Attachment(Base):
    """붙임 파일 테이블"""
    __tablename__ = "attachments"

    attachment_id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(64), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False)

    # 파일 정보
    file_name = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    file_order = Column(Integer, nullable=True)  # 파일명의 숫자 (000, 001, ...)
    is_main_pdf = Column(Boolean, default=False)  # ODT와 같은 이름의 본문 PDF인지

    # 타임스탬프
    created_at = Column(DateTime, default=datetime.utcnow)

    # 관계
    document = relationship("Document", back_populates="attachments")

    def __repr__(self):
        return f"<Attachment(file_name='{self.file_name}', is_main={self.is_main_pdf})>"


class PermitExtraction(Base):
    """Permit-related fields extracted from document body text."""
    __tablename__ = "permit_extractions"

    doc_id = Column(String(64), ForeignKey("documents.doc_id", ondelete="CASCADE"), primary_key=True)

    org_nm = Column(String(255), nullable=True, comment="기관명")
    ask_org_nm = Column(Text, nullable=True, comment="피허가자명")
    addr = Column(Text, nullable=True, comment="위치")
    xtn = Column(Text, nullable=True, comment="면적")
    purps_cn = Column(Text, nullable=True, comment="목적")
    collection_volume = Column(String(100), nullable=True)
    permit_start_date = Column(Date, nullable=True)
    permit_end_date = Column(Date, nullable=True)
    amount = Column(String(100), nullable=True)
    confidence = Column(Float, nullable=True)
    source_text = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    document = relationship("Document", back_populates="permit_extraction")

    def __repr__(self):
        return f"<PermitExtraction(doc_id='{self.doc_id}', confidence={self.confidence})>"


class DocumentChunk(Base):
    """문서 청크 테이블 (벡터 포함)"""
    __tablename__ = "document_chunks"

    chunk_id = Column(String(64), primary_key=True)
    doc_id = Column(String(64), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False)

    # 청크 정보
    page_num = Column(Integer, default=1)
    chunk_num = Column(Integer, nullable=False)
    chunk_type = Column(String(20), nullable=False)  # 'text' or 'table'
    text = Column(Text, nullable=False)

    # 임베딩 벡터 (BGE-M3: 1024 차원)
    embedding = Column(Vector(1024), nullable=True)

    # 타임스탬프
    created_at = Column(DateTime, default=datetime.utcnow)

    # 관계
    document = relationship("Document", back_populates="chunks")

    def __repr__(self):
        return f"<DocumentChunk(chunk_id='{self.chunk_id}', type='{self.chunk_type}')>"


# 벡터 검색 인덱스 (테이블 생성 후 별도로 생성 필요)
# CREATE INDEX idx_chunks_embedding ON document_chunks
# USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);


def create_tables(database_url: str, drop_existing: bool = False):
    """
    데이터베이스 테이블 생성

    Args:
        database_url: PostgreSQL 연결 URL
        drop_existing: 기존 테이블 삭제 여부
    """
    engine = create_engine(database_url)
    initialize_vector_schema(
        engine,
        database_url=database_url,
        drop_existing=drop_existing,
    )

    # IVFFlat 인덱스 생성 (이미 존재하면 무시)
    try:
        create_ivfflat_index(
            engine,
            database_url=database_url,
            lists=100,
            if_not_exists=True,
        )
    except SQLAlchemyError as e:
        logger.warning("Index creation warning: %s", e)

    logger.info("Tables created successfully in %s", _mask_database_url(database_url))
    return engine
