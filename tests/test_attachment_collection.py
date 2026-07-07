from src.parsers.odt_parser import ODTParser


def test_find_pdf_attachments_keeps_pdfs_and_only_non_duplicated_hwp_variants(tmp_path):
    attachments_dir = tmp_path / "DCT0001" / "attachments"
    attachments_dir.mkdir(parents=True)

    source_odt = attachments_dir / "000_main.odt"
    source_odt.write_text("odt", encoding="utf-8")

    (attachments_dir / "000_main.pdf").write_text("pdf", encoding="utf-8")
    (attachments_dir / "001_appendix.pdf").write_text("pdf", encoding="utf-8")
    (attachments_dir / "001_appendix.hwp").write_text("hwp", encoding="utf-8")
    (attachments_dir / "002_legacy.odt").write_text("odt", encoding="utf-8")
    (attachments_dir / "002_legacy.hwpx").write_text("hwpx", encoding="utf-8")
    (attachments_dir / "003_only_hwp.hwp").write_text("hwp", encoding="utf-8")
    (attachments_dir / "004_only_hwpx.hwpx").write_text("hwpx", encoding="utf-8")
    (attachments_dir / "005_reference.docx").write_text("docx", encoding="utf-8")
    (attachments_dir / "notes.txt").write_text("txt", encoding="utf-8")

    parser = ODTParser()

    attachments = parser._find_pdf_attachments(source_odt)

    assert [item.file_name for item in attachments] == [
        "000_main.pdf",
        "001_appendix.pdf",
        "003_only_hwp.hwp",
        "004_only_hwpx.hwpx",
    ]
    assert attachments[0].is_main_pdf is True
    assert all(item.file_name != source_odt.name for item in attachments)


def test_find_pdf_attachments_keeps_hwp_when_pdf_exists_without_matching_odt(tmp_path):
    attachments_dir = tmp_path / "DCT0002" / "attachments"
    attachments_dir.mkdir(parents=True)

    source_odt = attachments_dir / "000_main.odt"
    source_odt.write_text("odt", encoding="utf-8")

    (attachments_dir / "000_main.pdf").write_text("pdf", encoding="utf-8")
    (attachments_dir / "010_scan.pdf").write_text("pdf", encoding="utf-8")
    (attachments_dir / "010_scan.hwp").write_text("hwp", encoding="utf-8")
    (attachments_dir / "011_notice.pdf").write_text("pdf", encoding="utf-8")
    (attachments_dir / "011_notice.hwpx").write_text("hwpx", encoding="utf-8")

    parser = ODTParser()

    attachments = parser._find_pdf_attachments(source_odt)

    assert [item.file_name for item in attachments] == [
        "000_main.pdf",
        "010_scan.pdf",
        "010_scan.hwp",
        "011_notice.pdf",
        "011_notice.hwpx",
    ]
