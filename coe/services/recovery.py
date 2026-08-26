"""Background recovery runs with replayable event logs (spec §3–§4)."""
import threading
import uuid


class EventLog:
    """Append-only list; readers replay from any index then tail."""

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._cond = threading.Condition()

    def append(self, item: dict) -> None:
        with self._cond:
            self._items.append(item)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        return getattr(self, "_closed", False)

    def snapshot(self, after: int = 0) -> tuple[list[dict], int]:
        with self._cond:
            return self._items[after:], len(self._items)

    def wait(self, after: int, timeout: float) -> int:
        with self._cond:
            self._cond.wait_for(lambda: len(self._items) > after
                                or self.closed, timeout=timeout)
            return len(self._items)


class RunManager:
    def __init__(self) -> None:
        self._logs: dict[str, EventLog] = {}
        self._lock = threading.Lock()

    def start(self, instance_name: str, narrative: str,
              runner=None) -> str:
        if runner is None:
            from coe.agents.graph import execute_recovery_streaming

            runner = execute_recovery_streaming
        token = uuid.uuid4().hex
        log = EventLog()
        with self._lock:
            self._logs[token] = log

        def worker():
            try:
                for item in runner(instance_name, trigger="CLI",
                                   narrative=narrative):
                    if "state" in item:   # terminal: summarize pydantic
                        st = item["state"]
                        sol = getattr(st, "solution", None) or {}
                        item = {"status": item["status"],
                                "run_id": item.get("run_id"),
                                "state_summary": {
                                    "solver_status": sol.get("status"),
                                    "makespan": sol.get("makespan"),
                                    "committed_version_id":
                                        getattr(st,
                                                "committed_version_id",
                                                None)}}
                    log.append(item)
            finally:
                log.close()

        threading.Thread(target=worker, daemon=True,
                         name=f"recovery-{token[:8]}").start()
        return token

    def log(self, token: str) -> EventLog:
        return self._logs[token]


MANAGER = RunManager()
