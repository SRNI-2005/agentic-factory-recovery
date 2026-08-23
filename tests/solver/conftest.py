import pytest
from sqlalchemy.orm import Query

MK01 = "data/raw/mk01/mk01.txt"
SFJW = "data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"
GASS = "data/raw/gass"

# The installed SQLAlchemy 2.0.52 build lacks the documented legacy
# Query.scalar_one; the canonical recovery tests rely on it.
if not hasattr(Query, "scalar_one"):
    def _scalar_one(self):
        return self.one()[0]

    Query.scalar_one = _scalar_one


@pytest.fixture(scope="session")
def built_db():
    """Reset once per session, import sources, build factory_demo_01.
    Read-only tests share this; state-mutating tests (Parts 3/5) must create
    their own instances or reset themselves."""
    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario
    from pathlib import Path

    reset_database(get_settings().database_url)
    import_mk01(Path(MK01))
    import_nouri(Path(SFJW))
    import_gass(Path(GASS))
    sid = build_scenario("factory_demo_01", seed=42)
    return {"settings": get_settings(), "scenario_id": sid}


@pytest.fixture()
def demo_session(built_db):
    """Read-mostly session that NEVER commits: recovery tests inject
    transient failure windows through it, which must not leak into
    subsequent tests (clipping suites assert exact warning counts)."""
    from sqlalchemy.orm import sessionmaker

    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine

    session = sessionmaker(bind=make_engine(), expire_on_commit=False)()
    try:
        yield session, (
            session.query(Instance)
            .filter(Instance.id == built_db["scenario_id"])
            .one()
        )
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def mk01_session(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        yield session, session.query(Instance).filter(Instance.name == "mk01").one()


@pytest.fixture()
def seeded_recovery_env(built_db):
    """Idempotent synthetic active schedule over factory_demo_01."""
    from coe.db.models.downtime import (
        MachineDowntimeWindow,
        WorkerAbsenceWindow,
    )
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope
    from sqlalchemy import select

    sid = built_db["scenario_id"]
    NOW = 1000
    with session_scope() as session:
        # idempotency: drop earlier seed artifacts (entries first — the
        # schedule_entries.version_id FK has no ON DELETE CASCADE), plus any
        # downtime/absence windows this fixture seeded on earlier runs.
        seeded_versions = (
            session.query(ScheduleVersion.id)
            .filter(ScheduleVersion.instance_id == sid,
                    ScheduleVersion.payload_hash.like("seeded-%"))
        )
        session.query(ScheduleEntry).filter(
            ScheduleEntry.instance_id == sid,
            ScheduleEntry.version_id.in_(seeded_versions),
        ).delete(synchronize_session=False)
        session.query(ScheduleVersion).filter(
            ScheduleVersion.instance_id == sid,
            ScheduleVersion.payload_hash.like("seeded-%"),
        ).delete(synchronize_session=False)
        session.query(MachineDowntimeWindow).filter(
            MachineDowntimeWindow.instance_id == sid,
        ).delete(synchronize_session=False)
        session.query(WorkerAbsenceWindow).filter(
            WorkerAbsenceWindow.instance_id == sid,
        ).delete(synchronize_session=False)
        session.flush()

        def _id(model, name):
            return session.execute(
                select(model.id).where(model.instance_id == sid,
                                       model.name == name)
            ).scalar_one()

        m = {n: _id(Machine, n) for n in ("M0", "M1", "M2", "M3")}
        w = {n: _id(Worker, n) for n in ("W1", "W2", "W3", "W4")}

        def _op(job_name, seq):
            jid = session.execute(
                select(Job.id).where(Job.instance_id == sid,
                                     Job.name == job_name)
            ).scalar_one()
            return session.execute(
                select(Operation.id).where(Operation.instance_id == sid,
                                           Operation.job_id == jid,
                                           Operation.sequence_number == seq)
            ).scalar_one()

        placements = [
            ("J1", 1, "M0", "W1", 400, 500),
            ("J1", 2, "M1", "W2", 900, 1100),
            ("J2", 1, "M2", "W3", 990, 1010),
            ("J2", 2, "M3", "W4", 1300, 1350),
            ("J3", 1, "M0", "W1", 1300, 1360),
        ]
        ver = ScheduleVersion(
            instance_id=sid, version_number=901, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=1360,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.01,
            rolled_back=False, payload_hash="seeded-" + "0" * 57,
            payload_json={"seeded": True},
        )
        session.add(ver)
        session.flush()
        for jname, seq, mname, wname, s, e in placements:
            session.add(ScheduleEntry(
                instance_id=sid, version_id=ver.id,
                operation_id=_op(jname, seq), machine_id=m[mname],
                worker_id=w[wname], start_time=s, end_time=e,
                processing_time=e - s, setup_time=0,
                is_frozen=False, status="SCHEDULED"))
        session.add(MachineDowntimeWindow(
            instance_id=sid, machine_id=m["M1"], downtime_from=1000,
            downtime_until=1100, reason="MAINTENANCE", source_event_ids=[]))
        session.add(MachineDowntimeWindow(
            instance_id=sid, machine_id=m["M0"], downtime_from=480,
            downtime_until=520, reason="MAINTENANCE", source_event_ids=[]))
        session.add(WorkerAbsenceWindow(
            instance_id=sid, worker_id=w["W1"], absence_from=300,
            absence_until=600, reason="WORKER_ABSENT", source_event_ids=[]))

    return {"scenario_id": sid, "version_id": ver.id, "now": NOW}


@pytest.fixture()
def env(demo_session, seeded_recovery_env):
    """Recovery-test environment: live session over freshly seeded state."""
    session, inst = demo_session
    return {"session": session, "instance": inst,
            "now": seeded_recovery_env["now"],
            "version_id": seeded_recovery_env["version_id"]}
