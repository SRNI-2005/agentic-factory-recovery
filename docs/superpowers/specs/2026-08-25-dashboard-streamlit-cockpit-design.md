# Streamlit Cockpit Dashboard Design

> **SUPERSEDED 2026-08-26** by `2026-08-26-react-fastapi-cockpit-design.md` (stack pivot after Playwright MCP changed verification economics; data model, fork semantics, workbook pipeline, and validation boundary carry forward verbatim).

**Status:** Approved → Superseded
**Date:** 2026-08-25
**Phase:** Post-P3 net-new scope (frontend); precedes and runs alongside P4/P5

## 1. Purpose

A Streamlit multipage dashboard that turns the existing CLI-driven recovery system into a visual cockpit for research demos, paper artifacts, and portfolio showcase. It wraps existing services — LangGraph pipeline, MQTT ingestion, CP-SAT committer — with zero duplicated business logic, and produces documentation-grade visuals (schedule Gantts, live decision feeds) usable in papers and specs.

Non-goals: production hardening (auth, TLS, multi-tenant), replacing the CLI, any change to frozen Phase 2 solver contracts or P1–P3 schemas beyond additive provenance writes.

## 2. Key Decisions

- **Streamlit + plotly**, pure Python, direct imports of `coe.*` modules. React+FastAPI and Dash were evaluated and rejected: weeks-vs-days effort for equal demo value at this project's stage.
- **Built before P4/P5** so documentation screenshots exist as those phases develop. Benchmark page grows comparison tables when P4/P5 land.
- **Fork-on-edit:** `factory_demo_01` is a pristine, read-only template. Every mutation — including Tier 1 structural edits — happens on an automatic fork (`factory_demo_01@<8hex>` or user-named), lineage recorded in provenance tables. Template byte-reproducibility pins (demo_scenario fixture, P4 extractor determinism, fidelity corpus) are never violated.
- **Honest infeasibility:** users may create broken schedules; the UI reports solver statuses verbatim (`INFEASIBLE` red with explanation, `UNKNOWN` amber as budget-starved, never mislabeled).
- **Interaction model — uploads over forms:** bulk configuration happens by downloading the shipped factory workbook template (xlsx), filling it in, and uploading it back. A validation-first importer rejects malformed input at upload time with row-level messages; only clean data becomes a derived instance; CP-SAT alone judges feasibility and the LLM explains verdicts. Event-style actions stay as buttons (machine toggle, absence, suspend) and chat stays the recovery trigger — nobody fills a spreadsheet mid-demo to turn off a machine.
- **Single workbook, whole-factory semantics:** the file IS the instance state (per-sheet whole-domain replace), validated holistically across sheets before anything is written — mirroring how mk01/nouri/gass imports work. This upload mechanism dissolves the earlier forms-cost argument: leaf value edits (speed matrices, setup times, availability windows) become cheap to expose and are **in scope on forks**. Only operation resequencing and provenance surgery remain excluded.

## 3. Architecture

```text
coe/dashboard/
├── app.py          # entrypoint, page config, sidebar (instance selector, fork badge)
├── pages/
│   ├── cockpit.py      # chat recovery + live decision feed + schedule animation
│   ├── configure.py    # read-only views + workbook download/upload
│   ├── runs.py         # recovery run history inspector
│   └── benchmarks.py   # fidelity charts (+ CP-SAT vs QAOA tables after P4/P5)
├── data.py         # plain query/loader functions (explicit ORDER BY everywhere)
├── actions.py      # thin wrappers calling existing service functions (CLI parity)
└── fork.py         # instance fork service: copies all instance-scoped rows atomically

coe/parsers/workbook.py     # NEW fourth importer: user workbook → instance tables,
                            #   same discipline as mk01/nouri/gass (atomic, checksum-
                            #   idempotent name@<8hex>, loud rejection); includes
                            #   export(instance) for the round-trip direction

data/templates/factory_workbook.xlsx   # shipped fill-in template: Meta, Jobs,
                            #   Alternatives, Speeds, Setups, Materials, Receipts,
                            #   Availability, BOM sheets
```

No existing parser covers user-authored factories (MK01/Nouri/GASS read benchmark formats; the scenario builder samples synthetic content), so the workbook importer is net-new — but bounded: it reuses the established importer transaction/naming machinery, and the coherence rules the scenario builder enforces implicitly (every op keeps ≥1 alternative, required roles exist, BOM SKUs resolve) are extracted into shared validators used by both builder and workbook importer — one definition of "legal factory".

