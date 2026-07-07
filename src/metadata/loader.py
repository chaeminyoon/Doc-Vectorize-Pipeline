"""
Metadata loader for document lists.

Supports:
- legacy positional Excel files
- header-based Korean schema
- header-based English schema
- loading a single file or all metadata files under a directory
- csv/xlsx/xls sources
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging

import pandas as pd

from src.quality import safe_path_name

logger = logging.getLogger(__name__)


@dataclass
class DocumentMetadata:
    """Document metadata fields used by the pipeline."""

    doc_id: str
    doc_no: str = ""
    title: str = ""
    doc_type: str = ""
    state_code: str = ""
    state_name: str = ""
    open_code: str = ""
    open_name: str = ""
    close_reason: str = ""
    receive_date: Optional[str] = None
    process_date: Optional[str] = None
    doc_year: Optional[int] = None
    is_attachment: bool = False
    attachment_order: Optional[int] = None
    link_id: str = ""
    register_datetime: Optional[str] = None
    approval_yn: str = ""
    org_name: str = ""
    view_scope: str = ""
    internal_external: str = ""
    receive_dept_id: str = ""
    attachment_count: Optional[int] = None
    processor_dept_id: str = ""
    processor_dept_name: str = ""
    processor_dept_detail: str = ""
    processor_type: str = ""
    receiver_dept_id: str = ""
    receiver_dept_name: str = ""
    receiver_dept_detail: str = ""
    sender_dept_name: str = ""


@dataclass
class ClassificationInfo:
    """Business classification metadata."""

    biz_cate: str = ""
    biz_sub_cate: str = ""
    biz_detail_cate: str = ""
    biz_full_path: str = ""
    doc_type: str = ""
    state: str = ""


@dataclass
class FullMetadata:
    """Combined document and classification metadata."""

    document: DocumentMetadata
    classification: ClassificationInfo = field(default_factory=ClassificationInfo)


class MetadataLoader:
    """Load metadata from a file or a directory of metadata files."""

    SUPPORTED_SUFFIXES = (".csv", ".xlsx", ".xls")

    COLUMN_NAMES = [
        "doc_type_raw",
        "state_code",
        "state_name",
        "doc_id",
        "doc_seq",
        "doc_no",
        "title",
        "open_code_raw",
        "close_reason",
        "receive_date",
        "process_date",
        "link_id",
        "register_datetime",
        "approval_yn",
        "biz_cate",
        "biz_sub_cate",
        "biz_detail_cate",
        "org_name",
        "view_scope",
        "internal_external",
        "receive_dept_id",
        "attachment_count",
        "processor_dept_id",
        "processor_dept_name",
        "processor_dept_detail",
        "processor_type",
        "receiver_dept_id",
        "receiver_dept_name",
        "receiver_dept_detail",
        "sender_dept_name",
    ]

    HEADER_ALIASES = {
        "doc_type_raw": {"doc_type_raw", "구분(p:생산,r:접수)", "se"},
        "state_code": {"state_code", "상태코드", "stts_cd"},
        "state_name": {"state_name", "상태명", "stts_nm"},
        "doc_id": {"doc_id", "문서id", "문서아이디"},
        "doc_seq": {"doc_seq", "문서연번", "doc_no_seq"},
        "doc_no": {"doc_no", "생산문서번호", "appr_doc_num"},
        "title": {"title", "문서명", "doc_nm"},
        "open_code_raw": {"open_code_raw", "공개여부", "open"},
        "close_reason": {"close_reason", "비공개사유", "open_reason"},
        "receive_date": {"receive_date", "수신일", "recv_dt"},
        "process_date": {"process_date", "처리일", "act_dt"},
        "link_id": {"link_id", "연계id"},
        "register_datetime": {"register_datetime", "등록일시", "reg_dt"},
        "approval_yn": {"approval_yn", "승인여부", "aprv_yn"},
        "biz_cate": {"biz_cate", "구분1", "exlpn_1"},
        "biz_sub_cate": {"biz_sub_cate", "구분2", "exlpn_2"},
        "biz_detail_cate": {"biz_detail_cate", "구분3", "exlpn_3"},
        "org_name": {"org_name", "기관명", "instt_nm"},
        "view_scope": {"view_scope", "열람범위종류", "read_range_type"},
        "internal_external": {"internal_external", "대내/대외구분", "enf_gubun"},
        "receive_dept_id": {"receive_dept_id", "수신부서id", "recv_dept_id"},
        "attachment_count": {"attachment_count", "붙임파일갯수", "attach_seq"},
        "processor_dept_id": {"processor_dept_id", "처리자부서id", "acter_dept_id"},
        "processor_dept_name": {"processor_dept_name", "처리자부서이름", "acter_dept_name"},
        "processor_dept_detail": {"processor_dept_detail", "처리자부서상세이름", "acter_dept_name_desc"},
        "processor_type": {"processor_type", "처리자구분", "acter_gubun"},
        "receiver_dept_id": {"receiver_dept_id", "수신자부서id", "rec_dept_id"},
        "receiver_dept_name": {"receiver_dept_name", "수신자부서이름", "rec_dept_name"},
        "receiver_dept_detail": {"receiver_dept_detail", "수신자부서상세이름", "rec_dept_name_desc"},
        "sender_dept_name": {"sender_dept_name", "발송자부서이름", "send_dept_name"},
    }

    def __init__(self, doc_list_path: str | Path):
        self.doc_list_path = Path(doc_list_path)
        self._doc_df: Optional[pd.DataFrame] = None
        self._source_paths: list[Path] = []

    def load(self) -> None:
        self._load_document_list()

    def _load_document_list(self) -> None:
        source_paths = self._discover_source_paths()
        if not source_paths:
            self._doc_df = pd.DataFrame(columns=self.COLUMN_NAMES)
            return

        frames: list[pd.DataFrame] = []
        for source_path in source_paths:
            raw_df = self._read_source(source_path)
            normalized = self._normalize_source(raw_df, source_path)
            if not normalized.empty:
                normalized["__source_name"] = source_path.name
                frames.append(normalized)

        if not frames:
            self._doc_df = pd.DataFrame(columns=self.COLUMN_NAMES)
            logger.warning("No metadata rows were loaded from %s", safe_path_name(self.doc_list_path))
            return

        combined = pd.concat(frames, ignore_index=True)

        duplicate_mask = combined.duplicated(subset=["doc_id"], keep="last")
        duplicate_count = int(duplicate_mask.sum())
        if duplicate_count:
            logger.warning(
                "Found %d duplicate doc_id rows across metadata files. Keeping the latest row by source order.",
                duplicate_count,
            )

        combined = combined.drop_duplicates(subset=["doc_id"], keep="last")
        combined = combined.drop(columns="__source_name", errors="ignore")
        combined = combined.set_index("doc_id", drop=True)
        self._doc_df = combined
        self._source_paths = source_paths

        logger.info(
            "Loaded %d metadata rows from %d source files under %s",
            len(self._doc_df),
            len(source_paths),
            safe_path_name(self.doc_list_path),
        )

    def _discover_source_paths(self) -> list[Path]:
        path = self.doc_list_path
        if not path.exists():
            logger.warning("Document list path not found: %s", safe_path_name(path))
            return []

        if path.is_file():
            if path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                logger.warning("Unsupported metadata file type: %s", safe_path_name(path))
                return []
            return [path]

        if not path.is_dir():
            logger.warning("Metadata path is not a file or directory: %s", safe_path_name(path))
            return []

        source_paths = sorted(
            (
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in self.SUPPORTED_SUFFIXES
            ),
            key=lambda candidate: (candidate.stat().st_mtime, candidate.name),
        )
        if not source_paths:
            logger.warning("No metadata files were found under %s", safe_path_name(path))
        return source_paths

    def _read_source(self, source_path: Path) -> pd.DataFrame:
        suffix = source_path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(source_path, header=None, dtype=object, encoding="utf-8-sig")
        return pd.read_excel(source_path, header=None, dtype=object)

    def _normalize_source(self, raw_df: pd.DataFrame, source_path: Path) -> pd.DataFrame:
        raw_df = raw_df.dropna(how="all").reset_index(drop=True)
        if raw_df.empty:
            return pd.DataFrame(columns=self.COLUMN_NAMES)

        header_info = self._detect_header_rows(raw_df)
        if header_info is None:
            normalized = self._normalize_positional_source(raw_df, source_path)
        else:
            normalized = self._normalize_header_source(raw_df, source_path, header_info)

        if normalized.empty:
            return pd.DataFrame(columns=self.COLUMN_NAMES)

        for column in self.COLUMN_NAMES:
            if column not in normalized.columns:
                normalized[column] = pd.NA

        normalized = normalized[self.COLUMN_NAMES]
        normalized["doc_id"] = normalized["doc_id"].map(self._safe_str)
        normalized = normalized[normalized["doc_id"] != ""].reset_index(drop=True)
        return normalized

    def _detect_header_rows(
        self, raw_df: pd.DataFrame
    ) -> Optional[tuple[dict[int, str], int]]:
        recognized_rows: list[tuple[int, dict[int, str]]] = []
        max_rows = min(len(raw_df), 5)

        for row_index in range(max_rows):
            mapping = self._recognize_header_mapping(raw_df.iloc[row_index].tolist())
            if "doc_id" in mapping.values() and len(mapping) >= 3:
                recognized_rows.append((row_index, mapping))

        if not recognized_rows:
            return None

        first_index, first_mapping = recognized_rows[0]
        last_index = first_index
        best_mapping = first_mapping

        for row_index, mapping in recognized_rows[1:]:
            if row_index != last_index + 1:
                break
            last_index = row_index
            if len(mapping) >= len(best_mapping):
                best_mapping = mapping

        return best_mapping, last_index + 1

    def _recognize_header_mapping(self, row_values: list[object]) -> dict[int, str]:
        mapping: dict[int, str] = {}
        assigned: set[str] = set()

        for column_index, value in enumerate(row_values):
            canonical = self._canonicalize_header(value)
            if canonical is None or canonical in assigned:
                continue
            mapping[column_index] = canonical
            assigned.add(canonical)

        return mapping

    def _canonicalize_header(self, value: object) -> Optional[str]:
        normalized = self._normalize_header_value(value)
        if not normalized:
            return None

        for canonical, aliases in self.HEADER_ALIASES.items():
            for alias in aliases:
                if normalized == alias or normalized.startswith(alias):
                    return canonical
        return None

    @staticmethod
    def _normalize_header_value(value: object) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        text = str(value).strip().lower().replace("\ufeff", "")
        return "".join(text.split())

    def _normalize_header_source(
        self,
        raw_df: pd.DataFrame,
        source_path: Path,
        header_info: tuple[dict[int, str], int],
    ) -> pd.DataFrame:
        column_mapping, data_start_row = header_info
        data_df = raw_df.iloc[data_start_row:].copy()
        if data_df.empty:
            return pd.DataFrame(columns=self.COLUMN_NAMES)

        renamed_columns: dict[int, str] = {}
        seen: set[str] = set()
        for column_index, canonical in column_mapping.items():
            if canonical in seen:
                continue
            renamed_columns[column_index] = canonical
            seen.add(canonical)

        data_df = data_df.iloc[:, list(renamed_columns.keys())]
        data_df.columns = list(renamed_columns.values())
        return data_df.reset_index(drop=True)

    def _normalize_positional_source(self, raw_df: pd.DataFrame, source_path: Path) -> pd.DataFrame:
        start_row = 1 if len(raw_df) > 1 else 0
        data_df = raw_df.iloc[start_row:].copy()
        if data_df.empty:
            return pd.DataFrame(columns=self.COLUMN_NAMES)

        actual_cols = len(data_df.columns)
        expected_cols = len(self.COLUMN_NAMES)
        if actual_cols < expected_cols:
            logger.warning(
                "Metadata source %s has %d columns, expected %d. Missing columns will be left blank.",
                safe_path_name(source_path),
                actual_cols,
                expected_cols,
            )

        usable_cols = min(actual_cols, expected_cols)
        data_df = data_df.iloc[:, :usable_cols]
        data_df.columns = self.COLUMN_NAMES[:usable_cols]
        return data_df.reset_index(drop=True)

    def get_loaded_doc_ids(self) -> set[str]:
        if self._doc_df is None or self._doc_df.empty:
            return set()
        return set(map(str, self._doc_df.index.tolist()))

    @staticmethod
    def _safe_str(value: object, default: str = "") -> str:
        if pd.isna(value) or value is None:
            return default
        return str(value).strip()

    @staticmethod
    def _safe_int(value: object) -> Optional[int]:
        if pd.isna(value) or value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def get_document_metadata(self, doc_id: str) -> Optional[DocumentMetadata]:
        if self._doc_df is None or self._doc_df.empty or doc_id not in self._doc_df.index:
            return None

        row = self._doc_df.loc[doc_id]

        doc_type_raw = self._safe_str(row.get("doc_type_raw"))
        doc_type = doc_type_raw[:1] if doc_type_raw else ""
        doc_year = None
        if len(doc_type_raw) > 1:
            try:
                doc_year = int(doc_type_raw[1:])
            except ValueError:
                logger.warning("Could not parse doc year for %s from %r", doc_id, doc_type_raw)

        open_code_raw = self._safe_str(row.get("open_code_raw"))
        open_code = open_code_raw
        open_name = ""
        if "DPBT1" in open_code_raw:
            open_code = "DPBT1"
            open_name = "대국민공개"
        elif "DPBT2" in open_code_raw:
            open_code = "DPBT2"
            open_name = "비공개"
        elif "DPBT3" in open_code_raw:
            open_code = "DPBT3"
            open_name = "부분공개"

        return DocumentMetadata(
            doc_id=doc_id,
            doc_no=self._safe_str(row.get("doc_no")),
            title=self._safe_str(row.get("title")),
            doc_type=doc_type,
            state_code=self._safe_str(row.get("state_code")),
            state_name=self._safe_str(row.get("state_name")),
            open_code=open_code,
            open_name=open_name,
            close_reason=self._safe_str(row.get("close_reason")),
            receive_date=self._safe_str(row.get("receive_date")) or None,
            process_date=self._safe_str(row.get("process_date")) or None,
            doc_year=doc_year,
            link_id=self._safe_str(row.get("link_id")),
            register_datetime=self._safe_str(row.get("register_datetime")) or None,
            approval_yn=self._safe_str(row.get("approval_yn")),
            org_name=self._safe_str(row.get("org_name")),
            view_scope=self._safe_str(row.get("view_scope")),
            internal_external=self._safe_str(row.get("internal_external")),
            receive_dept_id=self._safe_str(row.get("receive_dept_id")),
            attachment_count=self._safe_int(row.get("attachment_count")),
            processor_dept_id=self._safe_str(row.get("processor_dept_id")),
            processor_dept_name=self._safe_str(row.get("processor_dept_name")),
            processor_dept_detail=self._safe_str(row.get("processor_dept_detail")),
            processor_type=self._safe_str(row.get("processor_type")),
            receiver_dept_id=self._safe_str(row.get("receiver_dept_id")),
            receiver_dept_name=self._safe_str(row.get("receiver_dept_name")),
            receiver_dept_detail=self._safe_str(row.get("receiver_dept_detail")),
            sender_dept_name=self._safe_str(row.get("sender_dept_name")),
        )

    def get_classification(self, doc_id: str, title: str = "") -> ClassificationInfo:
        if self._doc_df is None or self._doc_df.empty or doc_id not in self._doc_df.index:
            return ClassificationInfo()

        row = self._doc_df.loc[doc_id]
        biz_cate = self._safe_str(row.get("biz_cate"))
        biz_sub_cate = self._safe_str(row.get("biz_sub_cate"))
        biz_detail_cate = self._safe_str(row.get("biz_detail_cate"))
        biz_full_path = self._build_full_path(biz_cate, biz_sub_cate, biz_detail_cate)

        doc_type_raw = self._safe_str(row.get("doc_type_raw"))
        doc_type = ""
        if doc_type_raw.startswith("P"):
            doc_type = "생산"
        elif doc_type_raw.startswith("R"):
            doc_type = "접수"

        return ClassificationInfo(
            biz_cate=biz_cate,
            biz_sub_cate=biz_sub_cate,
            biz_detail_cate=biz_detail_cate,
            biz_full_path=biz_full_path,
            doc_type=doc_type,
            state=self._safe_str(row.get("state_name")),
        )

    @staticmethod
    def _build_full_path(cate: str, sub: str, detail: str) -> str:
        return " > ".join(part for part in (cate, sub, detail) if part)

    def get_full_metadata(self, doc_id: str, title: str = "") -> FullMetadata:
        doc_meta = self.get_document_metadata(doc_id)
        if doc_meta is None:
            doc_meta = DocumentMetadata(doc_id=doc_id, title=title)
        classification = self.get_classification(doc_id, title)
        return FullMetadata(document=doc_meta, classification=classification)

    def get_stats(self) -> dict:
        if self._doc_df is None or self._doc_df.empty:
            return {"total": 0, "sources": len(self._source_paths)}

        stats = {
            "total": len(self._doc_df),
            "sources": len(self._source_paths),
        }
        if "biz_cate" in self._doc_df.columns:
            stats["biz_cate"] = self._doc_df["biz_cate"].value_counts().to_dict()
        if "biz_sub_cate" in self._doc_df.columns:
            stats["biz_sub_cate"] = self._doc_df["biz_sub_cate"].value_counts().to_dict()
        return stats
