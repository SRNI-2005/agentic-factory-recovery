import pytest

from coe.dashboard.actions import (
    machine_down_action,
    machine_restore_action,
    material_shortage_action,
    resume_job_action,
    suspend_job_action,
    worker_absent_action,
    worker_return_action,
)

pytestmark = pytest.mark.db


# ── suspend / resume ────────────────────────────────────────────────

def test_suspend_resume_job_via_dashboard(clean_db, session, demo_scenario):
    from sqlalchemy import text

    jid = session.execute(text(
        "SELECT id FROM jobs WHERE instance_id = :i ORDER BY name LIMIT 1"
    ), {"i": demo_scenario}).scalar_one()
    from coe.db.models.fjsp import Job
    name = session.get(Job, jid).name

    ok, err = suspend_job_action("factory_demo_01", name, session=session)
    assert ok is not None
    assert err is None

    session.expire_all()
    st = session.execute(text("SELECT status FROM jobs WHERE id = :j"),
                         {"j": jid}).scalar_one()
    assert st == "BLOCKED"

    ok, err = resume_job_action("factory_demo_01", name, session=session)
    assert ok is not None
    assert err is None

    session.expire_all()
    st2 = session.execute(text("SELECT status FROM jobs WHERE id = :j"),
                          {"j": jid}).scalar_one()
    assert st2 == "PENDING"


def test_suspend_unknown_job_returns_error(clean_db, session, demo_scenario):
    ok, err = suspend_job_action("factory_demo_01", "NOPE-JOB", session=session)
    assert ok is None
    assert err is not None
    assert "unknown job" in err


def test_double_suspend_returns_error(clean_db, session, demo_scenario):
    from sqlalchemy import text
    from coe.db.models.fjsp import Job

    jid = session.execute(text(
        "SELECT id FROM jobs WHERE instance_id = :i ORDER BY name LIMIT 1"
    ), {"i": demo_scenario}).scalar_one()
    name = session.get(Job, jid).name

    ok1, _ = suspend_job_action("factory_demo_01", name, session=session)
    assert ok1 is not None

    ok, err = suspend_job_action("factory_demo_01", name, session=session)
    assert ok is None
    assert "already suspended" in err


# ── machine restore ─────────────────────────────────────────────────

def test_machine_restore_via_dashboard(clean_db, session, demo_scenario):
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine

    m = (session.query(Machine)
         .filter(Machine.instance_id == demo_scenario)
         .order_by(Machine.name).first())
    session.add(MachineDowntimeWindow(
        instance_id=demo_scenario, machine_id=m.id,
        downtime_from=10, downtime_until=None, reason="test"))
    session.flush()

    ok, err = machine_restore_action("factory_demo_01", m.name, at=100,
                                     session=session)
    assert ok is not None
    assert err is None
    assert "Restored" in ok


def test_machine_restore_no_window_returns_error(clean_db, session, demo_scenario):
    from coe.db.models.fjsp import Machine

    m = (session.query(Machine)
         .filter(Machine.instance_id == demo_scenario)
         .order_by(Machine.name).first())
    ok, err = machine_restore_action("factory_demo_01", m.name, at=100,
                                     session=session)
    assert ok is None
    assert "no open outage window" in err


# ── MQTT publishers (monkeypatched) ─────────────────────────────────

def test_machine_down_action_monkeypatched(monkeypatch):
    monkeypatch.setattr(
        "coe.services.actions.machine_down",
        lambda inst, mach, at=None, **kw: "msg-001")

    ok, err = machine_down_action("inst-1", "M1")
    assert ok is not None
    assert "msg-001" in ok
    assert err is None


def test_machine_down_action_broker_error(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionRefusedError("broker down")

    monkeypatch.setattr("coe.services.actions.machine_down", boom)
    ok, err = machine_down_action("inst-1", "M1")
    assert ok is None
    assert "broker down" in err


def test_worker_absent_action_monkeypatched(monkeypatch):
    monkeypatch.setattr(
        "coe.services.actions.worker_absent",
        lambda *a, **kw: "msg-002")

    ok, err = worker_absent_action("inst-1", "W3", at=60, duration=240)
    assert "msg-002" in ok
    assert err is None


def test_worker_return_action_monkeypatched(monkeypatch):
    monkeypatch.setattr(
        "coe.services.actions.worker_return",
        lambda *a, **kw: "msg-003")

    ok, err = worker_return_action("inst-1", "W3")
    assert "msg-003" in ok
    assert err is None


def test_material_shortage_action_monkeypatched(monkeypatch):
    monkeypatch.setattr(
        "coe.services.actions.material_shortage",
        lambda *a, **kw: "msg-004")

    ok, err = material_shortage_action("inst-1", "MAT-001")
    assert "msg-004" in ok
    assert err is None
