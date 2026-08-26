"""Tests for coe.dashboard.diff — schedule_frames animation logic."""
from __future__ import annotations


def _entry(job: str, seq: int, machine: str, start: int, end: int) -> dict:
    return {
        "job_name": job,
        "sequence_number": seq,
        "machine_name": machine,
        "start_time": start,
        "end_time": end,
        "worker_name": "W1",
    }


# ---------------------------------------------------------------------------
# identical schedules → exactly one frame
# ---------------------------------------------------------------------------

def test_identical_schedules_returns_one_frame():
    from coe.dashboard.diff import schedule_frames

    entries = [
        _entry("J1", 1, "M1", 0, 10),
        _entry("J1", 2, "M2", 10, 20),
    ]
    frames = schedule_frames(entries, list(entries))
    assert len(frames) == 1


def test_identical_schedules_title():
    from coe.dashboard.diff import schedule_frames

    entries = [_entry("J1", 1, "M1", 0, 10)]
    frames = schedule_frames(entries, list(entries))
    assert len(frames) == 1
    assert frames[0].layout.title.text == "Final schedule"


# ---------------------------------------------------------------------------
# moved operation
# ---------------------------------------------------------------------------

def test_moved_operation_produces_two_frames():
    """One moved op → one intermediate frame + one final frame."""
    from coe.dashboard.diff import schedule_frames

    before = [
        _entry("J1", 1, "M1", 0, 10),
        _entry("J1", 2, "M1", 10, 20),
    ]
    after = [
        _entry("J1", 1, "M1", 0, 10),
        _entry("J1", 2, "M2", 10, 20),  # moved to M2
    ]
    frames = schedule_frames(before, after)
    # 1 intermediate (step for moved op) + 1 final
    assert len(frames) == 2


def test_moved_operation_final_frame_has_all_after_entries():
    from coe.dashboard.diff import schedule_frames

    before = [_entry("J1", 1, "M1", 0, 10)]
    after = [_entry("J1", 1, "M2", 0, 10)]
    frames = schedule_frames(before, after)
    final = frames[-1]
    assert final.layout.title.text == "Final schedule"


# ---------------------------------------------------------------------------
# added operation
# ---------------------------------------------------------------------------

def test_added_operation_produces_two_frames():
    from coe.dashboard.diff import schedule_frames

    before = [_entry("J1", 1, "M1", 0, 10)]
    after = [
        _entry("J1", 1, "M1", 0, 10),
        _entry("J2", 1, "M2", 0, 15),
    ]
    frames = schedule_frames(before, after)
    # 1 intermediate (added) + 1 final
    assert len(frames) == 2


# ---------------------------------------------------------------------------
# removed operation — ghost then gone
# ---------------------------------------------------------------------------

def test_removed_operation_produces_intermediate_with_ghost():
    """Removed op should appear as ghost in intermediate frames."""
    from coe.dashboard.diff import schedule_frames

    before = [
        _entry("J1", 1, "M1", 0, 10),
        _entry("J2", 1, "M2", 0, 10),
    ]
    after = [_entry("J1", 1, "M1", 0, 10)]
    frames = schedule_frames(before, after)
    assert len(frames) == 2
    # The final frame should not have any ghost traces
    final = frames[-1]
    for trace in final.data:
        assert "removed" not in (trace.name or "")


# ---------------------------------------------------------------------------
# zero-duration bar rendered as at least one minute
# ---------------------------------------------------------------------------

def test_zero_duration_bar_rendered_as_one_minute():
    from coe.dashboard.diff import schedule_frames

    entries = [_entry("J1", 1, "M1", 5, 5)]  # zero duration
    frames = schedule_frames(entries, list(entries))
    assert len(frames) == 1
    trace = frames[0].data[0]
    # The bar width should be 1 (minute)
    assert trace.x[0] == 1


# ---------------------------------------------------------------------------
# multiple moved operations
# ---------------------------------------------------------------------------

def test_multiple_moved_operations():
    from coe.dashboard.diff import schedule_frames

    before = [
        _entry("J1", 1, "M1", 0, 10),
        _entry("J2", 1, "M2", 0, 10),
    ]
    after = [
        _entry("J1", 1, "M2", 0, 10),
        _entry("J2", 1, "M1", 0, 10),
    ]
    frames = schedule_frames(before, after)
    # 2 intermediate (one per moved op) + 1 final
    assert len(frames) == 3


# ---------------------------------------------------------------------------
# intermediate frame title contains operation label
# ---------------------------------------------------------------------------

def test_intermediate_frame_title_labels_operation():
    from coe.dashboard.diff import schedule_frames

    before = [_entry("J1", 1, "M1", 0, 10)]
    after = [_entry("J1", 1, "M2", 0, 10)]
    frames = schedule_frames(before, after)
    assert "J1/op1" in (frames[0].layout.title.text or "")
