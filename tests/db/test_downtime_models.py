import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _ids(session):
    from coe.db.models.fjsp import Machine
    from coe.db.models.provenance import Instance

    inst = Instance(name=f"t-dt-{id(session) % 10000}", source_name="test")
    session.add(inst)
    session.flush()
    m = Machine(instance_id=inst.id, name="MC-1")
    session.add(m)
    session.flush()
    return inst, m.id


def _event(inst_id, machine_id, **over):
    from coe.db.models.downtime import TelemetryEvent

    base = dict(
        occurred_at=10, instance_id=inst_id, message_id="m-1",
        machine_id=machine_id, event_type="FAILURE",
        received_at=10, payload_json={}, resource_kind="MACHINE",
    )
    base.update(over)
    return TelemetryEvent(**base)


def test_hypertable_exists(clean_db):
    from coe.db.session import session_scope

    with session_scope() as s:
        n = s.execute(
            text(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'telemetry_events'"
            )
        ).scalar_one()
    assert n == 1


def test_negative_occurred_at_rejected(clean_db):
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, mid = _ids(s)
        try:
            s.add(_event(inst.id, mid, occurred_at=-5))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_rows_span_two_chunks(clean_db):
    from coe.db.session import make_engine, session_scope

    with session_scope() as s:
        inst, mid = _ids(s)
        s.add(_event(inst.id, mid, message_id="m-a", occurred_at=0))
        s.add(_event(inst.id, mid, message_id="m-b", occurred_at=20000))
    with make_engine().begin() as conn:
        chunks = conn.execute(
            text("SELECT count(*) FROM show_chunks('telemetry_events')")
        ).scalar_one()
    assert chunks >= 2


def test_open_ended_downtime_allowed(clean_db):
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.session import session_scope

    with session_scope() as s:
        inst, mid = _ids(s)
        w = MachineDowntimeWindow(
            instance_id=inst.id, machine_id=mid,
            downtime_from=100, downtime_until=None,
            reason="FAILURE", severity="HIGH", source_event_ids=["evt-1"],
        )
        s.add(w)
        s.flush()
        assert w.downtime_until is None
