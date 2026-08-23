import json
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from coe.config import get_settings
from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

TOPIC_FILTERS = (
    "factory/+/machine/+/events",
    "factory/+/worker/+/events",
    "factory/+/material/+/events",
)

_RESOURCE_FIELDS = {
    "machine": "machine_id",
    "worker": "worker_id",
    "material": "material_sku",
}


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

    # Topic contract (Amendment 2026-08-23):
    # factory/{instance}/{machine|worker|material}/{resource_id}/events —
    # and it must agree with the decoded payload before anything reaches the DB.
    segments = msg.topic.split("/")
    if (
        len(segments) != 5
        or segments[0] != "factory"
        or segments[4] != "events"
        or segments[2] not in _RESOURCE_FIELDS
    ):
        print(f"[subscriber] REJECTED: malformed topic '{msg.topic}'")
        return

    if not isinstance(payload, dict):
        print(f"[subscriber] REJECTED: non-object payload on {msg.topic}")
        return

    field = _RESOURCE_FIELDS[segments[2]]
    if (
        segments[1] != payload.get("instance_id")
        or segments[3] != payload.get(field)
    ):
        print(
            f"[subscriber] REJECTED: topic/payload mismatch on {msg.topic} "
            f"(payload instance={payload.get('instance_id')!r}, "
            f"{field}={payload.get(field)!r})"
        )
        return

    try:
        telemetry_id, created = ingest_telemetry_event(payload)
        status = "created" if created else "duplicate-suppressed"
        print(f"[subscriber] {status} telemetry id={telemetry_id}")
    except PayloadError as exc:
        # Unresolvable payloads cannot populate telemetry FK columns;
        # they are logged loudly instead (documented limitation).
        print(f"[subscriber] REJECTED: {exc}")
    except Exception as exc:  # keep the network thread alive no matter what
        print(f"[subscriber] ERROR ingesting event: {exc!r}")


def run_subscriber() -> SubscriberHandle:
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = _on_message
    client.connect(s.mqtt_host, s.mqtt_port)
    for topic in TOPIC_FILTERS:
        client.subscribe(topic, qos=1)
    client.loop_start()
    return SubscriberHandle(client=client)
