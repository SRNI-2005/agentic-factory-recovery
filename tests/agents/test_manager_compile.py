"""§4.4 manager compile + ordering contract (§6.1 tail)."""
import pytest

pytestmark = pytest.mark.db

from coe.agents.state import RecoveryState


@pytest.fixture()
def world(clean_db):
    """Mini instance WITH an active OPTIMAL v1 so RECOVERY builds work."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.materials import Material, OperationBom
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="mgr-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m = Machine(instance_id=iid, name="M1")
        w = Worker(instance_id=iid, name="W1")
        session.add_all([m, w])
        session.flush()
        ja = Job(instance_id=iid, name="J-A", priority=1, release_time=0,
                 deadline=60)
        jb = Job(instance_id=iid, name="J-B", priority=3, release_time=0,
                 deadline=90)
        session.add_all([ja, jb])
        session.flush()
        oa = Operation(instance_id=iid, job_id=ja.id, sequence_number=1)
        ob = Operation(instance_id=iid, job_id=jb.id, sequence_number=1)
        session.add_all([oa, ob])
        session.flush()
        for o in (oa, ob):
            session.add(OperationMachineAlternative(
                instance_id=iid, operation_id=o.id, machine_id=m.id,
                processing_time=5))
        mat = Material(instance_id=iid, sku="MAT-X", initial_stock=5)
        session.add(mat)
        session.flush()
        # both ops consume MAT-X: stock 5 < demand 10 -> shortfall
        for o in (oa, ob):
            session.add(OperationBom(instance_id=iid, operation_id=o.id,
                                     material_id=mat.id,
                                     quantity_required=5))
        v1 = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=10,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.1,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v1)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v1.id, operation_id=oa.id,
            machine_id=m.id, worker_id=w.id, start_time=0, end_time=5,
            processing_time=5, is_frozen=False, status="SCHEDULED"))
        session.flush()
    return iid


def _state(**over):
    base = {"instance_name": "mgr-world", "reference_clock": 20}
    base.update(over)
    return RecoveryState(**base)


def test_compiles_recovery_payload_with_parent(world):
    from coe.agents.nodes.manager import run_manager_compile

    out = run_manager_compile(_state())
    p = out.compiled_payload
    assert p["schedule_type"] == "RECOVERY"
    assert p["parent_version_id"] is not None
    assert out.material_reactive is True      # stock 5 < demand 10
    assert any(w["type"] == "MATERIAL_SHORTFALL"
               for w in p["warnings"])


def test_valid_candidate_applied_invalid_filtered(world):
    from coe.agents.nodes.manager import run_manager_compile

    cand = {"type": "DEFER_JOB", "job_id": "J-A", "release_offset": 15}
    st = _state(
        strategy_candidates=[{"candidate": cand, "round": 1}],
        round_verdicts=[
            {"candidate": cand, "round": 1, "verdict": "VALID",
             "reason": "ok"},
            {"candidate": {"type": "TARDINESS_WEIGHT",
                           "job_id": "J-B", "weight": 99}, "round": 1,
             "verdict": "INVALID", "reason": "out_of_bounds"},
        ])
    out = run_manager_compile(st)
    ja = [j for j in out.compiled_payload["jobs"]
          if j["job_id"] == "J-A"][0]
    assert ja["release_time"] == 15           # VALID applied
    applied = [w for w in out.compiled_payload["warnings"]
               if w["type"] == "STRATEGY_APPLIED"]
    assert len(applied) == 1                  # INVALID never reached applier


def test_weight_derivation_uses_post_preset_beta(world):
    from coe.agents.nodes.manager import run_manager_compile

    preset = {"type": "WEIGHT_PRESET", "alpha": 0.25, "beta": 2.0}
    st = _state(strategy_candidates=[
        {"candidate": preset, "round": 1}],
        round_verdicts=[{"candidate": preset, "round": 1,
                         "verdict": "VALID", "reason": "ok"}])
    out = run_manager_compile(st)
    assert out.compiled_payload["config"]["beta"] == 2.0
    w = out.compiled_payload.get("job_tardiness_weights") or {}
    assert w                                  # derived under beta=2
    total = sum(w.values())
    n_dl = len(w)
    assert abs(total - 2.0 * n_dl) < 1e-6     # mean-preserving around beta


def test_no_baseline_is_loud(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    from coe.agents.nodes.manager import NoBaselineError, run_manager_compile

    with session_scope() as session:
        session.add(Instance(name="mgr-empty", source_name="synthetic"))
    with pytest.raises(NoBaselineError):
        run_manager_compile(_state(instance_name="mgr-empty"))
