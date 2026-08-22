import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def test_deadline_bounds_and_priorities(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        jobs = c.execute(
            text(
                "SELECT id, release_time, deadline, priority FROM jobs "
                "WHERE instance_id=:i"
            ),
            {"i": demo_scenario},
        ).all()
        twk_rows = c.execute(
            text(
                "SELECT o.job_id, AVG(a.processing_time) AS twk FROM operations o "
                "JOIN operation_machine_alternatives a ON a.operation_id = o.id "
                "WHERE a.instance_id=:i GROUP BY o.job_id"
            ),
            {"i": demo_scenario},
        ).all()
    twk = {jid: float(avg or 0) for jid, avg in twk_rows}
    assert len(jobs) == 30
    for jid, rel, dl, prio in jobs:
        assert dl > rel
        assert dl - rel >= 1.45 * twk[jid]   # floor of uniform(1.5,3.0) after rounding
        assert prio in (1, 2, 3, 4, 5)


def test_reproducible_with_same_seed(clean_db):
    from pathlib import Path

    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    def snapshot():
        reset_database(get_settings().database_url)
        import_mk01(Path("data/raw/mk01/mk01.txt"))
        import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
        import_gass(Path("data/raw/gass"))
        sid = build_scenario("factory_demo_01", seed=42)
        eng = create_engine(get_settings().database_url)
        with eng.begin() as c:
            return c.execute(
                text(
                    "SELECT name, release_time, deadline, priority FROM jobs "
                    "WHERE instance_id=:i ORDER BY name"
                ),
                {"i": sid},
            ).all()

    assert snapshot() == snapshot()


def test_provenance_recorded(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        n = c.execute(
            text(
                "SELECT count(*) FROM scenario_sources WHERE scenario_id=:i "
                "AND contribution_type='job_attributes'"
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert n == 1
