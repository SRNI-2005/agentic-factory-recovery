# tests/simulator/test_projector.py
"""Task 5: day projector — classification boundaries + builder arithmetic."""
import subprocess
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.db


@contextmanager
def _sessions(instance_name: str = "factory_demo_01"):
    from sqlalchemy.orm import Session

    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine

    with Session(make_engine()) as s:
        inst = s.query(Instance).filter(
            Instance.name == instance_name).one()
        yield s, inst


def _forked_baseline_clone() -> str:
    """Fork factory_demo_01 and commit a baseline on the clone (isolation:
    read-only projector tests must not stack versions on the canonical
    instance)."""
    from sqlalchemy.orm import Session

    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.services.fork import fork_instance

    with Session(make_engine()) as s:
        source = (s.query(Instance)
                  .filter(Instance.name == "factory_demo_01").one())
        forked = fork_instance(s, source)
        s.commit()
        clone_name = forked.name
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", clone_name],
        check=True, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    return clone_name


def _baseline_makespan(instance_name: str) -> int:
    """Makespan of the instance's latest active version."""
    from sqlalchemy import text
    from coe.db.session import make_engine

    with make_engine().begin() as c:
        return c.execute(text(
            "SELECT sv.makespan FROM schedule_versions sv "
            "JOIN instances i ON i.id = sv.instance_id "
            "WHERE i.name = :name "
            "AND sv.rolled_back = false "
            "AND sv.solver_status IN ('OPTIMAL', 'FEASIBLE') "
            "ORDER BY sv.version_number DESC, sv.id DESC "
            "LIMIT 1"), {"name": instance_name}).scalar_one()


def test_classification_boundaries(demo_scenario):
    from coe.simulator.projector import project_day

    clone = _forked_baseline_clone()
    with _sessions(clone) as (s, inst_row):
        ds0 = project_day(s, instance_name=clone, t=0)
    assert ds0.completed_ops == []
    assert ds0.clock == 0

    baseline_makespan = _baseline_makespan(clone)
    with _sessions(clone) as (s, inst_row):
        ds = project_day(s, instance_name=clone, t=baseline_makespan)
        first_ops = ds.completed_ops
        assert first_ops, "at makespan, schedule is fully consumed"
        # explicit boundary: end == t is COMPLETED — the closer of the
        # version's entries rides exactly on the makespan and must land in
        # completed_ops (end_time <= t), not in_progress.
        from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

        version = (s.query(ScheduleVersion)
                   .filter(ScheduleVersion.instance_id == inst_row.id,
                           ScheduleVersion.solver_status.in_(
                               ("OPTIMAL", "FEASIBLE")),
                           ScheduleVersion.rolled_back.is_(False))
                   .order_by(ScheduleVersion.version_number.desc(),
                             ScheduleVersion.id.desc()).first())
        total_entries = (s.query(ScheduleEntry)
                         .filter(ScheduleEntry.version_id == version.id)
                         .count())
        closer = (s.query(ScheduleEntry)
                  .filter(ScheduleEntry.version_id == version.id)
                  .order_by(ScheduleEntry.end_time.desc(),
                            ScheduleEntry.id.desc()).first())
        assert closer.end_time == baseline_makespan
        assert len(first_ops) == total_entries
    assert ds.in_progress == []
    assert ds.clock == baseline_makespan
    assert ds.feed_line().startswith(f"t={baseline_makespan} ")


def test_effective_stock_matches_builder_arithmetic(demo_scenario):
    """Cross-check: project_day's effective stock at t equals the payload
    builder's deducted capacity at the same clock (Task 3 invariants)."""
    from coe.db.session import make_engine
    from coe.solver.payload_builder import build_payload
    from coe.simulator.projector import project_day

    clone = _forked_baseline_clone()

    T = 200
    with _sessions(clone) as (s, inst):
        ds = project_day(s, instance_name=clone, t=T)
        assert ds.completed_ops, "t=200 must have at least one completion"

        rec = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                            time_limit_seconds=1,
                            schedule_type="RECOVERY", now=T)
        cap = {m["sku"]: m["capacity"] for m in rec["materials"]}

        assert ds.effective_stock == cap

        # receipts already arrived by T are folded into stock, not rows
        assert all(r["available_at"] > T for r in rec["material_receipts"])
