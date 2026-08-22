from pathlib import Path

import pytest

from coe.db.admin import reset_database  # noqa: F401  (re-exported for fixtures)
from coe.config import get_settings


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
