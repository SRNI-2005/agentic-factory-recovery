# Phase 2: Classical Optimization Engine

**Status:** Approved — Amended 2026-08-23 (worker-dependent processing times)
**Date:** 2026-08-21
**Phase:** Classical Optimization Engine

> **Amendment 2026-08-23 (user-approved):** solver alternatives carry per-worker processing durations sourced from `operation_machine_worker_times` (Phase 1 §6.3, authoritative). The payload's alternative schema gains a `workers` duration map (§5); CP-SAT models one optional interval per machine-worker combination whose duration is the assigned worker's (§6.2, §6.5); the safe horizon accounts for worker durations (§6.7); truncated recovery operations rescale worker durations proportionally (§3.1). An absent or empty `workers` map falls back to the machine-level `processing_time` with no worker requirement (pure benchmarks such as MK01). Rationale: the Nouri-derived worker layer encodes per-person speed variation (±15% skill factor); scheduling it makes worker choice affect makespan and tardiness instead of availability only.

> **Amendment 2026-08-24 (user-approved): material capacity enforcement (Option B hybrid).** Materials move from advisory-only gatekeeping into the constraint model. §5: the payload gains a root `materials` array (`{"sku", "capacity"}`, capacity = initial stock + receipts arriving before the horizon) and per-operation `materials` demand lists. §6.11: the engine enforces inventory-non-negativity with a **reservoir** constraint — consumption events at operation starts, refill events at receipt times, floor = stock on hand — so an operation can never start unless the material physically exists, and permanently over-demanded instances are INFEASIBLE by construction instead of silently committed. §7 is restructured into three layers: zero-supply pre-blocking unchanged, temporal physics in-model, aggregate shortfall demoted to an advisory warning with structured totals. §11/§12 gain the corresponding tests and criteria. Why reservoir and not AddCumulative: a fixed-capacity cumulative cannot express time-varying supply — capacity = stock + future arrivals would let an early operation consume bars that have not arrived yet, resurrecting the timing bug this amendment closes. Phase 3 owns the business reaction (protect high-priority jobs, suspend/defer low-priority ones) per its amendment of the same date. Robustness riders folded into the same amendment (all user-approved 2026-08-24): (a) the gate/invariant exempts frozen echoes from the failed-machine membership check — historic work on a since-stripped machine cannot be rewritten and must not fail recovery; (b) variable-domain spans include Σ temporary machine-downtime durations so an operation may legitimately wait out maintenance (the old behavior declared such schedules infeasible); (c) suspension memory — the builder skips jobs whose persisted `jobs.status` is `BLOCKED` and lists them as `JOB_SUSPENDED`, so Phase 3 sacrifices are remembered across runs instead of re-litigated; (d) status truth — machines with `machines.status = 'FAILED'` are stripped even without passing them on the CLI, and workers with `workers.status = 'UNAVAILABLE'` receive full-horizon unavailability, making the mirrored columns authoritative alongside windows.

> **Amendment 2026-08-24 (fourth, user-approved): multi-worker operational default.** `SOLVER_NUM_SEARCH_WORKERS` flips from 1 to **8**: speed-first for day-to-day solves. Empirically verified same day (5-scenario × 3-run probe): 8-worker runs are non-reproducible across invocations — alternative optima on mk01, a ~6% recovery-makespan swing — and on one disrupted instance they found solutions single-thread never reached at the same budget. Consequently the determinism contract (§12-9, §11 Tier 4) is now conditioned on single-worker mode, and every reproducibility consumer — benchmark proofs, byte-determinism pins, audit regeneration via stored `payload_json` — MUST set `num_search_workers=1`. The knob remains fully wired (`--workers`, env).

> **Amendment 2026-08-24 (third, user-approved): honest material horizons and solver statuses.** Empirically verified against live data (H = 1827 while every scenario receipt lands at t = 2000): the strict pre-horizon receipt filter silently dropped ALL deliveries, making defer-to-delivery recoveries impossible and `EXPEDITE_MATERIAL` inert past H; separately, capacity defined as "stock + pre-horizon receipts" would have double-counted any receipt that DID land inside H (once in capacity, once as a refill event). Fixes: (1) §5/§7 — `material_receipts` lists ALL arrivals regardless of horizon (post-horizon refills are inert to the solver but audit-relevant, and a deferred operation must see its delivery); `materials[].capacity` is restated as **initial stock at t = 0**, with arrivals entering exclusively as refill events; the horizon's downtime term merges overlapping windows. (2) §3.2 — CP-SAT `UNKNOWN` passes through as status `"UNKNOWN"` instead of being conflated with `INFEASIBLE`; `INFEASIBLE` henceforth strictly means *proven* impossibility. Rationale: Phase 3's material-reactive back-edge routes on INFEASIBLE — mislabeling an unfinished search would suspend jobs merely because the budget ran out.

> **Amendment 2026-08-23 (second, user-approved):** five consistency fixes against the Phase 1 data model. (1) §3.3, §11, §12: the committer mirrors schedule state onto `operations.status` inside its transaction. (2) §3.1, §5, §6.9, §12: the payload builder derives default per-job tardiness weights from `jobs.priority` (convention: **1 = most important**, per Phase 1 plan) through the existing `job_tardiness_weights` mechanism — derived last, against the run's effective α/β; Phase 3 strategy candidates may override entries. (3) §6.8, §12: a null deadline contributes zero tardiness (required by pure benchmarks such as MK01, where every deadline is null). (4) §10, §12: CLI recovery injects failures through the Phase 1 ingestion path (`ingest_telemetry_event`) instead of direct SQL, restoring audit parity with MQTT triggers and making `machine restore`'s telemetry-default clock reliable. (5) §3.1: roster availability converts to unavailability as its exact complement within `[0, H]` (H = the §6.7 conservative horizon), closing the silent after-shift availability gap.

## 1. Purpose

