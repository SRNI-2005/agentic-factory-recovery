import json
import uuid

import paho.mqtt.client as mqtt

from coe.config import get_settings


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
    mid = message_id or f"evt-{uuid.uuid4().hex[:12]}"
    payload = {
        "message_id": mid,
        "instance_id": instance_name,
        "machine_id": machine_name,
        "event_type": "FAILURE",
        "occurred_at": occurred_at,
        "severity": severity,
        "estimated_downtime": estimated_downtime,
        "reason": reason,
    }
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(s.mqtt_host, s.mqtt_port)
    client.loop_start()  # plan bugfix: without a network loop, wait_for_publish blocks forever
    topic = f"factory/{instance_name}/machine/{machine_name}/events"
    info = client.publish(topic, json.dumps(payload), qos=1)
    info.wait_for_publish(timeout=10)
    client.loop_stop()
    client.disconnect()
    return mid
