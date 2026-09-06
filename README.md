# COE — Agentic Factory Recovery System

LLM-agent middleware for event-driven FJSP (Flexible Job Shop Scheduling) recovery. Combines a deterministic CP-SAT optimization engine with an LLM-powered agent pipeline to recover factory schedules when disruptions occur — machine failures, worker absences, material shortages.

**Agents own semantics** (translate → propose → explain); **deterministic solvers own math** (CP-SAT production engine).

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Setup](#setup)
- [Database](#database)
- [Solver](#solver)
- [LLM Integration](#llm-integration)
- [Streamlit Dashboard](#streamlit-dashboard)
- [Disruptions](#disruptions)
- [Demo Walkthrough](#demo-walkthrough)
- [CLI Reference](#cli-reference)
- [Testing](#testing)
- [Project Status](#project-status)

---

## Quick Start

```bash
# 1. Start infrastructure (TimescaleDB + Mosquitto)
docker compose up -d

# 2. Install dependencies
uv sync

# 3. Configure environment
cp .env.example .env  # or edit .env with your values

# 4. Reset database (runs all Alembic migrations)
uv run python -m coe.cli db reset

# 5. Import benchmark data sources
uv run python -m coe.cli import mk01
uv run python -m coe.cli import hutter --dir data/raw/nouri-fjspw/extracted/SFJW/
uv run python -m coe.cli import hutter --dir data/raw/nouri-fjspw/extracted/MFJW/
uv run python -m coe.cli import gass --dir data/raw/gass/

# 6. Build demo scenario
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42

# 7. Run baseline solver
uv run python -m coe.cli solve baseline --instance factory_demo_01

# 8. Launch dashboard
uv run python -m coe.cli dashboard
# → http://127.0.0.1:8501
```

---

## Architecture

```
Disruption Event (MQTT / CLI / Dashboard)
    │
    ▼
┌─────────────────────────────────────────────────┐
│           LangGraph Agent Pipeline               │
│                                                  │
│  Translate (LLM) → Ingest → Investigate (×4)     │
│       ↓                                          │
│  Strategy (LLM) → [negotiation loop] → Compile   │
│       ↓                                          │
│  Solve (CP-SAT) → Gate → Commit → Verify         │
│       ↓                                          │
│  Explain (LLM)                                   │
└─────────────────────────────────────────────────┘
    │
    ▼
TimescaleDB (versioned schedules, telemetry)
```

### Component Map

| Layer | Module | Responsibility |
|-------|--------|---------------|
| CLI | `coe/cli.py` | ~20 subcommands for all operations |
| Config | `coe/config.py` | Pydantic Settings from `.env` |
| Database | `coe/db/` | SQLAlchemy 2.0 + 8 Alembic migrations |
| Parsers | `coe/parsers/` | MK01, Nouri (Hutter), GASS, Workbook import |
| Scenario | `coe/scenario/` | Seeded deterministic factory builder |
| MQTT | `coe/mqtt/` | Kind-routed event ingestion |
| Solver | `coe/solver/` | CP-SAT engine, payload builder, committer |
| Agents | `coe/agents/` | LangGraph pipeline with 3 LLM nodes |
| Dashboard | `coe/dashboard/` | Streamlit cockpit (4 pages) |
| Services | `coe/services/` | Fork, recovery runs, schedule loaders |

---

## Setup

### Prerequisites

- Docker & Docker Compose
- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager

### Infrastructure

```bash
docker compose up -d
```

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| timescaledb | `timescale/timescaledb:latest-pg16` | 127.0.0.1:5432 | Time-series DB with hypertables |
| mosquitto | `eclipse-mosquitto:2` | 127.0.0.1:1883 | MQTT broker (dev-only, anonymous) |

**Credentials:** `coe/coe/coe` (dev-only, never deploy as-is)

### Environment Configuration (`.env`)

```bash
# Database
DATABASE_URL=postgresql+psycopg://coe:coe@localhost:5432/coe

# MQTT
MQTT_HOST=localhost
MQTT_PORT=1883

# LLM (choose one provider)
LLM_PROVIDER=gemini          # or: openai
LLM_MODEL=gemini-3.5-flash   # or: gpt-4o-mini, gemini-2.5-flash
LLM_TEMPERATURE=0            # reproducibility default

# API Key (for Gemini — get one at https://aistudio.google.com/apikey)
GOOGLE_API_KEY=your-key-here
# Or for OpenAI:
# OPENAI_API_KEY=your-key-here

# Agent behavior
STRATEGY_MAX_ROUNDS=3
LLM_MAX_RETRIES=2
BENCHMARK_TRANSLATION_ACCURACY=0.90

# Solver knobs
SOLVER_TIME_LIMIT_SECONDS=60
SOLVER_ALPHA_WEIGHT=1.0
SOLVER_BETA_WEIGHT=1.0
SOLVER_NUM_SEARCH_WORKERS=8
```

### Data Import

```bash
# Brandimarte MK01 benchmark (10 jobs, 6 machines, proven OPTIMAL mk=40)
uv run python -m coe.cli import mk01

# Nouri FJSSP-W benchmark (worker flexibility layer)
uv run python -m coe.cli import hutter --dir data/raw/nouri-fjspw/extracted/SFJW/
uv run python -m coe.cli import hutter --dir data/raw/nouri-fjspw/extracted/MFJW/
# Note: MFJW-05/06/07 are author-corrupted — rejected loudly at import time

# GASS setup-time benchmarks (xlsx → instance_profiles)
uv run python -m coe.cli import gass --dir data/raw/gass/
```

### Scenario Builder

Builds a deterministic factory from all imported sources:

```bash
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
```

Produces: 30 jobs / 8 machines / 168 operations with worker flexibility, setup times, and synthetic materials. Byte-reproducible for a given seed.

---

## Database

**8 Alembic migrations** (authoritative DDL — `create_all` is forbidden). All tables are instance-scoped (`instance_id` FK).

### Key Tables

| Category | Tables |
|----------|--------|
| Core | `instances`, `scenario_sources`, `instance_profiles` |
| FJSP | `machines`, `jobs`, `operations`, `operation_machine_alternatives`, `setup_times` |
| Workers | `workers`, `worker_roles`, `operation_machine_worker_times`, `worker_availability_windows`, `worker_absence_windows` |
| Materials | `materials`, `operation_bom`, `material_receipts` |
| Telemetry | `telemetry_events` (hypertable), `machine_downtime_windows` |
| Schedule | `schedule_versions`, `schedule_entries` |
| Recovery | `recovery_runs`, `recovery_proposals`, `schedule_explanations` |

### Commands

```bash
uv run python -m coe.cli db reset      # DESTRUCTIVE: drops user tables, re-runs migrations
uv run python -m coe.cli db migrate    # Alembic upgrade head
```

---

## Solver

### CP-SAT Engine (`coe/solver/engine.py`)

Pure function: payload JSON in → solution JSON out. No DB, no LLM, no side effects.

**Two-phase warm start:**
1. **Relaxation** — solves without setup circuit; solution replayed as hint
2. **Full model** — setup circuit enabled, warm-started from Phase A (or greedy fallback)

**Solver contract:** Statuses `OPTIMAL` / `FEASIBLE` / `INFEASIBLE` / `UNKNOWN` (UNKNOWN = budget starved — never material-conflict)

### Commands

```bash
# Baseline (no disruptions)
uv run python -m coe.cli solve baseline --instance factory_demo_01

# Recovery (with failed machine)
uv run python -m coe.cli solve recovery --instance factory_demo_01 --failed-machine M1 [--at 512]

# View active schedule
uv run python -m coe.cli schedule show --instance factory_demo_01

# Rollback last schedule
uv run python -m coe.cli schedule rollback --instance factory_demo_01

# Restore a failed machine
uv run python -m coe.cli machine restore --instance factory_demo_01 --machine M1
```

### Engine Limits

- **MK01**: proven OPTIMAL mk=40 (~1.5s)
- **Factory-scale**: FEASIBLE-at-cap (hint-quality, not proven optimal)
- Recovery solves: 180s floor by default

---

## LLM Integration

### 3 LLM Nodes

| Node | Role | Output |
|------|------|--------|
| **Translate** | Natural language → structured DisruptionRecord | `{kind, machine/worker/material, event_type, occurred_at, severity}` |
| **Strategy** | Propose recovery candidates from closed catalog | `{candidates: [...], final: bool}` |
| **Explain** | Generate human-readable rationale | Plain-text prose (≤150 words) |

Everything else in the pipeline is deterministic. LLM usage is confined to these 3 nodes behind a `LLMClient` Protocol — tests inject `FakeLLMClient` with canned responses.

### Closed Strategy Catalog

| Candidate | Effect |
|-----------|--------|
| `TARDINESS_WEIGHT` | Per-job solver weight (0..10) |
| `DEFER_JOB` | Delay job release by offset |
| `SUSPEND_JOB` | Block all pending ops of a job |
| `EXPEDITE_MATERIAL` | Add a material receipt |
| `WEIGHT_PRESET` | Override alpha/beta objective weights |

### Natural Language Mapping

The translate node accepts natural language names ("machine one", "press 01") and maps them to valid instance identifiers. It queries the DB for actual machine/worker/material IDs and includes them in the prompt.

---

## Streamlit Dashboard

```bash
uv run python -m coe.cli dashboard              # default port 8501
uv run python -m coe.cli dashboard --port 9000  # custom port
```

### Sidebar

- **Instance selector** (defaults to `factory_demo_01`)
- **Fork lineage** caption (for workbook-derived instances)
- **Live events rail** — passive MQTT mirror showing last 10 events

### Pages

#### 1. Cockpit — Recovery Chat

The main interface for agentic recovery:

1. Type a disruption description in natural language
2. Watch the live decision feed (node-by-node progress)
3. View outcome metrics (makespan, tardiness, solver status)
4. See before/after Gantt diff animation
5. Read LLM-generated explanation

**Example prompts:**
```
Machine M3 failed, estimate 2 hours to repair
Worker W5 called in sick today
Material MAT-002 is running low — won't last the shift
M1 went down hard — looks like 4 hours downtime
```

#### 2. Configure — Instance Inspector

Five read-only tabs with action buttons:

| Tab | Shows | Actions |
|-----|-------|---------|
| Schedule | Active Gantt + version history | — |
| Materials | Stock, reorder points, receipts | Report SHORTAGE |
| Machines | Status table | Mark DOWN / Restore |
| Workers | Availability table | Mark ABSENT / Return |
| Jobs/day | Job overview + deadline chart | Suspend / Resume |

Also: **Workbook** section for downloading/uploading xlsx workbooks (upload creates forked instances).

#### 3. Runs — Recovery History

- Recovery run history with expandable cards
- Disruption record JSON, per-node wall-clock bar charts
- Quantum shadow data

#### 4. Benchmarks — Fidelity Report

- Corpus pass rate, exact match rate, threshold MET/MISS
- Per-case translation data table
- Strategy comparison metrics

---

## Disruptions

### Supported Disruption Types

| Resource | Event | Effect |
|----------|-------|--------|
| **Machine** | `FAILURE` | Creates downtime window, sets status=FAILED |
| **Machine** | `MAINTENANCE` | Creates downtime window |
| **Worker** | `WORKER_ABSENT` | Creates absence window, sets status=UNAVAILABLE |
| **Worker` | `WORKER_RETURN` | Closes absence windows, sets status=AVAILABLE |
| **Material** | `MATERIAL_SHORTAGE` | Telemetry only (triggers recovery) |
| **Material` | `MATERIAL_RESTOCK` | Telemetry only |

### Triggering Disruptions

#### Via CLI

```bash
# Machine failure
uv run python -m coe.cli mqtt test-failure --instance factory_demo_01 --machine M3 --at 512

# Worker absence
uv run python -m coe.cli mqtt test-absence --instance factory_demo_01 --worker W3 --at 480

# Material shortage
uv run python -m coe.cli mqtt test-shortage --instance factory_demo_01 --sku MAT-001 --at 300
```

#### Via Streamlit Dashboard

Use the action buttons on the **Configure** page:
- Machines tab → "Mark DOWN" / "Restore"
- Workers tab → "Mark ABSENT" / "Return"
- Materials tab → "Report SHORTAGE"
- Jobs → "Suspend" / "Resume"

#### Via MQTT

Publish to topic `factory/{instance}/{resource}/{id}/events`:

```json
{
  "message_id": "unique-id-123",
  "instance_id": "factory_demo_01",
  "resource_kind": "MACHINE",
  "machine_id": "M3",
  "event_type": "FAILURE",
  "occurred_at": 512,
  "severity": "HIGH"
}
```

### Expected Results

**Before disruption:**
- Baseline schedule: FEASIBLE, makespan=406, tardiness=1973
- All machines ACTIVE, workers AVAILABLE

**After machine failure (e.g., M3 at t=512):**
- M3 status → FAILED with downtime window
- Recovery pipeline triggers automatically (via MQTT listener) or manually (via Cockpit)
- New schedule committed: operations on M3 reassigned to other machines
- Makespan may increase (or stay same if slack exists)
- LLM explains the changes made

**After worker absence:**
- Worker status → UNAVAILABLE with absence window
- Operations requiring that worker reassigned to eligible alternatives

**After material shortage:**
- Three-layer check: absolute supply → aggregate shortfall → time-phased
- Recovery may defer/suspend consuming jobs or expedite delivery
- Material-reactive back-edges trigger deterministic re-planning

---

## Demo Walkthrough (~5 minutes)

### 1. Configure Tour
- Open dashboard at http://127.0.0.1:8501
- Browse the **Configure** page tabs: Schedule (Gantt), Machines, Workers, Materials, Jobs
- Note the baseline metrics: makespan=406, tardiness=1973

### 2. Trigger a Disruption
```bash
uv run python -m coe.cli mqtt test-failure --instance factory_demo_01 --machine M3 --at 512
```
- Watch the live events rail appear in the sidebar
- Machines tab now shows M3 as FAILED

### 3. Recover via Cockpit
- Go to **Cockpit** page
- Type: `Machine M3 has failed at minute 512. Recover.`
- Watch the decision feed: translate → ingest → investigate → strategy → compile → solve → gate → commit → verify → explain
- View the before/after Gantt diff
- Read the LLM explanation

### 4. Verify Recovery
- **Configure** → Schedule tab: new version committed
- Metrics updated (makespan may differ)
- M3 operations reassigned to other machines

### 5. Restore Machine
```bash
uv run python -m coe.cli machine restore --instance factory_demo_01 --machine M3
```
- M3 status → ACTIVE
- Downtime window closed

### 6. Workbook Fork (Optional)
- **Configure** → Download workbook
- Modify the xlsx (e.g., change a processing time)
- Upload → creates new forked instance
- Select it in the sidebar to view

---

## CLI Reference

```bash
# Data Import
uv run python -m coe.cli import mk01
uv run python -m coe.cli import hutter --path FILE | --dir DIR
uv run python -m coe.cli import gass --dir data/raw/gass
uv run python -m coe.cli import workbook --path FILE

# Scenario + Template
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
uv run python -m coe.cli template export --instance I --out FILE.xlsx

# Database
uv run python -m coe.cli db reset
uv run python -m coe.cli db migrate

# Solver
uv run python -m coe.cli solve baseline --instance I
uv run python -m coe.cli solve recovery --instance I --failed-machine M1 [--at MIN]
uv run python -m coe.cli schedule show --instance I
uv run python -m coe.cli schedule rollback --instance I
uv run python -m coe.cli machine restore --instance I --machine M1

# Agentic Recovery
uv run python -m coe.cli recover --instance I --narrative "..." [--at MIN]
uv run python -m coe.cli recover --instance I --narrative-file FILE
uv run python -m coe.cli explain --instance I
uv run python -m coe.cli benchmark fidelity --corpus data/corpus/fidelity-seed42 --seed 42

# MQTT
uv run python -m coe.cli mqtt listen
uv run python -m coe.cli mqtt test-failure --instance I --machine M3 --at 512
uv run python -m coe.cli mqtt test-absence --instance I --worker W3 --at 480 [--duration N]
uv run python -m coe.cli mqtt test-shortage --instance I --sku MAT-001 --at 300

# Dashboard
uv run python -m coe.cli dashboard [--port 8501]
```

---

## Testing

```bash
# Full suite (~220 tests, ~5 min)
uv run pytest -q

# Quick gate — skip MQTT + slow (~214 tests, ~2.5 min)
uv run pytest -m "not mqtt and not slow"

# Skip only MQTT-dependent tests
uv run pytest -m "not mqtt"

# Live LLM tests (opt-in, requires API key)
uv run pytest -m llm
```

### Test Markers

| Marker | Requires |
|--------|----------|
| `db` | TimescaleDB running |
| `mqtt` | Mosquitto broker running |
| `benchmark` | MK01 optimality pins |
| `slow` | Long-running tests |
| `llm` | Live LLM provider (opt-in) |

### Key Fixtures (`tests/conftest.py`)

- `clean_db` — resets database per test
- `demo_scenario` — imports all sources + builds `factory_demo_01`
- `fake_llm` — factory for `FakeLLMClient` with canned responses

---

## Project Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1. Infrastructure & Data Ingestion | **COMPLETE** | All 11 acceptance criteria green |
| 2. Classical CP-SAT Engine | **COMPLETE** | MK01 OPTIMAL; factory FEASIBLE via warm start |
| 3. Agentic Middleware | **COMPLETE** | Full LangGraph pipeline + CLI + dashboard |
| 4. Quantum Formulation | Design doc | QAOA research benchmark |
| 5. Integration Benchmarking | Design doc | — |

---

## Conventions

- **Determinism:** Every query feeding RNG/float summation has explicit `ORDER BY`. Same inputs + seed ⇒ byte-identical scenarios.
- **Alembic is authoritative DDL** — `create_all` is forbidden.
- **Instance scoping:** All table rows have `instance_id` FK — no cross-instance joins.
- **Time convention:** All times are integer minutes (shift=480, day=1440). Half-open intervals `[start, end)`.
- **Workers knob:** Default 8 (speed-first); set `workers=1` for determinism consumers.
- **psycopg3 raises CHECK violations at `execute()`, not `commit()`.**

---

## Gotchas

- `db reset` is **destructive** — wipes all instances/scenarios.
- Scenario builds refuse duplicate names; rebuild requires reset first.
- Ports are loopback-bound (127.0.0.1) — dev-only anonymous Mosquitto + coe/coe/coe DB creds.
- MFJW-05/06/07 are author-corrupted (rejected loudly at import).
- GASS process codes include lettered variants (`P2b`) — handled by `_fam_num()`.
- Gemini 3.5 returns content as list of dicts — normalized to string in `LLMClient.complete()`.
