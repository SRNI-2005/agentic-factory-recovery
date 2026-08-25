# React + FastAPI Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** React+TypeScript cockpit over a FastAPI service layer wrapping the existing COE recovery system — SVG Gantt with animated schedule diffs, workbook-driven configuration on auto-forked instances, MQTT disruption buttons, and chat-driven LangGraph recovery streamed live via SSE.

**Architecture:** One shared service layer (`coe/services/`) consumed by both FastAPI routers and the CLI; recovery runs execute in background tasks publishing node-boundary events to in-memory topics; the frontend is a Vite SPA talking REST + SSE. All domain rules (fork-on-edit, workbook validation tiers, action semantics) are inherited unchanged from `docs/superpowers/specs/2026-08-26-react-fastapi-cockpit-design.md`.

**Tech Stack:** Python 3.12 · FastAPI + uvicorn · pydantic v2 · SQLAlchemy 2.0 · paho-mqtt · React 19 · Vite 7 · TypeScript · Tailwind v4 · shadcn/ui (Radix) · Recharts · TanStack Table · Motion (`motion/react`) · Vitest · Playwright MCP for visual checkpoints.

**Skills pipeline (loaded in this repo):** `ui-ux-pro-max` → token candidates; `frontend-design` → DESIGN.md brief + anti-default critique; `vercel-react-best-practices` → enforced during all component tasks (waterfalls, bundle, re-renders); `impeccable critique/polish` → gate before milestone acceptance.

## Global Constraints

- `uv` exclusively for Python; `npm`/`pnpm` inside `frontend/` only.
- Alembic is authoritative DDL; **zero migrations** in this plan; `create_all` forbidden.
- Every backend query: explicit `ORDER BY` + `instance_id` filter. Integer minutes everywhere.
- Loopback only; no auth. CORS allows exactly the Vite dev origin (:5173).
- Solver contract frozen: statuses verbatim; UNKNOWN = budget-starved caption, never material-conflict; recovery floor 180 s.
- `factory_demo_01` never mutated by any code path here.
- `recovery_runs.trigger` ∈ {CLI, MQTT}; chat runs record `'CLI'`.
- Fork copies exclude telemetry/recovery history; lineage via `ScenarioSource`.
- SSE endpoints must not block the event loop: recovery work happens in `asyncio.create_task`/thread executor, topics are thread-safe queues.
- TDD: backend tasks pytest-first; frontend logic (`lib/diff.ts`, SSE reducer) vitest-first; render layers verified via Playwright MCP checkpoints (screenshots → `.screenshots/`, gitignored).
- Quick gates between milestones: `uv run pytest -m "not mqtt and not slow" -q` AND `cd frontend && npm run build && npm run test -- run`.

## File Structure

```text
coe/services/
├── __init__.py
├── instances.py        # list/get + fork lineage read
├── schedules.py        # gantt payload from active_schedule VIEW, versions list
├── configure.py        # materials/workers/machines/jobs reads (ports data.py)
├── actions.py          # ports actions.py: machine_down/restore, worker_*,
│                       #   suspend/resume_job, material_shortage
├── fork.py             # moved verbatim from coe/dashboard/fork.py
├── recovery.py         # RunManager: start(), topics dict[run_id]->Queue,
│                       #   subscribe(run_id), publishes node boundaries
│                       #   around execute_recovery_streaming()
├── events.py           # passive MQTT mirror thread -> fan-out topic
└── schemas.py          # pydantic models: InstanceOut, GanttOut, VersionOut,
                        #   MaterialOut, WorkerOut, MachineOut, JobOut,
                        #   RunOut, WorkbookError, ActionOk

coe/api/
├── __init__.py
├── app.py              # create_app(): CORS, include routers, /api prefix,
│                       #   static mount when frontend/dist exists
└── routers/
    ├── __init__.py
    ├── instances.py  ├── schedules.py  ├── configure.py
    ├── runs.py       ├── events.py     └── benchmarks.py

coe/cli.py              # dashboard command → uvicorn serving app.py (+build hint)

frontend/
├── package.json  vite.config.ts  tsconfig.json  index.html
├── DESIGN.md           # pinned aesthetic brief (Task F6)
├── .screenshots/       # gitignored Playwright captures
└── src/
    ├── main.tsx  App.tsx  styles/tokens.css
    ├── api/client.ts   # typed fetch wrappers (instances/schedules/configure)
    ├── api/sse.ts      # useEventSource(url) hook w/ reconnect+stale state
    ├── lib/diff.ts     # schedule diff → Move[] (vitest-tested)
    ├── lib/format.ts   # minute→HH:MM axis ticks etc.
    ├── components/
    │   ├── ui/*        # shadcn copies (button, tabs, dialog, select, badge…)
    │   ├── GanttSvg.tsx        # machine rows × minute axis, op bars, tooltips
    │   ├── GanttDiffOverlay.tsx# Motion layoutId ghost→solid move frames
    │   ├── DecisionFeed.tsx    # SSE consumer → icon lines
    │   ├── EventRail.tsx       # sidebar live MQTT mirror
    │   ├── VerdictChip.tsx     # OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN colors
    │   └── WorkbookPanel.tsx   # download/upload + dry-run error list
    └── views/
        ├── CockpitView.tsx  ConfigureView.tsx
        ├── RunsView.tsx     BenchmarksView.tsx

tests/services/          # ported + new backend tests
tests/api/               # httpx TestClient router tests + SSE consumption tests
frontend/src/lib/__tests__/diff.test.ts   # vitest
```

**Porting map from superseded Streamlit plan:** `coe/dashboard/data.py` → `services/configure.py`+`schedules.py`; `coe/dashboard/actions.py` → `services/actions.py`; `coe/dashboard/fork.py` → `services/fork.py` (verbatim); workbook parser stays at `coe/parsers/workbook.py` untouched; `execute_recovery_streaming` task C14 becomes part of Task S5's RunManager; superseded test files under `tests/dashboard/` port to `tests/services/` with import-path changes only.

