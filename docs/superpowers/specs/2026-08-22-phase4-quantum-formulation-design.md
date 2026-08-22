# Phase 4: Quantum Formulation

**Status:** Draft
**Date:** 2026-08-22
**Phase:** Quantum Formulation

## 1. Purpose

Phase 4 implements the research-pipeline quantum benchmark: a manually encoded time-indexed QUBO of a strict 3-job / 2-machine FJSP micro-instance, solved by QAOA on local statevector simulators. It is the academic hook of the project — a comparative latency and feasibility study against the classical engine — not a production scheduler.

The formulation choices follow published practice: time-indexed one-hot encoding with head/tail pruning (Venturelli et al. 2015), the core JSSP constraint triad as penalty terms, a weighted-tardiness surrogate objective (§5.3), and a horizon seeded from the Phase 2 CP-SAT engine (Carugno et al. 2022).

## 2. Scope

### Included

- A deterministic extractor producing the frozen micro-instance from `factory_demo_01`.
- A pure `quantum` module: micro-instance JSON in, benchmark results JSON out; no database access, no agents.
- Hand-derived penalty terms for the core JSSP triad: one-start-per-operation, intra-job precedence, machine no-overlap.
- Weighted-tardiness linear objective over last operations.
- Head/tail variable pruning with an asserted ceiling of 32 binaries after pruning.
- A systematic `(p_sum, p_pair)` penalty-weight sweep with automatic selection and provenance recording.
- QAOA execution over depth sweep `p ∈ {1, 2, 3}` on Aer statevector simulation, seed-pinned.
- Symbolic decoding and independent feasibility validation of every sampled solution.
- Optimality-gap computation against CP-SAT ground truth on the identical instance.
- A reproducible `quantum_benchmark.json` report consumed by Phase 5.

### Excluded

- LangGraph integration (Phase 5 wires the module into orchestration).
- Real quantum hardware execution.
- Variational alternatives beyond standard QAOA (VarQITE, Iterative-QAOA).
- Setups, workers, materials, downtime, or release-time constraints in the polynomial — the micro-study isolates the core triad.
- Any production scheduling role: Phase 2 remains the sole committing authority.

## 3. Architecture

```text
factory_demo_01 (DB)
    → extractor        (deterministic 3-job/2-machine slice → fixture JSON)
        → qubo_builder (JSON → sparse QUBO matrix, pruned variables)
            → qaoa_runner (Qiskit/Aer, seed-pinned, depth sweep)
                → decoder + feasibility validator (bitstrings → schedule → triad re-check)
                    → benchmark report writer (quantum_benchmark.json)

Phase 2 CP-SAT engine (same fixture) → ground truth makespan/objective + horizon seed T
```

Strict one-way data flow; each stage is independently testable. The quantum pipeline never writes to production tables.

## 4. Micro-Instance Sourcing