Phase 2 implements the deterministic CP-SAT solver pipeline that computes both baseline and recovery schedules. It consumes the data model and scenario produced by Phase 1 and outputs committed, versioned schedules to the database.

The solver is a pure function: it accepts a self-contained JSON payload and returns an optimized schedule. It has no database access, no LLM dependency, and no side effects. All database interaction is handled by the payload builder (input) and committer (output).

This phase does not implement agents, LLM calls, or MQTT-triggered recovery. Recovery is triggered manually via CLI with explicit failed-machine arguments.

## 2. Scope

### Included

- A `payload_builder` module that reads Phase 1 schema and produces solver-ready JSON.
- A `solver.engine` module implementing Google OR-Tools CP-SAT with the full constraint model.
- A `solver.committer` module that writes solved schedules to versioned database tables.
- Full constraint model: precedence, release times, no-overlap, machine eligibility, asymmetric processing times, sequence-dependent setup times, worker assignment and availability, worker-dependent processing durations *(Amendment 2026-08-23)*, worker absence windows *(Amendment 2026-08-23)*, machine downtime, deadline tardiness, and material gatekeeping.
- Weighted multi-objective optimization (makespan + tardiness) with configurable weights.
- Temporal material capacity enforcement in the engine (reservoir constraints over per-SKU stock, receipts, and operation demands) *(Amendment 2026-08-24)*.
- Both baseline and recovery schedule solving.
- Fixed-vs-fixed conflict clipping: frozen operations take precedence over overlapping downtime and worker-unavailability windows, with interventions recorded as payload warnings.
- Recovery with support for multiple simultaneously failed machines.
- Machine restore command returning a repaired machine to future payloads.
- Append-only schedule versioning with rollback support.
- Benchmark validation against MK01 known optimal (makespan = 40).
- CLI commands for solving, viewing, restoring machines, and rolling back schedules.

### Excluded

- LangGraph agents and LLM calls.
- MQTT-triggered recovery (recovery is CLI-only).
- Narrative-to-payload translation.
- Post-hoc schedule explainability.
- QUBO/QAOA quantum formulation.
- Discrete-event schedule simulation.

## 3. Architecture

Three modules with strict one-way data flow:

```text
DB (Phase 1 schema)
    → payload_builder     (reads DB → produces JSON)
        → solver.engine   (JSON in → schedule JSON out, pure function)
            → committer   (schedule JSON → writes to DB)
```

### 3.1 `coe.solver.payload_builder`

Queries the database for a given instance and produces a self-contained JSON payload. Responsibilities:

- Reads jobs, operations, machines, and `operation_machine_alternatives` with processing times.
- Reads `operation_machine_worker_times` *(Amendment 2026-08-23)*: each alternative carries a `workers` map of eligible worker → specific duration. The Phase 1 importer guarantees every `(operation_id, machine_id)` alternative has at least one eligible worker row; a source instance imported without the worker layer (pure benchmarks such as MK01) yields an empty map and falls back to the machine-level `processing_time`.
- Reads `job_families` and `setup_times` per machine.
- Converts worker availability windows (positive) into unavailability intervals (negative) *(Amendment 2026-08-23, second)*: the builder first computes the conservative horizon `H` exactly as §6.7 specifies, then emits the complement of the unioned availability windows within `[0, H]` — including the leading edge before the first window and the trailing stretch after the last one — so workers are never implicitly available outside their shifts.
- Reads `worker_absence_windows` directly — already negative semantics, no conversion needed *(Amendment 2026-08-23)*.
- Reads `machine_downtime_windows` directly (already negative semantics).
- Handles materials in three layers *(restructured by Amendment 2026-08-24; details in §7)*: emits `materials` capacities (initial stock + receipts strictly before the horizon) and per-operation `materials` demands from `operation_bom`; hard-blocks operations whose SKU has zero total supply (`MATERIAL_UNAVAILABLE`, cascading); records partial shortfalls as advisory warnings only — enforcement lives in the engine (§6.11). Blocked operations contribute no demands, no capacities, and no events.
- For recovery payloads: reads the current active schedule.
  - **Completed operations:** Marked as frozen with their historic fixed assignments.
  - **In-progress on healthy machines:** Marked as frozen to prevent disrupting ongoing work.
  - **In-progress on the failed machine (INTERRUPTED):** MUST NOT be frozen (which would crash the solver by conflicting with the downtime window). Instead, it is dynamically truncated, assigned a remaining processing time, set to `PENDING`, and given alternatives. The originally assigned worker is explicitly UN-FROZEN for the remaining duration so they are not stranded. For truncated operations *(Amendment 2026-08-23)*, the remaining processing time replaces each alternative's machine-level duration and worker durations are rescaled by the same ratio (remaining ÷ original base, floored at 1 minute), preserving the skill spread.
  - **Failed Machine Alternatives:** For each failed machine: if its downtime `until` is `null` (permanent), it is stripped from all operation alternative lists. If the downtime is temporary, it remains in the alternative lists so the solver has the option to simply "wait it out" if setup times on other machines are too penalizing.
  - **Zero Alternatives (Dead End):** If stripping a permanently failed machine leaves any operation with an empty alternatives list, the `payload_builder` must immediately mark it as `status = BLOCKED` with reason `NO_CAPABLE_MACHINES` and exclude it from the solver payload.
