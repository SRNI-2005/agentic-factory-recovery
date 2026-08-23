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
    assert time.monotonic() - t0 < 5.0
    assert sol["status"] in ("OPTIMAL", "FEASIBLE", "INFEASIBLE")


def test_normalized_objective_is_ratio():
    p = _fx("deadline_tardiness")
    p["config"]["normalize_objectives"] = True
    sol = solve(p)
    # horizon = 80 (processing dominates); obj = (makespan + tardiness)/H
    assert sol["objective_value"] == pytest.approx(110.0 / 80.0)


def test_span_admits_three_transition_setup_stack():
    p = _fx("worker_no_overlap")
    # rebuild into a 3-job / 1-machine stack with all-pairs setups of 20
    import copy
    q = copy.deepcopy(p)
    q["machines"] = ["M0"]
    fams = ["A", "B", "C"]
    for i, j in enumerate(q["jobs"][:3]):
        j["operations"][0]["alternatives"] = [
            {"machine_id": "M0", "processing_time": 5, "workers": {}}]
        j["family_id"] = fams[i]
    rows = []
    for a in fams:
        for b in fams:
            if a != b:
                rows.append({"machine_id": "M0", "from_family": a,
                             "to_family": b, "duration": 20})
    q["jobs"] = q["jobs"][:3]
    q["setup_times"] = rows
    sol = solve(q)
    assert sol["status"] in ("OPTIMAL", "FEASIBLE"), sol["status"]
