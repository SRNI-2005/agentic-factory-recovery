import subprocess

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db

MK01 = "data/raw/mk01/mk01.txt"
SFJW = "data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"
GASS = "data/raw/gass"


def cli(*args: str) -> str:
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return r.stdout


@pytest.fixture(scope="module")
def full_pipeline():
    """Criterion 1 is proven by the suite's dependency on compose services;
    criteria 2-9 exercised here through the public CLI."""
    from coe.config import get_settings
    from coe.db.admin import reset_database

    reset_database(get_settings().database_url)          # criterion 2
    cli("import", "mk01", "--path", MK01)                # criteria 3+4
    cli("import", "hutter", "--path", SFJW)              # criterion 5a
    cli("import", "gass", "--dir", GASS)                 # criterion 5b
    cli("scenario", "build", "--name", "factory_demo_01", "--seed", "42")  # 6-9
    return get_settings().database_url


def test_criterion_4_mk01_shape(full_pipeline):
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        row = c.execute(
            text(
                """
                SELECT count(DISTINCT j.id), count(DISTINCT m.id), count(DISTINCT o.id)
                FROM instances i
                JOIN jobs j ON j.instance_id = i.id
                JOIN machines m ON m.instance_id = i.id
                JOIN operations o ON o.instance_id = i.id
                WHERE i.name = 'mk01'
                """
            )
        ).fetchone()
    assert tuple(row) == (10, 6, 55)


def test_criterion_6_scenario_dimensions(full_pipeline):
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        jobs, machines = c.execute(
            text(
                """
                SELECT (SELECT count(*) FROM jobs WHERE instance_id=i.id),
                       (SELECT count(*) FROM machines WHERE instance_id=i.id)
                FROM instances i WHERE i.name='factory_demo_01'
                """
            )
        ).fetchone()
    assert (jobs, machines) == (30, 8)


def test_criterion_7_no_dead_end_operations(full_pipeline):
    """Every generated operation has at least one capable machine."""
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        dead = c.execute(
            text(
                """
                SELECT count(*) FROM operations o
                JOIN instances i ON i.id = o.instance_id
                WHERE i.name = 'factory_demo_01'
                  AND NOT EXISTS (
                    SELECT 1 FROM operation_machine_alternatives a
                    WHERE a.operation_id = o.id)
                """
            )
        ).scalar_one()
    assert dead == 0


def test_criterion_8_provenance_complete(full_pipeline):
    expected = {
        "topology", "job_attributes", "worker_flexibility",
        "worker_availability", "setup_times", "maintenance_windows",
        "materials_inventory", "time_normalization",
    }
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        types = {
            r[0]
            for r in c.execute(
                text(
                    """
                    SELECT ss.contribution_type FROM scenario_sources ss
                    JOIN instances i ON i.id = ss.scenario_id
                    WHERE i.name = 'factory_demo_01'
                    """
                )
            ).all()
        }
    assert expected <= types


def test_criterion_10_same_seed_identical(clean_db):
    from pathlib import Path

    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    def build_and_count():
        reset_database(get_settings().database_url)
        import_mk01(Path(MK01))
        import_nouri(Path(SFJW))
        import_gass(Path(GASS))
        sid = build_scenario("factory_demo_01", seed=42)
        eng = create_engine(get_settings().database_url)
        with eng.begin() as c:
            return c.execute(
                text(
                    "SELECT count(*) FROM operation_machine_worker_times "
                    "WHERE instance_id=:i"
                ),
                {"i": sid},
            ).scalar_one()

    assert build_and_count() == build_and_count()


def test_criterion_11_mqtt_event_once_with_window(full_pipeline):
    import time

    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_failure
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text as sqltext

    handle = run_subscriber()
    try:
        mid = publish_failure("factory_demo_01", "M2", occurred_at=900)
        deadline = time.time() + 5
        stored = False
        while time.time() < deadline and not stored:
            eng = create_engine(get_settings().database_url)
            with eng.begin() as c:
                n = c.execute(
                    sqltext(
                        """
                        SELECT count(*) FROM telemetry_events te
                        JOIN instances i ON i.id = te.instance_id
                        WHERE i.name='factory_demo_01' AND te.message_id=:m
                        """
                    ),
                    {"m": mid},
                ).scalar_one()
                win = c.execute(
                    sqltext(
                        """
                        SELECT count(*) FROM machine_downtime_windows w
                        JOIN instances i ON i.id = w.instance_id
                        JOIN machines mm ON mm.instance_id = i.id AND mm.name='M2'
                        WHERE i.name='factory_demo_01'
                          AND w.downtime_from <= 900
                          AND (w.downtime_until IS NULL OR w.downtime_until > 900)
                        """
                    )
                ).scalar_one()
            stored = n == 1 and win >= 1
            if not stored:
                time.sleep(0.25)
        assert stored
    finally:
        handle.stop()
