import pytest

from coe.services.actions import (
    material_shortage,
    machine_down,
    resume_job,
    restore_machine,
    suspend_job,
    worker_absent,
    worker_return,
)

pytestmark = pytest.mark.db


def test_suspend_and_resume_job(clean_db, session, demo_scenario):
    from sqlalchemy import text

    jid = session.execute(text(
        "SELECT id FROM jobs WHERE instance_id = :i ORDER BY name "
        "LIMIT 1"), {"i": demo_scenario}).scalar_one()
    suspend_job(session, "factory_demo_01", _job_name_by_id(session, jid))
    st = session.execute(text("SELECT status FROM jobs WHERE id = :j"),
                         {"j": jid}).scalar_one()
    assert st == "BLOCKED"
    resume_job(session, "factory_demo_01", _job_name_by_id(session, jid))
    st2 = session.execute(text("SELECT status FROM jobs WHERE id = :j"),
                          {"j": jid}).scalar_one()
    assert st2 == "PENDING"


def test_suspend_unknown_job_raises(clean_db, session, demo_scenario):
    with pytest.raises(ValueError):
        suspend_job(session, "factory_demo_01", "NOPE-JOB")


def test_double_suspend_raises(clean_db, session, demo_scenario):
    from sqlalchemy import text

    name = _job_name_by_id(session, session.execute(
        text("SELECT id FROM jobs WHERE instance_id = :i ORDER BY name "
             "LIMIT 1"), {"i": demo_scenario}).scalar_one())
    suspend_job(session, "factory_demo_01", name)
    with pytest.raises(ValueError):
        suspend_job(session, "factory_demo_01", name)


def test_restore_machine_closes_open_window(clean_db, session,
                                            demo_scenario):
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine

    m = (session.query(Machine)
         .filter(Machine.instance_id == demo_scenario)
         .order_by(Machine.name).first())
    session.add(MachineDowntimeWindow(
        instance_id=demo_scenario, machine_id=m.id, downtime_from=10,
        downtime_until=None, reason="test"))
    session.flush()
    now = restore_machine(session, "factory_demo_01", m.name, at=100)
    win = (session.query(MachineDowntimeWindow)
           .filter(MachineDowntimeWindow.instance_id == demo_scenario,
                   MachineDowntimeWindow.machine_id == m.id,
                   MachineDowntimeWindow.downtime_until.is_(None))
           .one_or_none())
    assert win is None
    assert now >= 10


def test_restore_machine_without_open_window_raises(clean_db, session,
                                                    demo_scenario):
    from coe.db.models.fjsp import Machine

    m = (session.query(Machine)
         .filter(Machine.instance_id == demo_scenario)
         .order_by(Machine.name).first())
    with pytest.raises(ValueError,
                       match=f"no open outage window for '{m.name}'"):
        restore_machine(session, "factory_demo_01", m.name, at=100)


def test_publishers_map_arguments(monkeypatch):
    calls = {}

    def fake_failure(instance_name, machine_name, **kw):
        calls["failure"] = (instance_name, machine_name, kw)

    def fake_event(**kw):
        calls["event"] = kw

    monkeypatch.setattr("coe.mqtt.edge_stub.publish_failure", fake_failure)
    monkeypatch.setattr(
        "coe.mqtt.edge_stub.publish_resource_event", fake_event)

    machine_down("inst-1", "M1")
    assert calls["failure"] == ("inst-1", "M1",
                                {"occurred_at": 512,
                                 "reason": "dashboard-toggle"})

    machine_down("inst-1", "M1", at=77, reason="seized")
    assert calls["failure"][2] == {"occurred_at": 77, "reason": "seized"}

    worker_absent("inst-1", "W3", at=60, duration=240)
    assert calls["event"] == {
        "instance_name": "inst-1", "resource_kind": "WORKER",
        "resource_id": "W3", "event_type": "WORKER_ABSENT",
        "occurred_at": 60, "duration": 240}

    worker_return("inst-1", "W3")
    assert calls["event"]["event_type"] == "RETURN"
    assert calls["event"]["occurred_at"] == 480
    assert "duration" not in calls["event"]

    material_shortage("inst-1", "MAT-001", at=300)
    assert calls["event"] == {
        "instance_name": "inst-1", "resource_kind": "MATERIAL",
        "resource_id": "MAT-001", "event_type": "MATERIAL_SHORTAGE",
        "occurred_at": 300}


@pytest.mark.mqtt
def test_publish_failure_round_trips_broker(demo_scenario):
    from coe.mqtt.edge_stub import publish_failure

    mid1 = publish_failure("factory_demo_01", "M1", occurred_at=5)
    mid2 = publish_failure("factory_demo_01", "M1", occurred_at=6)
    assert mid1 != mid2  # distinct presses must not dedup-suppress


def _job_name_by_id(session, jid):
    from coe.db.models.fjsp import Job

    return session.get(Job, jid).name
