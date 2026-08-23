# Phase 3: Agentic Middleware

**Status:** Approved — Amended 2026-08-23 (multi-resource disruption records)
**Date:** 2026-08-22
**Phase:** Agentic Middleware

> **Amendment 2026-08-23 (user-approved):** disruptions may target machines, workers, or materials. `DisruptionRecord` becomes a pydantic discriminated union on `kind` (§4.1); the investigation stage gains a deterministic `worker_agent` node (§4.2); MQTT listening covers all three topic patterns (§3.4); the fidelity corpus must cover all three narrative families (§8). Rationale: PRD §5 lists worker-absentee and inventory-shortage narratives; machine-only records cannot represent them.

## 1. Purpose

Phase 3 implements the LangGraph orchestration layer that connects disruption events to the Phase 2 solver. LLM agents own the semantic layer exclusively: translating messy narratives into structured records, proposing soft recovery strategies from a fixed catalog, and explaining committed schedules. All mathematical work remains in the deterministic solver; every LLM-derived output passes schema and database validation before it can influence system state.

Recovery runs are triggered by the CLI or by an MQTT listener that converts validated FAILURE events into runs (moved forward from Phase 5).

## 2. Scope

### Included

- A LangGraph workflow with a fixed, deterministic node topology.
- Translation Agent converting narrative disruption reports into validated structured records.
- Deterministic investigation nodes (Machine, Production, Inventory) implemented as pure database queries.
- Strategy Agent proposing recovery trade-offs from a closed catalog via a bounded negotiation loop.
- Manager compilation of the solver payload using hard constraints from the database alone.
- A strategy applier transforming payloads deterministically from validated candidates.
- Per-job tardiness weights in the solver objective (the single Phase 2 engine extension).
- Pre-commit invariant gate and post-commit verifier with automated rollback.
- Post-hoc schedule explanation service writing to `schedule_explanations`.
- Population of the reserved `recovery_runs` and `recovery_proposals` tables.
- A fidelity benchmark: translation exact-match accuracy and strategy validity plus non-degradation rates against a seeded ground-truth corpus.
- CLI commands for recovery, explanation, and benchmarking.
- An MQTT listener (`mqtt listen`) launching recovery runs automatically from edge FAILURE events.

### Excluded

- QUBO/QAOA formulation (Phase 4).
- Latency comparison between CP-SAT and QAOA (Phase 5).
- Free-form natural-language strategy negotiation beyond the bounded loop.
- Any LLM participation in constraint computation, objective values, or schedule mathematics.
- Autonomous re-training or prompt optimization loops.

## 3. Architecture

### 3.1 Graph Topology

The workflow is a fixed linear pipeline with one bounded sub-loop. Conditional edges exist only for validation retries and failure fallbacks:

```text
translate → ingest → machine_agent → production_agent → inventory_agent → worker_agent
    → strategy_loop (≤ STRATEGY_MAX_ROUNDS rounds) → manager_compile
    → solve → gate → commit → verify → explain
```

