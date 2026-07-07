"""
ODT 벡터 DB 파이프라인 - 독립 실행 진입점
scripts/ 폴더 없이 src/ 모듈을 직접 호출하여 파이프라인 실행

시큐어 코딩 적용:
- CWE-20:  CLI 입력값 검증 (숫자 범위, 경로 존재, 쿼리 길이)
- CWE-22:  파일/디렉토리 경로 탐색 방지
- CWE-532: DB URL 로깅 시 비밀번호 마스킹
- CWE-209: 에러 메시지에서 민감 정보 제거
"""
import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from config import settings
from src.converters import HWPImageExtractor
from src.pipeline.processor import DocumentPipeline
from src.quality import sanitize_path_text
from src.vectordb.mirror_repository import MirrorRepository
from src.vectordb.repository import VectorRepository

# ── 상수 ──────────────────────────────────────────
_MAX_QUERY_LENGTH = 500
_MAX_WORKERS = 32
_MAX_BATCH_SIZE = 512
_MAX_CHUNK_SIZE = 8192
_MIN_CHUNK_SIZE = 64
_MAX_TOP_K = 1000
_MAX_INDEX_LISTS = 10000


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


# ── 입력 검증 유틸리티 ────────────────────────────

def _get_allowed_input_roots() -> list[Path]:
    roots: list[Path] = [Path(__file__).resolve().parent]

    for configured_root in settings.data_dir_list:
        try:
            roots.append(Path(configured_root).resolve(strict=False))
        except (OSError, RuntimeError, ValueError):
            continue

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        unique_roots.append(root)
        seen.add(key)

    return unique_roots


def _is_path_within_allowed_roots(candidate: Path, allowed_roots: list[Path]) -> bool:
    for root in allowed_roots:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _validate_positive_int(value: int, name: str, max_val: int) -> int:
    """CWE-20: 양의 정수 범위 검증"""
    if value < 1:
        print(f"[ERROR] {name}은(는) 1 이상이어야 합니다. (입력값: {value})")
        sys.exit(1)
    if value > max_val:
        print(f"[ERROR] {name}은(는) {max_val} 이하여야 합니다. (입력값: {value})")
        sys.exit(1)
    return value


def _validate_file_path(path_str: str) -> Path:
    """CWE-22: 파일 경로 검증"""
    p = Path(path_str)

    # 경로 탐색 공격 방지: resolve 후 확인
    try:
        resolved = p.resolve(strict=False)
    except (OSError, ValueError):
        print(f"[ERROR] 잘못된 파일 경로입니다: {path_str}")
        sys.exit(1)

    if not resolved.exists():
        print(f"[ERROR] 파일이 존재하지 않습니다: {path_str}")
        sys.exit(1)

    if not resolved.is_file():
        print(f"[ERROR] 파일이 아닙니다: {path_str}")
        sys.exit(1)

    if not _is_path_within_allowed_roots(resolved, _get_allowed_input_roots()):
        print(f"[ERROR] 허용된 입력 루트 밖의 파일입니다: {resolved.name}")
        sys.exit(1)

    return resolved


def _validate_dir_paths(dir_list: list[str]) -> list[str]:
    """CWE-22: 디렉토리 경로 검증"""
    validated = []
    allowed_roots = _get_allowed_input_roots()
    for d in dir_list:
        p = Path(d)
        try:
            resolved = p.resolve(strict=False)
        except (OSError, ValueError):
            print(f"[WARNING] 잘못된 디렉토리 경로 건너뜀: {d}")
            continue

        if not resolved.exists():
            print(f"[WARNING] 디렉토리 미존재, 건너뜀: {d}")
            continue

        if not resolved.is_dir():
            print(f"[WARNING] 디렉토리가 아닌 경로 건너뜀: {d}")
            continue

        if not _is_path_within_allowed_roots(resolved, allowed_roots):
            print(f"[WARNING] 허용된 입력 루트 밖 디렉토리 건너뜀: {resolved.name}")
            continue

        validated.append(str(resolved))

    if not validated:
        print("[ERROR] 유효한 디렉토리가 없습니다.")
        sys.exit(1)

    return validated


def _validate_query(query: str) -> str:
    """CWE-20: 검색 쿼리 길이 제한"""
    stripped = query.strip()
    if not stripped:
        print("[ERROR] 검색 쿼리가 비어 있습니다.")
        sys.exit(1)
    if len(stripped) > _MAX_QUERY_LENGTH:
        print(f"[WARNING] 쿼리가 {_MAX_QUERY_LENGTH}자로 잘립니다.")
        stripped = stripped[:_MAX_QUERY_LENGTH]
    return stripped


