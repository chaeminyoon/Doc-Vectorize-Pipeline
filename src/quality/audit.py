from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


DATE_PATTERNS = (
    re.compile(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})"),
    re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
)

HEADER_DATE_PATTERNS = (
    ("결재일자", re.compile(r"결재일자\D{0,20}(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2})")),
    ("등록일자", re.compile(r"등록일자\D{0,20}(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2})")),
    ("전결", re.compile(r"전결\D{0,20}(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2})")),
    ("대결", re.compile(r"대결\D{0,20}(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2})")),
    ("결재", re.compile(r"결재\D{0,20}(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2})")),
)


@dataclass
class ReportDateMismatchDecision:
    current_report_date: Optional[date]
    proposed_report_date: Optional[date]
    auto_fixable: bool
    confidence: str
    reason: str
    matched_label: Optional[str] = None
    candidates: list[str] = field(default_factory=list)


def parse_date_value(value: object) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            year, month, day = match.groups()
            return date(int(year), int(month), int(day))
        except ValueError:
            continue
    return None


def extract_all_dates(text: str) -> list[date]:
    results: list[date] = []
    for pattern in DATE_PATTERNS:
        for year, month, day in pattern.findall(text or ""):
            try:
                results.append(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return results


def extract_header_date_candidates(text: str) -> list[tuple[str, date]]:
    candidates: list[tuple[str, date]] = []
    for label, pattern in HEADER_DATE_PATTERNS:
        for raw in pattern.findall(text or ""):
            parsed = parse_date_value(raw)
            if parsed is not None:
                candidates.append((label, parsed))
    return candidates


def choose_report_date_for_doc_year(
    doc_year: Optional[int],
    current_report_date: object,
    raw_text: str,
) -> ReportDateMismatchDecision:
    current = parse_date_value(current_report_date)
    header_candidates = extract_header_date_candidates(raw_text)

    candidate_strings = [f"{label}:{value.isoformat()}" for label, value in header_candidates]

    if doc_year is None:
        return ReportDateMismatchDecision(
            current_report_date=current,
            proposed_report_date=None,
            auto_fixable=False,
            confidence="none",
            reason="doc_year_missing",
            candidates=candidate_strings,
        )

    preferred_labels = ("결재일자", "전결", "대결", "결재", "등록일자")
    for label in preferred_labels:
        for candidate_label, candidate_date in header_candidates:
            if candidate_label != label:
                continue
            if candidate_date.year != doc_year:
                continue
            if current == candidate_date:
                return ReportDateMismatchDecision(
                    current_report_date=current,
                    proposed_report_date=current,
                    auto_fixable=False,
                    confidence="high",
                    reason="already_matches_preferred_candidate",
                    matched_label=label,
                    candidates=candidate_strings,
                )
            return ReportDateMismatchDecision(
                current_report_date=current,
                proposed_report_date=candidate_date,
                auto_fixable=True,
                confidence="high" if label != "등록일자" else "medium",
                reason="doc_year_matched_header_date",
                matched_label=label,
                candidates=candidate_strings,
            )

    return ReportDateMismatchDecision(
        current_report_date=current,
        proposed_report_date=None,
        auto_fixable=False,
        confidence="low",
        reason="no_doc_year_matched_header_date",
        candidates=candidate_strings,
    )