*(Amendment 2026-08-23: `worker_agent` appended to the investigation stage. All investigation nodes run for every record; each is a pure database query that no-ops (empty findings) when the record's `kind` does not concern it — keeping the pipeline fixed and deterministic by construction, with conditional edges still limited to validation retries and failure fallbacks.)*

Deterministic routing is enforced by construction: node order is static, and every conditional edge resolves to either a retry with a bounded counter or a documented fallback.

### 3.2 Shared State

A single typed state object (pydantic model) threads through all nodes:

- `narrative`: raw input text.
- `disruption_record`: validated structured record (Section 4.1).
- `db_facts`: failed machine capabilities, stranded jobs, alternative routings, material and worker availability.
- `strategy_candidates[]`: proposed catalog entries with round numbers.
- `round_verdicts[]`: per-candidate feasibility verdicts from the investigation nodes.
- `compiled_payload`: the final solver JSON.
- `solution`: solver output.
- `gate_result`, `verify_result`: safety-net outcomes.
- `explanation`: generated rationale text.
- `errors[]`, `warnings[]`: accumulated diagnostics.

### 3.3 LLM Boundary Rule

LLM calls exist at exactly three nodes: `translate`, `strategy_loop`, and `explain`. The investigation nodes are pure database query functions with no LLM dependency. Every LLM output passes its pydantic validator before entering state; on validation failure the validator error message is appended to the prompt and the call retried, up to `LLM_MAX_RETRIES`.

Fallback policy:

- `translate` exhausted → run aborts with status `TRANSLATION_FAILED`. No state mutation occurs.
- `strategy_loop` exhausted or persistently invalid → candidate list is emptied; the run proceeds as a no-strategy solve with a logged warning.
- `explain` failure → run completes; explanation is logged as missing.

### 3.4 Trigger Sources

Two entry points feed the same graph:

- **CLI:** `recover --narrative ...` — narrative text enters the `translate` node.
- **MQTT listener:** `mqtt listen` is a long-running command subscribing to all three topic patterns — `factory/{instance_id}/machine/{machine_id}/events`, `.../worker/{worker_id}/events`, `.../material/{material_sku}/events` *(Amendment 2026-08-23)*. Validated structured payloads are already typed by their topic's resource kind: the listener derives the matching `DisruptionRecord` variant directly from payload fields and starts the graph at `ingest`, skipping the `translate` node and its LLM call entirely. Non-disruption events never trigger runs.

Listener guarantees:

- **Idempotent launch:** QoS 1 permits redelivery; before starting a run, the listener checks whether the event's `message_id` has already produced one and skips duplicates.
- **Lock contention:** triggers block on the per-instance run lock up to `RECOVERY_LOCK_WAIT_SECONDS`, then fail loudly. Cascades serialize; no disruption is silently dropped.
- **Malformed payloads:** recorded in `telemetry_events.processing_error` per Phase 1 semantics; no run starts.

MAINTENANCE records (CLI narratives describing planned outages) follow the identical graph; with finite windows from `estimated_downtime` (Phase 1 §6.5), the run behaves as planned-outage rescheduling rather than failure recovery.

## 4. Agent Specifications

### 4.1 Translation Agent (AI Role 1)

Input: raw narrative text. Output: a `DisruptionRecord` — as of Amendment 2026-08-23, a pydantic **discriminated union on `kind`** with three variants:

```json
{ "kind": "MACHINE",
  "instance_id": "factory_demo_01",
  "machine_id": "MC-04",
  "event_type": "FAILURE",
  "occurred_at": 512,
  "severity": "HIGH",
  "estimated_downtime": 90,
  "narrative_excerpt": "MC-04 gearbox seized, sparks everywhere" }
```

```json
{ "kind": "WORKER",
  "instance_id": "factory_demo_01",
  "worker_id": "W-03",
  "event_type": "WORKER_ABSENT",
  "occurred_at": 480,
  "severity": "MEDIUM",
  "estimated_absence": 240,
  "narrative_excerpt": "W-03 called in sick this morning" }
```

```json
{ "kind": "MATERIAL",
  "instance_id": "factory_demo_01",
  "material_sku": "STEEL-304",
  "event_type": "MATERIAL_SHORTAGE",
  "occurred_at": 300,
  "severity": "LOW",
  "narrative_excerpt": "STEEL-304 bin is empty, delivery stuck at supplier" }
```

Common fields across all kinds: `kind`, `instance_id`, `event_type`, `occurred_at`, `severity`, `narrative_excerpt`; plus exactly one resource reference (`machine_id` / `worker_id` / `material_sku`). Machine-kind carries optional `estimated_downtime`; worker-kind carries optional `estimated_absence`; material-kind carries neither (shortages are conditions evaluated at solve time).

Scope: one disruption per run. A record names exactly one resource of one kind; narratives describing multiple simultaneous disruptions are rejected with a clear error (repeat the command per resource). Multi-record translation remains a documented future extension, not a Phase 3 deliverable.

**Future extension — quiescence batching (design intent, deliberately not built):** sequential per-record runs can churn under correlated bursts (run 1 may commit work onto resources whose failure arrives moments later as run 2's input). The intended eventual remedy is a debounce window, not multi-resource records: coalesce disruptions arriving within `RECOVERY_DEBOUNCE_SECONDS` into the next single solve, bounded by `max_delay` so batching never stalls response indefinitely, with CRITICAL-severity events bypassing the window. Worst case degrades gracefully to today's behavior plus bounded latency. Deferred because no Phase 3–5 benchmark exercises burst arrivals.

Validation layers:

1. Pydantic discriminated union: per-kind enums for `event_type` (per Phase 1 §6.5 table), severities, `occurred_at >= 0`, and presence/absence of the correct duration field per kind.
2. Instance cross-check: `record.instance_id` must equal the CLI `--instance` value; the CLI value is authoritative, and any mismatch rejects the record.
3. Database checks: the referenced resource exists within the instance — machine by id, worker by id, material by SKU, depending on kind. Schema and database failures both feed the validator error back into the prompt and retry, up to `LLM_MAX_RETRIES`, before the run aborts as `TRANSLATION_FAILED`.
4. Time resolution: relative expressions ("two hours ago") resolve against an explicit reference clock — the `--at` flag if given, otherwise the instance's latest telemetry `occurred_at`. The resolved absolute minute is stored in `occurred_at`.

On success the record is written through the same ingestion functions used by the Phase 1 MQTT subscriber, guaranteeing identical DB semantics for CLI-triggered and MQTT-triggered runs *(Amendment 2026-08-23: machine records write telemetry + downtime-window union + FAILED status; worker records write telemetry + absence-window union + UNAVAILABLE status; material records write telemetry only)*. Idempotency keys differ by source: MQTT events carry their wire `message_id`; CLI records derive one from a stable hash of the validated record fields (`cli-{hash}`), so re-running an identical narrative is an idempotent no-op rather than a duplicate event. Field mapping differs by source: CLI fills `narrative_excerpt` from the input text; MQTT-derived records copy `payload.reason` into it. Duration fields map straight through, producing finite windows when present and open-ended ones when absent (Phase 1 §6.5).

### 4.2 Investigation Nodes (no LLM)

- **Machine Agent:** confirms the failed machine, reads its capability set from `machine_capabilities`, and records which capabilities are lost. No-ops unless `kind = MACHINE`.
- **Production Agent:** queries the active schedule for operations assigned to the failed machine that are `SCHEDULED` or `IN_PROGRESS` (stranded work), plus their jobs and deadlines. For worker-kind records (Amendment 2026-08-23): queries future scheduled assignments of the absent worker within the absence window.
- **Inventory Agent:** for each alternative routing candidate, verifies worker eligibility rows and material supply, producing the availability facts consumed by the strategy loop. For material-kind records (Amendment 2026-08-23): additionally records the shortage evidence — total supply vs pending demand for the affected SKU and the operations whose BOM references it.
- **Worker Agent** *(Amendment 2026-08-23)*: confirms the absent worker, reads their eligibility footprint (`operation_machine_worker_times` rows referencing them), flags operation-machine pairs where the absent worker was the *sole* eligible worker (hard infeasibility candidates vs reassignment options), and records their stranded assignments from the active schedule. No-ops unless `kind = WORKER`.

Each node appends its findings to `db_facts`. Findings are reproducible queries; they are never generated by an LLM.

### 4.3 Strategy Loop (AI Role 2)

Each round:

1. Strategy Agent emits a structured response: zero or more candidates from the closed catalog (Section 5) plus an explicit `final: true|false` declaration, given `db_facts` and prior verdicts. Finality is a schema field, never inferred.
2. Investigation-node validators return a verdict per candidate: `VALID`, `VALID_WITH_WARNING`, or `INVALID`, with a machine-readable reason (unknown job, job not PENDING, unknown material, out-of-bounds parameter, effect-beyond-horizon).
3. Verdicts are appended to state; the loop repeats until the agent sets `final: true` or `STRATEGY_MAX_ROUNDS` is reached.

Cap exhaustion yields an empty candidate list: the run degrades to a baseline-equivalent solve, never to a failure.

### 4.4 Manager Compile

The Manager node assembles the solver payload by invoking the Phase 2 `payload_builder`, then applying only candidates whose latest round verdict is `VALID` or `VALID_WITH_WARNING` through the strategy applier (Section 6); the Phase 2 tardiness-weight derivation runs after the applier, using the effective objective weights (Phase 2 §3.1). `INVALID` entries remain in state and `recovery_proposals` for audit but are filtered out before the applier runs. Hard constraints come from the database alone; candidates influence only the soft-preference fields listed in Section 5. Every application is recorded in `payload.warnings`.

### 4.5 Explanation Service (AI Role 3)

Input: the committed schedule diff versus the parent version (moved operations, reassignments, newly blocked operations, applied strategies, clipped windows from `payload.warnings`) plus a constraint summary. Output: a human-readable rationale stored in `schedule_explanations`. When the committed version has no parent (baseline), the service produces a full-schedule summary instead of a diff. The service is strictly post-hoc; its output never influences scheduling state.

## 5. Strategy Catalog

A pydantic discriminated union on `type`. Anything outside the catalog is rejected by schema validation before reaching the applier.

| Type | Parameters | Deterministic payload effect |
| --- | --- | --- |
| `TARDINESS_WEIGHT` | `job_id`, `weight` ∈ [0, 10] | Sets the job's tardiness weight in the objective |
| `DEFER_JOB` | `job_id`, `release_offset` ≥ 0 | Raises the job's `release_time` by the offset |
| `EXPEDITE_MATERIAL` | `material_sku`, `quantity` > 0, `available_at` ≥ 0 | Adds a synthetic material receipt |
| `WEIGHT_PRESET` | `alpha` ≥ 0, `beta` ≥ 0, `alpha + beta > 0` | Overrides global objective weights |

Design notes:

- "Route to a slower capable machine" requires no catalog entry: the solver already weighs every eligible alternative.
- "Accept tardiness on a low-priority job" maps to `TARDINESS_WEIGHT` with a small weight.
- The catalog is intentionally minimal; extending it is a deliberate schema change, not a prompt change.
- An `EXPEDITE_MATERIAL` whose `available_at` falls beyond the projected solver horizon cannot take effect (Phase 2 counts only receipts arriving before the horizon); it receives verdict `VALID_WITH_WARNING` rather than failing silently.
- *(Amendment 2026-08-23)* No new catalog entries are required by multi-resource disruptions: worker absence recovers through the solver's native worker reassignment (alternatives already carry eligible-worker sets), mitigated where useful by `TARDINESS_WEIGHT`/`DEFER_JOB`; material shortage recovers through the existing `EXPEDITE_MATERIAL`.

### 5.1 Objective Extension (Phase 2 Engine Change)

The scalar `beta` generalizes to a per-job weight vector. The payload gains:

```json
"job_tardiness_weights": { "J-07": 0.2 }
```

Jobs absent from the map use the global `beta`. The objective becomes `alpha * normMakespan + Σ_j w_j * normTardiness_j`. This is the only modification to the Phase 2 engine; all Phase 2 tests must continue to pass unchanged with an empty weight map.

## 6. Strategy Applier and Safety Net

### 6.1 Applier

A pure module: `(payload, validated_candidates) → transformed_payload`. It performs no validation itself; it assumes candidates passed the catalog validator. Each application appends `{type: STRATEGY_APPLIED, candidate, field_changed}` to `payload.warnings`. Synthetic rows created by `EXPEDITE_MATERIAL` are recorded with `source = 'strategy_agent'` in provenance metadata, per Phase 1's synthetic-labeling rule. Candidates apply in emission order; when two candidates target the same job or material, the later overrides the earlier, and every application is recorded. An exact duplicate proposed within the same round receives verdict `INVALID_DUPLICATE`. Ordering contract with Phase 2 *(Amendment 2026-08-23)*: the applier runs strictly before the payload builder's tardiness-weight derivation (Phase 2 §3.1), so derived default weights reflect the post-`WEIGHT_PRESET` effective `alpha`/`beta`, and explicit `TARDINESS_WEIGHT` entries applied here survive as overrides on top of those defaults.

### 6.2 Pre-Commit Gate

Before the committer writes anything, the solution JSON must satisfy all invariants:

- Frozen operations are byte-identical to their parent-version entries (assignment, start, end).
- No operation is assigned to a failed machine.
- Every assignment respects machine eligibility and worker eligibility.
- Job precedence order holds.
- Blocked operations do not appear in the schedule.

Gate failure → no commit; the run is recorded as `GATE_FAILED`.

### 6.3 Post-Commit Verifier

After commit, the verifier re-reads the new version through `active_schedule` and evaluates the identical invariant functions. Violation → automatic `rolled_back = true` on the version, run recorded as `VERIFIER_ROLLBACK`, error logged. The gate and verifier share one implementation to prevent drift.

## 7. Run Lifecycle and Data Model

`recovery_runs` (reserved in Phase 1) is populated with one row per run:

- `id`, `instance_id`
- `trigger` (`CLI` or `MQTT`)
- `status`: `TRANSLATION_FAILED`, `SOLVE_INFEASIBLE`, `GATE_FAILED`, `VERIFIER_ROLLBACK`, `COMMITTED`. Strategy-loop exhaustion is deliberately not a terminal status: such runs proceed and end `COMMITTED`, with exhaustion visible in `recovery_proposals` and payload warnings.
- `disruption_record_json`, `final_status_version_id`, `started_at`, `finished_at`
- `node_timings_json` (JSONB, nullable; populated by Phase 5 latency instrumentation)
- `quantum_shadow_json` (JSONB, nullable; populated by Phase 5 shadow node when enabled)

`recovery_proposals` stores each candidate with its round number, verdict, and verdict reason.

`schedule_explanations` stores the generated rationale keyed to the committed version.

**Run exclusivity:** every run (CLI or MQTT-triggered) acquires a per-instance advisory lock for the graph's duration. Contending runs block up to `RECOVERY_LOCK_WAIT_SECONDS`, then abort with a loud error rather than proceeding unsynchronized. Cascades serialize; no disruption is silently dropped.

## 8. Fidelity Benchmark

Corpus: seeded JSONL files under `data/corpus/` pairing messy narratives with ground-truth `DisruptionRecord`s and scenario context. Generation is deterministic given a seed; synthetic narratives are labeled as synthetic in provenance metadata. *(Amendment 2026-08-23: the corpus must include all three narrative families — machine failure, worker absence, material shortage — in proportions recorded in the report, so translation accuracy is measured per kind and aggregate.)*

Metrics:

- **Translation:** per-field exact-match rate (machine, event type, occurred_at, severity, estimated downtime) and corpus pass rate (records passing both schema and DB validation).
- **Strategy:** schema-validity rate (candidates accepted by the catalog validator) and non-degradation rate — for each candidate set, the resulting schedule rescored under canonical weights (`alpha = beta = 1`) must be less than or equal to the no-strategy baseline schedule rescored identically. Rescoring is mandatory because `WEIGHT_PRESET` candidates change the objective definition itself, making raw objective values incomparable. When the no-strategy solve returns `INFEASIBLE` (no baseline schedule exists), the candidate set is excluded from the non-degradation denominator and reported separately as `baseline_infeasible`; when a strategy-applied solve itself fails to commit, it counts as degradation.

The benchmark runner emits a `benchmark_report.json` that Phase 5 consumes for comparative analysis. Report structure is deterministic; scores are exactly reproducible under cached or fake LLM responses, while live-provider runs may vary.

## 9. Configuration

Extends the pydantic-settings stack:

- `LLM_PROVIDER`, `LLM_MODEL` (no defaults; run fails fast with a clear error if unset)
- `LLM_TEMPERATURE` (default 0 for reproducibility)
- `STRATEGY_MAX_ROUNDS` (default 3)
- `LLM_MAX_RETRIES` (default 2)
- `BENCHMARK_TRANSLATION_ACCURACY` (default 0.90; the corpus pass threshold referenced by acceptance criterion 2)
- `RECOVERY_LOCK_WAIT_SECONDS` (default 600; how long a contending trigger waits for the instance run lock)

`recover` performs a pre-flight configuration check before the graph starts: a missing provider or model fails immediately with a setup error, never mid-run. The LLM client sits behind a narrow interface so tests can inject a fake client with canned responses.

## 10. Command Interface

```bash
uv run python -m coe.cli recover --instance factory_demo_01 --narrative "MC-04 gearbox seized, sparks everywhere"
uv run python -m coe.cli recover --instance factory_demo_01 --narrative-file scenario.txt [--at MINUTE]
uv run python -m coe.cli explain --instance factory_demo_01
uv run python -m coe.cli benchmark fidelity --corpus data/corpus --seed 42
uv run python -m coe.cli mqtt listen
```

`recover` executes the full graph; `--at` supplies the time-resolution reference clock (Section 4.1). `explain` regenerates the rationale for the active version. `benchmark fidelity` runs the corpus evaluation without touching production tables (it operates on isolated instances). `mqtt listen` subscribes to the broker and launches recoveries automatically (Section 3.4).

## 11. Validation and Testing Strategy

### Tier 1: Translation Fidelity

- Corpus-driven pytest: per-field exact match against ground truth.
- Malformed-narrative cases assert graceful `TRANSLATION_FAILED` with no DB mutation, including multi-machine narratives, unknown-machine hallucinations, and instance mismatches.

### Tier 2: Catalog and Applier Units

- Each catalog type: valid application produces the documented payload transformation.
- Bounds enforcement: negative weights, unknown jobs/materials, non-PENDING jobs rejected with precise reasons.
- Applier determinism: same inputs produce byte-identical payloads.
- Duplicate and conflicting candidates resolve via the last-wins rule with complete warning records.

### Tier 3: Pipeline Integration (Fake LLM)

- Full graph execution with injected fake LLM responses covering: happy path, retry-then-success, retry-exhaustion fallbacks for each LLM node.
- Listener coverage with a fake MQTT client: structured-event launch skips the translate node, duplicate `message_id`s start exactly one run, and lock-wait serializes cascaded failures.
- Assert node order, state threading, and fallback behavior; no network calls.

### Tier 4: Safety Net

- Corruption injection: mutate solution JSON post-solve (move frozen op, assign failed machine) → gate refuses.
- Simulate verifier mismatch (commit then tamper) → auto-rollback fires, `active_schedule` points to parent.

### Tier 5: End-to-End (Live LLM, opt-in marker)

- `@pytest.mark.llm`: real provider, temperature 0, single canonical scenario asserting a committed, explained recovery.

### Test Infrastructure

- Fake LLM client fixture with canned responses in `tests/fixtures/llm/`.
- Benchmark corpus fixtures reused by Tier 1.
- DB-dependent tests use Docker Compose TimescaleDB from Phase 1.

## 12. Acceptance Criteria

Phase 3 is complete when:

1. `coe.cli recover` executes the full graph on `factory_demo_01` and commits a recovery version linked to its parent.
2. Translation achieves the `BENCHMARK_TRANSLATION_ACCURACY` threshold on the full corpus — reported per `kind` (MACHINE / WORKER / MATERIAL) and aggregate *(Amendment 2026-08-23)* — with failures producing `TRANSLATION_FAILED` and zero DB mutations.
3. All four catalog types apply their documented payload transformations and reject invalid parameters with machine-readable reasons.
4. The negotiation loop caps at `STRATEGY_MAX_ROUNDS` and degrades to a no-strategy solve with a warning rather than failing.
5. Per-job tardiness weights flow through to the solver objective; Phase 2 test suite passes unchanged with an empty weight map.
6. Gate rejects corrupted solutions (frozen drift, failed-machine assignment, eligibility violation) without committing.
7. Verifier detects a tampered committed version and automatically rolls back to the parent.
8. `recovery_runs` and `recovery_proposals` record complete lifecycles for every run.
9. `coe.cli explain` produces and stores a rationale for the active version.
10. `benchmark fidelity` emits a deterministic report containing translation exact-match rates, strategy validity rate, and non-degradation rate.
11. No LLM call exists outside `translate`, `strategy_loop`, and `explain`; investigation nodes are pure database functions.
12. Concurrent runs on the same instance serialize through the advisory lock; contention beyond `RECOVERY_LOCK_WAIT_SECONDS` aborts loudly.
13. Re-running an identical narrative does not duplicate telemetry events (content-derived `message_id`).
14. An MQTT event of *any* resource kind *(Amendment 2026-08-23)* triggers a committed recovery without any translation LLM call, and duplicate deliveries of the same `message_id` produce exactly one run.

## 13. Phase Boundary

Phase 3 produces a validated agentic middleware that turns a narrative disruption into a committed, explained, auditable recovery schedule. It does not:

- Measure end-to-end trigger-to-commit latency across the pipeline (Phase 5 benchmarks).
- Formulate QUBO models or invoke quantum solvers (Phase 4).
- Produce comparative latency or feasibility analyses (Phase 5).
- Allow LLM outputs to bypass schema validation or alter hard constraints.
