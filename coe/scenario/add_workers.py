import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import OperationMachineAlternative
from coe.db.models.provenance import ScenarioSource
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    Worker,
    WorkerAvailabilityWindow,
    WorkerRole,
)

ROLES = ("OPERATOR", "TECHNICIAN", "SUPERVISOR")
PATTERNS: tuple[tuple[str, list[tuple[int, int]], float], ...] = (
    ("full-day", [(0, 1440)], 0.60),
    ("late-start", [(240, 1440)], 0.25),
    ("split-shift", [(0, 240), (480, 1440)], 0.15),
)


def _pick_pattern(rng: random.Random) -> tuple[str, list[tuple[int, int]]]:
    roll = rng.random()
    acc = 0.0
    for name, spans, weight in PATTERNS:
        acc += weight
        if roll < acc:
            return name, spans
    return PATTERNS[0][0], PATTERNS[0][1]


def add_workers(
    session: Session,
    scenario_id: int,
    *,
    nouri_source_id: int,
    seed: int,
    n_workers: int = 12,
    skill_low: float = 0.9,
    skill_high: float = 1.15,
) -> dict:
    """Worker layer onto the sampled topology. Structure follows the imported
    Hutter/Nouri flexibility behavior; values are synthetic (seeded)."""
    rng = random.Random(seed)

    roles = [
        WorkerRole(instance_id=scenario_id, role_name=name) for name in ROLES
    ]
    session.add_all(roles)
    session.flush()

    workers = [
        Worker(
            instance_id=scenario_id,
            source_id=str(wi),
            name=f"W{wi + 1}",
            role_id=rng.choice(roles).id,
        )
        for wi in range(n_workers)
    ]
    session.add_all(workers)
    session.flush()

    windows = 0
    for w in workers:
        pattern_name, spans = _pick_pattern(rng)
        for a, b in spans:
            session.add(
                WorkerAvailabilityWindow(
                    instance_id=scenario_id,
                    worker_id=w.id,
                    available_from=a,
                    available_until=b,
                    source_pattern=pattern_name,
                )
            )
            windows += 1

    alts = session.execute(
        select(OperationMachineAlternative)
        .where(OperationMachineAlternative.instance_id == scenario_id)
        .order_by(
            OperationMachineAlternative.operation_id,
            OperationMachineAlternative.machine_id,
        )
    ).scalars().all()

    eligibility = 0
    for alt in alts:
        k = rng.randint(1, min(3, n_workers))
        chosen = rng.sample(workers, k)
        for w in chosen:
            factor = rng.uniform(skill_low, skill_high)
            session.add(
                OperationMachineWorkerTime(
                    instance_id=scenario_id,
                    operation_id=alt.operation_id,
                    machine_id=alt.machine_id,
                    worker_id=w.id,
                    processing_time=max(1, round(alt.processing_time * factor)),
                )
            )
            eligibility += 1

    # Spec §6.3 invariant — must hold before the transaction can commit.
    covered = {
        (r[0], r[1])
        for r in session.execute(
            select(OperationMachineWorkerTime.operation_id,
                   OperationMachineWorkerTime.machine_id).where(
                OperationMachineWorkerTime.instance_id == scenario_id)
        ).all()
    }
    missing = [
        (a.operation_id, a.machine_id)
        for a in alts
        if (a.operation_id, a.machine_id) not in covered
    ]
    if missing:
        raise RuntimeError(f"alternatives without eligible workers: {missing[:5]}")

    session.add_all(
        [
            ScenarioSource(
                scenario_id=scenario_id,
                source_instance_id=nouri_source_id,
                contribution_type="worker_flexibility",
                transformation_description=(
                    "applied Hutter/Nouri worker-flexibility structure: "
                    f"{n_workers} workers, {eligibility} eligibility rows "
                    "(values synthetic)"
                ),
                random_seed=seed,
            ),
            ScenarioSource(
                scenario_id=scenario_id,
                source_instance_id=nouri_source_id,
                contribution_type="worker_availability",
                transformation_description=(
                    f"{windows} concrete availability windows from shift patterns; synthetic"
                ),
                random_seed=seed,
            ),
        ]
    )
    session.flush()
    return {"workers": n_workers, "eligibility_rows": eligibility, "windows": windows}
