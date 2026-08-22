import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from coe.config import get_settings


def reset_database(url: str) -> None:
    """Drop every user table in public, then rebuild via Alembic (authoritative DDL)."""
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(
            text(
                "DO $do$ DECLARE r record; BEGIN "
                "FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP "
                "EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
                "END LOOP; END $do$;"
            )
        )
    eng.dispose()
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)


@pytest.fixture(scope="session")
def db_url() -> str:
    return get_settings().database_url


@pytest.fixture()
def clean_db(db_url):
    reset_database(db_url)
    yield db_url


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"
