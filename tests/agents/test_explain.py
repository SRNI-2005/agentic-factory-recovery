# tests/agents/test_explain.py
"""§4.5 post-hoc explanation service."""
import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient


@pytest.fixture()
def two_versions(clean_db):
    """Parent v1 (J-1,J-2 both on M1) then child v2 (J-1 moved to M2,
    J-2 suspended via JOB_SUSPENDED)."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.solver.committer import commit_solution

    def _payload(parent, warnings=(), jobs_override=None):
        jobs = jobs_override if jobs_override is not None else [
            {"job_id": "J-1", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [{"operation_id": "J-1-O1", "sequence": 1,
                             "status": "PENDING", "materials": [],
                             "alternatives": [], "frozen": None}]},
            {"job_id": "J-2", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 2,
             "operations": [{"operation_id": "J-2-O1", "sequence": 1,
                             "status": "PENDING", "materials": [],
                             "alternatives": [], "frozen": None}]}]
        return {
            "instance_id": "exp-world", "schedule_type": "RECOVERY",
            "parent_version_id": parent, "config": {
                "alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                "normalize_objectives": True, "random_seed": 42,
                "num_search_workers": 1},
            "machines": ["M1", "M2"], "failed_machines": ["M1"],
            "machine_initial_families": {}, "warnings": list(warnings),
            "jobs": jobs, "machine_downtime": [], "materials": [],
            "material_receipts": [], "worker_unavailability": [],
            "setup_times": [], "blocked_operations": [],
            "suspended_jobs": []}

    def _sol(pairs):
        return {"status": "OPTIMAL", "objective_value": 1.0,
                "makespan": max(e for _, _, (s, e) in pairs),
                "total_tardiness": 0,
                "assignments": [
                    {"operation_id": oid, "job_id": oid.split("-O")[0],
                     "machine_id": mid, "worker_id": None,
                     "start": s, "end": e, "processing_time": e - s,
                     "setup_time": 0, "is_frozen": False}
                    for oid, mid, (s, e) in pairs],
                "solve_duration_seconds": 0.01}

    with session_scope() as session:
        inst = Instance(name="exp-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m1 = Machine(instance_id=iid, name="M1")
        m2 = Machine(instance_id=iid, name="M2")
        session.add_all([m1, m2])
        session.flush()
        j1 = Job(instance_id=iid, name="J-1", priority=1)
        j2 = Job(instance_id=iid, name="J-2", priority=2)
        session.add_all([j1, j2])
        session.flush()
        o11 = Operation(instance_id=iid, job_id=j1.id, sequence_number=1)
        o21 = Operation(instance_id=iid, job_id=j2.id, sequence_number=1)
        session.add_all([o11, o21])
        session.flush()
        for o in (o11, o21):
            for mm in (m1, m2):
                session.add(OperationMachineAlternative(
                    instance_id=iid, operation_id=o.id, machine_id=mm.id,
                    processing_time=5))

        v1 = commit_solution(
            session, instance_row=inst, payload=_payload(None),
            solution=_sol([("J-1-O1", "M1", (0, 5)),
                           ("J-2-O1", "M1", (5, 10))]))

        warn = [{"type": "STRATEGY_APPLIED", "round": 1,
                 "candidate": {"type": "SUSPEND_JOB", "job_id": "J-2"},
                 "field_changed": "suspended_jobs"},
                {"type": "DOWNTIME_CLIPPED", "machine_id": "M1",
                 "window": [10, 200], "clipped_to": [40, 200],
                 "reason": "overlaps frozen operations"}]
        jobs2 = [
            {"job_id": "J-1", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [{"operation_id": "J-1-O1", "sequence": 1,
                             "status": "PENDING", "materials": [],
                             "alternatives": [], "frozen": None}]},
            {"job_id": "J-2", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 2,
             "operations": [{"operation_id": "J-2-O1", "sequence": 1,
                             "status": "BLOCKED", "materials": [],
                             "alternatives": [], "frozen": None}]}]
        p2 = _payload(v1.id, warn, jobs_override=jobs2)
        p2["blocked_operations"] = [{"operation_id": "J-2-O1",
                                     "reason": "JOB_SUSPENDED",
                                     "material_sku": None}]
        p2["suspended_jobs"] = ["J-2"]
        v2 = commit_solution(
            session, instance_row=inst, payload=p2,
            solution=_sol([("J-1-O1", "M2", (40, 45))]))
    return v1.version_number, v2.version_number


def test_compute_diff_moves_blocks_strategies(two_versions):
    from coe.agents.nodes.explain import compute_diff
    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import session_scope

    with session_scope() as session:
        child = (session.query(ScheduleVersion)
                 .filter(ScheduleVersion.version_number
                         == two_versions[1]).one())
        parent = (session.query(ScheduleVersion)
                  .filter(ScheduleVersion.version_number
                          == two_versions[0]).one())
        diff = compute_diff(session, child, parent)
    moved = {(m["operation_id"], m["to"]["machine_id"])
             for m in diff["moved_operations"]}
    assert ("J-1-O1", "M2") in moved
    assert "J-2-O1" in diff["newly_blocked"]
    assert diff["applied_strategies"][0]["candidate"]["type"] == "SUSPEND_JOB"
    assert diff["clipped_windows"][0]["type"] == "DOWNTIME_CLIPPED"


def test_explain_version_stores_rationale(two_versions):
    from coe.agents.nodes.explain import explain_version
    from coe.db.models.recovery import ScheduleExplanation
    from coe.db.session import session_scope

    prose = explain_version(
        "exp-world",
        client=FakeLLMClient(["Moved J-1 off M1 because it failed; "
                              "suspended J-2."]))
    assert prose.startswith("Moved J-1")
    with session_scope() as session:
        rows = session.query(ScheduleExplanation).all()
    assert len(rows) == 1 and rows[0].rationale == prose


def test_explain_llm_failure_returns_none(two_versions):
    from coe.agents.nodes.explain import explain_version

    res = explain_version("exp-world", client=FakeLLMClient([]),
                          max_retries=1)
    assert res is None
