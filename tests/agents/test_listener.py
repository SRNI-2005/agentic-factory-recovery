# tests/agents/test_listener.py
"""§3.4 listener guarantees with a fake transport."""
import pytest

pytestmark = pytest.mark.db


class _Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


@pytest.fixture()
def g_world(clean_db):
    from tests.agents.worlds import build_g_world

    build_g_world(clean_db)


def _failure_payload(mid="m-1"):
    import json

    return json.dumps({
        "message_id": mid, "instance_id": "g-world",
        "resource_kind": "MACHINE", "machine_id": "M2",
        "event_type": "FAILURE", "occurred_at": 30, "severity": "HIGH",
        "estimated_downtime": 200, "reason": "edge boom"})


def _recording_runner(launched):
    """Runner double for threaded dispatch: signals completion and exposes
    its worker thread so tests can join it (daemon threads would otherwise
    race clean_db wipes across tests)."""
    import threading

    done = threading.Event()
    box = {}

    def runner(**kw):
        box["thread"] = threading.current_thread()
        launched.append(kw)
        done.set()

    return runner, box, done


def test_failure_launches_exactly_one_run(g_world):
    from coe.agents import listener
    from coe.db.session import make_engine
    from sqlalchemy import text

    launched = []
    runner, box, done = _recording_runner(launched)

    msg = _Msg("factory/g-world/machine/M2/events",
               _failure_payload().encode())
    listener.handle_event(msg, runner=runner)
    assert done.wait(timeout=15)
    box["thread"].join(timeout=15)
    assert len(launched) == 1
    call = launched[0]
    assert call["trigger"] == "MQTT"
    assert call["source_message_id"] == "m-1"
    assert call["reference_clock"] == 30
    assert call["record"]["machine_id"] == "M2"
    # telemetry written through the shared ingestion path exactly once
    with make_engine().begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='g-world' AND te.message_id='m-1'")).scalar_one()
    assert n == 1

    # redelivery of the same message_id: no second launch
    listener.handle_event(msg, runner=runner)
    assert len(launched) == 1


def test_return_event_ingests_but_never_launches(g_world):
    import json

    from coe.agents import listener

    launched = []
    msg = _Msg("factory/g-world/worker/W1/events", json.dumps({
        "message_id": "ret-1", "instance_id": "g-world",
        "resource_kind": "WORKER", "worker_id": "W1",
        "event_type": "WORKER_RETURN", "occurred_at": 40,
        "severity": "LOW"}).encode())
    listener.handle_event(msg, runner=lambda **kw: launched.append(kw))
    assert launched == []          # non-disruption event (criterion 14 scope)


def test_malformed_payload_no_run(g_world):
    from coe.agents import listener

    launched = []
    bad = _Msg("factory/g-world/machine/M2/events", b"{not json")
    listener.handle_event(bad, runner=lambda **kw: launched.append(kw))
    mismatch = _Msg("factory/g-world/machine/M9/events",
                    _failure_payload("m-2").encode())
    listener.handle_event(mismatch, runner=lambda **kw: launched.append(kw))
    assert launched == []


def test_lock_waits_serialize_cascades(g_world):
    """Second trigger during a held lock waits, then runs (no drop)."""
    import threading
    import time

    from coe.agents import listener
    from coe.agents.runs import InstanceRunLock

    started = []
    box = {}
    done = threading.Event()

    def runner(**kw):
        # handle_event dispatches us on a worker daemon thread; record it
        # so the test can join instead of leaking it into later tests.
        box["thread"] = threading.current_thread()
        with InstanceRunLock(kw["instance_name"], wait_seconds=5):
            started.append(time.monotonic())
        done.set()

    with InstanceRunLock("g-world", wait_seconds=5):
        t = threading.Thread(
            target=listener.handle_event,
            args=(_Msg("factory/g-world/machine/M2/events",
                       _failure_payload("cascade-1").encode()),),
            kwargs={"runner": runner})
        t.start()
        time.sleep(0.8)          # hold the lock while handler blocks
    t.join(timeout=15)           # handler returns; work continues off-thread
    assert done.wait(timeout=15)  # serialized through, never dropped
    box["thread"].join(timeout=15)
    assert len(started) >= 1
