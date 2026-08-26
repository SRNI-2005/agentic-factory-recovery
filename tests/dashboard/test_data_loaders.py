"""Tests for coe.dashboard.data read-only adapters."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from coe.db.models.downtime import MachineDowntimeWindow, WorkerAbsenceWindow
from coe.db.models.fjsp import Job, JobFamily, Machine, Operation
from coe.db.models.materials import Material, MaterialReceipt
from coe.db.models.provenance import Instance, ScenarioSource
from coe.db.models.recovery import RecoveryRun
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
from coe.db.models.workers import (
    Worker,
    WorkerAvailabilityWindow,
    WorkerRole,
)

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mk_instance(session, name="dash-fixture"):
    inst = Instance(name=name, source_name="test",
                    source_version="t", source_license="test")
    session.add(inst)
    session.flush()
    return inst


def _mk_version(session, inst_id, number, makespan=100, rolled_back=False):
    ver = ScheduleVersion(
        instance_id=inst_id, version_number=number,
        schedule_type="BASELINE", solver_status="FEASIBLE",
        objective_value=float(makespan), makespan=makespan,
        total_tardiness=0, alpha_weight=0.5, beta_weight=0.5,
        time_limit_seconds=30, solve_duration_seconds=0.42,
        failed_machine_ids=None, parent_version_id=None,
        rolled_back=rolled_back, payload_hash="0" * 64, payload_json={})
    session.add(ver)
    session.flush()
    return ver


# ---------------------------------------------------------------------------
# list_instances
# ---------------------------------------------------------------------------

def test_list_instances_returns_dicts(clean_db, session):
    from coe.dashboard.data import list_instances

    _mk_instance(session, "aaa")
    _mk_instance(session, "bbb")
    rows = list_instances(session)
    assert isinstance(rows, list)
    assert all(isinstance(r, dict) for r in rows)
    assert [r["name"] for r in rows] == ["aaa", "bbb"]
    assert set(rows[0]) == {"name", "source_name", "parent"}


def test_list_instances_fork_parent(clean_db, session):
    from coe.dashboard.data import list_instances

    parent = _mk_instance(session, "parent-inst")
    child = _mk_instance(session, "child@dead")
    session.add(ScenarioSource(
        scenario_id=child.id, source_instance_id=parent.id,
        contribution_type="fork", transformation_description="fork"))
    session.flush()
    rows = {r["name"]: r for r in list_instances(session)}
    assert rows["child@dead"]["parent"] == "parent-inst"
    assert rows["parent-inst"]["parent"] is None


# ---------------------------------------------------------------------------
# active_schedule
# ---------------------------------------------------------------------------

def test_active_schedule_none_when_empty(clean_db, session):
    from coe.dashboard.data import active_schedule

    inst = _mk_instance(session)
    assert active_schedule(session, inst.id) is None


def test_active_schedule_returns_dict(clean_db, session):
    from coe.dashboard.data import active_schedule

    inst = _mk_instance(session)
    mach = Machine(instance_id=inst.id, name="M1")
    job = Job(instance_id=inst.id, name="J1")
    session.add_all([mach, job])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=job.id, sequence_number=1)
    session.add(op)
    session.flush()
    ver = _mk_version(session, inst.id, 1, makespan=100)
    session.add(ScheduleEntry(
        instance_id=inst.id, version_id=ver.id, operation_id=op.id,
        machine_id=mach.id, worker_id=None, start_time=10, end_time=30,
        processing_time=20, setup_time=0, status="SCHEDULED",
        is_frozen=False))
    session.flush()

    result = active_schedule(session, inst.id)
    assert isinstance(result, dict)
    assert result["version"]["version_number"] == 1
    assert result["version"]["makespan"] == 100
    assert len(result["entries"]) == 1
    assert result["entries"][0]["start_time"] == 10
    assert set(result["version"]) == {"id", "version_number", "schedule_type",
                                      "solver_status", "makespan",
                                      "total_tardiness", "rolled_back"}


# ---------------------------------------------------------------------------
# schedule_versions
# ---------------------------------------------------------------------------

def test_schedule_versions_returns_dicts(clean_db, session):
    from coe.dashboard.data import schedule_versions

    inst = _mk_instance(session)
    _mk_version(session, inst.id, 1, makespan=120)
    _mk_version(session, inst.id, 2, makespan=90)

    rows = schedule_versions(session, inst.id)
    assert isinstance(rows, list)
    assert all(isinstance(r, dict) for r in rows)
    assert [r["version_number"] for r in rows] == [2, 1]
    assert rows[0]["makespan"] == 90
    assert set(rows[0]) == {"id", "version_number", "schedule_type",
                            "solver_status", "makespan", "total_tardiness",
                            "rolled_back"}


# ---------------------------------------------------------------------------
# materials_overview
# ---------------------------------------------------------------------------

def test_materials_overview_returns_dicts(clean_db, session):
    from coe.dashboard.data import materials_overview

    inst = _mk_instance(session)
    m = Material(instance_id=inst.id, sku="MAT-1", initial_stock=50,
                 reorder_point=5)
    session.add(m)
    session.flush()
    session.add(MaterialReceipt(instance_id=inst.id, material_id=m.id,
                                quantity=10, available_at=100,
                                source="initial"))
    session.flush()

    rows = materials_overview(session, inst.id)
    assert isinstance(rows, list)
    assert rows[0]["sku"] == "MAT-1"
    assert rows[0]["initial_stock"] == 50
    assert rows[0]["receipts"] == [{"quantity": 10, "available_at": 100,
                                    "source": "initial"}]


# ---------------------------------------------------------------------------
# workers_overview
# ---------------------------------------------------------------------------

def test_workers_overview_returns_dicts(clean_db, session):
    from coe.dashboard.data import workers_overview

    inst = _mk_instance(session)
    role = WorkerRole(instance_id=inst.id, role_name="operator")
    session.add(role)
    session.flush()
    w = Worker(instance_id=inst.id, name="W1", role_id=role.id)
    session.add(w)
    session.flush()
    session.add(WorkerAvailabilityWindow(
        instance_id=inst.id, worker_id=w.id,
        available_from=0, available_until=480, source_pattern="shift"))
    session.add(WorkerAbsenceWindow(
        instance_id=inst.id, worker_id=w.id,
        absence_from=100, absence_until=None, reason="absent"))
    session.flush()

    rows = workers_overview(session, inst.id)
    assert isinstance(rows, list)
    assert rows[0]["name"] == "W1"
    assert rows[0]["role"] == "operator"
    assert rows[0]["availability"] == [(0, 480)]
    assert rows[0]["absent_since"] == 100


# ---------------------------------------------------------------------------
# machines_overview
# ---------------------------------------------------------------------------

def test_machines_overview_returns_dicts(clean_db, session):
    from coe.dashboard.data import machines_overview

    inst = _mk_instance(session)
    m = Machine(instance_id=inst.id, name="M1", status="FAILED")
    session.add(m)
    session.flush()
    session.add(MachineDowntimeWindow(
        instance_id=inst.id, machine_id=m.id,
        downtime_from=10, downtime_until=None, reason="failure"))
    session.flush()

    rows = machines_overview(session, inst.id)
    assert isinstance(rows, list)
    assert rows[0]["name"] == "M1"
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["down_since"] == 10


# ---------------------------------------------------------------------------
# jobs_overview
# ---------------------------------------------------------------------------

def test_jobs_overview_returns_dicts(clean_db, session):
    from coe.dashboard.data import jobs_overview

    inst = _mk_instance(session)
    fam = JobFamily(instance_id=inst.id, name="fam-a")
    session.add(fam)
    session.flush()
    j = Job(instance_id=inst.id, name="J1", job_family_id=fam.id,
            release_time=5, deadline=100, priority=2, status="PENDING")
    session.add(j)
    session.flush()
    session.add(Operation(instance_id=inst.id, job_id=j.id, sequence_number=1))
    session.flush()

    rows = jobs_overview(session, inst.id)
    assert isinstance(rows, list)
    assert rows[0]["name"] == "J1"
    assert rows[0]["family"] == "fam-a"
    assert rows[0]["ops"] == 1
    assert rows[0]["deadline"] == 100


# ---------------------------------------------------------------------------
# jobs_per_day
# ---------------------------------------------------------------------------

def test_jobs_per_day_groups_by_deadline(clean_db, session):
    from coe.dashboard.data import jobs_per_day

    inst = _mk_instance(session)
    session.add_all([
        Job(instance_id=inst.id, name="J1", release_time=0, deadline=1400,
            priority=1, status="PENDING"),
        Job(instance_id=inst.id, name="J2", release_time=0, deadline=1500,
            priority=1, status="PENDING"),
    ])
    grouped = jobs_per_day(session, inst.id)
    assert isinstance(grouped, dict)
    assert grouped == {0: ["J1"], 1: ["J2"]}


# ---------------------------------------------------------------------------
# recovery_runs
# ---------------------------------------------------------------------------

def test_recovery_runs_returns_dicts(clean_db, session):
    from coe.dashboard.data import recovery_runs

    inst = _mk_instance(session)
    session.add(RecoveryRun(
        instance_id=inst.id, trigger="CLI", status="COMMITTED",
        disruption_record_json={"k": 1}, started_at=datetime(2026, 1, 2),
        node_timings_json={"translate": 0.4}, quantum_shadow_json=None))
    session.flush()

    rows = recovery_runs(session, inst.id)
    assert isinstance(rows, list)
    assert rows[0]["trigger"] == "CLI"
    assert rows[0]["status"] == "COMMITTED"
    assert rows[0]["node_timings_json"] == {"translate": 0.4}


# ---------------------------------------------------------------------------
# fidelity_report
# ---------------------------------------------------------------------------

def test_fidelity_report_none_when_missing(tmp_path):
    from coe.dashboard.data import fidelity_report

    assert fidelity_report(tmp_path / "missing.json") is None


def test_fidelity_report_reads_json(tmp_path):
    from coe.dashboard.data import fidelity_report

    report = {"seed": 42, "cases": []}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert fidelity_report(path) == report
