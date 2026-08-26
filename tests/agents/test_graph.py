# tests/agents/test_graph.py
"""§3.1 topology + Tier 3 pipeline integration with fake LLM."""
import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient

TRANSLATE_OK = ('{"kind": "MACHINE", "instance_id": "g-world", '
                '"machine_id": "M2", "event_type": "FAILURE", '
                '"occurred_at": 30, "severity": "HIGH", '
                '"estimated_downtime": 200, "narrative_excerpt": "boom"}')


@pytest.fixture()
def g_world(clean_db):
    from tests.agents.worlds import build_g_world

    build_g_world(clean_db)


def test_happy_path_commits_child_version(g_world):
    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine
    from sqlalchemy import text

    client = FakeLLMClient([
        TRANSLATE_OK,                          # translate
        '{"candidates": [], "final": true}',   # strategy round
        "Rerouted J-A and J-B off M2.",        # explain
    ])
    out = execute_recovery(
        "g-world", trigger="CLI", narrative="boom",
        reference_clock=30, client=client, lock_wait=5)
    assert out["status"] == "COMMITTED"
    st = out["state"]
    assert st.compiled_payload["schedule_type"] == "RECOVERY"
    # node order pinned: exactly one LLM call per LLM node (criterion 11).
    assert len(client.calls) == 3
    engine = make_engine()
    with engine.begin() as c:
        v = c.execute(text(
            "SELECT sv.parent_version_id, sv.schedule_type "
            "FROM schedule_versions sv JOIN instances i "
            "ON i.id = sv.instance_id WHERE i.name='g-world' "
            "ORDER BY sv.version_number DESC LIMIT 1")).one()
        run = c.execute(text(
            "SELECT status FROM recovery_runs ORDER BY id DESC LIMIT 1"
        )).one()
    assert v.schedule_type == "RECOVERY" and v.parent_version_id is not None
    assert run.status == "COMMITTED"


def test_translation_failed_records_run_no_versions(g_world):
    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine
    from sqlalchemy import text

    client = FakeLLMClient(["garbage", "garbage"])
    out = execute_recovery(
        "g-world", trigger="CLI", narrative="boom",
        reference_clock=30, client=client, lock_wait=5, max_retries=1)
    assert out["status"] == "TRANSLATION_FAILED"
    engine = make_engine()
    with engine.begin() as c:
        n_runs = c.execute(text(
            "SELECT count(*) FROM recovery_runs WHERE status="
            "'TRANSLATION_FAILED'")).scalar_one()
        n_versions = c.execute(text(
            "SELECT count(*) FROM schedule_versions")).scalar_one()
    assert n_runs == 1 and n_versions == 1      # baseline only, nothing new


def test_strategy_budget_cap_degrades_to_commit(g_world):
    from coe.agents.graph import execute_recovery

    client = FakeLLMClient([
        TRANSLATE_OK,
        '{"candidates": [], "final": false}',   # round 1
        '{"candidates": [], "final": false}',   # round 2
        '{"candidates": [], "final": false}',   # round 3 (=max)
        "Explanation.",
    ])
    out = execute_recovery(
        "g-world", trigger="CLI", narrative="boom",
        reference_clock=30, client=client, lock_wait=5)
    assert out["status"] == "COMMITTED"         # criterion 4: degrade not fail
    assert out["state"].round_count == 3


def test_mqtt_entry_skips_translate(g_world):
    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine
    from sqlalchemy import text

    record = {
        "kind": "MACHINE", "instance_id": "g-world", "machine_id": "M2",
        "event_type": "FAILURE", "occurred_at": 30, "severity": "HIGH",
        "estimated_downtime": 200, "narrative_excerpt": "edge boom"}
    client = FakeLLMClient(['{"candidates": [], "final": true}',
                            "Explained."])
    out = execute_recovery(
        "g-world", trigger="MQTT", record=record,
        source_message_id="edge-msg-1", reference_clock=30,
        client=client, lock_wait=5)
    assert out["status"] == "COMMITTED"
    assert len(client.calls) == 2               # ZERO translate calls
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='g-world' AND te.message_id='edge-msg-1'"
        )).scalar_one()
        runs = c.execute(text(
            "SELECT disruption_record_json->>'message_id' AS mid "
            "FROM recovery_runs")).scalars().all()
    assert n == 1
    assert "edge-msg-1" in runs                 # embedded for dedup (§3.4)


def test_lock_contention_aborts_loudly(g_world):
    from coe.agents.graph import execute_recovery
    from coe.agents.runs import InstanceRunLock, RunLockTimeout

    with InstanceRunLock("g-world", wait_seconds=10):
        with pytest.raises(RunLockTimeout):
            execute_recovery(
                "g-world", trigger="CLI", narrative="boom",
                reference_clock=30, client=FakeLLMClient([]),
                lock_wait=1)


def test_routers_are_pure_functions():
    """Back-edge routing decided without solving anything (§3.1)."""
    from coe.agents.graph import route_after_compile, route_after_solve
    from coe.agents.state import RecoveryState

    base = {"instance_name": "x"}
    reactive = RecoveryState(**base, material_reactive=True,
                             material_reactive_passes=0)
    spent = RecoveryState(**base, material_reactive=True,
                          material_reactive_passes=1)
    assert route_after_compile(reactive) == "strategy"     # back-edge 1
    assert route_after_compile(spent) == "solve_node"

    infeas = RecoveryState(**base, material_reactive=True,
                           material_reactive_passes=0,
                           solve_infeasible_material=True)
    done = RecoveryState(**base, solve_infeasible_material=False)
    assert route_after_solve(infeas) == "strategy"         # back-edge 2
    assert route_after_solve(done) == "END"


