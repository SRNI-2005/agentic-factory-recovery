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

def _scripted_message_id(script_name: str, idx: int) -> str:
    canonical = f"{script_name}|{idx}"
    return "simul-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _walk_paced(speed) -> float | None:
    """None = instant; else workers-per-minute pacing knob N (= 60/speed).
    The actual pause formula lives at the sleep site in walk_timeline."""
    if speed == "instant":
        return None
    return 60.0 / int(speed)


def _pin_single_worker(prior: dict) -> None:
    """P2 §9: determinism consumers pin single-worker search. Settings are
    lru_cached, so force the env and clear the cache BEFORE the first
    recovery in this process. The prior value is stored in the per-walk
    `prior` cell so the walk can restore exactly what IT overwrote
    afterwards (an engine walk must not reconfigure the host process for
    everyone else; interleaved/restored walks never clobber each other)."""
    prior.setdefault("workers", os.environ.get("SOLVER_NUM_SEARCH_WORKERS"))
    os.environ["SOLVER_NUM_SEARCH_WORKERS"] = "1"
    from coe.config import get_settings

    get_settings.cache_clear()


def _restore_workers(prior: dict) -> None:
    prev = prior.get("workers")
    if prev is None:
        os.environ.pop("SOLVER_NUM_SEARCH_WORKERS", None)
    else:
        os.environ["SOLVER_NUM_SEARCH_WORKERS"] = prev
    from coe.config import get_settings

    get_settings.cache_clear()


def _record_to_wire(ev, *, instance_name: str, message_id: str) -> dict:
    # Hard-coded "MEDIUM" is cosmetic for the simulator: structured
    # (non-narrative) timeline events carry no severity of their own, and
    # ingest treats it as telemetry metadata only. NarrativeEvent.severity
    # stays bound to the graph path, not telemetry.
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


def _auto_recover_narrative(ev) -> str:
    """Synthesized report line for an auto-recovery run after a structured
    event (spec amendment 2026-09-13: disruption -> auto re-plan).

    The resource identifier is prefixed so the degraded translate fallback
    binds ITS OWN resource: token-free report lines would fall back to the
    first valid machine (coe/agents/degraded_client.py), which is wrong for
    WORKER/MATERIAL events. Id-shaped tokens (M3 / W3 / MAT-001) bind by
    trailing digits via check_narrative_ids."""
    if isinstance(ev, MachineEvent):
        token = ev.machine_id
    elif isinstance(ev, WorkerEvent):
        token = ev.worker_id
    elif isinstance(ev, MaterialEvent):
        token = ev.sku
    else:
        token = ev.kind
    return (f"auto-recover: {token} {ev.kind} {ev.event_type}"
            f" at minute {ev.t}")


def _recovery_step(narrative: str, t: int, idx: int, *,
                   instance_name: str, llm_client_factory, prior: dict,
                   committed: list):
    """One full recovery graph run — the ONLY recovery path of the walk
    (spec §5 NARRATIVE events + auto_recover Step 2 share it verbatim:
    same trigger/reference_clock conventions, same _pin_single_worker
    prior-cell semantics). Generator of feed chunks."""
    from coe.agents.graph import execute_recovery

    if not prior:
        _pin_single_worker(prior)
    client = llm_client_factory() if llm_client_factory else None
    # Pre-announce: recoveries are live, multi-minute steps (translate +
    # solver floor). Consumers MUST surface this BEFORE executing so the
    # UI never looks frozen.
    yield {"event": "recovery_start", "t": t, "idx": idx,
           "text": narrative, "live": client is None}
    result = execute_recovery(
        instance_name, trigger="CLI", narrative=narrative,
        reference_clock=t, client=client)
    committed.append(
        getattr(result["state"], "committed_version_id", None))
    yield {"event": "recovery", "t": t, "idx": idx,
           "kind": "NARRATIVE", "status": result["status"]}


def walk_timeline(timeline: Timeline | str, *, instance_name: str,
                  speed="instant", llm_client_factory=None,
                  start_index: int = 0) -> Iterator[dict]:
    """Walk `timeline.events[from start_index:]`.

    Accepts an already-loaded Timeline or a path string (loaded lazily via
    load_timeline). Structured events ingest via the shared Phase 1
    ingestion function (idempotent scripted message ids). NARRATIVE events
    and (when timeline.auto_recover) every structured disruption event run
    the real recovery graph, passing reference_clock = event.t (spec
    amendment 2026-09-13: auto_recover defaults True — disruption -> auto
    re-plan; narrative steps remain optional semantic flair).
    Resume: events with index < start_index are already persisted and are
    simply not re-executed (idempotency would suppress them anyway; skipping
    keeps the log truthful).
    """
    if isinstance(timeline, str):
        timeline = load_timeline(timeline)
    pace = _walk_paced(speed)

    committed = []
    prior: dict = {}   # per-walk cell: env values THIS walk overwrote
    try:
        for idx, ev in enumerate(timeline.events):
            if idx < start_index:
                continue
            if pace is not None and idx > start_index:
                # Inter-event wall pause = gap * (60 / workers_per_minute);
                # i.e. pacing N = N schedule-minutes per wall-minute, capped
                # at 10 s so a sparse script never stalls a CLI thread.
                gap = ev.t - timeline.events[idx - 1].t
                time.sleep(min(gap * pace, 10.0))
            if isinstance(ev, NarrativeEvent):
                gen = _recovery_step(
                    ev.text, ev.t, idx, instance_name=instance_name,
                    llm_client_factory=llm_client_factory, prior=prior,
                    committed=committed)
            else:
                from coe.mqtt.ingest import ingest_telemetry_event

                telemetry_id, created = ingest_telemetry_event(
                    _record_to_wire(ev, instance_name=instance_name,
                                    message_id=_scripted_message_id(
                                        timeline.name, idx)))
                if (created and isinstance(ev, MaterialEvent)
                        and ev.event_type == "MATERIAL_RESTOCK"
                        and ev.quantity is not None):
                    _materialize_restock(instance_name, ev)
                yield {"event": "ingest", "t": ev.t, "idx": idx,
                       "kind": ev.kind, "created": bool(created),
                       "telemetry_id": telemetry_id}
                # auto_recover amendment: ALL six structured kinds alter the
                # constraint world (downtime/availability/supply), so each
                # one followed by the same recovery path. The in-memory
                # NarrativeEvent-shaped text is never appended to the file
                # — single path, dual triggers.
                gen = None
                if timeline.auto_recover:
                    yield {"event": "auto_recover", "t": ev.t, "idx": idx}
                    gen = _recovery_step(
                        _auto_recover_narrative(ev), ev.t, idx,
                        instance_name=instance_name,
                        llm_client_factory=llm_client_factory, prior=prior,
                        committed=committed)
            if gen is not None:
                yield from gen
        yield {"event": "done", "committed": committed}
    finally:
        if prior:
            _restore_workers(prior)
