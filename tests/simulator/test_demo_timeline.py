import pytest

pytestmark = pytest.mark.db


def test_shipped_timeline_loads_and_is_deterministic():
    from coe.simulator.timeline import load_timeline
    tl = load_timeline("data/timelines/demo_day_01.json")
    ts = [e.t for e in tl.events]
    assert ts == sorted(ts) and len(ts) >= 8
    assert all(b >= a for a, b in zip(ts, ts[1:])), "non-decreasing"