def _get_masked_db_url(database_url: str | None = None) -> str:
    """CWE-532: 로깅/출력용 마스킹된 DB URL"""
    if database_url:
        try:
            return make_url(database_url).render_as_string(hide_password=True)
        except Exception:
            return "<invalid database url>"
    return settings.masked_database_url


def _get_mirror_database_url(args: argparse.Namespace) -> str | None:
    return getattr(args, "mirror_database_url", None) or settings.mirror_database_url


def _should_extract_hwp_images(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "extract_hwp_images", False) or settings.extract_hwp_images)


def _should_clean_hwp_images(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "image_clean", False))


def _should_skip_pipeline(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "skip_pipeline", False))


def _run_hwp_image_cleanup(
    directories: list[str],
    show_progress: bool,
    logger: logging.Logger,
) -> None:
    extractor = HWPImageExtractor()
    deleted = extractor.clean_directories(directories, show_progress=show_progress)
    logger.info("HWP image cleanup complete: deleted=%d", deleted)


def _run_hwp_image_extraction(
    directories: list[str],
    show_progress: bool,
    logger: logging.Logger,
) -> None:
    extractor = HWPImageExtractor()
    result = extractor.extract_directories(directories, show_progress=show_progress)
    logger.info(
        "HWP image extraction complete: total=%d, extracted=%d, skipped=%d, failed=%d, images=%d",
        result.total_files,
        result.extracted_files,
        result.skipped_files,
        result.failed_files,
        result.total_images,
    )


# ── 커맨드 핸들러 ─────────────────────────────────

def cmd_run(args: argparse.Namespace):
    """전체 파이프라인 실행 (init-db → process → stats)"""
    logger = logging.getLogger("main")
    skip_pipeline = _should_skip_pipeline(args)

    # CWE-20: 입력값 검증
    _validate_positive_int(args.chunk_size, "chunk-size", _MAX_CHUNK_SIZE)
    _validate_positive_int(args.chunk_overlap, "chunk-overlap", args.chunk_size - 1)
    _validate_positive_int(args.batch_size, "batch-size", _MAX_BATCH_SIZE)
    _validate_positive_int(args.workers, "workers", _MAX_WORKERS)

    if args.chunk_size < _MIN_CHUNK_SIZE:
        print(f"[ERROR] chunk-size는 {_MIN_CHUNK_SIZE} 이상이어야 합니다.")
        sys.exit(1)

    if skip_pipeline and args.init_db:
        print("[ERROR] --skip-pipeline cannot be combined with --init-db.")
        sys.exit(1)

    pipeline = None
    if not skip_pipeline:
        # CWE-532: DB URL 마스킹 로깅
        logger.info("DB target: %s", _get_masked_db_url(args.database_url or settings.database_url))
        pipeline = DocumentPipeline(
            database_url=args.database_url or settings.database_url,
            use_mock_embedder=args.mock,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            batch_size=args.batch_size,
            exclude_boilerplate_table=args.exclude_boilerplate or None,
        )

    # 1) DB 초기화
    if args.init_db:
        if pipeline is None:
            logger.error("Pipeline is not initialized.")
            sys.exit(1)

        logger.info("Initializing database...")
        pipeline.init_database(drop_existing=args.drop_existing)
        logger.info("Database initialized.")

    # 2) 디렉토리 / 파일 처리
    if args.file:
        # CWE-22: 파일 경로 검증
        validated_path = _validate_file_path(args.file)
        if _should_clean_hwp_images(args):
            logger.warning("HWP image cleanup is ignored in single-file mode.")
        if _should_extract_hwp_images(args):
            logger.warning("HWP image extraction is ignored in single-file mode.")
        if skip_pipeline:
            logger.info("Skipping vector pipeline as requested.")
            print("\n" + "=" * 50)
            print("Pipeline Skipped")
            print("=" * 50)
            print("Single-file mode does not run image cleanup or extraction.")
            return
        
        if pipeline is None:
            logger.error("Pipeline is not initialized.")
            sys.exit(1)

        logger.info("Processing single file: %s", validated_path.name)
        result = pipeline.process_file(str(validated_path))
    else:
        directories = args.directories or [str(d) for d in settings.data_dir_list]
        # CWE-22: 디렉토리 경로 검증
        directories = _validate_dir_paths(directories)
        if _should_clean_hwp_images(args):
            logger.info("Cleaning extracted HWP image directories...")
            _run_hwp_image_cleanup(
                directories,
                show_progress=not args.no_progress,
                logger=logger,
            )
        if _should_extract_hwp_images(args):
            logger.info("Extracting embedded images from HWP files...")
            _run_hwp_image_extraction(
                directories,
                show_progress=not args.no_progress,
                logger=logger,
            )
        if skip_pipeline:
            if not (_should_clean_hwp_images(args) or _should_extract_hwp_images(args)):
                logger.warning("Skip-pipeline requested without image preprocessing options.")
                print("[WARNING] Nothing to do: use --skip-pipeline with --image-clean and/or --extract-hwp-images.")
                return
            logger.info("Skipping vector pipeline after HWP image preprocessing.")
            print("\n" + "=" * 50)
            print("Pipeline Skipped")
            print("=" * 50)
            print(f"Directories:       {', '.join(directories)}")
            print(f"Image cleanup:     {_should_clean_hwp_images(args)}")
            print(f"Image extraction:  {_should_extract_hwp_images(args)}")
            return
        
        if pipeline is None:
            logger.error("Pipeline is not initialized.")
            sys.exit(1)

        logger.info("Processing %d directories", len(directories))
        result = pipeline.process_directories(
            directories,
            incremental=not args.no_incremental,
            show_progress=not args.no_progress,
            max_workers=args.workers,
        )

    # 3) 결과 출력
    print("\n" + "=" * 50)
    print("Pipeline Execution Complete")
    print("=" * 50)
    print(result)

    # CWE-209: 에러 메시지에서 전체 경로 대신 파일명만 노출
    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors[:10]:
            # 경로가 포함될 수 있으므로 파일명만 추출
            safe_err = _sanitize_error_message(err)
            print(f"  - {_console_safe_text(safe_err)}")
        if len(result.errors) > 10:
            print(f"  ... and {len(result.errors) - 10} more")

    # 4) DB 통계
    if pipeline is None:
        logger.error("Pipeline is not initialized.")
        sys.exit(1)

    try:
        stats = pipeline.get_stats()
        print("\nDatabase Stats:")
        print(f"  Documents: {stats['document_count']}")
        print(f"  Chunks:    {stats['chunk_count']}")
        print(f"  By type:   {stats['chunks_by_type']}")
    except SQLAlchemyError:
        # CWE-209: 예외 상세 정보 외부 노출 방지
        logger.warning("Could not retrieve database statistics.")


