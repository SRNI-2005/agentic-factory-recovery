# coe/agents/runs.py
"""Run lifecycle rows + per-instance advisory lock (spec §7)."""
import time
from datetime import datetime, timezone

from sqlalchemy import text

from coe.config import get_settings
from coe.db.session import make_engine


class RunLockTimeout(RuntimeError):
    """Contending trigger exceeded RECOVERY_LOCK_WAIT_SECONDS (§7)."""


def _ts(f: float) -> datetime:
    return datetime.fromtimestamp(f, tz=timezone.utc)


def record_run(instance_name: str, *, trigger: str, status: str,
               disruption_record_json: dict, started_at: float,
               finished_at: float,
               final_status_version_id: int | None = None) -> int:
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import RecoveryRun
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        run = RecoveryRun(
            instance_id=inst.id, trigger=trigger, status=status,
            disruption_record_json=disruption_record_json,
            final_status_version_id=final_status_version_id,
            started_at=_ts(started_at), finished_at=_ts(finished_at))
        session.add(run)
        session.flush()
        return run.id


def write_proposals(instance_name: str, run_id: int,
                    verdicts: list[dict]) -> int:
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import RecoveryProposal
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        for v in verdicts:
            session.add(RecoveryProposal(
                instance_id=inst.id, run_id=run_id,
                round_number=v["round"], candidate_json=v["candidate"],
                verdict=v["verdict"], verdict_reason=v.get("reason")))
        return len(verdicts)


class InstanceRunLock:
    """Session-level advisory lock held on a dedicated connection."""

    def __init__(self, instance_name: str,
                 wait_seconds: float | None = None) -> None:
        self._key = f"coe-run:{instance_name}"
        self._wait = (get_settings().recovery_lock_wait_seconds
                      if wait_seconds is None else wait_seconds)
        self._conn = None

    def __enter__(self) -> "InstanceRunLock":
        self._conn = make_engine().connect()
        deadline = time.monotonic() + self._wait
        while True:
            got = self._conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": self._key}).scalar_one()
            if got:
                return self
            if time.monotonic() >= deadline:
                self._conn.close()
                self._conn = None
                raise RunLockTimeout(
                    f"instance run lock {self._key!r} held elsewhere; "
                    f"gave up after {self._wait}s (§7)")
            time.sleep(0.25)

    def __exit__(self, *exc) -> None:
        if self._conn is not None:
            self._conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": self._key})
            self._conn.close()
            self._conn = None
