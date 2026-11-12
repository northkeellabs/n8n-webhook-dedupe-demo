import uuid

import pytest

from dedupe_demo.config import load_settings


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture()
def db(settings):
    """Live Postgres from the compose stack (python scripts/demo.py setup)."""
    import psycopg

    try:
        conn = psycopg.connect(settings.pg_conninfo(), autocommit=True)
    except psycopg.OperationalError as exc:  # pragma: no cover
        pytest.skip(f"Postgres container not reachable: {exc.__class__.__name__}")
    yield conn
    conn.execute("DELETE FROM processed_events WHERE event_key LIKE 'test_%'")
    conn.close()


@pytest.fixture()
def key():
    return f"test_{uuid.uuid4().hex[:12]}"
