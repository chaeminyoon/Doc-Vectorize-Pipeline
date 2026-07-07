import csv
import logging
from pathlib import Path

import pandas as pd

from src.metadata.loader import MetadataLoader
from src.pipeline.processor import DocumentPipeline, PipelineResult


LEGACY_COLUMN_COUNT = 30
KOREAN_HEADERS = [
    "구분(P:생산, R:접수)",
    "상태코드",
    "상태명",
    "문서ID",
    "문서연번",
    "생산문서번호",
    "문서명",
    "공개여부",
    "비공개사유",
    "수신일",
    "처리일",
    "연계ID",
    "등록일시",
    "승인여부",
    "구분1",
    "구분2",
    "구분3",
    "기관명",
    "열람범위종류",
    "대내/대외구분",
    "수신부서ID",
    "붙임파일갯수",
    "처리자부서ID",
    "처리자부서이름",
    "처리자부서상세이름",
    "처리자구분",
    "수신자부서ID",
    "수신자부서이름",
    "수신자부서상세이름",
    "발송자부서이름",
]
ENGLISH_HEADERS = [
    "se",
    "stts_cd",
    "stts_nm",
    "doc_id",
    "doc_no_seq",
    "appr_doc_num",
    "doc_nm",
    "open",
    "open_reason",
    "recv_dt",
    "act_dt",
    "link_id",
    "reg_dt",
    "aprv_yn",
    "exlpn_1",
    "exlpn_2",
    "exlpn_3",
    "instt_nm",
    "read_range_type",
    "enf_gubun",
    "recv_dept_id",
    "attach_seq",
    "acter_dept_id",
    "acter_dept_name",
    "acter_dept_name_desc",
    "acter_gubun",
    "rec_dept_id",
    "rec_dept_name",
    "rec_dept_name_desc",
    "send_dept_name",
]


def _metadata_row(
    *,
    doc_id: str,
    title: str,
    doc_type_raw: str = "P2026",
    process_date: str = "2026-04-16",
    biz_cate: str = "인허가",
    biz_sub_cate: str = "점사용",
    biz_detail_cate: str = "신규허가",
    processor_dept_name: str = "처리부서",
    sender_dept_name: str = "발송부서",
    receiver_dept_name: str = "수신부서",
) -> list[str]:
    row = [
        doc_type_raw,
        "ST01",
        "완료",
        doc_id,
        "1",
        "문서-001",
        title,
        "DPBT1",
        "",
        "2026-04-15",
        process_date,
        "LINK-001",
        "2026-04-16 10:00:00",
        "Y",
        biz_cate,
        biz_sub_cate,
        biz_detail_cate,
        "해양수산부",
        "전체",
        "대내",
        "RECV-001",
        "2",
        "ACT-001",
        processor_dept_name,
        f"{processor_dept_name} 상세",
        "담당",
        "REC-001",
        receiver_dept_name,
        f"{receiver_dept_name} 상세",
        sender_dept_name,
    ]
    assert len(row) == LEGACY_COLUMN_COUNT
    return row


def _write_legacy_xlsx(path: Path, rows: list[list[str]]) -> None:
    df = pd.DataFrame([["legacy-note"] * LEGACY_COLUMN_COUNT, *rows])
    df.to_excel(path, header=False, index=False)


def _write_new_schema_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(KOREAN_HEADERS)
        writer.writerow(ENGLISH_HEADERS)
        writer.writerows(rows)


def test_metadata_loader_supports_legacy_excel_positional_mapping(tmp_path):
    metadata_file = tmp_path / "shared_legacy.xlsx"
    _write_legacy_xlsx(
        metadata_file,
        [
            _metadata_row(
                doc_id="DCT-LEGACY-001",
                title="기존 엑셀 제목",
                biz_cate="공유수면",
                biz_sub_cate="매립",
                biz_detail_cate="변경허가",
                processor_dept_name="연안계획과",
            )
        ],
    )

    loader = MetadataLoader(metadata_file)
    loader.load()

    meta = loader.get_full_metadata("DCT-LEGACY-001")

    assert meta.document.title == "기존 엑셀 제목"
    assert meta.document.processor_dept_name == "연안계획과"
    assert meta.document.doc_year == 2026
    assert meta.classification.biz_full_path == "공유수면 > 매립 > 변경허가"


