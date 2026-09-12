# tests/simulator/test_auto_recover.py
"""auto_recover amendment (spec §5, amendment 2026-09-13): every
structured disruption event itself triggers a full recovery solve;
narrative steps become optional free-text solves."""
import json
import subprocess

import pytest

pytestmark = pytest.mark.db

# token-free report lines degrade to the FIRST valid machine
# (DegradedLLMClient fallback) — synthesized auto-recover text therefore
# always leads with its own resource id (see engine._auto_recover_narrative).
_USER = (
    "Target instance: factory_demo_01\n"
    "Reference clock: minute 100\n"
    "Valid machine IDs: M0, M1, M2, M3, M4, M5, M6, M7\n"
    "Valid worker IDs: W1, W2, W3, W4\n"
    "Valid material SKUs: MAT-001, MAT-002\n"
    "Report: {report}")


def _translated(report: str) -> dict:
    from coe.agents.degraded_client import DegradedLLMClient

    return json.loads(DegradedLLMClient()._translate(
        _USER.format(report=report)))


def test_timeline_default_auto_recover_true():
    from coe.simulator.timeline import NarrativeEvent, Timeline

    tl = Timeline(name="t", events=[NarrativeEvent(t=0, kind="NARRATIVE",
                                                   text="x")])
    assert tl.auto_recover is True


def test_cli_auto_recover_flag_default_none():
    from coe.cli import build_parser

    args = build_parser().parse_args(
        ["simulate", "timeline", "--file", "x.json"])
    assert args.auto_recover is None
    args = build_parser().parse_args(
        ["simulate", "timeline", "--file", "x.json", "--no-auto-recover"])
    assert args.auto_recover is False
    args = build_parser().parse_args(
        ["simulate", "timeline", "--file", "x.json", "--auto-recover"])
    assert args.auto_recover is True


def _timeline(path, events, auto_recover=None):
    data = {"name": "t1", "seed": 42, "horizon_days": 1, "events": events}
    if auto_recover is not None:
        data["auto_recover"] = auto_recover
    p = path / "t.json"
    p.write_text(json.dumps(data))
    return str(p)


def _walk_chunks(script, **kw):
    from coe.simulator.engine import walk_timeline

    kw.setdefault("instance_name", "factory_demo_01")
    return list(walk_timeline(script, speed="instant", **kw))


def test_auto_recover_false_yields_zero_recoveries(tmp_path, demo_scenario):
    """Explicit auto_recover=False preserves today's semantics: structured
    facts ingest, no recovery is ever spawned."""
    script = _timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"},
        {"t": 150, "kind": "MATERIAL", "event_type": "MATERIAL_RESTOCK",
         "sku": "MAT-001", "quantity": 40}], auto_recover=False)
    items = _walk_chunks(script)
    assert not any(i.get("event") in ("auto_recover", "recovery_start",
                                      "recovery") for i in items)
    assert any(i.get("event") == "done" for i in items)
    assert items[-1]["committed"] == []


def test_auto_recover_synthesized_narratives_bind_own_resources(tmp_path):
    """Degraded translate fallback: token-free lines map to the first
    valid machine — so synthesized text prefixes machine_id / worker_id /
    sku, and each class binds its OWN resource (and real event_type where
    the classifier supports it)."""
    from coe.simulator.engine import _auto_recover_narrative
    from coe.simulator.timeline import (
        MachineEvent,
        MaterialEvent,
        WorkerEvent,
    )

    machine = _auto_recover_narrative(MachineEvent(
        t=100, kind="MACHINE", event_type="FAILURE", machine_id="M3"))
    assert machine.endswith("at minute 100") and machine.startswith(
        "auto-recover: M3")
    rec = _translated(machine)
    assert rec["machine_id"] == "M3"

    worker = _auto_recover_narrative(WorkerEvent(
        t=330, kind="WORKER", event_type="WORKER_ABSENT", worker_id="W3"))
    rec = _translated(worker)
    assert rec["worker_id"] == "W3"
    assert rec["event_type"] == "WORKER_ABSENT"

    restock = _auto_recover_narrative(MaterialEvent(
        t=300, kind="MATERIAL", event_type="MATERIAL_RESTOCK", sku="MAT-001",
        quantity=112))
    rec = _translated(restock)
    assert rec["material_sku"] == "MAT-001"
    assert rec["event_type"] == "MATERIAL_RESTOCK"


@pytest.mark.slow
def test_auto_recover_walks_solve_and_commit_per_structured_event(
        tmp_path):
    """Fork-clone-per-file pattern: baseline-bearing unique fork, one
    structured MACHINE FAILURE with auto_recover=True (default) — the
    walk yields auto_recover + recovery_start for the structured event
    and commits a non-empty chain."""
    from sqlalchemy.orm import Session

    from coe.agents.degraded_client import DegradedLLMClient
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.services.fork import fork_instance

    with Session(make_engine()) as s:
        source = (s.query(Instance)
                  .filter(Instance.name == "factory_demo_01").one())
        forked = fork_instance(s, source)
        s.commit()
        clone = forked.name
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", clone, "--workers", "1"],
        check=True, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr

    script = _timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"}])
    items = _walk_chunks(script, instance_name=clone,
                         llm_client_factory=lambda: DegradedLLMClient())
    assert [i["event"] for i in items][:2] == ["ingest", "auto_recover"]
    assert any(i.get("event") == "recovery_start" for i in items)
    committed = [c["committed"] for c in items if c.get("event") == "done"]
    assert committed and committed[0], "auto_recover walk committed nothing"
