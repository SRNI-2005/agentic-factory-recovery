# Phase 2: Classical Optimization Engine

**Status:** Approved
**Date:** 2026-08-21
**Phase:** Classical Optimization Engine

## 1. Purpose

Phase 2 implements the deterministic CP-SAT solver pipeline that computes both baseline and recovery schedules. It consumes the data model and scenario produced by Phase 1 and outputs committed, versioned schedules to the database.

The solver is a pure function: it accepts a self-contained JSON payload and returns an optimized schedule. It has no database access, no LLM dependency, and no side effects. All database interaction is handled by the payload builder (input) and committer (output).

This phase does not implement agents, LLM calls, or MQTT-triggered recovery. Recovery is triggered manually via CLI with explicit failed-machine arguments.

## 2. Scope

### Included

- A `payload_builder` module that reads Phase 1 schema and produces solver-ready JSON.
- A `solver.engine` module implementing Google OR-Tools CP-SAT with the full constraint model.
- A `solver.committer` module that writes solved schedules to versioned database tables.
- Full constraint model: precedence, release times, no-overlap, machine eligibility, asymmetric processing times, sequence-dependent setup times, worker assignment and availability, worker absence windows *(Amendment 2026-08-23)*, machine downtime, deadline tardiness, and material gatekeeping.
- Weighted multi-objective optimization (makespan + tardiness) with configurable weights.
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
- Reads `job_families` and `setup_times` per machine.
- Converts worker availability windows (positive) into unavailability intervals (negative).
- Reads `worker_absence_windows` directly — already negative semantics, no conversion needed *(Amendment 2026-08-23)*.
- Reads `machine_downtime_windows` directly (already negative semantics).
- Performs pre-solve material gatekeeping: computes material availability from `materials.initial_stock` plus `material_receipts` minus `operation_bom` requirements. Operations with provably unavailable materials are excluded from the payload and listed in `blocked_operations` with a reason.
- For recovery payloads: reads the current active schedule.
  - **Completed operations:** Marked as frozen with their historic fixed assignments.
  - **In-progress on healthy machines:** Marked as frozen to prevent disrupting ongoing work.
  - **In-progress on the failed machine (INTERRUPTED):** MUST NOT be frozen (which would crash the solver by conflicting with the downtime window). Instead, it is dynamically truncated, assigned a remaining processing time, set to `PENDING`, and given alternatives. The originally assigned worker is explicitly UN-FROZEN for the remaining duration so they are not stranded.
  - **Failed Machine Alternatives:** For each failed machine: if its downtime `until` is `null` (permanent), it is stripped from all operation alternative lists. If the downtime is temporary, it remains in the alternative lists so the solver has the option to simply "wait it out" if setup times on other machines are too penalizing.
  - **Zero Alternatives (Dead End):** If stripping a permanently failed machine leaves any operation with an empty alternatives list, the `payload_builder` must immediately mark it as `status = BLOCKED` with reason `NO_CAPABLE_MACHINES` and exclude it from the solver payload.
- **Blocked-Op Cascade:** Blocking an operation automatically blocks all later-sequence operations in the same job with reason `PREDECESSOR_BLOCKED`, so no successor ever loses its precedence anchor.
- **Initial Family Seeding:** Derives `machine_initial_families`: for each machine with entries in the active schedule, the family of the last operation committed on it. Machines absent from the map are treated as clean (`from_family: null`).
- **Conflict Clipping:** After freeze/truncate decisions, clips any remaining downtime window overlapping a frozen interval to start at the frozen op's end (drops it if fully covered). Applies the same rule to worker-unavailability windows against frozen ops assigned to that worker. Every clip or drop appends an entry to the payload `warnings` array.
- **Setup Matrix Hygiene:** Warns when a setup pair is defined in one direction only (asymmetric row missing).
- Outputs a deterministic JSON payload. The same database state and parameters produce the same payload.

### 3.2 `coe.solver.engine`

A pure function with no database access. Accepts a JSON payload, builds a CP-SAT model, solves it, and returns a solution JSON.

If the payload contains zero PENDING operations, the engine short-circuits without building a CP-SAT model: status `OPTIMAL`, makespan equal to the latest frozen end (0 if none), and total tardiness evaluated from frozen completion times against deadlines.

Inputs: the solver payload JSON (defined in Section 5).

Outputs:
- Assigned machine, worker, start time, end time, and setup time for each scheduled operation.
- Objective value (weighted sum), makespan, and total tardiness.
- Solver status: `OPTIMAL`, `FEASIBLE`, or `INFEASIBLE`.
- Solve duration in seconds.

