"""Rule-based extractor for permit-related fields in official document bodies."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class PermitExtractedFields:
    org_name: Optional[str] = None
    permittee_name: Optional[str] = None
    location: Optional[str] = None
    area: Optional[str] = None
    purpose: Optional[str] = None
    collection_volume: Optional[str] = None
    permit_start_date: Optional[date] = None
    permit_end_date: Optional[date] = None
    amount: Optional[str] = None
    confidence: float = 0.0
    source_text: Optional[str] = None


class PermitFieldExtractor:
    """Extract permit fields with conservative label and pattern rules."""

    DATE_PATTERN = re.compile(
        r"(\d{4})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:[.]|일)?"
    )
    SHORT_DATE_PATTERN = re.compile(
        r"(?<!\d)[`'‘’]?\s*(\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:[.]|일)?(?!\d)"
    )
    AMOUNT_PATTERN = re.compile(r"(?:일금|금)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*원")
    VOLUME_PATTERN = re.compile(r"([0-9][0-9,]*(?:\.\d+)?)\s*(㎥|m3|m³|㎡|m2|m²)", re.IGNORECASE)
    ORG_SUFFIX_PATTERN = re.compile(r"(청|부|처|시|군|구|도|공사|공단|위원회|사업소)$")
    KNOWN_ORG_NAMES = (
        "부산지방해양수산청",
        "인천지방해양수산청",
        "여수지방해양수산청",
        "마산지방해양수산청",
        "울산지방해양수산청",
        "목포지방해양수산청",
        "군산지방해양수산청",
        "포항지방해양수산청",
        "평택지방해양수산청",
        "대산지방해양수산청",
        "동해지방해양수산청",
        "남해지방해양수산청",
        "부산지방해양항만청",
        "인천지방해양항만청",
        "여수지방해양항만청",
        "마산지방해양항만청",
        "울산지방해양항만청",
        "목포지방해양항만청",
        "군산지방해양항만청",
        "포항지방해양항만청",
        "평택지방해양항만청",
        "대산지방해양항만청",
        "동해지방해양항만청",
        "해양수산부",
    )
    ORG_URL_FALLBACKS = {
        "busan.mof.go.kr": "부산지방해양수산청",
        "portbusan.go.kr": "부산지방해양수산청",
        "incheon.mof.go.kr": "인천지방해양수산청",
        "yeosu.mof.go.kr": "여수지방해양수산청",
        "masan.mof.go.kr": "마산지방해양수산청",
        "ulsan.mof.go.kr": "울산지방해양수산청",
        "mokpo.mof.go.kr": "목포지방해양수산청",
        "gunsan.mof.go.kr": "군산지방해양수산청",
        "pohang.mof.go.kr": "포항지방해양수산청",
        "pyeongtaek.mof.go.kr": "평택지방해양수산청",
        "daesan.mof.go.kr": "대산지방해양수산청",
        "donghae.mof.go.kr": "동해지방해양수산청",
    }
    KNOWN_LOCATION_STARTS = (
        "서울특별시", "부산광역시", "인천광역시", "대구광역시", "광주광역시",
        "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원도",
        "충청북도", "충청남도", "전라북도", "전라남도", "경상북도", "경상남도",
        "제주특별자치도", "창원시", "포항시", "신안군", "무안군", "여수시",
        "목포시", "통영시", "군산시", "울진군", "울주군",
        "서울", "부산", "인천", "대구", "광주", "대전", "울산", "세종",
        "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    )

    REQUESTED_FIELD_COUNT = 9

    def extract(self, text: str) -> PermitExtractedFields:
        normalized = self._normalize_text(text)
        compact_fields = self._extract_compact_fields(normalized)

        org_name = self._extract_org_name(normalized) or compact_fields.get("org_name")
        permittee_name = compact_fields.get("permittee_name") or self._extract_permittee_name(normalized)
        location = self._extract_labeled_line(normalized, ("위치", "위 치", "점용장소", "점용·사용 장소", "장소", "소재지", "허가장소", "신청위치")) or compact_fields.get("location")
        area = self._extract_area(normalized) or compact_fields.get("area")
        purpose = self._extract_purpose(normalized) or compact_fields.get("purpose")

        first_permit_date = (
            self._extract_date_by_labels(normalized, ("최초허가일자", "최초 허가일자"))
            or compact_fields.get("first_permit_date")
        )
        permit_date = self._extract_date_by_labels(normalized, ("허가일자", "허가일")) or compact_fields.get("permit_date")
        permit_start_date = (
            self._extract_date_by_labels(normalized, ("허가시작일", "허가 시작일"))
            or compact_fields.get("permit_start_date")
        )
        permit_end_date = (
            self._extract_date_by_labels(normalized, ("허가종료일", "허가 종료일"))
            or compact_fields.get("permit_end_date")
        )

        period_value = self._extract_labeled_line(normalized, ("허가기간", "허가 기간", "점용기간", "점용 기간"))
        if period_value:
            period_dates = self._extract_dates(period_value)
            if permit_start_date is None and period_dates:
                permit_start_date = period_dates[0]
            if permit_end_date is None and len(period_dates) >= 2:
                permit_end_date = period_dates[1]

        dumping_volume = (
            self._extract_volume_by_labels(normalized, ("투기량",))
            or compact_fields.get("dumping_volume")
        )
        collection_volume = (
            self._extract_volume_by_labels(normalized, ("채취량", "준설량"))
            or compact_fields.get("collection_volume")
        )
        amount = self._extract_amount(normalized) or compact_fields.get("amount")

        permittee_name = self._sanitize_permittee_field(permittee_name)
        location = self._sanitize_location_field(location)
        area = self._sanitize_area_field(area)
        purpose = self._sanitize_purpose_field(purpose)

        values = (
            org_name,
            permittee_name,
            location,
            area,
            purpose,
            collection_volume,
            permit_start_date,
            permit_end_date,
            amount,
        )
        confidence = round(sum(value is not None for value in values) / self.REQUESTED_FIELD_COUNT, 3)

        return PermitExtractedFields(
            org_name=org_name,
            permittee_name=permittee_name,
            location=location,
            area=area,
            purpose=purpose,
            collection_volume=collection_volume,
            permit_start_date=permit_start_date,
            permit_end_date=permit_end_date,
            amount=amount,
            confidence=confidence,
            source_text=normalized[:2000] or None,
        )

    @classmethod
    def _sanitize_permittee_field(cls, value: Optional[str]) -> Optional[str]:
        cleaned = cls._clean_permittee(value or "")
        if not cleaned:
            return None
        compact = re.sub(r"\s+", "", cleaned)
        if len(cleaned) > 100 or cls._has_table_markup(cleaned):
            return None
        if re.match(r"^[은는이가]\s+", cleaned):
            return None
        if any(token in compact for token in ("신청내역", "허가기간", "면적", "장소", "목적")):
            return None
        if re.search(r"\d[\d,.]*\s*(?:㎡|m2|m²|㎥|m3|m³)", cleaned, re.I):
            return None
        if "허가일로부터" in cleaned:
            return None
        if any(token in cleaned for token in ("수신", "제목", "붙임", "결재", "공개구분", "등록번호")):
            return None
        if re.search(r"(?:합니다|하였|하시|승인코자|검토|통보|제출|허가를|신청한|신청하)", cleaned):
            return None
        return cleaned

    @classmethod
    def _sanitize_location_field(cls, value: Optional[str]) -> Optional[str]:
        cleaned = cls._clean_value(value or "")
        if not cleaned or cls._has_table_markup(cleaned):
            return None
        compact = re.sub(r"\s+", "", cleaned)
        if any(
            token in compact
            for token in (
                "목적면적", "목적허가면적", "면적목적", "면적/목적", "사용목적면적", "사용목적점용기간",
                "점용기간면적", "면적기간", "신청기간", "부과액", "납부기간"
            )
        ):
            return None
        if re.search(r"(?:직접|간접)\s*\d[\d,.]*\s*(?:㎡|m2|m²)", cleaned, re.I):
            return None
        attached_sentence = re.match(r"(.+?공유수면)(?:을|를)\s*['\"“‘]?\S.{0,120}?목적", cleaned)
        if attached_sentence:
            cleaned = cls._clean_value(attached_sentence.group(1)) or cleaned
        if re.search(r"(?:허가를\s*받은|실태조사|확인(?:하고|되었습니다)|부과하고|지시한|제출하였|검토\s*의뢰|회신하여\s*주시기)", cleaned):
            return None
        cleaned = cls._prefer_final_slash_part(cleaned, cls._looks_like_location_value)
        cleaned = cls._dedupe_marked_items(cleaned)
        return cleaned

    @classmethod
    def _sanitize_area_field(cls, value: Optional[str]) -> Optional[str]:
        cleaned = cls._clean_value(value or "")
        if not cleaned or cls._has_table_markup(cleaned):
            return None
        compact = re.sub(r"\s+", "", cleaned)
        if any(token in compact for token in ("부과액", "납부기간", "사용목적", "허가기간")):
            return None
        if not re.search(r"\d", cleaned):
            return None
        if re.search(r"(?:합니다|하였|검토|통보|제출|승인코자|붙임|첨부서류|누락)", cleaned):
            return None
        if "번지" in cleaned and re.search(r"\d{4}[.\-년]\s*\d{1,2}", cleaned):
            return None
        area_parts = cls._split_top_level_slash(cleaned)
        if not (
            len(area_parts) == 2
            and re.match(r"^\(?직접\)?", area_parts[0].strip())
            and re.match(r"^\(?간접\)?", area_parts[1].strip())
        ):
            cleaned = cls._prefer_final_slash_part(cleaned, cls._looks_like_area_value)
        if len(cleaned) > 80 and cls.DATE_PATTERN.search(cleaned):
            return None
        return cleaned

    @classmethod
    def _sanitize_purpose_field(cls, value: Optional[str]) -> Optional[str]:
        cleaned = cls._clean_value(value or "")
        if not cleaned or cls._has_table_markup(cleaned):
            return None
        compact = re.sub(r"\s+", "", cleaned)
        if compact.startswith(("목적면적", "면적기간", "사용목적면적", "신청위치/면적", "신청위치면적")):
            return None
        if any(token in compact for token in ("부과액", "납부기간", "기간연장")):
            return None
        if re.search(r"(?:승인\s*신청|승인하였|알려드리오니|준수하시기|붙임과\s*같이)", cleaned):
            return None
        cleaned = cls._prefer_final_slash_part(cleaned, cls._looks_like_purpose_value)
        cleaned = cls._prefer_repeated_purpose_tail(cleaned)
        cleaned = cls._extract_equipment_purpose(cleaned)
        return cleaned

    @classmethod
    def _prefer_final_slash_part(cls, value: str, predicate) -> str:
        parts = cls._split_top_level_slash(value)
        if len(parts) < 2:
            return value
        valid_parts = [part for part in parts if predicate(part)]
        if len(valid_parts) < 2:
            return value
        return valid_parts[-1]

    @staticmethod
    def _split_top_level_slash(value: str) -> list[str]:
        parts: list[str] = []
        start = 0
        depth = 0
        for idx, char in enumerate(value):
            if char in "([":
                depth += 1
            elif char in ")]" and depth > 0:
                depth -= 1
            elif char == "/" and depth == 0:
                before = value[idx - 1] if idx > 0 else ""
                after = value[idx + 1] if idx + 1 < len(value) else ""
                if before.isspace() and after.isspace():
                    part = value[start:idx].strip()
                    if part:
                        parts.append(part)
                    start = idx + 1
        last = value[start:].strip()
        if last:
            parts.append(last)
        return parts

    @staticmethod
    def _looks_like_location_value(value: str) -> bool:
        cleaned = value.strip()
        if len(cleaned) < 8:
            return False
        if any(token in cleaned for token in ("검토", "회신", "신청서", "반려하오니")):
            return False
        return bool(
            re.search(r"(?:번지|지선|공유수면|해상|해역|EEZ|[NS],?\s*\d{2,3}[-°◦])", cleaned, re.I)
            or re.search(r"\d{2}[-°◦]\d{2}.*[NE]", cleaned)
        )

    @staticmethod
    def _looks_like_area_value(value: str) -> bool:
        cleaned = value.strip()
        if re.search(r"\d{4}[.\-년]\s*\d{1,2}", cleaned) and not re.search(r"(?:㎡|m2|m²|㎥|m3|m³)", cleaned, re.I):
            return False
        return bool(re.search(r"\d[\d,.]*\s*(?:㎡|m2|m²|㎥|m3|m³)", cleaned, re.I))

    @staticmethod
    def _looks_like_purpose_value(value: str) -> bool:
        cleaned = value.strip()
        if len(cleaned) < 6:
            return False
        if any(token in cleaned for token in ("번지", "지선", "신청 위치", "부과액", "납부기간")):
            return False
        return bool(re.search(r"[가-힣]", cleaned))

    @staticmethod
    def _dedupe_marked_items(value: str) -> str:
        marker_pattern = r"[ⓐⓑⓒⓓⓔⓕ①②③④⑤⑥⑦⑧⑨]"
        matches = list(re.finditer(marker_pattern, value))
        if len(matches) < 2:
            return value

        prefix = value[: matches[0].start()].strip()
        segments: list[str] = []
        seen: set[str] = set()
        for idx, match in enumerate(matches):
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(value)
            segment = value[match.start():end].strip()
            key = re.sub(r"\s+", "", segment)
            if key and key not in seen:
                seen.add(key)
                segments.append(segment)

        if len(segments) == len(matches):
            return value
        body = " ".join(segments)
        return f"{prefix} {body}".strip() if prefix else body

    @staticmethod
    def _prefer_repeated_purpose_tail(value: str) -> str:
        starters = (
            "선박수리조선소",
            "공기부양정",
            "선박 접안시설",
        )
        for starter in starters:
            second = value.find(starter, len(starter))
            if second > 0:
                tail = value[second:].strip()
                if len(tail) >= 30:
                    return tail
        return value

    @staticmethod
    def _extract_equipment_purpose(value: str) -> str:
        if "다목적 인양기" not in value:
            return value
        source = value.rsplit("/", 1)[-1]
        matches = re.findall(r"다목적\s*인양기\([^)]*급\)\s*\d+\s*기", source)
        if len(matches) >= 2:
            return ", ".join(match.strip() for match in matches)
        return value

    @staticmethod
    def _has_table_markup(value: str) -> bool:
        return "[TABLE_START]" in value or "[TABLE_END]" in value or "|" in value

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = []
        for raw_line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    @classmethod
    def _extract_compact_fields(cls, text: str) -> dict[str, object]:
        fields: dict[str, object] = {}

        org_match = re.search(
            r"([가-힣A-Za-z0-9]+(?:지방해양항만청|지방해양수산청|해양수산청|해양항만청|항만청))수신",
            text,
        )
        if org_match:
            fields["org_name"] = cls._clean_org_name(org_match.group(1))

        for extractor in (
            cls._extract_collapsed_permit_detail_table,
            cls._extract_collapsed_application_table,
            cls._extract_collapsed_occupation_collection_table,
            cls._extract_collapsed_position_area_table,
            cls._extract_markdown_table_fields,
            cls._extract_change_table,
            cls._extract_inline_collapsed_summary_table,
            cls._extract_inline_application_summary_table,
            cls._extract_compact_applicant_location_area_period_table,
            cls._extract_narrative_fields,
        ):
            for key, value in extractor(text).items():
                if value is not None and key not in fields:
                    fields[key] = value

        return fields

    @classmethod
    def _extract_markdown_table_fields(cls, text: str) -> dict[str, object]:
        fields: dict[str, object] = {}
        for table_match in re.finditer(r"\[TABLE_START\](?P<body>.*?)\[TABLE_END\]", text, re.S):
            rows = cls._parse_markdown_table_rows(table_match.group("body"))
            if not rows:
                continue
            for key, value in cls._fields_from_key_value_rows(rows).items():
                fields.setdefault(key, value)
            for key, value in cls._fields_from_tabular_rows(rows).items():
                fields.setdefault(key, value)
        return fields

    @classmethod
    def _parse_markdown_table_rows(cls, table_body: str) -> list[list[str]]:
        rows = []
        for raw_line in table_body.splitlines():
            cells = cls._parse_pipe_cells(raw_line)
            if cells:
                rows.append(cells)
        if len(rows) > 1:
            return rows

        cells = cls._parse_pipe_cells(table_body)
        if not cells:
            return rows

        separator_start = next(
            (index for index, cell in enumerate(cells) if cls._is_separator_cell(cell)),
            None,
        )
        if separator_start is None:
            return [cells]

        header = cells[:separator_start]
        separator_end = separator_start
        while separator_end < len(cells) and cls._is_separator_cell(cells[separator_end]):
            separator_end += 1
        width = len(header)
        if width == 0:
            return []

        rebuilt = [header, cells[separator_start:separator_end]]
        body = cells[separator_end:]
        rebuilt.extend(body[index:index + width] for index in range(0, len(body), width))
        return [row for row in rebuilt if row]

    @staticmethod
    def _parse_pipe_cells(value: str) -> list[str]:
        cleaned = re.sub(r"\[/?TABLE_(?:START|END)\]", "", value or "")
        cells = []
        for cell in cleaned.split("|"):
            stripped = cell.strip()
            if stripped:
                cells.append(stripped)
        return cells

    @staticmethod
    def _is_separator_cell(value: str) -> bool:
        return bool(re.fullmatch(r":?-{2,}:?", (value or "").strip()))

    @classmethod
    def _fields_from_key_value_rows(cls, rows: list[list[str]]) -> dict[str, object]:
        fields: dict[str, object] = {}
        for row in rows:
            if len(row) < 2 or cls._is_separator_row(row):
                continue
            if sum(cls._table_field_name(cell) is not None for cell in row) > 1:
                continue
            label = row[0]
            value = " / ".join(cell for cell in row[1:] if cell)
            cls._assign_table_field(fields, label, value)
        return fields

    @classmethod
    def _fields_from_tabular_rows(cls, rows: list[list[str]]) -> dict[str, object]:
        fields: dict[str, object] = {}
        for index, row in enumerate(rows[:-1]):
            if cls._is_separator_row(row) or not any(cls._table_field_name(cell) for cell in row):
                continue
            header = row
            data_row = next((candidate for candidate in rows[index + 1:] if not cls._is_separator_row(candidate)), None)
            if not data_row:
                continue
            for column, value in zip(header, data_row):
                cls._assign_table_field(fields, column, value)
            break
        return fields

    @classmethod
    def _assign_table_field(cls, fields: dict[str, object], label: str, value: str) -> None:
        field_name = cls._table_field_name(label)
        cleaned = cls._clean_value(value)
        if not field_name or not cleaned:
            return

        if field_name == "area":
            normalized = cls._normalize_area_from_table(label, cleaned)
            if normalized:
                fields.setdefault(field_name, normalized)
            return

        if field_name == "permit_period":
            dates = cls._extract_dates(cleaned)
            if dates:
                fields.setdefault("permit_start_date", dates[0])
            if len(dates) >= 2:
                fields.setdefault("permit_end_date", dates[1])
            return

        if field_name == "amount":
            amount_match = cls.AMOUNT_PATTERN.search(cleaned)
            if amount_match:
                fields.setdefault("amount", f"{amount_match.group(1)}원")
            return

        fields.setdefault(field_name, cleaned)

    @staticmethod
    def _is_separator_row(row: list[str]) -> bool:
        return bool(row) and all(PermitFieldExtractor._is_separator_cell(cell) for cell in row)

    @staticmethod
    def _table_field_name(label: str) -> Optional[str]:
        compact = re.sub(r"[\s·ㆍ․･.()\-/]", "", label or "")
        if compact in {"신청인", "피허가자", "신청자", "업체명", "명칭", "성명"}:
            return "permittee_name"
        if compact in {"위치", "위치지선", "장소", "장소지선", "소재지", "점용사용위치", "점사용위치"}:
            return "location"
        if compact.startswith("면적"):
            return "area"
        if compact in {"목적", "목적용도", "용도", "사용목적", "점용목적", "점용사용목적", "점사용목적", "공사내용"}:
            return "purpose"
        if compact in {"기간", "허가기간", "점용기간", "점용사용기간", "점사용기간"}:
            return "permit_period"
        if compact in {"금액", "사용료", "점용료", "점사용료", "점용료사용료"}:
            return "amount"
        return None

    @classmethod
    def _normalize_area_from_table(cls, label: str, value: str) -> Optional[str]:
        area = cls._clean_value(value)
        if not area:
            return None
        if re.search(r"(㎡|m2|m²|㎥|m3|m³)", area, re.I):
            return area
        if "㎡" in label:
            return f"{area}㎡"
        if re.search(r"m2|m²", label, re.I):
            return f"{area}m2"
        return area

    @classmethod
    def _extract_collapsed_permit_detail_table(cls, text: str) -> dict[str, object]:
        if "허가번호" not in text or "점용장소" not in text or "점사용료" not in text:
            return {}

        body_match = re.search(r"허가번호.*?성\s*명\s*(?P<body>\d{4}-\d+.+)", text, re.S)
        if not body_match:
            return {}

        body = body_match.group("body").strip()
        permit_no_match = re.match(r"\d{4}-\d+", body)
        if permit_no_match:
            body = body[permit_no_match.end():]

        date = r"\d{4}\.\d{1,2}\.\d{1,2}\.?"
        tail_match = re.search(
            rf"(?P<prefix>.+?)(?P<area>\d[\d,]*(?:\.\d{{1,2}})?)(?P<amount>\d{{1,3}}(?:,\d{{3}})+)"
            rf"(?P<start>{date})\s*~?\s*(?P<end>{date})",
            body,
            re.S,
        )
        if not tail_match:
            return {}

        prefix = tail_match.group("prefix").strip()
        location_start = cls._find_last_location_start(prefix)
        if location_start is None:
            return {}
        applicant_part = prefix[:location_start]
        location_part = prefix[location_start:]
        permittee = cls._extract_trailing_permittee(applicant_part)
        location, purpose = cls._split_location_and_purpose(location_part)

        fields: dict[str, object] = {
            "permittee_name": permittee,
            "location": location,
            "purpose": purpose,
            "area": f"{tail_match.group('area')}㎡",
            "amount": f"{tail_match.group('amount')}원",
        }

        dates = cls._extract_dates(f"{tail_match.group('start')} {tail_match.group('end')}")
        if dates:
            fields["permit_start_date"] = dates[0]
        if len(dates) >= 2:
            fields["permit_end_date"] = dates[1]
        return fields

    @classmethod
    def _extract_collapsed_occupation_collection_table(cls, text: str) -> dict[str, object]:
        if "신청인점용" not in text or "채취량" not in text:
            return {}

        match = re.search(
            r"신청인점용[·․･ㆍ.]?사용\s*장소점용[·․･ㆍ.]?사용\s*목적면적\(㎡\)\s*/채취량\(㎥\)"
            r"점용[·․･ㆍ.]?사용\s*기간(?P<body>.+)",
            text,
            re.S,
        )
        if not match:
            return {}

        return cls._parse_compact_applicant_location_purpose_measure(
            match.group("body"),
            volume_required=True,
        )

    @classmethod
    def _extract_collapsed_position_area_table(cls, text: str) -> dict[str, object]:
        if "신청인점용" not in text or "직접간접계" not in text:
            return {}

        match = re.search(r"직접간접계\s*(?P<body>.+)", text, re.S)
        if not match:
            return {}

        return cls._parse_compact_applicant_location_purpose_measure(
            match.group("body"),
            volume_required=False,
        )

    @classmethod
    def _parse_compact_applicant_location_purpose_measure(
        cls,
        body: str,
        *,
        volume_required: bool,
    ) -> dict[str, object]:
        measure_match = re.search(
            r"(?P<prefix>.+?)(?P<area>\d[\d,]*(?:\.\d+)?\s*㎡)"
            r"(?P<rest>.*)",
            body,
            re.S,
        )
        if not measure_match:
            return {}

        prefix = measure_match.group("prefix").strip()
        applicant_location = cls._split_applicant_and_location_prefix(prefix)
        if applicant_location is None:
            return {}

        permittee_part, location_part = applicant_location
        permittee = cls._clean_value(permittee_part)
        location, purpose = cls._split_location_and_purpose(location_part)
        if not permittee or not location:
            return {}

        fields: dict[str, object] = {
            "permittee_name": permittee,
            "location": location,
            "purpose": purpose,
            "area": cls._normalize_unit_value(measure_match.group("area")),
        }

        rest = measure_match.group("rest")
        volume_match = re.search(r"(?:회당\s*약\s*)?(\d[\d,]*(?:\.\d+)?\s*(?:㎥|m3|m³))", rest, re.I)
        if volume_match:
            fields["collection_volume"] = cls._normalize_unit_value(volume_match.group(1))
        elif volume_required:
            return {}

        return fields

    @classmethod
    def _extract_change_table(cls, text: str) -> dict[str, object]:
        if "변경 전" not in text or "변경 후" not in text or "장" not in text:
            return {}

        match = re.search(
            r"장\s*소(?P<location>.+?)면\s*적(?P<area>.+?)목\s*적(?P<purpose>.+?)기\s*간(?P<period>.+)",
            text,
            re.S,
        )
        if not match:
            return {}

        location = cls._clean_value(match.group("location"))
        area = cls._clean_value(match.group("area"))
        purpose = cls._clean_value(match.group("purpose"))
        period = match.group("period")

        if area:
            area = re.sub(r"변경\s*없음.*$", "", area).strip()
        if purpose:
            purpose = re.sub(r"변경\s*없음.*$", "", purpose).strip()

        fields: dict[str, object] = {
            "location": location,
            "area": area,
            "purpose": purpose,
        }

        dates = cls._extract_dates(period)
        if len(dates) >= 4:
            fields["permit_start_date"] = dates[2]
            fields["permit_end_date"] = dates[3]
        elif len(dates) >= 2:
            fields["permit_start_date"] = dates[0]
            fields["permit_end_date"] = dates[1]

        return fields

    @classmethod
    def _extract_inline_collapsed_summary_table(cls, text: str) -> dict[str, object]:
        if "위" not in text or "면" not in text or "간" not in text:
            return {}

        header_match = re.search(
            r"(?:피승인자|신청인|신\s*청\s*자)\s*위\s*치\s*면\s*적(?:\([^)]*\))?.{0,40}?기\s*간(?P<body>.+)",
            text,
            re.S,
        )
        if not header_match:
            return {}

        area_match = re.search(
            r"(?P<prefix>.+?)(?P<area>\d[\d,]*(?:\.\d+)?\s*(?:㎡|m2|m²))(?P<rest>.+)",
            header_match.group("body").strip(),
            re.S | re.I,
        )
        if not area_match:
            return {}

        applicant_location = cls._split_applicant_and_location_prefix(area_match.group("prefix"))
        if applicant_location is None:
            return {}

        permittee_part, location_part = applicant_location
        permittee = cls._clean_value(permittee_part)
        location, purpose_before_area = cls._split_location_and_purpose(location_part)
        if not permittee or not location:
            return {}

        purpose_after_area, period_text = cls._split_inline_purpose_and_period(area_match.group("rest"))
        purpose = purpose_before_area or purpose_after_area

        fields: dict[str, object] = {
            "permittee_name": permittee,
            "location": location,
            "area": cls._normalize_unit_value(area_match.group("area")),
        }
        if purpose:
            fields["purpose"] = purpose

        dates = cls._extract_dates(period_text)
        if dates:
            fields["permit_start_date"] = dates[0]
        if len(dates) >= 2:
            fields["permit_end_date"] = dates[1]
        return fields

    @classmethod
    def _split_inline_purpose_and_period(cls, value: str) -> tuple[Optional[str], str]:
        cleaned = value or ""
        marker_indexes = [
            match.start()
            for pattern in (cls.DATE_PATTERN, cls.SHORT_DATE_PATTERN)
            for match in pattern.finditer(cleaned)
        ]
        for marker in ("허가일", "착공일", "협의일", "승인일"):
            index = cleaned.find(marker)
            if index >= 0:
                marker_indexes.append(index)

        if marker_indexes:
            split_at = min(marker_indexes)
            purpose = cls._clean_value(cleaned[:split_at])
            period = cleaned[split_at:]
        else:
            purpose = cls._clean_value(cleaned)
            period = ""

        if purpose:
            purpose = re.sub(r"(?:끝|붙임).*$", "", purpose).strip()
            purpose = cls._clean_value(purpose)
        return purpose, period

    @classmethod
    def _extract_inline_application_summary_table(cls, text: str) -> dict[str, object]:
        header_match = re.search(
            r"(?:신\s*청\s*자|신청인|성\s*명)\s*위\s*치\s*사\s*용\s*목\s*적\s*"
            r"면\s*적(?:\((?P<unit>㎡|m2|m²)\))?\s*기\s*간(?P<body>.+)",
            text,
            re.S | re.I,
        )
        if not header_match:
            return {}

        body = header_match.group("body").strip()
        row_match = re.search(
            r"(?P<prefix>.+?)(?P<area>\d[\d,]*(?:\.\d+)?)(?P<period>\s*(?:허가일|착공일|협의일|승인일|(?:\d{4}|[`'‘’]?\d{2})[.\-/]).+)",
            body,
            re.S,
        )
        if not row_match:
            return {}

        applicant_location = cls._split_inline_applicant_and_location_prefix(row_match.group("prefix"))
        if applicant_location is None:
            return {}

        permittee_part, location_purpose = applicant_location
        permittee = cls._clean_value(permittee_part)
        location, purpose = cls._split_location_and_purpose(location_purpose)
        if not permittee or not location:
            return {}

        unit = header_match.group("unit") or "㎡"
        fields: dict[str, object] = {
            "permittee_name": permittee,
            "location": location,
            "area": cls._normalize_unit_value(f"{row_match.group('area')}{unit}"),
        }
        if purpose:
            fields["purpose"] = purpose

        dates = cls._extract_dates(row_match.group("period"))
        if dates:
            fields["permit_start_date"] = dates[0]
        if len(dates) >= 2:
            fields["permit_end_date"] = dates[1]
        return fields

    @classmethod
    def _split_inline_applicant_and_location_prefix(cls, text: str) -> Optional[tuple[str, str]]:
        split = cls._split_applicant_and_location_prefix(text)
        if split is not None:
            return split

        compact = cls._clean_value(text)
        if not compact:
            return None
        for match in re.finditer(r"(?:읍|면|동)", compact):
            location_start = match.start() - 2
            if location_start > 0:
                return compact[:location_start], compact[location_start:]
        return None

    @classmethod
    def _extract_compact_applicant_location_area_period_table(cls, text: str) -> dict[str, object]:
        if "신청인" not in text or "위" not in text or "목" not in text or "허가" not in text:
            return {}

        match = re.search(
            r"신청인위\s*치목\s*적면\s*적허가\s*기간(?P<body>.+)",
            text,
            re.S,
        )
        if not match:
            return {}

        body = match.group("body").strip()
        tail_match = re.search(
            r"(?P<prefix>.+?)(?P<area>\d[\d,]*(?:\.\d+)?\s*(?:㎡|m2|m²))"
            r"(?P<start>\d{4}\.\d{1,2}\.\d{1,2}\.?)\s*~\s*(?P<end>\d{4}\.\d{1,2}\.\d{1,2}\.?)",
            body,
            re.S | re.I,
        )
        if not tail_match:
            return {}

        applicant_location = cls._split_applicant_and_location_prefix(tail_match.group("prefix"))
        if applicant_location is None:
            return {}

        permittee, location_purpose = applicant_location
        location, purpose = cls._split_location_and_purpose(location_purpose)
        dates = cls._extract_dates(f"{tail_match.group('start')} {tail_match.group('end')}")
        fields: dict[str, object] = {
            "permittee_name": cls._clean_value(permittee),
            "location": location,
            "purpose": purpose,
            "area": cls._normalize_unit_value(tail_match.group("area")),
        }
        if dates:
            fields["permit_start_date"] = dates[0]
        if len(dates) >= 2:
            fields["permit_end_date"] = dates[1]
        return fields

    @classmethod
    def _extract_collapsed_application_table(cls, text: str) -> dict[str, object]:
        header_match = re.search(
            r"신\s*청\s*자\s*위\s*치\s*사\s*용\s*목\s*적\s*면적\s*준설량\s*기간(?P<body>.+)",
            text,
            re.S,
        )
        if not header_match:
            return {}

        body = header_match.group("body").strip()
        tail_match = re.search(
            r"(?P<prefix>.+?)(?P<area>\d[\d,]*(?:\.\d+)?\s*(?:㎡|m2|m²))"
            r"(?P<volume>\d[\d,]*(?:\.\d+)?\s*(?:㎥|m3|m³|㎡|m2|m²))(?P<rest>.+)",
            body,
            re.S | re.IGNORECASE,
        )
        if not tail_match:
            return {}

        prefix = tail_match.group("prefix").strip()
        applicant_match = re.match(r"(?P<applicant>.+?(?:지사|시장|군수|구청장|청장|공사|공단))(?P<location_purpose>.+)", prefix)
        if not applicant_match:
            return {}

        location, purpose = cls._split_location_and_purpose(applicant_match.group("location_purpose"))
        fields: dict[str, object] = {
            "permittee_name": cls._clean_value(applicant_match.group("applicant")),
            "location": location,
            "purpose": purpose,
            "area": cls._normalize_unit_value(tail_match.group("area")),
            "collection_volume": cls._normalize_unit_value(tail_match.group("volume")),
        }

        receipt_match = re.search(r"협의서\s*접수\s*([0-9.\-/\s]+)", tail_match.group("rest"))
        if receipt_match:
            dates = cls._extract_dates(receipt_match.group(1))
            if dates:
                fields["first_permit_date"] = dates[0]
        return fields

    @classmethod
    def _extract_narrative_fields(cls, text: str) -> dict[str, object]:
        fields: dict[str, object] = {}

        dumping_match = re.search(r"투기량\s*[:：]\s*([0-9][0-9,]*(?:\.\d+)?\s*(?:㎥|m3|m³|㎡|m2|m²))", text, re.I)
        if dumping_match:
            fields["dumping_volume"] = cls._normalize_unit_value(dumping_match.group(1))

        dumping_area_match = re.search(r"투기면적\s*[:：]\s*([0-9][0-9,]*(?:\.\d+)?\s*(?:㎡|m2|m²))", text, re.I)
        if dumping_area_match:
            fields["area"] = cls._normalize_unit_value(dumping_area_match.group(1))

        quoted_parenthesized_match = re.search(
            r"(?P<applicant>.+?)(?:에서|으로부터|로부터)\s*신청한\s*[“\"](?P<purpose>[^”\"]+)[”\"]"
            r"\((?P<location>[^)]*(?:번지선|번지|지선|공유수면|해역|일원)[^)]*)\)\s*목적",
            text,
            re.S,
        )
        if quoted_parenthesized_match:
            applicant = cls._clean_narrative_applicant(quoted_parenthesized_match.group("applicant"))
            location = cls._clean_value(quoted_parenthesized_match.group("location"))
            purpose = cls._clean_value(quoted_parenthesized_match.group("purpose"))
            if applicant:
                fields["permittee_name"] = applicant
            if location:
                fields["location"] = location
            if purpose:
                fields["purpose"] = purpose

        location_before_purpose_match = re.search(
            r"(?P<applicant>.+?)(?:에서|으로부터|로부터|에서\s*제출한)\s+"
            r"(?P<location>.+?(?:번지선|번지|지선|공유수면|해역|일원))에\s+"
            r"(?P<purpose>.+?)\s*목적의\s*공유수면",
            text,
            re.S,
        )
        if location_before_purpose_match:
            applicant = cls._clean_narrative_applicant(location_before_purpose_match.group("applicant"))
            location = cls._clean_value(location_before_purpose_match.group("location"))
            purpose = cls._clean_value(location_before_purpose_match.group("purpose"))
            if location:
                location_start = cls._find_first_location_start(location)
                if location_start is not None and location_start > 0:
                    location = location[location_start:]
            if applicant:
                fields.setdefault("permittee_name", applicant)
            if location:
                fields.setdefault("location", location)
            if purpose:
                fields.setdefault("purpose", purpose)

        quoted_location_purpose_match = re.search(
            r"(?P<applicant>.+?)(?:에서|으로부터|로부터)\s+"
            r"(?P<location>.+?(?:번지선|번지|지선|공유수면|해역|일원))에\s*"
            r"[『“\"](?P<purpose>[^』”\"]+)[』”\"](?:를|을)\s*위한",
            text,
            re.S,
        )
        if quoted_location_purpose_match:
            applicant = cls._clean_narrative_applicant(quoted_location_purpose_match.group("applicant"))
            location = cls._clean_value(quoted_location_purpose_match.group("location"))
            purpose = cls._clean_value(quoted_location_purpose_match.group("purpose"))
            if location:
                location_start = cls._find_last_location_start(location)
                if location_start is not None and location_start > 0:
                    location = location[location_start:]
            if applicant:
                fields.setdefault("permittee_name", applicant)
            if location:
                fields.setdefault("location", location)
            if purpose:
                fields.setdefault("purpose", purpose)

        location_purpose_match = re.search(
            r"(?P<location>[가-힣A-Za-z0-9\s\-]+(?:번지선|번지|지선|일원|해역|공유수면)(?:의\s*공유수면)?)에\s*"
            r"[“\"](?P<purpose>[^”\"]+)[”\"](?:를|을)\s*위한",
            text,
            re.S,
        )
        if location_purpose_match:
            location = cls._clean_value(location_purpose_match.group("location"))
            if location:
                location_start = cls._find_last_location_start(location)
                if location_start is not None and location_start > 0:
                    location = location[location_start:]
            if location:
                fields.setdefault("location", location)
            purpose = cls._clean_value(location_purpose_match.group("purpose"))
            if purpose:
                fields.setdefault("purpose", purpose)

        collection_match = re.search(r"준설량\s*([0-9][0-9,]*(?:\.\d+)?\s*(?:㎥|m3|m³))", text, re.I)
        if collection_match:
            fields["collection_volume"] = cls._normalize_unit_value(collection_match.group(1))

        area_volume_match = re.search(
            r"면\s*적\s*\(\s*준설량\s*\)\s*[:：]?\s*"
            r"(?P<area>[0-9][0-9,]*(?:\.\d+)?\s*(?:㎡|m2|m²))\s*"
            r"\((?P<volume>[0-9][0-9,]*(?:\.\d+)?\s*(?:㎥|m3|m³|㎡|m2|m²))\)",
            text,
            re.I,
        )
        if area_volume_match:
            fields["area"] = cls._normalize_unit_value(area_volume_match.group("area"))
            fields["collection_volume"] = cls._normalize_unit_value(area_volume_match.group("volume"))

        return fields

    @classmethod
    def _clean_narrative_applicant(cls, value: str) -> Optional[str]:
        cleaned = cls._clean_value(value)
        if not cleaned:
            return None
        cleaned = re.sub(r"\[TABLE_START\].*?\[TABLE_END\]\s*", "", cleaned, flags=re.S).strip()
        cleaned = cls._take_last_numbered_item(cleaned)
        cleaned = re.sub(r"^[\d.\s가-힣]*관련입니다\.?\s*", "", cleaned)
        cleaned = re.sub(r"(?:으로부터|로부터).*$", "", cleaned).strip()
        cleaned = re.sub(r"\([^)]*(?:대표|대표이사)[^)]*\)", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cls._find_first_location_start(cleaned) == 0:
            trailing_name = re.search(r"\d[\d\-]*(?:번지)?\s+(?P<name>[가-힣A-Za-z㈜()·\s-]{2,50})$", cleaned)
            if trailing_name:
                cleaned = trailing_name.group("name")
        entity = cls._prefer_embedded_permittee_entity(cleaned)
        if entity:
            cleaned = entity
        return cls._clean_permittee(cleaned)

    @staticmethod
    def _prefer_embedded_permittee_entity(value: str) -> Optional[str]:
        text = value or ""
        marked = PermitFieldExtractor._extract_marked_org_name(text)
        if marked:
            return marked
        entity_patterns = (
            r"(?:\(주\)|㈜|\(유\))\s*[가-힣A-Za-z0-9·-]{2,30}(?:\s*대표(?:이사)?\s*[가-힣]{2,5})?",
            r"[가-힣A-Za-z0-9·-]{2,30}(?:주식회사|유한회사|\(주\)|㈜|\(유\))(?:\s*대표(?:이사)?\s*[가-힣]{2,5})?",
            r"[가-힣A-Za-z0-9·-]{2,30}(?:공사|공단|조합|어촌계|재단|법인)(?:\s*대표(?:이사)?\s*[가-힣]{2,5})?",
            r"[가-힣]{2,12}(?:시장|군수|구청장|군|시|구청|어촌계장)",
            r"민원인\s*\([^)]+\)",
        )
        matches: list[str] = []
        for pattern in entity_patterns:
            matches.extend(match.group(0).strip() for match in re.finditer(pattern, text))
        if not matches:
            return None
        candidate = matches[-1]
        meaningful = re.sub(r"[^가-힣A-Za-z0-9]", "", candidate)
        if len(meaningful) < 3:
            return None
        return candidate

    @staticmethod
    def _extract_marked_org_name(value: str) -> Optional[str]:
        text = value or ""
        suffix_markers = (
            "주식회사", "유한회사", "(주)", "㈜", "(유)",
            "공사", "공단", "조합", "어촌계", "재단", "법인",
        )
        prefix_markers = ("(주)", "㈜", "(유)")
        candidates: list[str] = []

        for marker in prefix_markers:
            start = text.find(marker)
            while start >= 0:
                end = start + len(marker)
                while end < len(text) and re.match(r"[가-힣A-Za-z0-9·-]", text[end]):
                    end += 1
                representative = re.match(r"\s*대표(?:이사)?\s*[가-힣]{2,5}", text[end:])
                if representative:
                    end += representative.end()
                candidate = text[start:end].strip()
                if len(re.sub(r"[^가-힣A-Za-z0-9]", "", candidate)) >= 3:
                    candidates.append(candidate)
                start = text.find(marker, start + len(marker))

        for marker in suffix_markers:
            end = text.find(marker)
            while end >= 0:
                stop = end + len(marker)
                start = end - 1
                while start >= 0 and re.match(r"[가-힣A-Za-z0-9·-]", text[start]):
                    start -= 1
                candidate = text[start + 1:stop].strip()
                if len(re.sub(r"[^가-힣A-Za-z0-9]", "", candidate)) >= 3:
                    candidates.append(candidate)
                end = text.find(marker, stop)

        return candidates[-1] if candidates else None

    @staticmethod
    def _take_last_numbered_item(value: str) -> str:
        cleaned = value or ""
        if not re.search(r"\d+\.", cleaned):
            return cleaned
        markers = [
            match
            for match in re.finditer(r"(?:^|\s)(\d{1,2})\.\s*", cleaned)
            if int(match.group(1)) < 20
        ]
        return cleaned[markers[-1].end():].strip() if markers else cleaned

    @staticmethod
    def _find_last_location_start(text: str) -> Optional[int]:
        known_indexes = []
        for token in PermitFieldExtractor.KNOWN_LOCATION_STARTS:
            known_indexes.extend(match.start() for match in re.finditer(re.escape(token), text))
        if known_indexes:
            selected = max(known_indexes)
            for province in ("경남", "경북", "전남", "전북", "충남", "충북", "강원", "경기", "제주"):
                province_index = text.rfind(province, 0, selected)
                if province_index >= 0 and selected - province_index <= 5:
                    return province_index
            return selected

        region_pattern = re.compile(
            r"(서울|부산|인천|대구|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주|"
            r"[가-힣]{2,}(?:시|군|구))"
        )
        matches = list(region_pattern.finditer(text))
        return matches[-1].start() if matches else None

    @classmethod
    def _split_applicant_and_location_prefix(cls, text: str) -> Optional[tuple[str, str]]:
        compact = cls._clean_value(text)
        if not compact:
            return None

        for pattern in (
            r"(?P<applicant>.+?(?:시장|군수|구청장|지사|청장|장관|대표\s*[가-힣A-Za-z0-9]+))(?P<location>.+)",
            r"(?P<applicant>.+?(?:외\s*\d+명))(?P<location>.+)",
        ):
            match = re.match(pattern, compact, re.S)
            if match and cls._find_first_location_start(match.group("location")) == 0:
                return match.group("applicant").strip(), match.group("location").strip()

        first_region = cls._find_first_location_start(compact)
        if first_region is None or first_region == 0:
            return None
        return compact[:first_region], compact[first_region:]

    @staticmethod
    def _find_first_location_start(text: str) -> Optional[int]:
        known_indexes = []
        for token in PermitFieldExtractor.KNOWN_LOCATION_STARTS:
            known_indexes.extend(match.start() for match in re.finditer(re.escape(token), text))
        if known_indexes:
            return min(known_indexes)

        region_pattern = re.compile(
            r"(서울|부산|인천|대구|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주|"
            r"[가-힣]{2,}(?:시|군|구))"
        )
        match = region_pattern.search(text)
        return match.start() if match else None

    @classmethod
    def _split_location_and_purpose(cls, text: str) -> tuple[Optional[str], Optional[str]]:
        endings = ("공유수면", "번지 지선", "번지선", "지선", "번지)", "번지", "일원", "해역")
        for ending in endings:
            index = text.find(ending)
            if index >= 0:
                best_end = index + len(ending)
                location = cls._clean_value(text[:best_end])
                purpose = cls._clean_value(text[best_end:])
                if purpose:
                    purpose = purpose.lstrip("(/ ").strip()
                return location, purpose or None

        return cls._clean_value(text), None

    @classmethod
    def _extract_trailing_permittee(cls, text: str) -> Optional[str]:
        cleaned = cls._clean_value(text)
        if not cleaned:
            return None
        for marker in ("호", "번지", "길"):
            index = cleaned.rfind(marker)
            if index >= 0 and index + len(marker) < len(cleaned):
                candidate = cleaned[index + len(marker):]
                candidate = re.sub(r"^[\d\s,\-]+", "", candidate)
                location_start = PermitFieldExtractor._find_first_location_start(candidate)
                if location_start and location_start > 0:
                    candidate = candidate[:location_start]
                if candidate:
                    return cls._clean_value(candidate)
        match = re.search(r"([가-힣A-Za-z㈜()·\s]+(?:외\s*\d+명)?)$", cleaned)
        return cls._clean_value(match.group(1)) if match else cleaned

    @classmethod
    def _normalize_unit_value(cls, value: str) -> Optional[str]:
        match = cls.VOLUME_PATTERN.search(value or "")
        if not match:
            return cls._clean_value(value)
        return f"{match.group(1)}{match.group(2)}"

    @staticmethod
    def _strip_list_marker(line: str) -> str:
        return re.sub(r"^(?:[가-힣]\s*[.)]|[oOㅇ]\s+|[○●□▪▫\-ㆍ·•])\s*", "", line).strip()

    @classmethod
    def _clean_value(cls, value: str) -> Optional[str]:
        cleaned = re.sub(r"\s+", " ", value or "").strip()
        cleaned = re.sub(r"^[\s:：=\-]+", "", cleaned).strip()
        cleaned = cleaned.rstrip(" .")
        if cls._is_noise_value(cleaned):
            return None
        return cleaned or None

    @staticmethod
    def _is_noise_value(value: str) -> bool:
        compact = re.sub(r"\s+", "", value or "")
        if not compact:
            return True
        if compact in {"|", "||", "|허가", "|허가|", "허가", "허가|", "|허가사항", "및"}:
            return True
        if compact in {"㎡", "(㎡)", "(㎡)|", "|(㎡)", "|(㎡)|"}:
            return True
        return bool(re.fullmatch(r"\|+", compact))

    @classmethod
    def _clean_org_name(cls, value: str) -> Optional[str]:
        cleaned = cls._clean_value(value)
        if not cleaned:
            return None
        cleaned = re.sub(r"\s+", "", cleaned)
        for marker in ("새시대", "미래로"):
            if marker in cleaned:
                cleaned = cleaned.split(marker)[-1]
        known_org = cls._find_known_org_name(cleaned)
        if known_org:
            return known_org
        if cls._looks_like_org_noise(cleaned):
            return None
        return cleaned or None

    @classmethod
    def _find_known_org_name(cls, value: str) -> Optional[str]:
        compact = re.sub(r"\s+", "", value or "")
        if compact in cls.KNOWN_ORG_NAMES:
            return compact
        matches = [org for org in cls.KNOWN_ORG_NAMES if org in compact]
        if not matches:
            return None
        return max(matches, key=len)

    @staticmethod
    def _looks_like_org_noise(value: str) -> bool:
        compact = re.sub(r"\s+", "", value or "")
        if not compact:
            return True
        if len(compact) > 80:
            return True
        if re.search(r"\d", compact):
            return True
        return any(
            token in compact
            for token in (
                "위치",
                "목적",
                "면적",
                "허가기간",
                "변경내역",
                "협의사항",
                "고시문",
                "산정조서",
                "허가증",
                "확인증",
                "신청서",
                "검토서",
                "사용료",
            )
        )

    @classmethod
    def _extract_labeled_line(cls, text: str, labels: tuple[str, ...]) -> Optional[str]:
        label_pattern = "|".join(re.escape(label) for label in labels)
        pattern = re.compile(rf"^(?:{label_pattern})(?:\([^)]*\))?\s*(?:[:：=\-])?\s*(.+)$")

        for raw_line in text.splitlines():
            line = cls._strip_list_marker(raw_line)
            match = pattern.search(line)
            if match:
                return cls._clean_value(match.group(1))
        return None

    @classmethod
    def _extract_org_name(cls, text: str) -> Optional[str]:
        explicit = cls._extract_labeled_line(text, ("기관명",))
        if explicit:
            cleaned_explicit = cls._clean_org_name(explicit)
            if cleaned_explicit:
                return cleaned_explicit

        patterned = cls._extract_org_name_by_pattern(text)
        if patterned:
            return patterned

        for raw_line in text.splitlines()[:20]:
            line = cls._strip_list_marker(raw_line)
            if not line or line.startswith(("수신", "참조", "제목", "붙임", "-")):
                continue
            if "|" in line or "제목" in line:
                continue
            if ":" in line or "：" in line:
                continue
            if len(line) <= 60 and cls.ORG_SUFFIX_PATTERN.search(line):
                cleaned_line = cls._clean_org_name(line)
                if cleaned_line:
                    return cleaned_line
        return cls._extract_org_name_from_url(text)

    @classmethod
    def _extract_org_name_by_pattern(cls, text: str) -> Optional[str]:
        compact_text = re.sub(r"\s+", "", text or "")

        org_patterns = (
            r"([가-힣]{1,8}지방해양수산청)",
            r"([가-힣]{1,8}지방해양항만청)",
            r"([가-힣]{1,8}지방항만청)",
        )
        search_scopes = []
        for marker in ("수신", "제목", "시행"):
            index = compact_text.find(marker)
            if index > 0:
                search_scopes.append(compact_text[:index])
        search_scopes.append(compact_text)

        for scope in search_scopes[:-1]:
            for pattern in org_patterns:
                match = re.search(pattern, scope)
                if match:
                    return cls._clean_org_name(match.group(1))
            if "해양수산부" in scope:
                return "해양수산부"

        full_scope = search_scopes[-1]
        for pattern in org_patterns:
            match = re.search(pattern, full_scope)
            if match:
                return cls._clean_org_name(match.group(1))
        if "해양수산부" in full_scope:
            return "해양수산부"
        return None

    @classmethod
    def _extract_org_name_from_url(cls, text: str) -> Optional[str]:
        lowered = (text or "").lower()
        for domain, org_name in cls.ORG_URL_FALLBACKS.items():
            if domain in lowered:
                return org_name
        return None

    @classmethod
    def _extract_permittee_name(cls, text: str) -> Optional[str]:
        explicit = cls._extract_labeled_line(text, ("피허가자명", "피허가자", "신청인", "신청자"))
        if explicit and not cls._looks_like_header_value(explicit):
            return cls._clean_permittee(explicit)

        for raw_line in text.splitlines()[:30]:
            line = raw_line.strip()
            if "수신" not in line or "수신자 참조" in line:
                continue
            match = re.search(r"수신(?!자)\s*(.+?)(?:\s*귀하|\(경유\)|제목|$)", line)
            if match:
                candidate = cls._clean_permittee(match.group(1))
                if (
                    candidate
                    and candidate not in {"내부결재", "수신자 참조"}
                    and not candidate.startswith("수신자")
                    and not re.search(r"(?:과장|담당관|팀장|주무관)$", candidate)
                    and not (candidate.endswith("계장") and "어촌계장" not in candidate)
                ):
                    return candidate

        applicant_match = re.search(
            r"([가-힣A-Za-z0-9㈜()·\s-]{2,80}?)(?:\([^)]*\))?\s*(?:으로부터|로부터).{0,80}허가\s*신청",
            text,
        )
        if applicant_match:
            candidate = cls._clean_narrative_applicant(applicant_match.group(1))
            if candidate and cls._looks_like_permittee_candidate(candidate):
                return candidate
        narrative = cls._extract_narrative_permittee_name(text)
        if narrative:
            return narrative
        return None

    @classmethod
    def _extract_narrative_permittee_name(cls, text: str) -> Optional[str]:
        search_text = text or ""
        represented = re.search(
            r"(?P<name>[^.\n]{2,80}?(?:주식회사|유한회사))"
            r"\([^)]*(?:대표|대표이사)[^)]*\)\s*(?:으로부터|로부터)",
            search_text,
        )
        if represented:
            return cls._clean_value(represented.group("name"))
        marked_submitter = re.search(
            r"(?P<name>[^.\n]{2,80}?(?:\(주\)|㈜|\(유\)))\s*에서[^.\n]{0,120}?신청",
            search_text,
        )
        if marked_submitter:
            candidate = cls._clean_narrative_applicant(marked_submitter.group("name"))
            if candidate and cls._looks_like_permittee_candidate(candidate):
                return candidate
        patterns = (
            r"(?P<name>[가-힣A-Za-z0-9㈜()·ㆍ\s-]{2,80}?)(?:의|에서|가|이)\s*"
            r"(?:제출한|신청한|협의\s*요청한)?[^.\n]{0,80}?(?:공유수면|점용|사용|매립)[^.\n]{0,80}?(?:허가|승인)[^.\n]{0,40}?신청",
            r"(?P<name>[가-힣A-Za-z0-9㈜()·ㆍ\s-]{2,80}?)(?:\([^)]*\))?\s*(?:으로부터|로부터)\s*"
            r"(?:제출된|제출한|신청받은|신청이|신청서가)?[^.\n]{0,80}?(?:공유수면|점용|사용|매립)[^.\n]{0,80}?(?:허가|승인)[^.\n]{0,40}?신청",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, search_text):
                candidate = cls._clean_narrative_applicant(match.group("name"))
                if (
                    candidate
                    and not cls._is_generic_addressee(candidate)
                    and cls._looks_like_permittee_candidate(candidate)
                ):
                    return candidate
        return None

    @staticmethod
    def _is_generic_addressee(value: str) -> bool:
        compact = re.sub(r"\s+", "", value or "")
        generic = {
            "귀하", "귀기관", "귀시", "귀군", "귀청", "희망", "국민",
            "공유수면", "사용", "변경", "허가", "승인", "우리청", "주식회사",
        }
        return (
            compact in generic
            or compact.startswith(("귀", "우리청"))
            or "새시대" in compact
            or "지방해양" in compact
        )

    @staticmethod
    def _looks_like_permittee_candidate(value: str) -> bool:
        compact = re.sub(r"\s+", "", value or "")
        if not compact or PermitFieldExtractor._is_generic_addressee(compact):
            return False
        if compact in {"주식회사", "(주)", "㈜", "유한회사", "(유)", "어촌계", "조합", "공사", "공단"}:
            return False
        if compact.endswith("도시"):
            return False
        corp_marker = re.match(r"^(?:㈜|\(주\)|\(유\))(.+)$", compact)
        if corp_marker and len(re.sub(r"[^가-힣A-Za-z0-9]", "", corp_marker.group(1))) < 2:
            return False
        entity_tokens = (
            "주식회사", "(주)", "㈜", "유한회사", "(유)", "재단법인", "사단법인",
            "공사", "공단", "조합", "어촌계", "회사", "법인", "재단", "발전",
            "건설", "산업", "개발", "해운", "수산", "에너지", "대표", "민원인",
        )
        if any(token in compact for token in entity_tokens):
            return True
        if re.fullmatch(r"[가-힣]{2,12}(?:시장|군수|구청장|군|시|구청|어촌계장)", compact):
            return True
        return False

    @staticmethod
    def _looks_like_header_value(value: str) -> bool:
        compact = re.sub(r"\s+", "", value or "")
        return (
            len(compact) > 100
            or compact.startswith(("점용", "위치", "성명", "주소", "신청내역", "장소", "목적", "면적"))
            or any(token in compact[:40] for token in ("점용", "허가기간", "신청내역", "면적", "장소", "목적"))
        )

    @staticmethod
    def _clean_permittee(value: str) -> Optional[str]:
        cleaned = re.sub(r"\(경유\)", "", value or "")
        cleaned = re.sub(r"\s*귀하\s*$", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = PermitFieldExtractor._clean_value(cleaned)
        if not cleaned:
            return None
        if PermitFieldExtractor._looks_like_header_value(cleaned):
            return None
        if any(marker in cleaned for marker in ("[TABLE_START]", "[TABLE_END]", "|")):
            return None
        return cleaned

    @classmethod
    def _extract_area(cls, text: str) -> Optional[str]:
        combined = cls._extract_labeled_line(text, ("면적 및 목적", "면적 및 용도", "면적 / 목적"))
        if combined:
            match = re.search(r"(.+?(?:㎡|m2|m²))", combined, re.IGNORECASE)
            return cls._clean_value(match.group(1) if match else combined)

        volume_area = cls._extract_labeled_line(text, ("면적(준설량)", "면 적(준설량)", "면적(또는 채취량·투기량)"))
        if volume_area:
            match = cls.VOLUME_PATTERN.search(volume_area)
            return f"{match.group(1)}{match.group(2)}" if match else volume_area

        area = cls._extract_labeled_line(text, ("면적", "면 적", "점용면적", "사용면적", "허가면적", "허가규모", "규모"))
        if area:
            return area

        return None

    @classmethod
    def _extract_purpose(cls, text: str) -> Optional[str]:
        combined = cls._extract_labeled_line(text, ("면적 및 목적", "면적 및 용도", "면적 / 목적"))
        if combined:
            paren_match = re.search(r"(?:㎡|m2|m²)\s*\(([^)]*)\)", combined, re.IGNORECASE)
            if paren_match:
                return cls._clean_value(paren_match.group(1))
            area_match = re.search(r".+?(?:㎡|m2|m²)\s*[/,]?\s*(.+)$", combined, re.IGNORECASE)
            if area_match:
                purpose = cls._clean_value(area_match.group(1))
                return purpose.lstrip("(/ ").strip() if purpose else None

        purpose = cls._extract_labeled_line(text, ("목적", "목 적", "허가목적", "점용목적", "사용목적", "신청목적", "용도"))
        if purpose:
            return purpose.lstrip("(/ ").strip()
        return None

    @classmethod
    def _extract_date_by_labels(cls, text: str, labels: tuple[str, ...]) -> Optional[date]:
        value = cls._extract_labeled_line(text, labels)
        if not value:
            return None
        dates = cls._extract_dates(value)
        return dates[0] if dates else None

    @classmethod
    def _extract_dates(cls, value: str) -> list[date]:
        dates: list[tuple[int, date]] = []
        for match in cls.DATE_PATTERN.finditer(value or ""):
            try:
                year, month, day = match.groups()
                dates.append((match.start(), date(int(year), int(month), int(day))))
            except ValueError:
                continue
        for match in cls.SHORT_DATE_PATTERN.finditer(value or ""):
            try:
                year, month, day = match.groups()
                dates.append((match.start(), date(2000 + int(year), int(month), int(day))))
            except ValueError:
                continue
        return [parsed for _, parsed in sorted(dates, key=lambda item: item[0])]

    @classmethod
    def _extract_volume_by_labels(cls, text: str, labels: tuple[str, ...]) -> Optional[str]:
        value = cls._extract_labeled_line(text, labels)
        if not value:
            return None
        match = cls.VOLUME_PATTERN.search(value)
        if not match:
            return value
        return f"{match.group(1)}{match.group(2)}"

    @classmethod
    def _extract_amount(cls, text: str) -> Optional[str]:
        amount_labels = ("금액", "점용료", "사용료", "점사용료", "점용료ㆍ사용료", "점용료·사용료")
        for raw_line in text.splitlines():
            if not any(label in raw_line for label in amount_labels):
                continue
            match = cls.AMOUNT_PATTERN.search(raw_line)
            if match:
                return f"{match.group(1)}원"

        match = cls.AMOUNT_PATTERN.search(text)
        if match:
            return f"{match.group(1)}원"
        return None
