"""Tier 2b individual-constraint tests over hand-crafted fixture payloads."""
import json
from pathlib import Path

import pytest

from coe.solver.engine import solve

FIX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return json.loads((FIX / f"{name}.json").read_text())


def _one(payload):
    sol = solve(payload)
    assert sol["status"] in ("OPTIMAL", "FEASIBLE")
    live = [a for a in sol["assignments"] if not a["is_frozen"]]
    assert len(live) == 1
    return sol, live[0]


def test_release_time_honored():
    _, a = _one(_fx("release_time"))
    assert (a["start"], a["end"]) == (100, 110)


def test_machine_downtime_avoided():
    _, a = _one(_fx("machine_downtime"))
    assert (a["start"], a["end"]) == (0, 10)      # fits before [50, 150)


def test_frozen_respected():
    sol = solve(_fx("frozen_respected"))
    frozen = [x for x in sol["assignments"] if x["is_frozen"]]
    live = [x for x in sol["assignments"] if not x["is_frozen"]]
    assert len(frozen) == 1 and len(live) == 1
    assert (frozen[0]["start"], frozen[0]["end"]) == (0, 15)
    assert live[0]["machine_id"] == "M1"
    assert live[0]["start"] >= 15


def test_deadline_tardiness_computed():
    sol, a = _one(_fx("deadline_tardiness"))
    assert a["end"] == 80
    assert sol["makespan"] == 80
    assert sol["total_tardiness"] == 30
    assert sol["objective_value"] == pytest.approx(110.0)   # normalization off


def test_null_deadline_zero_tardiness():
    sol, _ = _one(_fx("null_deadline"))
    assert sol["total_tardiness"] == 0


def test_empty_pending_short_circuits():
    sol = solve(_fx("empty_pending"))
    assert sol["status"] == "OPTIMAL"
    assert sol["makespan"] == 15
    assert sol["total_tardiness"] == 5          # frozen end 15 vs deadline 10
    assert len(sol["assignments"]) == 1
    assert sol["assignments"][0]["is_frozen"]


def test_blocked_operations_never_scheduled():
    p = _fx("release_time")
    p["jobs"][0]["operations"].append({
        "operation_id": "J1-O2", "sequence": 2, "status": "BLOCKED",
        "alternatives": [], "frozen": None})
    sol = solve(p)
    assert all(a["operation_id"] != "J1-O2" for a in sol["assignments"])


def test_worker_unavailability_delays_start():
    _, a = _one(_fx("worker_unavailable"))
    assert a["worker_id"] == "W1"
    assert (a["start"], a["end"]) == (100, 110)


def test_no_worker_fallback_uses_machine_duration():
    _, a = _one(_fx("no_worker_fallback"))
    assert a["worker_id"] is None
    assert (a["start"], a["end"]) == (0, 10)


def test_worker_no_overlap_serializes():
    sol = solve(_fx("worker_no_overlap"))
    assert sorted(a["start"] for a in sol["assignments"]) == [0, 5]
    assert sorted(a["end"] for a in sol["assignments"]) == [5, 10]
    workers = {a["worker_id"] for a in sol["assignments"]}
    assert workers == {"W1"}


def test_infeasible_reports_without_live_assignments():
    p = _fx("release_time")
    p["machine_downtime"] = [{"machine_id": "M0", "from": 0,
                              "until": 100000, "reason": "MAINTENANCE"}]
    sol = solve(p)
    assert sol["status"] == "INFEASIBLE"
    assert [a for a in sol["assignments"] if not a["is_frozen"]] == []


def test_invalid_weights_rejected():
    p = _fx("release_time")
    p["config"]["alpha"] = -1.0
    with pytest.raises(ValueError):
        solve(p)


def test_zero_sum_weights_rejected():
    p = _fx("release_time")
    p["config"]["alpha"] = 0.0
    p["config"]["beta"] = 0.0
    with pytest.raises(ValueError):
        solve(p)
