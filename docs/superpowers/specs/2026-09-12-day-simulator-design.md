# Day Simulator (Timeline Injector) — Design

**Status:** Approved (2026-09-12, user-reviewed in conversation)
**Date:** 2026-09-12
**Phase:** Post-Phase-3 capability; serves the demo + Phase 5 e2e harness

## 1. Purpose

A deterministic scripted-day engine ("fast clock"): replay an authored timeline
of disruptions against a factory instance at accelerated speed so that the
Streamlit cockpit becomes a story player. Events drive the REAL ingestion path;
agentic events drive the REAL Phase 3 recovery graph; the committed schedule is
projected forward through simulated time so a disruption at t=980 sees
mid-day state (jobs already consumed, W3 on break, M3 restored at 130), never a
clean t=0 board. It is not a production scheduler — Phase 2 stays the sole
committing authority; the simulator only drives existing public entry points.

## 2. Scope

### Included

- Timeline JSON loader + pydantic schema with loud validation failures.
- A day-playback projector deriving consumed/offline state at arbitrary t.
- Materials: builder deduction for correctness + `material_transactions`
  ledger (P1 §6.4, built at last) for the audit trail.
- Engine walk loop: ingest + agentic recovery steps, pacing (instant / N× wall
  clock), pause/resume-by-index, per-event live feed items.
- CLI `simulate timeline` command; shipped `demo_day_01` timeline.
- Streamlit `Simulate` page: clock display, scale selector, pause/resume/step,
  decision feed, mini-Gantt evolution.
- Determinism contract: identical timeline + seed + fake LLM ⇒ identical
  commit chain (live-provider runs excluded, per P3 §8 posture).

### Excluded

- Any autonomous stochastic event generation (scripts are authored or
  generator-assisted by deterministic tools, never random).
- Continuing the txt narrative multi-disruption batch (quiescence batching
  stays a documented P3 future extension).
- Real-time MQTT wall-clock injection (the injector uses the same ingestion
  function directly; broker round-trips are an already-proven property).

## 3. Architecture

```text
data/timelines/*.json
    → coe.simulator.timeline   (load + validate, fail loudly)
        → coe.simulator.engine (clock walk; event executor state machine)
             ├─ structured events → coe.mqtt.ingest.ingest_telemetry_event
             │                      (message_id = simul-<hash>; advisory locks
             │                       and interval-union parity via shared fn)
             ├─ narrative steps   → coe.agents.graph.execute_recovery
             │                      (same graph as CLI; ref clock = event t)
             └─ COMMIT/rollback   → material_transactions rows
co.simulator.projector (advance active schedule to t: COMPLETED/IN_PROGRESS
                        classification + effective-material-stock view for
                        the dashboard rail; the authoritative deduction lives
                        in the Phase 2 payload builder, amended §6.11)
Projector semantic: playback is physical consumption truth (pre-failure
                    history included); the recovery builder re-derives
                    demands — the dashboard rail may therefore exceed the
                    next solve's effective capacity when a mid-flight
                    failure exists.
```

One-way flow; each unit independently testable. The engine never writes
schedule state directly — only through the graph and the ingestion function.

## 4. Materials: deduction + ledger (approved user choice)

- **(a) Correctness (P2 §6.11 amendment, normative):** the Phase 2 payload
  builder deducts the BOM demand of every *frozen/completed* operation from
  each SKU's `capacity` for recovery payloads, since those bars were
  physically consumed before the recovery clock. Blocked ops still contribute
  nothing. The reservoir semantics are unchanged otherwise.
- **(b) Audit trail:** new `material_transactions` table (instance-scoped,
  per P1 §6.4: `id, instance_id, operation_id (nullable), material_id,
  quantity, timestamp, transaction_type ∈ {CONSUME, REFILL, RESTOCK},
  source ∈ {commit, simulate}`) + Alembic migration #7. The committer writes
  one CONSUME row per committed entry's BOM demand (in-transaction). The
  simulator materializes scripted restock events itself: a `MaterialReceipt`
  row (`available_at = event.t`) plus a RESTOCK transaction row — keeping
  Phase 1's telemetry-only rule for restock events intact (ingest never
  auto-creates receipts; the simulator is scenario-side). Scenario-seeded
  receipts keep their existing provenance; `create_all` stays forbidden.

## 5. Engine semantics

- **Event kinds:** the six structured Phase 1 events (FAILURE, MAINTENANCE,
  WORKER_ABSENT, WORKER_RETURN, MATERIAL_SHORTAGE, MATERIAL_RESTOCK) plus
  `NARRATIVE` events (text + severity) that trigger exactly one graph run
  each — §4.1 one-disruption-per-run scope preserved.