Workbook flow (two-phase):
1. **Validate (dry run):** sheet/column presence, types, integer minutes, non-negative constraints, cross-sheet FK resolution (unknown SKU/job/machine names), duplicate keys, coherence invariants. Output: row-level error report — nothing written.
2. **Apply:** validated workbook builds a NEW derived instance (fork lineage recorded; auto-fork when the upload targets a template), one transaction, whole-file-or-nothing.

- Launch: `uv run python -m coe.cli dashboard` (wraps `streamlit run coe/dashboard/app.py`). New deps: `streamlit`, `plotly`.
- Views open read-only SQLAlchemy sessions over existing models; every query scoped by sidebar-selected `instance_id` (FK discipline preserved).
- Actions invoke exactly the functions the CLI commands call. Disruption buttons publish **real MQTT events**, exercising subscriber → listener → auto-recovery end-to-end.
- No auth; loopback-only, matching dev posture.

### Fork service (`fork.py`)

- Copies all instance-scoped rows under a new `instance_id` in one transaction, **excluding** `telemetry_events` (fresh audit log per fork, see §5); records parentage in provenance tables.
- Name collisions resolved with the importer `name@<8hex>` convention.
- Deep tests required: per-table row-count invariants, FK integrity sweep, lineage assertions.

## 4. Pages

### Cockpit (main)
- Chat input driving `build_graph(client).stream(state)` — same path as `cli recover --narrative`; LLM pre-flight check reused (chat disabled with guidance if key missing).
- Decision feed: one appended line per graph-node boundary ("Detected machine down M3" → "Running CP-SAT solver…" → …). Solve node renders as spinner stage (~180s factory floor is expected latency).
- On COMMIT: animated old→new schedule transition (plotly frames between the two latest `schedule_versions`).
- Live events rail: background MQTT subscriber thread (`st.cache_resource`) buffering into a deque, fragment auto-refresh ~1s.

### Configure
Read-only tabs: Schedule (Gantt via `active_schedule`, version picker) · Materials · Workers · Machines · Jobs/day view — each with a **Download template** / **Upload** control pair.
- Upload runs the two-phase flow (§3): dry-run report shown inline; on success the fork is created/updated and the badge reflects it.
- Event actions (machine toggle, absence, suspend) live here as buttons, not in templates.

### Runs
Every `recovery_run`: narrative, per-node wall-clock timeline, strategy rounds, gate verdicts, explain text.

### Benchmarks
Fidelity metrics now; solver-comparison tables once P5 publishes them.

## 5. Data Access Tiers

All 25 instance-scoped tables fall into three tiers.

### Tier 1 — Problem definition (read-only on template, editable on forks via safe unit operations)
`machines`, `machine_capabilities`, `job_families`, `operations`, `operation_machine_alternatives`, `setup_times`, `workers`, `worker_roles`, `operation_machine_worker_times`, `worker_availability_windows`, `operation_bom` — plus the provenance tables `instances`, `scenario_sources`, `instance_profiles` (read-only everywhere; rows are created only by importers, scenario builds, and forks).

Editable on forks via workbook uploads, implemented as **validated atomic unit operations** (integrity-guarded at parse time, never feasibility-policed there):
- Add/remove a whole job: job + operations + alternatives + BOM cascade as one transaction.
- Tune `processing_time` on existing alternatives; toggle op↔machine eligibility (a toggle leaving any operation with zero remaining alternatives is rejected with an explanatory message).
- Adjust BOM quantities; edit `materials` stock/reorder fields via the `Materials` sheet; insert receipts via the `Receipts` sheet (Tier 2 values ride the same workbook pipeline).
- **Leaf value edits:** `operation_machine_worker_times` speeds, `setup_times`, `worker_availability_windows` bounds — safe because these tables have no dependents.

Excluded everywhere: operation resequencing (breaks precedence-chain integrity), edits to `machines`/`workers`/`worker_roles`/`job_families`/`machine_capabilities` node rows (structural surgery deferred until a concrete need exists), provenance tables.

