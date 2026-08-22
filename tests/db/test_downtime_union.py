import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


@pytest.fixture()
def demo(demo_scenario):
    return demo_scenario


def _payload(inst="factory_demo_01", machine="M3", **over):
    base = {
        "message_id": "evt-x",
        "instance_id": inst,
        "machine_id": machine,
        "event_type": "FAILURE",
        "occurred_at": 100,
        "estimated_downtime": 50,
    }
    base.update(over)
    return base


def _windows(url, inst="factory_demo_01"):
    from coe.config import get_settings

    with create_engine(get_settings().database_url).begin() as c:
        return c.execute(
            text(
                """
                SELECT downtime_from, downtime_until, reason FROM machine_downtime_windows w
                JOIN instances i ON i.id = w.instance_id
                WHERE i.name = :i AND w.machine_id = (
                    SELECT id FROM machines WHERE instance_id = i.id AND name = 'M3')
                ORDER BY downtime_from
                """
            ),
            {"i": inst},
        ).all()


def test_touching_intervals_union(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    ingest_telemetry_event(_payload(message_id="a"))          # [100,150)
    ingest_telemetry_event(_payload(message_id="b", occurred_at=150))  # touches
    rows = _windows(None)
    assert rows == [(100, 200, "FAILURE")]


def test_disjoint_intervals_stay_separate(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    ingest_telemetry_event(_payload(message_id="c"))
    ingest_telemetry_event(_payload(message_id="d", occurred_at=500, estimated_downtime=10))
    rows = _windows(None)
    assert len(rows) == 2


def test_duplicate_message_suppressed(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    t1, c1 = ingest_telemetry_event(_payload(message_id="dup"))
    t2, c2 = ingest_telemetry_event(_payload(message_id="dup"))
    assert c1 is True and c2 is False and t1 == t2
    assert len(_windows(None)) == 1


def test_machine_status_flipped_and_restorable_by_union(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    ingest_telemetry_event(_payload(message_id="s1"))
    from coe.config import get_settings

    with create_engine(get_settings().database_url).begin() as c:
        st = c.execute(
            text(
                "SELECT m.status FROM machines m JOIN instances i ON i.id=m.instance_id "
                "WHERE i.name='factory_demo_01' AND m.name='M3'"
            )
        ).scalar_one()
    assert st == "FAILED"


def test_negative_occurred_at_rejected_payload_level(demo):
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    try:
        ingest_telemetry_event(_payload(message_id="neg", occurred_at=-1))
        raised = False
    except PayloadError:
        raised = True
    assert raised