---
### Task S1: Service-layer extraction

**Files:**
- Create: `coe/services/__init__.py`, `instances.py`, `schedules.py`, `configure.py`, `schemas.py`
- Move: `coe/dashboard/fork.py` → `coe/services/fork.py`; `coe/dashboard/actions.py` → `coe/services/actions.py`
- Move tests: `tests/dashboard/test_data_loaders.py` → `tests/services/test_configure.py` (+ split), `test_fork.py` → `tests/services/test_fork.py`, `test_actions.py` → `tests/services/test_actions.py`
- Delete after move: `coe/dashboard/` (all remaining Streamlit files)

**Interfaces (produces):**
- `services.instances.list_instances(session) -> list[InstanceOut]`, `.get(session, name) -> InstanceOut | None` (includes `parent` from fork lineage join — port query verbatim from superseded plan A2)
- `services.schedules.active(session, iid) -> GanttOut | None`, `.versions(session, iid) -> list[dict]`, `.recovery_runs(session, iid) -> list[dict]` (ports the three superseded loaders verbatim)
- `services.configure.materials/workers/machines/jobs/jobs_per_day(session, iid)` (port `data.py` bodies, returning pydantic models)
- `schemas`: `InstanceOut{name, source_name, parent}`, `GanttOut{version: dict, entries: list[dict]}`, `VersionOut{id, version_number, schedule_type, solver_status, makespan, total_tardiness, rolled_back}`, `MaterialOut{sku, initial_stock, reorder_point, receipts[]}`, `MachineOut{name, status, down_since}`, `WorkerOut{name, role, availability[], absent_since}`, `JobOut{name, family, release_time, deadline, priority, status, ops}`, `ActionOk{ok: bool, detail: str}`
- `services.actions.*` and `services.fork.fork_instance` signatures unchanged from their current implementations **except**: `restore_machine` additionally raises `ValueError("no open outage window for '<machine>'")` when zero open windows exist (spec §4 requires HTTP 409 there; the superseded CLI variant silently no-op'd).

- [ ] **Step 1: Move code + fix imports**

```bash
mkdir -p coe/services tests/services
git mv coe/dashboard/fork.py coe/services/fork.py
git mv coe/dashboard/actions.py coe/services/actions.py
git mv tests/dashboard/test_fork.py tests/services/test_fork.py
git mv tests/dashboard/test_actions.py tests/services/test_actions.py
```

In moved files: change no logic; only module docstrings stay accurate (`fork.py` imports nothing from dashboard ✓; `actions.py` same ✓). Port the loaders by writing the three new service modules whose bodies are the superseded plan's `data.py` functions with return types swapped to the pydantic models above (construct models from the same dict literals). Delete `coe/dashboard/` and `tests/dashboard/` remnants (`app.py`, `pages/`, `data.py`, `rail.py`, `diff.py`, smoke test) — they are superseded.

- [ ] **Step 2: Write schemas + one exemplar service**

Create `coe/services/schemas.py`:

```python
"""API/service response models (single source for FastAPI, spec §3)."""
from pydantic import BaseModel


class InstanceOut(BaseModel):
    name: str
    source_name: str | None = None
    parent: str | None = None


class VersionOut(BaseModel):
    id: int
    version_number: int
    schedule_type: str
    solver_status: str
    makespan: int
    total_tardiness: int
    rolled_back: bool


class GanttOut(BaseModel):
    version: VersionOut
    entries: list[dict]


class MaterialOut(BaseModel):
    sku: str
    initial_stock: int
    reorder_point: int | None
    receipts: list[dict]


class MachineOut(BaseModel):
    name: str
    status: str
    down_since: int | None


class WorkerOut(BaseModel):
    name: str
    role: str | None
    availability: list[tuple[int, int]]
    absent_since: int | None


class JobOut(BaseModel):
    name: str
    family: str | None
    release_time: int
    deadline: int | None
    priority: int
    status: str
    ops: int


class ActionOk(BaseModel):
    ok: bool
    detail: str
```

Create `coe/services/configure.py` porting each superseded `data.py` loader; exemplar pattern:

```python
from sqlalchemy.orm import Session

from coe.services.schemas import JobOut


def jobs(session: Session, instance_id: int) -> list[JobOut]:
    """Port of superseded data.jobs_overview — identical query."""
    from sqlalchemy import func

    from coe.db.models.fjsp import Job, JobFamily, Operation

    op_counts = dict(
        session.query(Operation.job_id, func.count(Operation.id))
        .filter(Operation.instance_id == instance_id)
        .group_by(Operation.job_id).all())
    families = dict(session.query(JobFamily.id, JobFamily.name)
                    .filter(JobFamily.instance_id == instance_id).all())
    rows = (session.query(Job)
            .filter(Job.instance_id == instance_id)
            .order_by(Job.name.asc()).all())
    return [JobOut(name=j.name,
                   family=families.get(j.family_id),
                   release_time=j.release_time, deadline=j.deadline,
                   priority=j.priority, status=j.status,
                   ops=op_counts.get(j.id, 0)) for j in rows]
```

(`materials`, `workers`, `machines`, `jobs_per_day` follow identically from their superseded bodies.) `schedules.py` ports the two `active_schedule` VIEW queries verbatim, constructing `GanttOut(version=VersionOut(**ver), entries=[...])`. `instances.py` ports the lineage LEFT-JOIN query.

- [ ] **Step 3: Port tests**

Move loader tests into `tests/services/test_configure.py` + `test_schedules.py` with only these deltas: import path `coe.dashboard.data` → `coe.services.{configure,schedules,instances}`; assertions on model attributes instead of dicts where the model differs (`job.family` not `[...]`); keep every ORDER BY/count assertion. Add `tests/services/__init__.py` empty. Run:

```bash
uv run pytest tests/services -q
```

Expected: all ported tests PASS (same count as superseded suite minus streamlit-only files).

- [ ] **Step 4: Quick gate + commit**

```bash
uv run pytest -m "not mqtt and not slow" -q
git add -A coe/services tests/services coe/dashboard tests/dashboard
git commit -m "feat(services): extract shared layer from streamlit dashboard"
```

### Task S2: FastAPI app + read routers

**Files:**
- Create: `coe/api/__init__.py`, `coe/api/app.py`, `coe/api/routers/{__init__,instances,schedules,configure,benchmarks}.py`
- Test: `tests/api/test_reads.py`

**Interfaces:** `create_app() -> FastAPI`; routes under `/api` per spec §4 read rows.

- [ ] **Step 1: Write failing router tests**

Create `tests/api/test_reads.py`:

```python
import pytest

pytestmark = pytest.mark.db


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from coe.api.app import create_app

    return TestClient(create_app())


def test_list_instances_includes_fork_parent(client, session, mini_factory):
    from coe.services.fork import fork_instance

    fork = fork_instance(session, mini_factory)
    r = client.get("/api/instances")
    assert r.status_code == 200
    names = {i["name"]: i for i in r.json()}
    assert names[fork.name]["parent"] == mini_factory.name


def test_gantt_404_when_no_active_schedule(client, clean_db):
    r = client.get("/api/schedules/999999/active")
    assert r.status_code == 404


def test_configure_domains_shape(clean_db, session, demo_scenario, client):
    for dom in ["materials", "machines", "workers", "jobs"]:
        r = client.get(f"/api/configure/{dom}",
                       params={"instance": "factory_demo_01"})
        assert r.status_code == 200 and isinstance(r.json(), list)
    r = client.get("/api/configure/jobs-per-day",
                   params={"instance": "factory_demo_01"})
    assert r.status_code == 200 and isinstance(r.json(), dict)
```

(`mini_factory` fixture: copy from superseded plan Task A3 Step 1 — identical body.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_reads.py -q`
Expected: FAIL — `ModuleNotFoundError: coe.api`.

- [ ] **Step 3: Implement app + routers**

`coe/api/app.py`:

```python
"""FastAPI application factory (spec §3)."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="COE Recovery Cockpit API", docs_url="/api/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"], allow_headers=["*"])
    from coe.api.routers import benchmarks, configure, instances, runs, schedules

    for r in (instances, schedules, configure, runs, benchmarks):
        app.include_router(r.router, prefix="/api")
    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    from pathlib import Path

    dist = Path(__file__).parents[2] / "frontend" / "dist"
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=dist, html=True),
                  name="spa")
```

Exemplar router `routers/configure.py`:

```python
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session

from coe.db.session import session_scope

router = APIRouter()


def _with_session(fn, *args, **kwargs):
    with session_scope() as session:
        return fn(session, *args, **kwargs)


def _iid(session: Session, instance: str) -> int:
    from sqlalchemy import text

    row = session.execute(text("SELECT id FROM instances WHERE name=:n"),
                          {"n": instance}).first()
    if row is None:
        raise HTTPException(404, f"unknown instance '{instance}'")
    return row.id


@router.get("/configure/{domain}")
def read_domain(domain: str, instance: str = Query(...)):
    from coe.services import configure

    fn = {"materials": configure.materials, "machines": configure.machines,
          "workers": configure.workers, "jobs": configure.jobs,
          "jobs-per-day": configure.jobs_per_day}.get(domain)
    if fn is None:
        raise HTTPException(404, f"unknown domain '{domain}'")

    def call(session: Session):
        return fn(session, _iid(session, instance))

    return _with_session(call)
```

`instances.py`, `schedules.py`, `benchmarks.py` follow the same ≤10-line pattern over their services (`schedules.active` raises 404 when None; `benchmarks.fidelity` returns report-or-null as `200`). Add `runs.py` placeholder now (`router = APIRouter()`), filled by S5.

Add deps: `uv add fastapi 'uvicorn[standard]' httpx` (httpx for TestClient).

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/api/test_reads.py -q      # PASS
uv run uvicorn coe.api.app:create_app --factory --port 8000 &
curl -s localhost:8000/api/instances | head -c 200; kill %1
```

Expected: JSON array containing factory_demo_01.

- [ ] **Step 5: Commit**

```bash
git add coe/api tests/api pyproject.toml uv.lock
git commit -m "feat(api): fastapi skeleton + read routers over services"
```

### Task S3: Action endpoints

**Files:**
- Modify: `coe/api/routers/configure.py` (or new `routers/actions.py` — choose actions.py)
- Create: `coe/api/routers/actions.py`
- Test: `tests/api/test_actions_api.py`

**Interfaces:**
- `POST /api/actions/machine/down {instance, machine, at?}` → publishes MQTT FAILURE (unique message id per press), returns `ActionOk`
- `POST /api/actions/machine/restore` → closes windows via `services.actions.restore_machine`; `409` when no open window
- `POST /api/actions/worker/absent|return`, `/material/shortage`, `/job/suspend|resume` → analogous; suspend/resume map ValueError→`409`

- [ ] **Step 1: Failing tests** — `tests/api/test_actions_api.py`:

```python
import pytest

pytestmark = pytest.mark.db


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from coe.api.app import create_app

    return TestClient(create_app())


def test_suspend_then_resume_via_api(client, demo_scenario):
    from sqlalchemy import text

    from coe.db.session import session_scope

    with session_scope() as s:
        name = s.execute(text(
            "SELECT name FROM jobs WHERE instance_id=:i ORDER BY name "
            "LIMIT 1"), {"i": demo_scenario}).scalar_one()
    r = client.post("/api/actions/job/suspend",
                    json={"instance": "factory_demo_01", "job": name})
    assert r.status_code == 200 and r.json()["ok"]
    r2 = client.post("/api/actions/job/suspend",
                     json={"instance": "factory_demo_01", "job": name})
    assert r2.status_code == 409
    r3 = client.post("/api/actions/job/resume",
                     json={"instance": "factory_demo_01", "job": name})
    assert r3.json()["ok"]


def test_unknown_job_is_404(client):
    r = client.post("/api/actions/job/suspend",
                    json={"instance": "factory_demo_01", "job": "GHOST"})
    assert r.status_code == 404


@pytest.mark.mqtt
def test_machine_down_publishes(client):
    r = client.post("/api/actions/machine/down",
                    json={"instance": "factory_demo_01", "machine": "M1"})
    assert r.status_code == 200
```

- [ ] **Step 2: Implement** — `routers/actions.py`, request models inline:

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class MachineAction(BaseModel):
    instance: str
    machine: str
    at: int | None = None


class JobAction(BaseModel):
    instance: str
    job: str


@router.post("/actions/machine/down")
def machine_down(body: MachineAction):
    from coe.services import actions

    try:
        mid = actions.machine_down(body.instance, body.machine, at=body.at)
    except RuntimeError as exc:
        raise HTTPException(503, f"broker unreachable: {exc}")
    return {"ok": True, "message_id": mid}


@router.post("/actions/machine/restore")
def machine_restore(body: MachineAction):
    from coe.db.session import session_scope

    from coe.services import actions

    def call(s):
        return actions.restore_machine(s, body.instance, body.machine,
                                       at=body.at)
    try:
        with session_scope() as s:
            t = call(s)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "restored_at": t}


@router.post("/actions/job/suspend")
def job_suspend(body: JobAction):
    from coe.db.session import session_scope

    from coe.services import actions

    try:
        with session_scope() as s:
            actions.suspend_job(s, body.instance, body.job)
    except ValueError as exc:
        msg = str(exc)
        raise HTTPException(404 if "unknown" in msg else 409, msg)
    return {"ok": True}


@router.post("/actions/job/resume")
def job_resume(body: JobAction):
    from coe.db.session import session_scope

    from coe.services import actions

    try:
        with session_scope() as s:
            actions.resume_job(s, body.instance, body.job)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}
```

Worker/material endpoints mirror `machine_down` one-to-one over `services.actions.worker_absent/worker_return/material_shortage`. Register router in `app.py` loop.

- [ ] **Step 3: Run** `uv run pytest tests/api/test_actions_api.py tests/services -q` → PASS (mqtt-marked one requires broker).
- [ ] **Step 4: Commit** `feat(api): disruption action endpoints`

### Task S4: Workbook endpoints (download + validated upload)

**Files:**
- Create: `coe/api/routers/workbook.py`
- Test: `tests/api/test_workbook_api.py`

**Interfaces:**
- `GET /api/configure/workbook?instance=` → `StreamingResponse` xlsx bytes, `content-disposition` filename `<instance>.xlsx`
- `POST /api/configure/workbook?instance=` multipart file → `200 {instance: fork_name}` or `422 {"errors": [WorkbookError...]}`; `409` on name collision

- [ ] **Step 1: Failing tests**

Create `tests/api/test_workbook_api.py`:

```python
import io
import pytest
from openpyxl import Workbook, load_workbook

pytestmark = pytest.mark.db


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from coe.api.app import create_app

    return TestClient(create_app())


def test_download_round_trips_through_upload(client, demo_scenario):
    r = client.get("/api/configure/workbook",
                   params={"instance": "factory_demo_01"})
    assert r.status_code == 200
    blob = r.content
    wb = load_workbook(io.BytesIO(blob))
    assert "Jobs" in wb.sheetnames
    up = client.post("/api/configure/workbook",
                     params={"instance": "factory_demo_01"},
                     files={"file": ("wb.xlsx", blob,
                                     "application/vnd.openxmlformats-"
                                     "officedocument.spreadsheetml.sheet")})
    assert up.status_code == 200
    body = up.json()
    assert body["instance"].startswith("factory_demo_01")


def test_upload_rejection_is_422_with_rows(client, demo_scenario):
    r = client.get("/api/configure/workbook",
                   params={"instance": "factory_demo_01"})
    blob = r.content
    wb = load_workbook(io.BytesIO(blob))
    wb["Receipts"].append(("MAT-GHOST", 5, 100, "test"))
    buf = io.BytesIO()
    wb.save(buf)
    up = client.post("/api/configure/workbook",
                     params={"instance": "factory_demo_01"},
                     files={"file": ("bad.xlsx", buf.getvalue(),
                                     "application/vnd.openxmlformats-"
                                     "officedocument.spreadsheetml.sheet")})
    assert up.status_code == 422
    errs = up.json()["errors"]
    assert any("MAT-GHOST" in e["message"] for e in errs)
```

- [ ] **Step 2: Implement** — `routers/workbook.py`:

```python
from fastapi import APIRouter, HTTPException, Query, UploadFile

router = APIRouter()


@router.get("/configure/workbook")
def download(instance: str = Query(...)):
    from coe.db.session import session_scope

    from coe.parsers.workbook import export_workbook
    from coe.services.instances import get_row

    with session_scope() as session:
        row = get_row(session, instance)
        if row is None:
            raise HTTPException(404, f"unknown instance '{instance}'")
        blob = export_workbook(session, row.id)
    return _xlsx_response(blob, f"{instance}.xlsx")


@router.post("/configure/workbook")
async def upload(instance: str = Query(...), file: UploadFile = None):
    from coe.db.session import session_scope

    from coe.parsers.workbook import (
        WorkbookRejected,
        apply_workbook,
    )
    from coe.services.instances import get_row

    blob = await file.read()
    with session_scope() as session:
        parent = get_row(session, instance)
        if parent is None:
            raise HTTPException(404, f"unknown instance '{instance}'")
        try:
            fork = apply_workbook(session, parent, blob)
        except WorkbookRejected as exc:
            raise HTTPException(422, detail={"errors": exc.errors})
        except ValueError as exc:      # ForkError name collision
            raise HTTPException(409, str(exc))
    return {"ok": True, "instance": fork.name}


def _xlsx_response(blob: bytes, filename: str):
    from fastapi.responses import Response

    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument."
                   "spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```

Add `get_row(session, name) -> Instance | None` to `services/instances.py` (single query). Register router. Note: `apply_workbook` raises both `WorkbookRejected` (422) and `ForkError` subclass of ValueError (409) — the except order above matters.

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/api/test_workbook_api.py -q   # PASS
git add -A && git commit -m "feat(api): workbook download/upload with dry-run 422s"
```

### Task S5: RunManager + SSE (decision feed & MQTT rail)

**Files:**
- Create: `coe/services/recovery.py`, `coe/services/events.py`, `coe/api/routers/runs.py` (replace placeholder), `coe/api/routers/events.py`
- Modify: `coe/agents/graph.py` — add `execute_recovery_streaming` per superseded plan Task C14 **verbatim** (generator yielding `{"node":…}` then terminal dict; recording semantics identical to `execute_recovery`)
- Test: `tests/services/test_run_manager.py`, `tests/api/test_sse.py`

**Interfaces:**

```python
# coe/services/recovery.py
class EventLog:
    """Append-only event list with tailing readers."""
    def append(self, item: dict) -> None
    def snapshot(self, after: int = 0) -> tuple[list[dict], int]
    def wait(self, after: int, timeout: float) -> int   # returns new idx


class RunManager:
    def start(self, instance_name: str, narrative: str,
              runner=None) -> str     # -> stream_token (uuid4 hex);
              # runner defaults to execute_recovery_streaming; thread daemon
    def log(self, token: str) -> EventLog
```

Terminal event shape: `{"status": …, "state_summary": {...}, "run_id": int}` — the SSE layer strips the pydantic state object to a summary (committed_version_id, solver status, makespan) so JSON stays serializable.

```python
# coe/services/events.py
def start_mirror() -> None          # idempotent paho thread → EventLog
def rail_log() -> EventLog
```

- [ ] **Step 1: Failing tests**

`tests/services/test_run_manager.py`:

```python
import time

import pytest


def test_start_streams_nodes_and_terminal_recorded(clean_db, demo_scenario):
    from coe.services.recovery import RunManager

    def fake_runner(instance_name, *, trigger, narrative, **kw):
        yield {"node": "entry"}
        yield {"node": "translate"}
        yield {"status": "COMMITTED", "run_id": 777,
               "state_summary": {"solver_status": "FEASIBLE",
                                 "makespan": 42}}

    mgr = RunManager()
    token = mgr.start("factory_demo_01", "M1 down", runner=fake_runner)
    deadline = time.time() + 5
    while time.time() < deadline:
        items, idx = mgr.log(token).snapshot()
        if any("status" in i for i in items):
            break
        time.sleep(0.05)
    items, _ = mgr.log(token).snapshot()
    assert items[0] == {"node": "entry"}
    assert items[-1]["run_id"] == 777


def test_log_snapshot_after_index(clean_db):
    from coe.services.recovery import EventLog

    log = EventLog()
    for i in range(5):
        log.append({"i": i})
    items, nxt = log.snapshot(after=3)
    assert [x["i"] for x in items] == [3, 4] and nxt == 5
```

`tests/api/test_sse.py`:

```python
import pytest

pytestmark = pytest.mark.db


def test_run_events_sse_streams_and_terminates(client, demo_scenario):
    from coe.api.app import create_app
    from coe.services import recovery

    def fake(instance_name, *, trigger, narrative, **kw):
        yield {"node": "entry"}
        yield {"status": "COMMITTED", "run_id": 1,
               "state_summary": {}}

    recovery.MANAGER.start("factory_demo_01", "n", runner=fake)
    # start() returns token; capture via monkeypatched manager return
```

Correction — cleaner test design: have `RunManager.start` return the token AND tests grab it directly:

```python
def test_run_events_sse_streams_and_terminates(client, demo_scenario,
                                               monkeypatch):
    from coe.services import recovery

    def fake(instance_name, *, trigger, narrative, **kw):
        yield {"node": "entry"}
        yield {"status": "COMMITTED", "run_id": 1, "state_summary": {}}

    token = recovery.MANAGER.start("factory_demo_01", "narrative",
                                   runner=fake)
    with client.stream("GET",
                       f"/api/runs/stream/{token}/events") as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes())
    assert b'event: node' in body
    assert b'event: terminal' in body
    assert b'"run_id": 1' in body and b'COMMITTED' in body
```

(`client` fixture same as S2.)

- [ ] **Step 2: Run to verify failure** — ImportError on `coe.services.recovery`.

- [ ] **Step 3: Implement**

`coe/services/recovery.py`:

```python
"""Background recovery runs with replayable event logs (spec §3–§4)."""
import threading
import uuid
```
(then:)
```python
"""Background recovery runs with replayable event logs (spec §3–§4)."""
import threading
import uuid


class EventLog:
    """Append-only list; readers replay from any index then tail."""

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._cond = threading.Condition()

    def append(self, item: dict) -> None:
        with self._cond:
            self._items.append(item)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        return getattr(self, "_closed", False)

    def snapshot(self, after: int = 0) -> tuple[list[dict], int]:
        with self._cond:
            return self._items[after:], len(self._items)

    def wait(self, after: int, timeout: float) -> int:
        with self._cond:
            self._cond.wait_for(lambda: len(self._items) > after
                                or self.closed, timeout=timeout)
            return len(self._items)


class RunManager:
    def __init__(self) -> None:
        self._logs: dict[str, EventLog] = {}
        self._lock = threading.Lock()

    def start(self, instance_name: str, narrative: str,
              runner=None) -> str:
        if runner is None:
            from coe.agents.graph import execute_recovery_streaming

            runner = execute_recovery_streaming
        token = uuid.uuid4().hex
        log = EventLog()
        with self._lock:
            self._logs[token] = log

        def worker():
            try:
                for item in runner(instance_name, trigger="CLI",
                                   narrative=narrative):
                    if "state" in item:   # terminal: summarize pydantic
                        st = item["state"]
                        sol = getattr(st, "solution", None) or {}
                        item = {"status": item["status"],
                                "run_id": item.get("run_id"),
                                "state_summary": {
                                    "solver_status": sol.get("status"),
                                    "makespan": sol.get("makespan"),
                                    "committed_version_id":
                                        getattr(st,
                                                "committed_version_id",
                                                None)}}
                    log.append(item)
            finally:
                log.close()

        threading.Thread(target=worker, daemon=True,
                         name=f"recovery-{token[:8]}").start()
        return token

    def log(self, token: str) -> EventLog:
        return self._logs[token]


MANAGER = RunManager()
```

`coe/services/events.py`: mirror of superseded C17 `rail.py` logic but appending dicts into a module-level `EventLog` instead of deque; same passive topic `factory/+/+/+/events`; idempotent `start_mirror()`.

`routers/runs.py` final:

```python
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class RunStart(BaseModel):
    instance: str
    narrative: str


def _sse(item: dict, event: str) -> bytes:
    return f"event: {event}\ndata: {json.dumps(item)}\n\n".encode()


@router.post("/runs")
def start_run(body: RunStart):
    from coe.config import get_settings

    from coe.services import recovery

    s = get_settings()
    try:
        from coe.agents.llm_client import require_llm_config

        require_llm_config(s)
    except RuntimeError as exc:
        raise HTTPException(503, f"LLM not configured: {exc}")
    token = recovery.MANAGER.start(body.instance, body.narrative)
    return {"stream_token": token}


@router.get("/runs/stream/{token}/events")
def run_events(token: str):
    from coe.services import recovery

    try:
        log = recovery.MANAGER.log(token)
    except KeyError:
        raise HTTPException(404, "unknown stream token")

    def gen():
        idx = 0
        while True:
            log.wait(idx, timeout=15)
            items, total = log.snapshot(after=idx)
            for it in items:
                kind = ("terminal" if "status" in it else "node")
                yield _sse(it, kind)
            idx = total
            if log.closed and idx == total:
                return

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/runs")
def list_runs(instance: str):
    from coe.services.schedules import recovery_runs  # moved here in S1

    from coe.db.session import session_scope
    from coe.services.instances import get_row

    with session_scope() as session:
        row = get_row(session, instance)
        if row is None:
            raise HTTPException(404, f"unknown instance '{instance}'")
        return recovery_runs(session, row.id)
```

(`recovery_runs` loader ports into `services/schedules.py` during S1 alongside the other loaders.) `routers/events.py`: identical streaming pattern over `events.rail_log()` after `start_mirror()`. Register both routers in `app.py`.

- [ ] **Step 4: Run** `uv run pytest tests/services/test_run_manager.py tests/api/test_sse.py -q` → PASS.
- [ ] **Step 5: Full gate + commit**

```bash
uv run pytest -m "not mqtt and not slow" -q
git tag milestone-R1-backend-spine
git commit -m "feat(api): background recovery runs, SSE feeds, mqtt mirror"
```
### Task F6: Vite scaffold + design system (DESIGN.md → tokens)

**Files:**
- Create: `frontend/` scaffold via Vite react-ts template; `src/styles/tokens.css`; `DESIGN.md`
- Modify: repo `.gitignore` (+`frontend/node_modules`, `frontend/dist`, `.screenshots/`, `benchmark_report.json` already ignored?)

**Interfaces:** `npm run dev` serves :5173 proxying `/api`→`:8000`; tokens.css defines every shadcn CSS variable consumed by later tasks.

- [ ] **Step 1: Scaffold**

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install
npm install tailwindcss @tailwindcss/vite motion recharts @tanstack/react-table lucide-react
npx shadcn@latest init -y   # defaults; new-york style
npx shadcn@latest add button tabs dialog select badge card separator tooltip input
```

Vite config — dev proxy per spec §3:

```ts
// vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
```

- [ ] **Step 2: Design brief with mandatory anti-default critique**

Run the pipeline from spec §6 and record the outcome in `frontend/DESIGN.md`:

```
python3 .opencode/skills/ui-ux-pro-max/scripts/search.py \
  "industrial recovery control room annunciator" --design-system \
  -p "COE Recovery Cockpit"
```

**PINNED DIRECTION (user-selected 2026-08-26): "Annunciator Panel"** — warm charcoal hardware panels (~#1B1916, matte), enamel off-white engraved labels (~#E8E4DA), signal-phosphor amber (~#FFB000) reserved for alarms/live events, muted sage healthy-state; condensed industrial grotesque display + mono readouts; signature: header annunciator band whose tiles light per live MQTT event; Gantt styled as strip-chart recorder output. Run the uupm query above to refine exact hexes/faces WITHIN this brief — do not re-open direction choice. Then apply the `frontend-design` process + impeccable's two-altitude category-reflex check to the refined result: reject if it collapses back into "observability dark-slate+neon". DESIGN.md must contain: 4–6 named hex values, display/body/utility typefaces (not Inter), layout concept + ASCII wireframe of CockpitView, annunciator-band signature spec, and an explicit anti-reference list.

- [ ] **Step 3: Tokens**

Translate DESIGN.md into `src/styles/tokens.css` overriding shadcn variables (`--background --foreground --card --primary --accent --destructive --ring --radius …`) in OKLCH/hex per brief; import once in `main.tsx`. Verify dark/light only as far as the brief pins (a pinned single-theme is acceptable and must be stated in DESIGN.md).

- [ ] **Step 4: Visual smoke via Playwright MCP**

Start backend (`uvicorn coe.api.app:create_app --factory --port 8000`) + `npm run dev`. Navigate to :5173, screenshot default scaffold → save `.screenshots/f6-scaffold.png`. Assert: page loads with zero console errors, tokens visibly applied (bg not stock white/slate-950).

- [ ] **Step 5: Commit**

```bash
git add frontend .gitignore
git commit -m "feat(frontend): vite+shadcn scaffold, design brief + tokens"
```

### Task F7: App shell + typed API client

**Files:**
- Create: `src/App.tsx` (replace scaffold), `src/api/client.ts`, `src/components/EventRail.tsx` (static skeleton), view stubs
- Test: vitest for client error mapping

**Interfaces:**
- `client.ts`: `listInstances(): Promise<InstanceOut[]>`, typed mirrors of S2 payloads; throws `ApiError{status, detail}`; **no barrel files, direct module imports** (vercel rule bundle-2.1)
- Shell: left sidebar = instance select, fork badge, rail placeholder; main = view router (tabs state, no router lib needed at 4 fixed views)

- [ ] **Step 1: Vitest failing test** — `src/api/client.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { ApiError, listInstances } from "./client";

describe("api client", () => {
  it("maps non-2xx to ApiError with detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "unknown instance 'x'" }),
                   { status: 404 })));
    await expect(listInstances()).rejects.toMatchObject({
      status: 404, detail: "unknown instance 'x'" });
  });
  it("parses instance payload", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify([{ name: "factory_demo_01", parent: null }]),
      { status: 200 })));
    const out = await listInstances();
    expect(out[0]!.name).toBe("factory_demo_01");
  });
});
```

- [ ] **Step 2: Implement client + shell** — client exemplar:

```ts
export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? "request failed");
  }
  return res.json() as Promise<T>;
}

export interface InstanceOut { name: string; source_name?: string | null;
  parent?: string | null; }
export const listInstances = () =>
  get<InstanceOut[]>("/api/instances");
```

App shell renders sidebar (instance `<Select>` fed by `listInstances`, fork badge when `parent != null`, rail slot) + `Tabs` switching four lazy-loaded views. Persist selection in `localStorage["coe.instance"]`.

- [ ] **Step 3: Playwright checkpoint** — shell renders, selector populates from live API, zero console errors; screenshot `f7-shell.png`.
- [ ] **Step 4:** `npm run build && npx vitest run` green → commit `feat(frontend): app shell + typed api client`.

### Task F8: SVG Gantt component

**Files:**
- Create: `src/lib/format.ts`, `src/components/GanttSvg.tsx`
- Test: vitest for `format.ts`

**Interfaces:**
- `format.minuteToLabel(m: number): string` → `"HH:MM"`; `computeTicks(start, end, targetCount): number[]` (nice minute steps)
- `GanttSvg({entries, height?})` where entries = GanttOut["entries"] (machine/job/seq/start/end/worker); pure render, no fetch

Design rules (from DESIGN.md): machine lanes as horizontal bands with engraved-style lane labels; bars carry job#seq + worker glyph; hover tooltip (shadcn Tooltip) shows times/setup/status; downtime windows overlay hatched if present in payload (add `downtime` prop optional). Colors exclusively from tokens.

- [ ] **Step 1:** vitest for tick math (`computeTicks(0,1440,12)` contains 0 and 1440; monotonic; count ≤ target*2). Implement `format.ts`.
- [ ] **Step 2:** Implement `GanttSvg.tsx`: viewBox scaled to [minStart..maxEnd], lane y = index * laneHeight; bars as `<rect>` with token fills keyed by job family hash (stable colors across renders); text labels only when bar width > threshold.
- [ ] **Step 3:** Playwright checkpoint against solved factory_demo_01 baseline: all 8 machine lanes visible, bars aligned to axis ticks, tooltip on hover works, screenshot `f8-gantt.png`. Cross-check bar count equals API entry count via DOM query.
- [ ] **Step 4:** build+test green → commit `feat(frontend): svg gantt over active schedule`.

### Task F10: Runs inspector view

**Files:** Create `src/views/RunsView.tsx`, `src/components/VerdictChip.tsx`

Verdict chip mapping (spec §7): OPTIMAL/FEASIBLE → success token; INFEASIBLE → destructive; UNKNOWN → amber + caption "budget starved"; run statuses TRANSLATION_FAILED/GATE_FAILED/VERIFIER_ROLLBACK → destructive, COMMITTED → success.

Per-run expandable card: trigger badge, disruption JSON (`<pre>`), node-timing horizontal bars (Recharts) when timings present, explanation link-out. Fetch `GET /api/runs?instance=`.

Checkpoint: seed one committed + one failed run (CLI or fake row insert), verify chips/colors/timings render; screenshot `f10-runs.png`. Commit `feat(frontend): runs inspector`.

### Task F11: Benchmarks view

**Files:** Create `src/views/BenchmarksView.tsx`

Metric tiles (pass rate, exact match, threshold MET/MISS) from `GET /api/benchmarks/fidelity`; empty-state card with the CLI command to generate the report when 404/null. Placeholder panel labelled for P4/P5 comparison tables.

Checkpoint: with a real `benchmark_report.json` served, tiles match file values (assert via DOM). Commit `feat(frontend): benchmarks view` + tag `milestone-R2-read-cockpit` after full gates.

### Task F9: Configure view — tabs, tables, actions, workbook flow

**Files:**
- Create: `src/views/ConfigureView.tsx`, `src/components/WorkbookPanel.tsx`
- Modify: `src/api/client.ts` (+ configure/action/workbook wrappers)

**Interfaces:**
- client additions: `getDomain(domain, instance)`, `jobsPerDay(instance)`, `downloadWorkbook(instance)` (native link), `uploadWorkbook(instance, file)` returning `{instance}` or `ApiError` with `errors[]` on 422, `postAction(path, body)`
- WorkbookPanel: download button; file input with **dry-run-first UX** — POST upload renders 422 error rows (`[sheet #row]: message`, destructive tokens) inline without state change; 200 renders success card with created instance name + one-click instance switch
- Tabs wire S3 endpoints: Machines down/restore (confirm dialog on DOWN), Workers absent/return, Jobs suspend/resume, Materials shortage declare
- jobs-per-day keys arrive JSON-stringified ("0") — Number() before charting

Checkpoint (Playwright): poisoned upload shows error list and leaves instance unchanged (verify via GET); valid edit creates fork, selector badge updates; machine DOWN publishes (assert via listener terminal or MQTT-marked test already covered backend-side). Screenshots `.screenshots/f9-*.png`. Commit `feat(frontend): configure view with workbook flow`.

### Task F12: Cockpit chat + SSE decision feed

**Files:** Create `src/api/sse.ts`, `src/components/DecisionFeed.tsx`; modify `CockpitView`. Test: vitest for the feed reducer.

`sse.ts`:

```ts
export function connectRunEvents(
    token: string,
    onEvent: (e: { event: string; data: any }) => void): () => void {
  const es = new EventSource(`/api/runs/stream/${token}/events`);
  es.addEventListener("node",
    ev => onEvent({ event: "node", data: JSON.parse(ev.data) }));
  es.addEventListener("terminal",
    ev => onEvent({ event: "terminal", data: JSON.parse(ev.data) }));
  es.onerror = () => { /* EventSource auto-reconnects */ };
  return () => es.close();
}
```

Feed reducer vitest: node events append labeled lines in order; terminal flips done-state and stores run_id; duplicate deliveries ignored. Label map ports superseded C15 NODE_LABELS verbatim (entry→explain_node). Solve stage = indeterminate progress + "~180 s floor" caption. Chat disabled when `GET /api/health/llm` is 503 — add that health route here (reuses `require_llm_config`). On COMMITTED capture `before_entries` snapshot taken BEFORE `POST /api/runs` (per F13 contract).

Checkpoint: real LLM run — lines tick progressively through solve spinner to verdict chip summary; screenshots mid-run + terminal. Commit `feat(frontend): chat recovery + live decision feed`.

### Task F13: Schedule diff animation

**Files:** Create `src/lib/diff.ts` (+ `__tests__/diff.test.ts`), `src/components/GanttDiffOverlay.tsx`; wire CockpitView.

`diff.ts` — vitest FIRST:

```ts
export interface Entry {
  job_name: string; sequence_number: number; machine_name: string;
  start_time: number; end_time: number; worker_name?: string | null;
}
export interface Move {
  key: string;
  from: Pick<Entry, "machine_name" | "start_time" | "end_time">;
  to: Pick<Entry, "machine_name" | "start_time" | "end_time">;
}
export const keyOf = (e: Entry) => `${e.job_name}#${e.sequence_number}`;

export function computeMoves(before: Entry[], after: Entry[]): Move[] {
  const prev = new Map(before.map(e => [keyOf(e), e]));
  return after.flatMap(a => {
    const b = prev.get(keyOf(a));
    if (!b) return [];
    if (b.machine_name === a.machine_name &&
        b.start_time === a.start_time && b.end_time === a.end_time)
      return [];
    return [{ key: keyOf(a),
      from: { machine_name: b.machine_name, start_time: b.start_time,
              end_time: b.end_time },
      to: { machine_name: a.machine_name, start_time: a.start_time,
            end_time: a.end_time } }];
  });
}
```

Tests: moved op → one move with old/new geometry; identical schedules → `[]`; additions alone → `[]`.

`GanttDiffOverlay({before, after})`: renders the AFTER Gantt; moved bars are `<motion.g>` starting at FROM geometry with 0.35 opacity and springing to TO on mount (`initial={fromGeom} animate={toGeom}`), keyed `layoutId={key}`. A shadcn Slider scrubs t∈[0,1] lerping geometry for narrated walkthroughs; springs handle interruption natively. Static bars render identically to F8 GanttSvg (extract shared `barGeometry()` helper into GanttSvg and import).

Wiring contract in CockpitView: capture `activeSchedule` snapshot BEFORE posting `/api/runs`; when terminal event is COMMITTED, refetch schedule and render overlay with `computeMoves(before.entries, after.entries)` above the summary.

Checkpoint: real recovery that moves at least one op (M-down narrative guarantees it); verify ghost→solid spring visible, slider scrubs, static bars unmoved; screenshots `f13-ghost.png`/`f13-final.png`. Commit `feat(frontend): schedule diff animation`.

### Task F14: MQTT rail + polish pass + milestone close

**Files:** Create `src/components/EventRail.tsx` (replace F7 skeleton); modify `app.py` sidebar wiring; docs.

- Rail subscribes `GET /api/events/stream` via same sse.ts pattern (`event: mqtt`), keeps last 10 reversed entries, degraded badge on error state (spec §7).
- **impeccable polish gate**: run critique against every view; fix flagged items (contrast, spacing rhythm, slop tells). Verify DESIGN.md anti-references hold.
- Rewrite `docs/dashboard-demo.md` for two-terminal dev mode or single-process prod mode; README command becomes:
  `uv run python -m coe.cli dashboard   # builds+serves frontend/dist on :8000`
- CLI `dashboard` implementation: check `frontend/dist`; if missing print build hint; launch uvicorn programmatically.
- Full gates: `uv run pytest -q` AND `npm run build && npx vitest run`.
- Commit `feat(frontend): live rail, polish pass, demo docs` + tag `milestone-R4-live-cockpit`.
