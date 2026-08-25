# tests/agents/test_acceptance_phase3.py
"""§12 acceptance pins needing the real engine (slow tier).

Deviation notes (vs plan brief):
- Criterion 15 pins enter MQTT-style (record + source_message_id): a record
  without source_message_id routes to `translate`, which would burn fake
  responses on an empty narrative.
- Strategy round 1 always precedes the first compile (material_reactive is
  armed BY compile), so the fake client carries one pre-compile JSON round
  plus the explain prose; the deterministic strategist itself consumes no
  client call.
- The committing sacrifice is pinned to SUSPEND_JOB: post-amendment-third
  evaluate_materials counts receipts into warning supply, so any payload
  carrying MATERIAL_SHORTFALL is globally short and the DEFER branch cannot
  produce a solver-feasible commit end-to-end.
"""
import pytest

pytestmark = [pytest.mark.db, pytest.mark.slow]


@pytest.fixture(scope="module")
def demo(db_url):
    """factory_demo_01 + real CP-SAT baseline, built once per module."""
    from pathlib import Path
    from types import SimpleNamespace

    from coe.cli import _run_solve
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    reset_database(db_url)
    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))
    build_scenario("factory_demo_01", seed=42)
    _run_solve(SimpleNamespace(
        solve_cmd="baseline", instance="factory_demo_01", alpha=None,
        beta=None, time_limit=None, seed=None, workers=None,
        no_normalize=False))


def test_criterion_1_factory_recovery_commits_child(demo):
    """Criterion 1: narrative -> committed RECOVERY child of the active
    baseline through the full pipeline with the REAL CP-SAT engine."""
    from sqlalchemy import text

    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine

    from tests.fixtures.llm.fake_client import FakeLLMClient

    narrative = ("M3 spindle seized, loud bang, we pulled the part — "
                 "looks like several hours")
    client = FakeLLMClient([
        '{"kind": "MACHINE", "instance_id": "factory_demo_01", '
        '"machine_id": "M3", "event_type": "FAILURE", "occurred_at": 512, '
        '"severity": "HIGH", "estimated_downtime": 300, '
        '"narrative_excerpt": "M3 spindle seized"}',
        '{"candidates": [], "final": true}',
        "M3 failed at minute 512; work rerouted to capable alternatives "
        "with frozen history preserved.",
    ])
    out = execute_recovery(
        "factory_demo_01", trigger="CLI", narrative=narrative,
        reference_clock=512, client=client)
    assert out["status"] == "COMMITTED"
    engine = make_engine()
    with engine.begin() as c:
        v = c.execute(text(
            "SELECT schedule_type, parent_version_id IS NOT NULL AS hasp "
            "FROM schedule_versions WHERE id=:vid"),
            {"vid": out["state"].committed_version_id}).one()
    assert v.schedule_type == "RECOVERY" and v.hasp


def test_criterion_15a_suspend_commits_protected_schedule(clean_db):
    """Criterion 15 leg A: a shortfall-triggered run suspends the
    lower-priority job, commits the protected schedule, and records the
    sacrifice in schedule_explanations (§12.15)."""
    from sqlalchemy import select

    from coe.agents.graph import execute_recovery
    from coe.db.models.recovery import ScheduleExplanation
    from coe.db.session import make_engine

    from tests.agents.worlds import build_shortage_world
    from tests.fixtures.llm.fake_client import FakeLLMClient

    inst = build_shortage_world(name="mr-a", receipt_at=None)
    record = {"kind": "MATERIAL", "instance_id": inst,
              "material_sku": "MAT-X",
              "event_type": "MATERIAL_SHORTAGE", "occurred_at": 5,
              "severity": "HIGH", "narrative_excerpt": "short"}
    client = FakeLLMClient([
        '{"candidates": [], "final": true}',   # pre-compile negotiation round
        "Suspended J-B so J-A keeps the MAT-X stock.",
    ])
    out = execute_recovery(inst, trigger="MQTT", record=record,
                           source_message_id="mr-a-msg-1",
                           reference_clock=5, client=client)
    assert out["status"] == "COMMITTED"
    st = out["state"]
    applied = [w for w in st.compiled_payload["warnings"]
               if w["type"] == "STRATEGY_APPLIED"]
    assert any(x["candidate"]["type"] in ("DEFER_JOB", "SUSPEND_JOB")
               for x in applied)
    jb = [j for j in st.compiled_payload["jobs"]
          if j["job_id"] == "J-B"][0]
    assert st.compiled_payload["suspended_jobs"] == ["J-B"]
    assert all(o["status"] == "BLOCKED" for o in jb["operations"])
    ja = [j for j in st.compiled_payload["jobs"]
          if j["job_id"] == "J-A"][0]
    assert all(o["status"] == "PENDING" for o in ja["operations"])
    with make_engine().connect() as c:
        rows = c.execute(select(ScheduleExplanation)).scalars().all()
    assert len(rows) == 1                       # sacrifice explained (§12.15)


def test_criterion_15b_infeasible_after_budget_nothing_committed(
        clean_db, monkeypatch):
    """Criterion 15 leg B: a material-driven INFEASIBLE surviving the round
    budget terminates SOLVE_INFEASIBLE with zero new versions."""
    from sqlalchemy import text

    from coe.agents import graph as graph_mod
    from coe.db.session import make_engine

    from tests.agents.worlds import build_shortage_world
    from tests.fixtures.llm.fake_client import FakeLLMClient

    inst = build_shortage_world(name="mr-b", receipt_at=None)

    def always_infeasible(payload):
        return {"status": "INFEASIBLE", "objective_value": 0.0,
                "makespan": 0, "total_tardiness": 0, "assignments": [],
                "solve_duration_seconds": 0.001}

    monkeypatch.setattr(graph_mod, "_solve_for_test", always_infeasible)
    record = {"kind": "MATERIAL", "instance_id": inst,
              "material_sku": "MAT-X",
              "event_type": "MATERIAL_SHORTAGE", "occurred_at": 5,
              "severity": "HIGH", "narrative_excerpt": "short"}
    out = graph_mod.execute_recovery(
        inst, trigger="MQTT", record=record,
        source_message_id="mr-b-msg-1", reference_clock=5,
        client=FakeLLMClient(['{"candidates": [], "final": true}']))
    assert out["status"] == "SOLVE_INFEASIBLE"
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM schedule_versions sv "
            "JOIN instances i ON i.id = sv.instance_id "
            "WHERE i.name = :n AND sv.version_number > 1"),
            {"n": inst}).scalar_one()
    assert n == 0                               # nothing committed