- **Blocked-Op Cascade:** Blocking an operation automatically blocks all later-sequence operations in the same job with reason `PREDECESSOR_BLOCKED`, so no successor ever loses its precedence anchor.
- **Initial Family Seeding:** Derives `machine_initial_families`: for each machine with entries in the active schedule, the family of the last operation committed on it. Machines absent from the map are treated as clean (`from_family: null`).
- **Conflict Clipping:** After freeze/truncate decisions, clips any remaining downtime window overlapping a frozen interval to start at the frozen op's end (drops it if fully covered). Applies the same rule to worker-unavailability windows against frozen ops assigned to that worker. Every clip or drop appends an entry to the payload `warnings` array.
- **Setup Matrix Hygiene:** Warns when a setup pair is defined in one direction only (asymmetric row missing).
- Derives default per-job tardiness weights *(Amendment 2026-08-23, second)*: with priorities `p_j` (1 = most important) and `n` the count of jobs holding a non-null deadline, `job_tardiness_weights[j] = beta · n · (p_max + 1 − p_j) / Σᵢ(p_max + 1 − pᵢ)` over deadline-bearing jobs. The map is mean-preserving around `beta` (identical objective magnitude to uniform weighting), self-degrades to uniform `beta` when all priorities are equal, and is omitted entirely when no job has a deadline. Jobs without deadlines never appear in the map. Derivation runs last — after any Phase 3 strategy application — using the run's effective `alpha`/`beta`, so a `WEIGHT_PRESET` override rescales the defaults rather than being nullified by them.
- Outputs a deterministic JSON payload. The same database state and parameters produce the same payload.

### 3.2 `coe.solver.engine`

A pure function with no database access. Accepts a JSON payload, builds a CP-SAT model, solves it, and returns a solution JSON.

If the payload contains zero PENDING operations, the engine short-circuits without building a CP-SAT model: status `OPTIMAL`, makespan equal to the latest frozen end (0 if none), and total tardiness evaluated from frozen completion times against deadlines.

Inputs: the solver payload JSON (defined in Section 5).

