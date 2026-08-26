"""Read-only dashboard data adapters.

Thin wrappers over coe.services; every function returns plain dicts/lists
suitable for Streamlit.  No mutations.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from coe.services import configure, instances, schedules


def list_instances(session: Session) -> list[dict]:
    """Instance list with fork-lineage parent — not instance-scoped."""
    return [r.model_dump() for r in instances.list_instances(session)]


def active_schedule(session: Session, instance_id: int) -> dict | None:
    """Canonical active schedule from the database view."""
    gantt = schedules.active(session, instance_id)
    if gantt is None:
        return None
    return {"version": gantt.version.model_dump(), "entries": gantt.entries}


def schedule_versions(session: Session, instance_id: int) -> list[dict]:
    return schedules.versions(session, instance_id)


def materials_overview(session: Session, instance_id: int) -> list[dict]:
    return [r.model_dump() for r in configure.materials(session, instance_id)]


def workers_overview(session: Session, instance_id: int) -> list[dict]:
    return [r.model_dump() for r in configure.workers(session, instance_id)]


def machines_overview(session: Session, instance_id: int) -> list[dict]:
    return [r.model_dump() for r in configure.machines(session, instance_id)]


def jobs_overview(session: Session, instance_id: int) -> list[dict]:
    return [r.model_dump() for r in configure.jobs(session, instance_id)]


def jobs_per_day(session: Session, instance_id: int,
                 day_length: int = 1440) -> dict[int, list[str]]:
    return configure.jobs_per_day(session, instance_id, day_length)


def recovery_runs(session: Session, instance_id: int) -> list[dict]:
    return schedules.recovery_runs(session, instance_id)


def fidelity_report(path: Path = Path("benchmark_report.json")) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())
