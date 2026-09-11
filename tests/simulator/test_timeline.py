"""Timeline schema: validation cases (spec §2/§5)."""
import pytest
from pydantic import ValidationError


def _write(tmp_path, data):
    import json
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data))
    return str(p)


BASE = {
    "name": "demo_day_01", "seed": 42, "horizon_days": 1,
    "events": [
        {"t": 380, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3", "estimated_downtime": 120,
         "agentic": True},
        {"t": 500, "kind": "WORKER", "event_type": "WORKER_ABSENT",
         "worker_id": "W3", "duration": 240},
        {"t": 900, "kind": "MATERIAL", "event_type": "MATERIAL_SHORTAGE",
         "sku": "MAT-001"},
        {"t": 1000, "kind": "NARRATIVE", "text": "M3 is back online",
         "at": 1000},
    ],
}


def test_load_happy_path(tmp_path):
    from coe.simulator.timeline import load_timeline
    tl = load_timeline(_write(tmp_path, BASE))
    assert tl.name == "demo_day_01" and tl.seed == 42
    assert [e.t for e in tl.events] == [380, 500, 900, 1000]
    assert tl.events[0].resource_kind == "MACHINE"
    assert tl.events[3].kind == "NARRATIVE" and tl.events[3].text


def test_per_kind_field_exclusivity():
    from coe.simulator.timeline import TimelineEvent
    with pytest.raises(ValidationError):
        TimelineEvent(**BASE["events"][0] | {"worker_id": "W1"})
    with pytest.raises(ValidationError):
        TimelineEvent(**BASE["events"][2] | {"machine_id": "M1"})


def test_non_monotonic_rejected(tmp_path):
    from coe.simulator.timeline import TimelineError, load_timeline
    bad = dict(BASE, events=[dict(BASE["events"][1]),
                             dict(BASE["events"][0])])
    with pytest.raises(TimelineError, match="monotonic"):
        load_timeline(_write(tmp_path, bad))


def test_horizon_guard(tmp_path):
    from coe.simulator.timeline import TimelineError, load_timeline
    with pytest.raises(TimelineError, match="SIMULATE_MAX_HORIZON_DAYS"):
        load_timeline(_write(tmp_path, dict(BASE, horizon_days=8)))
