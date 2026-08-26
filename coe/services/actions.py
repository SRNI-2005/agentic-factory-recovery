"""Cockpit event actions. Every function mirrors a CLI-equivalent code path."""
from sqlalchemy.orm import Session


def suspend_job(session: Session, instance_name: str, job_name: str) -> None:
    from coe.db.models.fjsp import Job
    from coe.db.models.provenance import Instance

    job = (session.query(Job)
           .join(Instance, Instance.id == Job.instance_id)
           .filter(Instance.name == instance_name, Job.name == job_name)
           .one_or_none())
    if job is None:
        raise ValueError(f"unknown job '{job_name}' on '{instance_name}'")
    if job.status == "BLOCKED":
        raise ValueError(f"job '{job_name}' is already suspended")
    job.status = "BLOCKED"
    session.flush()


def resume_job(session: Session, instance_name: str, job_name: str) -> None:
    from coe.db.models.fjsp import Job
    from coe.db.models.provenance import Instance

    job = (session.query(Job)
           .join(Instance, Instance.id == Job.instance_id)
           .filter(Instance.name == instance_name, Job.name == job_name)
           .one_or_none())
    if job is None:
        raise ValueError(f"unknown job '{job_name}' on '{instance_name}'")
    job.status = "PENDING"
    session.flush()


def restore_machine(session: Session, instance_name: str,
                    machine_name: str, at: int | None = None) -> int:
    """Close every open outage window; mirrors cli._run_restore.

    Raises ValueError when zero open windows exist (spec §4: HTTP 409 there).
    """
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine
    from coe.db.models.provenance import Instance
    from coe.solver.payload_builder import resolve_reference_clock

    inst = (session.query(Instance)
            .filter(Instance.name == instance_name).one())
    mach = (session.query(Machine)
            .filter(Machine.instance_id == inst.id,
                    Machine.name == machine_name).one_or_none())
    if mach is None:
        raise ValueError(f"unknown machine '{machine_name}'")
    now = resolve_reference_clock(session, inst.id, at)
    opens = (session.query(MachineDowntimeWindow)
             .filter(MachineDowntimeWindow.instance_id == inst.id,
                     MachineDowntimeWindow.machine_id == mach.id,
                     MachineDowntimeWindow.downtime_until.is_(None)).all())
    if not opens:
        raise ValueError(f"no open outage window for '{machine_name}'")
    for w in opens:
        w.downtime_until = max(w.downtime_from + 1, now)
    mach.status = "ACTIVE"
    session.flush()
    return now


def machine_down(instance_name: str, machine_name: str,
                 at: int | None = None,
                 reason: str = "dashboard-toggle") -> str:
    """Publish a real MACHINE FAILURE so subscriber+listener react live.

    message_id stays auto-generated (uuid-based) so repeated toggles are
    distinct disruptions, not duplicate-suppressed replays.
    """
    from coe.mqtt.edge_stub import publish_failure

    return publish_failure(instance_name, machine_name,
                           occurred_at=at if at is not None else 512,
                           reason=reason)


def worker_absent(instance_name: str, worker_name: str,
                  at: int | None = None,
                  duration: int | None = None) -> str:
    from coe.mqtt.edge_stub import publish_resource_event

    return publish_resource_event(
        instance_name=instance_name, resource_kind="WORKER",
        resource_id=worker_name, event_type="WORKER_ABSENT",
        occurred_at=at if at is not None else 480,
        duration=duration)


def worker_return(instance_name: str, worker_name: str,
                  at: int | None = None) -> str:
    from coe.mqtt.edge_stub import publish_resource_event

    return publish_resource_event(
        instance_name=instance_name, resource_kind="WORKER",
        resource_id=worker_name, event_type="RETURN",
        occurred_at=at if at is not None else 480)


def material_shortage(instance_name: str, sku: str,
                      at: int | None = None) -> str:
    """MATERIAL telemetry (spec §6 map row); mirrors cli mqtt test-shortage."""
    from coe.mqtt.edge_stub import publish_resource_event

    return publish_resource_event(
        instance_name=instance_name, resource_kind="MATERIAL",
        resource_id=sku, event_type="MATERIAL_SHORTAGE",
        occurred_at=at if at is not None else 300)
