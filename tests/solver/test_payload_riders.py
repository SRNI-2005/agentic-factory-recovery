"""Controller riders (2026-08-24, binding): suspension memory + status truth.

Canonical materials coverage lives in test_payload_materials.py; this module
pins the robustness riders so regressions surface loudly.
"""
import pytest

from coe.solver.horizon import compute_horizon
from coe.solver.payload_builder import build_payload

pytestmark = pytest.mark.db


def _build(session, inst):
    return build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                         time_limit_seconds=60)


def _one(model, session, inst, name):
    return (session.query(model)
            .filter(model.instance_id == inst.id, model.name == name).one())


def test_suspended_job_excluded_and_remembered(demo_session):
    """jobs.status == BLOCKED at query time -> ops never enter the payload;
    one JOB_SUSPENDED entry per operation; root suspended_jobs lists names."""
    from coe.db.models.fjsp import Job, Operation

    session, inst = demo_session
    job = _one(Job, session, inst, "J5")
    job.status = "BLOCKED"
    session.flush()

    p = _build(session, inst)

    assert "J5" not in {j["job_id"] for j in p["jobs"]}
    assert p["suspended_jobs"] == ["J5"]
    db_ops = (session.query(Operation.sequence_number)
              .filter(Operation.instance_id == inst.id,
                      Operation.job_id == job.id)
              .order_by(Operation.sequence_number).all())
    sus = [b for b in p["blocked_operations"]
           if b["reason"] == "JOB_SUSPENDED"]
    assert {b["operation_id"] for b in sus} == \
        {f"J5-O{seq}" for (seq,) in db_ops}
    assert len(sus) == len(db_ops)
    assert all(b["material_sku"] is None for b in sus)
    assert "J5" not in p["job_tardiness_weights"]
    assert all(not op["operation_id"].startswith("J5-")
               for j in p["jobs"] for op in j["operations"])
    # suspended demand must not leak into material gatekeeping either
    assert p["warnings"] == []


def test_suspended_and_active_blocks_coexist(demo_session):
    """JOB_SUSPENDED entries never cascade PREDECESSOR_BLOCKED into live jobs,
    and stay deterministic alongside ordinary blocking."""
    import json

    from coe.db.models.fjsp import Job

    session, inst = demo_session
    j5 = _one(Job, session, inst, "J5")
    j7 = _one(Job, session, inst, "J7")
    j5.status = "BLOCKED"
    j7.status = "BLOCKED"
    session.flush()
    a = json.dumps(_build(session, inst), sort_keys=True)
    b = json.dumps(_build(session, inst), sort_keys=True)
    p = json.loads(a)
    assert p["suspended_jobs"] == ["J5", "J7"]
    reasons = {x["reason"] for x in p["blocked_operations"]}
    assert reasons <= {"JOB_SUSPENDED"}
    assert a == b


def test_failed_status_machine_stripped_without_cli_args(env):
    """Rider (d): DB status FAILED strips the machine even when absent from
    failed_machine_names (RECOVERY path, in-progress work truncated)."""
    from coe.db.models.fjsp import Machine

    session, inst = env["session"], env["instance"]
    m2 = _one(Machine, session, inst, "M2")
    m2.status = "FAILED"
    session.flush()
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60, schedule_type="RECOVERY",
                      now=env["now"])
    assert "M2" not in p["machines"]
    for j in p["jobs"]:
        for op in j["operations"]:
            for alt in op["alternatives"]:
                assert alt["machine_id"] != "M2"
    assert all(d["machine_id"] != "M2" for d in p["machine_downtime"])


def test_failed_status_machine_stripped_in_baseline(demo_session):
    from coe.db.models.fjsp import Machine

    session, inst = demo_session
    m3 = _one(Machine, session, inst, "M3")
    m3.status = "FAILED"
    session.flush()
    p = _build(session, inst)
    assert "M3" not in p["machines"]
    assert p["failed_machines"] == ["M3"]
    assert all(d["machine_id"] != "M3" for d in p["machine_downtime"])
    assert all(alt["machine_id"] != "M3"
               for j in p["jobs"] for op in j["operations"]
               for alt in op["alternatives"])


def test_unavailable_worker_blocked_full_horizon(demo_session):
    """Rider (d): UNAVAILABLE workers get [0,H) unavailability merged through
    the normal (dedup/clipping) emission path."""
    from coe.db.models.workers import Worker

    session, inst = demo_session
    w2 = _one(Worker, session, inst, "W2")
    w2.status = "UNAVAILABLE"
    session.flush()
    p = _build(session, inst)
    horizon = compute_horizon(jobs=p["jobs"],
                              machine_downtime=p["machine_downtime"],
                              setup_times=p["setup_times"])
    wins = [(u["from"], u["until"]) for u in p["worker_unavailability"]
            if u["worker_id"] == "W2"]
    assert wins == [(0, horizon)]
