from __future__ import annotations

import re
from pathlib import Path


_ABSOLUTE_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s:]+|/[^\s:]+")


def safe_path_name(path: str | Path) -> str:
    try:
        name = Path(path).name
    except (TypeError, ValueError):
        return "<invalid-path>"
    return name or "<unnamed>"


def sanitize_path_text(value: object) -> str:
    text = str(value)
    return _ABSOLUTE_PATH_PATTERN.sub(
        lambda match: safe_path_name(match.group()),
        text,
    )
