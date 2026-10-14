# Phase 4: Quantum Formulation

**Status:** Draft
**Date:** 2026-08-22
**Phase:** Quantum Formulation

## 1. Purpose

Phase 4 implements the research-pipeline quantum benchmark: a manually encoded time-indexed QUBO of a strict 3-job / 2-machine FJSP micro-instance, solved by QAOA on local statevector simulators. It is the academic hook of the project — a comparative latency and feasibility study against the classical engine — not a production scheduler.

The formulation choices follow published practice: time-indexed one-hot encoding with head/tail pruning (Venturelli et al. 2015), the core JSSP constraint triad as penalty terms, a weighted-tardiness surrogate objective following the Just-in-Time JSSP encoding (Lopez-Ruiz et al. 2025; §5.3), and a horizon seeded from the Phase 2 CP-SAT engine (Carugno et al. 2022).

## 2. Scope

### Included

- A deterministic extractor producing the frozen micro-instance from `factory_demo_01`.
- A pure `quantum` module: micro-instance JSON in, benchmark results JSON out; no database access, no agents.
- Hand-derived penalty terms for the core JSSP triad: one-start-per-operation, intra-job precedence, machine no-overlap.
- Weighted-tardiness linear objective over last operations.
- Head/tail variable pruning with an asserted ceiling of `QUANTUM_VAR_CEILING` binaries after pruning (default 24; see §5.1).
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

> **[Amendment 2026-09-11]:** the Phase 2 engine is already a pure JSON-in/JSON-out function with no DB access (P2 §1, §3.2); only its payload_builder is DB-bound. The required deliverable is therefore a minimal **fixture adapter** — `micro_instance.json → valid Phase 2 solver payload JSON` (§5 schema of the Phase 2 spec) — after which the existing engine is invoked directly, no CP-SAT code re-written. The adapter requires status **OPTIMAL** on the tiny fixture (anything else — FEASIBLE/INFEASIBLE/UNKNOWN per the frozen P2 contract — fails loudly). It runs with `num_search_workers=1` (P2 §9's mandatory setting for reproducibility consumers) and a uniform `job_tardiness_weights` map (absent map ⇒ uniform `beta`, matching the extractor's fixed `w = 1.0`), so the horizon seed `T` is bit-reproducible, which AC 7 depends on.

## 4. Micro-Instance Sourcing

- Extractor selects the first 3 jobs (by job-name sort) whose full operation chains **all fit within a single common 2-machine set** — i.e., there exists one pair {M_a, M_b} such that every operation's machine alternatives across all 3 chains are subsets of that pair — with minimum durations ≤ 3 minutes each; jobs violating the cap are skipped in sorted order until 3 fit.

> **[Amendment 2026-09-11]:** "fit 2 machines" was ambiguous; the single-common-machine-set rule above is now definitive. If no such triple exists in the source instance, `extract` fails loudly (AC 1 presumes `factory_demo_01` contains one).
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

Slots per operation are pruned to `[head(o), T − tail(o)]`, where `head(o)` is the earliest feasible start given preceding operations' minimum durations and `tail(o)` the minimum total duration of operation `o` plus all its successors (so `T − tail(o)` is the latest feasible start for `o`). `T` is seeded from the Phase 2 CP-SAT makespan on the identical fixture.

> **[Amendment 2026-09-11]:** `tail(o)` explicitly includes `o`'s own minimum duration; the original text ("minimum duration of succeeding operations") omitted `o` and made the bound loose by one operation.

The builder asserts the post-pruning variable count is ≤ `QUANTUM_VAR_CEILING`; extraction or formulation changes that break this ceiling fail the build rather than silently degrading simulation practicality.

> **[Amendment 2026-09-11]:** the ceiling constant is lowered from 32 to **24**. At exactly 32 simulated qubits an Aer statevector needs ~68 GB RAM (2³² × 16 B), which pushes the guard above what `statevector_simulator` can practically run; 24 keeps the guard comfortably inside simulator limits. The micro-instance's true post-pruning count is expected well below 24 — the ceiling is a fail-loud guard, not a target.

### 5.2 Penalty Terms (Core Triad)

1. **One-start:** for each operation, `p_sum · (Σ_{m,t} x[o,m,t] − 1)²`
2. **Precedence:** for each consecutive pair within a job and every pair of slots violating order, additive quadratic terms scaled by `p_pair`
3. **No-overlap:** for each machine, pairwise penalties for two operations overlapping in time, scaled by `p_pair`

Every term is hand-derived in the module source with its algebraic expansion (`x² = x` reduction applied) and covered by symbolic unit tests against hand-computed schedules.

### 5.3 Objective

Weighted-tardiness surrogate over each job's final operation, following the published Just-in-Time JSSP encoding of Lopez-Ruiz et al. 2025 (arXiv:2510.26859; see the attribution amendment below):

```
C_obj(x) = Σ_j w'_j · t · x[j_last, m, t] − offset
```

