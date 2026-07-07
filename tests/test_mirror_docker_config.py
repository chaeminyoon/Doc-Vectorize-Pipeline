from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mirror_test_compose_uses_plain_postgres_for_server_b():
    compose = (ROOT / "docker-compose.mirror-test.yml").read_text(encoding="utf-8")

    assert "image: postgres:15" in compose
    assert "pgvector/pgvector" not in compose.split("mirror-db:", 1)[1].split("sync:", 1)[0]


def test_sync_image_has_only_the_dependencies_needed_for_mirroring():
    dockerfile = (ROOT / "sync" / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "sync" / "requirements.txt").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["python", "/app/sync_to_mirror.py"]' in dockerfile
    assert "sqlalchemy" in requirements
    assert "psycopg2-binary" in requirements
    assert "pgvector" in requirements