### 3.3 `coe.solver.committer`

Takes the solution JSON and writes it to the database:
- Creates a new `schedule_versions` row.
- Writes `schedule_entries` for each assigned operation.
- Links to the parent version for recovery schedules.
- Wrapped in a database transaction: any failure rolls back the entire commit.
- Only commits solutions with status `OPTIMAL` or `FEASIBLE`. `INFEASIBLE` results are logged to standard application logs but not committed.

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
          "alternatives": [
            { "machine_id": "MC-03", "processing_time": 12, "eligible_workers": ["W-01", "W-03"] },
            { "machine_id": "MC-05", "processing_time": 18, "eligible_workers": ["W-02"] }
          ],
          "frozen": null
        }
      ]
    }
  ],

  "machine_downtime": [
    { "machine_id": "MC-05", "from": 200, "until": 350, "reason": "MAINTENANCE" }
  ],

  "worker_unavailability": [
    { "worker_id": "W-01", "from": 480, "until": 960 }
  ],

  "setup_times": [
    { "machine_id": "MC-03", "from_family": "FAM-A", "to_family": "FAM-B", "duration": 8 },
    { "machine_id": "MC-03", "from_family": null, "to_family": "FAM-A", "duration": 5 }
  ],

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
- `job_tardiness_weights` (optional map of job_id → weight ≥ 0) overrides the global `beta` per job; introduced for Phase 3 strategy application. An absent map means uniform `beta` for every job.
- `warnings` records payload_builder interventions (clipped or dropped windows) for downstream explainability.
- Completed operations and operations in-progress on healthy machines have empty `alternatives` and a `frozen` block with fixed assignments.
- Operations interrupted by a machine failure are NOT frozen; they are dynamically truncated to their remaining processing time, set to `PENDING`, and given alternatives excluding the failed machine.
- Worker unavailability is pre-converted from positive availability windows by the payload builder.
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

Each pending operation gets one `NewOptionalIntervalVar` per eligible machine-worker combination. `AddExactlyOne` enforces that exactly one alternative is selected.

### 6.3 No-Overlap (Machine Capacity)

One `AddNoOverlap` per machine. The interval list includes scheduled operation intervals, frozen operation intervals, downtime intervals, and setup intervals.

### 6.4 Machine Downtime

Downtime windows are fixed intervals added to the machine's no-overlap constraint. In practice, permanently failed machines never reach the solver (stripped from `machines`, their downtime entries omitted), so every window the engine sees references a listed machine and has a finite `until`; if a finite-bound window is absent, the horizon is used defensively as its end time.

### 6.5 Worker Assignment

Each worker is modeled as a no-overlap resource. For each operation-machine alternative, the eligible workers are specified. The selected worker's interval is added to that worker's no-overlap constraint. Worker unavailability windows are fixed intervals on the worker's no-overlap constraint.

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

`Horizon = max(Σ max processing(pending ops) + Σ max setups + Σ temporary downtime durations, max(frozen end), max(release_time))`

Permanent downtimes (`until: null`) are excluded because their machine is stripped and consumes no scheduling capacity. The frozen-end and release-time terms guarantee that fixed historic intervals and future-dated jobs always fit inside the horizon.

### 6.8 Deadlines and Tardiness

```python
tardiness[j] = model.NewIntVar(0, horizon, f'tardiness_{j}')
model.AddMaxEquality(tardiness[j], [0, last_op[j].end - deadline[j]])
```

### 6.9 Objective Function

```python
model.Minimize(alpha * normMakespan + beta * normTardiness)
```

When `normalize_objectives` is enabled, both makespan and total tardiness are divided by the horizon to yield a `[0.0, 1.0]` ratio before weighting (`normMakespan = makespan / horizon`), so that `alpha` and `beta` are directly comparable across different instances. When `job_tardiness_weights` is present, tardiness is weighted per job (`Σ_j w_j * normTardiness_j`) with absent entries using the global `beta`.

### 6.10 Frozen Operations (Recovery)

Frozen operations are added as fixed-interval constants (not decision variables) to the relevant machine and worker no-overlap constraints. They occupy time on their assigned resources but are not subject to optimization.

## 7. Material Gatekeeping

Materials are handled as a pre-solve absolute-supply check, not as solver constraints or priority-based allocation.

The `payload_builder` cannot pre-allocate materials to specific operations because the allocation order depends on the schedule, which is exactly what the solver is computing. Instead, the check is conservative:

