import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.downtime import MachineDowntimeWindow
from coe.db.models.fjsp import Machine
from coe.db.models.provenance import Instance, ScenarioSource


def _gass_or_self(session: Session, scenario_id: int) -> int:
    """Attribution target for synthetic rows: the gass instance when present,
    else self-reference. Keeps the FK valid either way."""
    row = (
        session.query(Instance.id)
        .filter(Instance.name.like("gass%"))
        .order_by(Instance.id.asc())
        .first()
    )
    return row[0] if row else scenario_id


def add_maintenance_windows(
    session: Session,
    scenario_id: int,
    *,
    seed: int,
    horizon: int = 1440,
    max_per_machine: int = 2,
    window_low: int = 60,
    window_high: int = 180,
) -> dict:
    rng = random.Random(seed)
    machine_ids = session.scalars(
        select(Machine.id).where(Machine.instance_id == scenario_id).order_by(Machine.id)
    ).all()

    placed = 0
    for mid in machine_ids:
        count = rng.randint(0, max_per_machine)
        windows: list[tuple[int, int]] = []
        attempts = 0
        while len(windows) < count and attempts < 50:
            attempts += 1
            dur = rng.randint(window_low, window_high)
            start = rng.randrange(window_low, horizon - dur)
            if all(start + dur < s or e < start for s, e in windows):
                windows.append((start, start + dur))
        for start, end in sorted(windows):
            session.add(
                MachineDowntimeWindow(
                    instance_id=scenario_id,
                    machine_id=mid,
                    downtime_from=start,
                    downtime_until=end,
                    reason="MAINTENANCE",
                    severity=rng.choice(["LOW", "MEDIUM"]),
                    source_event_ids=[],
                )
            )
            placed += 1

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=_gass_or_self(session, scenario_id),
            contribution_type="maintenance_windows",
            transformation_description=(
                f"{placed} planned MAINTENANCE windows within horizon {horizon}; "
                "synthetic (GASS releases no downtime data)"
            ),
            random_seed=seed,
        )
    )
    session.flush()
    return {"windows": placed}