def cmd_init_db(args: argparse.Namespace):
    """DB 초기화만 실행"""
    logger = logging.getLogger("main")

    # CWE-532: DB URL 마스킹 로깅
    db_url = args.database_url or settings.database_url
    logger.info("DB target: %s", _get_masked_db_url(db_url))
    repo = VectorRepository(db_url)
    repo.init_db(drop_existing=args.drop_existing)
    logger.info("Database initialized.")

    if args.create_index:
        # CWE-20: index-lists 범위 검증
        _validate_positive_int(args.index_lists, "index-lists", _MAX_INDEX_LISTS)
        repo.create_vector_index(lists=args.index_lists)
        logger.info("Vector index created (lists=%d).", args.index_lists)


def cmd_sync_to_mirror(args: argparse.Namespace):
    """서버 A PostgreSQL 결과를 서버 B PostgreSQL로 동기화"""
    logger = logging.getLogger("main")

    source_db_url = args.database_url or settings.database_url
    mirror_database_url = _get_mirror_database_url(args)
    if not mirror_database_url:
        print("[ERROR] MIRROR_DATABASE_URL is required for sync-to-mirror.")
        sys.exit(1)

    logger.info("Source DB target: %s", _get_masked_db_url(source_db_url))
    logger.info("Mirror DB target: %s", _get_masked_db_url(mirror_database_url))

    source_repo = VectorRepository(source_db_url)
    mirror_repo = MirrorRepository(mirror_database_url)
    mirror_repo.init_db(drop_existing=args.drop_existing)
    result = mirror_repo.sync_from_vector_repository(source_repo)

    print("\n" + "=" * 50)
    print("Mirror Sync Complete")
    print("=" * 50)
    print(result)


def cmd_search(args: argparse.Namespace):
    """벡터 검색"""
    # CWE-20: 쿼리 검증
    query = _validate_query(args.query)
    top_k = _validate_positive_int(args.top_k, "top-k", _MAX_TOP_K)

    pipeline = DocumentPipeline(
        database_url=args.database_url or settings.database_url,
    )

    results = pipeline.search(query=query, top_k=top_k)

    print(f"\nSearch results (top {top_k})\n")
    for i, r in enumerate(results, 1):
        print(f"--- [{i}] similarity={r['similarity']:.4f} | mode={r['search_mode']} ---")
        print(f"  doc_id:    {r['doc_id']}")
        print(f"  title:     {r['title']}")
        print(f"  dept:      {r['dept_name']}")
        print(f"  category:  {r['biz_full_path']}")
        # CWE-209: 텍스트 출력 길이 제한
        text_preview = (r['text'] or "")[:200]
        print(f"  text:      {text_preview}...")
        print()


