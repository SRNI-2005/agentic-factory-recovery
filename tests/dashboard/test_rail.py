"""Unit tests for the passive live MQTT events rail (brief C17)."""
import json
import threading
import types

import pytest

from coe.dashboard import rail


@pytest.fixture(autouse=True)
def _reset_rail():
    """Reset module state between tests."""
    rail._BUF.clear()
    rail._STARTED = False
    yield
    rail._BUF.clear()
    rail._STARTED = False


def _make_msg(topic: str, payload: dict | str) -> types.SimpleNamespace:
    if isinstance(payload, dict):
        payload = json.dumps(payload)
    return types.SimpleNamespace(topic=topic, payload=payload)


class TestOnMessage:
    def test_valid_machine_event(self):
        msg = _make_msg(
            "factory/site1/factory_demo_01/m1/events",
            {"event_type": "MACHINE_FAILURE", "machine_id": "M1",
             "occurred_at": 100},
        )
        rail._on_message(None, None, msg)
        snap = rail.snapshot()
        assert len(snap) == 1
        assert snap[0]["event_type"] == "MACHINE_FAILURE"
        assert snap[0]["resource"] == "M1"
        assert snap[0]["occurred_at"] == 100
        assert snap[0]["topic"] == msg.topic

    def test_valid_worker_event(self):
        msg = _make_msg(
            "factory/site1/factory_demo_01/w3/events",
            {"event_type": "WORKER_ABSENCE", "worker_id": "W3",
             "occurred_at": 200},
        )
        rail._on_message(None, None, msg)
        assert rail.snapshot()[0]["resource"] == "W3"

    def test_valid_material_event(self):
        msg = _make_msg(
            "factory/site1/factory_demo_01/mat1/events",
            {"event_type": "MATERIAL_SHORTFALL", "material_sku": "MAT-001",
             "occurred_at": 300},
        )
        rail._on_message(None, None, msg)
        assert rail.snapshot()[0]["resource"] == "MAT-001"

    def test_invalid_json_ignored(self):
        msg = _make_msg("factory/x/y/z/events", "not-json {{{")
        rail._on_message(None, None, msg)
        assert rail.snapshot() == []

    def test_none_payload_ignored(self):
        msg = types.SimpleNamespace(topic="factory/x/y/z/events",
                                   payload=None)
        rail._on_message(None, None, msg)
        assert rail.snapshot() == []


class TestCap:
    def test_buffer_capped_at_50(self):
        for i in range(60):
            msg = _make_msg(
                "factory/x/y/z/events",
                {"event_type": "TEST", "machine_id": f"M{i}",
                 "occurred_at": i},
            )
            rail._on_message(None, None, msg)
        assert len(rail.snapshot()) == 50
        assert rail.snapshot()[0]["occurred_at"] == 10


class TestSnapshot:
    def test_returns_copy(self):
        rail._BUF.append({"topic": "t", "event_type": "E",
                          "resource": "R", "occurred_at": 0})
        s1 = rail.snapshot()
        s2 = rail.snapshot()
        assert s1 == s2
        assert s1 is not s2

    def test_mutation_does_not_affect_buffer(self):
        rail._BUF.append({"topic": "t", "event_type": "E",
                          "resource": "R", "occurred_at": 0})
        s = rail.snapshot()
        s.clear()
        assert len(rail.snapshot()) == 1


class TestStartRail:
    def test_idempotent(self):
        started = []

        def fake_thread(*a, **kw):
            started.append(True)
            return types.SimpleNamespace(start=lambda: None, daemon=True)

        import coe.dashboard.rail as mod
        original_thread = threading.Thread
        threading.Thread = fake_thread
        try:
            mod.start_rail()
            mod.start_rail()
        finally:
            threading.Thread = original_thread
        assert mod._STARTED is True
