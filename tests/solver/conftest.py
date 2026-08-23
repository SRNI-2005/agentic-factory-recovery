import pytest

MK01 = "data/raw/mk01/mk01.txt"
SFJW = "data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"
GASS = "data/raw/gass"


@pytest.fixture(scope="session")
def built_db():
    """Reset once per session, import sources, build factory_demo_01.
    Read-only tests share this; state-mutating tests (Parts 3/5) must create
    their own instances or reset themselves."""
    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario
    from pathlib import Path

    reset_database(get_settings().database_url)
    import_mk01(Path(MK01))
    import_nouri(Path(SFJW))
    import_gass(Path(GASS))
    sid = build_scenario("factory_demo_01", seed=42)
    return {"settings": get_settings(), "scenario_id": sid}


@pytest.fixture()
def demo_session(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        yield session, (
            session.query(Instance)
            .filter(Instance.id == built_db["scenario_id"])
            .one()
        )


@pytest.fixture()
def mk01_session(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        yield session, session.query(Instance).filter(Instance.name == "mk01").one()
