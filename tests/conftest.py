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


@pytest.fixture()
def demo_scenario(clean_db):
    """All three sources imported + factory_demo_01 built with seed 42 -> id."""
    from pathlib import Path

    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))
    return build_scenario("factory_demo_01", seed=42)
