# Phase 5: Integration & Benchmarking

**Status:** Approved
**Date:** 2026-08-22
**Phase:** Integration & Benchmarking

## 1. Purpose

Phase 5 completes the system: it wires the Phase 4 quantum pipeline into the recovery workflow as a strictly observational shadow branch, instruments end-to-end latency across the LangGraph graph, assembles the CP-SAT versus QAOA comparison on identical fixtures, and produces the reproducible data bundle for publication.

The architectural principle, confirmed against current hybrid quantum-classical literature, is that the classical engine remains the sole scheduling authority; the quantum path exists exclusively for benchmarking and feasibility study.

## 2. Scope

### Included

- A config-gated `quantum_shadow` graph node executing the Phase 4 pipeline on a micro-slice of each recovery (default OFF).
- Per-node wall-clock instrumentation persisted with every run.
- An end-to-end benchmark harness replaying seeded disruption scenarios against isolated instance clones.
- The solver comparison table joining CP-SAT solve durations with QAOA depth rows from `quantum_benchmark.json`.
- A `publication/` bundle: consolidated CSV tables plus a full reproducibility manifest.
- Final fidelity metric consolidation from Phase 3's benchmark output into the bundle.

### Excluded

- Any change to solver internals, agent prompts, or schema definitions (Phases 1–4 are frozen interfaces).
- Real quantum hardware execution.
- Statistical claims beyond what the recorded runs support (no manufactured significance).
- LaTeX/figure generation; the bundle stops at clean tabular inputs.
- Live deployment hardening (auth, TLS, multi-tenant concerns).

## 3. Architecture

```text
LangGraph recovery flow
    ... → commit → verify → explain → [quantum_shadow]        (optional node)

recovery_runs.node_timings_json          ← per-node wall-clock per run
recovery_runs.quantum_shadow_json        ← shadow study results when enabled

benchmark harness
    scenario replay → stage timings → latency_by_stage.csv
    phase3 report   → fidelity_metrics.csv
    schedule_versions + quantum_benchmark.json → solver_comparison.csv
    everything      → manifest.json
```

One-way dependencies only. The shadow node consumes Phase 4 modules verbatim and writes reference JSON; nothing downstream reads it during scheduling decisions.

## 4. Shadow Quantum Node

- Appended after `explain`; executes only when `QUANTUM_SHADOW_ENABLED=true` (default false).
- Input: the same deterministic 3-job/2-machine micro-instance produced by Phase 4's fixed extractor (first 3 jobs by name-sort fitting 2 machines). The shadow measures quantum feasibility on a canonical micro-slice of `factory_demo_01`, not on the specific disruption's affected jobs — neighborhood-aware extraction is a documented future extension.
- Execution: Phase 4's extractor/QUBO/QAOA/decode chain invoked as pure functions with the fixture seed; results (feasible rate, gap, timings) serialized into `recovery_runs.quantum_shadow_json`.
- Failure isolation: any exception inside the node is caught and recorded as `{status: SHADOW_FAILED, error}`; the run itself remains `COMMITTED` and fully successful.
- Determinism: identical run state plus seed produce byte-identical shadow JSON.

## 5. Latency Instrumentation

- Each graph node boundary appends `{node, started_at, ended_at}` wall-clock pairs to shared state; the committer persists the assembled map as `recovery_runs.node_timings_json`.
- Timers cover: translate, ingest, machine_agent, production_agent, inventory_agent, strategy_loop, manager_compile, solve, gate, commit, verify, explain, quantum_shadow (when enabled).
- Clock discipline: single monotonic clock source per process; no mixing of wall and CPU time.
- Overhead guarantee: instrumentation adds no LLM or solver calls and must measure under 10 ms cumulative per run (asserted in tests).

## 6. End-to-End Benchmark Harness

```bash
uv run python -m coe.cli benchmark e2e --corpus data/corpus --repeats 10 --seed 42
```

- Replays corpus disruptions against isolated clones of `factory_demo_01` (Phase 1 clone semantics), `--repeats` times each, all seeds derived deterministically from the base seed.
- Aggregates per-stage medians and p95s across runs, plus end-to-end totals, emitted to `latency_by_stage.csv`.
- Shadow-disabled by default so production-path numbers stay clean; a `--with-shadow` flag enables the node for the optional shadow-overhead row.
- Live-LLM caveat applies unchanged: timing distributions are exactly reproducible only under fake/cached LLM responses; live-provider replays are marked as such in the CSV header comment.

## 7. Solver Comparison Methodology

