import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def _publish(kind_payload: dict):
    from coe.mqtt.ingest import ingest_telemetry_event

    return ingest_telemetry_event({"instance_id": "factory_demo_01",
                                   "occurred_at": 500,
                                   "message_id": "fixed-id-see-test", **kind_payload})


def _one(sql, params):
    from coe.config import get_settings

    with create_engine(get_settings().database_url).begin() as c:
        return c.execute(text(sql), params).scalar_one()


def test_worker_absent_creates_window_and_status(demo_scenario):
    tid, created = _publish({
        "message_id": "mr-w1", "resource_kind": "WORKER", "worker_id": "W3",
        "event_type": "WORKER_ABSENT", "severity": "MEDIUM",
    })
    assert created is True
    assert _one(
        "SELECT count(*) FROM worker_absence_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.worker_id = ("
        "  SELECT id FROM workers WHERE instance_id=i.id AND name='W3') "
        "AND w.absence_from <= 500 "
        "AND (w.absence_until IS NULL OR w.absence_until > 500)",
        {}) == 1
    assert _one(
        "SELECT status FROM workers w JOIN instances i ON i.id=w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.name='W3'", {}) == "UNAVAILABLE"


def test_worker_return_closes_open_absence(demo_scenario):
    _publish({"message_id": "mr-w2a", "resource_kind": "WORKER",
              "worker_id": "W4", "event_type": "WORKER_ABSENT"})
    _publish({"message_id": "mr-w2b", "resource_kind": "WORKER",
              "worker_id": "W4", "event_type": "WORKER_RETURN", "occurred_at": 700})
    assert _one(
        "SELECT absence_until FROM worker_absence_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.worker_id = ("
        "  SELECT id FROM workers WHERE instance_id=i.id AND name='W4')",
        {}) == 700
    assert _one(
        "SELECT status FROM workers w JOIN instances i ON i.id=w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.name='W4'", {}) == "AVAILABLE"


def test_touching_absences_merge(demo_scenario):
    _publish({"message_id": "mr-w3a", "resource_kind": "WORKER",
              "worker_id": "W5", "event_type": "WORKER_ABSENT",
              "estimated_absence": 60})
    _publish({"message_id": "mr-w3b", "resource_kind": "WORKER",
              "worker_id": "W5", "event_type": "WORKER_ABSENT",
              "occurred_at": 560, "estimated_absence": 30})
    assert _one(
        "SELECT count(*) FROM worker_absence_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.worker_id = ("
        "  SELECT id FROM workers WHERE instance_id=i.id AND name='W5')",
        {}) == 1


def test_material_shortage_is_telemetry_only(demo_scenario):
    before = _one("SELECT count(*) FROM material_receipts", {})
    tid, created = _publish({
        "message_id": "mr-m1", "resource_kind": "MATERIAL",
        "material_sku": "MAT-001", "event_type": "MATERIAL_SHORTAGE",
    })
    assert created is True
    assert _one(
        "SELECT count(*) FROM telemetry_events te "
        "JOIN instances i ON i.id=te.instance_id "
        "JOIN materials m ON m.id=te.material_id "
        "WHERE i.name='factory_demo_01' AND m.sku='MAT-001' "
        "AND te.resource_kind='MATERIAL'", {}) == 1
    assert _one("SELECT count(*) FROM material_receipts", {}) == before


def test_legacy_machine_payload_infers_kind(demo_scenario):
    """No resource_kind + machine_id present => MACHINE (wire backward compat)."""
    tid, created = _publish({
        "message_id": "mr-legacy", "machine_id": "M7",
        "event_type": "FAILURE", "estimated_downtime": 45,
    })
    assert created is True
    assert _one(
        "SELECT resource_kind FROM telemetry_events te "
        "JOIN instances i ON i.id=te.instance_id "
        "WHERE i.name='factory_demo_01' AND te.message_id='mr-legacy'",
        {}) == "MACHINE"


def test_unknown_worker_rejected(demo_scenario):
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    raised = False
    try:
        ingest_telemetry_event({
            "message_id": "mr-bad", "instance_id": "factory_demo_01",
            "resource_kind": "WORKER", "worker_id": "NOPE",
            "event_type": "WORKER_ABSENT", "occurred_at": 10,
        })
    except PayloadError:
        raised = True
    assert raised


def test_wrong_duration_field_for_kind_rejected(demo_scenario):
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    raised = False
    try:
        ingest_telemetry_event({
            "message_id": "mr-bad2", "instance_id": "factory_demo_01",
            "resource_kind": "MATERIAL", "material_sku": "MAT-001",
            "event_type": "MATERIAL_SHORTAGE", "occurred_at": 10,
            "estimated_absence": 30,
        })
    except PayloadError:
        raised = True
    assert raised