### Tier 2 — Situation state (user-editable, fork-gated)
| Table | Editable |
|---|---|
| `jobs` | `release_time`, `deadline`, `priority`. Status changes only via actions (suspend ⇒ `BLOCKED`) |
| `materials` | `initial_stock`, `reorder_point` |
| `material_receipts` | Insert receipts (SKU, qty, ETA) |
| `machine_downtime_windows` | Created via machine toggle (MQTT MACHINE path; restore closes window) |
| `worker_absence_windows` | Created via absence action (WORKER topic; RETURN closes) |

Job removal maps to `SUSPEND_JOB` catalog semantics (`jobs.status=BLOCKED`), preserving audit trails — row deletion only exists inside the whole-job unit op above.

### Tier 3 — System-owned (display-only)
| Table | Writer |
|---|---|
| `telemetry_events` | MQTT ingestion (append-only audit log; TimescaleDB hypertable) |
| `schedule_versions`, `schedule_entries` | Solver committer (rollback creates versions, never edits) |
| `recovery_runs`, `recovery_proposals`, `schedule_explanations` | LangGraph pipeline |

Display surfaces:
- The `active_schedule` view (latest non-rolled-back OPTIMAL/FEASIBLE version per instance) is the canonical source for Gantt rendering — never re-derive it in dashboard queries.
- `recovery_runs.trigger` admits only `'CLI' | 'MQTT'`; chat-initiated recoveries record as `'CLI'`.

Fork semantics for system-owned data:
- Copied: derived situation state (`machine_downtime_windows`, `worker_absence_windows`), so the fork solves the same problem as its parent.
- **Not copied:** `telemetry_events` (the audit log of what happened to the *original*); each fork starts a fresh telemetry history.

## 6. Action → Code Path Map

| UI action | Existing code path exercised |
|---|---|
| Machine OFF/ON | Publish MQTT `MACHINE` event → `coe/mqtt/ingest` (advisory lock, interval union) |
| Worker absent/return | `WORKER` topic, same machinery |
| Material shortage | `MATERIAL` telemetry |
| Receipt insert | Materials reservoir insert path |
| Suspend job | `coe/agents/catalog.py` SUSPEND_JOB semantics |
| Chat recovery | `coe/agents/graph.py::build_graph().stream()` + `runs.py` recorder |
| Workbook upload (valid) | `coe/parsers/workbook.py` import → derived instance (auto-fork lineage), one transaction |
| Workbook upload (invalid) | Rejected at dry-run with row-level report; nothing written |

## 7. Error Handling

- **Parse-time (upload):** missing sheets/columns, bad types, negative values, unknown names, duplicate keys, broken cross-sheet references → immediate row-level report ("Jobs sheet row 7: deadline must be integer ≥ release_time"); file rejected whole, nothing written. Malformed data never reaches the solver.
- **Solve-time:** capacity conflicts / impossible plans are CP-SAT's verdict alone — INFEASIBLE explained by the agent, per the honest-infeasibility decision.
- Missing LLM key: chat disabled with env-setup pointer (pre-flight shared with CLI).
- Solver statuses rendered verbatim: OPTIMAL/FEASIBLE green, INFEASIBLE red (+ explain output), UNKNOWN amber ("budget starved").
- Broker unreachable: banner + retry button.
- Fork name collision: `@<8hex>` suffix.

## 8. Testing

- `coe/parsers/workbook.py` (import + export): heaviest new-test surface — every sheet's happy path, every rejection class, atomicity (partial workbook never lands), fork lineage integration, and the round-trip property: `export(instance) → import unchanged ⇒ derived instance byte-identical to parent` (canonical-dump hash). TDD per repo convention; mirrors existing importer test style.
- `fork.py`: row counts, FK sweep, lineage, atomicity under failure.
- `data.py` loaders and `actions.py` wrappers are plain functions → pytest under `db` marker.
- One Streamlit `AppTest` smoke test asserting pages render against `clean_db` + `demo_scenario`.
- No browser e2e automation; manual demo script lives in docs.

## 9. Build Milestones

1. **A — Read-only cockpit:** app shell, sidebar/fork badge, Configure views, Runs inspector, Benchmarks (fidelity only).
2. **B — Configuration pipeline:** workbook template + `coe/parsers/workbook.py` (import/export, shared validators), upload UI with dry-run reports, event buttons (machine toggle, absence, suspend), chat recovery (blocking stream display).
3. **C — Live polish:** decision feed streaming, schedule diff animation, live events rail.
