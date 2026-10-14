import pytest

pytestmark = [pytest.mark.db, pytest.mark.slow]


def _timeline():
    from coe.simulator.timeline import load_timeline
    return load_timeline("data/timelines/demo_day_01.json")


def test_shipped_timeline_loads_and_is_deterministic():
    tl = _timeline()
    assert tl.auto_recover is True   # default field, honored by the walk
    ts = [e.t for e in tl.events]
    assert ts == sorted(ts)
    assert all(b >= a for a, b in zip(ts, ts[1:])), "non-decreasing"


def test_shipped_arc_shape():
    """auto_recover amendment 2026-09-13: the t=230 narrative duplicated
    the t=200 structured SHORTAGE's auto solve — deleted. Final arc:
    [90 narrative, 200 structured, 300 structured, 330 structured,
    380 narrative] — 5 events."""
    tl = _timeline()
    events = tl.events
    assert len(events) == 5
    assert [(e.t, e.kind) for e in events] == [
        (90, "NARRATIVE"), (200, "MATERIAL"), (300, "MATERIAL"),
        (330, "WORKER"), (380, "NARRATIVE")]
    assert events[1].event_type == "MATERIAL_SHORTAGE"
    assert events[2].event_type == "MATERIAL_RESTOCK"
    assert events[3].event_type == "WORKER_ABSENT"


def test_shipped_events_fit_inside_schedulable_day(sim_factory_instance):
    """Disruptions after the baseline's makespan re-plan nothing (engine
    short-circuit on an empty pending set) — the shipped arc must stay
    comfortably inside the schedulable day (2026-09-13 demo-fix: a
    700-minute arc died with hollow instant solves past minute ~300).
    The makespan bound comes from the session's baseline-bearing clone
    (same scenario, seed 42 — same makespan as the live instance)."""
    tl = _timeline()
    makespan = _active_makespan_of(sim_factory_instance)
    assert makespan > 0
    assert max(e.t for e in tl.events) < makespan - 15, (
        max(e.t for e in tl.events), makespan)


def test_shipped_arc_single_machine_failure():
    """No double-failure of the same machine: an open-ended narrative
    outage merged with a finite structured window killed M3 forever and
    produced SOLVE_INFEASIBLE (auto-fix has no strategist to sacrifice
    jobs, P3 §3.1 exhaustion)."""
    tl = _timeline()
    machine_tokens = [e.text for e in tl.events
                      if e.kind == "NARRATIVE" and "M3" in e.text]
    assert len(machine_tokens) <= 1, machine_tokens


def _active_makespan_of(instance_name: str) -> int:
    from sqlalchemy import text

    from coe.db.session import make_engine

    with make_engine().connect() as c:
        return int(c.execute(text(
            "SELECT MAX(sv.makespan) FROM schedule_versions sv "
            "JOIN instances i ON i.id = sv.instance_id "
            "WHERE i.name = :n AND sv.rolled_back = false"),
            {"n": instance_name}).scalar() or 0)
