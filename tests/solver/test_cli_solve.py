"""Tier 3: end-to-end CLI flow over factory_demo_01 (spec §11 Tier 3).

Sequential by design: each command advances shared database state.
Do not parallelize this module.
"""
import subprocess

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def fresh_demo_instance():
    """CLI subprocesses talk to the live DB but share no fixtures; this file
    sorts first under tests/solver/ and asserts version numbers 1..3, so it
    must guarantee a freshly built factory_demo_01 with an empty version
    chain. Later suites re-request the session-scoped ``built_db`` fixture,
    which resets again — no state leaks either direction."""
    from pathlib import Path

    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    reset_database(get_settings().database_url)
    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))
    build_scenario("factory_demo_01", seed=42)


def cli(*args):
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", *args],
        capture_output=True, text=True,
    )
    return r


def _sql(q, **params):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        return c.execute(text(q), params)


def test_baseline_commits_version_one():
    r = cli("solve", "baseline", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "version=1" in r.stdout
    row = _sql(
        "SELECT solver_status, schedule_type FROM schedule_versions "
        "WHERE version_number = 1").one()
    assert row[0] in ("OPTIMAL", "FEASIBLE") and row[1] == "BASELINE"
    n = _sql(
        "SELECT count(*) FROM operations o "
        "JOIN instances i ON i.id = o.instance_id "
        "WHERE i.name='factory_demo_01' AND o.status = 'SCHEDULED'").scalar_one()
    assert n > 0


def test_show_prints_active_schedule():
    r = cli("schedule", "show", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "version=1" in r.stdout and "M0" in r.stdout
    names = [ln.split()[0] for ln in r.stdout.splitlines()
             if ln.startswith("  ")]
    assert names == sorted(names)   # entries grouped by machine NAME


def test_recovery_injects_through_ingest_and_commits():
    r = cli("solve", "recovery", "--instance", "factory_demo_01",
            "--failed-machine", "M3", "--at", "1000")
    assert r.returncode == 0, r.stderr
    assert "version=2" in r.stdout
    failed = _sql(
        "SELECT failed_machine_ids FROM schedule_versions "
        "WHERE version_number = 2").scalar_one()
    assert failed == ["M3"]
    parent = _sql(
        "SELECT parent_version_id FROM schedule_versions "
        "WHERE version_number = 2").scalar_one()
    assert parent == 1
    telemetry = _sql(
        "SELECT count(*) FROM telemetry_events te "
        "JOIN instances i ON i.id = te.instance_id "
        "WHERE i.name='factory_demo_01' AND te.event_type='FAILURE' "
        "AND te.message_id LIKE 'cli-%'").scalar_one()
    assert telemetry == 1
    status = _sql(
        "SELECT m.status FROM machines m "
        "JOIN instances i ON i.id = m.instance_id "
        "WHERE i.name='factory_demo_01' AND m.name='M3'").scalar_one()
    assert status == "FAILED"
    live_on_failed = _sql(
        "SELECT count(*) FROM schedule_entries se "
        "JOIN schedule_versions sv ON sv.id = se.version_id "
        "WHERE sv.version_number = 2 AND se.machine_id IN ("
        "  SELECT m.id FROM machines m JOIN instances i ON i.id=m.instance_id "
        "  WHERE i.name='factory_demo_01' AND m.name='M3')"
        "AND se.status = 'SCHEDULED'").scalar_one()
    assert live_on_failed == 0
    completed = _sql(
        "SELECT count(*) FROM operations o "
        "JOIN instances i ON i.id = o.instance_id "
        "WHERE i.name='factory_demo_01' AND o.status='COMPLETED'").scalar_one()
    assert completed > 0


def test_repeat_recovery_is_idempotent_on_telemetry():
    r = cli("solve", "recovery", "--instance", "factory_demo_01",
            "--failed-machine", "M3", "--at", "1000")
    assert r.returncode == 0, r.stderr
    assert "version=3" in r.stdout
    telemetry = _sql(
        "SELECT count(*) FROM telemetry_events te "
        "JOIN instances i ON i.id = te.instance_id "
        "WHERE i.name='factory_demo_01' AND te.message_id LIKE "
            "'cli-%' AND te.event_type='FAILURE'").scalar_one()
    assert telemetry == 1


def test_restore_closes_window_and_activates():
    r = cli("machine", "restore", "--instance", "factory_demo_01",
            "--machine", "M3")
    assert r.returncode == 0, r.stderr
    until, status = _sql(
        "SELECT w.downtime_until, m.status FROM machine_downtime_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "JOIN machines m ON m.id = w.machine_id AND m.instance_id = i.id "
        "WHERE i.name='factory_demo_01' AND m.name='M3' "
        "ORDER BY w.downtime_from LIMIT 1").one()
    assert until is not None and status == "ACTIVE"


def test_rollback_chain_then_floor():
    r = cli("schedule", "rollback", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "rolled back 3" in r.stdout and "active 2" in r.stdout
    r = cli("schedule", "rollback", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "rolled back 2" in r.stdout and "active 1" in r.stdout
    r = cli("schedule", "rollback", "--instance", "factory_demo_01")
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "refused" in combined or "floor" in combined.lower()
    active = _sql(
        "SELECT version_number FROM schedule_versions sv "
        "JOIN instances i ON i.id = sv.instance_id "
        "WHERE i.name = 'factory_demo_01' "
        "AND sv.rolled_back = false AND sv.solver_status IN "
        "('OPTIMAL','FEASIBLE')").scalar_one()
    assert active == 1
