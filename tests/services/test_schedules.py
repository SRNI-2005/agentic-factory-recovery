from datetime import datetime

import pytest

from coe.db.models.fjsp import Job, Machine, Operation
from coe.db.models.provenance import Instance
from coe.db.models.recovery import RecoveryRun
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
from coe.services.schedules import active, recovery_runs, versions

pytestmark = pytest.mark.db


def _mk_version(session, inst_id, number, makespan=100):
    """ScheduleVersion with every NOT NULL column satisfied."""
    ver = ScheduleVersion(
        instance_id=inst_id, version_number=number,
        schedule_type="BASELINE", solver_status="FEASIBLE",
        objective_value=float(makespan), makespan=makespan,
        total_tardiness=0, alpha_weight=0.5, beta_weight=0.5,
        time_limit_seconds=30, solve_duration_seconds=0.42,
        failed_machine_ids=None, parent_version_id=None,
        rolled_back=False, payload_hash="0" * 64, payload_json={})
    session.add(ver)
    session.flush()
    return ver


def _mk_instance(session, name="dash-fixture"):
    inst = Instance(name=name, source_name="test",
                    source_version="t", source_license="test")
    session.add(inst)
    session.flush()
    return inst


def test_active_schedule_none_when_empty(clean_db, session):
    inst = _mk_instance(session)
    assert active(session, inst.id) is None


def test_active_schedule_reads_view(clean_db, session):
    inst = _mk_instance(session)
    mach = Machine(instance_id=inst.id, name="M1")
    job = Job(instance_id=inst.id, name="J1")
    session.add_all([mach, job])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=job.id, sequence_number=1)
    session.add(op)
    session.flush()
    ver = _mk_version(session, inst.id, 1, makespan=100)
    session.flush()
    session.add(ScheduleEntry(
        instance_id=inst.id, version_id=ver.id, operation_id=op.id,
        machine_id=mach.id, worker_id=None, start_time=10, end_time=30,
        processing_time=20, setup_time=0, status="SCHEDULED",
        is_frozen=False))
    ver2 = _mk_version(session, inst.id, 2, makespan=90)
    session.flush()
    session.add(ScheduleEntry(
        instance_id=inst.id, version_id=ver2.id, operation_id=op.id,
        machine_id=mach.id, worker_id=None, start_time=5, end_time=25,
        processing_time=20, setup_time=0, status="SCHEDULED",
        is_frozen=False))
    session.flush()

    snap = active(session, inst.id)
    assert snap.version.version_number == 2
    assert snap.version.makespan == 90
    assert snap.version.solver_status == "FEASIBLE"
    assert len(snap.entries) == 1
    assert snap.entries[0]["start_time"] == 5


def test_active_schedule_skips_rolled_back(clean_db, session):
    inst = _mk_instance(session)
    mach = Machine(instance_id=inst.id, name="M1")
    job = Job(instance_id=inst.id, name="J1")
    session.add_all([mach, job])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=job.id, sequence_number=1)
    session.add(op)
    session.flush()
    ver1 = _mk_version(session, inst.id, 1)
    v1 = ScheduleEntry(instance_id=inst.id, version_id=ver1.id,
                       operation_id=op.id, machine_id=mach.id, worker_id=None,
                       start_time=0, end_time=10, processing_time=10,
                       setup_time=0, status="SCHEDULED", is_frozen=False)
    ver2 = _mk_version(session, inst.id, 2)
    ver2.rolled_back = True
    v2 = ScheduleEntry(instance_id=inst.id, version_id=ver2.id,
                       operation_id=op.id, machine_id=mach.id, worker_id=None,
                       start_time=0, end_time=10, processing_time=10,
                       setup_time=0, status="SCHEDULED", is_frozen=False)
    session.add_all([v1, v2])
    session.flush()

    snap = active(session, inst.id)
    assert snap.version.version_number == 1
    assert len(snap.entries) == 1


def test_versions_ordered_desc_and_content(clean_db, session):
    inst = _mk_instance(session)
    _mk_version(session, inst.id, 1, makespan=120)
    _mk_version(session, inst.id, 2, makespan=90)

    rows = versions(session, inst.id)
    assert [r["version_number"] for r in rows] == [2, 1]
    assert rows[0]["makespan"] == 90
    assert rows[0]["solver_status"] == "FEASIBLE"
    assert rows[0]["schedule_type"] == "BASELINE"
    assert rows[0]["total_tardiness"] == 0
    assert rows[0]["rolled_back"] is False
    assert set(rows[0]) == {"id", "version_number", "schedule_type",
                            "solver_status", "makespan", "total_tardiness",
                            "rolled_back"}


def test_recovery_runs_ordered_by_started_desc(clean_db, session):
    inst = _mk_instance(session)
    session.add(RecoveryRun(
        instance_id=inst.id, trigger="CLI", status="COMMITTED",
        disruption_record_json={"k": 1}, started_at=datetime(2026, 1, 2),
        node_timings_json={"translate": 0.4}, quantum_shadow_json=None))
    session.add(RecoveryRun(
        instance_id=inst.id, trigger="MQTT", status="GATE_FAILED",
        disruption_record_json={}, started_at=datetime(2026, 1, 1)))
    session.flush()

    runs = recovery_runs(session, inst.id)
    assert [r["status"] for r in runs] == ["COMMITTED", "GATE_FAILED"]
    assert runs[0]["trigger"] == "CLI"
    assert runs[0]["node_timings_json"] == {"translate": 0.4}
    assert runs[0]["quantum_shadow_json"] is None
    assert runs[0]["finished_at"] is None
