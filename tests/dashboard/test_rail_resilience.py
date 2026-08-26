"""Tests for Important #3 and #4: rail reconnect resilience + MQTT bytes.

rail.py's _loop must survive unexpected exceptions in on_message or
connect without dying.  Payload bytes (invalid UTF-8 / JSON) must be
handled gracefully.
"""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest


class TestRailResilience:

    def test_on_message_survives_invalid_utf8(self):
        """Invalid UTF-8 bytes in msg.payload must not crash on_message."""
        import coe.dashboard.rail as rail_mod

        msg = MagicMock()
        msg.topic = "factory/i1/machine/M1/events"
        msg.payload = b'\xff\xfe\xfd'
        rail_mod._on_message(None, None, msg)

    def test_on_message_survives_invalid_json(self):
        """Non-JSON payload must be silently dropped."""
        import coe.dashboard.rail as rail_mod

        msg = MagicMock()
        msg.topic = "factory/i1/machine/M1/events"
        msg.payload = b'not-json-at-all'
        rail_mod._on_message(None, None, msg)

    def test_on_message_survives_none_payload(self):
        """None payload must not crash on_message."""
        import coe.dashboard.rail as rail_mod

        msg = MagicMock()
        msg.topic = "factory/i1/machine/M1/events"
        msg.payload = None
        rail_mod._on_message(None, None, msg)

    def test_loop_restarts_after_connect_error(self, monkeypatch):
        """_loop must restart after a non-OSError exception in the body."""
        import coe.dashboard.rail as rail_mod
        import time

        call_count = 0

        def fake_connect(host, port):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError("transient boom")

        def fake_loop_forever():
            nonlocal call_count
            if call_count <= 1:
                return
            raise OSError("done")

        mock_client = MagicMock()
        mock_client.connect.side_effect = fake_connect
        mock_client.loop_forever.side_effect = fake_loop_forever

        with patch("paho.mqtt.client.Client", return_value=mock_client):
            # Patch sleep to avoid real delay
            with patch("time.sleep"):
                # Run _loop in a thread that we'll stop
                stop = threading.Event()
                original_forever = mock_client.loop_forever

                def stopping_forever():
                    if call_count > 1:
                        stop.set()
                        raise OSError("done")

                mock_client.loop_forever.side_effect = stopping_forever
                t = threading.Thread(target=rail_mod._loop,
                                     args=("localhost", 1883,
                                           "factory/+/+/+/events"),
                                     daemon=True)
                t.start()
                stop.wait(timeout=2)
                # Should have restarted: connect called at least twice
                assert mock_client.connect.call_count >= 2

    def test_on_message_appends_valid_event(self):
        """Valid JSON payload must be appended to the buffer."""
        import coe.dashboard.rail as rail_mod

        rail_mod._BUF.clear()
        msg = MagicMock()
        msg.topic = "factory/i1/machine/M1/events"
        msg.payload = json.dumps({
            "event_type": "FAILURE",
            "machine_id": "M1",
            "occurred_at": 100
        }).encode()
        rail_mod._on_message(None, None, msg)
        buf = rail_mod.snapshot()
        assert len(buf) == 1
        assert buf[0]["event_type"] == "FAILURE"
        assert buf[0]["resource"] == "M1"
