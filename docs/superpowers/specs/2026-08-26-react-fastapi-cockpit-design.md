# React + FastAPI Recovery Cockpit Design

**Status:** Approved
**Date:** 2026-08-26
**Phase:** Post-P3 net-new scope (frontend); precedes and runs alongside P4/P5
**Supersedes:** `2026-08-25-dashboard-streamlit-cockpit-design.md`

## 1. Purpose

A React (Vite + TypeScript) cockpit backed by a FastAPI service layer, turning the existing CLI-driven recovery system into a visual, demo-grade instrument for research demos, paper artifacts, and portfolio showcase. It wraps existing services — LangGraph pipeline, MQTT ingestion, CP-SAT committer — through one shared service layer with zero duplicated business logic, and produces documentation-grade visuals (schedule Gantt, live decision feed, animated schedule diff).

Non-goals: production hardening (auth beyond loopback, TLS, multi-tenant), replacing the CLI, changes to frozen Phase 2 solver contracts or P1–P3 schemas beyond additive provenance writes.

## 2. Key Decisions

- **Stack pivot 2026-08-26:** Streamlit → **React 19 + Vite + TypeScript** frontend, **FastAPI** backend. Drivers: (a) demo must not look generic — Streamlit has a hard aesthetic ceiling; shadcn/ui + custom SVG Gantt does not; (b) irreversibility asymmetry — Streamlit→React discards render layers, the reverse never happens; (c) token economics favored building the final stack now. Verification via Playwright MCP applies to both, so it was never the deciding factor.
- **Component base: shadcn/ui + Tailwind v4.** Own-the-code model, smallest bundle, Radix a11y, best AI-codegen fit. Charts via shadcn/ui Charts (Recharts). Tables via TanStack Table.
- **Gantt: custom SVG**, not a library — 168 operations makes library performance benchmarks irrelevant; version-diff animation as first-class motion is the differentiator no library provides. Fallback if heavy: `@svar-ui/react-gantt` (MIT, pure React, hours/minutes scales) for static view + Motion on top.
- **Animation: Motion** (`motion/react`), `layoutId` FLIP for schedule moves; CSS transitions elsewhere per Vercel rules ("smallest tool that satisfies").
- **Live transport: SSE** over WebSockets — decision feed and MQTT rail are server-push only; all actions stay plain HTTP POST/JSON. `POST /runs` starts recovery as a background task; `GET /runs/{id}/events` (EventSource) subscribes — page refresh mid-solve reattaches instead of killing a 180 s run.
- **Built before P4/P5** (unchanged) so documentation visuals exist during those phases. Benchmarks view grows comparison tables when they land.
- **Carried forward verbatim from superseded spec (still normative):**
  - Fork-on-edit: `factory_demo_01` pristine template; every mutation lands on an automatic fork (`name@<8hex>`, provenance lineage); forks copy all instance-scoped rows **except** `telemetry_events`, `recovery_runs`, `recovery_proposals`; `parent_version_id`/`failed_machine_ids` nulled in copies.
  - Three data tiers incl. Tier 1 editable-on-fork via validated atomic unit ops (whole-job add/remove, processing-time/eligibility/BOM edits, leaf value edits); excludes operation resequencing, physics-node surgery, provenance writes.
  - Single-workbook round-trip configuration (`coe/parsers/workbook.py`): whole-factory semantics, two-phase validate→apply, row-level rejection reports; parse-time integrity vs solve-time feasibility boundary.
  - Interaction triad: workbook uploads = bulk config, buttons = events, chat = agentic recovery.
  - Honest verdicts: solver statuses rendered verbatim (OPTIMAL/FEASIBLE green, INFEASIBLE red + explanation, UNKNOWN amber = budget-starved, never material-conflict).
  - `active_schedule` VIEW is the canonical Gantt source; chat-initiated runs record `trigger='CLI'`.
- **Excel/user-authored schedule upload** remains out of scope (workbook templates are our own format).

