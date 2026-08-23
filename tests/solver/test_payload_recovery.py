"""Recovery-path payload builder tests (spec §3.1, both amendments).

Timeline lives in conftest.seeded_recovery_env (now = 1000).
"""
import json

import pytest
from sqlalchemy import select

from coe.db.models.fjsp import Machine, OperationMachineAlternative
from coe.db.models.workers import Worker
from coe.solver.payload_builder import build_payload, resolve_reference_clock

pytestmark = pytest.mark.db

REMAINING = 10          # J2-O1 spans [990, 1010); cut at now = 1000


def _build(env, **kw):
    return build_payload(env["session"], instance_row=env["instance"],
                         alpha=1.0, beta=1.0, time_limit_seconds=60,
                         schedule_type="RECOVERY", now=env["now"], **kw)


def _find(p, oid_):
    for j in p["jobs"]:
        for op in j["operations"]:
            if op["operation_id"] == oid_:
                return op
    raise AssertionError(f"{oid_} not in payload")


def _db_op(session, inst, job_name, seq):
    from coe.db.models.fjsp import Job, Operation

    jid = session.query(Job.id).filter(
        Job.instance_id == inst.id, Job.name == job_name).scalar_one()
    return session.query(Operation).filter(
        Operation.instance_id == inst.id, Operation.job_id == jid,
        Operation.sequence_number == seq).one()


def test_completed_and_future_classification(env):
    p = _build(env)
    done = _find(p, "J1-O1")
    assert done["status"] == "COMPLETED"
    assert done["frozen"] == {"machine_id": "M0", "worker_id": "W1",
                              "start": 400, "end": 500}
    assert done["alternatives"] == []
    fut = _find(p, "J2-O2")
    assert fut["status"] == "PENDING" and fut["frozen"] is None
    assert fut["alternatives"]


def test_inprogress_healthy_frozen(env):
    ip = _find(_build(env), "J1-O2")
    assert ip["status"] == "IN_PROGRESS"
    assert ip["frozen"] == {"machine_id": "M1", "worker_id": "W2",
                            "start": 900, "end": 1100}
    assert ip["alternatives"] == []


def test_interrupted_truncated_durations(env):
    from coe.db.models.workers import OperationMachineWorkerTime

    session, inst = env["session"], env["instance"]
    p = _build(env, failed_machine_names=("M2",))
    it = _find(p, "J2-O1")
    assert it["status"] == "PENDING" and it["frozen"] is None

    oprow = _db_op(session, inst, "J2", 1)
    got = {a["machine_id"]: a for a in it["alternatives"]}
    bases = session.execute(
        select(OperationMachineAlternative.machine_id,
               OperationMachineAlternative.processing_time)
        .where(OperationMachineAlternative.instance_id == inst.id,
               OperationMachineAlternative.operation_id == oprow.id)
    ).all()
    assert got, "interrupted op lost all alternatives"
    worker_names = dict(session.query(Worker.id, Worker.name)
                        .filter(Worker.instance_id == inst.id).all())
    for mid, base in bases:
        mname = session.query(Machine.name).filter(Machine.id == mid).scalar_one()
        expected = REMAINING if base > REMAINING else base
        assert got[mname]["processing_time"] == expected, (mname, base)
        if base > REMAINING:
            times = session.execute(
                select(OperationMachineWorkerTime.worker_id,
                       OperationMachineWorkerTime.processing_time)
                .where(OperationMachineWorkerTime.instance_id == inst.id,
                       OperationMachineWorkerTime.operation_id == oprow.id,
                       OperationMachineWorkerTime.machine_id == mid)
            ).all()
            for wid, wdur in times:
                assert got[mname]["workers"][worker_names[wid]] == \
                    max(1, round(wdur * REMAINING / base))


def test_permanent_failure_strips_machine(env, demo_session):
    from coe.db.models.downtime import MachineDowntimeWindow

    session, inst = demo_session
    mid = session.query(Machine.id).filter(
        Machine.instance_id == inst.id, Machine.name == "M2").scalar_one()
    session.add(MachineDowntimeWindow(
        instance_id=inst.id, machine_id=mid, downtime_from=900,
        downtime_until=None, reason="FAILURE", source_event_ids=[]))
    p = _build({**env, "session": session}, failed_machine_names=("M2",))
    assert "M2" not in p["machines"]
    for j in p["jobs"]:
        for op in j["operations"]:
            for alt in op["alternatives"]:
                assert alt["machine_id"] != "M2"
    assert all(d["machine_id"] != "M2" for d in p["machine_downtime"])
    assert _find(p, "J2-O1")["status"] == "PENDING"


