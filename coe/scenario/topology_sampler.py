import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import (
    Job,
    Machine,
    Operation,
    OperationMachineAlternative,
)


def _extract_profiles(session: Session, source_instance_id: int) -> dict:
    ops_per_job = [
        len(jobs)
        for jobs in (
            session.scalars(
                select(Operation.id)
                .where(
                    Operation.instance_id == source_instance_id,
                    Operation.job_id == jid,
                )
                .order_by(Operation.id)
            ).all()
            for jid in session.scalars(
                select(Job.id)
                .where(Job.instance_id == source_instance_id)
                .order_by(Job.id)
            ).all()
        )
    ]
    alt_counts: list[int] = []
    durations: list[int] = []
    rows = session.execute(
        select(
            OperationMachineAlternative.operation_id,
            OperationMachineAlternative.processing_time,
        )
        .where(OperationMachineAlternative.instance_id == source_instance_id)
        .order_by(
            OperationMachineAlternative.operation_id,
            OperationMachineAlternative.machine_id,
            OperationMachineAlternative.processing_time,
        )
    ).all()
    per_op: dict[int, int] = {}
    for op_id, t in rows:
        per_op[op_id] = per_op.get(op_id, 0) + 1
        durations.append(t)
    alt_counts = list(per_op.values())
    return {"ops_per_job": ops_per_job, "alt_counts": alt_counts, "durations": durations}


def sample_topology(
    session: Session,
    instance_id: int,
    *,
    source_instance_id: int,
    n_jobs: int = 30,
    n_machines: int = 8,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    profiles = _extract_profiles(session, source_instance_id)

    machines = [
        Machine(instance_id=instance_id, source_id=str(mi), name=f"M{mi}")
        for mi in range(n_machines)
    ]
    session.add_all(machines)
    session.flush()

    n_operations = 0
    for ji in range(n_jobs):
        jrow = Job(instance_id=instance_id, source_id=str(ji), name=f"J{ji + 1}")
        session.add(jrow)
        session.flush()
        n_ops = profiles["ops_per_job"][rng.randrange(len(profiles["ops_per_job"]))]
        for oi in range(n_ops):
            orow = Operation(
                instance_id=instance_id,
                job_id=jrow.id,
                source_id=f"{ji}:{oi}",
                sequence_number=oi + 1,
            )
            session.add(orow)
            session.flush()
            k = profiles["alt_counts"][rng.randrange(len(profiles["alt_counts"]))]
            k = min(k, n_machines)
            chosen = rng.sample(range(n_machines), k)
            for mi in chosen:
                session.add(
                    OperationMachineAlternative(
                        instance_id=instance_id,
                        operation_id=orow.id,
                        machine_id=machines[mi].id,
                        processing_time=rng.choice(profiles["durations"]),
                    )
                )
            n_operations += 1
    session.flush()
    return {"jobs": n_jobs, "machines": n_machines, "operations": n_operations}
