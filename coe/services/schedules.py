"""Schedule-domain loaders. The active_schedule VIEW is the canonical source."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from coe.services.schemas import GanttOut, VersionOut


def active(session: Session, instance_id: int) -> GanttOut | None:
    """Canonical Gantt source: the active_schedule VIEW (spec §5). Never re-derived."""
    ver = session.execute(text(
        "SELECT sv.* FROM active_schedule asev "
        "JOIN schedule_versions sv ON sv.id = asev.version_id "
        "WHERE asev.instance_id = :iid LIMIT 1"
    ), {"iid": instance_id}).mappings().first()
    if ver is None:
        return None
    entries = session.execute(text(
        "SELECT se.*, m.name AS machine_name, j.name AS job_name, "
        "       o.sequence_number, w.name AS worker_name "
        "FROM active_schedule asev "
        "JOIN schedule_entries se ON se.id = asev.id "
        "JOIN machines m ON m.id = se.machine_id "
        "JOIN operations o ON o.id = se.operation_id "
        "JOIN jobs j ON j.id = o.job_id "
        "LEFT JOIN workers w ON w.id = se.worker_id "
        "WHERE se.instance_id = :iid "
        "ORDER BY m.name ASC, se.start_time ASC, j.name ASC, "
        "         o.sequence_number ASC"
    ), {"iid": instance_id}).mappings().all()
    return GanttOut(version=VersionOut(**ver),
                    entries=[dict(e) for e in entries])


def versions(session: Session, instance_id: int) -> list[dict]:
    """Port of superseded data.schedule_versions — identical query."""
    from coe.db.models.schedule import ScheduleVersion

    rows = (session.query(ScheduleVersion)
            .filter(ScheduleVersion.instance_id == instance_id)
            .order_by(ScheduleVersion.version_number.desc()).all())
    return [{"id": r.id, "version_number": r.version_number,
             "schedule_type": r.schedule_type,
             "solver_status": r.solver_status, "makespan": r.makespan,
             "total_tardiness": r.total_tardiness,
             "rolled_back": r.rolled_back} for r in rows]


def recovery_runs(session: Session, instance_id: int) -> list[dict]:
    """Port of superseded data.recovery_runs — identical query."""
    from coe.db.models.recovery import RecoveryRun

    runs = (session.query(RecoveryRun)
            .filter(RecoveryRun.instance_id == instance_id)
            .order_by(RecoveryRun.started_at.desc()).all())
    return [{"id": r.id, "trigger": r.trigger, "status": r.status,
             "started_at": r.started_at, "finished_at": r.finished_at,
             "disruption_record_json": r.disruption_record_json,
             "node_timings_json": r.node_timings_json,
             "quantum_shadow_json": r.quantum_shadow_json,
             "final_status_version_id": r.final_status_version_id}
            for r in runs]
