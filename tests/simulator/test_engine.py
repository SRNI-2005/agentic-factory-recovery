# tests/simulator/test_engine.py
"""Task 6: engine walker — scripted-day playback through shared entry
points (spec §5). Structured events reuse Phase 1 ingestion; NARRATIVE
events run the real recovery graph with a fake LLM client."""
import json

import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient

NARRATIVE = "MC-04 gearbox seized, sparks everywhere"
# MC-04 binds (unique digit match) to M4 among machines M0..M7
GOOD_MACHINE = {
    "kind": "MACHINE", "instance_id": "factory_demo_01",
    "machine_id": "M4", "event_type": "FAILURE", "occurred_at": 512,
    "severity": "HIGH", "estimated_downtime": 90,
    "narrative_excerpt": NARRATIVE,
}

_STRATEGY = '{"candidates": [], "final": true}'
_EXPLAIN = "M4 failed; work rerouted to capable alternatives."


def _canned_disruption() -> str:
    return json.dumps(GOOD_MACHINE)


def _timeline(path, events):
    data = {"name": "t1", "seed": 42, "horizon_days": 1, "events": events}
    p = path / "t.json"
    p.write_text(json.dumps(data))
    return str(p)


def _simul_count() -> int:
    from sqlalchemy import text

    from coe.db.session import make_engine

    with make_engine().begin() as c:
        return c.execute(text(
            "SELECT COUNT(*) FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='factory_demo_01' "
            "AND te.message_id LIKE 'simul-%'")).scalar()


def test_structured_event_ingests_through_shared_path(tmp_path, demo_scenario):
    from coe.simulator.engine import walk_timeline

    tl_path = _timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"}])
    items = list(walk_timeline(tl_path, instance_name="factory_demo_01",
                               speed="instant"))
    assert _simul_count() >= 1
    assert any(i.get("event") == "done" for i in items)


@pytest.mark.slow
def test_narrative_event_runs_graph_commit(tmp_path, demo_scenario):
    from coe.simulator.engine import walk_timeline

    def fake_factory():
        return FakeLLMClient([_canned_disruption(), _STRATEGY, _EXPLAIN])

    items = list(walk_timeline(
        _timeline(tmp_path, [
            {"t": 100, "kind": "NARRATIVE",
             "text": "M3 gearbox seized", "severity": "HIGH"}]),
        instance_name="factory_demo_01", speed="instant",
        llm_client_factory=fake_factory))
    committed = [c["committed"] for c in items if c.get("event") == "done"]
    assert committed and len(committed[0]) >= 1   # at least one recovery version


def test_resume_skips_completed_prefix(tmp_path, demo_scenario):
    """start_index=k suppresses events < k (already persisted)."""
    from coe.simulator.engine import walk_timeline

    script = _timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"},
        {"t": 150, "kind": "WORKER", "event_type": "WORKER_ABSENT",
         "worker_id": "W3", "duration": 60}])
    first = list(walk_timeline(script, instance_name="factory_demo_01",
                               speed="instant"))
    assert [i["idx"] for i in first if i["event"] == "ingest"] == [0, 1]
    before = _simul_count()
    second = list(walk_timeline(script, instance_name="factory_demo_01",
                                speed="instant", start_index=1))
    assert [i["idx"] for i in second if i["event"] == "ingest"] == [1]
    assert _simul_count() == before   # first event not re-executed


def test_replay_idempotent_and_deterministic(tmp_path, demo_scenario):
    from coe.simulator.engine import walk_timeline
    from sqlalchemy import text

    from coe.db.session import make_engine

    script = str(_timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"},
        {"t": 150, "kind": "WORKER", "event_type": "WORKER_ABSENT",
         "worker_id": "W3", "duration": 60},
        {"t": 200, "kind": "MATERIAL", "event_type": "MATERIAL_RESTOCK",
         "sku": "MAT-001", "quantity": 40}]))
    first = list(walk_timeline(script, instance_name="factory_demo_01",
                               speed="instant"))
    # deterministic message ids: replay everything, count ids
    with make_engine().begin() as c:
        ids = [r[0] for r in c.execute(text(
            "SELECT DISTINCT te.message_id FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='factory_demo_01' AND te.message_id "
            "LIKE 'simul-%'")).all()]
    assert len(ids) == 3          # one per scripted structured event
    second = list(walk_timeline(script, instance_name="factory_demo_01",
                                speed="instant", start_index=2))
    assert any(c.get("event") == "done" for c in second)
    assert second[-1]["committed"] == []   # nothing re-executed post-prefix
    with make_engine().begin() as c:
        ids2 = [r[0] for r in c.execute(text(
            "SELECT DISTINCT te.message_id FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='factory_demo_01' "
            "AND te.message_id LIKE 'simul-%'")).all()]
    assert ids2 == ids                    # no duplicates on replay


def test_full_replay_does_not_rematerialize_restock(tmp_path, demo_scenario):
    """Regression: restock materialization is gated on ingest `created`,
    so a full replay (start_index=0) leaves the RESTOCK ledger and
    receipts reservoir byte-for-byte in count unchanged."""
    from coe.simulator.engine import walk_timeline
    from sqlalchemy import text

    from coe.db.session import make_engine

    script = str(_timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"},
        {"t": 150, "kind": "MATERIAL", "event_type": "MATERIAL_RESTOCK",
         "sku": "MAT-001", "quantity": 40}]))
    list(walk_timeline(script, instance_name="factory_demo_01",
                       speed="instant"))

    def _counts():
        with make_engine().begin() as c:
            restocks = c.execute(text(
                "SELECT COUNT(*) FROM material_transactions mt "
                "JOIN instances i ON i.id=mt.instance_id "
                "WHERE i.name='factory_demo_01' "
                "AND mt.transaction_type='RESTOCK'")).scalar()
            receipts = c.execute(text(
                "SELECT COUNT(*) FROM material_receipts mr "
                "JOIN instances i ON i.id=mr.instance_id "
                "WHERE i.name='factory_demo_01' "
                "AND mr.source='simulate'")).scalar()
        return restocks, receipts

    restocks, receipts = _counts()
    assert (restocks, receipts) == (1, 1)
    items = list(walk_timeline(script, instance_name="factory_demo_01",
                               speed="instant", start_index=0))
    assert [i["created"] for i in items
            if i.get("event") == "ingest"] == [False, False]
    assert _counts() == (restocks, receipts)
