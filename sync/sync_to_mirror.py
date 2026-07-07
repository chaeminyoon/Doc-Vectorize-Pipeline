"""Copy pipeline records from pgvector PostgreSQL to plain PostgreSQL."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from sqlalchemy.engine import make_url

from src.vectordb.mirror_repository import MirrorRepository
from src.vectordb.repository import VectorRepository


def _masked_url(value: str) -> str:
    return make_url(value).render_as_string(hide_password=True)


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync pgvector pipeline output to plain PostgreSQL."
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop and recreate target mirror tables before syncing.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        source_url = _required_environment("SOURCE_DATABASE_URL")
        target_url = _required_environment("TARGET_DATABASE_URL")
        logging.info("Source: %s", _masked_url(source_url))
        logging.info("Target: %s", _masked_url(target_url))

        target = MirrorRepository(target_url)
        target.init_db(drop_existing=args.drop_existing)
        result = target.sync_from_vector_repository(VectorRepository(source_url))
    except Exception as exc:
        logging.error("Mirror sync failed: %s", exc)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
