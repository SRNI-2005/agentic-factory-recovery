# Streamlit Cockpit Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **SUPERSEDED 2026-08-26** by `2026-08-26-react-cockpit-implementation-plan.md`. Tasks A3 (fork), B7–B9 (workbook), C14 (streaming), and diff logic survive as backend tasks; all Streamlit render-layer tasks are replaced.

**Goal:** A Streamlit multipage cockpit over the existing COE recovery system — read-only views, workbook-driven configuration edits on auto-forked instances, event buttons publishing real MQTT disruptions, and a chat-driven LangGraph recovery with live decision feed.

**Architecture:** Pure-Python Streamlit app importing `coe.*` services directly (no API layer). Views are read-only SQLAlchemy queries scoped by sidebar-selected `instance_id`. Mutations flow through three existing mechanisms only: MQTT ingestion (events), `coe/parsers/workbook.py` importer (bulk config → derived instance), and `execute_recovery()` / its streaming variant (chat). Spec: `docs/superpowers/specs/2026-08-25-dashboard-streamlit-cockpit-design.md`.

**Tech Stack:** Python 3.12, Streamlit + plotly (new), SQLAlchemy 2.0, openpyxl (already present), paho-mqtt, LangGraph, pytest.

## Global Constraints

- Use `uv` exclusively; never pip, never system Python.
- Alembic is authoritative DDL — `create_all` is forbidden. This plan adds **zero migrations** (spec §1: no P1–P3 schema changes beyond additive provenance writes).
- Every dashboard query carries explicit `ORDER BY` (stable screenshots) and an `instance_id` filter (FK discipline).
- All times are integer minutes.
- Loopback-only, no auth (dev posture).
- Solver contract frozen at Phase 2 HEAD: statuses OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN rendered verbatim; UNKNOWN is budget-starved, never material-conflict; recovery solves floor at 180s (`_recovery_floor`, `coe/cli.py:79`).
- `factory_demo_01` is never mutated by any code path in this plan.
- `recovery_runs.trigger` CHECK admits only `'CLI' | 'MQTT'` — chat-initiated recoveries record as `'CLI'`.
- Forks copy all instance-scoped rows except `telemetry_events`.
- TDD for all logic; commit after every task; quick gate between milestones: `uv run pytest -m "not mqtt and not slow"`.
- Docker services required before db/mqtt tests: `docker compose up -d`.

## File Structure

```text
coe/dashboard/
├── __init__.py          # empty package marker
├── app.py               # st.set_page_config, sidebar instance selector, fork badge, page router
├── data.py              # pure query functions returning plain dicts/lists (all ORDER BY'd)
├── actions.py           # event wrappers: machine toggle, absence, suspend, chat recovery
├── fork.py              # fork_instance(session, source_instance, new_name) transactional copy
└── pages/
    ├── cockpit.py       # chat input, decision feed, schedule animation
    ├── configure.py     # read-only tabs + workbook download/upload controls
    ├── runs.py          # recovery run inspector
    └── benchmarks.py    # fidelity report charts (grows with P4/P5)

coe/parsers/workbook.py  # fourth importer: SHEETS schema constants, export(),
                         # validate_workbook() -> list[WorkbookError], apply_workbook()

data/templates/factory_workbook.xlsx   # generated once by Task B6 script, committed

tests/dashboard/
├── conftest.py          # dashboard-local fixtures if needed (none expected initially)
├── test_data_loaders.py
├── test_fork.py
├── test_actions.py
└── test_app_smoke.py

tests/parsers/test_workbook.py

Modified existing files:
├── pyproject.toml                    # +streamlit, +plotly
├── coe/cli.py                        # +dashboard group, +import workbook subcommand,
│                                     #   +template export command
└── coe/agents/graph.py               # +execute_recovery_streaming (additive sibling of
                                      #   execute_recovery, same recording semantics)
```

Responsibility boundaries: `data.py` never mutates; `actions.py` never renders; pages never touch models directly (they compose `data.py` + `actions.py`); `fork.py` knows nothing about Streamlit; `workbook.py` knows nothing about Streamlit or forks' UI (it receives explicit target names).

---

### Task A1: Dependencies, CLI launch command, app shell

**Files:**
- Modify: `pyproject.toml:6-18`
- Create: `coe/dashboard/__init__.py`
- Create: `coe/dashboard/app.py`
- Modify: `coe/cli.py` (add `dashboard` subcommand in `build_parser()`, dispatcher in `main()`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `uv run python -m coe.cli dashboard [--page NAME]` launches Streamlit on loopback; `coe.dashboard.app.run()` importable entrypoint later tasks' pages register against.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, extend the dependencies array (openpyxl is already present):

```toml
dependencies = [
    "sqlalchemy>=2.0",
    "alembic",
    "psycopg[binary]>=3.0",
    "paho-mqtt",
    "pydantic-settings",
    "openpyxl",
    "ortools>=9.10",
    "langgraph>=1.2.11",
    "langchain-core>=1.6.0",
    "langchain-openai>=1.6.0",
    "langchain-google-genai>=4.3.5",
    "streamlit>=1.45",
    "plotly>=5.24",
]
```

Run: `uv sync && uv run streamlit version`
Expected: streamlit version prints, no dependency resolution errors.

- [ ] **Step 2: Create the app shell**

Create `coe/dashboard/__init__.py` (empty file).

Create `coe/dashboard/app.py`:

```python
"""Streamlit cockpit entrypoint (spec: dashboard design §3)."""
import importlib


def main() -> None:
    import streamlit as st

    from coe.dashboard.data import list_instances

    st.set_page_config(
        page_title="COE Factory Recovery Cockpit",
        page_icon="🏭",
        layout="wide",
    )

    pages = {
        "Cockpit": "pages/cockpit.py",
        "Configure": "pages/configure.py",
        "Runs": "pages/runs.py",
        "Benchmarks": "pages/benchmarks.py",
    }
    choice = st.sidebar.radio("Pages", list(pages), key="nav")
    slug = choice.lower()
    try:
        module = importlib.import_module(f"coe.dashboard.pages.{slug}")
    except ModuleNotFoundError:
        # Milestone A1 ships before page modules exist (Tasks A4-A6)
        st.info(f"{choice} page arrives in a later task.")
        return
    st.title(choice)
    module.render()

    from coe.db.session import session_scope

    with session_scope() as session:
        instances = list_instances(session)
    if not instances:
        st.warning("No instances found. Build one: "
                   "`uv run python -m coe.cli import mk01 && "
                   "uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42`")
        st.stop()
    names = [i["name"] for i in instances]
    default = "factory_demo_01" if "factory_demo_01" in names else names[0]
    selected = st.sidebar.selectbox("Instance", names,
                                    index=names.index(default),
                                    key="instance_name")
    st.session_state["instance"] = selected
    parent = next(i["parent"] for i in instances if i["name"] == selected)
    if parent:
        st.sidebar.caption(f"fork of **{parent}**")


if __name__ == "__main__":
    main()
```

Note: page routing here is deliberately simple (radio + `st.session_state`) rather than Streamlit's `pages/` auto-discovery, because the sidebar must hold the instance selector above navigation. Pages themselves are plain modules imported by their script path in Task A4–A6 wiring; `st.session_state["instance"]` is the single source of instance scope.

- [ ] **Step 3: Wire the CLI command**

In `coe/cli.py`, inside `build_parser()` after the last existing subparser block, add:

```python
    dash = sub.add_parser("dashboard", help="launch the Streamlit cockpit")
    dash.add_argument("--port", type=int, default=8501)
```

In `main()`, add to the dispatch chain (matching the existing elif style):

```python
    elif args.group == "dashboard":
        from streamlit.web import cli as stcli
        import sys

        sys.argv = ["streamlit", "run", "coe/dashboard/app.py",
                    "--server.port", str(args.port),
                    "--server.address", "127.0.0.1",
                    "--server.headless", "true"]
        sys.exit(stcli.main())
```

- [ ] **Step 4: Manual verification**

Build data if the DB is empty:

```bash
docker compose up -d
uv run python -m coe.cli import mk01
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
uv run python -m coe.cli dashboard
```

Expected: browser tab opens on http://127.0.0.1:8501 showing sidebar with instance selector (`factory_demo_01` preselected), empty main pane. No traceback.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock coe/dashboard/__init__.py coe/dashboard/app.py coe/cli.py
git commit -m "feat(dash): streamlit shell + cli dashboard command"
```

### Task A2: data.py read-only loaders

**Files:**
- Create: `coe/dashboard/data.py`
- Test: `tests/dashboard/test_data_loaders.py`

**Interfaces:**
- Consumes: SQLAlchemy models under `coe/db/models/`; the `active_schedule` database view; `ScheduleVersion`, `ScheduleEntry`, `Material`, `MaterialReceipt`, `Worker`, `WorkerRole`, `WorkerAvailabilityWindow`, `WorkerAbsenceWindow`, `Machine`, `MachineDowntimeWindow`, `Job`, `JobFamily`, `Operation`, `RecoveryRun`.
- Produces (all return plain dicts/lists, all queries ordered):
  - `list_instances(session) -> list[dict]` keys: `name, source_name, parent`
  - `active_schedule(session, instance_id) -> dict | None` keys: `version{...}, entries[list[dict]]`; None when no active schedule
  - `schedule_versions(session, instance_id) -> list[dict]`
  - `materials_overview(session, instance_id) -> list[dict]` (material + its receipts)
  - `workers_overview(session, instance_id) -> list[dict]` (worker, role, availability windows, absence windows)
  - `machines_overview(session, instance_id) -> list[dict]` (machine, status, open downtime window)
  - `jobs_overview(session, instance_id) -> list[dict]` (job fields + family + op count)
  - `jobs_per_day(session, instance_id, day_length=1440) -> dict[int, list[str]]` (deadline day → job names)
  - `recovery_runs(session, instance_id) -> list[dict]`
  - `fidelity_report(path: Path) -> dict | None`

- [ ] **Step 1: Write failing tests**

Create `tests/dashboard/test_data_loaders.py`:

```python
import pytest

from coe.db.models.downtime import MachineDowntimeWindow, TelemetryEvent
from coe.db.models.fjsp import Job, Machine, Operation
from coe.db.models.materials import Material, MaterialReceipt
from coe.db.models.provenance import Instance
from coe.db.models.recovery import RecoveryRun
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
from coe.dashboard import data

pytestmark = pytest.mark.db


def _mk_version(session, inst_id, number, makespan=100):
    """ScheduleVersion with every NOT NULL column satisfied."""
    return ScheduleVersion(
        instance_id=inst_id, version_number=number,
        schedule_type="BASELINE", solver_status="FEASIBLE",
        objective_value=float(makespan), makespan=makespan,
        total_tardiness=0, alpha_weight=0.5, beta_weight=0.5,
        time_limit_seconds=30, solve_duration_seconds=0.42,
        failed_machine_ids=None, parent_version_id=None,
        rolled_back=False, payload_hash="0" * 64, payload_json={})


def _mk_instance(session, name="dash-fixture"):
    inst = Instance(name=name, source_name="test", source_version="t",
                    source_license="test")
    session.add(inst)
    session.flush()
    return inst


def test_list_instances_orders_and_reports_parent(clean_db, session):
    _mk_instance(session)
    session.add(Instance(name="child@deadbeef", source_name="test",
                         source_version="t", source_license="test",
                         source_checksum="deadbeefcafe"))
    rows = data.list_instances(session)
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)
    child = next(r for r in rows if r["name"].startswith("child"))
    # parent linkage comes from provenance lineage written by fork (Task A3);
    # before any fork exists the column is simply None
    assert child["parent"] is None


def test_active_schedule_none_when_empty(clean_db, session):
    inst = _mk_instance(session)
    assert data.active_schedule(session, inst.id) is None


def test_active_schedule_reads_view(clean_db, session):
    inst = _mk_instance(session)
    mach = Machine(instance_id=inst.id, name="M1")
    job = Job(instance_id=inst.id, name="J1")
    session.add_all([mach, job])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=job.id, sequence_number=1)
    session.add(op)
    session.flush()
    ver = _mk_version(session, inst.id, 1, makespan=100)
    session.flush()
    session.add(ScheduleEntry(
        instance_id=inst.id, version_id=ver.id, operation_id=op.id,
        machine_id=mach.id, worker_id=None, start_time=10, end_time=30,
        processing_time=20, setup_time=0, status="SCHEDULED",
        is_frozen=False))
    ver2 = _mk_version(session, inst.id, 2, makespan=90)
    session.flush()
    session.add(ScheduleEntry(
        instance_id=inst.id, version_id=ver2.id, operation_id=op.id,
        machine_id=mach.id, worker_id=None, start_time=5, end_time=25,
        processing_time=20, setup_time=0, status="SCHEDULED",
        is_frozen=False))
    session.flush()

    snap = data.active_schedule(session, inst.id)
    assert snap["version"]["version_number"] == 2
    assert len(snap["entries"]) == 1
    assert snap["entries"][0]["start_time"] == 5


def test_jobs_per_day_groups_by_deadline(clean_db, session):
    inst = _mk_instance(session)
    session.add_all([
        Job(instance_id=inst.id, name="J1", release_time=0, deadline=1400,
            priority=1, status="PENDING"),
        Job(instance_id=inst.id, name="J2", release_time=0, deadline=1500,
            priority=1, status="PENDING"),
    ])
    grouped = data.jobs_per_day(session, inst.id)
    assert grouped == {0: ["J1"], 1: ["J2"]}  # deadline//1440
```

Add the missing `session` fixture to `tests/conftest.py` (function-scoped, wraps `clean_db`):

```python
@pytest.fixture
def session(clean_db):
    from coe.db.session import session_scope

    with session_scope() as s:
        yield s
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dashboard/test_data_loaders.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coe.dashboard.data'` (or ImportError of `data`).

- [ ] **Step 3: Implement data.py**

Create `coe/dashboard/data.py`:

```python
"""Read-only loaders for the cockpit. Every query: instance-scoped + ORDER BY."""
from pathlib import Path
import json

from sqlalchemy import text
from sqlalchemy.orm import Session


def list_instances(session: Session) -> list[dict]:
    """Fork lineage per Task A3's provenance row:
    ScenarioSource(scenario_id=fork, source_instance_id=parent,
    contribution_type='fork')."""
    rows = session.execute(text(
        "SELECT i.name, i.source_name, p.name AS parent "
        "FROM instances i "
        "LEFT JOIN scenario_sources ss "
        "  ON ss.scenario_id = i.id AND ss.contribution_type = 'fork' "
        "LEFT JOIN instances p ON p.id = ss.source_instance_id "
        "ORDER BY i.name ASC"
    )).all()
    return [{"name": r.name, "source_name": r.source_name,
             "parent": r.parent} for r in rows]


def active_schedule(session: Session, instance_id: int) -> dict | None:
    """Canonical Gantt source: the active_schedule VIEW (spec §5). Never re-derived."""
    ver = session.execute(text(
        "SELECT sv.* FROM active_schedule asev "
        "JOIN schedule_versions sv ON sv.id = asev.version_id "
        "WHERE asev.instance_id = :iid LIMIT 1"
    ), {"iid": instance_id}).mappings().first()
    if ver is None:
        return None
    entries = session.execute(text(
        "SELECT se.*, m.name AS machine_name, j.name AS job_name, "
        "       o.sequence_number, w.name AS worker_name "
        "FROM active_schedule asev "
        "JOIN schedule_entries se ON se.id = asev.id "
        "JOIN machines m ON m.id = se.machine_id "
        "JOIN operations o ON o.id = se.operation_id "
        "JOIN jobs j ON j.id = o.job_id "
        "LEFT JOIN workers w ON w.id = se.worker_id "
        "WHERE se.instance_id = :iid "
        "ORDER BY m.name ASC, se.start_time ASC, j.name ASC, "
        "         o.sequence_number ASC"
    ), {"iid": instance_id}).mappings().all()
    return {"version": dict(ver), "entries": [dict(e) for e in entries]}
```