## 3. Architecture

```text
coe/services/             # NEW shared layer — CLI parity lives here now
├── instances.py          # list/get instances + fork lineage
├── schedules.py          # active_schedule/gantt payloads, versions
├── configure.py          # materials/workers/machines/jobs reads
├── actions.py            # machine_down/restore, worker absent/return,
│                         #   suspend/resume job, material shortage
├── recovery.py           # run manager: start background task, event topics
└── schemas.py            # pydantic response models (single source for API)

coe/api/
├── app.py                # FastAPI factory, CORS (vite dev origin), routers
├── routers/
│   ├── instances.py      # GET /instances, /instances/{name}
│   ├── schedules.py      # GET /schedules/{iid}/active|versions
│   ├── configure.py      # GET materials/workers/machines/jobs;
│   │                     #   POST actions; GET+POST workbook
│   ├── runs.py           # GET /runs; POST /runs; GET /runs/{id}/events (SSE)
│   ├── events.py         # GET /events/stream (SSE passive MQTT mirror)
│   └── benchmarks.py     # GET /benchmarks/fidelity
└── static.py             # serves frontend/dist when built (prod mode)

frontend/
├── src/
│   ├── api/              # typed fetch client + SSE hooks (useEventSource)
│   ├── components/       # shadcn/ui copies + Gantt/, Feed/, Rail/
│   ├── views/            # Cockpit · Configure · Runs · Benchmarks
│   ├── lib/diff.ts       # schedule diff → move list (unit-tested)
│   └── styles/tokens.css # design tokens from ui-ux-pro-max + critique pass
├── DESIGN.md             # pinned aesthetic direction (frontend-design brief)
└── vite.config.ts        # dev proxy /api → :8000

data/templates/factory_workbook.xlsx   # unchanged
```

Run modes:
- **Dev:** `uvicorn coe.api.app:app --reload` (:8000) + `npm run dev` (:5173, proxies `/api`). MQTT listener stays a separate terminal process exactly as today.
- **Prod-ish demo:** `uv run python -m coe.cli dashboard` builds (if needed) and serves `frontend/dist` via FastAPI on loopback :8000 — single process.

### Service layer rule

Every router handler delegates to `coe/services/*`; services contain all orchestration and are pytest-tested directly (httpx-free). Routers stay ≤10 lines per endpoint: authn-none, parse, delegate, status-code mapping. The CLI's mutating commands migrate to call the same services where signatures allow — divergence between CLI and API behavior is a defect class this layer exists to prevent.

## 4. API Surface (action map)

| UI action | Endpoint | Service call |
|---|---|---|
| Instance select | `GET /api/instances` | `instances.list()` |
| Gantt + versions | `GET /api/schedules/{iid}/active`, `/versions` | `schedules.active()/versions()` |
| Configure reads | `GET /api/configure/{domain}?instance=` | `configure.*()` |
| Workbook download/upload | `GET/POST /api/configure/workbook?instance=` | `workbook.export()/apply()` |
| Machine ↓/↑ | `POST /api/actions/machine/down` `/restore` | publish MQTT FAILURE / window close |
| Worker absent/return | `POST /api/actions/worker/{absent,return}` | MQTT WORKER topic |
| Material shortage | `POST /api/actions/material/shortage` | MQTT MATERIAL telemetry |
| Suspend/resume job | `POST /api/actions/job/{suspend,resume}` | direct `status=BLOCKED/PENDING` |
| Start recovery (chat) | `POST /api/runs {narrative}` → `{run_id}` | background task + topic |
| Decision feed | `GET /api/runs/{id}/events` (SSE) | node-boundary events, then terminal state |
| Live rail | `GET /api/events/stream` (SSE) | passive paho mirror thread |
| Runs history | `GET /api/runs?instance=` | run rows + timings |
| Benchmarks | `GET /api/benchmarks/fidelity` | report JSON |