1. For each material, compute `total_supply = materials.initial_stock + sum(material_receipts.quantity)` across all receipts arriving before the solver horizon.
2. For each material, compute `total_demand = sum(operation_bom.quantity_required)` across all pending operations in the instance.
3. If `total_supply >= total_demand`, all operations pass the material check. The solver determines the actual schedule order; material timing is implicitly feasible because enough material exists across the horizon.
4. If `total_supply < total_demand`, the shortfall is reported. Operations are only blocked if their specific material has zero total supply (no stock and no receipts). Partial shortfalls are reported to Phase 3 agents as warnings, not as hard blocks, because the solver's schedule may naturally resolve the conflict by ordering jobs such that receipts arrive before consumption.

This is intentionally loose. True temporal inventory routing (ensuring material is available at the exact moment an operation is scheduled) would require cumulative resource constraints in CP-SAT, which significantly increases solve complexity at 30-job scale. The conservative total-supply check avoids artificial bottlenecks while catching genuinely impossible material shortages.

Within a single job, material flow between operations is handled implicitly by precedence constraints. Cross-job material dependencies are not modeled; jobs are independent customer orders.

Blocked operations are excluded from the solver payload with a reason. The reason is available for Phase 3 agents to propose recovery actions (e.g., expedite a material receipt, substitute materials, defer the job).

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
- `SOLVER_NUM_SEARCH_WORKERS` (default: 1. Must be exactly 1 to guarantee deterministic solutions.)

These are defaults. The `payload_builder` can override them per-run via CLI arguments or, in Phase 3, via agent-provided parameters.
**Constraint:** The payload builder must enforce `alpha >= 0`, `beta >= 0`, and `(alpha + beta) > 0`. Permitting negative weights would cause the solver to maximize time/tardiness. A zero-weight objective sum (`Minimize(0)`) causes the solver to legally return the very first feasible schedule it finds, which can produce highly irrational long-running schedules.

## 10. Command Interface

```bash
uv run python -m coe.cli solve baseline --instance factory_demo_01
uv run python -m coe.cli solve baseline --instance factory_demo_01 --alpha 2.0 --beta 0.5 --time-limit 120
uv run python -m coe.cli solve recovery --instance factory_demo_01 --failed-machine MC-04 MC-07
uv run python -m coe.cli machine restore --instance factory_demo_01 --machine MC-04 [--at MINUTE]
uv run python -m coe.cli schedule show --instance factory_demo_01
uv run python -m coe.cli schedule rollback --instance factory_demo_01
```

`solve baseline` creates the initial schedule. `solve recovery` explicitly injects a permanent `machine_downtime_windows` row (`until: null`) for each specified failed machine into the database, sets each machine's status to `FAILED`, then reads the active schedule, freezes completed and in-progress operations, removes every failed machine, and re-solves. `machine restore` closes the open-ended downtime window (`downtime_until = at`, defaulting to the instance's latest telemetry `occurred_at`; an explicit `--at` is required when no telemetry exists) and sets the machine status back to `ACTIVE`; it does not trigger a solve — the machine simply re-enters future payloads. `schedule show` prints the active schedule. `schedule rollback` reverts to the previous version and refuses when the active version is the last remaining one.

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
- Every precedence respected.
- No machine overlaps (including setup intervals and downtime).
- No worker overlaps (including unavailability windows).
- No blocked operations in the schedule.
- All setup times correct for family transitions.
- Tardiness computed correctly against deadlines.

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
9. Determinism: same payload and solver seed (with `num_search_workers=1`) produces identical output.
10. Infeasibility: impossible payload returns `INFEASIBLE` with no version committed.
11. Solver respects `time_limit_seconds` and returns best-found solution with appropriate status.
12. Horizon accounts for frozen ends and release times; payloads with future-dated jobs or long frozen intervals still solve.
13. Rollback floor enforced: refusing to roll back the last active version leaves the database unchanged.
14. `machine restore` closes the open downtime window, flips status to `ACTIVE`, and the machine re-appears in the next built payload.
15. Conflict clipping emits `warnings` entries whenever windows are clipped or dropped.
16. Empty-pending payloads commit as trivially `OPTIMAL` versions with makespan equal to the latest frozen end.

## 13. Phase Boundary

Phase 2 produces a validated, deterministic solver pipeline that accepts a JSON payload and commits an optimized schedule. It does not:

- Invoke LLMs or agents.
- Listen for MQTT events.
- Generate disruption payloads automatically (recovery is triggered manually via CLI).
- Implement narrative translation or schedule explainability.
- Compare against QAOA results.

The solver is a pure function ready to be called by Phase 3's agentic middleware.
