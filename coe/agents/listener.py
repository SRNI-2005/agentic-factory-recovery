# coe/agents/listener.py
"""MQTT listener (spec §3.4): validated edge events -> recovery runs.

Every valid resource event is ingested through the shared Phase 1 path
(idempotent on message_id). Only disruption-class events additionally
launch runs; duplicates are suppressed by checking whether this
message_id already produced one. Malformed payloads are rejected loudly
with no run (parity with the Phase 1 subscriber limitation).
"""
import json
import threading

import paho.mqtt.client as mqtt

from coe.config import get_settings
from coe.db.session import make_engine
from sqlalchemy import text

RUN_TRIGGERING_EVENTS = {
    "MACHINE": {"FAILURE", "MAINTENANCE"},
    "WORKER": {"WORKER_ABSENT"},
    "MATERIAL": {"MATERIAL_SHORTAGE"},
}

_RESOURCE_FIELD = {"machine": "machine_id", "worker": "worker_id",
                   "material": "material_sku"}
_KIND_NAME = {"machine": "MACHINE", "worker": "WORKER",
              "material": "MATERIAL"}


def already_launched(message_id: str) -> bool:
    with make_engine().begin() as conn:
        row = conn.execute(text(
            "SELECT 1 FROM recovery_runs "
            "WHERE disruption_record_json->>'message_id' = :mid LIMIT 1"),
            {"mid": message_id}).first()
        return row is not None


def _validate_topic(topic: str, payload: dict) -> tuple[str, str] | None:
    """Returns (kind_segment, resource_ref) or None when malformed."""
    segments = topic.split("/")
    if (len(segments) != 5 or segments[0] != "factory"
            or segments[4] != "events" or segments[2] not in _RESOURCE_FIELD):
        return None
    field = _RESOURCE_FIELD[segments[2]]
    if (segments[1] != payload.get("instance_id")
            or segments[3] != payload.get(field)):
        return None
    return segments[2], segments[3]


def handle_event(msg, *, runner) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"[listener] undecodable payload on {msg.topic}")
        return
    if not isinstance(payload, dict):
        print(f"[listener] non-object payload on {msg.topic}")
        return
    check = _validate_topic(msg.topic, payload)
    if check is None:
        print(f"[listener] REJECTED malformed/mismatched topic {msg.topic}")
        return
    kind_seg, _ref = check
    kind = _KIND_NAME[kind_seg]

    # Always ingest first — Phase 1 semantics, idempotent on message_id.
    # (Triggering events are ingested AGAIN by the graph's ingest node;
    # suppression makes the second call a no-op.)
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    try:
        _tid, created = ingest_telemetry_event(payload)
    except PayloadError as exc:
        print(f"[listener] REJECTED payload: {exc} — no run starts")
        return

    wire_kind = payload.get("resource_kind") or (
        "MACHINE" if kind_seg == "machine" else None)
    if wire_kind != kind or payload.get("event_type") \
            not in RUN_TRIGGERING_EVENTS.get(kind, frozenset()):
        return                      # valid telemetry; never a run trigger

    mid = payload["message_id"]
    if not created or already_launched(mid):
        print(f"[listener] message {mid} already processed; skipping")
        return

    from coe.agents.records import parse_disruption_record

    excerpt = payload.get("reason") or ""
    base = {
        "kind": kind,
        "instance_id": payload["instance_id"],
        "event_type": payload["event_type"],
        "occurred_at": payload["occurred_at"],
        "severity": payload.get("severity") or "LOW",
        "narrative_excerpt": excerpt,
    }
    if kind == "MACHINE":
        base["machine_id"] = payload["machine_id"]
        if payload.get("estimated_downtime") is not None:
            base["estimated_downtime"] = payload["estimated_downtime"]
    elif kind == "WORKER":
        base["worker_id"] = payload["worker_id"]
        if payload.get("estimated_absence") is not None:
            base["estimated_absence"] = payload["estimated_absence"]
    else:
        base["material_sku"] = payload["material_sku"]

    try:
        record = parse_disruption_record(base).model_dump()
    except Exception as exc:
        print(f"[listener] record invalid for {mid}: {exc} — no run starts")
        return

    print(f"[listener] launching recovery for {mid} ({kind})")
    # Dispatch off paho's network thread: 180s recoveries must never block
    # loop_forever past keepalive. Serialization is guaranteed by the
    # InstanceRunLock inside execute_recovery, not by call synchrony.
    threading.Thread(
        target=runner, daemon=True,
        kwargs=dict(instance_name=payload["instance_id"], trigger="MQTT",
                    record=record, source_message_id=mid,
                    reference_clock=payload["occurred_at"])).start()


def run_listener(runner=None) -> None:
    if runner is None:
        from functools import partial

        from coe.agents.graph import execute_recovery

        runner = partial(execute_recovery)
    s = get_settings()
    # clean_session=False: unacked QoS1 messages survive reconnects instead
    # of being discarded by the broker on session reset.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         clean_session=False)

    def _on_message(_c, _u, msg):
        try:
            handle_event(msg, runner=runner)
        except Exception as exc:      # keep the network thread alive
            print(f"[listener] ERROR handling event: {exc!r}")

    client.on_message = _on_message
    client.connect(s.mqtt_host, s.mqtt_port)
    from coe.mqtt.subscriber import TOPIC_FILTERS

    for topic in TOPIC_FILTERS:
        client.subscribe(topic, qos=1)
    print("[listener] subscribed; waiting for disruptions")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()
