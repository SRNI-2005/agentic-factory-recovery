import subprocess

from sqlalchemy import create_engine, text

from coe.config import get_settings


def reset_database(url: str | None = None) -> None:
    """Drop every user table in public, then rebuild via Alembic (authoritative DDL).
    Development-only and destructive (spec §10)."""
    url = url or get_settings().database_url
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
