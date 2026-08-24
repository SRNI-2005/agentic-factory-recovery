import pytest

pytestmark = pytest.mark.db

from coe.db.models.recovery import (
    RecoveryProposal,
    RecoveryRun,
    ScheduleExplanation,
)
from coe.db.session import session_scope


def _instance(session):
    from coe.db.models.provenance import Instance

    inst = Instance(name="p3models", source_name="synthetic")
    session.add(inst)
    session.flush()
    return inst


def test_run_roundtrip(clean_db):
    with session_scope() as session:
        inst = _instance(session)
        run = RecoveryRun(
            instance_id=inst.id, trigger="CLI", status="COMMITTED",
            disruption_record_json={"kind": "MACHINE", "machine_id": "M3"},
        )
        session.add(run)
        session.flush()
        assert run.id is not None
        assert run.started_at is not None          # server_default
        assert run.finished_at is None
        assert run.node_timings_json is None       # Phase 5 populates
        assert run.quantum_shadow_json is None


def test_trigger_domain(clean_db):
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        inst = _instance(session)
        session.add(RecoveryRun(instance_id=inst.id, trigger="HTTP",
                                status="COMMITTED",
                                disruption_record_json={}))
        try:
            session.flush()
            raise AssertionError("expected CHECK violation")
        except IntegrityError:
            session.rollback()


def test_status_domain(clean_db):
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        inst = _instance(session)
        session.add(RecoveryRun(instance_id=inst.id, trigger="CLI",
                                status="SOLVED",     # not a legal status
                                disruption_record_json={}))
        try:
            session.flush()
            raise AssertionError("expected CHECK violation")
        except IntegrityError:
            session.rollback()


def test_proposal_verdict_domain(clean_db):
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        inst = _instance(session)
        run = RecoveryRun(instance_id=inst.id, trigger="CLI", status="COMMITTED",
                          disruption_record_json={})
        session.add(run)
        session.flush()
        session.add(RecoveryProposal(
            instance_id=inst.id, run_id=run.id, round_number=1,
            candidate_json={"type": "DEFER_JOB"}, verdict="MAYBE"))
        try:
            session.flush()
            raise AssertionError("expected CHECK violation")
        except IntegrityError:
            session.rollback()


def test_explanation_unique_per_version(clean_db):
    from sqlalchemy.exc import IntegrityError

    from coe.db.models.schedule import ScheduleVersion

    with session_scope() as session:
        inst = _instance(session)
        v = ScheduleVersion(
            instance_id=inst.id, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=0.0, makespan=0,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.0,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v)
        session.flush()
        session.add(ScheduleExplanation(instance_id=inst.id, version_id=v.id,
                                        rationale="r"))
        session.flush()
        session.add(ScheduleExplanation(instance_id=inst.id, version_id=v.id,
                                        rationale="r2"))
        try:
            session.flush()
            raise AssertionError("expected UNIQUE violation")
        except IntegrityError:
            session.rollback()