Remaining loaders follow the identical pattern (model queries with `.order_by(...)`):

```python
def schedule_versions(session: Session, instance_id: int) -> list[dict]:
    from coe.db.models.schedule import ScheduleVersion

    rows = (session.query(ScheduleVersion)
            .filter(ScheduleVersion.instance_id == instance_id)
            .order_by(ScheduleVersion.version_number.desc()).all())
    return [{"id": r.id, "version_number": r.version_number,
             "schedule_type": r.schedule_type,
             "solver_status": r.solver_status, "makespan": r.makespan,
             "total_tardiness": r.total_tardiness,
             "rolled_back": r.rolled_back} for r in rows]


def materials_overview(session: Session, instance_id: int) -> list[dict]:
    from coe.db.models.materials import Material, MaterialReceipt

    out = []
    mats = (session.query(Material)
            .filter(Material.instance_id == instance_id)
            .order_by(Material.sku.asc()).all())
    for m in mats:
        receipts = (session.query(MaterialReceipt)
                    .filter(MaterialReceipt.instance_id == instance_id,
                            MaterialReceipt.material_id == m.id)
                    .order_by(MaterialReceipt.available_at.asc()).all())
        out.append({"sku": m.sku, "initial_stock": m.initial_stock,
                    "reorder_point": m.reorder_point,
                    "receipts": [{"quantity": r.quantity,
                                  "available_at": r.available_at,
                                  "source": r.source} for r in receipts]})
    return out


def machines_overview(session: Session, instance_id: int) -> list[dict]:
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine

    out = []
    machines = (session.query(Machine)
                .filter(Machine.instance_id == instance_id)
                .order_by(Machine.name.asc()).all())
    for m in machines:
        open_win = (session.query(MachineDowntimeWindow)
                    .filter(MachineDowntimeWindow.instance_id == instance_id,
                            MachineDowntimeWindow.machine_id == m.id,
                            MachineDowntimeWindow.downtime_until.is_(None))
                    .order_by(MachineDowntimeWindow.downtime_from.asc())
                    .first())
        out.append({"name": m.name, "status": m.status,
                    "down_since": open_win.downtime_from if open_win else None})
    return out


def workers_overview(session: Session, instance_id: int) -> list[dict]:
    from coe.db.models.workers import (
        Worker,
        WorkerAvailabilityWindow,
        WorkerRole,
    )
    from coe.db.models.downtime import WorkerAbsenceWindow

    out = []
    workers = (session.query(Worker)
               .filter(Worker.instance_id == instance_id)
               .order_by(Worker.name.asc()).all())
    roles = dict(session.query(WorkerRole.id, WorkerRole.role_name)
                 .filter(WorkerRole.instance_id == instance_id).all())
    for w in workers:
        avail = (session.query(WorkerAvailabilityWindow)
                 .filter(WorkerAvailabilityWindow.instance_id == instance_id,
                         WorkerAvailabilityWindow.worker_id == w.id)
                 .order_by(WorkerAvailabilityWindow.available_from.asc()).all())
        absences = (session.query(WorkerAbsenceWindow)
                    .filter(WorkerAbsenceWindow.instance_id == instance_id,
                            WorkerAbsenceWindow.worker_id == w.id,
                            WorkerAbsenceWindow.absence_until.is_(None))
                    .order_by(WorkerAbsenceWindow.absence_from.asc()).all())
        out.append({"name": w.name, "role": roles.get(w.role_id),
                    "availability": [(a.available_from, a.available_until)
                                     for a in avail],
                    "absent_since": absences[0].absence_from
                    if absences else None})
    return out


def jobs_overview(session: Session, instance_id: int) -> list[dict]:
    from sqlalchemy import func

    from coe.db.models.fjsp import Job, JobFamily, Operation

    op_counts = dict(
        session.query(Operation.job_id, func.count(Operation.id))
        .filter(Operation.instance_id == instance_id)
        .group_by(Operation.job_id).all())
    families = dict(session.query(JobFamily.id, JobFamily.name)
                    .filter(JobFamily.instance_id == instance_id).all())
    jobs = (session.query(Job)
            .filter(Job.instance_id == instance_id)
            .order_by(Job.name.asc()).all())
    return [{"name": j.name, "family": families.get(j.family_id),
             "release_time": j.release_time, "deadline": j.deadline,
             "priority": j.priority, "status": j.status,
             "ops": op_counts.get(j.id, 0)} for j in jobs]


def jobs_per_day(session: Session, instance_id: int,
                 day_length: int = 1440) -> dict[int, list[str]]:
    from coe.db.models.fjsp import Job

    jobs = (session.query(Job)
            .filter(Job.instance_id == instance_id,
                    Job.deadline.isnot(None))
            .order_by(Job.name.asc()).all())
    grouped: dict[int, list[str]] = {}
    for j in sorted(jobs, key=lambda x: x.name):
        grouped.setdefault(j.deadline // day_length, []).append(j.name)
    return dict(sorted(grouped.items()))


def recovery_runs(session: Session, instance_id: int) -> list[dict]:
    from coe.db.models.recovery import RecoveryRun

    runs = (session.query(RecoveryRun)
            .filter(RecoveryRun.instance_id == instance_id)
            .order_by(RecoveryRun.started_at.desc()).all())
    return [{"id": r.id, "trigger": r.trigger, "status": r.status,
             "started_at": r.started_at, "finished_at": r.finished_at,
             "disruption_record_json": r.disruption_record_json,
             "node_timings_json": r.node_timings_json,
             "quantum_shadow_json": r.quantum_shadow_json,
             "final_status_version_id": r.final_status_version_id}
            for r in runs]


def fidelity_report(path: Path = Path("benchmark_report.json")) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())
```

Column-name caveat for the implementer: `WorkerAvailabilityWindow.start_minute/end_minute`, `WorkerAbsenceWindow.absent_from/absent_until`, and `ScheduleVersion` fields (`solver_status`, `makespan`, `total_tardiness`, `rolled_back`, `solve_duration_seconds`) must be verified against the models before implementing — open the model file and copy names exactly; adjust this plan's code if a name differs.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dashboard/test_data_loaders.py -v`
Expected: 4 PASS. If a column name mismatch raises AttributeError, fix the loader to match the model, not the test fixture intent.

- [ ] **Step 5: Commit**

```bash
git add coe/dashboard/data.py tests/dashboard/test_data_loaders.py tests/conftest.py
git commit -m "feat(dash): read-only instance/schedule/materials/workers/jobs/runs loaders"
```

### Task A3: fork.py instance fork service

**Files:**
- Create: `coe/dashboard/fork.py`
- Test: `tests/dashboard/test_fork.py`

**Interfaces:**
- Consumes: all `coe/db/models/*` tables; `ScenarioSource` for lineage.
- Produces: `fork_instance(session, source: Instance, new_name: str | None = None) -> Instance` — raises `ForkError` on name collision. Copies every domain table with fresh PKs and remapped FKs; skips `telemetry_events`, `recovery_runs`, `recovery_proposals`; `ScheduleVersion.parent_version_id` is set to None in copies (cross-instance version lineage is intentionally not preserved; the provenance row carries fork lineage instead); `failed_machine_ids` JSONB set to None (contains parent-scoped machine ids).

- [ ] **Step 1: Write failing tests**

Create `tests/dashboard/test_fork.py`:

```python
from datetime import datetime

import pytest

from coe.db.models.downtime import TelemetryEvent
from coe.db.models.recovery import RecoveryRun
from coe.dashboard.fork import ForkError, fork_instance

pytestmark = pytest.mark.db


@pytest.fixture
def mini_factory(session):
    """Hermetic source instance exercising every copied table."""
    from coe.db.models.downtime import (MachineDowntimeWindow,
                                         TelemetryEvent,
                                         WorkerAbsenceWindow)
    from coe.db.models.fjsp import (
        Job, JobFamily, Machine, MachineCapability, Operation,
        OperationMachineAlternative, SetupTime)
    from coe.db.models.materials import Material, MaterialReceipt
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import RecoveryRun
    from coe.db.models.schedule import ScheduleEntry
    from coe.db.models.workers import (
        OperationMachineWorkerTime, Worker, WorkerAvailabilityWindow,
        WorkerRole)

    inst = Instance(name="fork-src", source_name="test",
                    source_version="t", source_license="test")
    session.add(inst)
    session.flush()
    fam = JobFamily(instance_id=inst.id, name="fam1")
    m1 = Machine(instance_id=inst.id, name="M1")
    m2 = Machine(instance_id=inst.id, name="M2")
    role = WorkerRole(instance_id=inst.id, role_name="operator")
    job = Job(instance_id=inst.id, name="J1", release_time=0,
              deadline=100, priority=1, status="PENDING")
    session.add_all([fam, m1, m2, role, job])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=job.id, sequence_number=1,
                   required_role_id=role.id)
    w = Worker(instance_id=inst.id, name="W1", role_id=role.id,
               status="AVAILABLE")
    session.add_all([op, w])
    session.flush()
    session.add(MachineCapability(instance_id=inst.id, machine_id=m1.id,
                                  capability_code="cnc", source="test"))
    session.add(OperationMachineAlternative(
        instance_id=inst.id, operation_id=op.id, machine_id=m1.id,
        processing_time=10))
    session.add(OperationMachineWorkerTime(
        instance_id=inst.id, operation_id=op.id, machine_id=m1.id,
        worker_id=w.id, processing_time=8))
    session.add(SetupTime(instance_id=inst.id, machine_id=m1.id,
                          from_family_id=None, to_family_id=fam.id,
                          setup_duration=5, source="test"))
    mat = Material(instance_id=inst.id, sku="MAT-1", initial_stock=50,
                   reorder_point=5)
    session.add(mat)
    session.flush()
    session.add(MaterialReceipt(instance_id=inst.id, material_id=mat.id,
                                quantity=10, available_at=200,
                                source="test"))
    session.add(OperationBom(instance_id=inst.id, operation_id=op.id,
                             material_id=mat.id, quantity_required=2))
    session.add(MachineDowntimeWindow(instance_id=inst.id, machine_id=m1.id,
                                      downtime_from=0, downtime_until=None,
                                      reason="test"))
    session.add(WorkerAvailabilityWindow(instance_id=inst.id, worker_id=w.id,
                                         available_from=0,
                                         available_until=480,
                                         source_pattern="shift"))
    session.add(WorkerAbsenceWindow(instance_id=inst.id, worker_id=w.id,
                                    absence_from=10, absence_until=None,
                                    reason="test"))
    # system-owned row that must NOT be copied (columns per downtime.py:16-53;
    # machine_id is an integer FK, exactly_one_resource CHECK satisfied):
    session.add(TelemetryEvent(
        occurred_at=1, instance_id=inst.id, message_id="t-1",
        machine_id=m1.id, worker_id=None, material_id=None,
        resource_kind="MACHINE", event_type="FAILURE", received_at=2,
        payload_json={}))
    session.flush()
    return inst


def test_fork_copies_all_domain_tables_with_matching_counts(clean_db, session, mini_factory):
    from sqlalchemy import inspect as sa_inspect

    from coe.db.models.downtime import (MachineDowntimeWindow,
                                         WorkerAbsenceWindow)
    from coe.db.models.fjsp import (
        Job, JobFamily, Machine, MachineCapability, Operation,
        OperationMachineAlternative, SetupTime)
    from coe.db.models.materials import Material, MaterialReceipt
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import (
        OperationMachineWorkerTime, Worker, WorkerAvailabilityWindow,
        WorkerRole)

    fork = fork_instance(session, mini_factory)
    for Model in [JobFamily, Machine, MachineCapability, WorkerRole, Worker,
                  Job, Operation, OperationMachineAlternative,
                  OperationMachineWorkerTime, SetupTime, Material,
                  MaterialReceipt, OperationBom, MachineDowntimeWindow,
                  WorkerAvailabilityWindow, WorkerAbsenceWindow]:
        src = (session.query(Model)
               .filter(Model.instance_id == mini_factory.id).count())
        dst = (session.query(Model)
               .filter(Model.instance_id == fork.id).count())
        assert src == dst, Model.__name__


def test_fork_skips_telemetry_and_recovery_history(clean_db, session, mini_factory):
    from coe.db.models.downtime import TelemetryEvent
    from coe.db.models.recovery import RecoveryRun

    session.add(RecoveryRun(
        instance_id=mini_factory.id, trigger="CLI", status="COMMITTED",
        disruption_record_json={}, started_at=datetime(2026, 1, 1),
        finished_at=None))
    session.flush()
    fork = fork_instance(session, mini_factory)
    assert session.query(TelemetryEvent).filter(
        TelemetryEvent.instance_id == fork.id).count() == 0
    assert session.query(RecoveryRun).filter(
        RecoveryRun.instance_id == fork.id).count() == 0


def test_fork_remaps_schedule_foreign_keys(clean_db, session,
                                           demo_scenario, mini_factory):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.schedule import ScheduleEntry

    mach = (session.query(Machine)
            .filter(Machine.instance_id == mini_factory.id).first())
    op = (session.query(Operation)
          .filter(Operation.instance_id == mini_factory.id).first())
    ver = _mk_version(session, mini_factory.id, 1)  # local helper below
    entry = ScheduleEntry(instance_id=mini_factory.id, version_id=ver.id,
                          operation_id=op.id, machine_id=mach.id,
                          worker_id=None, start_time=0, end_time=10,
                          processing_time=10, setup_time=0,
                          status="SCHEDULED", is_frozen=False)
    session.add(entry)
    session.flush()

    fork = fork_instance(session, mini_factory)
    f_entry = (session.query(ScheduleEntry)
               .filter(ScheduleEntry.instance_id == fork.id).one())
    assert f_entry.id != entry.id
    f_op = session.query(Operation).filter(
        Operation.instance_id == fork.id,
        Operation.job_id.in_(
            session.query(Job.id).filter(Job.instance_id == fork.id))
    ).one()
    assert f_entry.operation_id == f_op.id
    assert f_entry.machine_id != mach.id


def test_fork_records_lineage(clean_db, session, mini_factory):
    from coe.db.models.provenance import ScenarioSource

    fork = fork_instance(session, mini_factory)
    ss = (session.query(ScenarioSource)
          .filter(ScenarioSource.scenario_id == fork.id,
                  ScenarioSource.contribution_type == "fork").one())
    assert ss.source_instance_id == mini_factory.id


def test_fork_name_collision_raises(clean_db, session, demo_scenario,
                                    mini_factory):
    fork_instance(session, mini_factory, new_name="my-fork")
    with pytest.raises(ForkError):
        fork_instance(session, mini_factory, new_name="my-fork")


def test_fork_default_name_pattern(clean_db, session, mini_factory):
    fork = fork_instance(session, mini_factory)
    stem, _, suffix = fork.name.partition("@")
    assert stem == "fork-src" and len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def _mk_version(session, inst_id, number, makespan=100):
    from coe.db.models.schedule import ScheduleVersion

    return ScheduleVersion(
        instance_id=inst_id, version_number=number,
        schedule_type="BASELINE", solver_status="FEASIBLE",
        objective_value=float(makespan), makespan=makespan,
        total_tardiness=0, alpha_weight=0.5, beta_weight=0.5,
        time_limit_seconds=30, solve_duration_seconds=0.42,
        failed_machine_ids=None, parent_version_id=None,
        rolled_back=False, payload_hash="0" * 64, payload_json={})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dashboard/test_fork.py -v`
Expected: FAIL — `ModuleNotFoundError` / ImportError (`coe.dashboard.fork` missing).

- [ ] **Step 3: Implement fork.py**

Create `coe/dashboard/fork.py`:

