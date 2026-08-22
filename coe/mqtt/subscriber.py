import json
import threading
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from coe.config import get_settings
from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

TOPIC_FILTER = "factory/+/machine/+/events"


@dataclass
class SubscriberHandle:
    client: mqtt.Client

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def _on_message(client, userdata, msg) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"[subscriber] undecodable payload on {msg.topic}")
        return
    try:
        telemetry_id, created = ingest_telemetry_event(payload)
        status = "created" if created else "duplicate-suppressed"
        print(f"[subscriber] {status} telemetry id={telemetry_id}")
    except PayloadError as exc:
        # Unresolvable payloads cannot populate telemetry_events.machine_id;
        # they are logged loudly instead (documented limitation).
        print(f"[subscriber] REJECTED: {exc}")


def run_subscriber() -> SubscriberHandle:
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = _on_message
    client.connect(s.mqtt_host, s.mqtt_port)
    client.subscribe(TOPIC_FILTER, qos=1)
    client.loop_start()
    return SubscriberHandle(client=client)