Outputs:
- Assigned machine, worker, start time, end time, and setup time for each scheduled operation.
- Objective value (weighted sum), makespan, and total tardiness.
- Solver status: `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, or `UNKNOWN` *(Amendment 2026-08-24 third)* — `INFEASIBLE` strictly means proven impossibility; `UNKNOWN` means the budget expired without a proof or a solution and is never committable.
- Solve duration in seconds.

### 3.3 `coe.solver.committer`

Takes the solution JSON and writes it to the database:
- Creates a new `schedule_versions` row.
- Writes `schedule_entries` for each assigned operation.
- Links to the parent version for recovery schedules.
- Wrapped in a database transaction: any failure rolls back the entire commit.
- Only commits solutions with status `OPTIMAL` or `FEASIBLE`. `INFEASIBLE` results are logged to standard application logs but not committed.
- Mirrors schedule state onto `operations.status` inside the same transaction *(Amendment 2026-08-23, second)*: each written entry with `end <= now` becomes `COMPLETED`, `start <= now < end` becomes `IN_PROGRESS`, otherwise `SCHEDULED`; every operation listed in the payload's `blocked_operations` becomes `BLOCKED`. The committer receives the recovery reference clock as `now`; baseline commits pass no clock and mark every entry `SCHEDULED`. Rollback never rewinds these mirrors — `schedule_versions` remains the audit truth. Additionally *(Amendment 2026-08-24)*, every job named in the payload's `suspended_jobs` gets `jobs.status = BLOCKED` in the same transaction, completing the suspension-memory loop with the builder.

## 4. Schedule Output Schema

### `schedule_versions`

- `id`
- `instance_id`
- `version_number` (auto-incrementing per instance)
- `schedule_type` (`BASELINE` or `RECOVERY`)
- `solver_status` (`OPTIMAL`, `FEASIBLE`, `INFEASIBLE`)
- `objective_value`
- `makespan`
- `total_tardiness`
- `alpha_weight`
- `beta_weight`
- `time_limit_seconds`
- `solve_duration_seconds`
- `failed_machine_ids` (JSONB array of machine ids; null for baseline)
- `parent_version_id` (nullable FK to self; null for baseline)
- `rolled_back` (boolean, default false)
- `committed_at`
- `payload_hash` (SHA-256 of the input JSON, serialized with sort_keys=True for canonicalization)
- `payload_json` (JSONB; the complete solver input payload for audit and reproducibility)

### `schedule_entries`

- `id`
- `instance_id`
- `version_id` (FK to `schedule_versions.id`)
- `operation_id`
- `machine_id`
- `worker_id` (nullable)
- `start_time`
- `end_time`
- `processing_time`
- `setup_time` (setup duration before this operation; 0 if none)
- `is_frozen` (boolean; true for operations carried from a prior schedule during recovery)
- `status` (`SCHEDULED` or `FROZEN`)

### `active_schedule` (view)

Returns entries from the latest committed, non-rolled-back, feasible schedule version for each instance:

```sql
SELECT se.* FROM schedule_entries se
JOIN schedule_versions sv ON se.version_id = sv.id
WHERE sv.id = (
    SELECT id FROM schedule_versions
    WHERE instance_id = se.instance_id
    AND solver_status IN ('OPTIMAL', 'FEASIBLE')
    AND rolled_back = false
    ORDER BY version_number DESC LIMIT 1
);
```

Rollback sets `rolled_back = true` on the current version, causing the view to select the previous version. No rows are deleted.

## 5. Solver Payload JSON Schema

The payload is the contract between `payload_builder` and `engine`. The solver sees only this structure.

```json
{
  "instance_id": "factory_demo_01",
  "schedule_type": "RECOVERY",
  "parent_version_id": 1,

  "config": {
    "alpha": 1.0,
    "beta": 1.0,
    "time_limit_seconds": 60,
    "normalize_objectives": true
  },

  "machines": ["MC-01", "MC-02", "MC-03", "MC-05", "MC-06", "MC-07", "MC-08"],

  "machine_initial_families": { "MC-03": "FAM-A", "MC-05": "FAM-B" },

  "warnings": [
    {
      "type": "DOWNTIME_CLIPPED",
      "machine_id": "MC-05",
      "window": [200, 350],
      "clipped_to": [310, 350],
      "reason": "overlaps frozen operation J02-O1"
    }
  ],

  "jobs": [
    {
      "job_id": "J-01",
      "family_id": "FAM-A",
      "release_time": 0,
      "deadline": 720,
      "priority": 1,
      "operations": [
        {
          "operation_id": "J01-O1",
          "sequence": 1,
          "status": "COMPLETED",
          "alternatives": [],
          "frozen": { "machine_id": "MC-02", "worker_id": "W-01", "start": 0, "end": 15 }
        },
        {
          "operation_id": "J01-O2",
          "sequence": 2,
          "status": "PENDING",
          "materials": [{"sku": "STEEL-304", "quantity": 6}],
          "alternatives": [
            { "machine_id": "MC-03", "processing_time": 12, "workers": { "W-01": 11, "W-03": 13 } },
            { "machine_id": "MC-05", "processing_time": 18, "workers": { "W-02": 19 } }
          ],
          "frozen": null
        }
      ]
    }
  ],

  "machine_downtime": [
    { "machine_id": "MC-05", "from": 200, "until": 350, "reason": "MAINTENANCE" }
  ],

  "materials": [
    { "sku": "STEEL-304", "capacity": 10 }
  ],

  "material_receipts": [
    { "sku": "STEEL-304", "quantity": 4, "available_at": 500 }
  ],

  "worker_unavailability": [
    { "worker_id": "W-01", "from": 480, "until": 960 }
  ],

  "setup_times": [
    { "machine_id": "MC-03", "from_family": "FAM-A", "to_family": "FAM-B", "duration": 8 },
    { "machine_id": "MC-03", "from_family": null, "to_family": "FAM-A", "duration": 5 }
  ],

  "job_tardiness_weights": { "J-01": 1.25, "J-02": 0.75 },

  "blocked_operations": [
    { "operation_id": "J05-O3", "reason": "MATERIAL_UNAVAILABLE", "material_sku": "STEEL-304" }
  ]
}
```

Design principles:
- Failed machines are absent from the `machines` list. The solver never sees them. With multiple failures, every failed machine is stripped.
- `release_time` lower-bounds the start of every non-frozen operation of its job.
- `machine_initial_families` seeds the dummy-start arc of the setup circuit; a missing entry means the machine starts clean and uses the `from_family: null` row.
- A missing `(from_family, to_family)` row in `setup_times` means duration 0.
- `job_tardiness_weights` (map of job_id → weight ≥ 0) overrides the global `beta` per job. The payload builder derives it from `jobs.priority` by default (§3.1; mean-preserving around `beta`, 1 = most important) *(Amendment 2026-08-23, second)*; Phase 3 strategy candidates may override entries. An absent map means uniform `beta` for every job.
- `warnings` records payload_builder interventions (clipped or dropped windows) for downstream explainability.
- Completed operations and operations in-progress on healthy machines have empty `alternatives` and a `frozen` block with fixed assignments.
- Operations interrupted by a machine failure are NOT frozen; they are dynamically truncated to their remaining processing time, set to `PENDING`, and given alternatives excluding the failed machine.
- Worker unavailability is pre-converted from positive availability windows by the payload builder.
- Each alternative's `workers` map binds an eligible worker to their specific duration (`operation_machine_worker_times`, authoritative) *(Amendment 2026-08-23)*: the engine schedules the assigned worker's duration, so worker choice affects makespan and tardiness. An absent or empty map means no worker is required and the machine-level `processing_time` applies.
- `materials` (root) lists every demanded SKU with its `capacity` = **initial stock at t = 0** *(restated by Amendment 2026-08-24 third — arrivals enter exclusively as refill events, never folded into capacity)*. `material_receipts` (root) lists **all** arrivals as `{sku, quantity, available_at}`, regardless of horizon: post-horizon refills are inert to the solver but audit-relevant, and an operation deferred past a delivery must see that delivery. Per-operation `materials` lists carry `{sku, quantity}` from `operation_bom`; frozen, completed, and blocked operations carry empty/absent lists. The engine treats a demanded SKU missing from the root array as capacity 0 with no receipts (defensive; normally pre-blocked).
- `failed_machines` (root, Amendment 2026-08-24 robustness): sorted names of EVERY machine excluded due to failure — CLI-named failures and status-derived strips alike — so the committer's `failed_machine_ids` audit column records the full truth even when stripping came from `machines.status` rather than the CLI. Baseline payloads emit `[]` unless status-truth stripping fired.
- Blocked operations are excluded from solving and listed with a reason for downstream explainability.
- Setup times use `from_family: null` for initial setup on a machine.
- `machine_downtime` entries with `until: null` represent permanent failures. Note: Permanently failed machines are stripped from the `machines` array entirely, and their downtime entries must ALSO be omitted from `machine_downtime` to prevent the solver from throwing a KeyError when looking up the non-existent machine.

## 6. CP-SAT Constraint Model

### 6.1 Precedence

Within each job, operations are chained by sequence number:

```python
model.Add(op[j][k].start >= op[j][k-1].end)
```

Frozen operations have fixed start and end times and serve as anchors for subsequent operations.

Each job's `release_time` lower-bounds the start of its non-frozen operations (`model.Add(op[j][k].start >= release[j])` for every pending operation).

### 6.2 Machine Assignment

Each pending operation gets one `NewOptionalIntervalVar` per eligible machine-worker combination *(Amendment 2026-08-23)*. The combination's duration is the assigned worker's duration from the alternative's `workers` map, or the machine-level `processing_time` when the map is empty or absent. `AddExactlyOne` enforces that exactly one combination is selected across all alternatives.

### 6.3 No-Overlap (Machine Capacity)

One `AddNoOverlap` per machine. The interval list includes scheduled operation intervals, frozen operation intervals, downtime intervals, and setup intervals.

### 6.4 Machine Downtime

Downtime windows are fixed intervals added to the machine's no-overlap constraint. In practice, permanently failed machines never reach the solver (stripped from `machines`, their downtime entries omitted), so every window the engine sees references a listed machine and has a finite `until`; if a finite-bound window is absent, the horizon is used defensively as its end time.

### 6.5 Worker Assignment

Each worker is modeled as a no-overlap resource. Because assignment happens per machine-worker combination (§6.2), the selected combination's interval *is* the worker interval; it is added to that worker's no-overlap constraint. Worker unavailability windows are fixed intervals on the worker's no-overlap constraint.

### 6.6 Sequence-Dependent Setup Times (Explicit-Interval Pattern)

The model strictly uses the explicit-interval pattern with dummy nodes to avoid double-counting durations and to safely handle optional machine assignments. For each machine:

1. **Dummy Nodes:** Two fixed, zero-duration dummy nodes are created: Dummy Start ($S_M$) and Dummy End ($E_M$). `AddCircuit` requires a closed loop, so the circuit must flow from $S_M$ through the scheduled operations, to $E_M$, and back to $S_M$.
2. **Optional Nodes:** Each pending operation eligible for this machine contributes an *optional* node to the circuit. The presence of this node is exactly tied to the boolean literal representing the machine assignment: `assignment[op, M]`.
3. **Circuit Transits:** The `AddCircuit` arc transit durations between all nodes are set strictly to **0**. The arc from $S_M$ to any operation represents the initial setup. The arcs to $E_M$ and from $E_M$ to $S_M$ have 0 duration and 0 setup.
4. **Setup Intervals:** Between any two operation nodes (or from $S_M$ to an operation), an explicit setup `NewOptionalIntervalVar` is created and added to the machine's `AddNoOverlap` constraint.
5. **Boolean Linking:** The setup interval's presence literal is the logical AND of the machine assignment and the arc selection. The model enforces: `If arc(Op1 -> Op2) == True AND families differ`, then `setup_interval_presence == True`. Because `arc(Op1 -> Op2)` can only be true if both nodes are present in the circuit, this automatically safely handles operations assigned to other machines.
6. If the families are identical, the setup interval presence is `False` (zero duration gap).

Initial setup (first pending operation on a machine) uses the row matching the machine's committed family from `machine_initial_families` when present, falling back to the `from_family: null` row for a clean machine; it is linked to the arc originating from the Dummy Start ($S_M$) node. A missing `(from_family, to_family)` row means duration 0.

### 6.7 Safe Horizon Calculation

To prevent "horizon overflow" where a valid schedule is rejected because it extends past a naive bound, the solver's horizon must be strictly computed as:

`Horizon = max(Σ max processing(pending ops) + Σ max setups + Σ temporary downtime durations, max(frozen end), max(release_time))`, where "max processing" *(Amendment 2026-08-23)* is the largest duration available to the operation: the maximum over each alternative's `workers` map values and machine-level `processing_time`.

Permanent downtimes (`until: null`) are excluded because their machine is stripped and consumes no scheduling capacity. The frozen-end and release-time terms guarantee that fixed historic intervals and future-dated jobs always fit inside the horizon.

### 6.8 Deadlines and Tardiness

```python
tardiness[j] = model.NewIntVar(0, horizon, f'tardiness_{j}')
model.AddMaxEquality(tardiness[j], [0, last_op[j].end - deadline[j]])
```

A job with a null `deadline` *(Amendment 2026-08-23, second)* contributes zero tardiness: no tardiness variable is created for it and it is excluded from the weighted sum. This is required by pure benchmarks such as MK01, where every deadline is null.

### 6.9 Objective Function

```python
model.Minimize(alpha * normMakespan + beta * normTardiness)
```

When `normalize_objectives` is enabled, both makespan and total tardiness are divided by the horizon to yield a `[0.0, 1.0]` ratio before weighting (`normMakespan = makespan / horizon`), so that `alpha` and `beta` are directly comparable across different instances. When `job_tardiness_weights` is present, tardiness is weighted per job (`Σ_j w_j * normTardiness_j`) with absent entries using the global `beta`. Weights arrive exclusively via the payload map; the engine never sees raw priorities *(Amendment 2026-08-23, second)*.

### 6.10 Frozen Operations (Recovery)

Frozen operations are added as fixed-interval constants (not decision variables) to the relevant machine and worker no-overlap constraints. They occupy time on their assigned resources but are not subject to optimization.

## 6.11 Material Capacity (Reservoir) *(Amendment 2026-08-24)*

Raw materials are *consumed*, not released when an operation finishes — so inventory is a depleting resource with timed replenishment. The faithful CP-SAT primitive is the reservoir constraint, not a renewable cumulative:

- **One reservoir per demanded SKU.** Events:
  - for each PENDING operation whose `materials` list carries the SKU: a consumption event at that operation's start variable with level change `−quantity`, activated by an any-combo boolean that is true iff the operation is scheduled at all (OR of its combo literals, linked with the same implication pattern as circuit node presence);
  - for each receipt of that SKU — **all arrivals are emitted** *(Amendment 2026-08-24 third)*: a fixed refill event at `available_at` with change `+quantity`.
- **Floor:** `−capacity`, where capacity comes from the payload's root `materials` row (initial stock at t = 0; arrivals act exclusively through their refill events). **Ceiling:** unconstrained-large (`Σ quantities + Σ refill quantities`).
- **Semantics:** inventory-on-hand never drops below zero at any point, hence no operation starts unless its bars physically exist at its start time. The solver may freely delay an operation past a receipt to wait for stock. Instances whose total demand can never be covered come back `INFEASIBLE` — nothing is committed, and Phase 3 reacts (its 2026-08-24 amendment).
- Blocked/frozen/completed operations contribute no events. Demanded SKUs missing from the root `materials` array get capacity 0 (normally unreachable because of pre-blocking).
- **Suspension memory *(Amendment 2026-08-24)*:** jobs whose persisted `jobs.status` is `BLOCKED` are excluded from the payload entirely and listed in `blocked_operations` with reason `JOB_SUSPENDED` — a Phase 3 sacrifice survives into every future payload until something un-blocks the job. The payload's root `suspended_jobs` array (job names, sorted) carries the run's own suspensions so the committer can mirror `jobs.status = BLOCKED` in its transaction.
- Determinism: events are emitted in payload iteration order; identical payloads build identical reservoirs.

> **Amendment 2026-08-25 (user-approved): time-phased shortfall warnings.** MATERIAL_SHORTFALL additionally fires when cumulative early-released demand exceeds stock-plus-timely-receipts at some release prefix, even when totals suffice. Dead-block (MATERIAL_UNAVAILABLE) semantics unchanged; warning shape unchanged. Rationale: restores Phase 3 §4.3 step-3 DEFER ("only timing is wrong") reachability.

## 7. Material Handling *(restructured by Amendment 2026-08-24)*

Three layers, in order:

1. **Zero-supply pre-block (builder, unchanged):** a SKU with no stock and no receipts blocks every operation whose BOM references it (`MATERIAL_UNAVAILABLE`), cascading `PREDECESSOR_BLOCKED`. Provably impossible work never reaches the solver.
2. **Temporal physics (engine, §6.11):** every remaining operation's demand feeds a per-SKU reservoir against capacity = initial stock, with all arrivals present as refill events. The solver resolves receipt-timed conflicts by delaying operations; permanently over-demanded instances return `INFEASIBLE`.
3. **Advisory totals (builder warning):** when total demand still exceeds total supply for a SKU, the payload records `{type: MATERIAL_SHORTFALL, material_sku, total_supply, total_demand}` as before. It is informational — a heads-up for Phase 3 strategists — and no longer the enforcement mechanism.

Rationale: aggregate totals are timing-blind — two operations drawing the same stock before a shared receipt pass the totals check while driving inventory negative at t=0. Only the solver sees start times, so only the solver can enforce the floor. Phase 3 reacts to INFEASIBLE results and structured shortfall warnings by suspending or deferring lower-priority jobs through its strategy catalog.

## 8. Schedule Versioning and Rollback

Every solve creates a new `schedule_versions` row. Previous versions are never modified or deleted.

- **Baseline solve:** `schedule_type = BASELINE`, `parent_version_id = NULL`.
- **Recovery solve:** `schedule_type = RECOVERY`, `parent_version_id` references the version being recovered from.
- **Rollback:** The current version is marked as `rolled_back = true`. The `active_schedule` view (defined in §4) selects the latest non-rolled-back, feasible version.
- **Rollback floor:** Rolling back is refused when the active version is the only non-rolled-back feasible version; the chain is never allowed to become empty.
- **Concurrency:** `version_number` is allocated inside the commit transaction under a row lock on the instance's latest version, backed by a `UNIQUE (instance_id, version_number)` constraint.

This provides:
- A complete audit trail (baseline → recovery₁ → recovery₂).
- Instant rollback without data deletion.
- A natural reference chain for the explainability layer in Phase 3.
- Full reproducibility via the stored `payload_json` on each version.

## 9. Configuration

Phase 2 configuration extends Phase 1's `pydantic-settings` approach:

- `SOLVER_TIME_LIMIT_SECONDS` (default: 60)
- `SOLVER_ALPHA_WEIGHT` (default: 1.0)
- `SOLVER_BETA_WEIGHT` (default: 1.0)
- `SOLVER_NORMALIZE_OBJECTIVES` (default: true)
- `SOLVER_RANDOM_SEED` (default: 42)
- `SOLVER_NUM_SEARCH_WORKERS` (default: 8. Multi-worker is the operational default for speed; **set 1 whenever outputs must be reproducible** — benchmark proofs, byte-determinism pins, audit regeneration. Verified empirically 2026-08-24: 8-worker runs differ across invocations, including alternative optima on mk01 and a ~6% makespan swing on recovery payloads; they also solved a recovery instance single-thread starved at the same budget.)

These are defaults. The `payload_builder` can override them per-run via CLI arguments or, in Phase 3, via agent-provided parameters. *(Robustness wiring, Amendment 2026-08-24: every knob is live — the builder seeds payload `config` from Settings for any value not explicitly overridden on the CLI, and the engine honors `random_seed`, `normalize_objectives`, and `num_search_workers` from that config.)*
**Constraint:** The payload builder must enforce `alpha >= 0`, `beta >= 0`, and `(alpha + beta) > 0`. Permitting negative weights would cause the solver to maximize time/tardiness. A zero-weight objective sum (`Minimize(0)`) causes the solver to legally return the very first feasible schedule it finds, which can produce highly irrational long-running schedules.

### 9.1 Weight Resolution Order *(Amendment 2026-08-23, second)*

Objective weights resolve in layers; **later layers win**:

| Layer | Scope | Source | Rule |
| --- | --- | --- | --- |
| 1. ENV / settings defaults | global scalars | `SOLVER_ALPHA_WEIGHT`, `SOLVER_BETA_WEIGHT` | operator baseline (`1.0` / `1.0`) |
| 2. CLI flags | global scalars | `--alpha`, `--beta` | per-run human override |
| 3. `WEIGHT_PRESET` (Phase 3) | global scalars | validated strategy candidate | per-incident override; multiple presets apply last-wins |
| 4. Priority derivation (§3.1) | per-job map | `jobs.priority` shape scaled by effective `beta` | runs **last**, after layer 3; mean-preserving around effective `beta`; priority provides relative shape only, never absolute magnitude |
| 5. `TARDINESS_WEIGHT` (Phase 3) | single job | validated strategy candidate | most specific; overwrites that job's entry only |

Merge semantics of the final map: derivation **fills absent jobs from the formula and leaves explicitly set entries untouched** — it never wholesale-replaces a map containing explicit `TARDINESS_WEIGHT` entries. Roles are strictly separated: env/CLI/preset own *how much lateness matters in aggregate*; priority owns *which jobs matter relative to each other*; candidates own surgical exceptions. In Phase 2 only layers 1, 2, and 4 exist — there is no LLM participation anywhere in this chain.

## 10. Command Interface

```bash
uv run python -m coe.cli solve baseline --instance factory_demo_01
uv run python -m coe.cli solve baseline --instance factory_demo_01 --alpha 2.0 --beta 0.5 --time-limit 120
uv run python -m coe.cli solve recovery --instance factory_demo_01 --failed-machine MC-04 MC-07
uv run python -m coe.cli machine restore --instance factory_demo_01 --machine MC-04 [--at MINUTE]
uv run python -m coe.cli schedule show --instance factory_demo_01
uv run python -m coe.cli schedule rollback --instance factory_demo_01
```

`solve baseline` creates the initial schedule. `solve recovery` resolves a reference clock (`--at MINUTE`, else the instance's latest telemetry `occurred_at`; error when neither exists) and injects each specified failure through the Phase 1 ingestion path *(Amendment 2026-08-23, second)*: `ingest_telemetry_event` with `event_type = FAILURE`, `occurred_at` = the resolved clock, and a content-derived `message_id` (`cli-{sha256(instance|machine)[:8]}`) — inheriting the telemetry audit row, advisory-lock interval union, idempotent re-runs (identical command ⇒ duplicate suppressed), and the automatic `FAILED` status flip; no direct SQL is written. It then reads the active schedule, freezes completed and in-progress operations against that same clock, truncates interrupted work, removes every failed machine, and re-solves; the committer receives the clock for status mirroring (§3.3). Recovery solves additionally enforce a **quality floor of 180 s** *(hardening E)* — `_recovery_floor` raises any smaller budget, including the Settings default, because disrupted payloads need materially longer search than baselines; an explicit `--time-limit` above 180 is honored as-is. Engine-side (CLI-invisible), every solve runs a **two-phase relax-then-repair warm start**: a setup-free relaxation is solved first and its complete variable assignment is replayed into the full model as a solution hint — complete hints prune search while partial hints waste it (CP-SAT Primer) — falling back to the deterministic greedy plan when the relaxation starves. `machine restore` closes the open-ended downtime window (`downtime_until = at`, defaulting to the instance's latest telemetry `occurred_at`; an explicit `--at` is required when no telemetry exists) and sets the machine status back to `ACTIVE`; it does not trigger a solve — the machine simply re-enters future payloads. `schedule show` prints the active schedule. `schedule rollback` reverts to the previous version and refuses when the active version is the last remaining one.

## 11. Validation and Testing Strategy

### Tier 1: Benchmark Correctness

Solve the pure MK01 instance (10 jobs, 6 machines, no augmentations) and assert:
- Makespan = 40 (published best-known optimal).
- Solver status = `OPTIMAL`.
- All precedence constraints satisfied.
- No machine overlaps.

### Tier 2: Constraint Satisfaction (Augmented Scenario)

Solve `factory_demo_01` (30 jobs, 8 machines, full model) and verify:
- Every operation assigned to an eligible machine.
- Every operation's scheduled duration equals the assigned worker's duration from its `workers` map, or the machine-level `processing_time` when no worker is assigned *(Amendment 2026-08-23)*.
- Every precedence respected.
- No machine overlaps (including setup intervals and downtime).
- No worker overlaps (including unavailability windows).
- No blocked operations in the schedule.
- All setup times correct for family transitions.
- Tardiness computed correctly against deadlines.
- Payload contains `job_tardiness_weights` derived from priorities, mean-preserving around `beta`; jobs without deadlines are absent from the map *(Amendment 2026-08-23, second)*.
- Committed entries carry mirrored `operations.status`: future entries `SCHEDULED`, clock-spanning entries `IN_PROGRESS`, ended entries `COMPLETED`, blocked operations `BLOCKED` *(Amendment 2026-08-23, second)*.

### Tier 2b: Individual Constraint Tests

Small, hand-crafted fixture payloads (2-3 jobs, 2-3 machines) where each constraint is the binding factor:

| Test | Setup | Expected Behavior |
| --- | --- | --- |
| Worker unavailable | W1 only eligible worker for Op A. W1 unavailable t=0–100. | Op A starts at t≥100. |
| Machine downtime | M1 only eligible machine for Op A. M1 down t=50–150. | Op A before t=50 or after t=150. |
| Material blocked | Op A material unavailable. Listed in `blocked_operations`. | Op A absent from schedule. |
| Setup enforced | Two ops, different families, same machine. Setup = 20. | Gap ≥ 20 between ops on that machine. |
| Setup skipped | Two ops, same family, same machine. | No setup gap. |
| Initial setup | First op on machine. `from_family: null`, setup = 10. | Setup interval precedes operation. |
| Worker no-overlap | W1 eligible for Op A and Op B. Both ops forced to start at t=0 via deadlines/precedence. | W1 assignments don't overlap (one is delayed). |
| Worker duration honored *(Amendment 2026-08-23)* | Op A only eligible on M1; `workers` map {W1: 10, W4: 20}; W1 unavailable for the whole horizon. | Assigned worker is W4 and end − start = 20. |
| No-worker fallback *(Amendment 2026-08-23)* | Alternative with empty/absent `workers` map (benchmark-style). | Duration = machine-level `processing_time`; no worker constraint applied. |
| Null deadline *(Amendment 2026-08-23, second)* | Job with `deadline: null` completing at t=80. | No tardiness variable; objective excludes the job. |
| Material timing *(Amendment 2026-08-24)* | Stock 8; receipt +2 at t=500; A(5) and B(5) both immediately eligible. | Both scheduled; the later one starts ≥ 500. |
| Permanent over-demand *(Amendment 2026-08-24)* | Stock 10; no receipts; A(6) and B(6). | `INFEASIBLE`; no live assignments. |
| Deadline tardiness | Job deadline = 50. Minimum feasible completion = 80. | Tardiness = 30. |
| Frozen respected | Op A frozen at M1 t=0–15. Op B eligible on M1. | Op B starts at t≥15 on M1. |
| Release time | Job `release_time=100`, machine idle at 0. | Op starts ≥ 100. |
| Downtime clipped | Frozen op M1 t=100–200; maintenance M1 t=150–250. | Window becomes 200–250; warning emitted. |
| Downtime dropped | Frozen op M1 t=100–300; maintenance M1 t=150–250. | Window removed; warning emitted. |
| Worker window clipped | Frozen op on W1 t=100–200; W1 unavailable t=150–250. | Unavailability becomes 200–250. |
| Initial setup from history | Machine's last committed family FAM-A; next op FAM-B; setup A→B=10. | Setup interval of 10 precedes first new op. |
| Blocked cascade | J05-O3 material-blocked; O4, O5 follow it in sequence. | O4/O5 blocked `PREDECESSOR_BLOCKED`, absent from schedule. |
| Missing setup row | Family transition with no matrix row. | Zero gap, no error. |
| Rollback floor | Only one non-rolled-back version exists. | CLI errors, DB unchanged. |
| Empty pending | All ops frozen/completed. | `OPTIMAL`, makespan = max frozen end. |
| Restore re-inclusion | `machine restore MC-04`. | Next payload includes MC-04. |

### Tier 3: Recovery Correctness

- Solve baseline for `factory_demo_01`.
- Remove one machine, freeze completed/in-progress ops.
- Solve recovery.
- Assert: frozen operations unchanged.
- Assert: no operation on failed machine.
- Assert: `parent_version_id` links to baseline.
- Assert: recovery makespan ≥ baseline makespan.
- Multi-failure: strip two machines; assert both absent, frozen operations intact, valid schedule committed with `failed_machine_ids` recording both.
- Clipping: baseline containing a planned maintenance window overlapping an in-progress operation; recovery asserts the window was clipped and a warning recorded.
- Injection audit *(Amendment 2026-08-23, second)*: each `--failed-machine` appears in `telemetry_events` exactly once (content-derived idempotency); re-running the identical command creates no duplicate event or window.
- Status mirroring *(Amendment 2026-08-23, second)*: after recovery commit, parent-frozen entries ending at/before the clock are `COMPLETED`, clock-spanning entries `IN_PROGRESS`, re-planned work `SCHEDULED`.

### Tier 4: Solver Properties

- **Infeasibility:** Payload with impossible constraints → `INFEASIBLE`, no version committed.
- **Time limit:** Solve with `time_limit_seconds=1` → returns within ~1 second with `FEASIBLE`.
- **Determinism:** Same payload, same seed → identical output.
- **Rollback:** Commit version, rollback CLI, assert `active_schedule` points to previous version.

### Test Infrastructure

- Fixture JSON payloads in `tests/fixtures/` for pure-function solver tests (no DB required).
- MK01 validation as a pytest marker: `@pytest.mark.benchmark`.
- DB-dependent tests use Docker Compose TimescaleDB from Phase 1.

## 12. Acceptance Criteria

Phase 2 is complete when:

1. `payload_builder` produces a valid JSON payload from `factory_demo_01` database state.
2. Pure MK01 solve achieves optimal makespan = 40 with status `OPTIMAL`.
3. `factory_demo_01` baseline solve returns `FEASIBLE` or `OPTIMAL` within the configured time limit.
4. All Tier 2b individual constraint tests pass (worker, downtime, material, setup, frozen).
5. Recovery solve with one or more failed machines produces a valid schedule with no operations on any failed machine and all frozen operations unchanged.
6. `schedule_versions` and `schedule_entries` are populated correctly for baseline and recovery.
7. `active_schedule` view returns the latest committed version.
8. `coe.cli schedule rollback` reverts to the previous version.
9. Determinism: same payload and solver seed produces identical output **in single-worker mode** (`num_search_workers=1` — mandatory for this criterion; multi-worker runs are speed-optimised and non-reproducible by design, §9).
10. Infeasibility: impossible payload returns `INFEASIBLE` with no version committed.
11. Solver respects `time_limit_seconds` and returns best-found solution with appropriate status.
12. Horizon accounts for frozen ends and release times; payloads with future-dated jobs or long frozen intervals still solve.
13. Rollback floor enforced: refusing to roll back the last active version leaves the database unchanged.
14. `machine restore` closes the open downtime window, flips status to `ACTIVE`, and the machine re-appears in the next built payload.
15. Conflict clipping emits `warnings` entries whenever windows are clipped or dropped.
16. Empty-pending payloads commit as trivially `OPTIMAL` versions with makespan equal to the latest frozen end.
17. Worker-dependent durations *(Amendment 2026-08-23)*: every committed entry whose alternative carried a non-empty `workers` map has `processing_time` equal to the assigned worker's duration; entries from empty-map alternatives equal the machine-level duration.
18. Status mirroring *(Amendment 2026-08-23, second)*: baseline commits mark planned entries `SCHEDULED`; recovery commits classify `COMPLETED` / `IN_PROGRESS` against the reference clock; blocked operations become `BLOCKED`; rollback leaves mirrors intact.
19. Derived tardiness weights *(Amendment 2026-08-23, second)*: payload weights from priority are mean-preserving around `beta`; jobs with null deadlines contribute zero tardiness and MK01 still solves to optimal makespan 40.
20. Injection audit *(Amendment 2026-08-23, second)*: CLI recovery failures land in `telemetry_events` exactly once each via the shared ingestion path; identical re-invocations create no duplicate events or windows.
21. Material capacity *(Amendment 2026-08-24)*: committed schedules never drive any SKU's inventory negative; receipt-timed conflicts resolve by delaying the affected operation past the arrival.
22. Permanent over-demand *(Amendment 2026-08-24)*: returns `INFEASIBLE` with no committed version; `MATERIAL_SHORTFALL` warnings persist as advisory metadata with structured totals for Phase 3.

## 13. Phase Boundary

Phase 2 produces a validated, deterministic solver pipeline that accepts a JSON payload and commits an optimized schedule. It does not:

- Invoke LLMs or agents.
- Listen for MQTT events.
- Generate disruption payloads automatically (recovery is triggered manually via CLI).
- Implement narrative translation or schedule explainability.
- Compare against QAOA results.

The solver is a pure function ready to be called by Phase 3's agentic middleware.
