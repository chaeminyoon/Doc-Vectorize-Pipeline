from src.vectordb.mirror_repository import (
    MANAGED_MIRROR_TABLES,
    MirrorPermitExtraction,
)


def test_permit_extractions_syncs_to_existing_partner_table():
    assert MirrorPermitExtraction.__tablename__ == "st_partner_link_doc"


def test_partner_table_is_not_created_or_dropped_by_mirror_initialization():
    assert {table.name for table in MANAGED_MIRROR_TABLES} == {
        "documents",
        "document_chunks",
        "attachments",
    }

