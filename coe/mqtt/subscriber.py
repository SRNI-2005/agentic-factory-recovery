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
    # Spec §6.5: topic must be factory/{instance}/machine/{machine}/events and
    # agree with the decoded payload before anything reaches the database.
    segments = msg.topic.split("/")
    if (
        len(segments) != 5
        or segments[0] != "factory"
        or segments[2] != "machine"
        or segments[4] != "events"
    ):
        print(f"[subscriber] REJECTED: malformed topic '{msg.topic}'")
        return
    topic_instance, topic_machine = segments[1], segments[3]
    if (
        topic_instance != payload.get("instance_id")
        or topic_machine != payload.get("machine_id")
    ):
        print(
            f"[subscriber] REJECTED: topic/payload mismatch on {msg.topic} "
            f"(payload instance={payload.get('instance_id')!r}, "
            f"machine={payload.get('machine_id')!r})"
        )
        return
    try:
        telemetry_id, created = ingest_telemetry_event(payload)
        status = "created" if created else "duplicate-suppressed"
        print(f"[subscriber] {status} telemetry id={telemetry_id}")
    except PayloadError as exc:
        # Unresolvable payloads cannot populate telemetry_events.machine_id;
        # they are logged loudly instead (documented limitation).
        print(f"[subscriber] REJECTED: {exc}")
    except Exception as exc:  # keep the network thread alive no matter what
        print(f"[subscriber] ERROR ingesting event: {exc!r}")


def run_subscriber() -> SubscriberHandle:
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = _on_message
    client.connect(s.mqtt_host, s.mqtt_port)
    client.subscribe(TOPIC_FILTER, qos=1)
    client.loop_start()
    return SubscriberHandle(client=client)
