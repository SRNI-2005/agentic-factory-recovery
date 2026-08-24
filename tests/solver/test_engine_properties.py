"""Pure-solver property tests (spec §11 Tier 4, DB-free subset)."""
import json
import time
from pathlib import Path

import pytest

from coe.solver.engine import solve

from tests.solver.test_engine_constraints import _fx

ENGINE_SRC = (
    Path(__file__).resolve().parents[2] / "coe" / "solver" / "engine.py"
)


def test_no_database_imports_allowed():
    assert "coe.db" not in ENGINE_SRC.read_text()


def test_deterministic_bytes_same_payload():
    p = _fx("worker_no_overlap")
    # DEVIATION from brief listing: solve_duration_seconds is wall-clock
    # instrumentation required by the committer (spec §4); determinism
    # pins every decision field, so the timing key is excluded.
    a = json.dumps({k: v for k, v in solve(p).items()
                    if k != "solve_duration_seconds"}, sort_keys=True)
    b = json.dumps({k: v for k, v in solve(p).items()
                    if k != "solve_duration_seconds"}, sort_keys=True)
    assert a == b


def test_time_limit_respected_and_status_valid():
    p = _fx("deadline_tardiness")
    p["config"]["time_limit_seconds"] = 0.001
    t0 = time.monotonic()
    sol = solve(p)
    assert time.monotonic() - t0 < 2.0
    assert sol["status"] in ("OPTIMAL", "FEASIBLE", "INFEASIBLE")


def test_normalized_objective_is_ratio():
    p = _fx("deadline_tardiness")
    p["config"]["normalize_objectives"] = True
    sol = solve(p)
    # horizon = 80 (processing dominates); obj = (makespan + tardiness)/H
    assert sol["objective_value"] == pytest.approx(110.0 / 80.0)


def test_span_admits_three_transition_setup_stack():
    """3 single-op jobs, one machine, all-pairs setups of 20: legal optimum
    ends at t=55; the pre-fix span (35) clipped it to false INFEASIBLE."""
    import copy

    q = copy.deepcopy(_fx("worker_no_overlap"))
    q["machines"] = ["M0"]
    third = copy.deepcopy(q["jobs"][0])
    third["job_id"] = "J3"
    third["operations"][0]["operation_id"] = "J3-O1"
    q["jobs"] = q["jobs"][:2] + [third]
    for i, j in enumerate(q["jobs"]):
        j["operations"][0]["alternatives"] = [
            {"machine_id": "M0", "processing_time": 5, "workers": {}}]
        j["family_id"] = ["A", "B", "C"][i]
        j["operations"][0]["frozen"] = None
    rows = [{"machine_id": "M0", "from_family": a, "to_family": b,
             "duration": 20}
            for a in ("A", "B", "C") for b in ("A", "B", "C") if a != b]
    q["setup_times"] = rows
    sol = solve(q)
    assert sol["status"] == "OPTIMAL"
    assert sol["makespan"] == 55


def test_hint_avoids_frozen_occupancy():
    """The greedy warm start must seed busy lists from frozen echoes, else
    it hints live placements into already-executed time (here M1 [0,15))."""
    p = _fx("frozen_respected")
    from coe.solver.engine import _combos, _greedy_plan

    pending = [(j, o) for j in p["jobs"]
               for o in j["operations"] if o["status"] == "PENDING"]
    frozen = [(j, o) for j in p["jobs"] for o in j["operations"]
              if o["status"] != "PENDING" and o.get("frozen")]
    combos = {o["operation_id"]: [
        {"machine": m, "worker": w, "dur": d}
        for m, w, d in _combos(o)] for _, o in pending}
    placements, _ = _greedy_plan(p, pending, combos, frozen)
    live = [h for h in placements if not h.get("is_frozen")]
    assert live, "hint produced no live placements"
    for h in live:
        assert (h["machine_id"] != "M1"
                or h["start"] >= 15), h
