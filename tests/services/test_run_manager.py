import time

import pytest


def test_start_streams_nodes_and_terminal_recorded(clean_db, demo_scenario):
    from coe.services.recovery import RunManager

    def fake_runner(instance_name, *, trigger, narrative, **kw):
        yield {"node": "entry"}
        yield {"node": "translate"}
        yield {"status": "COMMITTED", "run_id": 777,
               "state_summary": {"solver_status": "FEASIBLE",
                                 "makespan": 42}}

    mgr = RunManager()
    token = mgr.start("factory_demo_01", "M1 down", runner=fake_runner)
    deadline = time.time() + 5
    while time.time() < deadline:
        items, idx = mgr.log(token).snapshot()
        if any("status" in i for i in items):
            break
        time.sleep(0.05)
    items, _ = mgr.log(token).snapshot()
    assert items[0] == {"node": "entry"}
    assert items[-1]["run_id"] == 777


def test_terminal_raw_state_object_is_summarized(clean_db, demo_scenario):
    """Fix round: a terminal carrying the raw pydantic state (the real
    execute_recovery_streaming shape) is stripped to JSON-safe
    {status, run_id, state_summary} before hitting the log."""
    import json
    from types import SimpleNamespace

    from coe.services.recovery import RunManager

    state = SimpleNamespace(solution={"status": "FEASIBLE",
                                      "makespan": 42},
                            committed_version_id=9)

    def fake_runner(instance_name, *, trigger, narrative, **kw):
        yield {"node": "entry"}
        yield {"status": "COMMITTED", "run_id": 5, "state": state}

    mgr = RunManager()
    token = mgr.start("factory_demo_01", "M1 down", runner=fake_runner)
    deadline = time.time() + 5
    while time.time() < deadline:
        items, _ = mgr.log(token).snapshot()
        if any("status" in i for i in items):
            break
        time.sleep(0.05)
    items, _ = mgr.log(token).snapshot()
    terminal = items[-1]
    assert terminal == {"status": "COMMITTED", "run_id": 5,
                        "state_summary": {
                            "solver_status": "FEASIBLE",
                            "makespan": 42,
                            "committed_version_id": 9}}
    assert json.loads(json.dumps(terminal)) == terminal   # SSE-serializable


def test_log_snapshot_after_index(clean_db):
    from coe.services.recovery import EventLog

    log = EventLog()
    for i in range(5):
        log.append({"i": i})
    items, nxt = log.snapshot(after=3)
    assert [x["i"] for x in items] == [3, 4] and nxt == 5