The linear form penalizes late completion monotonically per job and equals zero cost for the earliest feasible completions; literal `max(0, t−d)` tardiness would require auxiliary variables, so the surrogate is the accepted trade-off and is reported as such. Slots are bounded only by head/tail pruning (§5.1); deadlines `d_j` enter only through the per-job normalization `w'_j = w_j / (t_max(j) − t_min(j))` over each job's feasible completion window and the constant offset — because the offset is constant, `d_j` does **not** affect the argmin of the QUBO; deadlines matter for the validator and the reported gap (§8) only. No auxiliary variables are introduced.

> **[Amendment 2026-09-11 — corrected by literature double-check]:** attribution fixed against primary sources. The **Just-in-Time JSSP encoding (tardiness-style per-job objective over completion slots)** is from **Lopez-Ruiz et al. 2025** ("A Non-Variational Quantum Approach to the Job Shop Scheduling Problem", arXiv:2510.26859 — benchmarks JIT-JSSP on IonQ hardware; listed as review reference #11). The **separation criterion** and the `(p_sum, p_pair)` encoding family are from **arXiv 2601.04402** (Doucet et al., "Thermodynamic significance of QUBO encoding on quantum annealers", New J. Phys. 28 054512, 2026 — cited in §6). **Carugno et al. 2022** ("Evaluating the job shop scheduling problem on a D-Wave quantum annealer", Sci. Reports 12:6539, DOI 10.1038/s41598-022-10169-0 — title previously misstated in the first amendment) is retained strictly for its actual contribution: the **horizon-selection trap and seeding T from OR-Tools** (§5.1; review reference #3). The original text credited the tardiness objective to 2601.04402. During implementation, the exact normalization/offset equation numbering must be re-verified against Lopez-Ruiz 2025 and the trace recorded in the module docstring.

## 6. Penalty Calibration Sweep

Penalty weights are never guessed constants:

- Sweep grid: multipliers ×{1, 2, 4, 8} of the maximum absolute objective coefficient, crossed for `(p_sum, p_pair)`.
- Exact method boundary: for fixtures with ≤ 24 binaries the matrix minimum is found by exhaustive enumeration; above that, by deterministic depth-first branch-and-bound over the QUBO (2³² enumeration is infeasible). The method used is recorded in the calibration section. `QUANTUM_VAR_CEILING` (24) keeps every conforming fixture within exhaustive reach, but the B&B path remains reachable via configuration and is exercised in tests.
- For each cell: solve exactly (classical exhaustive/QAOA-free minimization on the small matrix), record ground-state feasibility rate and best feasible objective.
- Selection rule: smallest multiplier pair achieving zero constraint violation on the optimum AND strictly lower energy for the best feasible schedule than for any infeasible one (the separation criterion of arXiv 2601.04402).
- No qualifying cell ⇒ calibration fails loudly; the sweep is never silently relaxed. The only permitted response is widening the grid (documented multiplier extensions) in a code-change commit, never an in-run relaxation.

> **[Amendment 2026-09-11]:** fallback behavior added — previously unspecified.

- Selected values are recorded in the benchmark report alongside the grid, becoming the defaults for all QAOA runs.

## 7. QAOA Runtime

- Mapping: QUBO → Ising Hamiltonian via Qiskit's standard transformation.
- Algorithm: `qiskit_algorithms.QAOA` with COBYLA capped at `QUANTUM_OPTIMIZER_MAX_ITER` iterations; Aer `statevector_simulator`, shots = 1024, fixed random seed.

> **[Amendment 2026-09-11]:** dependency-risk note (normative for implementation): `qiskit-algorithms` is community-maintained upstream and deprecated on the main Qiskit line. pyproject must pin exact versions of `qiskit`, `qiskit-aer`, and `qiskit-algorithms` together (an incompatible header of e.g. Qiskit 2.x breaks imports). If `qiskit_algorithms.QAOA` cannot be installed against the pinned Qiskit, the sanctioned fallback is a hand-rolled QAOA ansatz (cost-unitary from the Ising mapping + mixer) optimized by `scipy.optimize.COBYLA` — it reproduces the same circuit semantics with zero extra dependencies. The determinism contract below applies identically to either path.

- Termination honesty: each depth row records the optimizer outcome as `CONVERGED`, `ITERATION_LIMIT`, or `TIME_LIMIT`. A run that hits either limit keeps its best-so-far parameters and proceeds to sampling rather than erroring out; the status travels into the benchmark report so Phase 5 can interpret solution quality honestly.
- Depth sweep `p ∈ {1, 2, 3}` (configurable upper bound); each depth runs end-to-end with wall-clock decomposition recorded separately: QUBO build, circuit optimization, sampling.
- Determinism contract: identical fixture + seed + parameters produce identical results — compared via the canonical results payload and `determinism_hash` defined in §9 (Environment excluded).

## 8. Decoding and Feasibility Validation

Every sampled bitstring is decoded to a concrete schedule (start times, machine assignments). An independent symbolic validator re-checks the core triad directly on the decoded schedule — deliberately not reusing QUBO internals — so an encoding bug manifests as validator disagreement instead of silent corruption.

Per-run metrics: feasible-sample rate, best feasible objective, optimality gap versus CP-SAT ground truth on the same fixture, and the timing decomposition above. The optimality gap is computed on decoded schedules' **makespan and true tardiness** — re-evaluated with the classical formulas (makespan; `max(0, end − deadline)` summed per job) — never on the QUBO surrogate cost, because the surrogate and the classical objective measure different quantities. Gap formulas per metric are defined in §9.

## 9. Benchmark Deliverable

`quantum_benchmark.json`, written once per harness invocation:

- Instance identity: source instance name, fixture path, fixture SHA-256, variable count, horizon T.
- Calibration section: sweep grid, selected `p_sum`/`p_pair`, separation evidence.
- Per-depth rows: `{p, qubits, optimizer_status, feasible_rate, best_objective, decoded_makespan, decoded_tardiness, cp_sat_gap_tardiness, cp_sat_gap_makespan, build_seconds, optimize_seconds, sample_seconds}`.
- Environment: package versions (qiskit, aer, ortools), seed, host timestamp.

> **[Amendment 2026-09-11]:** AC 7's byte-identical determinism is defined over the **results payload** — instance identity, calibration, per-depth rows, and seed — serialized canonically (sorted keys, fixed float formatting). The `Environment` section (host timestamp, package versions) sits outside the deterministic comparison: timestamps can never collide across runs. The report embeds a `determinism_hash` computed over the results payload, and the AC 7 test compares only that hash plus the results payload.

The optimality gap is defined per metric (both computed on decoded schedules, re-evaluated with the classical formulas — never on the QUBO surrogate cost):

- `cp_sat_gap_tardiness = (quantum_weighted_tardiness − cp_sat_weighted_tardiness) / cp_sat_weighted_tardiness` (0 if CP-SAT tardiness is 0 and quantum ties it; ∞-sentinel recorded as `null` when CP-SAT tardiness is 0 and quantum does not).
- `cp_sat_gap_makespan` analogously on makespan.

Makespan is not in the QUBO objective (§5.3); it is reported as a secondary honest metric. The surrogate cost is reported alongside for research transparency.

Phase 5 consumes this file for the comparative latency and feasibility analysis; its schema is therefore stable across runs.

## 10. Configuration

Extends the pydantic-settings stack:

- `QUANTUM_MAX_P` (default 3)
- `QUANTUM_SHOTS` (default 1024)
- `QUANTUM_SEED` (default 42)
- `QUANTUM_TIME_LIMIT_SECONDS` (default 300 per depth)
- `QUANTUM_OPTIMIZER_MAX_ITER` (default 1000)
- `QUANTUM_VAR_CEILING` (default 24)

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
 - QAOA determinism: same seed ⇒ identical results payload / `determinism_hash`.
- Decoder/validator independence: mutated bitstrings (flipped bits) are caught as infeasible at the expected rate.

### Tier 4: Benchmark Smoke

- Full `quantum benchmark` completes within the configured time limit and emits a schema-valid `quantum_benchmark.json`.

## 13. Acceptance Criteria

Phase 4 is complete when:

1. `quantum extract` produces a deterministic ≤ `QUANTUM_VAR_CEILING`-binary (default 24) micro-instance fixture from `factory_demo_01`.
2. All three penalty terms and the objective pass symbolic tests against hand-computed cases.
3. Brute-force enumeration agrees with the QUBO minimum on the hand-crafted brute-force fixture.
4. The calibration sweep automatically selects `(p_sum, p_pair)` meeting the separation criterion and records the grid.
5. QAOA at some swept depth decodes a fully feasible schedule (all triad validators zero).

> **[Amendment 2026-09-11 — acknowledged risk]:** AC 5 depends on stochastic sampling outcomes; with calibrated penalties, 1024 shots × 3 depths on a ≤ 24-binary instance makes it likely but not guaranteed. A run with feasible_rate = 0 at every depth is an honest result (reported per §7), not a bug — if it occurs, AC 5 is addressed by documented calibration/grid widening, never by massaging results.
6. The report contains the optimality gap versus CP-SAT ground truth, computed on re-evaluated makespan and true tardiness values per the per-metric formulas of Section 9.
7. Identical seeds reproduce identical result JSONs end to end, verified via the `determinism_hash` and canonical results payload per Section 9 (Environment section excluded).
8. `quantum_benchmark.json` is emitted with the stable schema Section 9 defines.

## 14. Phase Boundary

Phase 4 produces a validated, reproducible quantum benchmark artifact for the micro-instance. It does not:

- Integrate with LangGraph or respond to disruptions (Phase 5 wires it).
- Commit or influence any production schedule (Phase 2 remains sole authority).
- Run on physical quantum hardware.
- Claim speedup: the deliverable measures and reports comparative behavior honestly.
