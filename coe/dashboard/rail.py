"""Passive live MQTT events rail for the Streamlit sidebar (brief C17).

Does NOT ingest — coe.mqtt.subscriber owns that.  This module mirrors wire
traffic into a bounded deque for display only.
"""
import json
import threading
from collections import deque

_BUF: deque[dict] = deque(maxlen=50)
_LOCK = threading.Lock()
_STARTED = False


def snapshot() -> list[dict]:
    """Return a safe copy of the buffered events."""
    with _LOCK:
        return list(_BUF)


def _on_message(client, userdata, msg):  # noqa: ANN001
    try:
        evt = json.loads(msg.payload)
    except (json.JSONDecodeError, TypeError):
        return
    entry = {
        "topic": msg.topic,
        "event_type": evt.get("event_type"),
        "resource": (
            evt.get("machine_id")
            or evt.get("worker_id")
            or evt.get("material_sku")
        ),
        "occurred_at": evt.get("occurred_at"),
    }
    with _LOCK:
        _BUF.append(entry)


def start_rail() -> None:
    """Idempotent: launch one daemon reconnecting thread."""
    global _STARTED  # noqa: PLW0603
    with _LOCK:
        if _STARTED:
            return
        from coe.config import get_settings

        s = get_settings()
        topic = "factory/+/+/+/events"
        threading.Thread(
            target=_loop,
            args=(s.mqtt_host, s.mqtt_port, topic),
            daemon=True,
            name="mqtt-rail",
        ).start()
        _STARTED = True


def _loop(host: str, port: int, topic: str) -> None:
    import time

    import paho.mqtt.client as mqtt

    while True:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client.on_message = _on_message
            client.connect(host, port)
            client.subscribe(topic)
            client.loop_forever()
        except OSError:
            time.sleep(2)


def render_rail() -> None:
    """Show the latest 10 events in the sidebar."""
    import streamlit as st

    events = snapshot()[-10:]
    if not events:
        return
    with st.sidebar:
        st.markdown("---")
        st.caption("Live events")
        for e in reversed(events):
            rid = e.get("resource") or "?"
            etype = e.get("event_type") or "?"
            st.text(f"{etype} · {rid}")