- **Time:** events advance the scripted clock monotonically; each agentic step
  passes `reference_clock = event.t`; structured events set their own
  `occurred_at = event.t`.
- **Pacing modes:** `instant` (no wall-clock waits, event-to-event) and
  `N×` (N schedule-minutes progress per wall-minute; the shipped ~700-minute
  arc takes ≈ 12 wall-minutes at 60×, seconds at instant). Dashboard exposes
  pause/resume/step; the engine reports progress via a generator of event
  feed items so both surfaces consume one implementation.
- **Resume:** from the run log's last completed index k — earlier events are
  skipped (they are already persisted; idempotency guarantees no duplicates),
  execution continues at k+1. No DB checkpoint row needed.
- **Determinism:** every agentic recovery along the walk runs in
  **`num_search_workers=1`** mode (P2 §9's mandatory configuration for
  reproducibility consumers) so the commit chain is replay-stable; the same
  timeline + seed + fake LLM ⇒ byte-identical committed versions.
- **Isolation:** `--on-clone` (default for demos) clones the source instance
  per run (`sim-<script>@<8hex>`), matching the e2e harness pattern.

> **[Amendment 2026-09-13]:** auto_recover (timeline field, default True) —
> every structured disruption event itself triggers a full recovery solve;
> narrative steps become optional free-text solves.

## 6. CLI + configuration

```bash
uv run python -m coe.cli simulate timeline --file data/timelines/demo_day_01.json \
    [--speed instant|10|30|60] [--from N] [--on-clone] [--instance factory_demo_01]
```

Pydantic settings: `SIMULATE_DEFAULT_SPEED` (default 30), `SIMULATE_CLONE`
(default true), `SIMULATE_MAX_HORIZON_DAYS` (default 7, script guard).

## 7. Shipped timeline

`data/timelines/demo_day_01.json` — authored to fit INSIDE the schedulable
day (factory_demo_01 baseline makespan ≈ 406; all events t < 400 —
amended 2026-09-13 after live demo analysis: a 700-minute arc re-planned
nothing after minute ~300, and a premature structured FAILURE +
narrative M3 outage double-killed M3 → SOLVE_INFEASIBLE under auto-fix).
Arc: t=90 narrative M3 failure recovery (agentic, machine stays out for the
day) → t=200 MAT-001 shortage telemetry → t=300 MAT-001 restock (112,
engine materializes receipt + RESTOCK ledger row) → t=330 W3 absent
60 min → t=380 narrative W3-returns recovery (agentic). Under auto_recover
(amendment 2026-09-13) the three structured events each auto-solve, so
the t=230 narrative shortage-plan recovery was deleted as redundant
(duplicating the t=200 auto solve 30 minutes later). Must pass end to
end with the current stack.

## 8. Testing strategy

- **Tier 1 (unit):** schema validation cases; projector classification
  boundaries (end == t is COMPLETED); deduction arithmetic on crafted payloads.
- **Tier 2 (ledger):** committer CONSUME rows correct and re-committed without
  duplicates; engine RESTOCK rows (receipt + ledger row) for scripted restocks;
  `REFILL` is a reserved type with no current producer (documented).
- **Tier 3 (engine):** fake-LLM determinism (byte-identical commit chain with
  `num_search_workers=1`); resume-from-k correctness; instant mode completes
  under the suite budget.
- **Tier 4 Surfaces:** CLI happy path tests (schema errors exit 1 with a
  message, not a traceback), AppTest smoke of the Simulate page (render +
  scripted "instant" full walk on a tiny timeline).

## 9. Acceptance criteria

1. `simulate timeline` full run of the shipped timeline commits ≥ 2 recovery
   versions and writes material ledger rows, against a clone instance.
2. Deterministic replay: same script + fake LLM + `num_search_workers=1` ⇒
   byte-identical recovery commit chain (criterion parity with P3
   determinism / P2 §9).
3. Mid-day realism: a recovery scripted at t=420 shows frozen COMPLETED/
   IN_PROGRESS entries from the morning timeline segment, and the payload's
   effective stock equals initial stock − consumed-up-to-clock.
4. Ledger integrity: Σ CONSUME rows matches the committed schedule's BOM at
   each version boundary (violations fail tests loudly).
5. Dashboard page streams live and pause/resume/step works (AppTest-able).
6. Full existing suite (quick gate) continues to pass — simulator adds no
   production coupling beyond §4's two amend points.
