import json

import pytest

from coe.solver.payload_builder import build_payload, derive_tardiness_weights


# ---------- pure weight derivation ----------

def _job(jid, priority, deadline):
    return {"job_id": jid, "priority": priority, "deadline": deadline}


def test_weights_mean_preserving():
    jobs = [_job("A", 1, 100), _job("B", 3, 100), _job("C", 3, 100)]
    w = derive_tardiness_weights(jobs, beta=2.0)
    assert w["A"] == pytest.approx(2.0 * 3 * 3 / 5)
    assert w["B"] == w["C"] == pytest.approx(2.0 * 3 * 1 / 5)
    assert sum(w.values()) / len(w) == pytest.approx(2.0)


def test_uniform_priorities_degrade_to_beta():
    jobs = [_job("A", 2, 100), _job("B", 2, 100)]
    w = derive_tardiness_weights(jobs, beta=1.5)
    assert w == {"A": 1.5, "B": 1.5}


def test_no_deadlines_returns_none():
    jobs = [_job("A", 1, None)]
    assert derive_tardiness_weights(jobs, beta=1.0) is None


# ---------- baseline payload on factory_demo_01 (DB) ----------

pytestmark = pytest.mark.db


def test_factory_baseline_shape(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    assert p["instance_id"] == "factory_demo_01"
    assert p["schedule_type"] == "BASELINE"
    assert p["parent_version_id"] is None
    assert len(p["machines"]) == 8
    assert p["machines"][0] == "M0"
    assert len(p["jobs"]) == 30
    assert p["machine_initial_families"] == {}
    assert p["warnings"] == []
    assert p["blocked_operations"] == []      # stock = 1.2x demand at build
    assert p["config"] == {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                           "normalize_objectives": True}


def test_every_alternative_carries_worker_map(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    for job in p["jobs"]:
        for op in job["operations"]:
            assert op["status"] == "PENDING"
            assert op["frozen"] is None
            assert op["alternatives"], f"{op['operation_id']} dead-ended"
            for alt in op["alternatives"]:
                assert alt["workers"], "Phase 1 invariant: >=1 eligible worker"
                assert all(d >= 1 for d in alt["workers"].values())


def test_operation_ids_follow_convention(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    j1 = next(j for j in p["jobs"] if j["job_id"] == "J1")
    assert j1["operations"][0]["operation_id"] == "J1-O1"


def test_worker_tail_covered_to_horizon(demo_session):
    """Amendment invariant: for EVERY worker, unavailability ∪ availability
    covers [0, H] exactly (disjoint, no gaps) — so nobody is implicitly
    available after their shift ends."""
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    from coe.db.models.workers import Worker, WorkerAvailabilityWindow
    from coe.solver.horizon import compute_horizon
    from coe.solver.windows import complement, merge_intervals

    horizon = compute_horizon(jobs=p["jobs"],
                              machine_downtime=p["machine_downtime"],
                              setup_times=p["setup_times"])

    unavail: dict[str, list[tuple[int, int]]] = {}
    for e in p["worker_unavailability"]:
        unavail.setdefault(e["worker_id"], []).append((e["from"], e["until"]))

    avail_rows = (
        session.query(WorkerAvailabilityWindow)
        .filter(WorkerAvailabilityWindow.instance_id == inst.id)
        .order_by(WorkerAvailabilityWindow.worker_id).all()
    )
    names = dict(session.query(Worker.id, Worker.name)
                 .filter(Worker.instance_id == inst.id).all())
    avail: dict[str, list[tuple[int, int]]] = {}
    for r in avail_rows:
        avail.setdefault(names[r.worker_id], []).append(
            (r.available_from, min(r.available_until, horizon)))

    assert set(unavail) == set(names.values())   # every worker represented
    for wname in names.values():
        ivs = sorted(unavail[wname])
        merged = merge_intervals(ivs)
        assert merged == ivs, f"unavailability not disjoint/normalized: {wname}"
        assert complement(0, horizon, ivs + avail.get(wname, [])) == [], \
            f"coverage gap for {wname}"
        assert ivs[-1][1] == horizon or \
            any(a[1] >= horizon for a in avail.get(wname, [])), \
            f"tail gap after last shift for {wname}"


def test_weights_present_mean_preserving(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    w = p["job_tardiness_weights"]
    assert len(w) == 30                      # every demo job has a TWK deadline
    assert sum(w.values()) / len(w) == pytest.approx(1.0, rel=1e-9)
    prio_by_job = {j["job_id"]: j["priority"] for j in p["jobs"]}
    strongest = max(w, key=lambda k: w[k])
    weakest = min(w, key=lambda k: w[k])
    assert prio_by_job[strongest] <= prio_by_job[weakest]


def test_baseline_deterministic_bytes(demo_session):
    session, inst = demo_session
    a = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    b = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------- baseline payload on pure MK01 (DB) ----------

def test_mk01_benchmark_path_degrades_gracefully(mk01_session):
    session, inst = mk01_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    assert len(p["machines"]) == 6
    assert len(p["jobs"]) == 10
    assert p["setup_times"] == []
    assert "job_tardiness_weights" not in p     # no deadlines at all
    for job in p["jobs"]:
        assert job["deadline"] is None
        for op in job["operations"]:
            for alt in op["alternatives"]:
                assert alt["workers"] == {}     # no worker layer on source instance


# ---------- final-review regressions ----------

def test_failed_status_dead_end_blocks_on_baseline(demo_session):
    """Rider-d gap: FAILED-status stripping on BASELINE must dead-end-block,
    never leak a zero-combo pending op into the engine."""
    from coe.db.models.fjsp import Machine, OperationMachineAlternative

    session, inst = demo_session
    rows = (session.query(OperationMachineAlternative.operation_id,
                          OperationMachineAlternative.machine_id)
            .filter(OperationMachineAlternative.instance_id == inst.id)
            .order_by(OperationMachineAlternative.operation_id,
                      OperationMachineAlternative.machine_id).all())
    per: dict[int, list[int]] = {}
    for oid_, mid in rows:
        per.setdefault(oid_, []).append(mid)
    solo_mid = next(mids[0] for mids in per.values() if len(mids) == 1)
    session.query(Machine).filter(Machine.id == solo_mid).update(
        {Machine.status: "FAILED"})
    session.flush()
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=30)
    reasons = {b["reason"] for b in p["blocked_operations"]}
    assert "NO_CAPABLE_MACHINES" in reasons


def test_setup_matrix_asymmetric_warning(demo_session):
    """Spec §3.1 hygiene: a named setup pair whose reverse row is missing on
    the same machine emits exactly one SETUP_MATRIX_ASYMMETRIC warning."""
    from coe.db.models.fjsp import JobFamily, Machine, SetupTime

    session, inst = demo_session
    m0 = (session.query(Machine)
          .filter(Machine.instance_id == inst.id, Machine.name == "M0")
          .one())
    fams = (session.query(JobFamily)
            .filter(JobFamily.instance_id == inst.id)
            .order_by(JobFamily.id).limit(2).all())
    assert len(fams) == 2
    fa, fb = fams
    stale = (session.query(SetupTime)
             .filter(SetupTime.instance_id == inst.id,
                     SetupTime.machine_id == m0.id,
                     SetupTime.from_family_id.in_([fa.id, fb.id]),
                     SetupTime.to_family_id.in_([fa.id, fb.id]))
             .all())
    for row in stale:
        session.delete(row)
    session.add(SetupTime(instance_id=inst.id, machine_id=m0.id,
                          from_family_id=fa.id, to_family_id=fb.id,
                          setup_duration=7, source="review-fixture"))
    session.flush()
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=30)
    warns = [w for w in p["warnings"]
             if w["type"] == "SETUP_MATRIX_ASYMMETRIC"]
    assert warns == [{"type": "SETUP_MATRIX_ASYMMETRIC",
                      "machine_id": "M0",
                      "from_family": fa.name, "to_family": fb.name}]
