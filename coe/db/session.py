from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from coe.config import get_settings


def make_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = sessionmaker(bind=make_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
