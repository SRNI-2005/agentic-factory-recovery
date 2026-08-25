# Streamlit Cockpit Dashboard Design

**Status:** Approved
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
- **Excel schedule upload deferred** to post-P5 (net-new parser domain work, no demo value lost).

## 3. Architecture

```text
coe/dashboard/
├── app.py          # entrypoint, page config, sidebar (instance selector, fork badge)
├── pages/
│   ├── cockpit.py      # chat recovery + live decision feed + schedule animation
│   ├── configure.py    # schedule / materials / workers / machines / jobs views + edits
│   ├── runs.py         # recovery run history inspector
│   └── benchmarks.py   # fidelity charts (+ CP-SAT vs QAOA tables after P4/P5)
├── data.py         # plain query/loader functions (explicit ORDER BY everywhere)
├── actions.py      # thin wrappers calling existing service functions (CLI parity)
└── fork.py         # instance fork service: copies all instance-scoped rows atomically
```

- Launch: `uv run python -m coe.cli dashboard` (wraps `streamlit run coe/dashboard/app.py`). New deps: `streamlit`, `plotly`.
- Views open read-only SQLAlchemy sessions over existing models; every query scoped by sidebar-selected `instance_id` (FK discipline preserved).
- Actions invoke exactly the functions the CLI commands call. Disruption buttons publish **real MQTT events**, exercising subscriber → listener → auto-recovery end-to-end.
- No auth; loopback-only, matching dev posture.

### Fork service (`fork.py`)

- Copies all ~25 instance-scoped tables under a new `instance_id` in one transaction; records parentage in provenance tables.
- Name collisions resolved with the importer `name@<8hex>` convention.
- Deep tests required: per-table row-count invariants, FK integrity sweep, lineage assertions.

## 4. Pages

### Cockpit (main)
- Chat input driving `build_graph(client).stream(state)` — same path as `cli recover --narrative`; LLM pre-flight check reused (chat disabled with guidance if key missing).
- Decision feed: one appended line per graph-node boundary ("Detected machine down M3" → "Running CP-SAT solver…" → …). Solve node renders as spinner stage (~180s factory floor is expected latency).
- On COMMIT: animated old→new schedule transition (plotly frames between the two latest `schedule_versions`).
- Live events rail: background MQTT subscriber thread (`st.cache_resource`) buffering into a deque, fragment auto-refresh ~1s.

### Configure
Tabs: Schedule (Gantt, version picker) · Materials (stock levels, receipts form, amount edits) · Workers (assignments, availability, absence windows) · Machines (status, downtime windows) · Jobs/day view.
- Any edit triggers fork-on-edit first; sidebar badge shows "viewing fork of factory_demo_01".

### Runs
Every `recovery_run`: narrative, per-node wall-clock timeline, strategy rounds, gate verdicts, explain text.

### Benchmarks
Fidelity metrics now; solver-comparison tables once P5 publishes them.

## 5. Data Access Tiers

All 25 instance-scoped tables fall into three tiers.

### Tier 1 — Problem definition (read-only on template, editable on forks via safe unit operations)
`machines`, `machine_capabilities`, `job_families`, `operations`, `operation_machine_alternatives`, `setup_times`, `workers`, `worker_roles`, `operation_machine_worker_times`, `worker_availability_windows`, `operation_bom`.

Editable on forks, implemented as **validated atomic unit operations** (integrity-guarded, not feasibility-policed):
- Add/remove a whole job: job + operations + alternatives + BOM cascade as one transaction.
- Tune `processing_time` on existing alternatives.
- Toggle op↔machine eligibility; a toggle that would leave any operation with zero remaining alternatives is rejected with an explanatory message.
- Adjust BOM quantities.

Individual operation resequencing and speed-matrix/capability surgery remain out of scope (high corruption risk, no demo need).

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
| `telemetry_events` | MQTT ingestion (append-only audit log) |
| `schedule_versions`, `schedule_entries` | Solver committer (rollback creates versions, never edits) |
| `recovery_runs`, `recovery_proposals`, `schedule_explanations` | LangGraph pipeline |

## 6. Action → Code Path Map

| UI action | Existing code path exercised |
|---|---|
| Machine OFF/ON | Publish MQTT `MACHINE` event → `coe/mqtt/ingest` (advisory lock, interval union) |
| Worker absent/return | `WORKER` topic, same machinery |
| Material shortage | `MATERIAL` telemetry |
| Receipt insert | Materials reservoir insert path |
| Suspend job | `coe/agents/catalog.py` SUSPEND_JOB semantics |
| Chat recovery | `coe/agents/graph.py::build_graph().stream()` + `runs.py` recorder |
| Configure edit | `fork.py`, then direct model mutation on the fork |

## 7. Error Handling

- Missing LLM key: chat disabled with env-setup pointer (pre-flight shared with CLI).
- Solver statuses rendered verbatim: OPTIMAL/FEASIBLE green, INFEASIBLE red (+ explain output), UNKNOWN amber ("budget starved").
- Broker unreachable: banner + retry button.
- Integrity violations in unit ops: friendly field-level messages, never raw tracebacks.
- Fork name collision: `@<8hex>` suffix.

## 8. Testing

- `data.py` loaders and `actions.py` wrappers are plain functions → pytest under `db` marker, TDD per repo convention.
- `fork.py`: deepest coverage (row counts, FK sweep, lineage, atomicity under failure).
- One Streamlit `AppTest` smoke test asserting pages render against `clean_db` + `demo_scenario`.
- No browser e2e automation; manual demo script lives in docs.

## 9. Build Milestones

1. **A — Read-only cockpit:** app shell, sidebar/fork badge, all Configure tabs, Runs inspector, Benchmarks (fidelity only).
2. **B — Actions:** machine toggle, absence, receipts form, suspend job, chat recovery (blocking stream display).
3. **C — Live polish:** decision feed streaming, schedule diff animation, live events rail.

Excel upload re-evaluated after P5.
