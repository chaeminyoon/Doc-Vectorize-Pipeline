"""Mirror pipeline output into a PostgreSQL database without pgvector."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .models import Attachment, Document, DocumentChunk, PermitExtraction


MirrorBase = declarative_base()
logger = logging.getLogger(__name__)


class MirrorDocument(MirrorBase):
    __tablename__ = "documents"

    doc_id = Column(String(64), primary_key=True)
    doc_no = Column(String(100), nullable=True)
    title = Column(Text, nullable=True)
    author_name = Column(String(100), nullable=True)
    dept_name = Column(String(100), nullable=True)
    report_date = Column(Date, nullable=True)
    doc_year = Column(Integer, nullable=True)
    state_code = Column(String(10), nullable=True)
    state_name = Column(String(50), nullable=True)
    open_code = Column(String(10), nullable=True)
    open_name = Column(String(100), nullable=True)
    close_reason = Column(Text, nullable=True)
    file_hash = Column(String(64), nullable=True)
    file_path = Column(Text, nullable=True)
    biz_cate = Column(String(50), nullable=True)
    biz_sub_cate = Column(String(50), nullable=True)
    biz_detail_cate = Column(String(100), nullable=True)
    biz_full_path = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MirrorPermitExtraction(MirrorBase):
    __tablename__ = "st_partner_link_doc"

    doc_id = Column(String(64), ForeignKey("documents.doc_id", ondelete="CASCADE"), primary_key=True)
    org_nm = Column(String(255), nullable=True)
    ask_org_nm = Column(Text, nullable=True)
    addr = Column(Text, nullable=True)
    xtn = Column(Text, nullable=True)
    purps_cn = Column(Text, nullable=True)
    collection_volume = Column(String(100), nullable=True)
    permit_start_date = Column(Date, nullable=True)
    permit_end_date = Column(Date, nullable=True)
    amount = Column(String(100), nullable=True)
    confidence = Column(Float, nullable=True)
    source_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MirrorDocumentChunk(MirrorBase):
    __tablename__ = "document_chunks"

    chunk_id = Column(String(64), primary_key=True)
    doc_id = Column(String(64), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False)
    page_num = Column(Integer, default=1)
    chunk_num = Column(Integer, nullable=False)
    chunk_type = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(ARRAY(Float), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MirrorAttachment(MirrorBase):
    __tablename__ = "attachments"

    attachment_id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(64), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    file_order = Column(Integer, nullable=True)
    is_main_pdf = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


MANAGED_MIRROR_TABLES = [
    MirrorDocument.__table__,
    MirrorDocumentChunk.__table__,
    MirrorAttachment.__table__,
]


@dataclass
class MirrorSyncResult:
    documents_synced: int = 0
    permit_extractions_synced: int = 0
    chunks_synced: int = 0
    attachments_synced: int = 0

    def __str__(self) -> str:
        return (
            "MirrorSyncResult(\n"
            f"  documents_synced={self.documents_synced},\n"
            f"  permit_extractions_synced={self.permit_extractions_synced},\n"
            f"  chunks_synced={self.chunks_synced},\n"
            f"  attachments_synced={self.attachments_synced}\n"
            ")"
        )


class MirrorRepository:
    """Write pipeline results to plain PostgreSQL tables.

    The mirror database intentionally does not use pgvector. Embeddings are
    stored as PostgreSQL float arrays so older PostgreSQL servers can receive
    the generated output with normal INSERT/UPDATE statements.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = create_engine(database_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def init_db(self, drop_existing: bool = False) -> None:
        if drop_existing:
            MirrorBase.metadata.drop_all(self.engine, tables=MANAGED_MIRROR_TABLES)
        MirrorBase.metadata.create_all(self.engine, tables=MANAGED_MIRROR_TABLES)
        logger.info("Mirror database initialized")

    def get_session(self) -> Session:
        return self.SessionLocal()

    def upsert_pipeline_output(
        self,
        document: Document,
        permit_extraction: Optional[PermitExtraction],
        chunks: list[DocumentChunk],
        attachments: Optional[list[Attachment]] = None,
    ) -> None:
        with self.get_session() as session:
            session.merge(MirrorDocument(**self._document_values(document)))
            if permit_extraction is not None:
                session.merge(MirrorPermitExtraction(**self._permit_values(permit_extraction)))
            else:
                session.query(MirrorPermitExtraction).filter(
                    MirrorPermitExtraction.doc_id == document.doc_id
                ).delete()

            session.query(MirrorDocumentChunk).filter(
                MirrorDocumentChunk.doc_id == document.doc_id
            ).delete()
            if chunks:
                session.bulk_insert_mappings(
                    MirrorDocumentChunk,
                    [self._chunk_values(chunk) for chunk in chunks],
                )

            if attachments is not None:
                session.query(MirrorAttachment).filter(
                    MirrorAttachment.doc_id == document.doc_id
                ).delete()
                if attachments:
                    session.bulk_insert_mappings(
                        MirrorAttachment,
                        [self._attachment_values(attachment) for attachment in attachments],
                    )

            session.commit()

    def sync_from_vector_repository(self, source_repository) -> MirrorSyncResult:
        result = MirrorSyncResult()

        with source_repository.get_session() as source_session:
            doc_ids = [
                row[0]
                for row in source_session.query(Document.doc_id)
                .order_by(Document.doc_id)
                .all()
            ]

            for doc_id in doc_ids:
                document = source_session.query(Document).filter(
                    Document.doc_id == doc_id
                ).first()
                if document is None:
                    continue

                permit_extraction = source_session.query(PermitExtraction).filter(
                    PermitExtraction.doc_id == doc_id
                ).first()
                chunks = source_session.query(DocumentChunk).filter(
                    DocumentChunk.doc_id == doc_id
                ).order_by(DocumentChunk.chunk_num).all()
                attachments = source_session.query(Attachment).filter(
                    Attachment.doc_id == doc_id
                ).order_by(Attachment.file_order, Attachment.file_name).all()

                self.upsert_pipeline_output(
                    document=document,
                    permit_extraction=permit_extraction,
                    chunks=chunks,
                    attachments=attachments,
                )
                result.documents_synced += 1
                if permit_extraction is not None:
                    result.permit_extractions_synced += 1
                result.chunks_synced += len(chunks)
                result.attachments_synced += len(attachments)

        return result

    @staticmethod
    def _document_values(document: Document) -> dict:
        return {
            "doc_id": document.doc_id,
            "doc_no": document.doc_no,
            "title": document.title,
            "author_name": document.author_name,
            "dept_name": document.dept_name,
            "report_date": document.report_date,
            "doc_year": document.doc_year,
            "state_code": document.state_code,
            "state_name": document.state_name,
            "open_code": document.open_code,
            "open_name": document.open_name,
            "close_reason": document.close_reason,
            "file_hash": document.file_hash,
            "file_path": document.file_path,
            "biz_cate": document.biz_cate,
            "biz_sub_cate": document.biz_sub_cate,
            "biz_detail_cate": document.biz_detail_cate,
            "biz_full_path": document.biz_full_path,
        }

    @staticmethod
    def _permit_values(extraction: PermitExtraction) -> dict:
        return {
            "doc_id": extraction.doc_id,
            "org_nm": extraction.org_nm,
            "ask_org_nm": extraction.ask_org_nm,
            "addr": extraction.addr,
            "xtn": extraction.xtn,
            "purps_cn": extraction.purps_cn,
            "collection_volume": extraction.collection_volume,
            "permit_start_date": extraction.permit_start_date,
            "permit_end_date": extraction.permit_end_date,
            "amount": extraction.amount,
            "confidence": extraction.confidence,
            "source_text": extraction.source_text,
        }

    @staticmethod
    def _chunk_values(chunk: DocumentChunk) -> dict:
        embedding = chunk.embedding
        if embedding is not None:
            embedding = [float(value) for value in embedding]

        return {
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "page_num": chunk.page_num,
            "chunk_num": chunk.chunk_num,
            "chunk_type": chunk.chunk_type,
            "text": chunk.text,
            "embedding": embedding,
        }

    @staticmethod
    def _attachment_values(attachment: Attachment) -> dict:
        return {
            "doc_id": attachment.doc_id,
            "file_name": attachment.file_name,
            "file_path": attachment.file_path,
            "file_order": attachment.file_order,
            "is_main_pdf": attachment.is_main_pdf,
        }
