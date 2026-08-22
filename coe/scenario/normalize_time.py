from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.downtime import MachineDowntimeWindow
from coe.db.models.fjsp import Job, OperationMachineAlternative
from coe.db.models.materials import MaterialReceipt
from coe.db.models.provenance import Instance, ScenarioSource
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    WorkerAvailabilityWindow,
)


def normalize_times(session: Session, scenario_id: int) -> dict:
    """Identity normalization (source already minutes): assert non-negative ints,
    stamp unit metadata, record provenance."""
    jobs = session.scalars(select(Job).where(Job.instance_id == scenario_id)).all()
    for j in jobs:
        assert j.release_time >= 0
        if j.deadline is not None:
            assert j.deadline > j.release_time

    for t, in session.execute(
        select(OperationMachineAlternative.processing_time).where(
            OperationMachineAlternative.instance_id == scenario_id)
    ).all():
        assert t >= 0

    for t, in session.execute(
        select(OperationMachineWorkerTime.processing_time).where(
            OperationMachineWorkerTime.instance_id == scenario_id)
    ).all():
        assert t >= 0

    for a, b in session.execute(
        select(WorkerAvailabilityWindow.available_from,
               WorkerAvailabilityWindow.available_until).where(
            WorkerAvailabilityWindow.instance_id == scenario_id)
    ).all():
        assert 0 <= a < b

    for a, b in session.execute(
        select(MachineDowntimeWindow.downtime_from,
               MachineDowntimeWindow.downtime_until).where(
            MachineDowntimeWindow.instance_id == scenario_id)
    ).all():
        assert a >= 0 and (b is None or b > a)

    for t, in session.execute(
        select(MaterialReceipt.available_at).where(
            MaterialReceipt.instance_id == scenario_id)
    ).all():
        assert t >= 0

    inst = session.get(Instance, scenario_id)
    inst.source_time_unit = "minute"
    inst.time_scale_to_minutes = 1.0
    inst.normalized_time_unit = "minute"

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=scenario_id,
            contribution_type="time_normalization",
            transformation_description=(
                "identity mapping verified: all time columns are non-negative "
                "integer minutes (spec §7 conventions)"
            ),
            random_seed=None,
        )
    )
    return {"checks_passed": True}
