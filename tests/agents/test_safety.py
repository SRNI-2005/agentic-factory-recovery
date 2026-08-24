# tests/agents/test_safety.py
"""§6.2 gate refuses corruption; §6.3 verifier rolls back tampering."""
import pytest

pytestmark = pytest.mark.db


@pytest.fixture()
def solved_world(clean_db):
    """Instance with baseline v1 plus v2 committed; v2 active."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.solver.committer import commit_solution

    with session_scope() as session:
        inst = Instance(name="safe-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m = Machine(instance_id=iid, name="M1")
        session.add(m)
        session.flush()
        j = Job(instance_id=iid, name="J-1", priority=1, release_time=0)
        session.add(j)
        session.flush()
        o = Operation(instance_id=iid, job_id=j.id, sequence_number=1)
        session.add(o)
        session.flush()
        session.add(OperationMachineAlternative(
            instance_id=iid, operation_id=o.id, machine_id=m.id,
            processing_time=5))

        payload = {
            "instance_id": "safe-world", "schedule_type": "BASELINE",
            "parent_version_id": None,
            "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                       "normalize_objectives": True, "random_seed": 42,
                       "num_search_workers": 1},
            "machines": ["M1"], "failed_machines": [],
            "machine_initial_families": {}, "warnings": [],
            "jobs": [{"job_id": "J-1", "family_id": None, "release_time": 0,
                      "deadline": None, "priority": 1,
                      "operations": [{
                          "operation_id": "J-1-O1", "sequence": 1,
                          "status": "PENDING", "materials": [],
                          "alternatives": [{"machine_id": "M1",
                                            "processing_time": 5,
                                            "workers": {}}],
                          "frozen": None}]}],
            "machine_downtime": [], "materials": [],
            "material_receipts": [], "worker_unavailability": [],
            "setup_times": [], "blocked_operations": [],
            "suspended_jobs": [],
        }
        solution = {
            "status": "OPTIMAL", "objective_value": 5.0, "makespan": 5,
            "total_tardiness": 0,
            "assignments": [{"operation_id": "J-1-O1", "job_id": "J-1",
                             "machine_id": "M1", "worker_id": None,
                             "start": 0, "end": 5, "processing_time": 5,
                             "setup_time": 0, "is_frozen": False}],
            "solve_duration_seconds": 0.01,
        }
        version = commit_solution(session, instance_row=inst,
                                  payload=payload, solution=solution)
        version = commit_solution(session, instance_row=inst,
                                  payload=payload, solution=solution)
        vid = version.id
    return iid, vid, payload, solution


def test_gate_passes_clean_solution(solved_world):
    from coe.agents.safety import run_gate

    _, _, payload, solution = solved_world
    assert run_gate(payload, solution)["passed"] is True


def test_gate_refuses_duration_corruption(solved_world):
    from coe.agents.safety import run_gate

    _, _, payload, solution = solved_world
    bad = dict(solution, assignments=[dict(solution["assignments"][0],
                                           end=9)])
    res = run_gate(payload, bad)
    assert res["passed"] is False
    assert any("duration arithmetic" in v for v in res["violations"])


def test_gate_refuses_failed_machine_assignment(solved_world):
    from coe.agents.safety import run_gate

    _, _, payload, solution = solved_world
    payload2 = dict(payload, machines=[], failed_machines=["M1"])
    res = run_gate(payload2, solution)
    assert res["passed"] is False


def test_verify_clean_passes(solved_world):
    from coe.agents.safety import verify_commit

    res = verify_commit("safe-world")
    assert res["passed"] is True
    assert res["rolled_back_from"] is None


def test_verify_detects_tamper_and_rolls_back(solved_world):
    from coe.agents.safety import verify_commit
    from coe.db.session import make_engine
    from sqlalchemy import text

    engine = make_engine()
    with engine.begin() as c:
        c.execute(text(
            "UPDATE schedule_entries SET end_time = end_time + 7 "
            "WHERE version_id = :vid"), {"vid": solved_world[1]})
    res = verify_commit("safe-world")
    assert res["passed"] is False
    assert res["rolled_back_from"] is not None
    with engine.begin() as c:
        rb = c.execute(text(
            "SELECT rolled_back FROM schedule_versions WHERE id = :vid"),
            {"vid": solved_world[1]}).scalar_one()
    assert rb is True
