"""Regression: MAINTENANCE without estimated_downtime => open-ended window."""
import pytest

pytestmark = pytest.mark.db


def test_maintenance_without_duration_is_open_ended(demo_scenario):
    from sqlalchemy import select

    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine
    from coe.db.session import session_scope

    from coe.mqtt.ingest import ingest_telemetry_event

    sid = demo_scenario
    with session_scope() as session:
        mid = session.query(Machine.id).filter(
            Machine.instance_id == sid, Machine.name == "M5").scalar()
        telemetry_id, created = ingest_telemetry_event({
            "message_id": "maint-open-1",
            "instance_id": "factory_demo_01",
            "resource_kind": "MACHINE",
            "machine_id": "M5",
            "event_type": "MAINTENANCE",
            "occurred_at": 700,
            "severity": "LOW",
        })
        assert created and telemetry_id > 0
        row = session.execute(
            select(MachineDowntimeWindow)
            .where(MachineDowntimeWindow.instance_id == sid,
                   MachineDowntimeWindow.machine_id == mid)
            .order_by(MachineDowntimeWindow.id.desc())
        ).scalars().first()
        assert row is not None
        assert row.downtime_from == 700
        assert row.downtime_until is None      # open-ended, no CHECK crash
