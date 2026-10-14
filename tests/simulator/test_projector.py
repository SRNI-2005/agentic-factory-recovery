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


def _active_version(session, instance_id):
    """Latest active (non-rolled-back, solved) version — same query shape
    as projector.py's version lookup."""
    from coe.db.models.schedule import ScheduleVersion

    return (session.query(ScheduleVersion)
            .filter(ScheduleVersion.instance_id == instance_id,
                    ScheduleVersion.solver_status.in_(("OPTIMAL",
                                                       "FEASIBLE")),
                    ScheduleVersion.rolled_back.is_(False))
            .order_by(ScheduleVersion.version_number.desc(),
                      ScheduleVersion.id.desc()).first())


def _baseline_makespan(instance_name: str) -> int:
    """Makespan of the instance's latest active version (ORM)."""
    from sqlalchemy.orm import Session

    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine

    with Session(make_engine()) as s:
        inst = (s.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = _active_version(s, inst.id)
        assert version is not None, "no active version"
        return version.makespan


def _committed_entries(session, instance_id):
    """Committed entries of the active version, joined with op/job names
    and machines (explicit ORDER BY for determinism)."""
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.schedule import ScheduleEntry

    return (session.query(ScheduleEntry, Operation, Job, Machine)
            .join(Operation, Operation.id == ScheduleEntry.operation_id)
            .join(Job, Job.id == Operation.job_id)
            .join(Machine, Machine.id == ScheduleEntry.machine_id)
            .filter(ScheduleEntry.instance_id == instance_id,
                    ScheduleEntry.version_id ==
                    _active_version(session, instance_id).id)
            .order_by(ScheduleEntry.start_time, ScheduleEntry.end_time,
                      ScheduleEntry.id).all())


def _recovery_caps(session, inst, t: int) -> dict[str, int]:
    """Payload builder's deducted capacity dict at clock t (pure DB→dict)."""
    from coe.solver.payload_builder import build_payload

    rec = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                        time_limit_seconds=1,
                        schedule_type="RECOVERY", now=t)
    return {m["sku"]: m["capacity"] for m in rec["materials"]}


def test_classification_boundaries(sim_factory_instance):
    from coe.simulator.projector import project_day

    clone = sim_factory_instance
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
        entries = _committed_entries(s, inst_row.id)
        total_entries = len(entries)
        closer = entries[-1][0]
        assert closer.end_time == baseline_makespan
        assert len(first_ops) == total_entries
    assert ds.in_progress == []
    assert ds.clock == baseline_makespan
    assert ds.feed_line().startswith(f"t={baseline_makespan} ")


def test_start_boundary_is_in_progress(sim_factory_instance):
    """Boundary pin: at exactly an entry's start_time the op is IN_PROGRESS
    (start_time <= t < end_time), never COMPLETED."""
    from coe.simulator.projector import project_day

    clone = sim_factory_instance
    with _sessions(clone) as (s, inst_row):
        entry, op, job, _machine = _committed_entries(s, inst_row.id)[0]
        ds = project_day(s, instance_name=clone, t=entry.start_time)
        name = f"{job.name}-O{op.sequence_number}"
        assert name in ds.in_progress
        assert name not in ds.completed_ops


def test_effective_stock_matches_builder_arithmetic(sim_factory_instance):
    """Cross-check: project_day's effective stock at t equals the payload
    builder's deducted capacity at the same clock (Task 3 invariants).

    Boundary-capable: clocks are derived from the data — actual entry
    start_time values of the active version (one mid-schedule, one equal to
    max(start_time) < makespan) plus the fixed t=200 — so any
    `start_time < t` vs `<= t` drift between projector and builder fails
    loudly on the exact boundary."""
    from coe.simulator.projector import project_day

    clone = sim_factory_instance

    with _sessions(clone) as (s, inst_row):
        entries = _committed_entries(s, inst_row.id)
        starts = sorted({e.start_time for e, _o, _j, _m in entries})
        makespan = _baseline_makespan(clone)
        assert starts, "committed schedule must have entries"

        # one mid-schedule start + one riding on max(start) < makespan
        t_mid = starts[len(starts) // 2]
        t_edge = max(t for t in starts if t < makespan)
        assert t_edge < makespan

        clocks = [200, t_mid, t_edge]
        for t in clocks:
            ds = project_day(s, instance_name=clone, t=t)
            assert ds.completed_ops or ds.in_progress, (
                f"t={t} must have playback activity")

            cap = _recovery_caps(s, inst_row, t)
            assert ds.effective_stock == cap, (
                f"projector/builder drift at boundary clock t={t}")

            # receipts already arrived by t are folded into stock, not rows
            from coe.solver.payload_builder import build_payload

            rec = build_payload(s, instance_row=inst_row, alpha=1.0,
                                beta=1.0, time_limit_seconds=1,
                                schedule_type="RECOVERY", now=t)
            assert all(r["available_at"] > t
                       for r in rec["material_receipts"]), (
                f"pre-clock receipt not folded at t={t}")


def test_failed_machine_playback_is_physical(demo_scenario):
    """Playback = physical truth: setting a machine's CURRENT status to
    FAILED must NOT retract consumption of entries that already started on
    it — the bars were physically consumed pre-failure (the recovery
    builder re-classifies for solving; the projector is the audit rail)."""
    from coe.db.models.fjsp import Machine
    from coe.simulator.projector import project_day

    clone = _forked_baseline_clone()
    T = 200

    with _sessions(clone) as (s, inst_row):
        before = project_day(s, instance_name=clone, t=T)
        assert before.completed_ops or before.in_progress

        # pick a machine with at least one committed entry started by T
        victim_id = None
        for entry, _op, _job, machine in _committed_entries(s, inst_row.id):
            if entry.start_time <= T:
                victim_id = machine.id
                break
        assert victim_id is not None, "no started entry by T"

        (s.query(Machine)
         .filter(Machine.instance_id == inst_row.id,
                 Machine.id == victim_id)
         .update({"status": "FAILED"}, synchronize_session=False))
        s.commit()

        after = project_day(s, instance_name=clone, t=T)

    assert after.effective_stock == before.effective_stock, (
        "failed machine's started entries must still count as consumed")
    assert after.completed_ops == before.completed_ops
    assert after.in_progress == before.in_progress
