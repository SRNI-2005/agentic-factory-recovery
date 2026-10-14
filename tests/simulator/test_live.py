# tests/simulator/test_live.py
"""Live-day engine (spec 2026-09-13 §3/§7)."""
import pytest

pytestmark = pytest.mark.db


def _no_factory():
    return None


def test_live_day_walks_completions_no_interruptions(sim_factory_instance):
    """Spec AC 1: zero queued disruptions -> zero solves, honest ticks."""
    from coe.simulator.live import live_day

    items = list(live_day(sim_factory_instance, speed="instant"))
    assert not any(str(i.get("event", "")).startswith("recovery")
                   for i in items)
    ticks = [i for i in items if i["event"] == "tick"]
    assert ticks, "no completion boundaries recorded"
    import math
    prev = -math.inf
    for i in ticks:
        assert i["t"] > prev          # strictly advancing clock
        prev = i["t"]
        assert isinstance(i["completed"], int)
        assert isinstance(i["stock"], dict)
    assert items[-1]["event"] == "day_end"
    # determinism: identical (start, empty queue) -> identical chunk seq
    keys1 = [(i["event"], i["t"]) for i in items]
    items2 = list(live_day(sim_factory_instance, speed="instant"))
    assert keys1 == [(i["event"], i["t"]) for i in items2]


def test_live_day_requires_baseline(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.simulator.live import LiveDayError, live_day

    with session_scope() as s:
        s.add(Instance(name="live-no-base", source_name="synthetic"))
    with pytest.raises(LiveDayError, match="baseline"):
        list(live_day("live-no-base", speed="instant"))


def test_live_interrupt_solves_commits_resumes(sim_factory_instance):
    """Spec AC §8.2: a pushed narrative resolves at the CURRENT clock; the
    recovered version commits and the board resumes (post-board ticks)."""
    from coe.agents.degraded_client import DegradedLLMClient
    from coe.simulator.live import InterruptQueue, live_day

    q = InterruptQueue()
    q.push("M3 gearbox seized, sparks everywhere")
    items = list(live_day(sim_factory_instance, speed="instant",
                          llm_client_factory=DegradedLLMClient,
                          interrupt_queue=q))
    kinds = [i["event"] for i in items]
    assert kinds.count("recovery_start") == 1
    assert kinds.count("recovery") == 1
    solve_t = next(i["t"] for i in items if i["event"] == "recovery")
    first_tick = next(i["t"] for i in items if i["event"] == "tick")
    # the walk resolves at the CURRENT clock (§8.2); queued before any tick
    # advances, that is start_clock=0 — at or before the first boundary
    assert solve_t <= first_tick, "interrupt fires at the FIRST boundary"
    post = [i for i in items if i["event"] == "tick" and i["t"] > solve_t]
    assert post, "board must resume with ticks after the solve"
    # exactly one RECOVERY version committed on the clone
    from sqlalchemy import text

    from coe.db.session import make_engine
    with make_engine().connect() as c:
        rec_n, base_n = c.execute(text(
            "SELECT SUM(CASE WHEN sv.schedule_type='RECOVERY' THEN 1 ELSE 0 END), "
            "       SUM(CASE WHEN sv.schedule_type='BASELINE' THEN 1 ELSE 0 END) "
            "FROM schedule_versions sv JOIN instances i ON i.id=sv.instance_id "
            "WHERE i.name = :n"), {"n": sim_factory_instance}).fetchone()
    assert base_n >= 1 and rec_n == 1
