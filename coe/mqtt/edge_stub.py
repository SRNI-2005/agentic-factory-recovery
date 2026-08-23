import json
import uuid

import paho.mqtt.client as mqtt

from coe.config import get_settings

_KIND_FIELD = {"MACHINE": "machine_id", "WORKER": "worker_id",
               "MATERIAL": "material_sku"}
_TOPIC_SEGMENT = {"MACHINE": "machine", "WORKER": "worker",
                  "MATERIAL": "material"}


def publish_resource_event(
    *,
    instance_name: str,
    resource_kind: str,
    resource_id: str,
    event_type: str,
    occurred_at: int = 512,
    severity: str | None = None,
    reason: str | None = None,
    duration: int | None = None,
    message_id: str | None = None,
) -> str:
    """Generic edge publisher for all three resource kinds (Amendment 2026-08-23)."""
    mid = message_id or f"evt-{uuid.uuid4().hex[:12]}"
    payload = {
        "message_id": mid,
        "instance_id": instance_name,
        "resource_kind": resource_kind,
        _KIND_FIELD[resource_kind]: resource_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
    }
    if severity is not None:
        payload["severity"] = severity
    if reason is not None:
        payload["reason"] = reason
    if duration is not None and resource_kind == "MACHINE":
        payload["estimated_downtime"] = duration
    if duration is not None and resource_kind == "WORKER" \
            and event_type == "WORKER_ABSENT":
        payload["estimated_absence"] = duration

    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(s.mqtt_host, s.mqtt_port)
    client.loop_start()  # without a network loop, wait_for_publish blocks forever
    topic = (f"factory/{instance_name}/"
             f"{_TOPIC_SEGMENT[resource_kind]}/{resource_id}/events")
    info = client.publish(topic, json.dumps(payload), qos=1)
    ok = info.wait_for_publish(timeout=10)
    client.loop_stop()
    client.disconnect()
    if ok is False or not info.is_published():
        raise RuntimeError(f"publish not acknowledged on {topic}")
    return mid


def publish_failure(
    instance_name: str,
    machine_name: str,
    *,
    occurred_at: int = 512,
    estimated_downtime: int | None = 90,
    severity: str = "HIGH",
    reason: str = "mechanical_failure",
    message_id: str | None = None,
) -> str:
    return publish_resource_event(
        instance_name=instance_name, resource_kind="MACHINE",
        resource_id=machine_name, event_type="FAILURE",
        occurred_at=occurred_at, severity=severity, reason=reason,
        duration=estimated_downtime, message_id=message_id,
    )
