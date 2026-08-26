"""Read-only configure-domain loaders. Every query: instance-scoped + ORDER BY."""
from sqlalchemy.orm import Session

from coe.services.schemas import JobOut, MachineOut, MaterialOut, WorkerOut


def materials(session: Session, instance_id: int) -> list[MaterialOut]:
    """Port of superseded data.materials_overview — identical query."""
    from coe.db.models.materials import Material, MaterialReceipt

    out = []
    mats = (session.query(Material)
            .filter(Material.instance_id == instance_id)
            .order_by(Material.sku.asc()).all())
    for m in mats:
        receipts = (session.query(MaterialReceipt)
                    .filter(MaterialReceipt.instance_id == instance_id,
                            MaterialReceipt.material_id == m.id)
                    .order_by(MaterialReceipt.available_at.asc()).all())
        out.append(MaterialOut(
            sku=m.sku, initial_stock=m.initial_stock,
            reorder_point=m.reorder_point,
            receipts=[{"quantity": r.quantity, "available_at": r.available_at,
                       "source": r.source} for r in receipts]))
    return out


def machines(session: Session, instance_id: int) -> list[MachineOut]:
    """Port of superseded data.machines_overview — identical query."""
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine

    out = []
    machines = (session.query(Machine)
                .filter(Machine.instance_id == instance_id)
                .order_by(Machine.name.asc()).all())
    for m in machines:
        open_win = (session.query(MachineDowntimeWindow)
                    .filter(MachineDowntimeWindow.instance_id == instance_id,
                            MachineDowntimeWindow.machine_id == m.id,
                            MachineDowntimeWindow.downtime_until.is_(None))
                    .order_by(MachineDowntimeWindow.downtime_from.asc())
                    .first())
        out.append(MachineOut(name=m.name, status=m.status,
                              down_since=(open_win.downtime_from
                                          if open_win else None)))
    return out


def workers(session: Session, instance_id: int) -> list[WorkerOut]:
    """Port of superseded data.workers_overview — identical query."""
    from coe.db.models.downtime import WorkerAbsenceWindow
    from coe.db.models.workers import (
        Worker,
        WorkerAvailabilityWindow,
        WorkerRole,
    )

    out = []
    workers = (session.query(Worker)
               .filter(Worker.instance_id == instance_id)
               .order_by(Worker.name.asc()).all())
    roles = dict(session.query(WorkerRole.id, WorkerRole.role_name)
                 .filter(WorkerRole.instance_id == instance_id).all())
    for w in workers:
        avail = (session.query(WorkerAvailabilityWindow)
                 .filter(WorkerAvailabilityWindow.instance_id == instance_id,
                         WorkerAvailabilityWindow.worker_id == w.id)
                 .order_by(WorkerAvailabilityWindow.available_from.asc()).all())
        absences = (session.query(WorkerAbsenceWindow)
                    .filter(WorkerAbsenceWindow.instance_id == instance_id,
                            WorkerAbsenceWindow.worker_id == w.id,
                            WorkerAbsenceWindow.absence_until.is_(None))
                    .order_by(WorkerAbsenceWindow.absence_from.asc()).all())
        out.append(WorkerOut(
            name=w.name, role=roles.get(w.role_id),
            availability=[(a.available_from, a.available_until) for a in avail],
            absent_since=(absences[0].absence_from if absences else None)))
    return out


def jobs(session: Session, instance_id: int) -> list[JobOut]:
    """Port of superseded data.jobs_overview — identical query."""
    from sqlalchemy import func

    from coe.db.models.fjsp import Job, JobFamily, Operation

    op_counts = dict(
        session.query(Operation.job_id, func.count(Operation.id))
        .filter(Operation.instance_id == instance_id)
        .group_by(Operation.job_id).all())
    families = dict(session.query(JobFamily.id, JobFamily.name)
                    .filter(JobFamily.instance_id == instance_id).all())
    rows = (session.query(Job)
            .filter(Job.instance_id == instance_id)
            .order_by(Job.name.asc()).all())
    return [JobOut(name=j.name,
                   family=families.get(j.job_family_id),
                   release_time=j.release_time, deadline=j.deadline,
                   priority=j.priority, status=j.status,
                   ops=op_counts.get(j.id, 0)) for j in rows]


def jobs_per_day(session: Session, instance_id: int,
                 day_length: int = 1440) -> dict[int, list[str]]:
    """Port of superseded data.jobs_per_day — identical query."""
    from coe.db.models.fjsp import Job

    jobs = (session.query(Job)
            .filter(Job.instance_id == instance_id,
                    Job.deadline.isnot(None))
            .order_by(Job.name.asc()).all())
    grouped: dict[int, list[str]] = {}
    for j in sorted(jobs, key=lambda x: x.name):
        grouped.setdefault(j.deadline // day_length, []).append(j.name)
    return dict(sorted(grouped.items()))
