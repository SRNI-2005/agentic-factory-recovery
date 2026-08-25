# coe/agents/safety.py
"""Pre-commit gate + post-commit verifier (spec §6.2-6.3).

Both evaluate the SAME check_solution implementation against (payload,
solution) — drift between gate and verifier is structurally impossible.
The verifier rebuilds assignments from schedule_entries (what actually got
committed) rather than trusting any stored solution blob.
"""
from sqlalchemy.orm import Session

from coe.solver.invariants import check_solution


def run_gate(payload: dict, solution: dict) -> dict:
    violations = check_solution(payload, solution)
    return {"passed": not violations, "violations": violations}


def _rebuilt_solution(session: Session, version) -> dict:
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.schedule import ScheduleEntry
    from coe.db.models.workers import Worker

    iid = version.instance_id
    machines = dict(session.query(Machine.id, Machine.name)
                    .filter(Machine.instance_id == iid)
                    .order_by(Machine.id).all())
    workers = dict(session.query(Worker.id, Worker.name)
                   .filter(Worker.instance_id == iid)
                   .order_by(Worker.id).all())
    jobs = dict(session.query(Job.id, Job.name)
                .filter(Job.instance_id == iid).order_by(Job.id).all())
    op_meta = {
        o.id: (jobs[o.job_id], o.sequence_number)
        for o in session.query(Operation)
        .filter(Operation.instance_id == iid)
        .order_by(Operation.job_id, Operation.sequence_number).all()}
    entries = (session.query(ScheduleEntry)
               .filter(ScheduleEntry.version_id == version.id)
               .order_by(ScheduleEntry.id).all())
    assignments = [{
        "operation_id": f"{op_meta[e.operation_id][0]}"
                        f"-O{op_meta[e.operation_id][1]}",
        "job_id": op_meta[e.operation_id][0],
        "machine_id": machines[e.machine_id],
        "worker_id": workers.get(e.worker_id) if e.worker_id else None,
        "start": e.start_time, "end": e.end_time,
        "processing_time": e.processing_time,
        "setup_time": e.setup_time,
        "is_frozen": e.is_frozen,
    } for e in entries]
    return {"status": version.solver_status,
            "objective_value": version.objective_value,
            "makespan": version.makespan,
            "total_tardiness": version.total_tardiness,
            "assignments": assignments,
            "solve_duration_seconds": version.solve_duration_seconds}


def verify_commit(instance_name: str) -> dict:
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import session_scope
    from coe.solver.committer import rollback_active

    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = (session.query(ScheduleVersion)
                   .filter(ScheduleVersion.instance_id == inst.id,
                           ScheduleVersion.solver_status.in_(("OPTIMAL",
                                                              "FEASIBLE")),
                           ScheduleVersion.rolled_back.is_(False))
                   .order_by(ScheduleVersion.version_number.desc(),
                             ScheduleVersion.id.desc()).first())
        if version is None:
            return {"passed": False,
                    "violations": ["no committed version found"],
                    "version_number": None, "rolled_back_from": None}
        solution = _rebuilt_solution(session, version)
        violations = check_solution(version.payload_json, solution)
        result = {"passed": not violations, "violations": violations,
                  "version_number": version.version_number,
                  "rolled_back_from": None}
        if violations:
            rollback_active(session, inst)
            result["rolled_back_from"] = version.version_number
        return result
