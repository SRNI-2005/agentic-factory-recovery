import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _ids(session):
    """Minimal instance + one machine + one worker + one material."""
    from coe.db.models.fjsp import Machine
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.models.workers import Worker

    inst = Instance(name=f"t-mr-{id(session) % 100000}", source_name="test")
    session.add(inst)
    session.flush()
    m = Machine(instance_id=inst.id, name="M1")
    w = Worker(instance_id=inst.id, name="W1")
    mat = Material(instance_id=inst.id, sku="SKU-1", initial_stock=10)
    session.add_all([m, w, mat])
    session.flush()
    return inst, m.id, w.id, mat.id


def _event(**kw):
    from coe.db.models.downtime import TelemetryEvent

    base = dict(
        occurred_at=10, instance_id=None, message_id="m",
        machine_id=None, worker_id=None, material_id=None,
        resource_kind="MACHINE", event_type="FAILURE",
        received_at=10, payload_json={},
    )
    base.update(kw)
    return TelemetryEvent(**base)


def test_two_resources_rejected(clean_db):
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, mid, wid, _mat = _ids(s)
        try:
            s.add(_event(instance_id=inst.id, message_id="a",
                         machine_id=mid, worker_id=wid))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_zero_resources_rejected(clean_db):
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, *_ = _ids(s)
        try:
            s.add(_event(instance_id=inst.id, message_id="b"))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_worker_kind_event_persists(clean_db):
    from coe.db.session import session_scope

    with session_scope() as s:
        inst, _, wid, _ = _ids(s)
        ev = _event(instance_id=inst.id, message_id="c",
                    resource_kind="WORKER", event_type="WORKER_ABSENT",
                    worker_id=wid)
        s.add(ev)
        s.flush()
        assert ev.machine_id is None and ev.worker_id == wid


def test_absence_window_open_ended_ok(clean_db):
    from coe.db.models.downtime import WorkerAbsenceWindow
    from coe.db.session import session_scope

    with session_scope() as s:
        inst, _, wid, _ = _ids(s)
        row = WorkerAbsenceWindow(
            instance_id=inst.id, worker_id=wid,
            absence_from=100, absence_until=None,
            reason="WORKER_ABSENT", severity="MEDIUM",
            source_event_ids=[7],
        )
        s.add(row)
        s.flush()
        assert row.absence_until is None


def test_absence_interval_order_enforced(clean_db):
    from coe.db.models.downtime import WorkerAbsenceWindow
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, _, wid, _ = _ids(s)
        try:
            s.add(WorkerAbsenceWindow(
                instance_id=inst.id, worker_id=wid,
                absence_from=100, absence_until=50,
                reason="WORKER_ABSENT", source_event_ids=[]))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised
