import pytest

pytestmark = pytest.mark.db

from coe.db.session import session_scope


def _mk_instance(session):
    from coe.db.models.provenance import Instance

    inst = Instance(name="t-sched", source_name="synthetic")
    session.add(inst)
    session.flush()
    return inst.id


def _mk_op_and_machine(session, inst_id):
    from coe.db.models.fjsp import Job, Machine, Operation

    m = Machine(instance_id=inst_id, name="M0")
    j = Job(instance_id=inst_id, name="J1")
    session.add_all([m, j])
    session.flush()
    o = Operation(instance_id=inst_id, job_id=j.id, sequence_number=1)
    session.add(o)
    session.flush()
    return o.id, m.id


def test_version_and_entry_roundtrip(clean_db):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    with session_scope() as session:
        iid = _mk_instance(session)
        oid, mid = _mk_op_and_machine(session, iid)
        v = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.5, makespan=40,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.5,
            failed_machine_ids=None, parent_version_id=None,
            rolled_back=False, payload_hash="a" * 64, payload_json={"k": "v"},
        )
        session.add(v)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v.id, operation_id=oid, machine_id=mid,
            worker_id=None, start_time=0, end_time=12, processing_time=12,
            setup_time=0, is_frozen=False, status="SCHEDULED",
        ))

    from sqlalchemy import create_engine, text

    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        row = c.execute(
            text("SELECT makespan, solver_status, payload_json FROM schedule_versions")
        ).one()
        n = c.execute(text("SELECT count(*) FROM schedule_entries")).scalar_one()
    assert tuple(row) == (40, "OPTIMAL", {"k": "v"})
    assert n == 1


def test_version_number_unique_per_instance(clean_db):
    from sqlalchemy.exc import IntegrityError

    from coe.db.models.schedule import ScheduleVersion

    with session_scope() as session:
        iid = _mk_instance(session)
        for _ in range(2):
            session.add(ScheduleVersion(
                instance_id=iid, version_number=1, schedule_type="BASELINE",
                solver_status="OPTIMAL", objective_value=0, makespan=0,
                total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
                time_limit_seconds=60, solve_duration_seconds=0.0,
                rolled_back=False, payload_hash="b" * 64, payload_json={},
            ))
        with pytest.raises(IntegrityError):
            session.flush()


def test_entry_status_domain(clean_db):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    with session_scope() as session:
        iid = _mk_instance(session)
        oid, mid = _mk_op_and_machine(session, iid)
        v = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=0, makespan=0,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.0,
            rolled_back=False, payload_hash="c" * 64, payload_json={},
        )
        session.add(v)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v.id, operation_id=oid, machine_id=mid,
            worker_id=None, start_time=0, end_time=1, processing_time=1,
            setup_time=0, is_frozen=False, status="BOGUS",
        ))
        with pytest.raises(Exception):  # CHECK violation raised at flush/execute
            session.flush()
        session.rollback()
