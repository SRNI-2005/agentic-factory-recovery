import time

import pytest

pytestmark = [pytest.mark.db, pytest.mark.mqtt]


def test_edge_to_db_roundtrip(demo_scenario):
    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_failure
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        mid = publish_failure("factory_demo_01", "M5", occurred_at=777)
        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 5
        stored = None
        while time.time() < deadline and stored is None:
            with eng.begin() as c:
                stored = c.execute(
                    text(
                        "SELECT te.processed_at FROM telemetry_events te "
                        "JOIN instances i ON i.id = te.instance_id "
                        "WHERE te.message_id = :m"
                    ),
                    {"m": mid},
                ).scalar_one_or_none()
            if stored is None:
                time.sleep(0.2)
        assert stored == 777
    finally:
        handle.stop()
