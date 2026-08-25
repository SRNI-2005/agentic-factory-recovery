"""§4.3 negotiation + Amendment 2026-08-24 fixed procedure."""
import pytest

from coe.agents.state import RecoveryState

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _p3_instance(clean_db):
    """Instance "p3" + its two consuming jobs.

    Plan-code deviation: the task brief's snippet created only the Instance
    row, but LLM-path verdicts resolve jobs against the DB — without
    Job("J-A") the happy-path candidate would come back unknown_job instead
    of VALID.
    """
    from coe.db.models.fjsp import Job
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="p3", source_name="synthetic")
        session.add(inst)
        session.flush()
        session.add_all([
            Job(instance_id=inst.id, name="J-A", priority=1, deadline=50),
            Job(instance_id=inst.id, name="J-B", priority=3, deadline=90),
        ])


def _payload(shortage=True, receipts=()):
    jobs = [
        {"job_id": "J-A", "family_id": None, "release_time": 0,
         "deadline": 50, "priority": 1,
         "operations": [{"operation_id": "J-A-O1", "sequence": 1,
                         "status": "PENDING",
                         "materials": [{"sku": "MAT-X", "quantity": 5}]
                         if shortage else [],
                         "alternatives": [], "frozen": None}]},
        {"job_id": "J-B", "family_id": None, "release_time": 0,
         "deadline": 90, "priority": 3,
         "operations": [{"operation_id": "J-B-O1", "sequence": 1,
                         "status": "PENDING",
                         "materials": [{"sku": "MAT-X", "quantity": 5}],
                         "alternatives": [], "frozen": None}]},
    ]
    warns = ([{"type": "MATERIAL_SHORTFALL", "material_sku": "MAT-X",
               "total_supply": 5, "total_demand": 10}] if shortage else [])
    return {
        "instance_id": "p3", "schedule_type": "RECOVERY",
        "parent_version_id": 1,
        "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                   "normalize_objectives": True, "random_seed": 42,
                   "num_search_workers": 8},
        "machines": ["M1"], "failed_machines": [],
        "machine_initial_families": {}, "warnings": warns,
        "jobs": jobs, "machine_downtime": [],
        "materials": [{"sku": "MAT-X", "capacity": 5}],
        "material_receipts": list(receipts),
        "worker_unavailability": [], "setup_times": [],
        "blocked_operations": [], "suspended_jobs": [],
        "job_tardiness_weights": {},
    }


def _state(**over):
    base = {"instance_name": "p3", "reference_clock": 10,
            "compiled_payload": _payload(),
            "material_reactive": True,
            "db_facts": {"projected_horizon": 500}}
    base.update(over)
    return RecoveryState(**base)


def test_defer_branch_picks_lowest_ranked():
    from coe.agents.nodes.strategy import material_reactive_plan

    st = _state(compiled_payload=_payload(
        receipts=[{"sku": "MAT-X", "quantity": 10, "available_at": 200}]))
    plan = material_reactive_plan(st)
    types = [c["type"] for c in plan["candidates"]]
    assert types == ["DEFER_JOB"]
    c = plan["candidates"][0]
    assert c["job_id"] == "J-B"                 # priority 3 sacrifices
    assert c["release_offset"] == 200           # lands start after receipt
    assert plan["final"] is True
    assert "J-B deferred" in plan["note"]
    assert "keeps the MAT-X" in plan["note"]


def test_suspend_branch_without_receipts():
    from coe.agents.nodes.strategy import material_reactive_plan

    plan = material_reactive_plan(_state())
    assert plan["candidates"][0]["type"] == "SUSPEND_JOB"
    assert plan["candidates"][0]["job_id"] == "J-B"


def test_receipt_beyond_horizon_still_suspends():
    from coe.agents.nodes.strategy import material_reactive_plan

    st = _state(compiled_payload=_payload(
        receipts=[{"sku": "MAT-X", "quantity": 100, "available_at": 900}]))
    plan = material_reactive_plan(st)
    assert plan["candidates"][0]["type"] == "SUSPEND_JOB"


def test_llm_round_validated_and_recorded():
    from coe.agents.nodes.strategy import run_strategy_round
    from tests.fixtures.llm.fake_client import FakeLLMClient

    resp = '{"candidates": [{"type": "TARDINESS_WEIGHT", ' \
        '"job_id": "J-A", "weight": 0.5}], "final": true}'
    out = run_strategy_round(
        _state(material_reactive=False),
        client=FakeLLMClient([resp]), max_retries=1)
    assert out.round_count == 1
    assert out.strategy_final is True
    assert out.round_verdicts[-1]["verdict"] == "VALID"
    assert out.strategy_candidates[-1]["candidate"]["type"] \
        == "TARDINESS_WEIGHT"


def test_llm_garbage_falls_back_empty_with_warning():
    from coe.agents.nodes.strategy import run_strategy_round
    from tests.fixtures.llm.fake_client import FakeLLMClient

    out = run_strategy_round(
        _state(material_reactive=False),
        client=FakeLLMClient(["not json", "still not json"]), max_retries=1)
    assert out.strategy_final is True
    assert out.strategy_candidates == []
    assert any("fallback" in w for w in out.warnings)


def test_llm_transport_failure_falls_back_not_crashes():
    from coe.agents.nodes.strategy import run_strategy_round

    class _Down:
        def __init__(self):
            self.calls = 0

        def complete(self, *, system, user):
            self.calls += 1
            raise TimeoutError("provider timeout")

    client = _Down()
    out = run_strategy_round(_state(material_reactive=False),
                             client=client, max_retries=2)
    assert client.calls == 3      # every attempt consumed by transport errs
    assert out.strategy_final is True
    assert out.strategy_candidates == []
    assert any("fallback" in w for w in out.warnings)


def test_invalid_candidate_recorded_not_applied_later():
    from coe.agents.nodes.strategy import run_strategy_round
    from tests.fixtures.llm.fake_client import FakeLLMClient

    resp = '{"candidates": [{"type": "TARDINESS_WEIGHT", ' \
        '"job_id": "GHOST", "weight": 1}], "final": false}'
    out = run_strategy_round(
        _state(material_reactive=False),
        client=FakeLLMClient([resp]), max_retries=1)
    assert out.strategy_final is False          # agent wants another round
    assert out.round_verdicts[0]["verdict"] == "INVALID"
    assert out.round_verdicts[0]["reason"].startswith("unknown_job")