SSE message envelope: `{"event": "node"|"terminal"|"mqtt", "data": {...}}`.

## 5. Frontend Views

1. **Cockpit** — chat input → `POST /runs`; decision feed lines stream via SSE with icon-per-node labels; on COMMITTED the Gantt animates old→new (Motion `layoutId`, ghost→solid bars, slider scrub through intermediate frames); "Why this plan?" pulls explanation text.
2. **Configure** — tabs Schedule (SVG Gantt + version picker), Materials (+receipts, shortage button), Machines (down/restore), Workers (absence/return), Jobs/day (bar chart), each tab headered by its workbook download/upload pair with inline dry-run error list.
3. **Runs** — expandable cards per run: status chip (verdict colors), disruption JSON, per-node wall-clock bars.
4. **Benchmarks** — fidelity metric tiles; placeholder slot for P4/P5 comparison tables.

## 6. Design System Process

Mandatory pipeline before any component code:
1. `ui-ux-pro-max search.py --design-system "industrial recovery control room"` → raw candidate palette/type/layout (first hit will be dark-slate+green — treat as *raw material only*).
2. **Anti-default critique** (frontend-design + impeccable rules): reject first-order reflexes ("observability → dark blue", cream+terracotta, broadsheet). Draw vocabulary from the subject's world — SCADA/HMI consoles, control-room annunciator panels, phosphor readouts, engineering drawings — and pin one aesthetic risk.
3. Write `frontend/DESIGN.md`: 4–6 named hex values, display/body/utility faces, layout concept + ASCII wireframe, signature element (candidate: annunciator-style alarm band that lights up with live MQTT events).
4. Tokens → `tokens.css` (shadcn CSS variables); `impeccable critique` gate before Milestone R2 components are accepted.

## 7. Error Handling

- Parse-time workbook rejection → `422` with the row-level report array; UI renders inline list, nothing written (unchanged contract).
- Solver statuses verbatim in run summaries and chips; UNKNOWN captioned "budget starved".
- SSE reconnect: EventSource auto-retry; client shows stale-state banner after N failures; recovery run keeps executing server-side regardless of listeners.
- Broker unreachable: rail shows degraded badge; action endpoints return `503` with reason.
- Fork name collision → `409` with suggested `@<8hex>` alternative.
- LLM key missing → `POST /api/runs` returns `503` with env-setup hint; chat input disabled client-side after preflight `GET /api/health/llm`.

## 8. Testing

- **Backend**: services tested directly under `db`/`mqtt` markers (port existing dashboard test suites); routers via `httpx` + `TestClient` — thin, happy-path + status-code mapping; SSE endpoints tested by consuming first N events from streaming response with injected fake client.
- **Frontend**: Vitest unit tests for `lib/diff.ts` (move computation) and SSE reducer; component rendering tests deliberately minimal — Playwright MCP visual checkpoints replace them (navigate, screenshot to `.screenshots/`, interact: upload poisoned workbook, toggle machine, submit chat, assert via DOM text).
- Round-trip property survives: export→import unchanged ⇒ semantically equal instance (backend test ported as-is).
- Full gates: `uv run pytest -q`; frontend `npm run build && npm run test`; manual demo script updated for two-process dev or single-process prod mode.

## 9. Build Milestones

1. **R1 — Backend spine:** services extraction, FastAPI app + read routers, workbook endpoints, action endpoints, SSE run/events infrastructure (fake-client tested).
2. **R2 — Read-only cockpit:** Vite scaffold + design system (DESIGN.md + tokens), app shell, SVG Gantt, Runs, Benchmarks.
3. **R3 — Configuration & actions:** Configure tabs wired, workbook download/upload flow E2E, action buttons publishing real MQTT, suspend/resume.
4. **R4 — Live polish:** chat recovery + decision feed SSE, schedule diff animation, MQTT rail, impeccable audit/polish pass, demo script, full gates + tags.