def test_metadata_loader_supports_metadata_directory_with_new_schema_csv(tmp_path):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()

    _write_new_schema_csv(
        metadata_dir / "문서목록_202604.csv",
        [
            _metadata_row(
                doc_id="DCT-NEW-001",
                title="신규 CSV 제목",
                biz_cate="공유수면",
                biz_sub_cate="점사용",
                biz_detail_cate="신규허가",
                processor_dept_name="공유수면과",
                sender_dept_name="발송부서A",
                receiver_dept_name="수신부서A",
            ),
            _metadata_row(
                doc_id="DCT-NEW-002",
                title="두 번째 문서",
                biz_cate="항만",
                biz_sub_cate="협의",
                biz_detail_cate="기타",
                processor_dept_name="항만과",
            ),
        ],
    )

    loader = MetadataLoader(metadata_dir)
    loader.load()

    first = loader.get_full_metadata("DCT-NEW-001")
    second = loader.get_full_metadata("DCT-NEW-002")

    assert first.document.title == "신규 CSV 제목"
    assert first.document.processor_dept_name == "공유수면과"
    assert first.document.sender_dept_name == "발송부서A"
    assert first.classification.biz_full_path == "공유수면 > 점사용 > 신규허가"
    assert second.document.title == "두 번째 문서"
    assert loader.get_stats()["total"] == 2


def test_pipeline_process_directories_runs_metadata_consistency_validation(tmp_path, monkeypatch):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    _write_new_schema_csv(
        metadata_dir / "문서목록.csv",
        [_metadata_row(doc_id="DCT-MATCH-001", title="정합 문서")],
    )

    data_dir = tmp_path / "data"
    (data_dir / "DCT-MATCH-001").mkdir(parents=True)
    (data_dir / "DCT-MATCH-001" / "000_match.odt").write_text("", encoding="utf-8")
    (data_dir / "DCT-MISSING-001").mkdir(parents=True)
    (data_dir / "DCT-MISSING-001" / "000_missing.odt").write_text("", encoding="utf-8")

    pipeline = DocumentPipeline(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        metadata_doc_list=str(metadata_dir),
        use_mock_embedder=True,
    )

    called = {}

    def fake_validate(directories):
        called["directories"] = list(directories)
        return {
            "data_doc_ids": {"DCT-MATCH-001", "DCT-MISSING-001"},
            "metadata_doc_ids": {"DCT-MATCH-001"},
            "matched_doc_ids": {"DCT-MATCH-001"},
            "missing_in_metadata": {"DCT-MISSING-001"},
            "missing_in_data": set(),
        }

    def fake_process_directory(directory, incremental=True, show_progress=True, max_workers=4):
        result = PipelineResult()
        result.start_time = result.end_time = None
        return result

    monkeypatch.setattr(pipeline, "validate_metadata_consistency", fake_validate)
    monkeypatch.setattr(pipeline, "process_directory", fake_process_directory)

    pipeline.process_directories([str(data_dir)], incremental=True, show_progress=False, max_workers=1)

    assert called["directories"] == [str(data_dir)]


def test_pipeline_reports_missing_metadata_doc_ids(tmp_path, caplog):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    _write_new_schema_csv(
        metadata_dir / "문서목록.csv",
        [
            _metadata_row(doc_id="DCT-MATCH-001", title="정합 문서"),
            _metadata_row(doc_id="DCT-ONLY-META-001", title="메타만 존재"),
        ],
    )

    data_dir = tmp_path / "data"
    (data_dir / "DCT-MATCH-001").mkdir(parents=True)
    (data_dir / "DCT-MATCH-001" / "000_match.odt").write_text("", encoding="utf-8")
    (data_dir / "DCT-ONLY-DATA-001").mkdir(parents=True)
    (data_dir / "DCT-ONLY-DATA-001" / "000_only_data.odt").write_text("", encoding="utf-8")

    pipeline = DocumentPipeline(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        metadata_doc_list=str(metadata_dir),
        use_mock_embedder=True,
    )

    with caplog.at_level(logging.WARNING):
        report = pipeline.validate_metadata_consistency([str(data_dir)])

    assert report["matched_doc_ids"] == {"DCT-MATCH-001"}
    assert report["missing_in_metadata"] == {"DCT-ONLY-DATA-001"}
    assert report["missing_in_data"] == {"DCT-ONLY-META-001"}
    assert "DCT-ONLY-DATA-001" in caplog.text
