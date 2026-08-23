import time

import pytest

pytestmark = [pytest.mark.db, pytest.mark.mqtt]


def test_worker_absence_roundtrip(demo_scenario):
    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_resource_event
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        mid = publish_resource_event(
            instance_name="factory_demo_01", resource_kind="WORKER",
            resource_id="W6", event_type="WORKER_ABSENT", occurred_at=800)
        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 5
        ok = False
        while time.time() < deadline and not ok:
            with eng.begin() as c:
                n = c.execute(text(
                    "SELECT count(*) FROM worker_absence_windows w "
                    "JOIN instances i ON i.id=w.instance_id "
                    "WHERE i.name='factory_demo_01' AND w.absence_from <= 800 "
                    "AND (w.absence_until IS NULL OR w.absence_until > 800)"
                )).scalar_one()
                st = c.execute(text(
                    "SELECT status FROM workers w JOIN instances i ON i.id=w.instance_id "
                    "WHERE i.name='factory_demo_01' AND w.name='W6'"
                )).scalar_one()
            ok = n >= 1 and st == "UNAVAILABLE"
            if not ok:
                time.sleep(0.2)
        assert ok
    finally:
        handle.stop()


def test_material_shortage_roundtrip(demo_scenario):
    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_resource_event
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        mid = publish_resource_event(
            instance_name="factory_demo_01", resource_kind="MATERIAL",
            resource_id="MAT-002", event_type="MATERIAL_SHORTAGE", occurred_at=810)
        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 5
        stored = False
        while time.time() < deadline and not stored:
            with eng.begin() as c:
                n = c.execute(text(
                    "SELECT count(*) FROM telemetry_events te "
                    "JOIN instances i ON i.id=te.instance_id "
                    "WHERE i.name='factory_demo_01' AND te.message_id=:m"
                ), {"m": mid}).scalar_one()
            stored = n == 1
            if not stored:
                time.sleep(0.2)
        assert stored
    finally:
        handle.stop()


def test_worker_topic_mismatch_rejected(demo_scenario):
    """Valid worker payload on the WRONG worker's topic must be rejected."""
    import json as _json

    import paho.mqtt.client as mqtt

    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_resource_event
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        # control: matching topic lands exactly once
        publish_resource_event(
            instance_name="factory_demo_01", resource_kind="WORKER",
            resource_id="W7", event_type="WORKER_ABSENT", occurred_at=820,
            message_id="evt-mr-ok")

        # mismatch: W7's payload published on W8's topic
        s = get_settings()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(s.mqtt_host, s.mqtt_port)
        bad_payload = {
            "message_id": "evt-mr-bad", "instance_id": "factory_demo_01",
            "resource_kind": "WORKER", "worker_id": "W7",
            "event_type": "WORKER_ABSENT", "occurred_at": 820,
        }
        client.publish("factory/factory_demo_01/worker/W8/events",
                       _json.dumps(bad_payload), qos=1).wait_for_publish(timeout=10)
        client.disconnect()

        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 3
        landed = 0
        while time.time() < deadline:
            with eng.begin() as c:
                landed = c.execute(text(
                    "SELECT count(*) FROM telemetry_events te "
                    "JOIN instances i ON i.id=te.instance_id "
                    "WHERE i.name='factory_demo_01' AND te.message_id IN "
                    "('evt-mr-ok','evt-mr-bad')")).scalar_one()
            if landed >= 1:
                break
            time.sleep(0.2)
        with eng.begin() as c:
            rejected = c.execute(text(
                "SELECT count(*) FROM telemetry_events te "
                "JOIN instances i ON i.id=te.instance_id "
                "WHERE i.name='factory_demo_01' AND te.message_id='evt-mr-bad'"
            )).scalar_one()
        assert rejected == 0  # mismatched delivery never ingested
        assert landed >= 1    # control delivery did land
    finally:
        handle.stop()