- Extractor selects the first 3 jobs (by job-name sort) whose full operation chains fit 2 machines with minimum durations ≤ 3 minutes each; jobs violating the cap are skipped in sorted order until 3 fit.
- Machine eligibility and per-machine processing times come verbatim from `operation_machine_alternatives`.
- Deadlines for the tardiness objective are computed by the TWK method on the slice (same formula family as Phase 1's `add_job_attributes`), weights fixed at `w = 1.0` unless overridden in the fixture.
- Output is written to `tests/fixtures/quantum/micro_instance.json` and hashed; all later stages reference the hash so any instance drift fails loudly.
- Re-running the extractor with the same DB state produces byte-identical output.

## 5. QUBO Formulation

### 5.1 Variables

Time-indexed one-hot binaries:

```
x[o, m, t] = 1  iff operation o starts on machine m at slot t
```

Slots per operation are pruned to `[head(o), T − tail(o)]`, where `head(o)` is the earliest feasible start given preceding operations' minimum durations and `tail(o)` the minimum duration of succeeding operations. `T` is seeded from the Phase 2 CP-SAT makespan on the identical fixture.

The builder asserts the post-pruning variable count is ≤ 32; extraction or formulation changes that break this ceiling fail the build rather than silently degrading simulation practicality.

### 5.2 Penalty Terms (Core Triad)

1. **One-start:** for each operation, `p_sum · (Σ_{m,t} x[o,m,t] − 1)²`
2. **Precedence:** for each consecutive pair within a job and every pair of slots violating order, additive quadratic terms scaled by `p_pair`
3. **No-overlap:** for each machine, pairwise penalties for two operations overlapping in time, scaled by `p_pair`

Every term is hand-derived in the module source with its algebraic expansion (`x² = x` reduction applied) and covered by symbolic unit tests against hand-computed schedules.

### 5.3 Objective

Weighted-tardiness surrogate over each job's final operation, following the published just-in-time encoding (arXiv 2601.04402, eq. 4):

```
C_obj(x) = Σ_j w'_j · t · x[j_last, m, t] − offset
```

The linear form penalizes late completion monotonically per job and equals zero cost for the earliest feasible completions; literal `max(0, t−d)` tardiness would require auxiliary variables, so the surrogate is the accepted trade-off and is reported as such. Slots are bounded only by head/tail pruning (§5.1); deadlines `d_j` enter through the per-job normalization `w'_j = w_j / (t_max(j) − t_min(j))` and the constant offset over each job's feasible completion window, following equations (A6) of the cited paper. No auxiliary variables are introduced.

## 6. Penalty Calibration Sweep

Penalty weights are never guessed constants:

- Sweep grid: multipliers ×{1, 2, 4, 8} of the maximum absolute objective coefficient, crossed for `(p_sum, p_pair)`.
- Exact method boundary: for fixtures with ≤ 24 binaries the matrix minimum is found by exhaustive enumeration; above that, by deterministic depth-first branch-and-bound over the QUBO (2³² enumeration is infeasible). The method used is recorded in the calibration section.
- For each cell: solve exactly (classical exhaustive/QAOA-free minimization on the small matrix), record ground-state feasibility rate and best feasible objective.
- Selection rule: smallest multiplier pair achieving zero constraint violation on the optimum AND strictly lower energy for the best feasible schedule than for any infeasible one (the separation criterion of arXiv 2601.04402).
- Selected values are recorded in the benchmark report alongside the grid, becoming the defaults for all QAOA runs.

## 7. QAOA Runtime

- Mapping: QUBO → Ising Hamiltonian via Qiskit's standard transformation.
- Algorithm: `qiskit_algorithms.QAOA`; classical optimizer COBYLA capped at `QUANTUM_OPTIMIZER_MAX_ITER` iterations; Aer `statevector_simulator`, shots = 1024, fixed random seed.
- Termination honesty: each depth row records the optimizer outcome as `CONVERGED`, `ITERATION_LIMIT`, or `TIME_LIMIT`. A run that hits either limit keeps its best-so-far parameters and proceeds to sampling rather than erroring out; the status travels into the benchmark report so Phase 5 can interpret solution quality honestly.
- Depth sweep `p ∈ {1, 2, 3}` (configurable upper bound); each depth runs end-to-end with wall-clock decomposition recorded separately: QUBO build, circuit optimization, sampling.
- Determinism contract: identical fixture + seed + parameters produce byte-identical results JSON.

## 8. Decoding and Feasibility Validation

Every sampled bitstring is decoded to a concrete schedule (start times, machine assignments). An independent symbolic validator re-checks the core triad directly on the decoded schedule — deliberately not reusing QUBO internals — so an encoding bug manifests as validator disagreement instead of silent corruption.

Per-run metrics: feasible-sample rate, best feasible objective, optimality gap versus CP-SAT ground truth on the same fixture, and the timing decomposition above. The optimality gap is computed on decoded schedules' **makespan and true tardiness** — re-evaluated with the classical formulas (makespan; `max(0, end − deadline)` summed per job) — never on the QUBO surrogate cost, because the surrogate and the classical objective measure different quantities. The surrogate cost is reported alongside for research transparency.

## 9. Benchmark Deliverable

`quantum_benchmark.json`, written once per harness invocation:

- Instance identity: source instance name, fixture path, fixture SHA-256, variable count, horizon T.
- Calibration section: sweep grid, selected `p_sum`/`p_pair`, separation evidence.
- Per-depth rows: `{p, qubits, optimizer_status, feasible_rate, best_objective, decoded_makespan, decoded_tardiness, cp_sat_gap, build_seconds, optimize_seconds, sample_seconds}`.
- Environment: package versions (qiskit, aer, ortools), seed, host timestamp.

Phase 5 consumes this file for the comparative latency and feasibility analysis; its schema is therefore stable across runs.

## 10. Configuration

Extends the pydantic-settings stack:

- `QUANTUM_MAX_P` (default 3)
- `QUANTUM_SHOTS` (default 1024)
- `QUANTUM_SEED` (default 42)
- `QUANTUM_TIME_LIMIT_SECONDS` (default 300 per depth)
- `QUANTUM_OPTIMIZER_MAX_ITER` (default 1000)
- `QUANTUM_VAR_CEILING` (default 32)

## 11. Command Interface

```bash
uv run python -m coe.cli quantum extract --instance factory_demo_01
uv run python -m coe.cli quantum calibrate --fixture tests/fixtures/quantum/micro_instance.json
uv run python -m coe.cli quantum solve --fixture tests/fixtures/quantum/micro_instance.json --p 2
uv run python -m coe.cli quantum benchmark --fixture tests/fixtures/quantum/micro_instance.json
```

`extract` regenerates the fixture deterministically. `calibrate` runs the penalty sweep only. `solve` executes a single-depth QAOA run. `benchmark` orchestrates calibrate + full depth sweep + report writing.

## 12. Validation and Testing Strategy

### Tier 1: Symbolic Correctness

- Each penalty term evaluated by hand on crafted schedules: legal schedule ⇒ zero; specific violations ⇒ exact expected cost.
- Objective term checked against known tardiness values.

### Tier 2: Brute-Force Cross-Validation

- On a hand-crafted brute-force fixture (`tests/fixtures/quantum/brute_force.json`, 2 jobs / 2 machines, short horizon, ≤ 20 binaries — deliberately not extractor output, which always emits the 3×2 production slice): exhaustive enumeration minimum must equal the QUBO matrix minimum, and the decoded optimum must pass the symbolic validator. The 3×2 extractor fixture is exercised by Tiers 3 and 4 only.

### Tier 3: Pipeline Properties

- Extractor determinism: byte-identical fixtures across repeated runs; variable-ceiling assertion fires on adversarial input.
- QAOA determinism: same seed ⇒ identical results JSON.
- Decoder/validator independence: mutated bitstrings (flipped bits) are caught as infeasible at the expected rate.

### Tier 4: Benchmark Smoke

- Full `quantum benchmark` completes within the configured time limit and emits a schema-valid `quantum_benchmark.json`.

## 13. Acceptance Criteria

Phase 4 is complete when:

1. `quantum extract` produces a deterministic ≤ 32-binary micro-instance fixture from `factory_demo_01`.
2. All three penalty terms and the objective pass symbolic tests against hand-computed cases.
3. Brute-force enumeration agrees with the QUBO minimum on the hand-crafted brute-force fixture.
4. The calibration sweep automatically selects `(p_sum, p_pair)` meeting the separation criterion and records the grid.
5. QAOA at some swept depth decodes a fully feasible schedule (all triad validators zero).
6. The report contains the optimality gap versus CP-SAT ground truth, computed on re-evaluated makespan and true tardiness values per Section 8.
7. Identical seeds reproduce byte-identical result JSONs end to end.
8. `quantum_benchmark.json` is emitted with the stable schema Section 9 defines.

## 14. Phase Boundary

Phase 4 produces a validated, reproducible quantum benchmark artifact for the micro-instance. It does not:

- Integrate with LangGraph or respond to disruptions (Phase 5 wires it).
- Commit or influence any production schedule (Phase 2 remains sole authority).
- Run on physical quantum hardware.
- Claim speedup: the deliverable measures and reports comparative behavior honestly.
