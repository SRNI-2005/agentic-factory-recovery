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
