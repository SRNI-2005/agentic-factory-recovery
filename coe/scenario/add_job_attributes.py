import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import Job, Operation, OperationMachineAlternative
from coe.db.models.provenance import ScenarioSource


def add_job_attributes(
    session: Session,
    scenario_id: int,
    *,
    source_instance_id: int,
    seed: int,
    release_span: int = 240,
    slack_low: float = 1.5,
    slack_high: float = 3.0,
) -> None:
    rng = random.Random(seed)

    sums: dict[int, int] = defaultdict(int)
    counts: dict[int, int] = defaultdict(int)
    alt_rows = session.execute(
        select(
            OperationMachineAlternative.operation_id,
            OperationMachineAlternative.processing_time,
        ).where(OperationMachineAlternative.instance_id == scenario_id)
    ).all()
    for op_id, t in alt_rows:
        sums[op_id] += t
        counts[op_id] += 1

    ops_by_job: dict[int, list[int]] = defaultdict(list)
    op_rows = session.execute(
        select(Operation.id, Operation.job_id)
        .where(Operation.instance_id == scenario_id)
        .order_by(Operation.id)
    ).all()
    for op_id, job_id in op_rows:
        ops_by_job[job_id].append(op_id)

    jobs = session.scalars(
        select(Job).where(Job.instance_id == scenario_id).order_by(Job.id)
    ).all()
    for job in jobs:
        release = rng.randint(0, release_span)
        twk = sum(sums[oid] / max(counts[oid], 1) for oid in ops_by_job[job.id])
        slack = rng.uniform(slack_low, slack_high)
        job.release_time = release
        job.deadline = release + max(1, round(slack * twk))
        job.priority = rng.randint(1, 5)

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=source_instance_id,
            contribution_type="job_attributes",
            transformation_description=(
                "TWK deadlines (slack 1.5-3.0x total work content), "
                f"releases in [0,{release_span}], priorities 1-5; synthetic"
            ),
            random_seed=seed,
        )
    )