- Classical side: Phase 2 engine solving the exact `tests/fixtures/quantum/micro_instance.json` fixture; `solve_duration_seconds`, makespan, tardiness, and status recorded per run over `--repeats`.
- Quantum side: rows imported from `quantum_benchmark.json` (per-depth build/optimize/sample seconds, feasible rate, optimizer status).
- Gap accounting: decoded-schedule makespan and true tardiness re-evaluated with classical formulas (Phase 4 §8), never surrogate costs.
- Emitted as `solver_comparison.csv`: one row per depth plus the CP-SAT baseline row, columns `{engine, p_or_null, seconds_build, seconds_optimize, seconds_sample_or_solve, feasible_rate, makespan, true_tardiness, gap_vs_cpsat, optimizer_status}`.
- Hardware/environment provenance copied into the manifest so the comparison is auditable.

## 8. Publication Bundle

Generated by one command (`benchmark publish`) into `publication/`:

- `latency_by_stage.csv` — stage-level median/p95 from Section 6.
- `fidelity_metrics.csv` — translation exact-match rates, strategy validity, non-degradation (including `baseline_infeasible` exclusions) from Phase 3's `benchmark_report.json`.
- `solver_comparison.csv` — Section 7 table.
- `manifest.json` — package versions (ortools, qiskit, aer, langgraph, sqlalchemy…), every random seed used, fixture SHA-256 hashes, instance checksums, host/timestamp, and the ordered CLI command sequence that regenerates all artifacts from a clean checkout.

Bundle regeneration is idempotent: same inputs, same bytes.

## 9. Configuration

Extends the pydantic-settings stack:

- `QUANTUM_SHADOW_ENABLED` (default false)
- `E2E_REPEATS` (default 10)
- `PUBLISH_OUTPUT_DIR` (default `publication/`)

All other knobs are inherited unchanged from Phases 1–4.

## 10. Command Interface

```bash
uv run python -m coe.cli benchmark e2e --corpus data/corpus --repeats 10 --seed 42 [--with-shadow]
uv run python -m coe.cli benchmark publish --out publication/
```

`publish` reads existing artifacts (phase 3 report, quantum benchmark, e2e CSV) and assembles the bundle; it never executes solvers itself.

## 11. Validation and Testing Strategy

### Tier 1: Shadow Isolation

- With shadow enabled: `quantum_shadow_json` populated, run status `COMMITTED`, committed entries byte-identical to a shadow-disabled run of the same state and seed.
- Neighborhood that cannot form a valid slice ⇒ `{status: NO_VALID_SLICE}`, no exception escapes.
- Injected failure inside Phase 4 calls ⇒ `SHADOW_FAILED`, run unaffected.

### Tier 2: Instrumentation Correctness

- Node timings present for every executed node; monotonic; sum-consistent within tolerance.
- Instrumentation overhead assertion (<10 ms cumulative).
- Disabled shadow ⇒ no quantum timing rows.

### Tier 3: Harness Reproducibility

- Same corpus + seed ⇒ statistically identical aggregated CSVs (byte-identical under fake LLM).
- `--with-shadow` changes only the shadow row, no others.

### Tier 4: Bundle Integrity

- `publish` regenerates byte-identical outputs from unchanged artifacts.
- Manifest command sequence, executed on a clean checkout, reproduces all three CSVs (fake-LLM mode).

## 12. Acceptance Criteria

Phase 5 is complete when:

1. The `quantum_shadow` node runs the Phase 4 pipeline end-to-end when enabled and writes valid `quantum_shadow_json`; disabled runs carry zero quantum overhead.
2. Committed schedules are provably identical with the shadow ON vs OFF for the same input state and seed.
3. Every run persists complete `node_timings_json`; overhead stays under the asserted bound.
4. `benchmark e2e` replays the corpus deterministically and emits `latency_by_stage.csv` with per-stage median/p95 and end-to-end totals.
5. `solver_comparison.csv` contains real CP-SAT and QAOA rows for the identical micro-instance fixture, with gaps computed on re-evaluated makespan and true tardiness.
6. `fidelity_metrics.csv` faithfully consolidates Phase 3 metrics including `baseline_infeasible` handling.
7. `manifest.json` lists versions, seeds, hashes, and the regeneration command sequence.
8. Executing the manifest commands on a clean checkout reproduces the bundle byte-for-byte in fake-LLM mode.

## 13. Phase Boundary

Phase 5 delivers the integrated, instrumented, measured system and its publication-ready data bundle. It does not:

- Alter any frozen interface from Phases 1–4.
- Grant the quantum path any scheduling authority.
- Author manuscript text or figures.
- Draw performance conclusions beyond the recorded evidence.
