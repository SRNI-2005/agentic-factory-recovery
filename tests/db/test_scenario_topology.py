from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


@pytest.fixture()
def sources_imported(clean_db):
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri

    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))


def test_build_creates_30x8(sources_imported):
    from coe.config import get_settings
    from coe.scenario.build import build_scenario

    sid = build_scenario("factory_demo_01", seed=42)
    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        jobs = c.execute(
            text("SELECT count(*) FROM jobs WHERE instance_id=:i"), {"i": sid}
        ).scalar_one()
        machines = c.execute(
            text("SELECT count(*) FROM machines WHERE instance_id=:i"), {"i": sid}
        ).scalar_one()
        src = c.execute(
            text("SELECT count(*) FROM scenario_sources WHERE scenario_id=:i"),
            {"i": sid},
        ).scalar_one()
    assert (jobs, machines) == (30, 8)
    assert src == 1


def _snapshot_topology():
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        iid = c.execute(
            text("SELECT id FROM instances WHERE name='factory_demo_01'")
        ).scalar_one()
        jobs = c.execute(
            text(
                "SELECT j.name, count(o.id) FROM jobs j "
                "LEFT JOIN operations o ON o.job_id = j.id "
                "WHERE j.instance_id = :i GROUP BY j.name ORDER BY j.name"
            ),
            {"i": iid},
        ).all()
        alts = c.execute(
            text(
                "SELECT operation_id, machine_id, processing_time "
                "FROM operation_machine_alternatives WHERE instance_id = :i "
                "ORDER BY operation_id, machine_id"
            ),
            {"i": iid},
        ).all()
    eng.dispose()
    return jobs, alts


def _import_sources():
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri

    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))


def test_topology_deterministic_same_seed(sources_imported):
    from coe.db.admin import reset_database
    from coe.scenario.build import build_scenario

    build_scenario("factory_demo_01", seed=42)
    first = _snapshot_topology()

    reset_database()
    _import_sources()
    build_scenario("factory_demo_01", seed=42)
    second = _snapshot_topology()

    assert first == second


def test_duplicate_name_refused(sources_imported):
    from coe.scenario.build import ScenarioError, build_scenario

    build_scenario("factory_demo_01", seed=42)
    with pytest.raises(ScenarioError, match="already exists"):
        build_scenario("factory_demo_01", seed=42)


def test_missing_source_aborts_clean(sources_imported):
    from sqlalchemy import text as sqltext

    from coe.db.session import make_engine
    from coe.scenario.build import ScenarioError, build_scenario

    with make_engine().begin() as c:
        gid = c.execute(
            sqltext("SELECT id FROM instances WHERE name='gass'")
        ).scalar_one()
        # gass import records verified profiles FK'd to the source instance
        c.execute(
            sqltext("DELETE FROM instance_profiles WHERE source_instance_id=:i"),
            {"i": gid},
        )
        c.execute(sqltext("DELETE FROM instances WHERE id=:i"), {"i": gid})
    with pytest.raises(ScenarioError):
        build_scenario("factory_demo_02", seed=42)
    eng = make_engine()
    with eng.begin() as c:
        leftovers = c.execute(
            sqltext("SELECT count(*) FROM instances WHERE name LIKE 'factory_demo_%'")
        ).scalar_one()
    assert leftovers == 0  # atomic rollback left nothing behind