def test_terminal_status_labels_verifier_rollback():
    """Fix round 1: terminal labeling keys off verify ``passed`` (not
    rolled_back_from), so verify_commit's no-committed-version degenerate
    (passed=False, rolled_back_from=None) is VERIFIER_ROLLBACK, and the
    SOLVE_INFEASIBLE > GATE_FAILED > VERIFIER_ROLLBACK > COMMITTED order
    is pinned."""
    from coe.agents.graph import _terminal_status
    from coe.agents.state import RecoveryState

    base = {"instance_name": "x", "solution": {"status": "OPTIMAL"},
            "gate_result": {"passed": True}}
    committed = RecoveryState(
        **base,
        verify_result={"passed": True, "violations": [],
                       "version_number": 7, "rolled_back_from": 7})
    assert _terminal_status(committed) == "COMMITTED"
    no_version = RecoveryState(
        **base,
        verify_result={"passed": False,
                       "violations": ["no committed version found"],
                       "version_number": None, "rolled_back_from": None})
    assert _terminal_status(no_version) == "VERIFIER_ROLLBACK"
    violated = RecoveryState(
        **base,
        verify_result={"passed": False, "violations": ["op overlap"],
                       "version_number": 7, "rolled_back_from": 6})
    assert _terminal_status(violated) == "VERIFIER_ROLLBACK"
    assert _terminal_status(
        RecoveryState(**{**base, "gate_result": {"passed": False}},
                      verify_result=None)) == "GATE_FAILED"
    assert _terminal_status(
        RecoveryState(**{**base, "solution": {"status": "INFEASIBLE"}})
    ) == "SOLVE_INFEASIBLE"


def test_streaming_yields_nodes_then_outcome(g_world):
    from coe.agents.graph import execute_recovery_streaming

    client = FakeLLMClient([
        TRANSLATE_OK,                          # translate
        '{"candidates": [], "final": true}',   # strategy round
        "Rerouted J-A and J-B off M2.",        # explain
    ])
    gen = execute_recovery_streaming(
        "g-world", trigger="CLI", narrative="boom",
        reference_clock=30, client=client, lock_wait=5)
    nodes = []
    final = None
    for item in gen:
        if "node" in item:
            nodes.append(item["node"])
        else:
            final = item
    assert nodes, "no node boundaries streamed"
    assert nodes[0] == "entry" and nodes[-1] == "explain_node"
    assert final is not None and "run_id" in final
    # recording parity with execute_recovery:
    from sqlalchemy import text

    from coe.db.session import session_scope
    with session_scope() as s:
        n = s.execute(text("SELECT count(*) FROM recovery_runs"),
                      {}).scalar_one()
    assert n == 1


def test_streaming_accumulates_partial_chunk_updates(g_world, monkeypatch):
    """Fix round: each chunk merges onto the PREVIOUS final_state (not a
    reset from initial), so a langgraph-style partial dict update can never
    silently drop fields set by earlier chunks."""
    import coe.agents.graph as graph_mod
    from coe.agents.graph import execute_recovery_streaming

    class FakeApp:
        def stream(self, initial, stream_mode=None):
            assert stream_mode == "updates"
            yield {"a": {"round_count": 2}}
            yield {"b": {"narrative": "partial-only"}}   # PARTIAL update

    monkeypatch.setattr(graph_mod, "build_graph",
                        lambda *a, **k: FakeApp())
    items = list(execute_recovery_streaming(
        "g-world", trigger="CLI", narrative="boom", reference_clock=30,
        client=object(), lock_wait=5))
    final = items[-1]
    assert "status" in final and "run_id" in final
    st = final["state"]
    assert st.round_count == 2        # earlier chunk survived the merge...
    assert st.narrative == "partial-only"   # ...and the partial field applied


def test_streaming_translation_failed_records_run_no_versions(g_world):
    """Twin of test_translation_failed_records_run_no_versions for the
    streaming entry point: same fake-client mechanism (garbage LLM output,
    max_retries=1), exactly one TRANSLATION_FAILED run row, no new schedule
    versions. Proposals need no assertion here — translate fails before any
    strategy round exists, mirroring the sibling's scope."""
    from coe.agents.graph import execute_recovery_streaming
    from coe.db.session import make_engine
    from sqlalchemy import text

    client = FakeLLMClient(["garbage", "garbage"])
    gen = execute_recovery_streaming(
        "g-world", trigger="CLI", narrative="boom", reference_clock=30,
        client=client, lock_wait=5, max_retries=1)
    final = None
    for item in gen:
        if "node" not in item:
            final = item
    assert final is not None
    assert final["status"] == "TRANSLATION_FAILED"
    engine = make_engine()
    with engine.begin() as c:
        n_runs = c.execute(text(
            "SELECT count(*) FROM recovery_runs WHERE status="
            "'TRANSLATION_FAILED'")).scalar_one()
        n_versions = c.execute(text(
            "SELECT count(*) FROM schedule_versions")).scalar_one()
    assert n_runs == 1 and n_versions == 1      # baseline only, nothing new
