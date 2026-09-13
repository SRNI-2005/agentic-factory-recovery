# tests/simulator/test_live.py
"""Live-day engine (spec 2026-09-13 §3/§7)."""
import pytest

pytestmark = pytest.mark.db


def _no_factory():
    return None


def _chunks(instance, queue=None):
    from coe.simulator.live import live_day

    items = list(live_day(instance, speed="instant",
                          llm_client_factory=_no_factory,
                          interrupt_queue=queue))
    return items


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
