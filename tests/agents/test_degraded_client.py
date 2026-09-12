# tests/agents/test_degraded_client.py
"""DegradedLLMClient — deterministic answers behind the LLMClient protocol.

Covers the translate binding rules (records.py check_narrative_ids
convention), the strategy no-op (§3.3), the explain constant, and the
unknown-node guard. DB-touching behavior lives in
tests/simulator/test_engine.py (degraded walk on a clone).
"""
import json

import pytest

from coe.agents.degraded_client import DegradedLLMClient, _report_text

pytestmark = pytest.mark.db

TRANSLATE_SYS = ("You translate factory disruption reports into ONE "
                 "structured JSON record. Output ONLY the JSON object.")
STRATEGY_SYS = ('You are a factory recovery strategist. Given database '
                'facts and prior verdicts, emit STRICT JSON.')
EXPLAIN_SYS = ("You explain factory schedule changes to a production "
               "planner. Input: JSON describing the previous vs new "
               "schedule plus constraint highlights.")

MACHINES = [f"M{i}" for i in range(8)]
WORKERS = [f"W{i}" for i in range(1, 9)]
MATERIALS = ["MAT-001", "MAT-002"]


def _user(report, *, clock=512, instance="factory_demo_01"):
    return (
        f"Target instance: {instance}\n"
        f"Reference clock: minute {clock}\n"
        f"Valid machine IDs: {', '.join(MACHINES)}\n"
        f"Valid worker IDs: {', '.join(WORKERS)}\n"
        f"Valid material SKUs: {', '.join(MATERIALS)}\n"
        f"Report:\n{report}")


def test_translate_machine_failure_binds_digits():
    out = json.loads(DegradedLLMClient().complete(
        system=TRANSLATE_SYS, user=_user("MC-04 gearbox seized")))
    assert out["kind"] == "MACHINE"
    assert out["machine_id"] == "M4"
    assert out["event_type"] == "FAILURE"
    assert out["instance_id"] == "factory_demo_01"
    assert out["occurred_at"] == 512
    assert out["severity"] == "HIGH"      # "seized" keyword hit
    assert out["narrative_excerpt"] == "MC-04 gearbox seized"


def test_translate_worker_return():
    out = json.loads(DegradedLLMClient().complete(
        system=TRANSLATE_SYS,
        user=_user("W3 is back, fully available again", clock=600)))
    assert out["kind"] == "WORKER"
    assert out["worker_id"] == "W3"
    assert out["event_type"] == "WORKER_RETURN"
    assert out["occurred_at"] == 600


def test_translate_worker_absent():
    out = json.loads(DegradedLLMClient().complete(
        system=TRANSLATE_SYS,
        user=_user("W5 called in sick this morning")))
    assert out["kind"] == "WORKER"
    assert out["worker_id"] == "W5"
    assert out["event_type"] == "WORKER_ABSENT"


def test_translate_material_shortage_and_restock():
    c = DegradedLLMClient()
    short = json.loads(c.complete(
        system=TRANSLATE_SYS,
        user=_user("MAT-001 bin is empty, line stuck")))
    assert short["kind"] == "MATERIAL"
    assert short["material_sku"] == "MAT-001"
    assert short["event_type"] == "MATERIAL_SHORTAGE"
    restock = json.loads(c.complete(
        system=TRANSLATE_SYS,
        user=_user("MAT-001 delivery arrived at the dock")))
    assert restock["event_type"] == "MATERIAL_RESTOCK"


def test_translate_unknown_token_rejects():
    out = json.loads(DegradedLLMClient().complete(
        system=TRANSLATE_SYS, user=_user("MC-999 gearbox exploded")))
    assert out == {"error": "unknown resource MC-999"}


def test_translate_severity_keywords():
    c = DegradedLLMClient()
    assert json.loads(c.complete(
        system=TRANSLATE_SYS,
        user=_user("M2 major breakdown, line down")))["severity"] == "HIGH"
    assert json.loads(c.complete(
        system=TRANSLATE_SYS,
        user=_user("M2 minor hiccup")))["severity"] == "LOW"


def test_strategy_returns_no_candidates_final():
    out = json.loads(DegradedLLMClient().complete(
        system=STRATEGY_SYS, user='{"instance": "x", "db_facts": {}}'))
    assert out == {"candidates": [], "final": True}


def test_explain_returns_constant_text():
    out = DegradedLLMClient().complete(system=EXPLAIN_SYS, user="{}")
    assert out == ("Auto-recovery committed. (LLM disabled for this run — "
                   "deterministic re-plan only.)")


def test_unknown_node_raises():
    with pytest.raises(RuntimeError, match="unknown node"):
        DegradedLLMClient().complete(system="be a pirate",
                                     user="say arr")


def test_report_text_helper_takes_after_marker():
    assert _report_text(" preamble\nReport:\nMC-04 down") == "MC-04 down"
