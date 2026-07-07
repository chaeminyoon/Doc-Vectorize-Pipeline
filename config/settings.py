"""Application settings with secure validation."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


_ALLOWED_DEVICES = {"cpu", "cuda"}
_ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_MIN_CHUNK_SIZE = 64
_MAX_CHUNK_SIZE = 8192
_MIN_CHUNK_OVERLAP = 0
_MAX_BATCH_SIZE = 512
_MAX_TIMEOUT = 3600
_YEAR_DIR_PATTERN = re.compile(r"^\d{4}$")


class Settings(BaseSettings):
    """Runtime settings with secure defaults and validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        description="PostgreSQL connection URL."
    )
    mirror_database_url: Optional[str] = Field(
        default=None,
        description="Optional PostgreSQL URL for mirroring pipeline output without pgvector.",
    )

    embedding_model: str = Field(
        default="BAAI/bge-m3",
        description="Embedding model name.",
    )
    embedding_device: str = Field(
        default="cpu",
        description="Embedding runtime device.",
    )
    embedding_dimension: int = Field(
        default=1024,
        ge=1,
        le=4096,
        description="Embedding vector dimension.",
    )

    chunk_size: int = Field(
        default=1024,
        ge=_MIN_CHUNK_SIZE,
        le=_MAX_CHUNK_SIZE,
        description="Maximum chunk size in tokens.",
    )
    chunk_overlap: int = Field(
        default=128,
        ge=_MIN_CHUNK_OVERLAP,
        description="Chunk overlap size in tokens.",
    )

    data_dirs: str = Field(
        default="./data",
        description="Comma-separated data directories.",
    )
    metadata_doc_list: str = Field(
        default="./metadata",
        description="Metadata file or directory path.",
    )

    batch_size: int = Field(
        default=32,
        ge=1,
        le=_MAX_BATCH_SIZE,
        description="Batch size for processing.",
    )
    ivfflat_probes: int = Field(
        default=200,
        ge=1,
        le=10000,
        description="pgvector IVFFlat probes value.",
    )
    log_level: str = Field(
        default="INFO",
        description="Application log level.",
    )
    exclude_boilerplate_table: bool = Field(
        default=False,
        description="Skip boilerplate table chunks.",
    )

    convert_hwpx: bool = Field(
        default=False,
        description="Convert HWPX/HWP files to PDF before parsing.",
    )
    extract_hwp_images: bool = Field(
        default=False,
        description="Extract embedded images from HWP files.",
    )
    soffice_path: str = Field(
        default="",
        description="LibreOffice executable path.",
    )
    hwpx_convert_timeout: int = Field(
        default=180,
        ge=1,
        le=_MAX_TIMEOUT,
        description="HWPX conversion timeout in seconds.",
    )

    @field_validator("embedding_device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_DEVICES:
            raise ValueError(
                f"embedding_device must be one of {_ALLOWED_DEVICES}. "
                f"(received: {value!r})"
            )
        return normalized

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _ALLOWED_LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {_ALLOWED_LOG_LEVELS}. "
                f"(received: {value!r})"
            )
        return normalized

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "database_url is required. Set DATABASE_URL in .env or pass it explicitly."
            )

        stripped = value.strip()
        try:
            url = make_url(stripped)
        except ArgumentError as exc:
            raise ValueError(f"Invalid database_url format: {exc}") from exc

        if url.drivername and not url.drivername.startswith("postgresql"):
            raise ValueError(
                f"Only PostgreSQL URLs are supported. (received: {url.drivername})"
            )
        return stripped

    @field_validator("mirror_database_url")
    @classmethod
    def validate_mirror_database_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            return None

        try:
            url = make_url(stripped)
        except ArgumentError as exc:
            raise ValueError(f"Invalid mirror_database_url format: {exc}") from exc

        if url.drivername and not url.drivername.startswith("postgresql"):
            raise ValueError(
                f"Only PostgreSQL URLs are supported. (received: {url.drivername})"
            )
        return stripped

    @model_validator(mode="after")
    def validate_chunk_overlap_less_than_size(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap({self.chunk_overlap}) must be smaller than "
                f"chunk_size({self.chunk_size})."
            )
        return self

    @field_validator("soffice_path")
    @classmethod
    def validate_soffice_path(cls, value: str) -> str:
        if not value:
            return value

        try:
            Path(value).resolve(strict=False)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid soffice_path: {exc}") from exc
        return value

    @property
    def data_dir_list(self) -> list[Path]:
        expanded_paths: list[Path] = []

        for item in self.data_dirs.split(","):
            path = Path(item.strip())
            expanded_paths.extend(self._expand_data_dir_path(path))

        return expanded_paths

    @staticmethod
    def _expand_data_dir_path(path: Path) -> list[Path]:
        """Expand a data root into its year-like child directories when applicable."""
        if path.name.lower() != "data":
            return [path]

        try:
            if not path.exists() or not path.is_dir():
                return [path]
        except OSError:
            return [path]

        child_dirs = sorted(
            child for child in path.iterdir()
            if child.is_dir() and _YEAR_DIR_PATTERN.fullmatch(child.name)
        )
        return child_dirs or [path]

    @property
    def metadata_doc_list_path(self) -> Path:
        return Path(self.metadata_doc_list)

    @property
    def masked_database_url(self) -> str:
        try:
            return make_url(self.database_url).render_as_string(hide_password=True)
        except (TypeError, ValueError, ArgumentError):
            return "<invalid-database-url>"

    @property
    def masked_mirror_database_url(self) -> str:
        if not self.mirror_database_url:
            return "<not-configured>"
        try:
            return make_url(self.mirror_database_url).render_as_string(hide_password=True)
        except (TypeError, ValueError, ArgumentError):
            return "<invalid-database-url>"


settings = Settings()
