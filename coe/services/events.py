"""Passive MQTT observer feeding the live events rail (dashboard §4).

Deliberately does NOT ingest: coe.mqtt.subscriber owns ingestion. This
client only mirrors wire traffic into a replayable module-level EventLog.
"""
import json
import threading

from coe.services.recovery import EventLog

_RAIL = EventLog()
_START_LOCK = threading.Lock()
_STARTED = False


def rail_log() -> EventLog:
    return _RAIL


def _on_message(client, userdata, msg):
    try:
        evt = json.loads(msg.payload)
    except json.JSONDecodeError:
        return
    _RAIL.append({"topic": msg.topic, "event_type":
                  evt.get("event_type"), "resource":
                  (evt.get("machine_id") or evt.get("worker_id")
                   or evt.get("material_sku")),
                  "occurred_at": evt.get("occurred_at")})


def start_mirror() -> None:
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        from coe.config import get_settings

        s = get_settings()
        topic = "factory/+/+/+/events"
        threading.Thread(target=_loop, args=(s.mqtt_host, s.mqtt_port,
                                             topic), daemon=True,
                         name="mqtt-mirror").start()
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
            time.sleep(2)   # broker down: retry quietly