```python
"""Transactional instance fork (spec §2, §5): template stays pristine."""
import uuid

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from coe.db.models.downtime import (
    MachineDowntimeWindow,
    WorkerAbsenceWindow,
)
from coe.db.models.fjsp import (
    Job,
    JobFamily,
    Machine,
    MachineCapability,
    Operation,
    OperationMachineAlternative,
    SetupTime,
)
from coe.db.models.materials import Material, MaterialReceipt, OperationBom
from coe.db.models.provenance import Instance, ScenarioSource
from coe.db.models.schedule import (
    ScheduleEntry,
    ScheduleExplanation,
    ScheduleVersion,
)
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    Worker,
    WorkerAvailabilityWindow,
    WorkerRole,
)


class ForkError(RuntimeError):
    pass


def _copy_rows(session: Session, rows: list, overrides_for) -> list:
    """Clone ORM entities with fresh PKs.

    Works for single-PK tables ('id' dropped) and composite-PK tables
    (whose PK columns are exactly the overridden FK columns).
    """
    out = []
    for r in rows:
        attrs = {c.key: getattr(r, c.key)
                 for c in sa_inspect(r).mapper.column_attrs}
        attrs.pop("id", None)
        attrs.update(overrides_for(r))
        out.append(type(r)(**attrs))
    session.add_all(out)
    session.flush()
    return out


def _remap(value, id_map):
    return None if value is None else id_map[value]


def _clone_table(session: Session, model, source_id: int, fork_id: int,
                 order_col, **fk_maps):
    """Copy rows of `model` to the fork; returns (old_rows, new_rows, id_map)."""
    olds = (session.query(model)
            .filter(model.instance_id == source_id)
            .order_by(order_col).all())

    def overrides(r):
        out = {"instance_id": fork_id}
        for col, id_map in fk_maps.items():
            out[col] = _remap(getattr(r, col), id_map)
        return out

    news = _copy_rows(session, olds, overrides)
    return olds, news, {o.id: n.id for o, n in zip(olds, news)}


def fork_instance(session: Session, source: Instance,
                  new_name: str | None = None) -> Instance:
    """Fork every domain table under a fresh instance row (one transaction).

    Skips system-owned history: telemetry_events, recovery_runs,
    recovery_proposals. ScheduleVersion.parent_version_id and
    failed_machine_ids are not carried over (parent-scoped references);
    lineage lives in the ScenarioSource row written here.
    """
    name = new_name or f"{source.name}@{uuid.uuid4().hex[:8]}"
    if session.query(Instance).filter_by(name=name).one_or_none():
        raise ForkError(f"instance '{name}' already exists")
    fork = Instance(name=name, source_name="fork",
                    source_version=f"of:{source.name}",
                    source_license=source.source_license,
                    source_checksum=source.source_checksum)
    session.add(fork)
    session.flush()

    _, _, fam_ids = _clone_table(session, JobFamily, source.id, fork.id,
                                 JobFamily.id)
    _, _, mach_ids = _clone_table(session, Machine, source.id, fork.id,
                                  Machine.name)
    _, _, role_ids = _clone_table(session, WorkerRole, source.id, fork.id,
                                  WorkerRole.role_name)
    _clone_table(session, MachineCapability, source.id, fork.id,
                 MachineCapability.id, machine_id=mach_ids)
    _, _, worker_ids = _clone_table(session, Worker, source.id, fork.id,
                                    Worker.name, role_id=role_ids)
    _, _, job_ids = _clone_table(session, Job, source.id, fork.id,
                                 Job.name, job_family_id=fam_ids)
    _, _, op_ids = _clone_table(session, Operation, source.id, fork.id,
                                Operation.id, job_id=job_ids,
                                required_role_id=role_ids)
    _clone_table(session, OperationMachineAlternative, source.id, fork.id,
                 OperationMachineAlternative.operation_id,
                 operation_id=op_ids, machine_id=mach_ids)
    _clone_table(session, OperationMachineWorkerTime, source.id, fork.id,
                 OperationMachineWorkerTime.operation_id,
                 operation_id=op_ids, machine_id=mach_ids,
                 worker_id=worker_ids)
    _clone_table(session, SetupTime, source.id, fork.id,
                 SetupTime.id, machine_id=mach_ids,
                 from_family_id=fam_ids, to_family_id=fam_ids)
    _, _, mat_ids = _clone_table(session, Material, source.id, fork.id,
                                 Material.sku)
    _clone_table(session, OperationBom, source.id, fork.id,
                 OperationBom.operation_id, operation_id=op_ids,
                 material_id=mat_ids)
    _clone_table(session, MaterialReceipt, source.id, fork.id,
                 MaterialReceipt.available_at, material_id=mat_ids)
    _clone_table(session, MachineDowntimeWindow, source.id, fork.id,
                 MachineDowntimeWindow.id, machine_id=mach_ids)
    _clone_table(session, WorkerAvailabilityWindow, source.id, fork.id,
                 WorkerAvailabilityWindow.id, worker_id=worker_ids)
    _clone_table(session, WorkerAbsenceWindow, source.id, fork.id,
                 WorkerAbsenceWindow.id, worker_id=worker_ids)

    old_versions = (session.query(ScheduleVersion)
                    .filter(ScheduleVersion.instance_id == source.id)
                    .order_by(ScheduleVersion.version_number).all())
    new_versions = []
    for old_v in old_versions:
        attrs = {c.key: getattr(old_v, c.key)
                 for c in sa_inspect(old_v).mapper.column_attrs
                 if c.key != "id"}
        attrs.update({"instance_id": fork.id, "parent_version_id": None,
                      "failed_machine_ids": None})
        new_versions.append(ScheduleVersion(**attrs))
    session.add_all(new_versions)
    session.flush()
    ver_ids = {o.id: n.id for o, n in zip(old_versions, new_versions)}
    _clone_table(session, ScheduleEntry, source.id, fork.id,
                 ScheduleEntry.id, version_id=ver_ids,
                 operation_id=op_ids, machine_id=mach_ids,
                 worker_id=worker_ids)
    _clone_table(session, ScheduleExplanation, source.id, fork.id,
                 ScheduleExplanation.id, version_id=ver_ids)

    session.add(ScenarioSource(
        scenario_id=fork.id, source_instance_id=source.id,
        contribution_type="fork",
        transformation_description=f"fork of {source.name}"))
    session.flush()
    return fork
```

