# tests/agents/test_runs.py
"""§7 lifecycle rows + per-instance advisory run lock."""
import time

import pytest

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def inst(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        session.add(Instance(name="runs-world", source_name="synthetic"))


def test_record_run_legal_statuses():
    from coe.agents.runs import record_run
    from coe.db.session import make_engine
    from sqlalchemy import text

    rid = record_run("runs-world", trigger="CLI", status="COMMITTED",
                     disruption_record_json={"kind": "MACHINE"},
                     started_at=time.time(), finished_at=time.time())
    with make_engine().begin() as c:
        row = c.execute(text(
            "SELECT status, trigger, final_status_version_id "
            "FROM recovery_runs WHERE id=:r"), {"r": rid}).one()
    assert row.status == "COMMITTED" and row.trigger == "CLI"
    assert row.final_status_version_id is None


def test_write_proposals():
    from coe.agents.runs import record_run, write_proposals
    from coe.db.session import make_engine
    from sqlalchemy import text

    rid = record_run("runs-world", trigger="MQTT", status="GATE_FAILED",
                     disruption_record_json={}, started_at=1.0,
                     finished_at=2.0)
    n = write_proposals("runs-world", rid, [
        {"candidate": {"type": "DEFER_JOB", "job_id": "J",
                       "release_offset": 5},
         "round": 1, "verdict": "VALID", "reason": "ok"},
        {"candidate": {"type": "DEFER_JOB", "job_id": "J",
                       "release_offset": 5},
         "round": 1, "verdict": "INVALID_DUPLICATE", "reason": "duplicate"},
    ])
    assert n == 2
    with make_engine().begin() as c:
        cnt = c.execute(text(
            "SELECT count(*) FROM recovery_proposals WHERE run_id=:r"),
            {"r": rid}).scalar_one()
    assert cnt == 2


def test_lock_serializes_and_times_out_loudly():
    from coe.agents.runs import InstanceRunLock, RunLockTimeout

    with InstanceRunLock("runs-world", wait_seconds=5):
        t0 = time.monotonic()
        with pytest.raises(RunLockTimeout):
            with InstanceRunLock("runs-world", wait_seconds=1):
                pass
        assert time.monotonic() - t0 >= 0.9   # waited, did not barge


def test_lock_released_after_exit():
    from coe.agents.runs import InstanceRunLock

    with InstanceRunLock("runs-world", wait_seconds=5):
        pass
    with InstanceRunLock("runs-world", wait_seconds=5):
        pass       # second acquisition succeeds => unlock happened
