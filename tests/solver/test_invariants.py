"""Pure invariant checks (spec §6.2)."""
from coe.solver.invariants import check_solution


def _payload():
    return {
        "machines": ["M0", "M1"],
        "blocked_operations": [{"operation_id": "J9-O1",
                                "reason": "NO_CAPABLE_MACHINES",
                                "material_sku": None}],
        "jobs": [
            {"job_id": "J1", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [
                 {"operation_id": "J1-O1", "sequence": 1,
                  "status": "COMPLETED", "alternatives": [],
                  "frozen": {"machine_id": "M0", "worker_id": "W1",
                             "start": 0, "end": 10}},
                 {"operation_id": "J1-O2", "sequence": 2,
                  "status": "PENDING",
                  "alternatives": [{"machine_id": "M1",
                                    "processing_time": 5,
                                    "workers": {"W1": 5}}],
                  "frozen": None},
             ]},
            {"job_id": "J9", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [
                 {"operation_id": "J9-O1", "sequence": 1,
                  "status": "BLOCKED", "alternatives": [], "frozen": None},
             ]},
        ],
    }


def _solution():
    return {"assignments": [
        {"operation_id": "J1-O1", "job_id": "J1", "machine_id": "M0",
         "worker_id": "W1", "start": 0, "end": 10, "processing_time": 10,
         "setup_time": 0, "is_frozen": True},
        {"operation_id": "J1-O2", "job_id": "J1", "machine_id": "M1",
         "worker_id": "W1", "start": 10, "end": 15, "processing_time": 5,
         "setup_time": 0, "is_frozen": False},
    ]}


def test_clean_solution_passes():
    assert check_solution(_payload(), _solution()) == []


def test_frozen_drift_detected():
    sol = _solution()
    sol["assignments"][0]["start"] = 1
    assert any("frozen drift" in v for v in check_solution(_payload(), sol))


def test_frozen_missing_detected():
    sol = _solution()
    sol["assignments"] = sol["assignments"][1:]
    assert any("missing" in v for v in check_solution(_payload(), sol))


def test_stripped_machine_detected():
    sol = _solution()
    sol["assignments"][1]["machine_id"] = "M99"
    assert any("unavailable machine" in v
               for v in check_solution(_payload(), sol))


def test_worker_and_duration_mismatch_detected():
    p = _payload()
    sol = _solution()
    sol["assignments"][1]["worker_id"] = "W2"
    assert any("worker W2 ineligible" in v for v in check_solution(p, sol))
    sol2 = _solution()
    sol2["assignments"][1]["processing_time"] = 7
    assert any("duration" in v for v in check_solution(p, sol2))


def test_precedence_violation_detected():
    sol = _solution()
    a = sol["assignments"][1]
    a["start"], a["end"] = 0, 5          # starts before frozen predecessor ends
    msgs = check_solution(_payload(), sol)
    assert any("precedence" in v for v in msgs)


def test_duration_arithmetic_violation_detected():
    sol = _solution()
    sol["assignments"][1]["end"] = 16          # J1-O2: start 10 + 5 + 0 != 16
    msgs = check_solution(_payload(), sol)
    assert any("duration arithmetic" in v for v in msgs)


def test_blocked_operation_scheduled_detected():
    sol = _solution()
    sol["assignments"].append({
        "operation_id": "J9-O1", "job_id": "J9", "machine_id": "M0",
        "worker_id": None, "start": 20, "end": 25, "processing_time": 5,
        "setup_time": 0, "is_frozen": False})
    assert any("blocked" in v.lower()
               for v in check_solution(_payload(), sol))


def test_duplicate_assignment_detected():
    sol = _solution()
    sol["assignments"].append(dict(sol["assignments"][1]))
    assert any("duplicate" in v for v in check_solution(_payload(), sol))


def test_frozen_on_stripped_machine_is_not_a_violation():
    p = _payload()
    p["machines"] = ["M1"]                 # M0 stripped post-failure
    sol = _solution()
    assert check_solution(p, sol) == []
    # but a LIVE op on the stripped machine still violates:
    sol["assignments"][1]["machine_id"] = "M0"
    assert any("unavailable machine" in v
               for v in check_solution(p, sol))
