"""§6.1 pure applier: documented transforms, last-wins, determinism."""
import json

import pytest


def _payload():
    return {
        "instance_id": "p3", "schedule_type": "RECOVERY",
        "parent_version_id": 7,
        "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                   "normalize_objectives": True, "random_seed": 42,
                   "num_search_workers": 8},
        "machines": ["M1"], "failed_machines": [],
        "machine_initial_families": {}, "warnings": [],
        "jobs": [
            {"job_id": "J-A", "family_id": None, "release_time": 0,
             "deadline": 50, "priority": 1,
             "operations": [
                 {"operation_id": "J-A-O1", "sequence": 1,
                  "status": "PENDING", "materials": [],
                  "alternatives": [], "frozen": None},
                 {"operation_id": "J-A-O2", "sequence": 2,
                  "status": "COMPLETED", "materials": [],
                  "alternatives": [],
                  "frozen": {"machine_id": "M1", "worker_id": None,
                             "start": 0, "end": 5}}]},
            {"job_id": "J-B", "family_id": None, "release_time": 0,
             "deadline": 90, "priority": 3,
             "operations": [
                 {"operation_id": "J-B-O1", "sequence": 1,
                  "status": "PENDING", "materials": [],
                  "alternatives": [], "frozen": None}]},
        ],
        "machine_downtime": [],
        "materials": [{"sku": "MAT-X", "capacity": 5}],
        "material_receipts": [
            {"sku": "MAT-Y", "quantity": 10, "available_at": 400}],
        "worker_unavailability": [], "setup_times": [],
        "blocked_operations": [], "suspended_jobs": [],
    }


def test_defer_raises_release():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                       "release_offset": 40}, "round": 1}])
    assert p["jobs"][0]["release_time"] == 40
    assert p["warnings"][0]["type"] == "STRATEGY_APPLIED"
    assert p["warnings"][0]["field_changed"] == "release_time"


def test_suspend_transforms_only_pending_ops():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "SUSPEND_JOB", "job_id": "J-A"},
         "round": 1}])
    ja = p["jobs"][0]
    assert ja["operations"][0]["status"] == "BLOCKED"
    assert ja["operations"][0]["alternatives"] == []
    assert ja["operations"][1]["status"] == "COMPLETED"       # history kept
    assert ja["operations"][1]["frozen"] is not None
    assert p["blocked_operations"] == [
        {"operation_id": "J-A-O1", "reason": "JOB_SUSPENDED",
         "material_sku": None}]
    assert p["suspended_jobs"] == ["J-A"]
    assert p["warnings"][0]["field_changed"] == "suspended_jobs"


def test_expedite_keeps_receipt_sort_and_marks_source():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "EXPEDITE_MATERIAL", "material_sku": "MAT-X",
                       "quantity": 20, "available_at": 120}, "round": 1}])
    rs = p["material_receipts"]
    assert [(r["sku"], r["available_at"]) for r in rs] == \
        [("MAT-X", 120), ("MAT-Y", 400)]
    assert rs[0]["source"] == "strategy_agent"


def test_weight_preset_updates_config():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "WEIGHT_PRESET", "alpha": 0.25, "beta": 2},
         "round": 2}])
    assert p["config"]["alpha"] == 0.25
    assert p["config"]["beta"] == 2.0


def test_tardiness_returns_explicit_map_not_direct_mutation():
    from coe.agents.applier import apply_candidates

    p, explicit = apply_candidates(_payload(), [
        {"candidate": {"type": "TARDINESS_WEIGHT", "job_id": "J-B",
                       "weight": 0.5}, "round": 1}])
    assert explicit == {"J-B": 0.5}
    assert "job_tardiness_weights" not in p    # merge is the manager's job


def test_last_wins_with_full_audit_trail():
    # §6.1: later candidates targeting the same job REPLACE earlier effects
    # (the offset substitutes, it does not accumulate) — and every
    # application is still recorded.
    from coe.agents.applier import apply_candidates

    p, explicit = apply_candidates(_payload(), [
        {"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                       "release_offset": 10}, "round": 1},
        {"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                       "release_offset": 99}, "round": 2}])
    assert p["jobs"][0]["release_time"] == 99      # replaced, not stacked
    applied = [w for w in p["warnings"]
               if w["type"] == "STRATEGY_APPLIED"]
    assert len(applied) == 2                       # full audit trail
    assert applied[-1]["round"] == 2


def test_byte_determinism():
    from coe.agents.applier import apply_candidates

    cands = [{"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                            "release_offset": 12}, "round": 1},
             {"candidate": {"type": "TARDINESS_WEIGHT", "job_id": "J-B",
                            "weight": 2}, "round": 1}]
    a = apply_candidates(_payload(), cands)
    b = apply_candidates(_payload(), cands)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
