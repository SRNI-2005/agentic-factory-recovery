# tests/agents/test_translate_node.py
"""§4.1 translate node: retries, fallback, write-through, idempotency."""
import json

import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient

NARRATIVE = "MC-04 gearbox seized, sparks everywhere"
GOOD_MACHINE = {
    "kind": "MACHINE", "instance_id": "factory_demo_01",
    "machine_id": "M3", "event_type": "FAILURE", "occurred_at": 512,
    "severity": "HIGH", "estimated_downtime": 90,
    "narrative_excerpt": NARRATIVE,
}


@pytest.fixture()
def demo(demo_scenario):
    return demo_scenario


def test_prompt_contains_clock_and_instance(demo):
    from coe.agents.nodes.translate import build_translate_messages

    system, user = build_translate_messages(NARRATIVE, "factory_demo_01", 512)
    assert "512" in user and "factory_demo_01" in user
    assert "JSON" in system


def test_translate_validates_without_writing(demo):
    from coe.agents.nodes.translate import run_translate
    from coe.agents.state import RecoveryState
    from coe.db.session import make_engine
    from sqlalchemy import text

    state = RecoveryState(instance_name="factory_demo_01",
                          reference_clock=30, narrative=NARRATIVE)
    out = run_translate(state, client=FakeLLMClient([json.dumps(GOOD_MACHINE)]))
    assert out.disruption_record["kind"] == "MACHINE"
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id = te.instance_id "
            "WHERE i.name='factory_demo_01' AND te.resource_kind='MACHINE'"
        )).scalar_one()
    assert n == 0      # translate is pure validation; ingest writes (§3.1)


def test_ingest_node_writes_cli_hashed_event_idempotently(demo):
    from coe.agents.nodes.translate import run_ingest, run_translate
    from coe.agents.state import RecoveryState
    from coe.db.session import make_engine
    from sqlalchemy import text

    base = RecoveryState(instance_name="factory_demo_01",
                         reference_clock=30, narrative=NARRATIVE)
    st = run_translate(base,
                       client=FakeLLMClient([json.dumps(GOOD_MACHINE)]))
    run_ingest(st)
    run_ingest(st)     # duplicate delivery: suppressed by message_id
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id = te.instance_id "
            "WHERE i.name='factory_demo_01' AND te.message_id LIKE 'cli-%'"
        )).scalar_one()
    assert n == 1          # criterion 13: no duplicate telemetry event


def test_invalid_then_valid_retries_with_feedback(demo):
    from coe.agents.nodes.translate import run_translate
    from coe.agents.state import RecoveryState

    bad = dict(GOOD_MACHINE, occurred_at=-5)
    client = FakeLLMClient([json.dumps(bad), json.dumps(GOOD_MACHINE)])
    out = run_translate(RecoveryState(instance_name="factory_demo_01",
                                      reference_clock=30,
                                      narrative=NARRATIVE), client=client,
                        max_retries=2)
    assert out.disruption_record["occurred_at"] == 512
    # second call happened => retry consumed exactly one extra response
    assert len(client.calls) == 2


def test_exhaustion_raises_translation_failed_no_db_mutation(demo):
    from coe.agents.nodes.translate import TranslationFailed, run_translate
    from coe.agents.state import RecoveryState
    from coe.db.session import make_engine
    from sqlalchemy import text

    bad = dict(GOOD_MACHINE, event_type="EXPLODED")
    client = FakeLLMClient([json.dumps(bad), json.dumps(bad),
                            json.dumps(bad)])   # 1 + max_retries(2)
    with pytest.raises(TranslationFailed):
        run_translate(RecoveryState(instance_name="factory_demo_01",
                                    reference_clock=30, narrative=NARRATIVE),
                      client=client,
                      max_retries=2)
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id = te.instance_id "
            "WHERE i.name='factory_demo_01'")).scalar_one()
    assert n == 0      # zero DB mutation (criterion 2)


def test_multi_disruption_refusal_is_retryable(demo):
    from coe.agents.nodes.translate import run_translate
    from coe.agents.state import RecoveryState

    two = [GOOD_MACHINE, dict(GOOD_MACHINE, worker_id="W3")]
    client = FakeLLMClient([json.dumps(two), json.dumps(GOOD_MACHINE)])
    out = run_translate(RecoveryState(instance_name="factory_demo_01",
                                      reference_clock=30,
                                      narrative=NARRATIVE), client=client,
                        max_retries=2)
    assert out.disruption_record["kind"] == "MACHINE"


def test_cli_message_id_stable():
    from coe.agents.nodes.translate import cli_message_id

    a = cli_message_id(GOOD_MACHINE)
    b = cli_message_id(dict(GOOD_MACHINE))     # copy, same content
    assert a == b and a.startswith("cli-")
    assert a != cli_message_id(dict(GOOD_MACHINE, occurred_at=600))