def cmd_stats(args: argparse.Namespace):
    """DB 통계 조회"""
    logger = logging.getLogger("main")
    db_url = args.database_url or settings.database_url
    logger.info("DB target: %s", _get_masked_db_url(db_url))
    repo = VectorRepository(db_url)
    stats = repo.get_stats()

    print("\nDatabase Stats:")
    print(f"  Documents: {stats['document_count']}")
    print(f"  Chunks:    {stats['chunk_count']}")
    print(f"  By type:   {stats['chunks_by_type']}")


# ── 유틸 ──────────────────────────────────────────

def _sanitize_error_message(msg: str) -> str:
    """CWE-209: 에러 메시지에서 전체 파일 경로를 파일명만으로 치환"""
    import re
    # Windows/Unix 풀 경로 패턴 → basename만 남김
    return sanitize_path_text(msg)


def _console_safe_text(value: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="replace").decode(encoding, errors="replace")


# ── CLI 파서 ──────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ODT Vector DB Pipeline (standalone, secure coding applied)"
    )
    # CWE-532: --database-url은 help에서 비밀번호 예시 제거
    parser.add_argument(
        "--database-url", type=str,
        help="DB URL (overrides .env). 비밀번호 포함 주의.",
    )
    parser.add_argument(
        "--mirror-database-url", type=str,
        help="Optional mirror DB URL for plain PostgreSQL insert output.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    sub = parser.add_subparsers(dest="command")

    # ---- run ----
    run_p = sub.add_parser("run", help="전체 파이프라인 실행")
    run_p.add_argument("-d", "--directories", nargs="+", help="처리할 디렉토리 목록")
    run_p.add_argument("-f", "--file", type=str, help="단일 파일 처리")
    run_p.add_argument("--init-db", action="store_true", help="실행 전 DB 초기화")
    run_p.add_argument("--drop-existing", action="store_true", help="기존 테이블 삭제 후 재생성")
    run_p.add_argument("--no-incremental", action="store_true", help="증분 업데이트 비활성화")
    run_p.add_argument("--mock", action="store_true", help="Mock 임베더 (GPU 없이 테스트)")
    run_p.add_argument("--exclude-boilerplate", action="store_true", help="Boilerplate 테이블 제외")
    run_p.add_argument("--chunk-size", type=int, default=1024)
    run_p.add_argument("--chunk-overlap", type=int, default=128)
    run_p.add_argument("--batch-size", type=int, default=32)
    run_p.add_argument("--workers", type=int, default=4, help="병렬 워커 수")
    run_p.add_argument("--no-progress", action="store_true", help="진행률 바 비활성화")

    run_p.add_argument("--skip-pipeline", action="store_true", help="이미지 전처리만 수행하고 벡터 파이프라인은 건너뜀")
    run_p.add_argument("--image-clean", action="store_true", help="HWP 추출 이미지 디렉터리를 먼저 정리")
    run_p.add_argument("--extract-hwp-images", action="store_true", help="HWP 내장 이미지 추출 후 계속 진행")

    # ---- init-db ----
    init_p = sub.add_parser("init-db", help="DB 초기화만 실행")
    init_p.add_argument("--drop-existing", action="store_true")
    init_p.add_argument("--create-index", action="store_true", help="벡터 인덱스 생성")
    init_p.add_argument("--index-lists", type=int, default=100, help="IVFFlat lists 값")

    # ---- search ----
    search_p = sub.add_parser("search", help="벡터 검색")
    search_p.add_argument("query", type=str, help="검색 쿼리")
    search_p.add_argument("--top-k", type=int, default=10)

    # ---- stats ----
    sub.add_parser("stats", help="DB 통계 조회")

    # ---- sync-to-mirror ----
    sync_p = sub.add_parser("sync-to-mirror", help="서버 A DB 결과를 서버 B DB로 동기화")
    sync_p.add_argument("--drop-existing", action="store_true", help="서버 B 미러 테이블 삭제 후 재생성")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)

    if args.command is None:
        parser.print_help()
        print("\n사용 예시:")
        print("  python main.py run --init-db               # DB 초기화 + 파이프라인 실행")
        print("  python main.py run --mock                   # Mock 임베더로 테스트")
        print("  python main.py run -f path/to/file.odt      # 단일 파일 처리")
        print("  python main.py init-db --drop-existing      # DB 초기화만")
        print("  python main.py search '공유수면 매립'        # 벡터 검색")
        print("  python main.py stats                        # DB 통계")
        print("  python main.py sync-to-mirror               # 서버 A DB -> 서버 B DB 동기화")
        return

    dispatch = {
        "run": cmd_run,
        "init-db": cmd_init_db,
        "search": cmd_search,
        "stats": cmd_stats,
        "sync-to-mirror": cmd_sync_to_mirror,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