def test_dead_end_blocks_cascade(env, demo_session):
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Job, Operation

    session, inst = demo_session
    rows = session.execute(
        select(OperationMachineAlternative.operation_id,
               OperationMachineAlternative.machine_id)
        .where(OperationMachineAlternative.instance_id == inst.id)
        .order_by(OperationMachineAlternative.operation_id,
                  OperationMachineAlternative.machine_id)
    ).all()
    per: dict[int, list[int]] = {}
    for oid_, mid in rows:
        per.setdefault(oid_, []).append(mid)
    solo = []
    for oid_, mids in per.items():
        if len(mids) != 1:
            continue
        oprow = session.query(Operation).filter(Operation.id == oid_).one()
        job_max = session.query(Operation.sequence_number).filter(
            Operation.job_id == oprow.job_id).order_by(
            Operation.sequence_number.desc()).first()
        if oprow.sequence_number < job_max[0]:     # successor must exist
            solo.append((oprow, mids[0]))
    assert solo, "expected a single-alternative op with a successor"
    oprow, mid = solo[0]
    mname = session.query(Machine.name).filter(Machine.id == mid).scalar_one()
    job_name = session.query(Job.name).filter(Job.id == oprow.job_id).scalar_one()
    session.add(MachineDowntimeWindow(
        instance_id=inst.id, machine_id=mid, downtime_from=0,
        downtime_until=None, reason="FAILURE", source_event_ids=[]))
    p = _build({**env, "session": session}, failed_machine_names=(mname,))
    blocked = {b["operation_id"]: b["reason"] for b in p["blocked_operations"]}
    assert blocked[f"{job_name}-O{oprow.sequence_number}"] == "NO_CAPABLE_MACHINES"
    assert blocked.get(f"{job_name}-O{oprow.sequence_number + 1}") == \
        "PREDECESSOR_BLOCKED"


def test_downtime_fully_covered_dropped(env):
    p = _build(env)
    assert [d for d in p["machine_downtime"] if d["machine_id"] == "M1"] == []
    dropped = [w for w in p["warnings"] if w["type"] == "DOWNTIME_DROPPED"]
    assert len(dropped) == 1
    assert dropped[0]["window"] == [1000, 1100]


def test_downtime_partially_clipped(env):
    p = _build(env)
    m0 = [d for d in p["machine_downtime"] if d["machine_id"] == "M0"]
    assert len(m0) == 1
    assert (m0[0]["from"], m0[0]["until"]) == (500, 520)
    clipped = [x for x in p["warnings"] if x["type"] == "DOWNTIME_CLIPPED"]
    assert len(clipped) == 1
    assert clipped[0]["window"] == [480, 520]
    assert clipped[0]["clipped_to"] == [500, 520]


def test_worker_window_clipped_against_frozen(env):
    p = _build(env)
    w1 = [u for u in p["worker_unavailability"] if u["worker_id"] == "W1"]
    for u in w1:
        overlaps_open = u["from"] < 500 and u["until"] > 400
        assert not overlaps_open, f"W1 unavailability overlaps frozen op: {u}"
    clipped = [w for w in p["warnings"] if w["type"] == "WORKER_WINDOW_CLIPPED"]
    assert len(clipped) == 1
    assert clipped[0]["window"] == [300, 600]
    assert clipped[0]["clipped_to"] == [500, 600]


def test_parent_link_and_type(env):
    p = _build(env)
    assert p["schedule_type"] == "RECOVERY"
    assert p["parent_version_id"] == env["version_id"]


def test_initial_families_seeded(env):
    p = _build(env)
    assert set(p["machine_initial_families"]) <= set(p["machines"])
    assert "M0" in p["machine_initial_families"]


def test_recovery_requires_clock_and_known_machines(env):
    with pytest.raises(ValueError):
        build_payload(env["session"], instance_row=env["instance"],
                      alpha=1.0, beta=1.0, time_limit_seconds=60,
                      schedule_type="RECOVERY", now=None)
    with pytest.raises(ValueError):
        _build(env, failed_machine_names=("M99",))


def test_recovery_deterministic_bytes(env):
    a = json.dumps(_build(env, failed_machine_names=("M3",)), sort_keys=True)
    b = json.dumps(_build(env, failed_machine_names=("M3",)), sort_keys=True)
    assert a == b


def test_clock_explicit_wins(mk01_session):
    session, inst = mk01_session
    assert resolve_reference_clock(session, inst.id, at=55) == 55


def test_clock_requires_telemetry_or_at(mk01_session):
    session, inst = mk01_session
    with pytest.raises(ValueError):
        resolve_reference_clock(session, inst.id, at=None)


def test_clock_defaults_to_latest_telemetry(env):
    from coe.db.models.downtime import TelemetryEvent

    session, inst = env["session"], env["instance"]
    mid = session.query(Machine.id).filter(
        Machine.instance_id == inst.id, Machine.name == "M0").scalar_one()
    session.add(TelemetryEvent(
        occurred_at=1234, instance_id=inst.id, message_id="clk-1",
        machine_id=mid, worker_id=None, material_id=None,
        resource_kind="MACHINE", event_type="MAINTENANCE", received_at=1234,
        severity="LOW", estimated_downtime=10, processed_at=1234,
        processing_error=None, payload_json={}))
    assert resolve_reference_clock(session, inst.id, at=None) == 1234
