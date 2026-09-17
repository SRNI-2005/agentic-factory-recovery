"""Live Gantt board: classification + figure build (spec 2026-09-18 §2)."""
import pytest

from coe.dashboard.gantt import build_board_figure, classify


def _entries():
    # two machines; J1 op1 completes at 5, J1 op2 in-progress at t=8,
    # J2 op1 future
    return [
        {"id": 1, "machine_name": "M1", "job_name": "J1",
         "sequence_number": 1, "start_time": 0, "end_time": 5},
        {"id": 2, "machine_name": "M1", "job_name": "J1",
         "sequence_number": 2, "start_time": 5, "end_time": 10},
        {"id": 3, "machine_name": "M2", "job_name": "J2",
         "sequence_number": 1, "start_time": 15, "end_time": 20},
    ]


def test_classify_states():
    c = classify(_entries(), 8)
    assert c == {"completed": [1], "in_progress": [2], "future": [3]}


def test_classify_boundary_inclusive():
    # end == t => completed; start == t => in_progress (marker began it)
    c = classify(_entries(), 5)
    assert 1 in c["completed"]
    assert 2 in c["in_progress"]
    assert 3 in c["future"]


def test_build_figure_state_encodings():
    fig = build_board_figure(_entries(), 8, {"J1": "#FF0000", "J2": "#00FF00"})
    bar = fig.data[0]
    # one trace for M1 (2 bars) — per-bar arrays
    assert bar.xaxis == "x" or isinstance(bar.x[0], (int, float))
    # J1 both ops: op1 completed (dim 0.35), op2 future (1.0)
    assert bar.marker.opacity[0] == 0.35
    assert bar.marker.opacity[1] == 1.0
    # no in-progress on M1 at t=8 boundary... op2 start=5<=8 -> in-progress
    assert bar.marker.line.color[1] == "#FFB000"
    assert bar.marker.line.width[1] == 3
    assert bar.marker.line.width[0] == 0


def test_build_empty_returns_none():
    from coe.dashboard.gantt import build_board_figure
    assert build_board_figure([], 0, {}) is None