(`ScheduleExplanation` has a UNIQUE(version_id) constraint; remapped ids are fresh so no collision.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dashboard/test_fork.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Run full quick gate**

Run: `uv run pytest -m "not mqtt and not slow" -q`
Expected: no regressions (~220 tests pass).

- [ ] **Step 6: Commit**

```bash
git add coe/dashboard/fork.py tests/dashboard/test_fork.py
git commit -m "feat(dash): transactional instance fork service with lineage"
```

### Task A4: Configure page — read-only tabs

**Files:**
- Create: `coe/dashboard/pages/configure.py`

**Interfaces:**
- Consumes: `data.active_schedule`, `data.materials_overview`, `data.machines_overview`, `data.workers_overview`, `data.jobs_overview`, `data.jobs_per_day`; `st.session_state["instance"]`.
- Produces: `render()` entrypoint called by `app.py` dispatch.

Page logic is deliberately nil beyond composition — all query behavior was tested in Task A2. Verification is manual now; automated smoke arrives in Task C15.

- [ ] **Step 1: Implement the page**

Create `coe/dashboard/pages/configure.py`:

```python
"""Configure page: read-only views (uploads/events arrive in Milestone B)."""
import streamlit as st

from coe.dashboard import data


def _gantt_fig(entries: list[dict]):
    import pandas as pd
    import plotly.express as px

    if not entries:
        return None
    df = pd.DataFrame([{
        "Machine": e["machine_name"],
        "Job/Op": f"{e['job_name']}#{e['sequence_number']}",
        "Worker": e["worker_name"] or "-",
        "start": e["start_time"],
        "end": max(e["end_time"], e["start_time"] + 1),
    } for e in entries])
    fig = px.timeline(df, x_start="start", x_end="end", y="Machine",
                      color="Job/Op", text="Worker",
                      title="Active schedule")
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(title="minutes")
    return fig


def render() -> None:
    from coe.db.session import session_scope

    instance = st.session_state["instance"]
    with session_scope() as session:
        inst = session.execute(
            text("SELECT id FROM instances WHERE name = :n"),
            {"n": instance}).one()
        iid = inst.id

        tab_sched, tab_mat, tab_mach, tab_work, tab_jobs = st.tabs(
            ["Schedule", "Materials", "Machines", "Workers", "Jobs/day"])

        with tab_sched:
            versions = data.schedule_versions(session, iid)
            snap = data.active_schedule(session, iid)
            if snap is None:
                st.info("No active schedule. Solve one first: "
                        "`coe.cli solve baseline --instance " + instance + "`")
            else:
                v = snap["version"]
                st.caption(f"version {v['version_number']} · "
                           f"{v['schedule_type']} · {v['solver_status']} · "
                           f"makespan {v['makespan']} · "
                           f"tardiness {v['total_tardiness']}")
                fig = _gantt_fig(snap["entries"])
                if fig:
                    st.plotly_chart(fig, use_container_width=True)
                with st.expander("All versions"):
                    st.dataframe(versions)

        with tab_mat:
            for m in data.materials_overview(session, iid):
                with st.expander(
                    f"{m['sku']} — stock {m['initial_stock']}"
                    + (f" (reorder @ {m['reorder_point']})"
                       if m["reorder_point"] is not None else "")):
                    st.dataframe(m["receipts"] or
                                 [{"info": "no receipts"}])

        with tab_mach:
            st.dataframe(data.machines_overview(session, iid))

        with tab_work:
            st.dataframe(data.workers_overview(session, iid))

        with tab_jobs:
            st.dataframe(data.jobs_overview(session, iid))
            st.bar_chart(
                {f"day {d}": jobs for d, jobs in
                 data.jobs_per_day(session, iid).items()})
```

Add the missing import at the top of the file (used by `render`):

```python
from sqlalchemy import text
```

- [ ] **Step 2: Manual verification**

```bash
uv run python -m coe.cli solve baseline --instance factory_demo_01
uv run python -m coe.cli dashboard
```

Expected: Configure → Schedule tab shows a Gantt of the solved baseline; Materials lists MAT-* expanders; Machines shows 8 rows; Workers shows Nouri workers with availability tuples; Jobs/day bar chart renders. No traceback anywhere.

- [ ] **Step 3: Commit**

```bash
git add coe/dashboard/pages/configure.py
git commit -m "feat(dash): configure page read-only tabs"
```

### Task A5: Runs inspector page

**Files:**
- Create: `coe/dashboard/pages/runs.py`

**Interfaces:**
- Consumes: `data.recovery_runs`.
- Produces: `render()`.

- [ ] **Step 1: Implement the page**

Create `coe/dashboard/pages/runs.py`:

```python
"""Recovery run history inspector."""
import json

import plotly.express as px
import streamlit as st

from coe.dashboard import data


def render() -> None:
    from coe.db.session import session_scope

    instance = st.session_state["instance"]
    with session_scope() as session:
        row = session.execute(
            text("SELECT id FROM instances WHERE name = :n"),
            {"n": instance}).one()
        runs = data.recovery_runs(session, row.id)

    if not runs:
        st.info("No recovery runs yet.")
        return

    ok = {"COMMITTED"}
    for r in runs:
        mark = "✅" if r["status"] in ok else "❌"
        header = (f"{mark} run #{r['id']} · {r['trigger']} · "
                  f"{r['status']} · {r['started_at']:%m-%d %H:%M}")
        with st.expander(header, expanded=False):
            st.json(r["disruption_record_json"])
            timings = r["node_timings_json"] or {}
            pairs = [{"node": t["node"],
                      "seconds": round(t["ended_at"] - t["started_at"], 3)}
                     for t in timings if
                     isinstance(t, dict) and "node" in t]
            if pairs:
                st.plotly_chart(px.bar(pairs, x="node", y="seconds",
                                       title="Per-node wall-clock"),
                                use_container_width=True)
            if r["quantum_shadow_json"]:
                st.json(r["quantum_shadow_json"])
```

Add `from sqlalchemy import text` at top.

Note on `node_timings_json` shape: verify against how the committer persists it (`coe/solver/` or `coe/agents/graph.py` writer). If entries are `{node, started_at, ended_at}` epoch floats per spec §5 of Phase 5 design, the code above stands; adjust field names to the persisted reality if they differ.

- [ ] **Step 2: Manual verification**

Trigger a run if none exist (`mqtt test-failure` with listener running, or CLI recover), then open Runs page.
Expected: expanders list runs; COMMITTED shows green; timing bar chart renders when node timings exist.

- [ ] **Step 3: Commit**

```bash
git add coe/dashboard/pages/runs.py
git commit -m "feat(dash): recovery runs inspector"
```

### Task A6: Benchmarks page

**Files:**
- Create: `coe/dashboard/pages/benchmarks.py`

**Interfaces:**
- Consumes: `data.fidelity_report()` (reads `benchmark_report.json` produced by `coe.cli benchmark fidelity`).
- Produces: `render()`.

- [ ] **Step 1: Implement the page**

Create `coe/dashboard/pages/benchmarks.py`:

```python
"""Benchmark charts. Grows CP-SAT vs QAOA tables when P4/P5 land (spec §4)."""
import pandas as pd
import streamlit as st

from coe.dashboard import data


def render() -> None:
    report = data.fidelity_report()
    if report is None:
        st.info("No benchmark_report.json found. Run: "
                "`uv run python -m coe.cli benchmark fidelity "
                "--corpus data/corpus/fidelity-seed42 --seed 42`")
        return

    agg = report["translation"]["aggregate"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Corpus pass rate", f"{agg['corpus_pass_rate']:.1%}")
    c2.metric("Exact match rate", f"{agg['exact_match_rate']:.1%}")
    c3.metric("Threshold", "MET" if report["threshold_met"] else "MISS")

    cases = report["translation"].get("cases", [])
    if cases:
        df = pd.DataFrame(cases)
        st.dataframe(df)
    else:
        st.caption("Per-case breakdown not present in this report version.")

    st.divider()
    st.caption("CP-SAT vs QAOA comparison tables arrive with Phase 4/5.")
```

Implementer note: verify the JSON structure against `write_report` output in `coe/agents/benchmark.py:run_fidelity` — keys `translation.aggregate.corpus_pass_rate` etc. must exist; adapt to the real schema where it differs (print the file once and match it).

- [ ] **Step 2: Manual verification**

Run fidelity benchmark once, then open Benchmarks page.
Expected: three metric tiles populated; no traceback when report absent (info box instead).

- [ ] **Step 3: Run quick gate + commit**

```bash
uv run pytest -m "not mqtt and not slow" -q
git add coe/dashboard/pages/benchmarks.py
git commit -m "feat(dash): benchmarks page"
git tag milestone-A-cockpit-readonly
```

### Task B7: workbook schema + export

**Files:**
- Create: `coe/parsers/workbook.py`
- Test: `tests/parsers/test_workbook.py`

**Interfaces:**
- Consumes: domain models; `Job`/`Operation`/etc. joins.
- Produces (later tasks rely on these exact names):
  - `SHEETS: dict[str, tuple[str, ...]]` — sheet name → required column headers
  - `export_workbook(session, instance_id: int) -> bytes`
  - `WorkbookError = dict` shaped `{"sheet": str, "row": int | None, "message": str}`
  - `validate_workbook(data: bytes, session, parent) -> list[WorkbookError]` (Task B8)
  - `apply_workbook(session, parent, data: bytes, new_name: str | None) -> Instance` raising `WorkbookRejected(errors)` (Task B9)

Sheet grammar (editable domains ONLY — physics tables ride the fork; spec §3):
```
Meta         key, value                       (exported_from, exported_at, target_name)
Jobs         name, family, release_time, deadline, priority
Alternatives job, op_sequence, machine, processing_time
Speeds       job, op_sequence, machine, worker, processing_time
Setups       machine, from_family, to_family, setup_duration
Materials    sku, initial_stock, reorder_point
Receipts     sku, quantity, available_at, source
Availability worker, available_from, available_until
BOM          job, op_sequence, sku, quantity_required
```
Operations have **no sheet**: an operation exists exactly where an `Alternatives` row exists for its `(job, op_sequence)` (an op with no capable machine is meaningless). `required_role_id` stays NULL for workbook-created ops. Every `(job, op_sequence)` appearing in Speeds/BOM must therefore appear in Alternatives — validated in Task B8.

- [ ] **Step 1: Write failing tests**

Create `tests/parsers/test_workbook.py`:

```python
import io

import pytest
from openpyxl import load_workbook

from coe.parsers.workbook import SHEETS, export_workbook

pytestmark = pytest.mark.db


def test_export_contains_all_sheets_with_headers(clean_db, session,
                                                 demo_scenario):
    blob = export_workbook(session, demo_scenario)
    wb = load_workbook(io.BytesIO(blob))
    assert set(SHEETS) <= set(wb.sheetnames)
    for name, headers in SHEETS.items():
        ws = wb[name]
        assert [c.value for c in ws[1]] == list(headers)


def test_export_jobs_match_database(clean_db, session, demo_scenario):
    from sqlalchemy import text as sqltext

    from coe.db.models.fjsp import Job

    blob = export_workbook(session, demo_scenario)
    ws = load_workbook(io.BytesIO(blob))["Jobs"]
    exported = {r[0] for r in ws.iter_rows(min_row=2, values_only=True)}
    db_names = {n for (n,) in session.query(Job.name)
                .filter(Job.instance_id == demo_scenario).all()}
    assert exported == db_names


def test_meta_sheet_records_source(clean_db, session, demo_scenario):
    blob = export_workbook(session, demo_scenario)
    ws = load_workbook(io.BytesIO(blob))["Meta"]
    meta = {r[0]: r[1] for r in ws.iter_rows(min_row=2, values_only=True)}
    assert meta["exported_from"] == "factory_demo_01"
    assert "target_name" in meta
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/parsers/test_workbook.py -v`
Expected: FAIL — ImportError (`coe.parsers.workbook` missing).

- [ ] **Step 3: Implement schema + export**

Create `coe/parsers/workbook.py`:

```python
"""Fourth importer: user-authored factory workbook (dashboard design §3).

Editable domains only; physics tables are inherited verbatim from the
parent instance via fork_instance(). Import is two-phase: validate_workbook()
produces row-level errors without writing; apply_workbook() forks then
replaces covered domains atomically.
"""
from datetime import datetime, timezone
from io import BytesIO

from openpyxl import Workbook, load_workbook
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

SHEETS: dict[str, tuple[str, ...]] = {
    "Meta": ("key", "value"),
    "Jobs": ("name", "family", "release_time", "deadline", "priority"),
    "Alternatives": ("job", "op_sequence", "machine", "processing_time"),
    "Speeds": ("job", "op_sequence", "machine", "worker",
               "processing_time"),
    "Setups": ("machine", "from_family", "to_family", "setup_duration"),
    "Materials": ("sku", "initial_stock", "reorder_point"),
    "Receipts": ("sku", "quantity", "available_at", "source"),
    "Availability": ("worker", "available_from", "available_until"),
    "BOM": ("job", "op_sequence", "sku", "quantity_required"),
}

_INT_COLS = {"op_sequence", "release_time", "deadline", "priority",
             "processing_time", "setup_duration", "initial_stock",
             "reorder_point", "quantity", "available_at",
             "available_from", "available_until", "quantity_required"}


class WorkbookRejected(ValueError):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        lines = "\n".join(f"[{e['sheet']}#{e['row']}] {e['message']}"
                          for e in errors[:20])
        super().__init__(f"workbook rejected, {len(errors)} problem(s):\n{lines}")


def _sheet_rows(session: Session, model, instance_id: int,
                order_col, project):
    rows = (session.query(model)
            .filter(model.instance_id == instance_id)
            .order_by(order_col).all())
    return [project(r) for r in rows]


def export_workbook(session: Session, instance_id: int) -> bytes:
    """Serialize the editable domains of an instance to xlsx bytes."""
    from coe.db.models.fjsp import (
        Job, JobFamily, Machine, Operation, OperationMachineAlternative,
        SetupTime)
    from coe.db.models.materials import Material, MaterialReceipt
    from coe.db.models.provenance import Instance
    from coe.db.models.workers import (
        OperationMachineWorkerTime, Worker, WorkerAvailabilityWindow)

    inst = session.get(Instance, instance_id)
    fam = {f.id: f.name for f in session.query(JobFamily)
           .filter(JobFamily.instance_id == instance_id)}
    mach = {m.id: m.name for m in session.query(Machine)
            .filter(Machine.instance_id == instance_id)}
    work = {w.id: w.name for w in session.query(Worker)
            .filter(Worker.instance_id == instance_id)}
    jobs = {j.id: j.name for j in session.query(Job)
            .filter(Job.instance_id == instance_id)}
    fam_by_name = {v: k for k, v in fam.items()}
    mach_by_name = {v: k for k, v in mach.items()}

    data: dict[str, list[tuple]] = {}
    data["Jobs"] = _sheet_rows(
        session, Job, instance_id, Job.name,
        lambda r: (r.name, fam.get(r.job_family_id), r.release_time,
                   r.deadline, r.priority))
    ops = (session.query(Operation)
           .filter(Operation.instance_id == instance_id)
           .order_by(Operation.id).all())
    op_key = {(jobs[o.job_id], o.sequence_number): o.id for o in ops}
    alt_rows = _sheet_rows(
        session, OperationMachineAlternative, instance_id,
        OperationMachineAlternative.operation_id,
        lambda r: (*[k for k, v in op_key.items() if v == r.operation_id][0],
                   mach[r.machine_id], r.processing_time))
    data["Alternatives"] = sorted(alt_rows)
    data["Speeds"] = sorted(_sheet_rows(
        session, OperationMachineWorkerTime, instance_id,
        OperationMachineWorkerTime.operation_id,
        lambda r: (*[k for k, v in op_key.items() if v == r.operation_id][0],
                   mach[r.machine_id], work[r.worker_id],
                   r.processing_time)))
    data["Setups"] = sorted(_sheet_rows(
        session, SetupTime, instance_id, SetupTime.id,
        lambda r: (mach[r.machine_id],
                   fam.get(r.from_family_id), fam.get(r.to_family_id),
                   r.setup_duration)))
    data["Materials"] = _sheet_rows(
        session, Material, instance_id, Material.sku,
        lambda r: (r.sku, r.initial_stock, r.reorder_point))
    mat = {m.sku: m.id for m in session.query(Material)
           .filter(Material.instance_id == instance_id)}
    data["Receipts"] = sorted(_sheet_rows(
        session, MaterialReceipt, instance_id, MaterialReceipt.id,
        lambda r: (*[k for k, v in mat.items() if v == r.material_id],
                   r.quantity, r.available_at, r.source)))
    data["Availability"] = sorted(_sheet_rows(
        session, WorkerAvailabilityWindow, instance_id,
        WorkerAvailabilityWindow.id,
        lambda r: (work[r.worker_id], r.available_from,
                   r.available_until)))

    wb = Workbook()
    wb.remove(wb.active)
    meta = wb.create_sheet("Meta")
    meta.append(("key", "value"))
    meta.append(("exported_from", inst.name))
    meta.append(("exported_at",
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
    meta.append(("target_name", f"{inst.name}-edited"))
    for name, headers in SHEETS.items():
        if name == "Meta":
            continue
        ws = wb.create_sheet(name)
        ws.append(list(headers))
        for row in data.get(name, []):
            ws.append(list(row))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

Implementer note: the `[k for k,v in op_key.items() if v == r.operation_id][0]` reverse lookups are O(n²) at factory scale (168 ops × rows). If export feels slow (>2 s), build `op_by_id = {v: k for k, v in op_key.items()}` once and index it; correctness identical.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/parsers/test_workbook.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Generate and commit the shipped template**

```bash
uv run python - << 'EOF'
from pathlib import Path
from coe.cli import _instance_or_die
from coe.db.session import session_scope
from coe.parsers.workbook import export_workbook

with session_scope() as s:
    inst = _instance_or_die(s, "factory_demo_01")
    Path("data/templates").mkdir(parents=True, exist_ok=True)
    Path("data/templates/factory_workbook.xlsx").write_bytes(
        export_workbook(s, inst.id))
print("template written")
EOF
git add coe/parsers/workbook.py tests/parsers/test_workbook.py \
        data/templates/factory_workbook.xlsx
git commit -m "feat(parsers): workbook exporter + shipped template"
```

### Task B8: validate_workbook — dry-run rejection classes

**Files:**
- Modify: `coe/parsers/workbook.py` (append functions)
- Test: `tests/parsers/test_workbook.py` (append)

**Interfaces:**
- Consumes: `SHEETS`, `_INT_COLS`.
- Produces:
  - `load_rows(data: bytes) -> dict[str, list[dict]]` — parsed sheets, header-checked
  - `validate_workbook(data: bytes, session, parent) -> list[WorkbookError]`

Rejection classes (spec §7 parse-time contract): missing sheet · missing column · non-integer time/qty column · negative value where CHECK forbids (`release_time ≥ 0`, quantities > 0, windows `from < until`) · unknown name reference (job/machine/worker/sku/family resolved against **parent** scope) · duplicate keys (Jobs.name, Materials.sku, Alternatives(job,seq,machine), Speeds(job,seq,machine,worker), Setups(machine,from,to)) · Speeds/BOM referencing an `(job, op_sequence)` absent from Alternatives · job present in DB but absent from Jobs sheet while any of its ops has schedule entries (rejection message points to Suspend action).

- [ ] **Step 1: Write failing tests**

Append to `tests/parsers/test_workbook.py`:

```python
from coe.parsers.workbook import apply_workbook, validate_workbook


def _edit(blob: bytes, sheet: str, mutate) -> bytes:
    """Load export bytes, apply mutate(wb), return new bytes."""
    wb = load_workbook(io.BytesIO(blob))
    mutate(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _set_header(ws, col_idx: int, value):
    ws.cell(row=1, column=col_idx, value=value)


def test_missing_column_rejected(clean_db, session, demo_scenario):
    blob = export_workbook(session, demo_scenario)

    def drop_col(wb):
        ws = wb["Jobs"]
        ws.delete_cols(3)          # release_time

    errs = validate_workbook(_edit(blob, "Jobs", drop_col), session,
                             _parent(session))
    assert any("missing column" in e["message"] and e["sheet"] == "Jobs"
               for e in errs)


def test_unknown_sku_rejected_with_row_number(clean_db, session,
                                              demo_scenario):
    blob = export_workbook(session, demo_scenario)

    def bad_receipt(wb):
        ws = wb["Receipts"]
        ws.append(("MAT-NOPE", 5, 100, "test"))

    errs = validate_workbook(_edit(blob, "Receipts", bad_receipt), session,
                             _parent(session))
    hit = [e for e in errs if e["sheet"] == "Receipts" and "MAT-NOPE" in
           e["message"]]
    assert hit and hit[0]["row"] == ws_row_of_append(demo_scenario)


def test_negative_processing_time_rejected(clean_db, session,
                                           demo_scenario):
    blob = export_workbook(session, demo_scenario)

    def negate(wb):
        ws = wb["Alternatives"]
        ws.cell(row=2, column=4, value=-5)

    errs = validate_workbook(_edit(blob, "Alternatives", negate), session,
                             _parent(session))
    assert any("non-negative" in e["message"] for e in errs)


def test_speed_without_alternative_rejected(clean_db, session,
                                            demo_scenario):
    from coe.db.models.fjsp import Job, Machine
    from coe.db.models.workers import Worker

    blob = export_workbook(session, demo_scenario)
    jname = session.query(Job.name).filter(
        Job.instance_id == demo_scenario).order_by(Job.name).first()[0]
    mname = session.query(Machine.name).filter(
        Machine.instance_id == demo_scenario).order_by(Machine.name).first()[0]
    wname = session.query(Worker.name).filter(
        Worker.instance_id == demo_scenario).order_by(Worker.name).first()[0]

    def add_speed(wb):
        wb["Speeds"].append((jname, 99, mname, wname, 7))

    errs = validate_workbook(_edit(blob, "Speeds", add_speed), session,
                             _parent(session))
    assert any("not in Alternatives" in e["message"] for e in errs)


def test_scheduled_job_removal_rejected(clean_db, session, demo_scenario):
    """Removing a scheduled job's rows must point the user at Suspend."""
    blob = export_workbook(session, demo_scenario)
    _add_scheduled_job(session, demo_scenario, "ZZZ-sched")

    def remove_zzz(wb):
        ws = wb["Jobs"]
        rows = list(ws.iter_rows(min_row=2))
        for r in rows:
            if r[0].value == "ZZZ-sched":
                ws.delete_rows(r[0].row)

    errs = validate_workbook(_edit(blob, "Jobs", remove_zzz), session,
                             _parent(session))
    assert any("Suspend" in e["message"] for e in errs)
```

Support fixtures/helpers appended to the same file:

```python
def _parent(session):
    from coe.db.models.provenance import Instance

    return (session.query(Instance)
            .filter(Instance.name == "factory_demo_01").one())


def ws_row_of_append(instance_id: int) -> int:
    """Row number the appended bad receipt occupied (2 + existing rows)."""
    from coe.db.models.materials import MaterialReceipt

    from coe.db.session import session_scope
    with session_scope() as s:
        n = s.query(MaterialReceipt).filter(
            MaterialReceipt.instance_id == instance_id).count()
    return n + 2


def _add_scheduled_job(session, instance_id: int, job_name: str):
    """Job+op+alternative+one schedule entry, to prove removal guard."""
    from coe.db.models.fjsp import (
        Job, Machine, Operation, OperationMachineAlternative)
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    mach = (session.query(Machine)
            .filter(Machine.instance_id == instance_id)
            .order_by(Machine.name).first())
    job = Job(instance_id=instance_id, name=job_name, release_time=0,
              deadline=500, priority=1, status="PENDING")
    session.add(job)
    session.flush()
    op = Operation(instance_id=instance_id, job_id=job.id,
                   sequence_number=1)
    session.add(op)
    session.flush()
    session.add(OperationMachineAlternative(
        instance_id=instance_id, operation_id=op.id, machine_id=mach.id,
        processing_time=10))
    ver = ScheduleVersion(instance_id=instance_id, version_number=999,
                          schedule_type="BASELINE", solver_status="FEASIBLE",
                          objective_value=1.0, makespan=10, total_tardiness=0,
                          alpha_weight=0.5, beta_weight=0.5,
                          time_limit_seconds=1, solve_duration_seconds=0.1,
                          failed_machine_ids=None, parent_version_id=None,
                          rolled_back=False, payload_hash="0" * 64,
                          payload_json={})
    session.add(ver)
    session.flush()
    session.add(ScheduleEntry(instance_id=instance_id, version_id=ver.id,
                              operation_id=op.id, machine_id=mach.id,
                              worker_id=None, start_time=0, end_time=10,
                              processing_time=10, setup_time=0,
                              status="SCHEDULED", is_frozen=False))
    session.flush()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/parsers/test_workbook.py -v -k "rejected or removal"`
Expected: FAIL — `ImportError: cannot import name 'validate_workbook'`.

- [ ] **Step 3: Implement loader + validator**

Append to `coe/parsers/workbook.py`:

```python
def load_rows(data: bytes) -> dict[str, list[dict]]:
    """Parse xlsx into per-sheet dict rows after header verification."""
    wb = load_workbook(BytesIO(data), data_only=True)
    problems: list[dict] = []
    for name in SHEETS:
        if name not in wb.sheetnames:
            problems.append({"sheet": name, "row": None,
                             "message": "missing sheet"})
    if problems:
        raise WorkbookRejected(problems)
    out: dict[str, list[dict]] = {}
    for name, headers in SHEETS.items():
        ws = wb[name]
        actual = [c.value for c in ws[1]]
        for h in headers:
            if h not in actual:
                problems.append({"sheet": name, "row": 1,
                                 "message": f"missing column '{h}'"})
        out[name] = []
        for ridx, row in enumerate(ws.iter_rows(min_row=2, values_only=True),
                                   start=2):
            if all(v is None for v in row):
                continue
            out[name].append(dict(zip(actual, row)) | {"_row": ridx})
    if problems:
        raise WorkbookRejected(problems)
    return out


def _as_int(sheet: str, row: dict, col: str, errors: list[dict],
            *, positive: bool = False, nonneg: bool = True) -> int | None:
    raw = row.get(col)
    if raw is None:
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        errors.append({"sheet": sheet, "row": row["_row"],
                       "message": f"'{col}' must be an integer, got {raw!r}"})
        return None
    if positive and val <= 0:
        errors.append({"sheet": sheet, "row": row["_row"],
                       "message": f"'{col}' must be > 0, got {val}"})
    elif nonneg and val < 0:
        errors.append({"sheet": sheet, "row": row["_row"],
                       "message": f"'{col}' must be non-negative, got {val}"})
    return val


def validate_workbook(data: bytes, session: Session, parent) -> list[dict]:
    """Full dry-run. Returns [] when the workbook is applicable.

    Name references resolve against the PARENT instance: apply forks the
    parent first, so parent scope == fork scope for inherited tables.
    """
    from coe.db.models.fjsp import (
        Job, JobFamily, Machine, Operation, OperationMachineAlternative)
    from coe.db.models.materials import Material, MaterialReceipt
    from coe.db.models.schedule import ScheduleEntry
    from coe.db.models.workers import Worker

    try:
        rows = load_rows(data)
    except WorkbookRejected as exc:
        return exc.errors

    errors: list[dict] = []
    pid = parent.id

    fam_names = {n for (n,) in session.query(JobFamily.name)
                 .filter(JobFamily.instance_id == pid)}
    mach_names = {n for (n,) in session.query(Machine.name)
                  .filter(Machine.instance_id == pid)}
    work_names = {n for (n,) in session.query(Worker.name)
                  .filter(Worker.instance_id == pid)}
    sku_names = {n for (n,) in session.query(Material.sku)
                 .filter(Material.instance_id == pid)}
    job_names = {n for (n,) in session.query(Job.name)
                 .filter(Job.instance_id == pid)}

    jobs_in_file: set[str] = set()
    seen_job_rows: set[str] = set()
    for r in rows["Jobs"]:
        name = r.get("name")
        if not name:
            errors.append({"sheet": "Jobs", "row": r["_row"],
                           "message": "job name required"})
            continue
        if name in seen_job_rows:
            errors.append({"sheet": "Jobs", "row": r["_row"],
                           "message": f"duplicate job '{name}'"})
        seen_job_rows.add(name)
        jobs_in_file.add(name)
        fam = r.get("family")
        if fam is not None and fam not in fam_names:
            errors.append({"sheet": "Jobs", "row": r["_row"],
                           "message": f"unknown family '{fam}'"})
        _as_int("Jobs", r, "release_time", errors)
        _as_int("Jobs", r, "deadline", errors)
        pr = _as_int("Jobs", r, "priority", errors)
        if pr is not None and pr < 1:
            errors.append({"sheet": "Jobs", "row": r["_row"],
                           "message": f"priority must be >= 1, got {pr}"})

    alt_keys: set[tuple] = set()
    seen_alt: set[tuple] = set()
    for r in rows["Alternatives"]:
        j, sq, m = r.get("job"), _as_int("Alternatives", r, "op_sequence",
                                         errors), r.get("machine")
        pt = _as_int("Alternatives", r, "processing_time", errors)
        key = (j, sq, m)
        if key in seen_alt:
            errors.append({"sheet": "Alternatives", "row": r["_row"],
                           "message": f"duplicate alternative {key}"})
        seen_alt.add(key)
        if j is not None:
            alt_keys.add((j, sq))
        if m is not None and m not in mach_names:
            errors.append({"sheet": "Alternatives", "row": r["_row"],
                           "message": f"unknown machine '{m}'"})
        if pt is not None and pt < 0:
            pass  # already reported by _as_int non-negative check

    for r in rows["Speeds"]:
        j = r.get("job")
        sq = _as_int("Speeds", r, "op_sequence", errors)
        m, w = r.get("machine"), r.get("worker")
        _as_int("Speeds", r, "processing_time", errors)
        if m not in mach_names:
            errors.append({"sheet": "Speeds", "row": r["_row"],
                           "message": f"unknown machine '{m}'"})
        if w not in work_names:
            errors.append({"sheet": "Speeds", "row": r["_row"],
                           "message": f"unknown worker '{w}'"})
        if (j, sq) not in alt_keys:
            errors.append({"sheet": "Speeds", "row": r["_row"],
                           "message":
                           f"speed for ('{j}',{sq}) not in Alternatives"})

    seen_setup: set[tuple] = set()
    for r in rows["Setups"]:
        m = r.get("machine")
        ff, tt = r.get("from_family"), r.get("to_family")
        _as_int("Setups", r, "setup_duration", errors, positive=True)
        for fname, label in ((ff, "from_family"), (tt, "to_family")):
            if fname is not None and fname not in fam_names:
                errors.append({"sheet": "Setups", "row": r["_row"],
                               "message": f"unknown {label} '{fname}'"})
        key = (m, ff, tt)
        if key in seen_setup:
            errors.append({"sheet": "Setups", "row": r["_row"],
                           "message": f"duplicate setup {key}"})
        seen_setup.add(key)

    seen_sku: set[str] = set()
    for r in rows["Materials"]:
        sku = r.get("sku")
        _as_int("Materials", r, "initial_stock", errors, nonneg=True)
        _as_int("Materials", r, "reorder_point", errors)
        if sku in seen_sku:
            errors.append({"sheet": "Materials", "row": r["_row"],
                           "message": f"duplicate sku '{sku}'"})
        seen_sku.add(sku or "")

    for r in rows["Receipts"]:
        if r.get("sku") not in sku_names:
            errors.append({"sheet": "Receipts", "row": r["_row"],
                           "message":
                           f"unknown material '{r.get('sku')}' — "
                           "add it under Materials first"})
        q = _as_int("Receipts", r, "quantity", errors, positive=True)
        _as_int("Receipts", r, "available_at", errors)

    for r in rows["Availability"]:
        if r.get("worker") not in work_names:
            errors.append({"sheet": "Availability", "row": r["_row"],
                           "message": f"unknown worker '{r.get('worker')}'"})
        a = _as_int("Availability", r, "available_from", errors)
        b = _as_int("Availability", r, "available_until", errors)
        if a is not None and b is not None and b <= a:
            errors.append({"sheet": "Availability", "row": r["_row"],
                           "message":
                           f"available_until ({b}) must exceed "
                           f"available_from ({a})"})

    for r in rows["BOM"]:
        j = r.get("job")
        sq = _as_int("BOM", r, "op_sequence", errors)
        _as_int("BOM", r, "quantity_required", errors, positive=True)
        if r.get("sku") not in sku_names and r.get("sku") not in seen_sku:
            errors.append({"sheet": "BOM", "row": r["_row"],
                           "message": f"unknown material '{r.get('sku')}'"})
        if (j, sq) not in alt_keys:
            errors.append({"sheet": "BOM", "row": r["_row"],
                           "message":
                           f"BOM for ('{j}',{sq}) not in Alternatives"})

    # ---- removal guards against DB state ----
    id_to_job = {j.id: j.name for j in session.query(Job)
                 .filter(Job.instance_id == pid)}
    existing_ops = {(id_to_job[o.job_id], o.sequence_number)
                    for o in session.query(Operation)
                    .filter(Operation.instance_id == pid)}

    removed_jobs = job_names - jobs_in_file
    shrunk_ops = existing_ops - alt_keys
    if removed_jobs or shrunk_ops:
        guarded_ids = {e.operation_id for e in
                       session.query(ScheduleEntry.operation_id)
                       .filter(ScheduleEntry.instance_id == pid)}
        guarded = {(id_to_job[o.job_id], o.sequence_number)
                   for o in session.query(Operation)
                   .filter(Operation.instance_id == pid,
                           Operation.id.in_(guarded_ids))}
        for job_name in sorted(removed_jobs):
            if any(jn == job_name for jn, _ in guarded):
                errors.append({
                    "sheet": "Jobs", "row": None,
                    "message":
                    f"job '{job_name}' has committed schedule entries — "
                    "remove it via the Suspend action instead"})
        for key in sorted(shrunk_ops):
            if key in guarded:
                errors.append({
                    "sheet": "Alternatives", "row": None,
                    "message":
                    f"operation {key} has committed schedule entries — "
                    "restore it in Alternatives or Suspend its job"})
    return errors
```
```


- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/parsers/test_workbook.py -v`
Expected: all previous PASS plus the five new rejection tests PASS. Note `test_scheduled_job_removal_rejected` requires the unmodified export to contain `ZZZ-sched` — the helper adds it before export? No: order is export→helper→mutate; fix by calling `_add_scheduled_job` BEFORE `export_workbook`. Adjusted test body:

```python
def test_scheduled_job_removal_rejected(clean_db, session, demo_scenario):
    """Removing a scheduled job's rows must point the user at Suspend."""
    _add_scheduled_job(session, demo_scenario, "ZZZ-sched")
    blob = export_workbook(session, demo_scenario)

    def remove_zzz(wb):
        ws = wb["Jobs"]
        for r in list(ws.iter_rows(min_row=2)):
            if r[0].value == "ZZZ-sched":
                ws.delete_rows(r[0].row)

    errs = validate_workbook(_edit(blob, "Jobs", remove_zzz), session,
                             _parent(session))
    assert any("Suspend" in e["message"] for e in errs)
```

- [ ] **Step 5: Commit**

```bash
git add coe/parsers/workbook.py tests/parsers/test_workbook.py
git commit -m "feat(parsers): workbook dry-run validation with row-level reports"
```

### Task B9: apply_workbook — fork-and-replace + round-trip property

**Files:**
- Modify: `coe/parsers/workbook.py` (append function)
- Test: `tests/parsers/test_workbook.py` (append)

**Interfaces:**
- Consumes: `fork_instance` (`coe/dashboard/fork.py`), `validate_workbook`, `load_rows`.
- Produces: `apply_workbook(session, parent, data: bytes, new_name: str | None = None) -> Instance` raising `WorkbookRejected`.

Apply semantics per domain (spec §5): **replace-all** for Materials, Receipts, Availability, Setups, Alternatives, Speeds, BOM; **upsert-by-name** for Jobs (field edits + additions; deletions only when the removal guard passed). Operations are derived from Alternatives: surviving `(job, seq)` pairs KEEP their operation rows (so committed `schedule_entries` FKs stay valid); vanished pairs were already guard-checked as unscheduled and get deleted with their job if applicable.

- [ ] **Step 1: Write failing tests**

Append to `tests/parsers/test_workbook.py`:

```python
import pytest

from coe.parsers.workbook import WorkbookRejected


def _semantic_state(session, iid: int) -> dict[str, set]:
    """Id-free multiset snapshot of every editable domain."""
    from sqlalchemy import text

    def q(sql):
        return {tuple(r) for r in session.execute(text(sql),
                                                  {"i": iid}).all()}

    return {
        "Jobs": q(
            "SELECT j.name, COALESCE(f.name,''), j.release_time, "
            "       j.deadline, j.priority FROM jobs j "
            "LEFT JOIN job_families f ON f.id = j.job_family_id "
            "WHERE j.instance_id = :i"),
        "Alternatives": q(
            "SELECT j.name, o.sequence_number, m.name, "
            "       a.processing_time FROM operation_machine_alternatives a "
            "JOIN operations o ON o.id = a.operation_id "
            "JOIN jobs j ON j.id = o.job_id "
            "JOIN machines m ON m.id = a.machine_id "
            "WHERE a.instance_id = :i"),
        "Speeds": q(
            "SELECT j.name, o.sequence_number, m.name, w.name, "
            "       t.processing_time FROM operation_machine_worker_times t "
            "JOIN operations o ON o.id = t.operation_id "
            "JOIN jobs j ON j.id = o.job_id "
            "JOIN machines m ON m.id = t.machine_id "
            "JOIN workers w ON w.id = t.worker_id "
            "WHERE t.instance_id = :i"),
        "Setups": q(
            "SELECT m.name, ff.name, tt.name, s.setup_duration "
            "FROM setup_times s JOIN machines m ON m.id = s.machine_id "
            "LEFT JOIN job_families ff ON ff.id = s.from_family_id "
            "LEFT JOIN job_families tt ON tt.id = s.to_family_id "
            "WHERE s.instance_id = :i"),
        "Materials": q(
            "SELECT sku, initial_stock, reorder_point FROM materials "
            "WHERE instance_id = :i"),
        "Receipts": q(
            "SELECT mat.sku, r.quantity, r.available_at, r.source "
            "FROM material_receipts r JOIN materials mat ON mat.id = "
            "r.material_id WHERE r.instance_id = :i"),
        "Availability": q(
            "SELECT w.name, v.available_from, v.available_until "
            "FROM worker_availability_windows v "
            "JOIN workers w ON w.id = v.worker_id "
            "WHERE v.instance_id = :i"),
        "BOM": q(
            "SELECT j.name, o.sequence_number, mat.sku, b.quantity_required "
            "FROM operation_bom b JOIN operations o ON o.id = b.operation_id "
            "JOIN jobs j ON j.id = o.job_id "
            "JOIN materials mat ON mat.id = b.material_id "
            "WHERE b.instance_id = :i"),
    }


def test_round_trip_identity(clean_db, session, demo_scenario):
    """export → apply unchanged ⇒ every editable domain semantically equal."""
    blob = export_workbook(session, demo_scenario)
    fork = apply_workbook(session, _parent(session), blob,
                          new_name="rt-check")
    before = _semantic_state(session, demo_scenario)
    after = _semantic_state(session, fork.id)
    assert before == after


def test_round_trip_keeps_schedule_fk_valid(clean_db, session,
                                            demo_scenario):
    _add_scheduled_job(session, demo_scenario, "ZZZ-sched")
    blob = export_workbook(session, demo_scenario)
    fork = apply_workbook(session, _parent(session), blob,
                          new_name="rt-sched")
    from sqlalchemy import text

    broken = session.execute(text(
        "SELECT count(*) FROM schedule_entries se "
        "WHERE se.instance_id = :f AND NOT EXISTS ("
        "  SELECT 1 FROM operations o WHERE o.id = se.operation_id)"
    ), {"f": fork.id}).scalar_one()
    assert broken == 0
    # and the ZZZ entry still points at an op named ZZZ-sched#1 equivalent
    mapped = session.execute(text(
        "SELECT j.name, o.sequence_number FROM schedule_entries se "
        "JOIN operations o ON o.id = se.operation_id "
        "JOIN jobs j ON j.id = o.job_id WHERE se.instance_id = :f"
    ), {"f": fork.id}).all()
    assert [(n, sq) for (n, sq) in mapped] == [("ZZZ-sched", 1)]


def test_edits_landing_and_parent_pristine(clean_db, session,
                                           demo_scenario):
    blob = export_workbook(session, demo_scenario)

    def bump_and_add(wb):
        ws = wb["Materials"]
        ws.cell(row=2, column=2, value=ws.cell(row=2, column=2).value + 7)
        wb["Jobs"].append(("NEWJOB", None, 0, 300, 1))
        wb["Alternatives"].append(
            ("NEWJOB", 1,
             wb["Alternatives"].cell(row=2, column=3).value, 12))

    fork = apply_workbook(session, _parent(session),
                          _edit(blob, "Materials", bump_and_add),
                          new_name="edited")
    mats = dict(_semantic_state(session, fork.id)["Materials"])
    parent_mats = _semantic_state(session, demo_scenario)["Materials"]
    first_sku = sorted(parent_mats)[0][0]
    assert mats[first_sku][1] == sorted(
        parent_mats)[0][1] + 7                      # stock bumped by 7
    assert ("NEWJOB", "", 0, 300, 1) in \
        _semantic_state(session, fork.id)["Jobs"]   # job added
    # parent untouched:
    assert _semantic_state(session, demo_scenario)["Materials"] \
        == parent_mats


def test_invalid_apply_raises_without_instance(clean_db, session,
                                               demo_scenario):
    from coe.db.models.provenance import Instance

    blob = export_workbook(session, demo_scenario)

    def poison(wb):
        wb["Receipts"].append(("MAT-GHOST", 5, 100, "test"))

    n_before = session.query(Instance).count()
    with pytest.raises(WorkbookRejected):
        apply_workbook(session, _parent(session),
                       _edit(blob, "Receipts", poison))
    assert session.query(Instance).count() == n_before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/parsers/test_workbook.py -v -k "round_trip or edits or invalid_apply"`
Expected: FAIL — `ImportError: cannot import name 'apply_workbook'`.

- [ ] **Step 3: Implement apply_workbook**

Append to `coe/parsers/workbook.py`:

```python
def planned_job_names(rows: dict) -> set[str]:
    return {r["name"] for r in rows["Jobs"]}


def apply_workbook(session: Session, parent, data: bytes,
                   new_name: str | None = None):
    """Validate → fork → replace covered domains (single transaction).

    Deletion order matters: dependents (bom/speeds/alts/setups/receipts/
    availability) die before materials; vanished OPERATIONS die before
    their vanished JOB (the validation guard proved both unscheduled).
    """
    from coe.dashboard.fork import fork_instance
    from coe.db.models.fjsp import (
        Job,
        JobFamily,
        Machine,
        Operation,
        OperationMachineAlternative,
        SetupTime,
    )
    from coe.db.models.materials import (
        Material,
        MaterialReceipt,
        OperationBom,
    )
    from coe.db.models.workers import (
        OperationMachineWorkerTime,
        Worker,
        WorkerAvailabilityWindow,
    )

    errors = validate_workbook(data, session, parent)
    if errors:
        raise WorkbookRejected(errors)
    rows = load_rows(data)

    if new_name is None:
        meta = {r.get("key"): r.get("value") for r in rows["Meta"]}
        new_name = meta.get("target_name")

    fork = fork_instance(session, parent, new_name=new_name)
    fid = fork.id

    fam = {f.name: f.id for f in session.query(JobFamily)
           .filter(JobFamily.instance_id == fid)}
    mach = {m.name: m.id for m in session.query(Machine)
            .filter(Machine.instance_id == fid)}
    work = {w.name: w.id for w in session.query(Worker)
            .filter(Worker.instance_id == fid)}

    planned_alt_keys = {(r["job"], int(r["op_sequence"]))
                        for r in rows["Alternatives"]}
    wanted_jobs = planned_job_names(rows)

    # --- snapshot current ops keyed by (job_name, seq) ---
    job_name_by_id = {j.id: j.name for j in session.query(Job)
                      .filter(Job.instance_id == fid)}
    old_jobs = {j.name: j for j in session.query(Job)
                .filter(Job.instance_id == fid)}
    old_ops = {}
    for o in session.query(Operation).filter(Operation.instance_id == fid):
        old_ops[(job_name_by_id[o.job_id], o.sequence_number)] = o

    # --- wipe replaced domains, FK-safe order ---
    def wipe(model):
        session.query(model).filter(model.instance_id == fid) \
            .delete(synchronize_session=False)

    wipe(OperationBom)
    wipe(OperationMachineWorkerTime)
    wipe(OperationMachineAlternative)
    wipe(SetupTime)
    wipe(MaterialReceipt)
    wipe(WorkerAvailabilityWindow)
    wipe(Material)
    session.flush()

    # --- prune vanished operations FIRST (unscheduled per guard),
    #     then their vanished jobs ---
    for key, op in list(old_ops.items()):
        if key not in planned_alt_keys:
            session.delete(op)
            del old_ops[key]
    session.flush()
    for name, job in list(old_jobs.items()):
        if name not in wanted_jobs:
            session.delete(job)
            del old_jobs[name]
    session.flush()

    # --- upsert jobs ---
    for r in rows["Jobs"]:
        vals = {"release_time": int(r["release_time"]),
                "deadline": (int(r["deadline"])
                             if r.get("deadline") is not None else None),
                "priority": int(r["priority"]),
                "job_family_id": (fam[r["family"]]
                                  if r.get("family") is not None else None)}
        if r["name"] in old_jobs:
            for k, v in vals.items():
                setattr(old_jobs[r["name"]], k, v)
        else:
            nj = Job(instance_id=fid, name=r["name"], status="PENDING",
                     **vals)
            session.add(nj)
            old_jobs[r["name"]] = nj
    session.flush()

    # --- insert missing operations (kept ops retain ids ⇒ entries valid) ---
    for key in sorted(planned_alt_keys - set(old_ops)):
        jname, seq = key
        old_ops[key] = Operation(instance_id=fid,
                                 job_id=old_jobs[jname].id,
                                 sequence_number=seq, required_role_id=None)
        session.add(old_ops[key])
    session.flush()

    # --- reinsert replaced domains from sheets ---
    for r in rows["Materials"]:
        session.add(Material(
            instance_id=fid, sku=r["sku"],
            initial_stock=int(r["initial_stock"]),
            reorder_point=(int(r["reorder_point"])
                           if r.get("reorder_point") is not None else None)))
    session.flush()
    sku_ids = {m.sku: m.id for m in session.query(Material)
               .filter(Material.instance_id == fid)}

    for r in rows["Receipts"]:
        session.add(MaterialReceipt(
            instance_id=fid, material_id=sku_ids[r["sku"]],
            quantity=int(r["quantity"]),
            available_at=int(r["available_at"]),
            source=r.get("source") or "workbook"))
    for r in rows["Availability"]:
        session.add(WorkerAvailabilityWindow(
            instance_id=fid, worker_id=work[r["worker"]],
            available_from=int(r["available_from"]),
            available_until=int(r["available_until"]),
            source_pattern="workbook"))
    for r in rows["Setups"]:
        session.add(SetupTime(
            instance_id=fid, machine_id=mach[r["machine"]],
            from_family_id=(fam[r["from_family"]]
                            if r.get("from_family") is not None else None),
            to_family_id=(fam[r["to_family"]]
                          if r.get("to_family") is not None else None),
            setup_duration=int(r["setup_duration"]), source="workbook"))
    for r in rows["Alternatives"]:
        op = old_ops[(r["job"], int(r["op_sequence"]))]
        session.add(OperationMachineAlternative(
            instance_id=fid, operation_id=op.id,
            machine_id=mach[r["machine"]],
            processing_time=int(r["processing_time"])))
    for r in rows["Speeds"]:
        op = old_ops[(r["job"], int(r["op_sequence"]))]
        session.add(OperationMachineWorkerTime(
            instance_id=fid, operation_id=op.id,
            machine_id=mach[r["machine"]], worker_id=work[r["worker"]],
            processing_time=int(r["processing_time"])))
    for r in rows["BOM"]:
        op = old_ops[(r["job"], int(r["op_sequence"]))]
        session.add(OperationBom(instance_id=fid, operation_id=op.id,
                                 material_id=sku_ids[r["sku"]],
                                 quantity_required=int(r[
                                     "quantity_required"])))
    session.flush()
    return fork
```

Implementer cleanup note: hoist `SetupTime` and `Job` imports to the top-of-function import block; `_job_name` uses module-level `Job`. Delete the dead `for model in [Material, MaterialReceipt]: pass` fragment if any copy-paste residue remains.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/parsers/test_workbook.py -v`
Expected: full file PASS including round-trip, FK-validity, edit-landing, and no-instance-on-reject.

- [ ] **Step 5: Quick gate + commit**

```bash
uv run pytest -m "not mqtt and not slow" -q
git add coe/parsers/workbook.py tests/parsers/test_workbook.py
git commit -m "feat(parsers): workbook apply with round-trip identity property"
```

### Task B10: CLI wiring — `import workbook` + `template export`

**Files:**
- Modify: `coe/cli.py` (parser block after the existing `gass` subparser; runner functions near `_run_benchmark`; dispatch in `main()`)

**Interfaces:**
- Consumes: `export_workbook`, `apply_workbook`, `WorkbookRejected` from `coe.parsers.workbook`; `_instance_or_die`.
- Produces: `uv run python -m coe.cli import workbook --path P [--name N]` and `uv run python -m coe.cli template export --instance I [--out PATH]`.

- [ ] **Step 1: Add parsers**

In `build_parser()`, immediately after the `gass` subparser block:

```python
    wbcmd = sources.add_parser(
        "workbook", help="import a user-authored factory workbook (xlsx)")
    wbcmd.add_argument("--path", required=True)
    wbcmd.add_argument("--name", default=None,
                       help="name for the derived instance "
                            "(default: Meta!target_name)")
```

And before `return parser`, alongside `mq = ...`:

```python
    tpl = sub.add_parser("template",
                         help="factory workbook template utilities")
    tpl_sub = tpl.add_subparsers(dest="template_cmd", required=True)
    te = tpl_sub.add_parser("export")
    te.add_argument("--instance", required=True)
    te.add_argument("--out",
                    default="data/templates/factory_workbook.xlsx")
```

- [ ] **Step 2: Add runners**

Near `_run_benchmark`:

```python
def _run_import_workbook(args) -> None:
    from pathlib import Path

    from coe.db.session import session_scope
    from coe.parsers.workbook import WorkbookRejected, apply_workbook

    blob = Path(args.path).read_bytes()
    with session_scope() as session:
        # workbooks derive from the template scenario; a --parent flag
        # can be added later if ever needed (YAGNI today)
        parent = _instance_or_die(session, "factory_demo_01")
        try:
            fork = apply_workbook(session, parent, blob,
                                  new_name=args.name)
        except WorkbookRejected as exc:
            raise SystemExit(str(exc))
        print(f"workbook applied -> instance '{fork.name}'")


def _run_template_export(args) -> None:
    from pathlib import Path

    from coe.db.session import session_scope
    from coe.parsers.workbook import export_workbook

    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(export_workbook(session, inst.id))
    print(f"template written: {args.out} (from {args.instance})")
```

In `main()`'s dispatch chain add:

```python
    elif args.group == "import" and args.source == "workbook":
        _run_import_workbook(args)
    elif args.group == "template" and args.template_cmd == "export":
        _run_template_export(args)
```

(Place them consistent with how `import mk01` etc. currently dispatch.)

- [ ] **Step 3: Manual verification**

```bash
uv run python -m coe.cli template export --instance factory_demo_01
uv run python -m coe.cli import workbook \
    --path data/templates/factory_workbook.xlsx --name wb-roundtrip
uv run python -m coe.cli schedule show --instance wb-roundtrip | head -3
```

Expected: both commands print success lines; schedule show works on the derived instance (baseline entries copied).

- [ ] **Step 4: Commit**

```bash
git add coe/cli.py
git commit -m "feat(cli): workbook import + template export commands"
```

### Task B11: Configure page — download/upload controls with dry-run report

**Files:**
- Modify: `coe/dashboard/pages/configure.py`

**Interfaces:**
- Consumes: `export_workbook`, `validate_workbook`, `apply_workbook`, `WorkbookRejected`.
- Produces: workbook controls rendered above the tabs.

- [ ] **Step 1: Extend render()**

Add at the top of `render()` in `configure.py`, right after `instance = st.session_state["instance"]`:

```python
    from io import BytesIO

    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.parsers.workbook import (
        WorkbookRejected,
        apply_workbook,
        export_workbook,
        validate_workbook,
    )

    cdl, cup = st.columns([1, 2])
    with cdl:
        with session_scope() as session:
            row = (session.query(Instance)
                   .filter(Instance.name == instance).one())
            blob = export_workbook(session, row.id)
        st.download_button("⬇ Download factory workbook", data=blob,
                           file_name=f"{instance}.xlsx",
                           mime="application/vnd.openxmlformats-"
                                "officedocument.spreadsheetml.sheet")
    with cup:
        upload = st.file_uploader(
            "⬆ Upload edited workbook (creates a derived instance)",
            type=["xlsx"])
        if upload is not None and st.button("Validate & apply"):
            data_bytes = upload.getvalue()
            with session_scope() as session:
                parent = (session.query(Instance)
                          .filter(Instance.name == instance).one())
                errs = validate_workbook(data_bytes, session, parent)
            if errs:
                st.error(f"{len(errs)} problem(s) — nothing was written")
                for e in errs[:25]:
                    where = f" #{e['row']}" if e["row"] else ""
                    st.markdown(f"- `{e['sheet']}`{where}: "
                                f"{e['message']}")
            else:
                with session_scope() as session:
                    parent = (session.query(Instance)
                              .filter(Instance.name == instance).one())
                    fork = apply_workbook(session, parent, data_bytes)
                st.success(f"Created derived instance **{fork.name}** — "
                           "select it in the sidebar.")
    st.divider()
```

Note: `validate_workbook` runs inside its own committed read-only scope; `apply_workbook` re-validates internally before forking, so TOCTOU between preview and apply is harmless (worst case: apply raises `WorkbookRejected` after something changed — catch and display):

Wrap the apply call:

```python
                try:
                    fork = apply_workbook(session, parent, data_bytes)
                except WorkbookRejected as exc:
                    st.error(str(exc))
                    st.stop()
```

- [ ] **Step 2: Manual verification**

Export → edit one stock cell in LibreOffice/Excel → upload → expect success banner; select the new instance in sidebar and confirm Materials shows the bump and the fork badge names its parent. Upload the same file twice → second run creates `@hex8` suffixed sibling via collision path? No — name comes from Meta.target_name each time, so second attempt hits ForkError; confirm the error surfaces as a readable message rather than a traceback. If raw traceback appears, wrap `fork_instance` collisions: catch `ForkError` in the page and `st.error(...)` it.

- [ ] **Step 3: Commit**

```bash
git add coe/dashboard/pages/configure.py
git commit -m "feat(dash): configure page workbook download/upload with dry-run"
```

### Task B12: Event actions — machine toggle, worker absence, suspend job

**Files:**
- Create: `coe/dashboard/actions.py`
- Test: `tests/dashboard/test_actions.py`
- Modify: `coe/dashboard/pages/configure.py` (buttons), `coe/dashboard/pages/cockpit.py` placeholder wiring deferred to B13

**Interfaces:**
- Consumes: `publish_resource_event` (`coe/mqtt/edge_stub.py`), models, `resolve_reference_clock` (`coe/solver/payload_builder`).
- Produces:
  - `machine_down(instance_name: str, machine_name: str, at: int | None = None, reason: str = "dashboard") -> str` (returns message_id; publishes MQTT FAILURE)
  - `restore_machine(instance_name: str, machine_name: str, at: int | None = None) -> int` (closes open windows; returns minutes `at`)
  - `worker_absent(instance_name, worker_name, at=None, duration=None) -> str` / `worker_return(instance_name, worker_name, at=None) -> str`
  - `suspend_job(instance_name: str, job_name: str) -> None` — direct `jobs.status='BLOCKED'` flip (spec §5 Tier 2); raises `ValueError` on unknown job or already-BLOCKED
  - `resume_job(instance_name: str, job_name: str) -> None`

SUSPEND_JOB cannot ride MQTT ingest (`record_to_wire_payload` accepts only MACHINE/WORKER/MATERIAL kinds, `translate.py:83-103`) and must not require an LLM call for a button press — hence the direct status flip, which is precisely the state `payload_builder` reads for suspension memory (AGENTS.md).

- [ ] **Step 1: Write failing tests**

Create `tests/dashboard/test_actions.py`:

```python
import pytest

from coe.dashboard.actions import (
    resume_job,
    restore_machine,
    suspend_job,
)

pytestmark = pytest.mark.db


def test_suspend_and_resume_job(clean_db, session, demo_scenario):
    from sqlalchemy import text

    jid = session.execute(text(
        "SELECT id FROM jobs WHERE instance_id = :i ORDER BY name "
        "LIMIT 1"), {"i": demo_scenario}).scalar_one()
    suspend_job(session, "factory_demo_01", _job_name_by_id(session, jid))
    st = session.execute(text("SELECT status FROM jobs WHERE id = :j"),
                         {"j": jid}).scalar_one()
    assert st == "BLOCKED"
    resume_job(session, demo_scenario, _job_name_by_id(session, jid))
    st2 = session.execute(text("SELECT status FROM jobs WHERE id = :j"),
                          {"j": jid}).scalar_one()
    assert st2 == "PENDING"


def test_suspend_unknown_job_raises(clean_db, session, demo_scenario):
    with pytest.raises(ValueError):
        suspend_job(session, "factory_demo_01", "NOPE-JOB")


def test_double_suspend_raises(clean_db, session, demo_scenario):
    from sqlalchemy import text

    name = _job_name_by_id(session, session.execute(
        text("SELECT id FROM jobs WHERE instance_id = :i ORDER BY name "
             "LIMIT 1"), {"i": demo_scenario}).scalar_one())
    suspend_job(session, "factory_demo_01", name)
    with pytest.raises(ValueError):
        suspend_job(session, "factory_demo_01", name)


def test_restore_machine_closes_open_window(clean_db, session,
                                            demo_scenario):
    from datetime import datetime, timezone

    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine

    m = (session.query(Machine)
         .filter(Machine.instance_id == demo_scenario)
         .order_by(Machine.name).first())
    session.add(MachineDowntimeWindow(
        instance_id=demo_scenario, machine_id=m.id, downtime_from=10,
        downtime_until=None, reason="test"))
    session.flush()
    now = restore_machine(session, "factory_demo_01", m.name,
                              at=100)
    win = (session.query(MachineDowntimeWindow)
           .filter(MachineDowntimeWindow.instance_id == demo_scenario,
                   MachineDowntimeWindow.machine_id == m.id,
                   MachineDowntimeWindow.downtime_until.is_(None))
           .one_or_none())
    assert win is None
    assert now >= 10


def _job_name_by_id(session, jid):
    from coe.db.models.fjsp import Job

    return session.get(Job, jid).name


@pytest.mark.mqtt
def test_publish_failure_round_trips_broker():
    from coe.mqtt.edge_stub import publish_failure

    mid1 = publish_failure("factory_demo_01", "M1", occurred_at=5)
    mid2 = publish_failure("factory_demo_01", "M1", occurred_at=6)
    assert mid1 != mid2  # distinct presses must not dedup-suppress
```

Implementer notes: `suspend_job/resume_job/restore_machine` signatures above take `(session, instance_name, ...)` — adjust to whatever ordering you implement, but keep tests and implementation in sync. The `mqtt` marker requires Mosquitto (`docker compose up -d`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dashboard/test_actions.py -v -k "not publishers"`
Expected: FAIL — ImportError (`actions` missing).

- [ ] **Step 3: Implement actions.py**

Create `coe/dashboard/actions.py`:

```python
"""Cockpit event actions. Every function mirrors a CLI-equivalent code path."""
from sqlalchemy.orm import Session


def suspend_job(session: Session, instance_name: str, job_name: str) -> None:
    from coe.db.models.fjsp import Job
    from coe.db.models.provenance import Instance

    job = (session.query(Job)
           .join(Instance, Instance.id == Job.instance_id)
           .filter(Instance.name == instance_name, Job.name == job_name)
           .one_or_none())
    if job is None:
        raise ValueError(f"unknown job '{job_name}' on '{instance_name}'")
    if job.status == "BLOCKED":
        raise ValueError(f"job '{job_name}' is already suspended")
    job.status = "BLOCKED"
    session.flush()


def resume_job(session: Session, instance_name: str, job_name: str) -> None:
    from coe.db.models.fjsp import Job
    from coe.db.models.provenance import Instance

    job = (session.query(Job)
           .join(Instance, Instance.id == Job.instance_id)
           .filter(Instance.name == instance_name, Job.name == job_name)
           .one_or_none())
    if job is None:
        raise ValueError(f"unknown job '{job_name}' on '{instance_name}'")
    job.status = "PENDING"
    session.flush()


def restore_machine(session: Session, instance_name: str,
                    machine_name: str, at: int | None = None) -> int:
    """Close every open outage window; mirrors cli._run_restore."""
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine
    from coe.db.models.provenance import Instance
    from coe.solver.payload_builder import resolve_reference_clock

    inst = (session.query(Instance)
            .filter(Instance.name == instance_name).one())
    mach = (session.query(Machine)
            .filter(Machine.instance_id == inst.id,
                    Machine.name == machine_name).one_or_none())
    if mach is None:
        raise ValueError(f"unknown machine '{machine_name}'")
    now = resolve_reference_clock(session, inst.id, at)
    opens = (session.query(MachineDowntimeWindow)
             .filter(MachineDowntimeWindow.instance_id == inst.id,
                     MachineDowntimeWindow.machine_id == mach.id,
                     MachineDowntimeWindow.downtime_until.is_(None)).all())
    for w in opens:
        w.downtime_until = max(w.downtime_from + 1, now)
    mach.status = "ACTIVE"
    session.flush()
    return now


def machine_down(instance_name: str, machine_name: str,
                 at: int | None = None,
                 reason: str = "dashboard-toggle") -> str:
    """Publish a real MACHINE FAILURE so subscriber+listener react live.

    message_id stays auto-generated (uuid-based) so repeated toggles are
    distinct disruptions, not duplicate-suppressed replays.
    """
    from coe.mqtt.edge_stub import publish_failure

    return publish_failure(instance_name, machine_name,
                           occurred_at=at if at is not None else 512,
                           reason=reason)


def worker_absent(instance_name: str, worker_name: str,
                  at: int | None = None,
                  duration: int | None = None) -> str:
    from coe.mqtt.edge_stub import publish_resource_event

    return publish_resource_event(
        instance_name=instance_name, resource_kind="WORKER",
        resource_id=worker_name, event_type="WORKER_ABSENT",
        occurred_at=at if at is not None else 480,
        duration=duration)


def worker_return(instance_name: str, worker_name: str,
                  at: int | None = None) -> str:
    from coe.mqtt.edge_stub import publish_resource_event

    return publish_resource_event(
        instance_name=instance_name, resource_kind="WORKER",
        resource_id=worker_name, event_type="RETURN",
        occurred_at=at if at is not None else 480)


def material_shortage(instance_name: str, sku: str,
                      at: int | None = None) -> str:
    """MATERIAL telemetry (spec §6 map row); mirrors cli mqtt test-shortage."""
    from coe.mqtt.edge_stub import publish_resource_event

    return publish_resource_event(
        instance_name=instance_name, resource_kind="MATERIAL",
        resource_id=sku, event_type="MATERIAL_SHORTAGE",
        occurred_at=at if at is not None else 300)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dashboard/test_actions.py -v`
Expected: db tests PASS; publisher smoke PASS with broker up (skip otherwise: it is `mqtt`-marked so the quick gate skips it).

- [ ] **Step 5: Wire buttons into Configure page**

In `configure.py`, extend the Machines tab body:

```python
        with tab_mach:
            machines = data.machines_overview(session, iid)
            st.dataframe(machines)
            mcol1, mcol2 = st.columns(2)
            with mcol1:
                down_m = st.selectbox("Machine ↓", [m["name"] for m in machines],
                                      key="down-machine")
                if st.button("⛔ Take DOWN", key="btn-down"):
                    try:
                        actions.machine_down(instance, down_m)
                        st.success(f"FAILURE published for {down_m}; "
                                   "listener will recover.")
                    except RuntimeError as exc:
                        st.error(f"broker unreachable: {exc}")
            with mcol2:
                up_m = st.selectbox("Machine ↑", [m["name"] for m in machines],
                                    key="up-machine")
                if st.button("✅ Restore", key="btn-up"):
                    try:
                        t = actions.restore_machine(session, instance, up_m)
                        st.success(f"{up_m} restored at t={t}")
                    except ValueError as exc:
                        st.error(str(exc))
```

Workers tab gains absence/return selects; Jobs tab gains Suspend/Resume selects; Materials tab gains a SKU select with a "⚠ Declare shortage" button — same pattern (`st.selectbox` + `st.button` calling `actions.worker_absent/worker_return/suspend_job/resume_job/material_shortage`, errors via `st.error(ValueError)`). Import `from coe.dashboard import actions` at top of the page module.

- [ ] **Step 6: Quick gate + commit**

```bash
uv run pytest -m "not mqtt and not slow" -q
git add coe/dashboard/actions.py tests/dashboard/test_actions.py coe/dashboard/pages/configure.py
git commit -m "feat(dash): disruption action buttons over real MQTT/service paths"
```

### Task B13: Cockpit chat — blocking recovery round-trip

**Files:**
- Create: `coe/dashboard/pages/cockpit.py`

**Interfaces:**
- Consumes: `execute_recovery(instance_name, *, trigger, narrative, client, ...)`; `require_llm_config`; `ScheduleExplanation` for post-commit prose.
- Produces: working chat that runs the full graph synchronously (streaming polish is Task C14).

- [ ] **Step 1: Implement blocking chat**

Create `coe/dashboard/pages/cockpit.py`:

```python
"""Cockpit chat: narrative-driven agentic recovery (blocking variant)."""
import streamlit as st

from coe.dashboard import data


def _llm_ready() -> bool:
    from coe.agents.llm_client import require_llm_config
    from coe.config import get_settings

    try:
        require_llm_config(get_settings())
        return True
    except RuntimeError as exc:
        st.warning(f"LLM not configured — chat disabled: {exc}")
        return False


def render() -> None:
    instance = st.session_state["instance"]
    if "chat" not in st.session_state:
        st.session_state["chat"] = []

    for msg in st.session_state["chat"]:
        with st.chat_message(msg["role"]):
            st.write(msg["text"])

    prompt = st.chat_input(
        f"Describe a disruption for {instance}…",
        disabled=not _llm_ready())
    if prompt is None:
        return

    st.session_state["chat"].append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    from coe.agents.graph import execute_recovery

    with st.chat_message("assistant"):
        with st.status("Running recovery pipeline…", expanded=False) as stat:
            st.write("translating narrative → investigating agents → "
                     "strategy loop → CP-SAT solve (~180 s floor) → gate → "
                     "commit → verify")
            outcome = execute_recovery(instance, trigger="CLI",
                                       narrative=prompt)
            state = outcome["state"]
            sol = state.solution or {}
            stat.update(label=f"Recovery {outcome['status']}",
                        state="complete" if outcome["status"] == "COMMITTED"
                        else "error")

        SOLVER_TONE = {"OPTIMAL": "🟢", "FEASIBLE": "🟢",
                       "INFEASIBLE": "🔴", "UNKNOWN": "🟠"}
        tone = SOLVER_TONE.get(sol.get("status"), "⚪")
        summary = (f"run **{outcome['status']}** · solver "
                   f"{tone} **{sol.get('status')}** · makespan "
                   f"**{sol.get('makespan')}** · version "
                   f"`{state.committed_version_id}` · run "
                   f"`{outcome['run_id']}`")
        st.markdown(summary)
        if sol.get("status") == "UNKNOWN":
            st.caption("UNKNOWN = solve budget starved — rerun with a "
                       "higher --time-limit; this is never a "
                       "material-conflict verdict.")
        if outcome["status"] == "COMMITTED":
            with st.expander("Why this plan?"):
                from coe.db.models.recovery import ScheduleExplanation
                from coe.db.session import session_scope

                with session_scope() as session:
                    row = (session.query(ScheduleExplanation)
                           .filter(ScheduleExplanation.version_id ==
                                   state.committed_version_id)
                           .one_or_none())
                st.write(row.rationale if row else
                         "(explanation node returned no text)")

    st.session_state["chat"].append({"role": "assistant",
                                     "text": summary})
```

- [ ] **Step 2: Manual verification (LLM key required)**

With provider env set (`cp .env.example .env` per repo template):

```bash
uv run python -m coe.cli dashboard
```

Cockpit → type *"M3 broke down at minute 600"*. Expected: user bubble appears; status box shows pipeline stages; after solve completes (minutes), assistant summary renders COMMITTED + explanation expandable. Runs page then lists the run with node timings.

Without key: input disabled with warning. Verify that too.

- [ ] **Step 3: Commit**

```bash
git add coe/dashboard/pages/cockpit.py
git commit -m "feat(dash): blocking chat recovery in cockpit"
git tag milestone-B-config-actions-chat
```

### Task C14: `execute_recovery_streaming` — node-boundary generator

**Files:**
- Modify: `coe/agents/graph.py` (additive sibling of `execute_recovery`; no existing function changes)

**Interfaces:**
- Consumes: `build_graph`, `InstanceRunLock`, `record_run`, `write_proposals`, `_terminal_status` — all already in `graph.py`.
- Produces:
  ```python
  def execute_recovery_streaming(instance_name: str, *, trigger: str,
                                 narrative: str | None = None,
                                 record: dict | None = None,
                                 source_message_id: str | None = None,
                                 reference_clock: int | None = None,
                                 client=None, lock_wait: float | None = None,
                                 max_retries: int | None = None):
      """Generator yielding {'node': name} per boundary, then a final
      {'status':…, 'state': RecoveryState, 'run_id': int} dict."""
  ```
  Recording semantics IDENTICAL to `execute_recovery` (one run row, proposals flushed on failure paths) — spec §2 forbids divergent behavior between CLI and cockpit paths.

- [ ] **Step 1: Write failing test**

Append to `tests/agents/test_graph.py` (file exists from Phase 3; reuse its fake-client fixtures — check its conftest for the injected client fixture name and use it):

```python
def test_streaming_yields_nodes_then_outcome(clean_db, demo_scenario,
                                             fake_llm_client):
    import pytest as _pytest

    from coe.agents.graph import execute_recovery_streaming

    gen = execute_recovery_streaming(
        "factory_demo_01", trigger="CLI",
        narrative="M1 failed", client=fake_llm_client)
    nodes = []
    final = None
    for item in gen:
        if "node" in item:
            nodes.append(item["node"])
        else:
            final = item
    assert nodes, "no node boundaries streamed"
    assert nodes[0] == "entry" and nodes[-1] == "explain_node"
    assert final is not None and "run_id" in final
    # recording parity with execute_recovery:
    from sqlalchemy import text

    from coe.db.session import session_scope
    with session_scope() as s:
        n = s.execute(text("SELECT count(*) FROM recovery_runs"),
                      {}).scalar_one()
    assert n == 1
```

Implementer note: adapt `fake_llm_client` to the actual Phase-3 test-double fixture name found in `tests/agents/` (grep for the client fixture used by existing `execute_recovery` tests). If those tests build clients inline rather than via fixture, copy their minimal constructor here.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_graph.py -v -k streaming`
Expected: FAIL — ImportError (`execute_recovery_streaming` missing).

- [ ] **Step 3: Implement**

Append to `coe/agents/graph.py`:

```python
def execute_recovery_streaming(instance_name: str, *, trigger: str,
                               narrative: str | None = None,
                               record: dict | None = None,
                               source_message_id: str | None = None,
                               reference_clock: int | None = None,
                               client=None, lock_wait: float | None = None,
                               max_retries: int | None = None):
    """Streaming twin of execute_recovery (dashboard design §4 Cockpit).

    Yields {'node': <name>} at each LangGraph update boundary, then a
    single terminal dict with the same shape execute_recovery returns.
    Recording semantics are identical: exactly one recovery_runs row,
    proposals flushed even on failure paths.
    """
    import time as _time

    started = _time.time()
    if client is None:
        from coe.agents.llm_client import make_llm_client

        client = make_llm_client()

    initial = RecoveryState(
        instance_name=instance_name, trigger=trigger,
        narrative=narrative or "", disruption_record=record,
        source_message_id=source_message_id,
        reference_clock=reference_clock)

    app = build_graph(client, max_retries=max_retries)
    status = "COMMITTED"
    final_state = initial
    try:
        with InstanceRunLock(instance_name, wait_seconds=lock_wait):
            for chunk in app.stream(initial, stream_mode="updates"):
                for node in chunk:
                    yield {"node": node}
                merged = initial.model_copy()
                for node, upd in chunk.items():
                    if upd is not None:
                        merged = merged.model_copy(update=dict(upd))
                final_state = merged
    except TranslationFailed as exc:
        final_state = initial
        status = "TRANSLATION_FAILED"
        record_json = {"narrative": exc.narrative,
                       "validation_error": exc.error}
        if source_message_id is not None:
            record_json["message_id"] = source_message_id
    else:
        status = _terminal_status(final_state)
        rec = final_state.disruption_record or {}
        record_json = dict(rec)
        if source_message_id is not None:
            record_json["message_id"] = source_message_id

    run_id = record_run(
        instance_name, trigger=trigger, status=status,
        disruption_record_json=record_json, started_at=started,
        finished_at=_time.time(),
        final_status_version_id=getattr(final_state,
                                        "committed_version_id", None))
    verdicts = getattr(final_state, "round_verdicts", [])
    if verdicts:
        write_proposals(instance_name, run_id, verdicts)
    yield {"status": status, "state": final_state, "run_id": run_id}
```

Implementer caveats: (a) verify `RecoveryState` supports `model_copy(update=…)` merging of langgraph update dicts — if updates arrive as full state replacements per channel instead, accumulate via `RecoveryState.model_validate(upd)` on the last non-None update per node; match whatever `app.invoke` semantics produce. (b) The generator holds the advisory lock across consumer pauses; Streamlit consumes eagerly so this is safe in practice, and matches execute_recovery's lock scope.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_graph.py -v -k streaming`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/graph.py tests/agents/test_graph.py
git commit -m "feat(agents): streaming recovery twin with identical recording"
```

### Task C15: Cockpit live decision feed

**Files:**
- Modify: `coe/dashboard/pages/cockpit.py` (replace blocking call in chat handler)

**Interfaces:**
- Consumes: `execute_recovery_streaming`.
- Produces: node-by-node feed lines; friendly labels map.

- [ ] **Step 1: Replace the status block**

In `cockpit.py`, swap the `with st.status(...)` block for:

```python
    NODE_LABELS = {
        "entry": "📥 accepted request",
        "translate": "🧠 translating narrative",
        "ingest": "🔌 ingesting disruption event",
        "machine_agent": "🏭 investigating machine constraints",
        "production_agent": "⚙️ investigating production chain",
        "inventory_agent": "📦 investigating material stock",
        "worker_agent": "👷 investigating worker availability",
        "strategy": "♟️ strategy round",
        "manager_compile": "🧩 compiling solver payload",
        "solve_node": "🧮 running CP-SAT solver (~180 s floor)…",
        "gate_node": "🛂 safety gate",
        "commit_node": "💾 committing schedule version",
        "verify_node": "✅ verifying invariants",
        "explain_node": "💬 composing explanation",
    }
    feed = st.container()
    outcome = None
    with feed:
        for item in execute_recovery_streaming(instance, trigger="CLI",
                                               narrative=prompt):
            if "node" not in item:
                outcome = item
                continue
            label = NODE_LABELS.get(item["node"], item["node"])
            feed.write(label)
```

Then render summary/explanation exactly as before but sourced from `outcome` instead of `outcome = execute_recovery(...)`.

- [ ] **Step 2: Manual verification**

Same LLM-keyed run as B13. Expected: lines tick in progressively (translate → agents → strategy …), solve line persists through the long solve, terminal summary renders. Compare Runs page: one run recorded, timings present.

- [ ] **Step 3: Commit**

```bash
git add coe/dashboard/pages/cockpit.py
git commit -m "feat(dash): live decision feed via streaming recovery"
```

### Task C16: Schedule diff animation on COMMIT

**Files:**
- Modify: `coe/dashboard/pages/cockpit.py`
- Create: `coe/dashboard/diff.py`
- Test: `tests/dashboard/test_diff.py`

**Interfaces:**
- Consumes: `data.active_schedule`.
- Produces: `schedule_frames(before_entries: list[dict], after_entries: list[dict]) -> list[plotly.graph_objects.Figure]` — one frame per operation that moved, animating old→new position; operations unchanged appear in every frame.

- [ ] **Step 1: Write failing test**

Create `tests/dashboard/test_diff.py`:

```python
from coe.dashboard.diff import schedule_frames


def _e(job, seq, machine, start, end, worker=None):
    return {"job_name": job, "sequence_number": seq,
            "machine_name": machine, "worker_name": worker,
            "start_time": start, "end_time": end}


def test_moved_operation_produces_intermediate_frame():
    before = [_e("J1", 1, "M1", 0, 10), _e("J2", 1, "M2", 10, 30)]
    after = [_e("J1", 1, "M3", 0, 10), _e("J2", 1, "M2", 10, 30)]
    frames = schedule_frames(before, after)
    assert len(frames) >= 2          # at least an intermediate + final


def test_identical_schedules_yield_single_final_frame():
    entries = [_e("J1", 1, "M1", 0, 10)]
    assert len(schedule_frames(entries, entries)) == 1


def test_added_and_removed_ops_render():
    before = [_e("J1", 1, "M1", 0, 10)]
    after = [_e("J1", 1, "M1", 0, 10), _e("J9", 1, "M2", 5, 15)]
    frames = schedule_frames(before, after)
    assert frames                    # final frame contains J9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dashboard/test_diff.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement diff.py**

Create `coe/dashboard/diff.py`:

```python
"""Before→after schedule animation frames (dashboard design §4 Cockpit)."""
import plotly.graph_objects as go


def _key(e: dict) -> tuple:
    return (e["job_name"], e["sequence_number"])


def _bar(e: dict, opacity: float) -> dict:
    return {"x": [e["machine_name"]],
            "base": [e["start_time"]],
            "x1": [max(e["end_time"], e["start_time"] + 1)],
            "label": f"{e['job_name']}#{e['sequence_number']}",
            "worker": e.get("worker_name") or "-",
            "opacity": opacity}


def schedule_frames(before: list[dict],
                    after: list[dict]) -> list[go.Figure]:
    b_map = {_key(e): e for e in before}
    a_map = {_key(e): e for e in after}
    steps: list[list[dict]] = []          # each step: list of bars to draw
    for k, a in a_map.items():
        b = b_map.get(k)
        if b is None:
            steps.append([_bar(a, 1.0)])                       # added
        elif (b["machine_name"], b["start_time"], b["end_time"]) \
                != (a["machine_name"], a["start_time"], a["end_time"]):
            steps.append([_bar(b, 0.35), _bar(a, 0.8)])        # ghost→new
    static = [_bar(b_map[k], 1.0) for k in b_map
              if k in a_map and (
                  b_map[k]["machine_name"],
                  b_map[k]["start_time"],
                  b_map[k]["end_time"]) == (
                      a_map[k]["machine_name"],
                      a_map[k]["start_time"],
                      a_map[k]["end_time"])]
    figures = []
    for i, moving in enumerate(steps + [[]]):
        bars = static + ([] if i == len(steps) else moving)
        fig = go.Figure()
        for bar in bars:
            fig.add_bar(y=bar["x"], base=bar["base"], x=bar["x1"],
                        orientation="h", name=bar["label"],
                        text=bar["worker"], opacity=bar["opacity"])
        tag = ("final" if i >= len(steps)
               else f"move {i + 1}/{len(steps)}")
        fig.update_layout(barmode="overlay", title=f"{tag}",
                          xaxis_title="minutes",
                          showlegend=len(bars) <= 12)
        figures.append(fig)
    return figures or []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dashboard/test_diff.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Wire into cockpit**

The chat handler captures the pre-recovery active schedule BEFORE invoking streaming, then animates against a fresh post-commit load. Final wiring inside the prompt-handling path:

```python
        # --- capture BEFORE state prior to running recovery ---
        from sqlalchemy import text as sqltext

        from coe.db.session import session_scope

        from coe.dashboard import data as ddata
        from coe.dashboard.diff import schedule_frames

        with session_scope() as s0:
            irow = s0.execute(
                sqltext("SELECT id FROM instances WHERE name = :n"),
                {"n": instance}).one()
            iid0 = irow.id
            before_entries = (
                ddata.active_schedule(s0, iid0)
                or {"entries": []})["entries"]

    outcome = None
    feed = st.container()
    with feed:
        for item in execute_recovery_streaming(instance, trigger="CLI",
                                               narrative=prompt):
            if "node" not in item:
                outcome = item
                continue
            feed.write(NODE_LABELS.get(item["node"], item["node"]))

    if outcome is not None and outcome["status"] == "COMMITTED":
        with session_scope() as s1:
            after_entries = ddata.active_schedule(s1, iid0)["entries"]
        figs = schedule_frames(before_entries, after_entries)
        st.plotly_chart(figs[-1], use_container_width=True)   # final layout
        if len(figs) > 1:
            idx = st.slider("Recovery move", 1, len(figs), len(figs))
            st.plotly_chart(figs[idx - 1], use_container_width=True)
```

(The slider walks intermediate ghost→new frames; the first chart pins the final layout. Keep the existing summary/explanation rendering after this block.)

- [ ] **Step 6: Commit**

```bash
git add coe/dashboard/diff.py tests/dashboard/test_diff.py coe/dashboard/pages/cockpit.py
git commit -m "feat(dash): schedule move animation on commit"

### Task C17: Live MQTT events rail

**Files:**
- Create: `coe/dashboard/rail.py`
- Modify: `coe/dashboard/app.py` (render rail under the sidebar)

**Interfaces:**
- Consumes: paho-mqtt directly — a PASSIVE subscriber (display only; ingestion remains the real `run_subscriber` process's job so the rail never double-ingests).
- Produces: `start_rail() -> None` idempotent; buffers last 50 raw events into `st.session_state` via a module-level deque guarded by a lock.

- [ ] **Step 1: Implement rail.py**

Create `coe/dashboard/rail.py`:

```python
"""Passive MQTT observer for the sidebar rail (dashboard design §4).

Deliberately does NOT ingest: coe.mqtt.subscriber owns ingestion. This
client only mirrors wire traffic for display.
"""
import json
import threading
from collections import deque

_BUFFER = deque(maxlen=50)
_LOCK = threading.Lock()
_STARTED = False


def snapshot() -> list[dict]:
    with _LOCK:
        return list(_BUFFER)


def _on_message(client, userdata, msg):
    try:
        evt = json.loads(msg.payload)
    except json.JSONDecodeError:
        return
    with _LOCK:
        _BUFFER.append({"topic": msg.topic, "event_type":
                        evt.get("event_type"), "resource":
                        (evt.get("machine_id") or evt.get("worker_id")
                         or evt.get("material_sku")),
                        "occurred_at": evt.get("occurred_at")})


def start_rail() -> None:
    global _STARTED
    if _STARTED:
        return
    from coe.config import get_settings

    s = get_settings()
    topic = "factory/+/+/+/events"
    thread = threading.Thread(target=_loop, args=(s.mqtt_host, s.mqtt_port,
                                                  topic), daemon=True)
    thread.start()
    _STARTED = True


def _loop(host: str, port: int, topic: str) -> None:
    import time

    import paho.mqtt.client as mqtt

    while True:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client.on_message = _on_message
            client.connect(host, port)
            client.subscribe(topic)
            client.loop_forever()
        except OSError:
            time.sleep(2)   # broker down: retry quietly


def render_rail() -> None:
    import streamlit as st

    st.sidebar.divider()
    st.sidebar.caption("🔴 Live factory events")
    for e in reversed(snapshot()[-10:]):
        st.sidebar.markdown(
            f"`t={e['occurred_at']}` **{e['event_type']}** "
            f"· {e['resource']}")
```

- [ ] **Step 2: Wire into app shell**

In `app.py` `main()`, after the instance selector block:

```python
    from coe.dashboard.rail import render_rail, start_rail

    start_rail()
    render_rail()
```

And in `configure.py`'s Machines DOWN button handler, no change needed — published events appear in the rail automatically.

Add a 1 Hz refresh fragment at the end of `app.main()`:

```python
    import streamlit as st

    @st.fragment(run_every=1.0)
    def _tick():
        pass                      # rerun redraws sidebar rail buffer

    _tick()
```

If `run_every` fragments re-execute page scripts undesirably in your Streamlit version, drop the fragment — the rail still updates on every natural rerun (button presses, chat input), which suffices for the demo.

- [ ] **Step 3: Manual verification**

With Mosquitto up: press ⛔ Take DOWN on M2 → within ~1 s rail shows `FAILURE · M2`; listener log shows auto-recovery start. Publish nothing → rail stays quiet.

- [ ] **Step 4: Commit**

```bash
git add coe/dashboard/rail.py coe/dashboard/app.py
git commit -m "feat(dash): passive live event rail"
```

### Task C18: Smoke test, demo script, milestone close

**Files:**
- Create: `tests/dashboard/test_app_smoke.py`
- Create: `docs/dashboard-demo.md`
- Modify: `README.md` (one-line pointer, matching existing tone)

**Interfaces:**
- Consumes: `streamlit.testing.v1.AppTest`; `clean_db`, `demo_scenario`.

- [ ] **Step 1: Write the smoke test**

Create `tests/dashboard/test_app_smoke.py`:

```python
import pytest

pytestmark = pytest.mark.db


def test_app_shell_renders_with_instances(clean_db, demo_scenario):
    from streamlit.testing.v1 import AppTest

    from coe.dashboard.app import main as app_main

    # AppTest executes the script file, not arbitrary callables; wrap:
    script = """
import sys
sys.argv = ["streamlit", "run", "smoke"]
from coe.dashboard.app import main
main()
"""
    at = AppTest.from_string(script)
    at.run(timeout=30)
    assert not at.exception
    radios = at.radio[0]
    assert "Configure" in radios.options
    assert at.session_state["instance"] == "factory_demo_01"
```

Implementer note: if `AppTest.from_string` is unavailable in the pinned Streamlit, switch to `AppTest.from_file("coe/dashboard/app.py")` with `st.set_page_config` guarded — adapt to whichever constructor exists and keep the assertions identical.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/dashboard/test_app_smoke.py -v`
Expected: PASS (no exception, instance preselected). If headless-config warnings appear, they are non-fatal.

- [ ] **Step 3: Write the demo script**

Create `docs/dashboard-demo.md`:

```markdown
# Cockpit Demo Script (~5 minutes)

Prereqs (once): docker compose up -d · import mk01 + gass + hutter ·
scenario build --name factory_demo_01 --seed 42 · solve baseline ·
`.env` with an LLM key · two terminals.

Terminal A: `uv run python -m coe.cli mqtt listen`
Terminal B: `uv run python -m coe.cli dashboard`

1. **Configure tour** — Schedule Gantt, Materials expanders, Workers,
   Jobs/day. Point out the fork badge discipline: template untouched.
2. **Workbook edit** — Download workbook, bump one material stock by 40,
   upload. Show the dry-run error path first: set quantity to -5 in a
   copy and upload → row-level rejection, nothing written. Then apply
   the good file and switch to the derived instance.
3. **Kill a machine** — Configure → Machines → take M3 DOWN.
   Watch: rail ticks FAILURE · M3 → Terminal A logs recovery start →
   cockpit Runs page fills with node timings.
4. **Chat recovery** — Cockpit: “M5 broke down around minute 700”.
   Decision feed narrates agents → CP-SAT spinner → COMMITTED summary →
   move slider animates schedule diff → “Why this plan?” explanation.
5. **Honest failure (optional)** — Suspend half the jobs via chat or
   buttons until INFEASIBLE; show red verdict + agent explanation.
```

Append one line to `README.md` commands section:

```markdown
uv run python -m coe.cli dashboard      # streamlit cockpit (docs/dashboard-demo.md)
```

- [ ] **Step 4: Full gates**

```bash
docker compose up -d
uv run pytest -q                       # full suite incl mqtt+slow pins
```

Expected: green across the suite (~330 tests now). Fix any regressions before tagging.

- [ ] **Step 5: Commit + tag**

```bash
git add tests/dashboard/test_app_smoke.py docs/dashboard-demo.md README.md
git commit -m "test(dash): app smoke + demo script; milestone C complete"
git tag milestone-C-live-cockpit
```
