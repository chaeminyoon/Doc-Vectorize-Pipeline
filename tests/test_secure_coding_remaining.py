import logging
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

import main
from config.settings import Settings
from src.embeddings.bge_embedder import BGEM3Embedder
from src.metadata.loader import MetadataLoader
from src.parsers.odt_parser import ODTParser
from src.pipeline.processor import DocumentPipeline
from src.vectordb.mirror_repository import MirrorRepository
from src.vectordb.models import DocumentChunk


def test_main_rejects_file_outside_allowed_input_roots(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    outside_file = tmp_path / "outside" / "secret.odt"
    outside_file.parent.mkdir()
    outside_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(main, "_get_allowed_input_roots", lambda: [allowed_root.resolve()])

    with pytest.raises(SystemExit):
        main._validate_file_path(str(outside_file))


def test_main_accepts_file_within_allowed_input_roots(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    allowed_file = allowed_root / "sample.odt"
    allowed_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(main, "_get_allowed_input_roots", lambda: [allowed_root.resolve()])

    assert main._validate_file_path(str(allowed_file)) == allowed_file.resolve()


def test_processor_logs_sanitized_file_paths(tmp_path, monkeypatch, caplog):
    secret_path = tmp_path / "sensitive" / "doc.odt"
    nested_secret = tmp_path / "sensitive" / "inner" / "payload.odt"

    pipeline = object.__new__(DocumentPipeline)
    pipeline.parser = type(
        "ExplodingParser",
        (),
        {
            "parse": lambda self, _path: (_ for _ in ()).throw(
                ValueError(f"failed to open {nested_secret}")
            )
        },
    )()

    monkeypatch.setattr(DocumentPipeline, "_ensure_metadata_loaded", lambda self: None)

    with caplog.at_level(logging.ERROR):
        result = DocumentPipeline.process_file(pipeline, secret_path)

    assert result.failed_files == 1
    assert nested_secret.name in caplog.text
    assert str(nested_secret.parent) not in caplog.text
    assert str(secret_path.parent) not in caplog.text


def test_odt_parser_does_not_expose_full_path_in_missing_content_xml_error(tmp_path):
    odt_path = tmp_path / "private" / "sample.odt"
    odt_path.parent.mkdir(parents=True)

    with zipfile.ZipFile(odt_path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")

    parser = ODTParser()

    with pytest.raises(ValueError) as exc_info:
        parser._read_content_xml(odt_path)

    message = str(exc_info.value)
    assert odt_path.name in message
    assert str(odt_path.parent) not in message


def test_metadata_loader_logs_missing_file_without_full_path(tmp_path, caplog):
    missing_path = tmp_path / "private" / "shared.xlsx"
    loader = MetadataLoader(missing_path)

    with caplog.at_level(logging.WARNING):
        loader.load()

    assert missing_path.name in caplog.text
    assert str(missing_path.parent) not in caplog.text


def test_bge_embedder_rejects_unapproved_remote_model_name():
    with pytest.raises(ValueError):
        BGEM3Embedder(model_name="evil/model")


def test_bge_embedder_accepts_existing_local_model_path(tmp_path):
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()

    embedder = BGEM3Embedder(model_name=str(model_dir))

    assert Path(embedder.model_name) == model_dir.resolve()


def test_settings_default_paths_match_current_repo_layout():
    settings = Settings(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        _env_file=None,
    )

    assert settings.data_dirs == "./data"
    assert settings.metadata_doc_list == "./metadata"


def test_settings_accepts_optional_mirror_database_url():
    settings = Settings(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        mirror_database_url="postgresql://mirror:secret@example.com:5432/outputdb",
        _env_file=None,
    )

    assert settings.mirror_database_url == "postgresql://mirror:secret@example.com:5432/outputdb"
    assert "secret" not in settings.masked_mirror_database_url


def test_mirror_repository_stores_embedding_as_float_array_values():
    chunk = DocumentChunk(
        chunk_id="CH-1",
        doc_id="DOC-1",
        page_num=1,
        chunk_num=0,
        chunk_type="text",
        text="sample",
        embedding=[1, 2.5, "3.0"],
    )

    values = MirrorRepository._chunk_values(chunk)

    assert values["embedding"] == [1.0, 2.5, 3.0]


def test_settings_expands_existing_data_root_into_child_directories(tmp_path):
    data_root = tmp_path / "data"
    year_2025 = data_root / "2025"
    year_2026 = data_root / "2026"
    (data_root / "notes.txt").parent.mkdir(parents=True, exist_ok=True)
    year_2026.mkdir(parents=True)
    year_2025.mkdir(parents=True)
    (data_root / "notes.txt").write_text("ignore", encoding="utf-8")

    settings = Settings(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        data_dirs=str(data_root),
        _env_file=None,
    )

    assert settings.data_dir_list == [year_2025, year_2026]


def test_settings_keeps_missing_data_root_as_configured_path(tmp_path):
    data_root = tmp_path / "missing-data"

    settings = Settings(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        data_dirs=str(data_root),
        _env_file=None,
    )

    assert settings.data_dir_list == [data_root]


def test_onprem_env_example_enables_image_extraction_and_uses_directory_metadata():
    env_text = Path(".env.onprem.example").read_text(encoding="utf-8")

    assert "DATA_DIRS=/app/data" in env_text
    assert "METADATA_DOC_LIST=/app/metadata" in env_text
    assert "EXTRACT_HWP_IMAGES=true" in env_text


def test_bundle_onprem_env_example_matches_onprem_defaults():
    env_text = Path("bundle/SFR-003/.env.onprem.example").read_text(encoding="utf-8")

    assert "DATA_DIRS=/app/data" in env_text
    assert "METADATA_DOC_LIST=/app/metadata" in env_text
    assert "EXTRACT_HWP_IMAGES=true" in env_text


def test_root_onprem_docs_exist_for_docker_build_and_bundle():
    assert Path("HOW_TO_RUN.md").is_file()
    assert Path("ONPREM_DOCKER.md").is_file()


def test_cmd_run_uses_expanded_data_root_directories(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    first = data_root / "2025"
    second = data_root / "2026"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    seen: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_directories(self, directories, incremental, show_progress, max_workers):
            seen["directories"] = directories
            seen["incremental"] = incremental
            seen["show_progress"] = show_progress
            seen["max_workers"] = max_workers
            return _FakeRunResult()

        def get_stats(self):
            return {
                "document_count": 0,
                "chunk_count": 0,
                "chunks_by_type": {},
            }

    settings_obj = Settings(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        data_dirs=str(data_root),
        _env_file=None,
    )

    monkeypatch.setattr(main, "settings", settings_obj)
    monkeypatch.setattr(main, "DocumentPipeline", FakePipeline)

    args = _make_run_args()
    args.file = None

    main.cmd_run(args)

    assert seen["directories"] == [str(first.resolve()), str(second.resolve())]


def test_console_safe_text_replaces_unencodable_characters(monkeypatch):
    class FakeStdout:
        encoding = "cp949"

    monkeypatch.setattr(main.sys, "stdout", FakeStdout())

    safe = main._console_safe_text("bad \u2024 char")

    assert "\u2024" not in safe
    assert "?" in safe


class _FakeRunResult:
    errors: list[str] = []

    def __str__(self) -> str:
        return "fake-result"


def _make_run_args() -> Namespace:
    return Namespace(
        database_url="postgresql://tester:secret@example.com:5432/vectordb",
        mirror_database_url=None,
        file="dummy.odt",
        directories=None,
        init_db=False,
        drop_existing=False,
        no_incremental=False,
        mock=True,
        exclude_boilerplate=False,
        chunk_size=1024,
        chunk_overlap=128,
        batch_size=32,
        workers=1,
        no_progress=True,
        skip_pipeline=False,
        image_clean=False,
        extract_hwp_images=False,
    )


def test_cmd_run_hides_sqlalchemy_stats_error(tmp_path, monkeypatch, caplog):
    file_path = tmp_path / "sample.odt"
    file_path.write_text("", encoding="utf-8")

    class FakePipeline:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_file(self, _path):
            return _FakeRunResult()

        def get_stats(self):
            raise SQLAlchemyError("db unavailable")

    monkeypatch.setattr(main, "DocumentPipeline", FakePipeline)
    monkeypatch.setattr(main, "_validate_file_path", lambda _path: file_path)

    with caplog.at_level(logging.WARNING):
        main.cmd_run(_make_run_args())

    assert "Could not retrieve database statistics." in caplog.text


def test_cmd_sync_to_mirror_uses_source_and_mirror_repositories(monkeypatch):
    seen = {}

    class FakeVectorRepository:
        def __init__(self, database_url, ivfflat_probes=200):
            seen["source_url"] = database_url

    class FakeMirrorRepository:
        def __init__(self, database_url):
            seen["mirror_url"] = database_url

        def init_db(self, drop_existing=False):
            seen["drop_existing"] = drop_existing

        def sync_from_vector_repository(self, source_repo):
            seen["source_repo_type"] = type(source_repo).__name__
            return "sync-result"

    monkeypatch.setattr(main, "VectorRepository", FakeVectorRepository)
    monkeypatch.setattr(main, "MirrorRepository", FakeMirrorRepository)

    args = _make_run_args()
    args.command = "sync-to-mirror"
    args.mirror_database_url = "postgresql://mirror:secret@example.com:5432/outputdb"

    main.cmd_sync_to_mirror(args)

    assert seen["source_url"] == args.database_url
    assert seen["mirror_url"] == args.mirror_database_url
    assert seen["drop_existing"] is False
    assert seen["source_repo_type"] == "FakeVectorRepository"


def test_cmd_sync_to_mirror_requires_mirror_database_url():
    args = _make_run_args()
    args.command = "sync-to-mirror"
    args.mirror_database_url = None

    with pytest.raises(SystemExit):
        main.cmd_sync_to_mirror(args)


def test_cmd_run_propagates_non_database_stats_error(tmp_path, monkeypatch):
    file_path = tmp_path / "sample.odt"
    file_path.write_text("", encoding="utf-8")

    class FakePipeline:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_file(self, _path):
            return _FakeRunResult()

        def get_stats(self):
            raise RuntimeError("unexpected bug")

    monkeypatch.setattr(main, "DocumentPipeline", FakePipeline)
    monkeypatch.setattr(main, "_validate_file_path", lambda _path: file_path)

    with pytest.raises(RuntimeError, match="unexpected bug"):
        main.cmd_run(_make_run_args())
