"""Scripted-day walker (spec §5). Deterministic; drives only public entry
points; yields feed items for BOTH the CLI (print) and the dashboard."""
import hashlib
import os
import time
from typing import Iterator

from coe.simulator.timeline import (
    MachineEvent, MaterialEvent, NarrativeEvent, Timeline, WorkerEvent,
    load_timeline,
)

_prev_workers: str | None = None


def _scripted_message_id(script_name: str, idx: int) -> str:
    canonical = f"{script_name}|{idx}"
    return "simul-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _walk_paced(speed) -> float | None:
    """None = instant. N means N schedule-minutes per wall-minute: the
    inter-event wall pause for a gap of g schedule-minutes is
    g * 60 / N seconds (bounded to <= 10 s so a sparse script doesn't
    stall the thread of a CLI run)."""
    if speed == "instant":
        return None
    return 60.0 / int(speed)


def _pin_single_worker() -> None:
    """P2 §9: determinism consumers pin single-worker search. Settings are
    lru_cached, so force the env and clear the cache BEFORE the first
    recovery in this process. The prior value is remembered so the walk
    can restore it afterwards (an engine walk must not reconfigure the
    host process for everyone else)."""
    global _prev_workers
    _prev_workers = os.environ.get("SOLVER_NUM_SEARCH_WORKERS")
    os.environ["SOLVER_NUM_SEARCH_WORKERS"] = "1"
    from coe.config import get_settings

    get_settings.cache_clear()


def _restore_workers() -> None:
    global _prev_workers
    if _prev_workers is None:
        os.environ.pop("SOLVER_NUM_SEARCH_WORKERS", None)
    else:
        os.environ["SOLVER_NUM_SEARCH_WORKERS"] = _prev_workers
    _prev_workers = None
    from coe.config import get_settings

    get_settings.cache_clear()


def _record_to_wire(ev, *, instance_name: str, message_id: str) -> dict:
    payload = {"message_id": message_id, "instance_id": instance_name,
               "event_type": ev.event_type, "occurred_at": ev.t,
               "severity": "MEDIUM", "reason": "simulated-day"}
    if isinstance(ev, MachineEvent):
        payload["resource_kind"] = "MACHINE"
        payload["machine_id"] = ev.machine_id
        if ev.estimated_downtime is not None:
            payload["estimated_downtime"] = ev.estimated_downtime
    elif isinstance(ev, WorkerEvent):
        payload["resource_kind"] = "WORKER"
        payload["worker_id"] = ev.worker_id
        if ev.duration is not None:
            payload["estimated_absence"] = ev.duration
    else:
        payload["resource_kind"] = "MATERIAL"
        payload["material_sku"] = ev.sku
    return payload


def _materialize_restock(instance_name: str, ev: MaterialEvent) -> None:
    """Receipt + RESTOCK ledger row in ONE session/commit (spec §4(b));
    ingestion itself stays telemetry-only."""
    from sqlalchemy.orm import Session

    from coe.db.models.materials import (Material, MaterialReceipt,
                                         MaterialTransaction)
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine

    with Session(make_engine()) as s:
        inst = s.query(Instance).filter_by(name=instance_name).one()
        mat = (s.query(Material)
               .filter(Material.instance_id == inst.id,
                       Material.sku == ev.sku).one())
        s.add(MaterialReceipt(instance_id=inst.id, material_id=mat.id,
                              quantity=ev.quantity,
                              available_at=ev.t, source="simulate"))
        s.add(MaterialTransaction(
            instance_id=inst.id, operation_id=None, material_id=mat.id,
            quantity=ev.quantity, timestamp=ev.t,
            transaction_type="RESTOCK", source="simulate"))
        s.commit()


def walk_timeline(timeline: Timeline | str, *, instance_name: str,
                  speed="instant", llm_client_factory=None,
                  start_index: int = 0) -> Iterator[dict]:
    """Walk `timeline.events[from start_index:]`.

    Accepts an already-loaded Timeline or a path string (loaded lazily via
    load_timeline). Structured events ingest via the shared Phase 1
    ingestion function (idempotent scripted message ids). NARRATIVE events
    run the real recovery graph once each, passing reference_clock = event.t.
    Resume: events with index < start_index are already persisted and are
    simply not re-executed (idempotency would suppress them anyway; skipping
    keeps the log truthful).
    """
    if isinstance(timeline, str):
        timeline = load_timeline(timeline)
    pace = _walk_paced(speed)

    committed = []
    pinned = False
    try:
        for idx, ev in enumerate(timeline.events):
            if idx < start_index:
                continue
            if pace is not None and idx > start_index:
                gap = ev.t - timeline.events[idx - 1].t
                time.sleep(min(gap * pace, 10.0))
            if isinstance(ev, NarrativeEvent):
                from coe.agents.graph import execute_recovery

                if not pinned:
                    _pin_single_worker()
                    pinned = True
                client = llm_client_factory() if llm_client_factory else None
                result = execute_recovery(
                    instance_name, trigger="CLI", narrative=ev.text,
                    reference_clock=ev.t, client=client)
                committed.append(
                    getattr(result["state"], "committed_version_id", None))
                yield {"event": "recovery", "t": ev.t, "idx": idx,
                       "status": result["status"]}
            else:
                from coe.mqtt.ingest import ingest_telemetry_event

                telemetry_id, created = ingest_telemetry_event(
                    _record_to_wire(ev, instance_name=instance_name,
                                    message_id=_scripted_message_id(
                                        timeline.name, idx)))
                if (isinstance(ev, MaterialEvent)
                        and ev.event_type == "MATERIAL_RESTOCK"
                        and ev.quantity is not None):
                    _materialize_restock(instance_name, ev)
                yield {"event": "ingest", "t": ev.t, "idx": idx,
                       "kind": ev.kind, "created": bool(created),
                       "telemetry_id": telemetry_id}
        yield {"event": "done", "committed": committed}
    finally:
        if pinned:
            _restore_workers()
