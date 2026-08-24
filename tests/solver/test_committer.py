"""Committer behavior against the real schema (mk01 pipeline, no workers)."""
import pytest
from sqlalchemy import create_engine

from coe.solver.engine import solve
from coe.solver.payload_builder import build_payload

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def solved_mk01(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = session.query(Instance).filter(Instance.name == "mk01").one()
        payload = build_payload(session, instance_row=inst,
                                alpha=1.0, beta=1.0, time_limit_seconds=30)
        solution = solve(payload)
    return payload, solution


def _inst(session, name="mk01"):
    from coe.db.models.provenance import Instance

    return session.query(Instance).filter(Instance.name == name).one()


def test_commit_creates_version_entries_and_mirrors(built_db, solved_mk01):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution, payload_hash

    payload, solution = solved_mk01
    with session_scope() as session:
        version = commit_solution(session, instance_row=_inst(session),
                                  payload=payload, solution=solution)
        vid = version.id
        assert version.version_number == 1
        assert version.schedule_type == "BASELINE"
        assert version.parent_version_id is None
        assert version.failed_machine_ids is None
        assert version.payload_hash == payload_hash(payload)
        assert version.makespan == solution["makespan"]

    with session_scope() as session:
        n = session.query(ScheduleEntry).filter(
            ScheduleEntry.version_id == vid).count()
        assert n == len(solution["assignments"])
        bad = session.query(ScheduleEntry).filter(
            ScheduleEntry.version_id == vid,
            ScheduleEntry.status != "SCHEDULED").count()
        assert bad == 0                      # baseline: no clock -> SCHEDULED


def test_commit_refuses_infeasible(built_db, solved_mk01):
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    payload, _ = solved_mk01
    bogus = {"status": "INFEASIBLE", "objective_value": 0, "makespan": 0,
             "total_tardiness": 0, "assignments": [],
             "solve_duration_seconds": 0.0}
    with session_scope() as session:
        with pytest.raises(ValueError):
            commit_solution(session, instance_row=_inst(session),
                            payload=payload, solution=bogus)
    bogus["status"] = "UNKNOWN"
    with session_scope() as session:
        with pytest.raises(ValueError):
            commit_solution(session, instance_row=_inst(session),
                            payload=payload, solution=bogus)


def test_commit_atomic_on_garbage(built_db, solved_mk01):
    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    payload, solution = solved_mk01
    solution = dict(solution)
    solution["assignments"] = [dict(solution["assignments"][0])]
    solution["assignments"][0]["machine_id"] = "GHOST"

    before = None
    with session_scope() as session:
        before = session.query(ScheduleVersion).count()
    try:
        with session_scope() as session:
            commit_solution(session, instance_row=_inst(session),
                            payload=payload, solution=solution)
    except Exception:
        pass
    with session_scope() as session:
        assert session.query(ScheduleVersion).count() == before


def test_status_mirroring_with_clock(built_db, solved_mk01):
    from sqlalchemy import select

    from coe.db.models.fjsp import Operation
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    payload, solution = solved_mk01
    horizonish = max(a["end"] for a in solution["assignments"])
    now = horizonish // 2
    with session_scope() as session:
        commit_solution(session, instance_row=_inst(session),
                        payload=payload, solution=solution, now=now)
        statuses = set(session.scalars(
            select(Operation.status)
            .where(Operation.instance_id == _inst(session).id)))
    assert {"SCHEDULED"} <= statuses
    assert statuses & {"COMPLETED", "IN_PROGRESS"}


def test_rollback_chain_and_floor(built_db, solved_mk01):
    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import session_scope

    from coe.solver.committer import (
        RollbackFloor,
        commit_solution,
        rollback_active,
    )

    payload, solution = solved_mk01
    with session_scope() as session:
        inst = _inst(session)
        # relative pattern: snapshot the live candidate chain BEFORE adding;
        # the floor lands after every candidate above the oldest is rolled.
        chain = [
            r[0] for r in session.query(ScheduleVersion.version_number)
            .filter(ScheduleVersion.instance_id == inst.id,
                    ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                    ScheduleVersion.rolled_back.is_(False))
            .order_by(ScheduleVersion.version_number.desc()).all()
        ]
        top = chain[0] if chain else 0
        commit_solution(session, instance_row=inst, payload=payload,
                        solution=solution)                     # top+1
        commit_solution(session, instance_row=inst, payload=payload,
                        solution=solution)                     # top+2
        chain.insert(0, top + 1)
        chain.insert(0, top + 2)
        rolled, active = rollback_active(session, inst)
        assert (rolled, active) == (top + 2, top + 1)
        for expected_rolled, expected_active in zip(chain[1:], chain[2:]):
            rolled, active = rollback_active(session, inst)
            assert (rolled, active) == (expected_rolled, expected_active)
        with pytest.raises(RollbackFloor):
            rollback_active(session, inst)


def test_failed_machines_root_key_recorded_on_baseline(built_db):
    """Minor 5: payload root failed_machines is the audit truth — a
    status-stripped BASELINE records the stripped set, not just RECOVERY."""
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution
    from coe.solver.payload_builder import build_payload

    with session_scope() as session:
        inst = _inst(session, "factory_demo_01")
        payload = build_payload(session, instance_row=inst,
                                alpha=1.0, beta=1.0, time_limit_seconds=30)
        payload["failed_machines"] = ["M2", "M3"]
        solution = {"status": "OPTIMAL", "objective_value": 1.0,
                    "makespan": 1, "total_tardiness": 0,
                    "assignments": [], "solve_duration_seconds": 0.0}
        version = commit_solution(session, instance_row=inst,
                                  payload=payload, solution=solution)
        assert version.failed_machine_ids == ["M2", "M3"]


def test_suspended_jobs_mirror_to_jobs_table(built_db):
    from coe.db.models.fjsp import Job
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution
    from coe.solver.payload_builder import build_payload

    victim = None
    vid = None
    with session_scope() as session:
        inst = _inst(session, "factory_demo_01")
        payload = build_payload(session, instance_row=inst,
                                alpha=1.0, beta=1.0, time_limit_seconds=30)
        payload["suspended_jobs"] = [payload["jobs"][0]["job_id"]]
        victim = payload["jobs"][0]["job_id"]
        # shared-instance hygiene (conftest contract): remember pre-test status
        original = (session.query(Job.status)
                    .filter(Job.instance_id == inst.id, Job.name == victim)
                    .scalar_one())
        solution = {"status": "OPTIMAL", "objective_value": 1.0,
                    "makespan": 1, "total_tardiness": 0,
                    "assignments": [], "solve_duration_seconds": 0.0}
        version = commit_solution(session, instance_row=inst, payload=payload,
                                  solution=solution)
        vid = version.id
        status = (session.query(Job.status)
                  .filter(Job.instance_id == inst.id, Job.name == victim)
                  .scalar_one())
    assert status == "BLOCKED"
    with session_scope() as session:          # restore for downstream suites
        inst = _inst(session, "factory_demo_01")
        # shared-instance hygiene: remove this test's ghost empty-assignment
        # version (entries first — no ON DELETE CASCADE on version_id).
        from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

        session.query(ScheduleEntry).filter(
            ScheduleEntry.version_id == vid).delete(synchronize_session=False)
        session.query(ScheduleVersion).filter(
            ScheduleVersion.id == vid).delete(synchronize_session=False)
        session.query(Job).filter(Job.instance_id == inst.id,
                                  Job.name == victim).update(
            {Job.status: original}, synchronize_session=False)


def test_failed_ids_union_records_unstripped_names(built_db):
    """A RECOVERY naming a machine that was NOT stripped still audits it."""
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution
    from coe.solver.payload_builder import build_payload

    with session_scope() as session:
        inst = _inst(session, "factory_demo_01")
        payload = build_payload(session, instance_row=inst,
                                alpha=1.0, beta=1.0, time_limit_seconds=30,
                                schedule_type="RECOVERY", now=1000)
        assert "M6" not in payload["failed_machines"]
        solution = {"status": "OPTIMAL", "objective_value": 1.0,
                    "makespan": 1, "total_tardiness": 0,
                    "assignments": [], "solve_duration_seconds": 0.0}
        version = commit_solution(session, instance_row=inst,
                                  payload=payload, solution=solution,
                                  failed_machine_names=("M6",))
        vid = version.id
    from sqlalchemy import text

    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        ids = c.execute(text(
            "SELECT failed_machine_ids FROM schedule_versions "
            "WHERE id = :v"), {"v": vid}).scalar_one()
    assert ids == ["M6"]
