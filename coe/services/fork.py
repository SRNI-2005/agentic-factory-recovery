"""Transactional instance fork (spec §2, §5): template stays pristine."""
import uuid

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from coe.db.models.downtime import (
    MachineDowntimeWindow,
    WorkerAbsenceWindow,
)
from coe.db.models.fjsp import (
    Job,
    JobFamily,
    Machine,
    MachineCapability,
    Operation,
    OperationMachineAlternative,
    SetupTime,
)
from coe.db.models.materials import Material, MaterialReceipt, OperationBom
from coe.db.models.provenance import Instance, ScenarioSource
from coe.db.models.recovery import ScheduleExplanation
from coe.db.models.schedule import (
    ScheduleEntry,
    ScheduleVersion,
)
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    Worker,
    WorkerAvailabilityWindow,
    WorkerRole,
)


class ForkError(RuntimeError):
    pass


def _copy_rows(session: Session, rows: list, overrides_for) -> list:
    """Clone ORM entities with fresh PKs.

    Works for single-PK tables ('id' dropped) and composite-PK tables
    (whose PK columns are exactly the overridden FK columns).
    """
    out = []
    for r in rows:
        attrs = {c.key: getattr(r, c.key)
                 for c in sa_inspect(r).mapper.column_attrs}
        attrs.pop("id", None)
        attrs.update(overrides_for(r))
        out.append(type(r)(**attrs))
    session.add_all(out)
    session.flush()
    return out


def _remap(value, id_map):
    return None if value is None else id_map[value]


def _clone_table(session: Session, model, source_id: int, fork_id: int,
                 order_col, **fk_maps):
    """Copy rows of `model` to the fork; returns (old_rows, new_rows, id_map)."""
    olds = (session.query(model)
            .filter(model.instance_id == source_id)
            .order_by(order_col).all())

    def overrides(r):
        out = {"instance_id": fork_id}
        for col, id_map in fk_maps.items():
            out[col] = _remap(getattr(r, col), id_map)
        return out

    news = _copy_rows(session, olds, overrides)
    # Composite-PK tables have no 'id'; their maps are unused by callers.
    id_map = ({o.id: n.id for o, n in zip(olds, news)}
              if olds and hasattr(olds[0], "id") else {})
    return olds, news, id_map


def fork_instance(session: Session, source: Instance,
                  new_name: str | None = None) -> Instance:
    """Fork every domain table under a fresh instance row (one transaction).

    Skips system-owned history: telemetry_events, recovery_runs,
    recovery_proposals. ScheduleVersion.parent_version_id and
    failed_machine_ids are not carried over (parent-scoped references);
    lineage lives in the ScenarioSource row written here.
    """
    name = new_name or f"{source.name}@{uuid.uuid4().hex[:8]}"
    if session.query(Instance).filter_by(name=name).one_or_none():
        raise ForkError(f"instance '{name}' already exists")
    fork = Instance(name=name, source_name="fork",
                    source_version=f"of:{source.name}",
                    source_license=source.source_license,
                    source_checksum=source.source_checksum)
    session.add(fork)
    session.flush()

    _, _, fam_ids = _clone_table(session, JobFamily, source.id, fork.id,
                                 JobFamily.id)
    _, _, mach_ids = _clone_table(session, Machine, source.id, fork.id,
                                  Machine.name)
    _, _, role_ids = _clone_table(session, WorkerRole, source.id, fork.id,
                                  WorkerRole.role_name)
    _clone_table(session, MachineCapability, source.id, fork.id,
                 MachineCapability.id, machine_id=mach_ids)
    _, _, worker_ids = _clone_table(session, Worker, source.id, fork.id,
                                    Worker.name, role_id=role_ids)
    _, _, job_ids = _clone_table(session, Job, source.id, fork.id,
                                 Job.name, job_family_id=fam_ids)
    _, _, op_ids = _clone_table(session, Operation, source.id, fork.id,
                                Operation.id, job_id=job_ids,
                                required_role_id=role_ids)
    _clone_table(session, OperationMachineAlternative, source.id, fork.id,
                 OperationMachineAlternative.operation_id,
                 operation_id=op_ids, machine_id=mach_ids)
    _clone_table(session, OperationMachineWorkerTime, source.id, fork.id,
                 OperationMachineWorkerTime.operation_id,
                 operation_id=op_ids, machine_id=mach_ids,
                 worker_id=worker_ids)
    _clone_table(session, SetupTime, source.id, fork.id,
                 SetupTime.id, machine_id=mach_ids,
                 from_family_id=fam_ids, to_family_id=fam_ids)
    _, _, mat_ids = _clone_table(session, Material, source.id, fork.id,
                                 Material.sku)
    _clone_table(session, OperationBom, source.id, fork.id,
                 OperationBom.operation_id, operation_id=op_ids,
                 material_id=mat_ids)
    _clone_table(session, MaterialReceipt, source.id, fork.id,
                 MaterialReceipt.available_at, material_id=mat_ids)
    _clone_table(session, MachineDowntimeWindow, source.id, fork.id,
                 MachineDowntimeWindow.id, machine_id=mach_ids)
    _clone_table(session, WorkerAvailabilityWindow, source.id, fork.id,
                 WorkerAvailabilityWindow.id, worker_id=worker_ids)
    _clone_table(session, WorkerAbsenceWindow, source.id, fork.id,
                 WorkerAbsenceWindow.id, worker_id=worker_ids)

    old_versions = (session.query(ScheduleVersion)
                    .filter(ScheduleVersion.instance_id == source.id)
                    .order_by(ScheduleVersion.version_number).all())
    new_versions = []
    for old_v in old_versions:
        attrs = {c.key: getattr(old_v, c.key)
                 for c in sa_inspect(old_v).mapper.column_attrs
                 if c.key != "id"}
        attrs.update({"instance_id": fork.id, "parent_version_id": None,
                      "failed_machine_ids": None})
        new_versions.append(ScheduleVersion(**attrs))
    session.add_all(new_versions)
    session.flush()
    ver_ids = {o.id: n.id for o, n in zip(old_versions, new_versions)}
    _clone_table(session, ScheduleEntry, source.id, fork.id,
                 ScheduleEntry.id, version_id=ver_ids,
                 operation_id=op_ids, machine_id=mach_ids,
                 worker_id=worker_ids)
    _clone_table(session, ScheduleExplanation, source.id, fork.id,
                 ScheduleExplanation.id, version_id=ver_ids)

    session.add(ScenarioSource(
        scenario_id=fork.id, source_instance_id=source.id,
        contribution_type="fork",
        transformation_description=f"fork of {source.name}"))
    session.flush()
    return fork
