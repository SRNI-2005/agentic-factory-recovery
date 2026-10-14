import os
from pathlib import Path

# MUST run before any coe import: pytest targets the DEDICATED TEST
# database (:5433, container `timescaledb-test`), never the interactive
# demo DB (:5432). An explicit user override wins only in CI-like
# scenarios where someone wires a different test DB.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coe:coe@localhost:5433/coe",
)

import pytest  # noqa: E402

from coe.db.admin import reset_database  # noqa: F401  (re-exported for fixtures)
from coe.config import get_settings


@pytest.fixture(autouse=True)
def _isolate_settings_bindings():
    """Prevent cross-test pollution of the settings caching layer.

    Tests that monkeypatch/patch ``coe.config.get_settings`` can leave a
    stale binding behind if another module lazily does ``from coe.config
    import get_settings`` while the patch is active (e.g. ``make_engine``
    imports lazily inside ``coe.db.session``). Snapshot the pristine
    bindings before each test and restore them after; also clear the
    ``get_settings`` lru_cache so env/tests that prime it never leak
    cached values across tests.
    """
    import coe.db.session as db_session

    pristine_db_session_get_settings = db_session.get_settings
    yield
    db_session.get_settings = pristine_db_session_get_settings
    get_settings.cache_clear()


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


@pytest.fixture()
def session():
    """Plain SQLAlchemy session over the current DB state; never commits."""
    from sqlalchemy.orm import sessionmaker

    from coe.db.session import make_engine

    s = sessionmaker(bind=make_engine(), expire_on_commit=False)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def fake_llm():
    """Factory fixture: fake_llm(["resp", ...]) or fake_llm({key: resp})."""
    from tests.fixtures.llm.fake_client import FakeLLMClient

    def _make(responses):
        return FakeLLMClient(responses)

    return _make
