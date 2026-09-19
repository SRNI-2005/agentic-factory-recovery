"""Live-day walker (spec 2026-09-13 §1-§5).

The committed schedule is played forward: t advances over completion
boundaries of the ACTIVE schedule; the projector derives board state at
each tick. Queued user narratives resolve through the real recovery
graph at reference_clock = current t — the ONLY solve source in this
lane. The engine never writes schedule state directly (spec §5).
"""
from collections import deque
from typing import Callable, Iterator


class LiveDayError(ValueError):
    pass


class InterruptQueue:
    """Mid-flight narratives, delivered at the current tick (§3 step 3)."""

    def __init__(self) -> None:
        self._pending: deque[str] = deque()

    def push(self, narrative: str) -> None:
        if not str(narrative).strip():
            raise ValueError("empty narrative")
        self._pending.append(str(narrative).strip())

    def pop(self, t: int) -> str | None:
        if self._pending:
            return self._pending.popleft()
        return None


class ScheduledInterruptQueue(InterruptQueue):
    """Timeline-file events as scheduled interrupts (spec A3 §11).

    Reuses InterruptQueue's contract verbatim: the walker pops at the
    current tick and never knows the difference between a typed
    narrative and an authored one. Authored events sit sorted by
    minute; an event becomes due once the walk minute reaches or
    passes its authored minute, and the ORDER of resolution stays
    authored.
    """

    def __init__(self, scheduled: list[tuple[int, str]] | None = None):
        super().__init__()
        self._scheduled: list[tuple[int, str]] = sorted(
            (int(t), str(msg)) for t, msg in (scheduled or []))

    @property
    def remaining(self) -> int:
        return len(self._scheduled)

    def pop(self, t: int) -> str | None:
        if self._scheduled and t >= self._scheduled[0][0]:
            minute, msg = self._scheduled.pop(0)
            return msg
        return None


def _dwell_pause(speed) -> float | None:
    if speed == "instant":
        return None
    try:
        n = int(speed)
    except (TypeError, ValueError):
        raise LiveDayError(f"invalid speed: {speed!r}")
    if n <= 0:
        raise LiveDayError(f"invalid speed: {speed!r}")
    return 60.0 / n


def _pin_single_worker(prior: dict) -> None:
    import os

    from coe.config import get_settings

    prior.setdefault("workers", os.environ.get("SOLVER_NUM_SEARCH_WORKERS"))
    os.environ["SOLVER_NUM_SEARCH_WORKERS"] = "1"
    get_settings.cache_clear()


def _restore_workers(prior: dict) -> None:
    import os

    value = prior.get("workers")
    if value is None:
        os.environ.pop("SOLVER_NUM_SEARCH_WORKERS", None)
    else:
        os.environ["SOLVER_NUM_SEARCH_WORKERS"] = value
    from coe.config import get_settings

    get_settings.cache_clear()


def _board(session, instance_name: str, t: int):
    """Active board facts at the walk's current state.

    Returns (bounds_next, makespan): bounds_next = the earliest committed
    entry end beyond t (None when none remain); makespan = the active
    version's makespan (None when no active version exists).
    """
    from sqlalchemy import text

    iid = session.execute(text(
        "SELECT id FROM instances WHERE name = :n"),
        {"n": instance_name}).scalar_one_or_none()
    if iid is None:
        raise LiveDayError(f"unknown instance {instance_name!r}")
    mk = session.execute(text(
        "SELECT sv.makespan FROM active_schedule asev "
        "JOIN schedule_versions sv ON sv.id = asev.version_id "
        "WHERE asev.instance_id = :i LIMIT 1"), {"i": iid}).scalar()
    nxt = session.execute(text(
        "SELECT MIN(se.end_time) FROM active_schedule asev "
        "JOIN schedule_entries se ON se.id = asev.id "
        "WHERE asev.instance_id = :i AND se.end_time > :t"),
        {"i": iid, "t": t}).scalar()
    return (int(nxt) if nxt is not None else None,
            int(mk) if mk is not None else None)


def live_day(instance_name: str, *, speed: int | str = "instant",
             llm_client_factory: Callable | None = None,
             start_clock: int = 0,
             interrupt_queue: InterruptQueue | None = None) -> Iterator[dict]:
    """Play the committed day forward (spec §3). Pure per-step."""
    from sqlalchemy.orm import Session

    from coe.db.session import make_engine
    from coe.simulator.projector import project_day

    pace = _dwell_pause(speed)
    prior: dict = {}
    t = int(start_clock)
    engine = make_engine()
    try:
        while True:
            with Session(engine) as session:
                nxt, mk = _board(session, instance_name, t)
            if mk is None:
                raise LiveDayError(
                    f"`{instance_name}` has no active schedule — run "
                    f"`uv run python -m coe.cli solve baseline --instance "
                    f"{instance_name}` first (the live day plays it, §1)")

            narration = (interrupt_queue.pop(t)
                         if interrupt_queue is not None else None)
            if narration is not None:
                from coe.agents.graph import execute_recovery

                if not prior:
                    _pin_single_worker(prior)
                client = llm_client_factory() if llm_client_factory else None
                yield {"event": "recovery_start", "t": t,
                       "live": client is None}
                result = execute_recovery(
                    instance_name, trigger="CLI", narrative=narration,
                    reference_clock=t, client=client)
                yield {"event": "recovery", "t": t, "kind": "NARRATIVE",
                       "status": result["status"]}
                continue    # next tick derives from the NEW active version

            if nxt is None:
                yield {"event": "day_end", "t": t}
                return
            if pace is not None and nxt > t:
                import time

                time.sleep(min((nxt - t) * pace, 10.0))
            t = nxt
            with Session(engine) as session:
                ds = project_day(session, instance_name=instance_name, t=t)
            yield {"event": "tick", "t": t,
                   "completed": len(ds.completed_ops),
                   "in_progress": len(ds.in_progress),
                   "stock": dict(ds.effective_stock)}
    finally:
        if prior:
            _restore_workers(prior)
        engine.dispose()
