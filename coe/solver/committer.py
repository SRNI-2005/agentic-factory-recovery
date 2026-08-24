"""Solution JSON -> versioned database rows (spec §3.3, §4, §8)."""
import hashlib
import json

from coe.db.models.fjsp import Job, Machine, Operation
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
from coe.db.models.workers import Worker
from coe.solver.identifier import parse_op_id


class RollbackFloor(Exception):
    """Refusing to roll back the last remaining active version (§8)."""


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()


def commit_solution(session, *, instance_row, payload, solution,
                    failed_machine_names=(), now=None) -> ScheduleVersion:
    if solution["status"] not in ("OPTIMAL", "FEASIBLE"):
        raise ValueError(
            f"refusing to commit {solution['status']} solution "
            "(only OPTIMAL/FEASIBLE are committed, §3.3)")
    iid = instance_row.id

    latest = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == iid)
        .order_by(ScheduleVersion.version_number.desc())
        .with_for_update().first()
    )
    version_number = latest.version_number + 1 if latest else 1

    cfg = payload["config"]
    failed_ids = None
    if payload["schedule_type"] == "RECOVERY":
        failed_ids = sorted(set(failed_machine_names)) or None

    version = ScheduleVersion(
        instance_id=iid,
        version_number=version_number,
        schedule_type=payload["schedule_type"],
        solver_status=solution["status"],
        objective_value=float(solution["objective_value"]),
        makespan=int(solution["makespan"]),
        total_tardiness=int(solution["total_tardiness"]),
        alpha_weight=float(cfg.get("alpha", 1.0)),
        beta_weight=float(cfg.get("beta", 1.0)),
        time_limit_seconds=int(cfg.get("time_limit_seconds", 60)),
        solve_duration_seconds=float(solution["solve_duration_seconds"]),
        failed_machine_ids=failed_ids,
        parent_version_id=payload.get("parent_version_id"),
        rolled_back=False,
        payload_hash=payload_hash(payload),
        payload_json=payload,
    )
    session.add(version)
    session.flush()

    machine_ids = dict(session.query(Machine.name, Machine.id)
                       .filter(Machine.instance_id == iid)
                       .order_by(Machine.name).all())
    worker_ids = dict(session.query(Worker.name, Worker.id)
                      .filter(Worker.instance_id == iid)
                      .order_by(Worker.name).all())
    job_ids = dict(session.query(Job.name, Job.id)
                   .filter(Job.instance_id == iid)
                   .order_by(Job.name).all())
    ops_by_key: dict[tuple[str, int], Operation] = {}
    for jname in sorted(job_ids):
        for o in session.query(Operation).filter(
                Operation.job_id == job_ids[jname]
                ).order_by(Operation.sequence_number).all():
            ops_by_key[(jname, o.sequence_number)] = o

    for a in solution["assignments"]:
        key = parse_op_id(a["operation_id"])
        op = ops_by_key[key]
        session.add(ScheduleEntry(
            instance_id=iid, version_id=version.id, operation_id=op.id,
            machine_id=machine_ids[a["machine_id"]],
            worker_id=(worker_ids[a["worker_id"]]
                       if a.get("worker_id") else None),
            start_time=a["start"], end_time=a["end"],
            processing_time=a["processing_time"],
            setup_time=a.get("setup_time", 0),
            is_frozen=bool(a["is_frozen"]),
            status="FROZEN" if a["is_frozen"] else "SCHEDULED"))
        if now is None:
            op.status = "SCHEDULED"
        elif a["end"] <= now:
            op.status = "COMPLETED"
        elif a["start"] <= now < a["end"]:
            op.status = "IN_PROGRESS"
        else:
            op.status = "SCHEDULED"

    for b in payload.get("blocked_operations", []):
        ops_by_key[parse_op_id(b["operation_id"])].status = "BLOCKED"

    # Suspension memory loop (amendment 2026-08-24): payload root
    # suspended_jobs mirrors onto jobs.status so the next build_payload
    # remembers the suspension (rider c).
    suspended_names = set(payload.get("suspended_jobs") or ())
    if suspended_names:
        session.query(Job).filter(Job.instance_id == iid,
                                  Job.name.in_(suspended_names)).update(
            {Job.status: "BLOCKED"}, synchronize_session=False)

    session.flush()
    return version


def commit_solution_autocommit(instance_name, payload, solution,
                               failed_machine_names=(), now=None) -> int:
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = commit_solution(session, instance_row=inst,
                                  payload=payload, solution=solution,
                                  failed_machine_names=failed_machine_names,
                                  now=now)
        return version.id


def rollback_active(session, instance_row) -> tuple[int, int]:
    rows = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == instance_row.id,
                ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                ScheduleVersion.rolled_back.is_(False))
        .order_by(ScheduleVersion.version_number.desc(),
                  ScheduleVersion.id.desc())
        .with_for_update().all()
    )
    if not rows:
        raise RollbackFloor("no active version to roll back")
    if len(rows) == 1:
        raise RollbackFloor(
            f"version {rows[0].version_number} is the last remaining active "
            "version; rollback refused (floor, spec §8)")
    victim, survivor = rows[0], rows[1]
    victim.rolled_back = True
    session.flush()
    return victim.version_number, survivor.version_number
