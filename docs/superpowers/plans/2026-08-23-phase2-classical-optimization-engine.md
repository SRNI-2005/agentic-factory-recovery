# Phase 2: Classical Optimization Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic CP-SAT solver pipeline (payload_builder → solver.engine → committer) that computes baseline and recovery schedules for Phase 1 instances and commits them as versioned database state.

**Architecture:** Three modules with strict one-way data flow. `coe/solver/payload_builder.py` reads the Phase 1 schema and emits a self-contained JSON payload; `coe/solver/engine.py` is a pure function (JSON in → solution JSON out, no DB, no LLM); `coe/solver/committer.py` writes solutions into new versioned tables inside one transaction. Recovery is CLI-triggered only; failures are injected through the Phase 1 ingestion path.

**Tech Stack:** Python 3.12, `uv`, Google OR-Tools (`ortools` CP-SAT), SQLAlchemy 2.0, Alembic (authoritative DDL), psycopg3, pytest.

## Global Constraints

These apply to every task. Copied from AGENTS.md + specs; specs are the source of truth when in doubt.

- Use `uv` exclusively. Never pip, never system Python. Run everything from repo root.
- Docker compose stack MUST be up before any DB test: `docker compose up -d` (TimescaleDB :5432, Mosquitto :1883).
- TDD for all work: failing test first, verify it fails, minimal implementation, verify pass, commit.
- **Alembic is authoritative DDL — `Base.metadata.create_all` is forbidden anywhere.**
- Determinism: every query feeding iteration order or arithmetic that reaches output gets an explicit `ORDER BY`. Solver runs with `num_search_workers=1`. Same inputs + seed ⇒ byte-identical payload and solution.
- All time is integer minutes. No floats in scheduling math except the normalized objective (floats confined to objective computation).
- Every table row is instance-scoped (`instance_id` FK discipline; no cross-instance joins).
- psycopg3 raises CHECK violations at `execute()`, not `commit()` — structure try/excepts accordingly.
- Testing markers: `db` tests need TimescaleDB; add `benchmark` marker usage per spec §11.
- Spec sections referenced below are `docs/superpowers/specs/2026-08-21-phase2-classical-optimization-engine-design.md` ("the spec"), including both 2026-08-23 amendments (worker durations; consistency fixes).
- Payload identifiers: machines/jobs/workers use DB `name` columns (`M0`–`M7`, `J1`–`J30`, `W1`+); operations synthesize `"{job.name}-O{sequence_number}"`; materials use `sku`.
- Worker durations come from `operation_machine_worker_times` (authoritative). Empty map ⇒ machine-level `processing_time`, no worker required (MK01 path).
- Weight resolution follows spec §9.1: env < CLI < (Phase 3 presets) < priority-derived map (fills absent entries only).
- Commit messages: conventional commits (`feat(solver): ...`, `test(solver): ...`, etc.).

## File Structure

New files (all created by this plan):

```text
coe/solver/__init__.py                 # package init
coe/solver/identifier.py               # op id synthesis + parsing helpers
coe/solver/horizon.py                  # shared conservative-horizon computation (§6.7)
coe/solver/windows.py                  # interval union/complement/clipping helpers
coe/solver/materials_check.py          # pre-solve material gatekeeping (§7)
coe/solver/payload_builder.py          # DB → payload JSON (§3.1)
coe/solver/engine.py                   # pure CP-SAT solver (§3.2, §6)
coe/solver/invariants.py               # solution invariant checks (shared gate logic)
coe/solver/committer.py                # solution JSON → DB transaction (§3.3, §8)
coe/cli.py                             # MODIFY: solve/machine/schedule command groups
coe/config.py                          # MODIFY: SOLVER_* settings
tests/solver/__init__.py
tests/solver/conftest.py               # solver fixtures (demo scenario, mk01, clock)
tests/solver/fixtures/*.json           # hand-crafted Tier 2b payloads (committed)
tests/solver/test_identifier.py
tests/solver/test_windows.py
tests/solver/test_horizon.py
tests/solver/test_materials_check.py
tests/solver/test_payload_baseline.py      # builder: topology/setups/workers/downtime/weights
tests/solver/test_payload_recovery.py      # builder: freeze/truncate/clip/cascade/seed families
tests/solver/test_engine_constraints.py    # Tier 2b table, fixture-driven, no DB
tests/solver/test_engine_properties.py     # determinism, time limit, infeasible, empty-pending
tests/solver/test_committer.py             # versioning, mirroring, rollback floor, tx atomicity
tests/solver/test_cli_solve.py             # CLI wiring incl. injection audit (Tier 3)
tests/solver/test_benchmark_mk01.py        # Tier 1: makespan == 40 (@pytest.mark.benchmark)
alembic/versions/<rev>_schedule_tables.py # migration #6: schedule_versions, schedule_entries, active_schedule view
```

Existing files modified:

```text
pyproject.toml       # add ortools dep + "benchmark" pytest marker
coe/cli.py           # add solve / machine / schedule groups
coe/config.py        # add six SOLVER_* settings
```

Module boundary rules:

- `engine.py` imports NOTHING from `coe.db` or `coe.cli`. Violations fail review.
- `payload_builder.py` and `committer.py` own ALL DB access; they never import each other.
- `invariants.py` is pure (payload+solution in → list of violations out); committer uses it pre-commit; Phase 3 reuses it for its gate/verifier.
- `windows.py`, `horizon.py`, `identifier.py`, `materials_check.py` are pure helpers used by builder and/or engine; no DB access.

---

## Part 1 — Migration #6: schedule tables + active_schedule view

### Task 1: Add ortools dependency and benchmark marker

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `ortools` importable as `from ortools.sat.python.cp_model import CpModel`; pytest marker `benchmark` registered.

- [ ] **Step 1: Add dependency and marker**

In `pyproject.toml`, add `"ortools>=9.10"` to `[project] dependencies` (after `"openpyxl",`). In `[tool.pytest.ini_options] markers` list, append `"benchmark: Tier 1 MK01 optimality validation (spec §11)"`.

- [ ] **Step 2: Verify install**

Run: `uv sync && uv run python -c "from ortools.sat.python.cp_model import CpModel; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(solver): add ortools dependency and benchmark marker"
```

### Task 2: Schedule-table models

**Files:**
- Create: `coe/db/models/schedule.py`
- Modify: `coe/db/models/__init__.py` (import the new module so Alembic autogenerate sees it)

**Interfaces:**
- Produces:
  - `ScheduleVersion` model: columns exactly per spec §4 `schedule_versions` — `id` (PK), `instance_id` (FK, indexed), `version_number` (int), `schedule_type` ('BASELINE'|'RECOVERY'), `solver_status` ('OPTIMAL'|'FEASIBLE'|'INFEASIBLE'), `objective_value` (Float — normalized weighted sum, a small ratio), `makespan` (int, minutes), `total_tardiness` (int, minutes), `alpha_weight` (float), `beta_weight` (float), `time_limit_seconds` (int), `solve_duration_seconds` (float), `failed_machine_ids` (JSONB, nullable), `parent_version_id` (self-FK, nullable), `rolled_back` (bool, default False), `committed_at` (DateTime tz, `server_default=func.now()` — house style from `provenance.py`), `payload_hash` (String(64)), `payload_json` (JSONB). Table args: `CheckConstraint("schedule_type IN ('BASELINE','RECOVERY')")`, `CheckConstraint("solver_status IN ('OPTIMAL','FEASIBLE','INFEASIBLE')")`, `UniqueConstraint("instance_id","version_number", name="uq_schedule_versions_instance_version")`.
  - `ScheduleEntry` model: `id` PK, `instance_id` FK indexed, `version_id` FK→`schedule_versions.id` indexed, `operation_id` FK→operations.id, `machine_id` FK→machines.id, `worker_id` (nullable FK), `start_time` int, `end_time` int, `processing_time` int (the effective duration: assigned worker's duration per Amendment 1, else machine-level), `setup_time` int default 0, `is_frozen` bool default False, `status` ('SCHEDULED'|'FROZEN') with CHECK.
  - Composite FK note: plain single-column FKs suffice (all tables share integer surrogate ids); instance-scoping enforced app-side like existing models.
- Model code must use `server_default=func.now()` for `committed_at`, matching `coe/db/models/provenance.py`.

- [ ] **Step 1: Write failing model test**

Create `tests/db/test_schedule_models.py`:

```python
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


def _commit_minimal(session, instance_id: int) -> int:
    v = ScheduleVersion(
        instance_id=instance_id, version_number=1, schedule_type="BASELINE",
        solver_status="OPTIMAL", objective_value=0, makespan=0,
        total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
        time_limit_seconds=60, solve_duration_seconds=0.0,
        failed_machine_ids=None, parent_version_id=None,
        rolled_back=False, payload_hash="0" * 64, payload_json={},
    )
    session.add(v)
    session.flush()
    return v.id


def test_version_roundtrip(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    with session_scope() as session:
        inst = Instance(name="t-sched", source_name="synthetic")
        session.add(inst)
        session.flush()
        vid = _commit_minimal(session, inst.id)
        session.add(ScheduleEntry(
            instance_id=inst.id, version_id=vid, operation_id=None, machine_id=None,
            worker_id=None, start_time=0, end_time=5, processing_time=5,
            setup_time=0, is_frozen=False, status="SCHEDULED",
        ))
    ...
```

Wait — `operation_id` and `machine_id` are FKs to real rows; the test must create a machine and operation first. Full test body:

```python
import pytest

pytestmark = pytest.mark.db

from coe.db.session import session_scope


def _mk_instance(session):
    from coe.db.models.provenance import Instance

    inst = Instance(name="t-sched", source_name="synthetic")
    session.add(inst)
    session.flush()
    return inst.id


def _mk_op_and_machine(session, inst_id):
    from coe.db.models.fjsp import Job, Machine, Operation

    m = Machine(instance_id=inst_id, name="M0")
    j = Job(instance_id=inst_id, name="J1")
    session.add_all([m, j])
    session.flush()
    o = Operation(instance_id=inst_id, job_id=j.id, sequence_number=1)
    session.add(o)
    session.flush()
    return o.id, m.id


def test_version_and_entry_roundtrip(clean_db):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    with session_scope() as session:
        iid = _mk_instance(session)
        oid, mid = _mk_op_and_machine(session, iid)
        v = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.5, makespan=40,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.5,
            failed_machine_ids=None, parent_version_id=None,
            rolled_back=False, payload_hash="a" * 64, payload_json={"k": "v"},
        )
        session.add(v)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v.id, operation_id=oid, machine_id=mid,
            worker_id=None, start_time=0, end_time=12, processing_time=12,
            setup_time=0, is_frozen=False, status="SCHEDULED",
        ))

    from sqlalchemy import create_engine, text

    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        row = c.execute(
            text("SELECT makespan, solver_status, payload_json FROM schedule_versions")
        ).one()
        n = c.execute(text("SELECT count(*) FROM schedule_entries")).scalar_one()
    assert tuple(row) == (40, "OPTIMAL", {"k": "v"})
    assert n == 1


def test_version_number_unique_per_instance(clean_db):
    from sqlalchemy.exc import IntegrityError

    from coe.db.models.schedule import ScheduleVersion

    with session_scope() as session:
        iid = _mk_instance(session)
        for _ in range(2):
            session.add(ScheduleVersion(
                instance_id=iid, version_number=1, schedule_type="BASELINE",
                solver_status="OPTIMAL", objective_value=0, makespan=0,
                total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
                time_limit_seconds=60, solve_duration_seconds=0.0,
                rolled_back=False, payload_hash="b" * 64, payload_json={},
            ))
        with pytest.raises(IntegrityError):
            session.flush()


def test_entry_status_domain(clean_db):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    with session_scope() as session:
        iid = _mk_instance(session)
        oid, mid = _mk_op_and_machine(session, iid)
        v = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=0, makespan=0,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.0,
            rolled_back=False, payload_hash="c" * 64, payload_json={},
        )
        session.add(v)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v.id, operation_id=oid, machine_id=mid,
            worker_id=None, start_time=0, end_time=1, processing_time=1,
            setup_time=0, is_frozen=False, status="BOGUS",
        ))
        with pytest.raises(Exception):  # CHECK violation raised at flush/execute
            session.flush()
        session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/db/test_schedule_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coe.db.models.schedule'` (tables don't exist yet either).

- [ ] **Step 3: Write the models**

Create `coe/db/models/schedule.py`:

```python
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class ScheduleVersion(Base):
    __tablename__ = "schedule_versions"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('BASELINE','RECOVERY')", name="schedule_version_type"
        ),
        CheckConstraint(
            "solver_status IN ('OPTIMAL','FEASIBLE','INFEASIBLE')",
            name="schedule_version_status",
        ),
        UniqueConstraint("instance_id", "version_number",
                         name="uq_schedule_versions_instance_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    schedule_type: Mapped[str] = mapped_column(String(20))
    solver_status: Mapped[str] = mapped_column(String(20))
    objective_value: Mapped[float] = mapped_column(Float)
    makespan: Mapped[int]
    total_tardiness: Mapped[int]
    alpha_weight: Mapped[float] = mapped_column(Float)
    beta_weight: Mapped[float] = mapped_column(Float)
    time_limit_seconds: Mapped[int]
    solve_duration_seconds: Mapped[float] = mapped_column(Float)
    failed_machine_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True
    )
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    committed_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSONB)


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"
    __table_args__ = (
        CheckConstraint("status IN ('SCHEDULED','FROZEN')", name="entry_status"),
        CheckConstraint("end_time >= start_time", name="entry_interval_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_versions.id"), index=True
    )
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    start_time: Mapped[int] = mapped_column(Integer)
    end_time: Mapped[int] = mapped_column(Integer)
    processing_time: Mapped[int] = mapped_column(Integer)
    setup_time: Mapped[int] = mapped_column(Integer, default=0)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
```

Append to `coe/db/models/__init__.py` (match its existing export style):

```python
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion  # noqa: F401
```

- [ ] **Step 4: Run model tests (expect failure on missing tables)**

Run: `uv run pytest tests/db/test_schedule_models.py -q`
Expected: FAIL — relation "schedule_versions" does not exist (models import fine now; migration comes next task). If the failure is instead an import error, fix the model code first.

- [ ] **Step 5: Commit**

```bash
git add coe/db/models/schedule.py coe/db/models/__init__.py tests/db/test_schedule_models.py
git commit -m "feat(db): schedule_versions and schedule_entries models"
```

### Task 3: Migration #6 — tables + active_schedule view

**Files:**
- Create: `alembic/versions/<auto>_schedule_tables.py` (generate via alembic, then hand-complete)

**Interfaces:**
- Produces: relations `schedule_versions`, `schedule_entries`, view `active_schedule` implementing spec §4 SQL verbatim. Later tasks query the view by name.

- [ ] **Step 1: Generate the migration skeleton**

Run: `uv run alembic revision --autogenerate -m "schedule tables and active view"`
Expected: new file in `alembic/versions/` containing `create_table` ops for both tables.

- [ ] **Step 2: Complete the migration by hand**

Autogenerate won't include the view. Edit the generated file: keep the two `create_table` calls (verify column types match Task 2 models, esp. JSONB and server_default for `committed_at`), then append after them in `upgrade()`:

```python
    op.execute(
        """
        CREATE VIEW active_schedule AS
        SELECT se.* FROM schedule_entries se
        JOIN schedule_versions sv ON se.version_id = sv.id
        WHERE sv.id = (
            SELECT id FROM schedule_versions
            WHERE instance_id = se.instance_id
              AND solver_status IN ('OPTIMAL', 'FEASIBLE')
              AND rolled_back = false
            ORDER BY version_number DESC LIMIT 1
        )
        """
    )
```

and at the top of `downgrade()`:

```python
    op.execute("DROP VIEW IF EXISTS active_schedule")
```

- [ ] **Step 3: Apply and verify**

Run: `uv run alembic upgrade head && docker compose exec db psql -U coe -c "\d active_schedule"`
Expected: upgrade succeeds; psql shows view columns mirroring `schedule_entries`.

- [ ] **Step 4: Write view behavior test**

Append to `tests/db/test_schedule_models.py`:

```python
def test_active_schedule_view_picks_latest_feasible(clean_db):
    from sqlalchemy import create_engine, text

    from coe.config import get_settings

    with session_scope() as session:
        iid = _mk_instance(session)
        oid, mid = _mk_op_and_machine(session, iid)

        def version(num, status="OPTIMAL", rolled=False):
            v = ScheduleVersion(
                instance_id=iid, version_number=num, schedule_type="BASELINE",
                solver_status=status, objective_value=0, makespan=num * 10,
                total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
                time_limit_seconds=60, solve_duration_seconds=0.0,
                rolled_back=rolled, payload_hash=f"v{num}".ljust(64, "0"),
                payload_json={},
            )
            session.add(v)
            session.flush()
            return v

        v1 = version(1)
        v2 = version(2, status="INFEASIBLE")   # skipped by the view
        v3 = version(3, rolled=True)           # rolled back, skipped
        v4 = version(4)                        # winner
        for v in (v1, v2, v3, v4):
            session.add(ScheduleEntry(
                instance_id=iid, version_id=v.id, operation_id=oid,
                machine_id=mid, worker_id=None, start_time=0,
                end_time=v.makespan, processing_time=v.makespan,
                setup_time=0, is_frozen=False, status="SCHEDULED",
            ))

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        n = c.execute(text("SELECT count(*) FROM active_schedule")).scalar_one()
        mk = c.execute(text("SELECT max(end_time) FROM active_schedule")).scalar_one()
    assert (n, mk) == (1, 40)


def test_rollback_floor_helper_exists():
    """Rollback floor enforcement lands with the committer; here we pin the
    view-level prerequisite: rolling back v4 must surface v1."""
    from sqlalchemy import create_engine, text

    from coe.config import get_settings

    with session_scope() as session:
        iid = _mk_instance(session)
        oid, mid = _mk_op_and_machine(session, iid)
        v1 = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=0, makespan=10,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.0,
            rolled_back=False, payload_hash="r1".ljust(64, "0"), payload_json={},
        )
        session.add(v1)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v1.id, operation_id=oid, machine_id=mid,
            worker_id=None, start_time=0, end_time=10, processing_time=10,
            setup_time=0, is_frozen=False, status="SCHEDULED",
        ))

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        c.execute(text(
            "UPDATE schedule_versions SET rolled_back = false WHERE "
            "version_number = 999"  # no-op guard; real rollback tested via CLI
        ))
        rows = c.execute(text(
            "SELECT count(*) FROM active_schedule"
        )).scalar_one()
    assert rows == 1
```

Add the missing imports used above at the top of the test file if not present (`ScheduleVersion`, `ScheduleEntry` already imported in earlier tests — reuse module-level imports).

- [ ] **Step 5: Run full model+view suite**

Run: `docker compose up -d && uv run pytest tests/db/test_schedule_models.py -q`
Expected: 5 passed (roundtrip, unique constraint, status domain, view picks latest feasible, floor prerequisite).

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/ tests/db/test_schedule_models.py
git commit -m "feat(db): migration 6 - schedule tables and active_schedule view"
```

<!-- PART-1-END -->

---

## Part 2 — Payload builder baseline path (pure helpers → DB-backed assembly)

### Task 4: Operation identifiers (`coe/solver/identifier.py`)

**Files:**
- Create: `coe/solver/__init__.py` (empty)
- Create: `coe/solver/identifier.py`
- Test: `tests/solver/__init__.py` (empty), `tests/solver/test_identifier.py`

**Interfaces:**
- Produces: `op_id(job_name: str, sequence_number: int) -> str`; `parse_op_id(raw: str) -> tuple[str, int]`. Format: `"J3-O2"` (decided convention — mirrors DB `Job.name`, no zero padding).

- [ ] **Step 1: Write failing tests**

```python
import pytest

from coe.solver.identifier import op_id, parse_op_id


def test_format_matches_convention():
    assert op_id("J3", 2) == "J3-O2"
    assert op_id("J10", 12) == "J10-O12"


def test_roundtrip():
    assert parse_op_id(op_id("J7", 1)) == ("J7", 1)


@pytest.mark.parametrize("bad", ["", "J3", "J3-O", "-O2", "J3-O2a"])
def test_malformed_rejected(bad):
    with pytest.raises(ValueError):
        parse_op_id(bad)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/solver/test_identifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'coe.solver'`.

- [ ] **Step 3: Implement**

`coe/solver/__init__.py`: empty file.
`coe/solver/identifier.py`:

```python
"""Payload identifier synthesis. Operations have no name column in Phase 1;
the payload contract synthesizes "{job.name}-O{sequence_number}"."""


def op_id(job_name: str, sequence_number: int) -> str:
    return f"{job_name}-O{sequence_number}"


def parse_op_id(raw: str) -> tuple[str, int]:
    job_name, sep, seq = raw.rpartition("-O")
    if not job_name or not sep or not seq.isdigit():
        raise ValueError(f"malformed operation id: {raw!r}")
    return job_name, int(seq)
```

Create empty `tests/solver/__init__.py`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/solver/test_identifier.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/solver tests/solver
git commit -m "feat(solver): operation identifier helpers"
```

### Task 5: Solver configuration settings

**Files:**
- Modify: `coe/config.py` (Settings class body)
- Test: `tests/test_solver_config.py`

**Interfaces:**
- Produces: `Settings.solver_time_limit_seconds=60`, `solver_alpha_weight=1.0`, `solver_beta_weight=1.0`, `solver_normalize_objectives=True`, `solver_random_seed=42`, `solver_num_search_workers=1` (env names are the uppercase field names, pydantic-settings default behavior — spec §9).

- [ ] **Step 1: Write failing test**

Create `tests/test_solver_config.py`:

```python
from coe.config import Settings


def test_solver_defaults_match_spec():
    s = Settings()
    assert s.solver_time_limit_seconds == 60
    assert s.solver_alpha_weight == 1.0
    assert s.solver_beta_weight == 1.0
    assert s.solver_normalize_objectives is True
    assert s.solver_random_seed == 42
    assert s.solver_num_search_workers == 1


def test_env_override(monkeypatch):
    monkeypatch.setenv("SOLVER_ALPHA_WEIGHT", "2.5")
    monkeypatch.setenv("SOLVER_TIME_LIMIT_SECONDS", "120")
    s = Settings()
    assert s.solver_alpha_weight == 2.5
    assert s.solver_time_limit_seconds == 120
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_solver_config.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'solver_time_limit_seconds'` (pydantic extra="ignore" swallows unknown env).

- [ ] **Step 3: Implement — append to Settings body in `coe/config.py`** (after `telemetry_chunk_interval_minutes`):

```python
    solver_time_limit_seconds: int = 60
    solver_alpha_weight: float = 1.0
    solver_beta_weight: float = 1.0
    solver_normalize_objectives: bool = True
    solver_random_seed: int = 42
    solver_num_search_workers: int = 1
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_solver_config.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/config.py tests/test_solver_config.py
git commit -m "feat(solver): SOLVER_* configuration settings"
```

### Task 6: Safe horizon computation (`coe/solver/horizon.py`)

**Files:**
- Create: `coe/solver/horizon.py`
- Test: `tests/solver/test_horizon.py`

**Interfaces:**
- Consumes: payload-shaped dicts only (works pre-builder for tests, post-builder for engine).
- Produces: `compute_horizon(*, jobs: list[dict], machine_downtime: list[dict], setup_times: list[dict], frozen_max_end: int = 0) -> int` implementing spec §6.7 (as amended: op max includes worker-map maxima; permanent downtimes excluded because `until is None`). Always ≥ 1 (CP-SAT requires a positive horizon).

- [ ] **Step 1: Write failing tests**

```python
from coe.solver.horizon import compute_horizon


def _op(durs, workers=None, status="PENDING"):
    alts = [{"machine_id": m, "processing_time": d, "workers": (workers or {}).get(m, {})}
            for m, d in durs.items()]
    return {"status": status, "alternatives": alts}


def test_sums_max_processing_including_worker_durations():
    jobs = [{"release_time": 0, "operations": [
        _op({"M0": 10}, {"M0": {"W1": 15}}),   # worker slower than machine
        _op({"M1": 20}),
    ]}]
    h = compute_horizon(jobs=jobs, machine_downtime=[], setup_times=[])
    assert h == 35


def test_setup_and_downtime_added():
    jobs = [{"release_time": 0, "operations": [_op({"M0": 10})]}]
    h = compute_horizon(
        jobs=jobs,
        machine_downtime=[{"machine_id": "M0", "from": 100, "until": 160}],
        setup_times=[{"machine_id": "M0", "from_family": None, "to_family": "A", "duration": 7},
                     {"machine_id": "M0", "from_family": "A", "to_family": "B", "duration": 3}],
    )
    assert h == 10 + 7 + 60


def test_permanent_downtime_excluded():
    jobs = [{"release_time": 0, "operations": [_op({"M0": 10})]}]
    h = compute_horizon(jobs=jobs,
                        machine_downtime=[{"machine_id": "M0", "from": 0, "until": None}],
                        setup_times=[])
    assert h == 10


def test_frozen_end_and_release_dominate():
    jobs = [{"release_time": 500, "operations": []}]
    assert compute_horizon(jobs=jobs, machine_downtime=[], setup_times=[],
                           frozen_max_end=900) == 900


def test_empty_is_one():
    assert compute_horizon(jobs=[], machine_downtime=[], setup_times=[]) == 1


def test_completed_ops_excluded():
    jobs = [{"release_time": 0, "operations": [_op({"M0": 999}, status="COMPLETED")]}]
    assert compute_horizon(jobs=jobs, machine_downtime=[], setup_times=[]) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/solver/test_horizon.py -q`
Expected: FAIL — ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Conservative horizon (spec §6.7, as amended 2026-08-23).

H = max( Σ op-max-processing + Σ per-machine max-setup + Σ temporary downtime,
         max frozen end, max release time, 1 )

All inputs are payload-shaped plain dicts; permanent downtimes (`until is None`)
are excluded because their machine never reaches the engine.
"""


def _op_max_duration(op: dict) -> int:
    durs: list[int] = []
    for alt in op["alternatives"]:
        durs.append(alt["processing_time"])
        durs.extend(alt.get("workers", {}).values())
    return max(durs) if durs else 0


def compute_horizon(*, jobs, machine_downtime, setup_times, frozen_max_end: int = 0) -> int:
    processing = sum(
        _op_max_duration(op)
        for job in jobs
        for op in job["operations"]
        if op["status"] == "PENDING"
    )
    max_setup_per_machine: dict[str, int] = {}
    for row in setup_times:
        mid = row["machine_id"]
        max_setup_per_machine[mid] = max(max_setup_per_machine.get(mid, 0), row["duration"])
    setups = sum(max_setup_per_machine.values())
    downtime = sum(
        w["until"] - w["from"] for w in machine_downtime if w["until"] is not None
    )
    releases = [j["release_time"] for j in jobs]
    return max(processing + setups + downtime, frozen_max_end,
               max(releases) if releases else 0, 1)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/solver/test_horizon.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/solver/horizon.py tests/solver/test_horizon.py
git commit -m "feat(solver): conservative horizon computation"
```

### Task 7: Interval helpers (`coe/solver/windows.py`)

**Files:**
- Create: `coe/solver/windows.py`
- Test: `tests/solver/test_windows.py`

**Interfaces:**
- Produces (all intervals half-open `[start, end)`, the project-wide payload convention):
  - `merge_intervals(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]` — sorted, touching merged, empty/dropped invalid.
  - `complement(start: int, end: int, busy: list[tuple[int, int]]) -> list[tuple[int, int]]` — gaps of `busy` within `[start, end)`.
  - `clip_window(window: tuple[int, int], busy: list[tuple[int, int]]) -> tuple[int, int] | None` — push start past every overlapping busy interval (spec §3.1 conflict clipping); None when fully covered.

- [ ] **Step 1: Write failing tests**

```python
from coe.solver.windows import clip_window, complement, merge_intervals


def test_merge_overlapping_touching_nested_unsorted():
    assert merge_intervals([(5, 10), (0, 3), (2, 6), (10, 12), (20, 22)]) == \
        [(0, 12), (20, 22)]
    assert merge_intervals([]) == []
    assert merge_intervals([(3, 3)]) == []


def test_complement_leading_gaps_trailing():
    assert complement(0, 100, [(10, 20), (30, 40)]) == [(0, 10), (20, 30), (40, 100)]
    assert complement(0, 10, []) == [(0, 10)]
    assert complement(0, 10, [(0, 10)]) == []
    assert complement(5, 15, [(0, 30)]) == []


def test_clip_partial_overlap_pushes_to_busy_end():
    assert clip_window((150, 250), [(100, 200)]) == (200, 250)


def test_clip_full_coverage_drops():
    assert clip_window((150, 250), [(100, 300)]) is None


def test_clip_no_overlap_keeps():
    assert clip_window((50, 80), [(100, 200)]) == (50, 80)


def test_clip_multiple_busy_uses_latest_end():
    assert clip_window((150, 400), [(100, 200), (250, 300)]) == (300, 400)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/solver/test_windows.py -q`
Expected: FAIL — ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Half-open interval algebra for payload construction (spec §3.1, §5).

Convention: every window is [start, end). Touching intervals behave like
overlapping ones under this convention, matching the Phase 1 union rule.
"""


def merge_intervals(pairs):
    out: list[list[int]] = []
    for s, e in sorted(pairs):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def complement(start, end, busy):
    gaps: list[tuple[int, int]] = []
    cursor = start
    for s, e in merge_intervals(busy):
        s, e = max(s, start), min(e, end)
        if s >= e:
            continue
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def clip_window(window, busy):
    ws, we = window
    for bs, be in sorted(busy):
        if bs < we and ws < be:
            ws = max(ws, be)
    return (ws, we) if ws < we else None
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/solver/test_windows.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/solver/windows.py tests/solver/test_windows.py
git commit -m "feat(solver): half-open interval algebra"
```

### Task 8: Pre-solve material gatekeeping (`coe/solver/materials_check.py`)

**Files:**
- Create: `coe/solver/materials_check.py`
- Test: `tests/solver/test_materials_check.py`

**Interfaces:**
- Produces: `evaluate_materials(*, initial_stock: dict[str, int], receipts: list[dict], bom_by_op: dict[str, list[dict]], horizon: int) -> tuple[dict[str, dict], list[dict]]`.
  - First element: `op_id → {"reason": "MATERIAL_UNAVAILABLE", "material_sku": <deadliest sku>}` for ops consuming a **zero-total-supply** material (spec §7 rule 4: block only when supply is zero).
  - Second element: `MATERIAL_SHORTFALL` warnings for partial shortages (supply > 0 but < demand) — advisory only.
  - Receipts count toward supply only when `available_at < horizon` (strictly before, per spec §7 "arriving before the solver horizon").
  - Deterministic: multi-material ops report the alphabetically-first dead SKU; warnings sorted by SKU.

- [ ] **Step 1: Write failing tests**

```python
from coe.solver.materials_check import evaluate_materials


def _run(stock, receipts, bom):
    return evaluate_materials(initial_stock=stock, receipts=receipts,
                              bom_by_op=bom, horizon=1000)


def test_zero_supply_blocks_ops():
    blocks, warns = _run({"STEEL": 0}, [], {"O1": [{"sku": "STEEL", "quantity": 2}]})
    assert blocks == {"O1": {"reason": "MATERIAL_UNAVAILABLE", "material_sku": "STEEL"}}
    assert warns == []


def test_sufficient_supply_passes_silently():
    blocks, warns = _run({"STEEL": 5}, [], {"O1": [{"sku": "STEEL", "quantity": 2}]})
    assert blocks == {} and warns == []


def test_receipt_before_horizon_counts():
    blocks, _ = _run({"STEEL": 0},
                     [{"sku": "STEEL", "quantity": 10, "available_at": 500}],
                     {"O1": [{"sku": "STEEL", "quantity": 2}]})
    assert blocks == {}


def test_receipt_at_or_after_horizon_ignored():
    blocks, _ = _run({"STEEL": 0},
                     [{"sku": "STEEL", "quantity": 10, "available_at": 1000}],
                     {"O1": [{"sku": "STEEL", "quantity": 2}]})
    assert blocks  # strictly-before rule


def test_partial_shortfall_warns_but_never_blocks():
    blocks, warns = _run({"STEEL": 3}, [], {"O1": [{"sku": "STEEL", "quantity": 2}],
                                            "O2": [{"sku": "STEEL", "quantity": 4}]})
    assert blocks == {}
    assert warns == [{"type": "MATERIAL_SHORTFALL", "material_sku": "STEEL",
                      "total_supply": 3, "total_demand": 6}]


def test_multi_material_reports_first_dead_sku():
    blocks, _ = _run({"AAA": 0, "ZZZ": 0}, [],
                     {"O1": [{"sku": "ZZZ", "quantity": 1}, {"sku": "AAA", "quantity": 1}]})
    assert blocks["O1"]["material_sku"] == "AAA"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/solver/test_materials_check.py -q`
Expected: FAIL — ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Pre-solve absolute-supply material gatekeeping (spec §7).

Deliberately loose: allocation order is the solver's job. We catch only
genuinely impossible shortages (zero total supply) and report partial
shortfalls as advisory warnings for the Phase 3 agents.
"""

DEAD = "MATERIAL_UNAVAILABLE"
SHORTFALL = "MATERIAL_SHORTFALL"


def evaluate_materials(*, initial_stock, receipts, bom_by_op, horizon):
    supply = dict(initial_stock)
    for r in receipts:
        if r["available_at"] < horizon:
            supply[r["sku"]] = supply.get(r["sku"], 0) + r["quantity"]

    demand: dict[str, int] = {}
    for items in bom_by_op.values():
        for it in items:
            demand[it["sku"]] = demand.get(it["sku"], 0) + it["quantity"]

    dead = {sku for sku, d in demand.items() if supply.get(sku, 0) == 0}
    short = {sku for sku, d in demand.items() if 0 < supply.get(sku, 0) < d}

    blocks: dict[str, dict] = {}
    for op_id in sorted(bom_by_op):
        hits = sorted({it["sku"] for it in bom_by_op[op_id]} & dead)
        if hits:
            blocks[op_id] = {"reason": DEAD, "material_sku": hits[0]}

    warnings = [
        {"type": SHORTFALL, "material_sku": sku,
         "total_supply": supply.get(sku, 0), "total_demand": demand[sku]}
        for sku in sorted(short)
    ]
    return blocks, warnings
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/solver/test_materials_check.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/solver/materials_check.py tests/solver/test_materials_check.py
git commit -m "feat(solver): pre-solve material gatekeeping"
```

### Task 9: Payload builder — baseline assembly

**Files:**
- Create: `coe/solver/payload_builder.py`
- Test: `tests/solver/conftest.py`, `tests/solver/test_payload_baseline.py`

**Interfaces:**
- Consumes: Tasks 4–8 helpers; Phase 1 models; fixtures below.
- Produces:
  - `derive_tardiness_weights(jobs: list[dict], beta: float) -> dict[str, float] | None` — spec §3.1 amended formula; `None` when no job has a deadline; deadline-less jobs excluded; mean of returned values ≡ `beta` (float-exact up to fp rounding).
  - `resolve_reference_clock(session, instance_id: int, at: int | None) -> int` — `at` if given, else latest `telemetry_events.occurred_at` for the instance, else raises `ValueError` (used fully in Part 3; implemented here because recovery injection shares it).
  - `build_payload(session, *, instance_row, alpha, beta, time_limit_seconds, normalize_objectives=True, schedule_type="BASELINE", now=None, failed_machine_names=()) -> dict` — full §5 structure. Part 2 delivers the baseline behavior (`schedule_type="BASELINE"`); recovery branches arrive in Part 3 and will reuse these internals.
  - Payload key order follows §5 exactly; all collection queries carry explicit `ORDER BY`; alternatives sorted by `Machine.id`; workers map insertion ordered by `Worker.id`; `warnings` starts empty at baseline.
  - Worker unavailability = complement of merged availability within `[0, H]` **plus** `worker_absence_windows` rows clamped to `[0, H]` (Amendment rules), then merged again.
  - `blocked_operations`: material blocks from Task 8, then **in-job cascade**: any later-sequence sibling of a blocked op gets `{"reason": "PREDECESSOR_BLOCKED", "material_sku": null}` (§11 Tier 2b cascade row). Blocked ops stay in `jobs[].operations` with `status: "BLOCKED"`, empty alternatives — the engine ignores non-PENDING ops.
  - `machine_initial_families` is `{}` at baseline (no committed history yet).

- [ ] **Step 1: Shared solver fixtures — create `tests/solver/conftest.py`**

```python
import pytest

MK01 = "data/raw/mk01/mk01.txt"
SFJW = "data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"
GASS = "data/raw/gass"


@pytest.fixture(scope="session")
def built_db():
    """Reset once per session, import sources, build factory_demo_01.
    Read-only tests share this; state-mutating tests (Parts 3/5) must create
    their own instances or reset themselves."""
    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario
    from pathlib import Path

    reset_database(get_settings().database_url)
    import_mk01(Path(MK01))
    import_nouri(Path(SFJW))
    import_gass(Path(GASS))
    sid = build_scenario("factory_demo_01", seed=42)
    return {"settings": get_settings(), "scenario_id": sid}


@pytest.fixture()
def demo_session(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        yield session, (
            session.query(Instance)
            .filter(Instance.id == built_db["scenario_id"])
            .one()
        )


@pytest.fixture()
def mk01_session(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        yield session, session.query(Instance).filter(Instance.name == "mk01").one()
```

- [ ] **Step 2: Write failing tests — `tests/solver/test_payload_baseline.py`**

```python
import json

import pytest

from coe.solver.payload_builder import build_payload, derive_tardiness_weights


# ---------- pure weight derivation ----------

def _job(jid, priority, deadline):
    return {"job_id": jid, "priority": priority, "deadline": deadline}


def test_weights_mean_preserving():
    jobs = [_job("A", 1, 100), _job("B", 3, 100), _job("C", 3, 100)]
    w = derive_tardiness_weights(jobs, beta=2.0)
    assert w["A"] == pytest.approx(2.0 * 3 * 3 / 7)
    assert w["B"] == w["C"] == pytest.approx(2.0 * 3 * 1 / 7)
    assert sum(w.values()) / len(w) == pytest.approx(2.0)


def test_uniform_priorities_degrade_to_beta():
    jobs = [_job("A", 2, 100), _job("B", 2, 100)]
    w = derive_tardiness_weights(jobs, beta=1.5)
    assert w == {"A": 1.5, "B": 1.5}


def test_no_deadlines_returns_none():
    jobs = [_job("A", 1, None)]
    assert derive_tardiness_weights(jobs, beta=1.0) is None


# ---------- baseline payload on factory_demo_01 (DB) ----------

pytestmark = pytest.mark.db


def test_factory_baseline_shape(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    assert p["instance_id"] == "factory_demo_01"
    assert p["schedule_type"] == "BASELINE"
    assert p["parent_version_id"] is None
    assert len(p["machines"]) == 8
    assert p["machines"][0] == "M0"
    assert len(p["jobs"]) == 30
    assert p["machine_initial_families"] == {}
    assert p["warnings"] == []
    assert p["blocked_operations"] == []      # stock = 1.2x demand at build
    assert p["config"] == {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                           "normalize_objectives": True}


def test_every_alternative_carries_worker_map(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    for job in p["jobs"]:
        for op in job["operations"]:
            assert op["status"] == "PENDING"
            assert op["frozen"] is None
            assert op["alternatives"], f"{op['operation_id']} dead-ended"
            for alt in op["alternatives"]:
                assert alt["workers"], "Phase 1 invariant: >=1 eligible worker"
                assert all(d >= 1 for d in alt["workers"].values())


def test_operation_ids_follow_convention(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    j1 = next(j for j in p["jobs"] if j["job_id"] == "J1")
    assert j1["operations"][0]["operation_id"] == "J1-O1"


def test_worker_tail_covered_to_horizon(demo_session):
    """Amendment invariant: for EVERY worker, unavailability ∪ availability
    covers [0, H] exactly (disjoint, no gaps) — so nobody is implicitly
    available after their shift ends."""
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    from coe.db.models.workers import Worker, WorkerAvailabilityWindow
    from coe.solver.horizon import compute_horizon
    from coe.solver.windows import complement, merge_intervals

    horizon = compute_horizon(jobs=p["jobs"],
                              machine_downtime=p["machine_downtime"],
                              setup_times=p["setup_times"])

    unavail: dict[str, list[tuple[int, int]]] = {}
    for e in p["worker_unavailability"]:
        unavail.setdefault(e["worker_id"], []).append((e["from"], e["until"]))

    avail_rows = (
        session.query(WorkerAvailabilityWindow)
        .filter(WorkerAvailabilityWindow.instance_id == inst.id)
        .order_by(WorkerAvailabilityWindow.worker_id).all()
    )
    names = dict(session.query(Worker.id, Worker.name)
                 .filter(Worker.instance_id == inst.id).all())
    avail: dict[str, list[tuple[int, int]]] = {}
    for r in avail_rows:
        avail.setdefault(names[r.worker_id], []).append(
            (r.available_from, min(r.available_until, horizon)))

    assert set(unavail) == set(names)            # every worker represented
    for wname in names:
        ivs = sorted(unavail[wname])
        merged = merge_intervals(ivs)
        assert merged == ivs, f"unavailability not disjoint/normalized: {wname}"
        assert complement(0, horizon, ivs + avail.get(wname, [])) == [], \
            f"coverage gap for {wname}"
        assert ivs[-1][1] == horizon or \
            any(a[1] >= horizon for a in avail.get(wname, [])), \
            f"tail gap after last shift for {wname}"


def test_weights_present_mean_preserving(demo_session):
    session, inst = demo_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    w = p["job_tardiness_weights"]
    assert len(w) == 30                      # every demo job has a TWK deadline
    assert sum(w.values()) / len(w) == pytest.approx(1.0, rel=1e-9)
    prio_by_job = {j["job_id"]: j["priority"] for j in p["jobs"]}
    strongest = max(w, key=lambda k: w[k])
    weakest = min(w, key=lambda k: w[k])
    assert prio_by_job[strongest] <= prio_by_job[weakest]


def test_baseline_deterministic_bytes(demo_session):
    session, inst = demo_session
    a = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    b = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------- baseline payload on pure MK01 (DB) ----------

def test_mk01_benchmark_path_degrades_gracefully(mk01_session):
    session, inst = mk01_session
    p = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                      time_limit_seconds=60)
    assert len(p["machines"]) == 6
    assert len(p["jobs"]) == 10
    assert p["setup_times"] == []
    assert "job_tardiness_weights" not in p     # no deadlines at all
    for job in p["jobs"]:
        assert job["deadline"] is None
        for op in job["operations"]:
            for alt in op["alternatives"]:
                assert alt["workers"] == {}     # no worker layer on source instance
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/solver/test_payload_baseline.py -q`
Expected: FAIL — ModuleNotFoundError (`coe.solver.payload_builder`).

- [ ] **Step 4: Implement `coe/solver/payload_builder.py`**

```python
"""Database → solver payload JSON (spec §3.1/§5, incl. both 2026-08-23
amendments). Owns ALL builder-side DB access; emits deterministic dicts.

Conventions:
- identifiers: machines/jobs/workers/materials use DB names; operations are
  synthesized "{job.name}-O{seq}".
- every collection query carries an explicit ORDER BY (repo determinism rule).
- intervals are half-open [from, until).
"""
from sqlalchemy import select

from coe.db.models.downtime import (
    MachineDowntimeWindow,
    TelemetryEvent,
    WorkerAbsenceWindow,
)
from coe.db.models.fjsp import (
    JobFamily,
    Machine,
    Operation,
    OperationMachineAlternative,
    SetupTime,
)
from coe.db.models.materials import Material, MaterialReceipt, OperationBom
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    Worker,
    WorkerAvailabilityWindow,
)
from coe.solver.horizon import compute_horizon
from coe.solver.identifier import op_id
from coe.solver.materials_check import evaluate_materials
from coe.solver.windows import complement, merge_intervals


def resolve_reference_clock(session, instance_id: int, at: int | None) -> int:
    """--at wins, else latest telemetry occurred_at, else loud failure (§10)."""
    if at is not None:
        return at
    latest = (
        session.query(TelemetryEvent.occurred_at)
        .filter(TelemetryEvent.instance_id == instance_id)
        .order_by(TelemetryEvent.occurred_at.desc(), TelemetryEvent.id.desc())
        .first()
    )
    if latest is None:
        raise ValueError(
            "no reference clock: pass --at or record telemetry first "
            "(fresh scenarios have no telemetry)"
        )
    return latest[0]


def derive_tardiness_weights(jobs, beta: float) -> dict[str, float] | None:
    """Spec §3.1 (second amendment): w_j = beta·n·(p_max+1−p_j)/Σ(p_max+1−p_i),
    deadline-bearing jobs only; mean-preserving around beta; None if no job
    has a deadline."""
    weighted = [j for j in jobs if j["deadline"] is not None]
    if not weighted:
        return None
    n = len(weighted)
    p_max = max(j["priority"] for j in weighted)
    bases = [p_max + 1 - j["priority"] for j in weighted]
    total = sum(bases)
    return {
        j["job_id"]: beta * n * base / total for j, base in zip(weighted, bases)
    }


def _cascade_blocked(ops_by_job: dict[int, list[dict]],
                     material_blocks: dict[str, dict]) -> dict[str, dict]:
    """Later-sequence siblings of a blocked op inherit PREDECESSOR_BLOCKED.
    Each per-job list arrives sequence-ordered (query sorts by sequence_number),
    so a single forward pass suffices."""
    blocked = dict(material_blocks)
    for entries in ops_by_job.values():
        spreading = False
        for e in entries:
            if e["operation_id"] in blocked:
                spreading = True
            elif spreading:
                blocked[e["operation_id"]] = {"reason": "PREDECESSOR_BLOCKED",
                                              "material_sku": None}
    return blocked


def build_payload(
    session,
    *,
    instance_row,
    alpha: float,
    beta: float,
    time_limit_seconds: int,
    normalize_objectives: bool = True,
    schedule_type: str = "BASELINE",
    now: int | None = None,                       # Part 3 seam — unused here
    failed_machine_names: tuple[str, ...] = (),   # Part 3 seam — unused here
):
    iid = instance_row.id

    machines = (
        session.query(Machine)
        .filter(Machine.instance_id == iid)
        .order_by(Machine.id).all()
    )
    machine_names = [m.name for m in machines]
    machine_by_id = {m.id: m.name for m in machines}

    families = (
        session.query(JobFamily).filter(JobFamily.instance_id == iid)
        .order_by(JobFamily.id).all()
    )
    family_name = {f.id: f.name for f in families}

    jobs = (
        session.query(Job).filter(Job.instance_id == iid).order_by(Job.id).all()
    )
    job_name = {j.id: j.name for j in jobs}

    ops = (
        session.query(Operation)
        .filter(Operation.instance_id == iid)
        .order_by(Operation.job_id, Operation.sequence_number).all()
    )
    op_by_id = {o.id: o for o in ops}

    alts = (
        session.query(OperationMachineAlternative)
        .filter(OperationMachineAlternative.instance_id == iid)
        .order_by(OperationMachineAlternative.operation_id,
                  OperationMachineAlternative.machine_id).all()
    )
    worker_rows = (
        session.query(OperationMachineWorkerTime, Worker.name)
        .join(Worker, Worker.id == OperationMachineWorkerTime.worker_id)
        .filter(OperationMachineWorkerTime.instance_id == iid)
        .order_by(OperationMachineWorkerTime.operation_id,
                  OperationMachineWorkerTime.machine_id,
                  OperationMachineWorkerTime.worker_id).all()
    )
    alt_workers: dict[tuple[int, int], dict[str, int]] = {}
    for row, wname in worker_rows:
        alt_workers.setdefault((row.operation_id, row.machine_id), {})[wname] = \
            row.processing_time

    setups = (
        session.query(SetupTime).filter(SetupTime.instance_id == iid)
        .order_by(SetupTime.machine_id, SetupTime.to_family_id,
                  SetupTime.from_family_id).all()
    )
    setup_entries = [
        {"machine_id": machine_by_id[s.machine_id],
         "from_family": family_name.get(s.from_family_id),
         "to_family": family_name[s.to_family_id],
         "duration": s.setup_duration}
        for s in setups
    ]

    downtime = (
        session.query(MachineDowntimeWindow)
        .filter(MachineDowntimeWindow.instance_id == iid)
        .order_by(MachineDowntimeWindow.machine_id,
                  MachineDowntimeWindow.downtime_from,
                  MachineDowntimeWindow.id).all()
    )
    downtime_entries = [
        {"machine_id": machine_by_id[w.machine_id],
         "from": w.downtime_from, "until": w.downtime_until,
         "reason": w.reason}
        for w in downtime
    ]

    availability = (
        session.query(WorkerAvailabilityWindow)
        .filter(WorkerAvailabilityWindow.instance_id == iid)
        .order_by(WorkerAvailabilityWindow.worker_id,
                  WorkerAvailabilityWindow.available_from,
                  WorkerAvailabilityWindow.available_until).all()
    )
    absences = (
        session.query(WorkerAbsenceWindow)
        .filter(WorkerAbsenceWindow.instance_id == iid)
        .order_by(WorkerAbsenceWindow.worker_id,
                  WorkerAbsenceWindow.absence_from,
                  WorkerAbsenceWindow.absence_until).all()
    )
    worker_names = dict(
        session.query(Worker.id, Worker.name)
        .filter(Worker.instance_id == iid).order_by(Worker.id).all()
    )

    boms = (
        session.query(OperationBom, Material.sku)
        .join(Material, Material.id == OperationBom.material_id)
        .filter(OperationBom.instance_id == iid)
        .order_by(Material.sku, OperationBom.operation_id).all()
    )
    bom_by_op: dict[str, list[dict]] = {}
    for row, sku in boms:
        op_row = op_by_id[row.operation_id]
        oid_ = op_id(job_name[op_row.job_id], op_row.sequence_number)
        bom_by_op.setdefault(oid_, []).append(
            {"sku": sku, "quantity": row.quantity_required})
    receipts = (
        session.query(MaterialReceipt, Material.sku)
        .join(Material, Material.id == MaterialReceipt.material_id)
        .filter(MaterialReceipt.instance_id == iid)
        .order_by(Material.sku, MaterialReceipt.available_at,
                  MaterialReceipt.id).all()
    )
    stock_by_sku = dict(
        session.query(Material.sku, Material.initial_stock)
        .filter(Material.instance_id == iid).order_by(Material.sku).all()
    )

    # ---- assemble operation dicts (baseline: everything PENDING) ----
    entry_by_dbid: dict[int, dict] = {}
    ops_by_job: dict[int, list[dict]] = {}
    for o in ops:
        jname = job_name[o.job_id]
        entry = {
            "operation_id": op_id(jname, o.sequence_number),
            "sequence": o.sequence_number,
            "status": "PENDING",
            "alternatives": [],
            "frozen": None,
        }
        entry_by_dbid[o.id] = entry
        ops_by_job.setdefault(o.job_id, []).append(entry)

    alt_index: dict[int, list] = {}
    for a in alts:
        alt_index.setdefault(a.operation_id, []).append(a)
    for o in ops:
        entry = entry_by_dbid[o.id]
        for a in alt_index.get(o.id, []):
            entry["alternatives"].append({
                "machine_id": machine_by_id[a.machine_id],
                "processing_time": a.processing_time,
                "workers": dict(alt_workers.get((o.id, a.machine_id), {})),
            })

    # ---- horizon BEFORE window conversion (tail-coverage amendment) ----
    payload_jobs_preview = [
        {"release_time": j.release_time,
         "operations": [e for e in ops_by_job[j.id]]}
        for j in jobs
    ]
    horizon = compute_horizon(jobs=payload_jobs_preview,
                              machine_downtime=downtime_entries,
                              setup_times=setup_entries)

    # ---- worker unavailability: complement within [0, H] + absence rows ----
    avail_by_worker: dict[int, list[tuple[int, int]]] = {}
    for w in availability:
        avail_by_worker.setdefault(w.worker_id, []).append(
            (w.available_from, min(w.available_until, horizon)))
    absence_by_worker: dict[int, list[tuple[int, int]]] = {}
    for w in absences:
        s = max(0, w.absence_from)
        e = horizon if w.absence_until is None else min(horizon, w.absence_until)
        if s < e:
            absence_by_worker.setdefault(w.worker_id, []).append((s, e))

    worker_unavailability = []
    for wid in sorted(worker_names):
        busy = list(absence_by_worker.get(wid, []))
        busy.extend(complement(0, horizon, avail_by_worker.get(wid, [])))
        for us, ue in merge_intervals(busy):
            worker_unavailability.append(
                {"worker_id": worker_names[wid], "from": us, "until": ue})

    # ---- material gatekeeping + cascade ----
    blocks, mat_warnings = evaluate_materials(
        initial_stock=stock_by_sku,
        receipts=[{"sku": sku, "quantity": r.quantity,
                   "available_at": r.available_at} for r, sku in receipts],
        bom_by_op=bom_by_op,
        horizon=horizon,
    )
    blocked_map = _cascade_blocked(ops_by_job, blocks)
    blocked_operations = []
    for entries in ops_by_job.values():
        for e in entries:
            if e["operation_id"] in blocked_map:
                e["status"] = "BLOCKED"
                e["alternatives"] = []
                blocked_operations.append(
                    {"operation_id": e["operation_id"],
                     **blocked_map[e["operation_id"]]})

    # ---- assemble final payload in §5 key order ----
    payload_jobs = []
    for j in jobs:
        payload_jobs.append({
            "job_id": j.name,
            "family_id": family_name.get(j.job_family_id),
            "release_time": j.release_time,
            "deadline": j.deadline,
            "priority": j.priority,
            "operations": [
                {k: e[k] for k in ("operation_id", "sequence", "status",
                                   "alternatives", "frozen")}
                for e in ops_by_job[j.id]
            ],
        })

    payload = {
        "instance_id": instance_row.name,
        "schedule_type": schedule_type,
        "parent_version_id": None,
        "config": {"alpha": alpha, "beta": beta,
                   "time_limit_seconds": time_limit_seconds,
                   "normalize_objectives": normalize_objectives},
        "machines": machine_names,
        "machine_initial_families": {},
        "warnings": list(mat_warnings),
        "jobs": payload_jobs,
        "machine_downtime": downtime_entries,
        "worker_unavailability": worker_unavailability,
        "setup_times": setup_entries,
        "blocked_operations": blocked_operations,
    }
    weights = derive_tardiness_weights(payload_jobs, beta)
    if weights is not None:
        payload["job_tardiness_weights"] = weights
    return payload
```

Executor notes for Step 4:
- The listing above is the complete file — one canonical version, no alternatives.
- `now` / `failed_machine_names` / `resolve_reference_clock` are deliberate Part 3 seams — leave them even though unused.
- `parent_version_id` stays `None` in Part 2; Part 3 wires the real value.
- Remove the `select` import if lint flags it unused after Part 3's final state lands.

- [ ] **Step 5: Run to verify pass**

Run: `docker compose up -d && uv run pytest tests/solver/test_payload_baseline.py -q`
Expected: 11 passed (3 pure weights + 7 factory + 1 mk01).

- [ ] **Step 6: Regression-check the rest of the suite**

Run: `uv run pytest -q -m "not mqtt"`
Expected: no failures; the new session fixture resets the DB once, so previously passing db tests still pass.

- [ ] **Step 7: Commit**

```bash
git add coe/solver/payload_builder.py tests/solver/conftest.py tests/solver/test_payload_baseline.py
git commit -m "feat(solver): baseline payload builder with weight derivation"
```

<!-- PART-2-END -->

---

## Part 3 — Payload builder recovery path (freeze/truncate/clip/dead-end/seeding)

### Task 10: Seeded-active-schedule fixtures

**Files:**
- Modify: `tests/solver/conftest.py` (append)

**Interfaces:**
- Produces:
  - `seeded_recovery_env(built_db)` → `{"scenario_id", "version_id", "now"}` after inserting an idempotent synthetic BASELINE version over real `factory_demo_01` rows.
  - Fixture `env(demo_session, seeded_recovery_env)` used by every recovery test → `{"session", "instance", "now", "version_id"}`.

Seeded timeline (`now = 1000`; durations deliberately tiny so truncation math is observable):

```text
entries (BASELINE v901, OPTIMAL):
  J1-O1  M0/W1  [400,  500)   -> COMPLETED (frozen)
  J1-O2  M1/W2  [900, 1100)   -> IN_PROGRESS healthy (frozen)
  J2-O1  M2/W3  [990, 1010)   -> INTERRUPTED when M2 fails; remaining = 10
  J2-O2  M3/W4  [1300, 1350)  -> future -> re-solved PENDING
  J3-O1  M0/W1  [1300, 1360)  -> future -> re-solved PENDING
windows:
  MAINTENANCE M1 [1000, 1100) -> fully covered by frozen J1-O2 -> DROPPED
  MAINTENANCE M0 [ 480,  520) -> partially covered by frozen J1-O1 -> CLIPPED to [500,520)
  ABSENCE     W1 [ 300,  600) -> overlaps frozen J1-O1 -> CLIPPED to [500,600)
```

- [ ] **Step 1: Append the fixtures**

Add to `tests/solver/conftest.py`:

```python
@pytest.fixture()
def seeded_recovery_env(built_db):
    """Idempotent synthetic active schedule over factory_demo_01."""
    from coe.db.models.downtime import (
        MachineDowntimeWindow,
        WorkerAbsenceWindow,
    )
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope
    from sqlalchemy import select

    sid = built_db["scenario_id"]
    NOW = 1000
    with session_scope() as session:
        # idempotency: drop earlier seed artifacts
        for v in session.query(ScheduleVersion).filter(
            ScheduleVersion.instance_id == sid,
            ScheduleVersion.payload_hash.like("seeded-%"),
        ).all():
            session.delete(v)
        session.flush()

        def _id(model, name):
            return session.execute(
                select(model.id).where(model.instance_id == sid,
                                       model.name == name)
            ).scalar_one()

        m = {n: _id(Machine, n) for n in ("M0", "M1", "M2", "M3")}
        w = {n: _id(Worker, n) for n in ("W1", "W2", "W3", "W4")}

        def _op(job_name, seq):
            jid = session.execute(
                select(Job.id).where(Job.instance_id == sid,
                                     Job.name == job_name)
            ).scalar_one()
            return session.execute(
                select(Operation.id).where(Operation.instance_id == sid,
                                           Operation.job_id == jid,
                                           Operation.sequence_number == seq)
            ).scalar_one()

        placements = [
            ("J1", 1, "M0", "W1", 400, 500),
            ("J1", 2, "M1", "W2", 900, 1100),
            ("J2", 1, "M2", "W3", 990, 1010),
            ("J2", 2, "M3", "W4", 1300, 1350),
            ("J3", 1, "M0", "W1", 1300, 1360),
        ]
        ver = ScheduleVersion(
            instance_id=sid, version_number=901, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=1360,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.01,
            rolled_back=False, payload_hash="seeded-" + "0" * 57,
            payload_json={"seeded": True},
        )
        session.add(ver)
        session.flush()
        for jname, seq, mname, wname, s, e in placements:
            session.add(ScheduleEntry(
                instance_id=sid, version_id=ver.id,
                operation_id=_op(jname, seq), machine_id=m[mname],
                worker_id=w[wname], start_time=s, end_time=e,
                processing_time=e - s, setup_time=0,
                is_frozen=False, status="SCHEDULED"))
        session.add(MachineDowntimeWindow(
            instance_id=sid, machine_id=m["M1"], downtime_from=1000,
            downtime_until=1100, reason="MAINTENANCE", source_event_ids=[]))
        session.add(MachineDowntimeWindow(
            instance_id=sid, machine_id=m["M0"], downtime_from=480,
            downtime_until=520, reason="MAINTENANCE", source_event_ids=[]))
        session.add(WorkerAbsenceWindow(
            instance_id=sid, worker_id=w["W1"], absence_from=300,
            absence_until=600, reason="WORKER_ABSENT", source_event_ids=[]))

    return {"scenario_id": sid, "version_id": ver.id, "now": NOW}


@pytest.fixture()
def env(demo_session, seeded_recovery_env):
    """Recovery-test environment: live session over freshly seeded state."""
    session, inst = demo_session
    return {"session": session, "instance": inst,
            "now": seeded_recovery_env["now"],
            "version_id": seeded_recovery_env["version_id"]}
```

(`Instance` is imported defensively for debugging convenience; unused imports are acceptable in test fixtures only if lint-clean — drop it if ruff flags it.)

- [ ] **Step 2: Collect-check**

Run: `uv run pytest tests/solver --collect-only -q`
Expected: collection OK, no errors.

- [ ] **Step 3: Commit**

```bash
git add tests/solver/conftest.py
git commit -m "test(solver): seeded active-schedule recovery fixtures"
```

### Task 11: Recovery branch of `build_payload`

**Files:**
- Modify: `coe/solver/payload_builder.py` (anchored edits below)
- Test: `tests/solver/test_payload_recovery.py`

**Interfaces (engine-facing contract — frozen from here on):**
- `build_payload(..., schedule_type="RECOVERY", now=<int>, failed_machine_names=("M2",))`.
- RECOVERY requires `now`; unknown failed names or absent active version raise `ValueError`.
- Failed machine holding an **open** window (`until IS NULL`) ⇒ *stripped*: gone from `machines`, every `alternatives`, and all emitted `machine_downtime`. Failed with only finite windows ⇒ stays listed (solver may wait it out).
- Classification vs clock, per op with a snapshot entry: `end <= now` ⇒ `COMPLETED` + frozen; `start <= now < end` on healthy machine ⇒ `IN_PROGRESS` + frozen; same span on a failed machine ⇒ truncated INTERRUPTED path; strictly future ⇒ plain `PENDING` (re-solved).
- Truncation: `remaining = entry.end - now`; per alternative, if `base > remaining`: `processing_time = remaining` and workers rescaled `max(1, round(d · remaining / base))`; otherwise values pass through. No frozen block; the originally assigned worker is naturally un-frozen.
- Frozen block shape `{"machine_id", "worker_id"|null, "start", "end"}`.
- Clipping (post-freeze, mechanical): surviving downtime windows against frozen blocks of their machine → `DOWNTIME_CLIPPED`/`DOWNTIME_DROPPED`; emitted worker-unavailability windows against frozen blocks of their worker → `WORKER_WINDOW_CLIPPED`/`WORKER_WINDOW_DROPPED`. Fully-covered ⇒ dropped (half-open convention makes `[1000,1100)` inside frozen `[900,1100)` a drop, never a zero-width clip).
- Dead ends: PENDING op left with zero alternatives ⇒ merged into the single cascade map as `NO_CAPABLE_MACHINES` (successors become `PREDECESSOR_BLOCKED`).
- `machine_initial_families`: machine → family of its latest `(end_time, id)` snapshot entry; absent machines stay clean.
- Open-window defense: non-stripped `until:null` windows emit clamped to `horizon` (engine never sees an open bound, §6.4).
- Baseline behavior stays byte-identical to Part 2 (recovery branches inert under `BASELINE`).

- [ ] **Step 1: Write failing tests — create `tests/solver/test_payload_recovery.py` with EXACTLY this content:**

```python
"""Recovery-path payload builder tests (spec §3.1, both amendments).

Timeline lives in conftest.seeded_recovery_env (now = 1000).
"""
import json

import pytest
from sqlalchemy import select

from coe.db.models.fjsp import Machine, OperationMachineAlternative
from coe.db.models.workers import Worker
from coe.solver.payload_builder import build_payload, resolve_reference_clock

pytestmark = pytest.mark.db

REMAINING = 10          # J2-O1 spans [990, 1010); cut at now = 1000


def _build(env, **kw):
    return build_payload(env["session"], instance_row=env["instance"],
                         alpha=1.0, beta=1.0, time_limit_seconds=60,
                         schedule_type="RECOVERY", now=env["now"], **kw)


def _find(p, oid_):
    for j in p["jobs"]:
        for op in j["operations"]:
            if op["operation_id"] == oid_:
                return op
    raise AssertionError(f"{oid_} not in payload")


def _db_op(session, inst, job_name, seq):
    from coe.db.models.fjsp import Job, Operation

    jid = session.query(Job.id).filter(
        Job.instance_id == inst.id, Job.name == job_name).scalar_one()
    return session.query(Operation).filter(
        Operation.instance_id == inst.id, Operation.job_id == jid,
        Operation.sequence_number == seq).one()


def test_completed_and_future_classification(env):
    p = _build(env)
    done = _find(p, "J1-O1")
    assert done["status"] == "COMPLETED"
    assert done["frozen"] == {"machine_id": "M0", "worker_id": "W1",
                              "start": 400, "end": 500}
    assert done["alternatives"] == []
    fut = _find(p, "J2-O2")
    assert fut["status"] == "PENDING" and fut["frozen"] is None
    assert fut["alternatives"]


def test_inprogress_healthy_frozen(env):
    ip = _find(_build(env), "J1-O2")
    assert ip["status"] == "IN_PROGRESS"
    assert ip["frozen"] == {"machine_id": "M1", "worker_id": "W2",
                            "start": 900, "end": 1100}
    assert ip["alternatives"] == []


def test_interrupted_truncated_durations(env):
    from coe.db.models.workers import OperationMachineWorkerTime

    session, inst = env["session"], env["instance"]
    p = _build(env, failed_machine_names=("M2",))
    it = _find(p, "J2-O1")
    assert it["status"] == "PENDING" and it["frozen"] is None

    oprow = _db_op(session, inst, "J2", 1)
    got = {a["machine_id"]: a for a in it["alternatives"]}
    bases = session.execute(
        select(OperationMachineAlternative.machine_id,
               OperationMachineAlternative.processing_time)
        .where(OperationMachineAlternative.instance_id == inst.id,
               OperationMachineAlternative.operation_id == oprow.id)
    ).all()
    assert got, "interrupted op lost all alternatives"
    worker_names = dict(session.query(Worker.id, Worker.name)
                        .filter(Worker.instance_id == inst.id).all())
    for mid, base in bases:
        mname = session.query(Machine.name).filter(Machine.id == mid).scalar_one()
        expected = REMAINING if base > REMAINING else base
        assert got[mname]["processing_time"] == expected, (mname, base)
        if base > REMAINING:
            times = session.execute(
                select(OperationMachineWorkerTime.worker_id,
                       OperationMachineWorkerTime.processing_time)
                .where(OperationMachineWorkerTime.instance_id == inst.id,
                       OperationMachineWorkerTime.operation_id == oprow.id,
                       OperationMachineWorkerTime.machine_id == mid)
            ).all()
            for wid, wdur in times:
                assert got[mname]["workers"][worker_names[wid]] == \
                    max(1, round(wdur * REMAINING / base))


def test_permanent_failure_strips_machine(env, demo_session):
    from coe.db.models.downtime import MachineDowntimeWindow

    session, inst = demo_session
    mid = session.query(Machine.id).filter(
        Machine.instance_id == inst.id, Machine.name == "M2").scalar_one()
    session.add(MachineDowntimeWindow(
        instance_id=inst.id, machine_id=mid, downtime_from=900,
        downtime_until=None, reason="FAILURE", source_event_ids=[]))
    p = _build({**env, "session": session}, failed_machine_names=("M2",))
    assert "M2" not in p["machines"]
    for j in p["jobs"]:
        for op in j["operations"]:
            for alt in op["alternatives"]:
                assert alt["machine_id"] != "M2"
    assert all(d["machine_id"] != "M2" for d in p["machine_downtime"])
    assert _find(p, "J2-O1")["status"] == "PENDING"


def test_dead_end_blocks_cascade(env, demo_session):
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Job, Operation

    session, inst = demo_session
    rows = session.execute(
        select(OperationMachineAlternative.operation_id,
               OperationMachineAlternative.machine_id)
        .where(OperationMachineAlternative.instance_id == inst.id)
        .order_by(OperationMachineAlternative.operation_id,
                  OperationMachineAlternative.machine_id)
    ).all()
    per: dict[int, list[int]] = {}
    for oid_, mid in rows:
        per.setdefault(oid_, []).append(mid)
    solo = []
    for oid_, mids in per.items():
        if len(mids) != 1:
            continue
        oprow = session.query(Operation).filter(Operation.id == oid_).one()
        job_max = session.query(Operation.sequence_number).filter(
            Operation.job_id == oprow.job_id).order_by(
            Operation.sequence_number.desc()).first()
        if oprow.sequence_number < job_max[0]:     # successor must exist
            solo.append((oprow, mids[0]))
    assert solo, "expected a single-alternative op with a successor"
    oprow, mid = solo[0]
    mname = session.query(Machine.name).filter(Machine.id == mid).scalar_one()
    job_name = session.query(Job.name).filter(Job.id == oprow.job_id).scalar_one()
    session.add(MachineDowntimeWindow(
        instance_id=inst.id, machine_id=mid, downtime_from=0,
        downtime_until=None, reason="FAILURE", source_event_ids=[]))
    p = _build({**env, "session": session}, failed_machine_names=(mname,))
    blocked = {b["operation_id"]: b["reason"] for b in p["blocked_operations"]}
    assert blocked[f"{job_name}-O{oprow.sequence_number}"] == "NO_CAPABLE_MACHINES"
    assert blocked.get(f"{job_name}-O{oprow.sequence_number + 1}") == \
        "PREDECESSOR_BLOCKED"


def test_downtime_fully_covered_dropped(env):
    p = _build(env)
    assert [d for d in p["machine_downtime"] if d["machine_id"] == "M1"] == []
    dropped = [w for w in p["warnings"] if w["type"] == "DOWNTIME_DROPPED"]
    assert len(dropped) == 1
    assert dropped[0]["window"] == [1000, 1100]


def test_downtime_partially_clipped(env):
    p = _build(env)
    m0 = [d for d in p["machine_downtime"] if d["machine_id"] == "M0"]
    assert len(m0) == 1
    assert (m0[0]["from"], m0[0]["until"]) == (500, 520)
    clipped = [x for x in p["warnings"] if x["type"] == "DOWNTIME_CLIPPED"]
    assert len(clipped) == 1
    assert clipped[0]["window"] == [480, 520]
    assert clipped[0]["clipped_to"] == [500, 520]


def test_worker_window_clipped_against_frozen(env):
    p = _build(env)
    w1 = [u for u in p["worker_unavailability"] if u["worker_id"] == "W1"]
    for u in w1:
        overlaps_open = u["from"] < 500 and u["until"] > 400
        assert not overlaps_open, f"W1 unavailability overlaps frozen op: {u}"
    clipped = [w for w in p["warnings"] if w["type"] == "WORKER_WINDOW_CLIPPED"]
    assert len(clipped) == 1
    assert clipped[0]["window"] == [300, 600]
    assert clipped[0]["clipped_to"] == [500, 600]


def test_parent_link_and_type(env):
    p = _build(env)
    assert p["schedule_type"] == "RECOVERY"
    assert p["parent_version_id"] == env["version_id"]


def test_initial_families_seeded(env):
    p = _build(env)
    assert set(p["machine_initial_families"]) <= set(p["machines"])
    assert "M0" in p["machine_initial_families"]


def test_recovery_requires_clock_and_known_machines(env):
    with pytest.raises(ValueError):
        build_payload(env["session"], instance_row=env["instance"],
                      alpha=1.0, beta=1.0, time_limit_seconds=60,
                      schedule_type="RECOVERY", now=None)
    with pytest.raises(ValueError):
        _build(env, failed_machine_names=("M99",))


def test_recovery_deterministic_bytes(env):
    a = json.dumps(_build(env, failed_machine_names=("M3",)), sort_keys=True)
    b = json.dumps(_build(env, failed_machine_names=("M3",)), sort_keys=True)
    assert a == b


def test_clock_explicit_wins(mk01_session):
    session, inst = mk01_session
    assert resolve_reference_clock(session, inst.id, at=55) == 55


def test_clock_requires_telemetry_or_at(mk01_session):
    session, inst = mk01_session
    with pytest.raises(ValueError):
        resolve_reference_clock(session, inst.id, at=None)


def test_clock_defaults_to_latest_telemetry(env):
    from coe.db.models.downtime import TelemetryEvent

    session, inst = env["session"], env["instance"]
    mid = session.query(Machine.id).filter(
        Machine.instance_id == inst.id, Machine.name == "M0").scalar_one()
    session.add(TelemetryEvent(
        occurred_at=1234, instance_id=inst.id, message_id="clk-1",
        machine_id=mid, worker_id=None, material_id=None,
        resource_kind="MACHINE", event_type="MAINTENANCE", received_at=1234,
        severity="LOW", estimated_downtime=10, processed_at=1234,
        processing_error=None, payload_json={}))
    assert resolve_reference_clock(session, inst.id, at=None) == 1234
```

(14 tests total.)

- [ ] **Step 2: Run to verify failure**

Run: `docker compose up -d && uv run pytest tests/solver/test_payload_recovery.py -q`
Expected: many FAILures (ops stay `PENDING`, `parent_version_id` None, no warnings) — NOT collection/import errors.

- [ ] **Step 3: Implement — anchored edits to `coe/solver/payload_builder.py`**

**Edit 3a — imports.** Extend the windows import and add the schedule models import:

```python
from coe.solver.windows import clip_window, complement, merge_intervals
```

```python
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
```

**Edit 3b — snapshot helper**, inserted directly above `def build_payload(`:

```python
def _load_active_snapshot(session, iid: int):
    """Latest non-rolled-back OPTIMAL/FEASIBLE version + entries indexed by
    operation db-id (mirrors the §4 active_schedule view semantics)."""
    version = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == iid,
                ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                ScheduleVersion.rolled_back.is_(False))
        .order_by(ScheduleVersion.version_number.desc(),
                  ScheduleVersion.id.desc()).first()
    )
    if version is None:
        return None, {}
    entries = (
        session.query(ScheduleEntry)
        .filter(ScheduleEntry.version_id == version.id)
        .order_by(ScheduleEntry.id).all()
    )
    return version, {e.operation_id: e for e in entries}
```

**Edit 3c — replace the head of `build_payload`.** Anchor: the function body starts at `    iid = instance_row.id`. Replace EVERYTHING from that line through the end of the Part 2 alternatives-attachment loop (its last lines are `            })` immediately before `    setups = (`) with this block:

```python
    iid = instance_row.id

    machines = (
        session.query(Machine)
        .filter(Machine.instance_id == iid)
        .order_by(Machine.id).all()
    )
    machine_by_id = {m.id: m.name for m in machines}
    machine_names = [m.name for m in machines]

    families = (
        session.query(JobFamily).filter(JobFamily.instance_id == iid)
        .order_by(JobFamily.id).all()
    )
    family_name = {f.id: f.name for f in families}

    jobs = (
        session.query(Job).filter(Job.instance_id == iid).order_by(Job.id).all()
    )
    job_name = {j.id: j.name for j in jobs}

    ops = (
        session.query(Operation)
        .filter(Operation.instance_id == iid)
        .order_by(Operation.job_id, Operation.sequence_number).all()
    )
    op_by_id = {o.id: o for o in ops}
    worker_names_by_dbid = dict(
        session.query(Worker.id, Worker.name)
        .filter(Worker.instance_id == iid).order_by(Worker.id).all()
    )

    recovering = schedule_type == "RECOVERY"
    failed_set = set(failed_machine_names)
    parent_version_id = None
    active_by_opid: dict[int, object] = {}
    if recovering:
        if now is None:
            raise ValueError("RECOVERY payloads require a reference clock (now)")
        unknown = sorted(failed_set - set(machine_names))
        if unknown:
            raise ValueError(f"unknown failed machines: {unknown}")
        parent_version, active_by_opid = _load_active_snapshot(session, iid)
        if parent_version is None:
            raise ValueError("RECOVERY requires an existing active schedule")
        parent_version_id = parent_version.id

    downtime = (
        session.query(MachineDowntimeWindow)
        .filter(MachineDowntimeWindow.instance_id == iid)
        .order_by(MachineDowntimeWindow.machine_id,
                  MachineDowntimeWindow.downtime_from,
                  MachineDowntimeWindow.id).all()
    )
    open_windows = {w.machine_id for w in downtime if w.downtime_until is None}
    stripped = {
        machine_by_id[mid] for mid in open_windows
        if machine_by_id[mid] in failed_set
    } if recovering else set()
    machine_names = [n for n in machine_names if n not in stripped]

    # ---- classify operations against the clock ----
    truncation: dict[int, int] = {}
    entry_by_dbid: dict[int, dict] = {}
    ops_by_job: dict[int, list[dict]] = {}
    for o in ops:
        jname = job_name[o.job_id]
        entry = {
            "operation_id": op_id(jname, o.sequence_number),
            "sequence": o.sequence_number,
            "status": "PENDING",
            "alternatives": [],
            "frozen": None,
        }
        ae = active_by_opid.get(o.id)
        if ae is not None:
            m_ae = machine_by_id[ae.machine_id]
            w_ae = (worker_names_by_dbid.get(ae.worker_id)
                    if ae.worker_id is not None else None)
            if ae.end_time <= now:
                entry["status"] = "COMPLETED"
                entry["frozen"] = {"machine_id": m_ae, "worker_id": w_ae,
                                   "start": ae.start_time, "end": ae.end_time}
            elif ae.start_time <= now:
                if m_ae in failed_set:
                    truncation[o.id] = ae.end_time - now
                else:
                    entry["status"] = "IN_PROGRESS"
                    entry["frozen"] = {"machine_id": m_ae, "worker_id": w_ae,
                                       "start": ae.start_time,
                                       "end": ae.end_time}
        entry_by_dbid[o.id] = entry
        ops_by_job.setdefault(o.job_id, []).append(entry)

    alts = (
        session.query(OperationMachineAlternative)
        .filter(OperationMachineAlternative.instance_id == iid)
        .order_by(OperationMachineAlternative.operation_id,
                  OperationMachineAlternative.machine_id).all()
    )
    alt_index: dict[int, list] = {}
    for a in alts:
        alt_index.setdefault(a.operation_id, []).append(a)

    worker_rows = (
        session.query(OperationMachineWorkerTime, Worker.name)
        .join(Worker, Worker.id == OperationMachineWorkerTime.worker_id)
        .filter(OperationMachineWorkerTime.instance_id == iid)
        .order_by(OperationMachineWorkerTime.operation_id,
                  OperationMachineWorkerTime.machine_id,
                  OperationMachineWorkerTime.worker_id).all()
    )
    alt_workers: dict[tuple[int, int], dict[str, int]] = {}
    for row, wname in worker_rows:
        alt_workers.setdefault((row.operation_id, row.machine_id), {})[wname] = \
            row.processing_time

    for o in ops:
        entry = entry_by_dbid[o.id]
        if entry["status"] != "PENDING":
            continue
        remaining = truncation.get(o.id)
        for a in alt_index.get(o.id, []):
            m_alt = machine_by_id[a.machine_id]
            if m_alt in stripped:
                continue
            workers = dict(alt_workers.get((o.id, a.machine_id), {}))
            base = a.processing_time
            level = base
            if remaining is not None and remaining < base:
                level = remaining
                workers = {wk: max(1, round(dv * remaining / base))
                           for wk, dv in workers.items()}
            entry["alternatives"].append({
                "machine_id": m_alt,
                "processing_time": level,
                "workers": workers,
            })
        if not entry["alternatives"] and recovering:
            entry["status"] = "BLOCKED"
            entry["alternatives"] = []
```

**Edit 3d — delete superseded legacy blocks.** After Edit 3c the module must contain EXACTLY ONE occurrence of each anchor below; delete any second occurrence left over from the Part 2 layout, plus these specific leftovers:

- the duplicate `alts = (...)` query + its `alt_index` rebuild + standalone attachment `for o in ops:` loop,
- the old `downtime = (...)` query (it moved up in Edit 3c),
- the old `downtime_entries = [...]` emission block (emission moved to the tail),
- the old `worker_names = dict(...)` query (superseded by `worker_names_by_dbid`).

Verify afterwards:

```bash
grep -c "downtime = (" coe/solver/payload_builder.py      # expect 1
grep -c "alts = (" coe/solver/payload_builder.py          # expect 1
grep -c "worker_names = dict" coe/solver/payload_builder.py  # expect 0
grep -c "downtime_entries = \[\]" coe/solver/payload_builder.py  # expect 1 (in tail)
uv run python -c "import ast; ast.parse(open('coe/solver/payload_builder.py').read()); print('parse ok')"
```

**Edit 3e — replace the tail.** Anchor comment: `    # ---- horizon BEFORE window conversion (tail-coverage amendment) ----`. Replace everything from that line through `    return payload` with:

```python
    # ---- horizon BEFORE window conversion (tail-coverage amendment) ----
    preview_jobs = [{"release_time": j.release_time,
                     "operations": ops_by_job[j.id]} for j in jobs]
    raw_windows = [
        {"machine_id": machine_by_id[w.machine_id],
         "from": w.downtime_from, "until": w.downtime_until,
         "reason": w.reason}
        for w in downtime if machine_by_id[w.machine_id] not in stripped
    ]
    horizon = compute_horizon(jobs=preview_jobs,
                              machine_downtime=raw_windows,
                              setup_times=setup_entries)

    frozen_by_machine: dict[str, list[tuple[int, int]]] = {}
    frozen_by_worker: dict[str, list[tuple[int, int]]] = {}
    for entries in ops_by_job.values():
        for e in entries:
            fz = e["frozen"]
            if fz is None:
                continue
            frozen_by_machine.setdefault(fz["machine_id"], []).append(
                (fz["start"], fz["end"]))
            if fz["worker_id"] is not None:
                frozen_by_worker.setdefault(fz["worker_id"], []).append(
                    (fz["start"], fz["end"]))

    warnings: list[dict] = []
    downtime_entries = []
    for w in raw_windows:
        s = w["from"]
        e = w["until"] if w["until"] is not None else horizon
        outcome = clip_window((s, e), frozen_by_machine.get(w["machine_id"], []))
        if outcome is None:
            warnings.append({"type": "DOWNTIME_DROPPED",
                             "machine_id": w["machine_id"],
                             "window": [s, e],
                             "reason": "fully covered by frozen operations"})
            continue
        if outcome != (s, e):
            warnings.append({"type": "DOWNTIME_CLIPPED",
                             "machine_id": w["machine_id"],
                             "window": [s, e],
                             "clipped_to": [outcome[0], outcome[1]],
                             "reason": "overlaps frozen operations"})
            s, e = outcome
        downtime_entries.append({"machine_id": w["machine_id"],
                                 "from": s, "until": e,
                                 "reason": w["reason"]})

    # ---- worker unavailability: complement within [0, H] + absence rows ----
    avail_by_worker: dict[int, list[tuple[int, int]]] = {}
    for w in availability:
        avail_by_worker.setdefault(w.worker_id, []).append(
            (w.available_from, min(w.available_until, horizon)))
    absence_by_worker: dict[int, list[tuple[int, int]]] = {}
    for w in absences:
        s = max(0, w.absence_from)
        e = horizon if w.absence_until is None else min(horizon, w.absence_until)
        if s < e:
            absence_by_worker.setdefault(w.worker_id, []).append((s, e))

    worker_unavailability = []
    for wid in sorted(worker_names_by_dbid):
        wname = worker_names_by_dbid[wid]
        busy = list(absence_by_worker.get(wid, []))
        busy.extend(complement(0, horizon, avail_by_worker.get(wid, [])))
        for us, ue in merge_intervals(busy):
            outcome = clip_window((us, ue), frozen_by_worker.get(wname, []))
            if outcome is None:
                warnings.append({"type": "WORKER_WINDOW_DROPPED",
                                 "worker_id": wname,
                                 "window": [us, ue],
                                 "reason": "fully covered by frozen operations"})
                continue
            if outcome != (us, ue):
                warnings.append({"type": "WORKER_WINDOW_CLIPPED",
                                 "worker_id": wname,
                                 "window": [us, ue],
                                 "clipped_to": [outcome[0], outcome[1]],
                                 "reason": "overlaps frozen operations"})
                us, ue = outcome
            worker_unavailability.append(
                {"worker_id": wname, "from": us, "until": ue})

    # ---- material gatekeeping + dead-end merge + single cascade ----
    blocks, mat_warnings = evaluate_materials(
        initial_stock=stock_by_sku,
        receipts=[{"sku": sku, "quantity": r.quantity,
                   "available_at": r.available_at}
                  for r, sku in receipt_rows],
        bom_by_op=bom_by_op,
        horizon=horizon,
    )
    for entries in ops_by_job.values():
        for e in entries:
            if e["status"] == "BLOCKED":
                blocks[e["operation_id"]] = {"reason": "NO_CAPABLE_MACHINES",
                                             "material_sku": None}
    blocked_map = _cascade_blocked(ops_by_job, blocks)
    blocked_operations = []
    for entries in ops_by_job.values():
        for e in entries:
            if e["operation_id"] in blocked_map:
                e["status"] = "BLOCKED"
                e["alternatives"] = []
                blocked_operations.append(
                    {"operation_id": e["operation_id"],
                     **blocked_map[e["operation_id"]]})
    warnings.extend(mat_warnings)

    # ---- initial family seeding from the active snapshot ----
    machine_initial_families: dict[str, str] = {}
    if recovering:
        last_entry: dict[int, tuple[tuple[int, int], int]] = {}
        for op_dbid, ae in active_by_opid.items():
            key = (ae.end_time, ae.id)
            prev = last_entry.get(ae.machine_id)
            if prev is None or key > prev[0]:
                last_entry[ae.machine_id] = (key, op_dbid)
        for mid_, (_key, op_dbid) in last_entry.items():
            fam = family_name.get(
                next(j for j in jobs
                     if j.id == op_by_id[op_dbid].job_id).job_family_id)
            if fam is not None:
                machine_initial_families[machine_by_id[mid_]] = fam

    payload_jobs = []
    for j in jobs:
        payload_jobs.append({
            "job_id": j.name,
            "family_id": family_name.get(j.job_family_id),
            "release_time": j.release_time,
            "deadline": j.deadline,
            "priority": j.priority,
            "operations": [
                {k: e[k] for k in ("operation_id", "sequence", "status",
                                   "alternatives", "frozen")}
                for e in ops_by_job[j.id]
            ],
        })

    payload = {
        "instance_id": instance_row.name,
        "schedule_type": schedule_type,
        "parent_version_id": parent_version_id,
        "config": {"alpha": alpha, "beta": beta,
                   "time_limit_seconds": time_limit_seconds,
                   "normalize_objectives": normalize_objectives},
        "machines": machine_names,
        "machine_initial_families": machine_initial_families,
        "warnings": warnings,
        "jobs": payload_jobs,
        "machine_downtime": downtime_entries,
        "worker_unavailability": worker_unavailability,
        "setup_times": setup_entries,
        "blocked_operations": blocked_operations,
    }
    weights = derive_tardiness_weights(payload_jobs, beta)
    if weights is not None:
        payload["job_tardiness_weights"] = weights
    return payload
```

Note the resulting module layout after all edits (queries may interleave, but each appears once): head (3c) → `setups`/`setup_entries` → `availability`/`absences` → `boms`/`receipt_rows`/`stock_by_sku` → tail (3e). The `select` import remains reserved for Part 3+ usage; drop it only when lint flags it in the FINAL state.

- [ ] **Step 4: Run recovery suite**

Run: `docker compose up -d && uv run pytest tests/solver/test_payload_recovery.py -q`
Expected: 14 passed.

- [ ] **Step 5: Full regression**

Run: `uv run pytest -q -m "not mqtt"`
Expected: baseline suites green (recovery branches inert under BASELINE).

- [ ] **Step 6: Commit**

```bash
git add coe/solver/payload_builder.py tests/solver/test_payload_recovery.py tests/solver/conftest.py
git commit -m "feat(solver): recovery payload path (freeze/truncate/clip/cascade/seed)"
```

<!-- PART-3-END -->

---

## Part 4 — CP-SAT engine (`coe/solver/engine.py`)

### Task 12: Engine core — combos, precedence, machines AND workers

**Files:**
- Create: `coe/solver/engine.py`
- Create: `tests/solver/fixtures/*.json` (nine files below)
- Test: `tests/solver/test_engine_constraints.py`

**Interfaces (contract frozen for committer + Phase 3):**
- `solve(payload: dict) -> dict` with keys `status` (`OPTIMAL|FEASIBLE|INFEASIBLE`), `objective_value` (float), `makespan` (int), `total_tardiness` (int), `assignments` (list), `solve_duration_seconds` (float).
- Assignment shape: `{operation_id, job_id, machine_id, worker_id|null, start, end, processing_time, setup_time, is_frozen}`. Frozen ops echoed verbatim (`setup_time=0`, `is_frozen=True`); solved ops get `worker_id=None` when their chosen combo carries none.
- Validation: raises `ValueError` unless `alpha>=0, beta>=0, alpha+beta>0`.
- Short-circuit: zero PENDING ops ⇒ no CP-SAT model; `OPTIMAL`; makespan = max frozen end; tardiness evaluated on frozen ends under payload weights (`job_tardiness_weights` overriding global beta per job — §9.1 layers 4–5).
- Status mapping: cp-sat OPTIMAL/FEASIBLE pass through; everything else reports `INFEASIBLE` with an empty live set (frozen echoes retained).
- Worker semantics (Amendment 1): one optional interval per (machine, worker) combination carrying THAT worker's duration; empty `workers` map ⇒ single machine-level interval, `worker_id=None`. Each worker gets a NoOverlap fed by its combo intervals + unavailability windows.
- Architecture rule (tested): the string `coe.db` never appears in `engine.py`.

- [ ] **Step 1: Write failing tests**

Create `tests/solver/test_engine_constraints.py`:

```python
"""Tier 2b individual-constraint tests over hand-crafted fixture payloads."""
import json
from pathlib import Path

import pytest

from coe.solver.engine import solve

FIX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return json.loads((FIX / f"{name}.json").read_text())


def _one(payload):
    sol = solve(payload)
    assert sol["status"] in ("OPTIMAL", "FEASIBLE")
    live = [a for a in sol["assignments"] if not a["is_frozen"]]
    assert len(live) == 1
    return sol, live[0]


def test_release_time_honored():
    _, a = _one(_fx("release_time"))
    assert (a["start"], a["end"]) == (100, 110)


def test_machine_downtime_avoided():
    _, a = _one(_fx("machine_downtime"))
    assert (a["start"], a["end"]) == (0, 10)      # fits before [50, 150)


def test_frozen_respected():
    sol = solve(_fx("frozen_respected"))
    frozen = [x for x in sol["assignments"] if x["is_frozen"]]
    live = [x for x in sol["assignments"] if not x["is_frozen"]]
    assert len(frozen) == 1 and len(live) == 1
    assert (frozen[0]["start"], frozen[0]["end"]) == (0, 15)
    assert live[0]["machine_id"] == "M1"
    assert live[0]["start"] >= 15


def test_deadline_tardiness_computed():
    sol, a = _one(_fx("deadline_tardiness"))
    assert a["end"] == 80
    assert sol["makespan"] == 80
    assert sol["total_tardiness"] == 30
    assert sol["objective_value"] == pytest.approx(110.0)   # normalization off


def test_null_deadline_zero_tardiness():
    sol, _ = _one(_fx("null_deadline"))
    assert sol["total_tardiness"] == 0


def test_empty_pending_short_circuits():
    sol = solve(_fx("empty_pending"))
    assert sol["status"] == "OPTIMAL"
    assert sol["makespan"] == 15
    assert sol["total_tardiness"] == 5          # frozen end 15 vs deadline 10
    assert len(sol["assignments"]) == 1
    assert sol["assignments"][0]["is_frozen"]


def test_blocked_operations_never_scheduled():
    p = _fx("release_time")
    p["jobs"][0]["operations"].append({
        "operation_id": "J1-O2", "sequence": 2, "status": "BLOCKED",
        "alternatives": [], "frozen": None})
    sol = solve(p)
    assert all(a["operation_id"] != "J1-O2" for a in sol["assignments"])


def test_worker_unavailability_delays_start():
    _, a = _one(_fx("worker_unavailable"))
    assert a["worker_id"] == "W1"
    assert (a["start"], a["end"]) == (100, 110)


def test_no_worker_fallback_uses_machine_duration():
    _, a = _one(_fx("no_worker_fallback"))
    assert a["worker_id"] is None
    assert (a["start"], a["end"]) == (0, 10)


def test_worker_no_overlap_serializes():
    sol = solve(_fx("worker_no_overlap"))
    assert sorted(a["start"] for a in sol["assignments"]) == [0, 5]
    assert sorted(a["end"] for a in sol["assignments"]) == [5, 10]
    workers = {a["worker_id"] for a in sol["assignments"]}
    assert workers == {"W1"}


def test_infeasible_reports_without_live_assignments():
    p = _fx("release_time")
    p["machine_downtime"] = [{"machine_id": "M0", "from": 0,
                              "until": 100000, "reason": "MAINTENANCE"}]
    sol = solve(p)
    assert sol["status"] == "INFEASIBLE"
    assert [a for a in sol["assignments"] if not a["is_frozen"]] == []


def test_invalid_weights_rejected():
    p = _fx("release_time")
    p["config"]["alpha"] = -1.0
    with pytest.raises(ValueError):
        solve(p)


def test_zero_sum_weights_rejected():
    p = _fx("release_time")
    p["config"]["alpha"] = 0.0
    p["config"]["beta"] = 0.0
    with pytest.raises(ValueError):
        solve(p)
```

Create nine fixtures under `tests/solver/fixtures/`. Common skeleton (fill `"jobs"` per note):

```json
{
  "instance_id": "fx",
  "schedule_type": "BASELINE",
  "parent_version_id": null,
  "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
             "normalize_objectives": false},
  "machines": ["M0"],
  "machine_initial_families": {},
  "warnings": [],
  "jobs": [],
  "machine_downtime": [],
  "worker_unavailability": [],
  "setup_times": [],
  "blocked_operations": []
}
```

Standard one-op job used everywhere unless stated (`D` = duration):

```json
{"job_id": "J1", "family_id": null, "release_time": R, "deadline": DL,
 "priority": 1,
 "operations": [{"operation_id": "J1-O1", "sequence": 1, "status": "PENDING",
                 "alternatives": [{"machine_id": "M0", "processing_time": D,
                                   "workers": W}],
                 "frozen": null}]}
```

1. `release_time.json` — R=100, DL=null, D=10, W={}
2. `deadline_tardiness.json` — R=0, DL=50, D=80, W={}
3. `null_deadline.json` — R=0, DL=null, D=10, W={}
4. `machine_downtime.json` — R=0, DL=null, D=10, W={}, plus `"machine_downtime": [{"machine_id": "M0", "from": 50, "until": 150, "reason": "MAINTENANCE"}]`
5. `frozen_respected.json` — skeleton with `"machines": ["M1"]`, `"schedule_type": "RECOVERY"`, `"parent_version_id": 7`; ONE job J1 (R=0, DL=null) with TWO ops: O1 `{sequence:1, status:"COMPLETED", alternatives:[], frozen:{machine_id:"M1", worker_id:null, start:0, end:15}}`; O2 `{sequence:2, status:"PENDING", alternatives:[{machine_id:"M1", processing_time:10, workers:{}}], frozen:null}`
6. `empty_pending.json` — skeleton with `"machines": ["M1"]`, `"schedule_type": "RECOVERY"`, `"parent_version_id": 3`; job J1 (R=0, DL=10) single op `{status:"IN_PROGRESS", alternatives:[], frozen:{machine_id:"M1", worker_id:null, start:0, end:15}}`
7. `worker_unavailable.json` — R=0, DL=null, D=10, W=`{"W1": 10}`, plus `"worker_unavailability": [{"worker_id": "W1", "from": 0, "until": 100}]`
8. `no_worker_fallback.json` — R=0, DL=null, D=10, W={}
9. `worker_no_overlap.json` — skeleton with `"machines": ["M0", "M1"]`; TWO jobs (R=0, DL=null): J1-O1 sole alternative M0/D=5/W={"W1":5}; J2-O1 sole alternative M1/D=5/W={"W1":5} — same worker across different machines forces serialization

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/solver/test_engine_constraints.py -q`
Expected: FAIL — ModuleNotFoundError `coe.solver.engine`.

- [ ] **Step 3: Implement `coe/solver/engine.py`** — type EXACTLY this file:

```python
"""Pure CP-SAT solver: payload JSON in -> solution JSON out (spec §3.2, §6).

No database access, no LLM calls, no side effects. Deterministic by contract:
num_search_workers=1 plus insertion-ordered construction over the payload's
ordered lists. Half-open time convention: [start, end).
"""
import time

from ortools.sat.python import cp_model
from ortools.sat.python.cp_model import CpModel, CpSolver

from coe.solver.horizon import compute_horizon

_STATUS_NAME = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
}


def _validate_config(cfg: dict) -> None:
    alpha = float(cfg.get("alpha", 1.0))
    beta = float(cfg.get("beta", 1.0))
    if alpha < 0 or beta < 0 or alpha + beta <= 0:
        raise ValueError(
            f"invalid objective weights alpha={alpha} beta={beta}: need "
            "alpha>=0, beta>=0, alpha+beta>0 (spec §9)")


def _combos(op: dict) -> list[tuple[str, str | None, int]]:
    """(machine, worker|None, duration) per eligible combination."""
    out = []
    for alt in op["alternatives"]:
        ws = alt.get("workers") or {}
        if ws:
            out.extend((alt["machine_id"], w, d) for w, d in ws.items())
        else:
            out.append((alt["machine_id"], None, alt["processing_time"]))
    return out


def _effective_beta(payload: dict, job_id: str) -> float:
    weights = payload.get("job_tardiness_weights") or {}
    return float(weights.get(job_id, payload["config"].get("beta", 1.0)))


def echo_assignment(job, op) -> dict:
    fz = op["frozen"]
    return {
        "operation_id": op["operation_id"],
        "job_id": job["job_id"],
        "machine_id": fz["machine_id"],
        "worker_id": fz.get("worker_id"),
        "start": fz["start"],
        "end": fz["end"],
        "processing_time": fz["end"] - fz["start"],
        "setup_time": 0,
        "is_frozen": True,
    }


def solve(payload: dict) -> dict:
    t0 = time.monotonic()
    cfg = payload["config"]
    _validate_config(cfg)
    alpha = float(cfg["alpha"])
    normalize = bool(cfg.get("normalize_objectives", True))
    time_limit = float(cfg.get("time_limit_seconds", 60))
    seed = int(cfg.get("random_seed", 42))

    pending, frozen_echo = [], []
    for job in payload["jobs"]:
        for op in job["operations"]:
            if op["status"] == "PENDING":
                pending.append((job, op))
            elif op.get("frozen") is not None:
                frozen_echo.append((job, op))

    def terms_for(ends_by_job: dict[str, int]) -> list[tuple[float, int]]:
        out = []
        for job in payload["jobs"]:
            dl = job["deadline"]
            if dl is None or job["job_id"] not in ends_by_job:
                continue
            out.append((_effective_beta(payload, job["job_id"]),
                        max(0, ends_by_job[job["job_id"]] - dl)))
        return out

    def finish(status, assignments, makespan, terms, horizon_used, dur=None):
        total = round(sum(t for _, t in terms))
        if normalize and horizon_used > 0:
            obj = alpha * makespan / horizon_used + sum(
                w * t / horizon_used for w, t in terms)
        else:
            obj = alpha * makespan + sum(w * t for w, t in terms)
        return {"status": status,
                "objective_value": round(float(obj), 9),
                "makespan": int(makespan),
                "total_tardiness": total,
                "assignments": assignments,
                "solve_duration_seconds": dur if dur is not None
                else round(time.monotonic() - t0, 6)}

    # ---- short circuit: nothing pending ----
    if not pending:
        ends_by_job = {j["job_id"]: o["frozen"]["end"] for j, o in frozen_echo}
        mk = max(ends_by_job.values(), default=0)
        return finish("OPTIMAL",
                      [echo_assignment(j, o) for j, o in frozen_echo],
                      mk, terms_for(ends_by_job), max(mk, 1))

    horizon = compute_horizon(
        jobs=payload["jobs"],
        machine_downtime=payload["machine_downtime"],
        setup_times=payload["setup_times"],
        frozen_max_end=max((o["frozen"]["end"] for _, o in frozen_echo),
                           default=0),
    )

    model = CpModel()
    machine_iv: dict[str, list] = {}
    worker_iv: dict[str, list] = {}

    # frozen anchors: fixed busy blocks on their resources
    for j, o in frozen_echo:
        fz = o["frozen"]
        iv = model.NewIntervalVar(fz["start"], fz["end"] - fz["start"],
                                  fz["end"], f"fz_{o['operation_id']}")
        machine_iv.setdefault(fz["machine_id"], []).append(iv)
        if fz.get("worker_id"):
            worker_iv.setdefault(fz["worker_id"], []).append(iv)

    # per-job chains across ALL ops; frozen constants anchor the chain
    ops_in_job: dict[str, list[dict]] = {}
    for j, o in [*frozen_echo, *pending]:
        ops_in_job.setdefault(j["job_id"], []).append(o)
    for olist in ops_in_job.values():
        olist.sort(key=lambda o: o["sequence"])

    combo_by_op: dict[str, list[dict]] = {}
    last_end_expr: dict[str, object] = {}
    prev_end: dict[str, object] = {}
    release_of = {j["job_id"]: j["release_time"] for j in payload["jobs"]}
    for jid, olist in ops_in_job.items():
        for o in olist:
            if o.get("frozen") is not None:
                prev_end[jid] = o["frozen"]["end"]
                continue
            oid_ = o["operation_id"]
            s = model.NewIntVar(release_of[jid], horizon, f"s_{oid_}")
            e = model.NewIntVar(release_of[jid], horizon, f"e_{oid_}")
            if prev_end.get(jid) is not None:
                model.Add(s >= prev_end[jid])
            prev_end[jid] = e
            last_end_expr[jid] = e

            combos = []
            for m, w, d in _combos(o):
                lit = model.NewBoolVar(f"x_{oid_}_{m}_{w}")
                sv = model.NewIntVar(0, horizon, f"sv_{oid_}_{m}_{w}")
                ev = model.NewIntVar(0, horizon, f"ev_{oid_}_{m}_{w}")
                iv = model.NewOptionalIntervalVar(sv, d, ev, lit,
                                                  f"iv_{oid_}_{m}_{w}")
                model.Add(s == sv).OnlyEnforceIf(lit)
                model.Add(e == ev).OnlyEnforceIf(lit)
                machine_iv.setdefault(m, []).append(iv)
                if w is not None:
                    worker_iv.setdefault(w, []).append(iv)
                combos.append({"lit": lit, "machine": m, "worker": w,
                               "dur": d, "start": s, "end": e})
            model.AddExactlyOne([c["lit"] for c in combos])
            combo_by_op[oid_] = combos

    # downtime + unavailability as fixed blocks
    for wdw in payload["machine_downtime"]:
        until = wdw["until"] if wdw["until"] is not None else horizon
        size = max(1, until - wdw["from"])
        iv = model.NewIntervalVar(wdw["from"], size, until,
                                  f"dt_{wdw['machine_id']}_{wdw['from']}")
        machine_iv.setdefault(wdw["machine_id"], []).append(iv)
    for i, uw in enumerate(payload["worker_unavailability"]):
        iv = model.NewIntervalVar(uw["from"], max(1, uw["until"] - uw["from"]),
                                  uw["until"], f"uw_{uw['worker_id']}_{i}")
        worker_iv.setdefault(uw["worker_id"], []).append(iv)

    for ivs in machine_iv.values():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)
    for ivs in worker_iv.values():
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # ---- tardiness, makespan, objective ----
    tardy_vars = []
    for job in payload["jobs"]:
        end_expr = last_end_expr.get(job["job_id"])
        if job["deadline"] is None or end_expr is None:
            continue
        t = model.NewIntVar(0, horizon, f"t_{job['job_id']}")
        model.AddMaxEquality(t, [0, end_expr - job["deadline"]])
        tardy_vars.append((_effective_beta(payload, job["job_id"]), t))

    all_ends = [prev_end[j] for j in ops_in_job if j in prev_end]
    makespan_var = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan_var, all_ends or [0])

    if normalize:
        obj_expr = alpha * makespan_var / horizon + sum(
            (w / horizon) * t for w, t in tardy_vars)
    else:
        obj_expr = alpha * makespan_var + sum(w * t for w, t in tardy_vars)
    model.Minimize(obj_expr)

    solver = CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    status_code = solver.Solve(model)
    duration = round(time.monotonic() - t0, 6)

    label = _STATUS_NAME.get(status_code, "INFEASIBLE")
    if label == "INFEASIBLE":
        return finish(label, [], 0, [], horizon, dur=duration)

    assignments = [echo_assignment(j, o) for j, o in frozen_echo]
    ends_solved: dict[str, int] = {}
    for j, o in pending:
        oid_ = o["operation_id"]
        chosen = next(c for c in combo_by_op[oid_]
                      if solver.BooleanValue(c["lit"]))
        st = int(solver.Value(chosen["start"]))
        en = int(solver.Value(chosen["end"]))
        assignments.append({
            "operation_id": oid_,
            "job_id": j["job_id"],
            "machine_id": chosen["machine"],
            "worker_id": chosen["worker"],
            "start": st,
            "end": en,
            "processing_time": en - st,
            "setup_time": 0,
            "is_frozen": False,
        })
        ends_solved[j["job_id"]] = max(ends_solved.get(j["job_id"], 0), en)

    merged = {j["job_id"]: o["frozen"]["end"] for j, o in frozen_echo}
    merged.update(ends_solved)
    return finish(label, assignments, max(a["end"] for a in assignments),
                  terms_for(merged), horizon, dur=duration)
```

Post-edit integrity checks:

```bash
uv run python -c "import ast; ast.parse(open('coe/solver/engine.py').read()); print('parse ok')"
grep -c "coe.db" coe/solver/engine.py          # expect 0 (grep exits nonzero)
grep -c "def solve(" coe/solver/engine.py      # expect 1
grep -c "__import__\|:=" coe/solver/engine.py  # expect 0 occurrences of each
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose up -d && uv run pytest tests/solver/test_engine_constraints.py -q`
Expected: 13 passed.

- [ ] **Step 5: Full regression**

Run: `uv run pytest -q -m "not mqtt"`
Expected: Parts 1–3 suites still green.

- [ ] **Step 6: Commit**

```bash
git add coe/solver/engine.py tests/solver/fixtures tests/solver/test_engine_constraints.py
git commit -m "feat(solver): CP-SAT engine core (precedence/no-overlap/workers/objective)"
```

---

### Task 13: Sequence-dependent setups (AddCircuit with dummy nodes)

**Files:**
- Modify: `coe/solver/engine.py` (three anchored edits below)
- Create: five fixtures (deltas listed below)
- Test: append to `tests/solver/test_engine_constraints.py`

**Interfaces:**
- Adds to §6.6 semantics: per-machine `AddCircuit` over node 0 = Dummy Start `S_M`, node 1 = Dummy End `E_M`, pending ops eligible on that machine as optional nodes (`self-loop ⇔ not assigned`). `S→E` fires iff the machine ends up empty; `E→S` closes every loop.
- Arc transits are 0-duration. Each incoming arc with a positive setup row spawns an explicit optional interval `[start_j − d, start_j)` (immediately before its successor) added to that machine's NoOverlap — presence literal IS the arc literal (family pairs are static).
- Missing `(from, to)` row ⇒ 0 ⇒ no interval. Initial setup uses `machine_initial_families[m]` (falling back to `None`) on the `S→j` arc.
- Extraction: each solved assignment's `setup_time` becomes the duration of its active incoming setup arc (0 when none). Frozen echo stays 0.
- Machines whose every relevant setup row is 0 skip the circuit entirely (MK01 fast path).

- [ ] **Step 1: Write failing tests** — append to `tests/solver/test_engine_constraints.py`:

```python
# --- sequence-dependent setups (spec §6.6) ---------------------------------

def _live_triples(sol):
    return sorted(
        (a["start"], a["end"], a["setup_time"])
        for a in sol["assignments"] if not a["is_frozen"])


def test_setup_enforced_cheapest_order_wins():
    """Two single-op jobs (families A/B) sharing M0; A→B=20, B→A=2.
    Solver must run B first: makespan 12 beats 30."""
    sol = solve(_fx("setup_enforced"))
    assert sol["makespan"] == 12
    triples = _live_triples(sol)
    assert triples[0] == (0, 5, 0)          # B first, no initial row
    assert triples[1][0] == 7               # setup [5,7) then A
    assert triples[1][2] == 2


def test_setup_skipped_same_family():
    sol = solve(_fx("setup_skipped"))
    assert _live_triples(sol) == [(0, 5, 0), (5, 10, 0)]


def test_initial_setup_precedes_first_op():
    sol, a = _one(_fx("initial_setup"))
    assert (a["start"], a["setup_time"]) == (10, 10)


def test_initial_setup_from_history():
    sol, a = _one(_fx("initial_from_history"))
    assert (a["start"], a["setup_time"]) == (7, 7)


def test_missing_setup_row_means_zero():
    sol = solve(_fx("missing_setup_row"))
    assert _live_triples(sol) == [(0, 5, 0), (5, 10, 0)]
```

Five fixture files (standard skeleton + standard one-op job shape from Task 12;
two-op variants spelled out):

1. `setup_enforced.json` — `"machines": ["M0"]`; TWO single-op jobs, R=0/DL=null/D=5/W={}: J1 `family_id:"A"` alt M0; J2 `family_id:"B"` alt M0; `"setup_times": [{"machine_id":"M0","from_family":"A","to_family":"B","duration":20}, {"machine_id":"M0","from_family":"B","to_family":"A","duration":2}]`
2. `setup_skipped.json` — same two jobs both `family_id:"A"`; `"setup_times": []`
3. `initial_setup.json` — like `release_time` with R=0 and job `family_id:"A"`; `"setup_times": [{"machine_id":"M0","from_family":null,"to_family":"A","duration":10}]`
4. `initial_from_history.json` — like `release_time` with R=0, job `family_id:"B"`; `"machine_initial_families": {"M0": "A"}`; `"setup_times": [{"machine_id":"M0","from_family":"A","to_family":"B","duration":7}]`
5. `missing_setup_row.json` — two jobs families A/B on M0 (like case 1); `"setup_times": []`

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/solver/test_engine_constraints.py -q -k setup`
Expected: 5 FAIL (starts 0/5, setup_time always 0).

- [ ] **Step 3: Implement — three anchored edits to `coe/solver/engine.py`**

**Edit A — module-level helper**, inserted above `def solve(`:

```python
def _add_setups(model, *, payload, pending, combo_by_op, machine_iv, fam_of):
    """Sequence-dependent setups via AddCircuit with dummy nodes (§6.6).

    Node 0 = Dummy Start, node 1 = Dummy End; each eligible pending op is an
    optional node tied to its machine-assignment literals. Arc transits are 0;
    setups are explicit optional intervals [start_j - d, start_j) gated by the
    incoming arc literal (family pairs are static, so no dynamic AND needed).
    Returns {operation_id: [(arc_literal, minutes), ...]} for extraction."""
    lookup: dict[tuple[str, str | None, str], int] = {}
    for row in payload["setup_times"]:
        d = int(row["duration"])
        if d > 0:
            key = (row["machine_id"], row.get("from_family"),
                   row["to_family"])
            lookup[key] = max(lookup.get(key, 0), d)

    init_fam = payload.get("machine_initial_families") or {}
    mach_ops: dict[str, list[str]] = {}
    for _, op in pending:
        for c in combo_by_op.get(op["operation_id"], []):
            mach_ops.setdefault(c["machine"], []).append(op["operation_id"])

    setup_choices: dict[str, list[tuple[object, int]]] = {}

    for m in sorted(mach_ops):
        oids = list(dict.fromkeys(mach_ops[m]))
        f = {o: fam_of.get(o) for o in oids}

        def d_of(ff, tf, _m=m):
            return lookup.get((_m, ff, tf), 0)

        has_any = any(d_of(init_fam.get(m), f[o]) > 0 for o in oids) or any(
            d_of(f[a], f[b]) > 0 for a in oids for b in oids if a != b)
        if not has_any:
            continue

        on = {o: model.NewBoolVar(f"on_{m}_{o}") for o in oids}
        idle = model.NewBoolVar(f"idle_{m}")
        arcs: list[tuple[int, int, object]] = []
        arcs.append((0, 1, idle))            # S->E iff nothing lands here
        arcs.append((1, 0, None))            # E->S closes every loop

        for i, o in enumerate(oids):
            idx = i + 2
            combos_m = [c for c in combo_by_op[o] if c["machine"] == m]
            for c in combos_m:
                model.AddImplication(c["lit"], on[o])
            model.AddBoolOr([*[c["lit"] for c in combos_m], on[o].Not()])
            model.AddImplication(on[o], idle.Not())

            sv = combos_m[0]["start"]
            arcs.append((idx, idx, on[o].Not()))
            arcs.append((0, idx, on[o]))
            d_init = d_of(init_fam.get(m), f[o])
            if d_init > 0:
                iv = model.NewOptionalIntervalVar(
                    sv - d_init, d_init, sv, on[o], f"su_{m}_init_{o}")
                machine_iv.setdefault(m, []).append(iv)
                setup_choices.setdefault(o, []).append((on[o], d_init))
            arcs.append((idx, 1, on[o]))

        for a in oids:
            for b in oids:
                if a == b:
                    continue
                arc = model.NewBoolVar(f"arc_{m}_{a}_{b}")
                arcs.append((oids.index(a) + 2, oids.index(b) + 2, arc))
                d = d_of(f[a], f[b])
                if d > 0:
                    sb = combo_by_op[b][0]["start"]
                    iv = model.NewOptionalIntervalVar(
                        sb - d, d, sb, arc, f"su_{m}_{a}_{b}")
                    machine_iv.setdefault(m, []).append(iv)
                    setup_choices.setdefault(b, []).append((arc, d))

        model.AddCircuit(arcs)

    return setup_choices
```

**Edit B — call site.** Insert immediately AFTER the worker-unavailability fixed-block loop and BEFORE the two NoOverlap loops:

```python
    fam_of = {ob["operation_id"]: jb.get("family_id") for jb, ob in pending}
    setup_choices = _add_setups(model, payload=payload, pending=pending,
                                combo_by_op=combo_by_op,
                                machine_iv=machine_iv, fam_of=fam_of)
```

**Edit C — extraction.** In the pending-assignment construction, replace `"setup_time": 0,` with the resolved value. The block becomes:

```python
        st = int(solver.Value(chosen["start"]))
        en = int(solver.Value(chosen["end"]))
        setup_used = sum(
            d for lit, d in setup_choices.get(oid_, [])
            if solver.BooleanValue(lit))
        assignments.append({
            "operation_id": oid_,
            "job_id": j["job_id"],
            "machine_id": chosen["machine"],
            "worker_id": chosen["worker"],
            "start": st,
            "end": en,
            "processing_time": en - st,
            "setup_time": setup_used,
            "is_frozen": False,
        })
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/solver/test_engine_constraints.py -q`
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/solver/engine.py tests/solver/fixtures tests/solver/test_engine_constraints.py
git commit -m "feat(solver): AddCircuit sequence-dependent setups with dummy nodes"
```

---

### Task 14: Engine property tests

**Files:**
- Test: `tests/solver/test_engine_properties.py`

**Interfaces:** none new; pins spec Tier 4 pure-solver properties (DB-side rollback/time-limit integration lands in Part 5).

- [ ] **Step 1: Write tests**

Create `tests/solver/test_engine_properties.py`:

```python
"""Pure-solver property tests (spec §11 Tier 4, DB-free subset)."""
import json
import time
from pathlib import Path

import pytest

from coe.solver.engine import solve

from tests.solver.test_engine_constraints import _fx

ENGINE_SRC = (
    Path(__file__).resolve().parents[2] / "coe" / "solver" / "engine.py"
)


def test_no_database_imports_allowed():
    assert "coe.db" not in ENGINE_SRC.read_text()


def test_deterministic_bytes_same_payload():
    p = _fx("worker_no_overlap")
    a = json.dumps(solve(p), sort_keys=True)
    b = json.dumps(solve(p), sort_keys=True)
    assert a == b


def test_time_limit_respected_and_status_valid():
    p = _fx("deadline_tardiness")
    p["config"]["time_limit_seconds"] = 0.001
    t0 = time.monotonic()
    sol = solve(p)
    assert time.monotonic() - t0 < 5.0
    assert sol["status"] in ("OPTIMAL", "FEASIBLE", "INFEASIBLE")


def test_normalized_objective_is_ratio():
    p = _fx("deadline_tardiness")
    p["config"]["normalize_objectives"] = True
    sol = solve(p)
    # horizon = 80 (processing dominates); obj = (makespan + tardiness)/H
    assert sol["objective_value"] == pytest.approx(110.0 / 80.0)
```

- [ ] **Step 2: Run to verify pass** (implementation already satisfies these; this task pins them)

Run: `uv run pytest tests/solver/test_engine_properties.py -q`
Expected: 4 passed.

- [ ] **Step 3: Full regression**

Run: `uv run pytest -q -m "not mqtt"`
Expected: everything green.

- [ ] **Step 4: Commit**

```bash
git add tests/solver/test_engine_properties.py
git commit -m "test(solver): engine determinism, time-limit, purity, normalization"
```

<!-- PART-4-END -->

---

## Part 4.5 — Material capacity enforcement (Amendment 2026-08-24, Option B)

> Executes BEFORE resuming Task 13. The engine has no setups yet; this part lands material physics first so Task 13's edits build on final constraint structure. Absorbs both controller riders from the Task 12 review (span setup-term becomes moot here for receipts; None-guard lands in 12B). **Second-review riders (2026-08-24 gap sweep, user-approved):** Task 12A additionally implements (i) *suspension memory* — builder skips jobs whose `jobs.status == 'BLOCKED'`, lists them as `JOB_SUSPENDED` blocked entries, emits root `suspended_jobs`; committer side mirrors job statuses (Task 16 rider); (ii) *status truth* — machines with DB status `FAILED` are stripped even without CLI args; workers with status `UNAVAILABLE` get full-horizon unavailability. Task 12B additionally makes `_schedule_span` include Σ temporary machine-downtime durations so ops may wait out maintenance — the old `test_infeasible_reports_without_live_assignments` fixture flips to delayed-start OPTIMAL (`start >= 100000`). Task 15 (Part 5) rider: invariant membership check exempts `is_frozen` echoes, with a frozen-on-stripped-machine regression test. The live code has drifted from earlier listings via approved fixes — these tasks therefore pin canonical TESTS + exact interfaces and describe implementation by anchor; implementers declare any further deviation with reasoning (house pattern since Task 9).

### Task 12A: Builder emits materials capacities + demands

**Files:**
- Modify: `coe/solver/payload_builder.py`
- Test: `tests/solver/test_payload_materials.py`

**Interfaces (payload contract additions — frozen):**
- Root `"materials": [{"sku", "capacity"}]`, sorted by SKU; `capacity = initial_stock + Σ receipts with available_at < horizon` (strict).
- Root `"material_receipts": [{"sku", "quantity", "available_at"}]`, same receipt set, sorted by (sku, available_at, quantity); arrivals at/after horizon omitted entirely.
- Per-operation entry gains `"materials": [{"sku", "quantity"}]` sorted by SKU (from `operation_bom`; empty list when none). Key order within an op dict: `operation_id, sequence, status, materials, alternatives, frozen`.
- Blocked operations carry `"materials": []` and contribute to neither arrays' demand side nor warnings beyond existing behavior; zero-supply pre-blocking, cascade, and `MATERIAL_SHORTFALL` warning shape are UNCHANGED.
- Baseline determinism must hold byte-for-byte.

- [ ] **Step 1: Write failing tests**

Create `tests/solver/test_payload_materials.py`:

```python
"""Amendment 2026-08-24: payload carries material physics inputs."""
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


def _build(session, inst):
    from coe.solver.payload_builder import build_payload

    return build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                         time_limit_seconds=60)


def test_factory_materials_capacities_match_db(demo_session):
    session, inst = demo_session
    p = _build(session, inst)
    skus = [m["sku"] for m in p["materials"]]
    assert skus == sorted(skus) and len(skus) == 8

    row = session.execute(text(
        "SELECT m.sku, m.initial_stock, "
        "  COALESCE(SUM(CASE WHEN r.available_at < :h THEN r.quantity END), 0) "
        "FROM materials m "
        "LEFT JOIN material_receipts r ON r.material_id = m.id "
        "WHERE m.instance_id = :i GROUP BY m.sku, m.initial_stock"),
        {"i": inst.id, "h": 10 ** 9}).all()
    # recompute per-sku with the payload's own horizon for exactness:
    from coe.solver.horizon import compute_horizon

    H = compute_horizon(jobs=p["jobs"],
                        machine_downtime=p["machine_downtime"],
                        setup_times=p["setup_times"])
    expected = {}
    for sku, stock, _ in row:
        rec = session.execute(text(
            "SELECT COALESCE(SUM(r.quantity),0) FROM material_receipts r "
            "JOIN materials m ON m.id = r.material_id "
            "WHERE m.instance_id = :i AND m.sku = :s AND r.available_at < :h"),
            {"i": inst.id, "s": sku, "h": H}).scalar_one()
        expected[sku] = stock + rec
    got = {m["sku"]: m["capacity"] for m in p["materials"]}
    assert got == expected


def test_operation_demands_mirror_bom(demo_session):
    session, inst = demo_session
    p = _build(session, inst)
    checked = 0
    for job in p["jobs"]:
        for op in job["operations"]:
            dem = op["materials"]
            assert [d["sku"] for d in dem] == sorted(d["sku"] for d in dem)
            if op["status"] != "BLOCKED":
                rows = session.execute(text(
                    "SELECT m.sku, b.quantity_required FROM operation_bom b "
                    "JOIN operations o ON o.id = b.operation_id "
                    "JOIN jobs j ON j.id = o.job_id "
                    "JOIN materials m ON m.id = b.material_id "
                    "WHERE j.name = :j AND o.sequence_number = :s "
                    "AND j.instance_id = :i ORDER BY m.sku"),
                    {"j": job["job_id"], "s": op["sequence"],
                     "i": inst.id}).all()
                assert dem == [{"sku": s, "quantity": q} for s, q in rows]
                checked += 1
    assert checked > 100          # factory_demo_01 has ~168 ops


def test_blocked_ops_carry_no_demands(demo_session):
    """Zero-supply pre-block unchanged; blocked entries have empty lists."""
    session, inst = demo_session
    p = _build(session, inst)
    assert p["blocked_operations"] == []      # demo baseline is unblocked
    for job in p["jobs"]:
        for op in job["operations"]:
            assert isinstance(op["materials"], list)


def test_materials_arrays_deterministic(demo_session):
    session, inst = demo_session
    a = json.dumps(_build(session, inst), sort_keys=True)
    b = json.dumps(_build(session, inst), sort_keys=True)
    assert a == b
```

- [ ] **Step 2: Fail run → implement → green**

Implementation anchors (live file):
1. Op-entry dicts gain `"materials": [...]` at construction time from `bom_by_op` (it exists before classification); key order per interface. When an op later flips to BLOCKED (dead-end or gatekeeping pass), reset its list to `[]`.
2. In the tail, after `horizon` exists and after gatekeeping, build receipt/sku joins already queried (`receipt_rows`) into:
   - `payload["material_receipts"]` filtered to `available_at < horizon`, sorted;
   - `payload["materials"]` capacity map from `stock_by_sku` + those receipts, restricted to SKUs that appear in any non-blocked demand OR any receipt (emit all instance SKUs — simplest deterministic rule: all SKUs of the instance, sorted).
3. Insert both keys into the final payload dict between `"worker_unavailability"` and `"setup_times"`… actually spec example places them after `machine_downtime` — use: `machine_downtime, materials, material_receipts, worker_unavailability`.

Run: target suite green (4) → full `-m "not mqtt"` regression green → commit `feat(solver): payload emits material capacities, receipts, demands`.

### Task 12B: Engine reservoir constraints

**Files:**
- Modify: `coe/solver/engine.py`
- Create fixtures: `material_timing.json`, `over_demand.json`
- Test: append to `tests/solver/test_engine_constraints.py`

**Interfaces:**
- Engine honors root `materials`, `material_receipts`, and per-op `materials`. One reservoir per demanded SKU: consumption events `(start_var, −qty)` active on an any-combo boolean per op (same implication pattern as circuit presence: each combo literal implies it; its negation completes the AddBoolOr); refill events `(available_at, +qty)` always active. Floor `−capacity` (missing root row ⇒ capacity 0, no receipts ⇒ any demand is INFEASIBLE — defensive). Ceiling `Σ|changes|`.
- API check first: `uv run python -c "from ortools.sat.python.cp_model import CpModel; print(hasattr(CpModel, 'AddReservoirConstraintWithActive'))"` — expect True on ortools ≥9.10; if False, STOP and report BLOCKED (do not invent a fallback).
- Rider 1 (Task-12-review debt): `_schedule_span` gains the latest counted receipt time as a reachability term — waiting for Friday's delivery must fit inside variable domains. Signature grows `receipt_times: list[int] | None = None`; span adds `max(receipt_times or [0])` to its sum. Engine passes `[r["available_at"] for r in payload.get("material_receipts", [])]`.
- Rider 2: `_schedule_span` worker-unavailability summation guards `until=None` (contributes 0).

- [ ] **Step 1: Fixtures**

`material_timing.json`: skeleton (Task 12) with `"machines": ["M0"]`; TWO single-op jobs J-A/J-B (R=0, DL=null, D=5, W={}, sole alt M0), each op `"materials": [{"sku": "STEEL", "quantity": 5}]`; root `"materials": [{"sku": "STEEL", "capacity": 8}]`; `"material_receipts": [{"sku": "STEEL", "quantity": 2, "available_at": 500}]`.

`over_demand.json`: identical but capacity 10, NO receipts key (omit entirely), quantities 6+6.

- [ ] **Step 2: Tests**

Append:

```python
# --- temporal material capacity (spec §6.11, Amendment 2026-08-24) ---------

def test_material_timing_delays_one_op():
    sol = solve(_fx("material_timing"))
    assert sol["status"] in ("OPTIMAL", "FEASIBLE")
    starts = sorted(a["start"] for a in sol["assignments"])
    assert starts[0] == 0
    assert starts[1] >= 500


def test_permanent_over_demand_is_infeasible():
    sol = solve(_fx("over_demand"))
    assert sol["status"] == "INFEASIBLE"
    assert [a for a in sol["assignments"] if not a["is_frozen"]] == []


def test_unknown_demanded_sku_defaults_to_zero():
    p = _fx("material_timing")
    for j in p["jobs"]:
        for op in j["operations"]:
            op["materials"] = [{"sku": "GHOST", "quantity": 1}]
    p.pop("materials", None)
    p.pop("material_receipts", None)
    assert solve(p)["status"] == "INFEASIBLE"
```

- [ ] **Step 3: Implement** (anchor-guided): combo loop already yields per-op combos + shared `s`; add `demands = {sku: qty}` scan building `b_any[op]` booleans + reservoir event lists; emit `model.AddReservoirConstraintWithActive(times, changes, actives, -capacity, ceiling)` per demanded sku after the NoOverlap loops; update `_schedule_span` signature/call sites + None-guard per riders.

- [ ] **Step 4: Green gates**: 3 new tests pass; full constraint file (21) passes; properties (4) pass; full regression `-m "not mqtt"` green; integrity greps (`coe.db`=0, one `def solve(`).

- [ ] **Step 5: Commit** `feat(solver): reservoir-based material capacity enforcement`

<!-- PART-4-5-END -->


---

## Part 5 — Invariants, committer, rollback

### Task 15: Pure solution invariants (`coe/solver/invariants.py`)

**Files:**
- Create: `coe/solver/invariants.py`
- Test: `tests/solver/test_invariants.py`

**Interfaces:**
- Produces: `check_solution(payload: dict, solution: dict) -> list[str]` — empty list means the solution satisfies every §6.2 invariant:
  1. frozen ops byte-identical to their payload `frozen` blocks (machine, worker, start, end);
  2. no **live** assignment on a machine absent from `payload["machines"]` (stripped = permanently failed; temporary failures stay listed and remain legal). *Rider (2026-08-24 gap sweep):* `is_frozen` echoes are EXEMPT from this check — historic work on a since-stripped machine is immutable fact, not a violation; regression test required;
  3. machine eligibility + worker eligibility + exact combo duration;
  4. job precedence order across *scheduled* ops (blocked/missing ops skip the chain);
  5. blocked operations never appear.
  Duplicate assignments are flagged too (defensive).
- Consumed by the committer (pre-commit) and, verbatim, by Phase 3's gate/verifier.
- Pure: no imports beyond stdlib.

- [ ] **Step 1: Write failing tests**

Create `tests/solver/test_invariants.py`:

```python
"""Pure invariant checks (spec §6.2)."""
from coe.solver.invariants import check_solution


def _payload():
    return {
        "machines": ["M0", "M1"],
        "blocked_operations": [{"operation_id": "J9-O1",
                                "reason": "NO_CAPABLE_MACHINES",
                                "material_sku": None}],
        "jobs": [
            {"job_id": "J1", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [
                 {"operation_id": "J1-O1", "sequence": 1,
                  "status": "COMPLETED", "alternatives": [],
                  "frozen": {"machine_id": "M0", "worker_id": "W1",
                             "start": 0, "end": 10}},
                 {"operation_id": "J1-O2", "sequence": 2,
                  "status": "PENDING",
                  "alternatives": [{"machine_id": "M1",
                                    "processing_time": 5,
                                    "workers": {"W1": 5}}],
                  "frozen": None},
             ]},
            {"job_id": "J9", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [
                 {"operation_id": "J9-O1", "sequence": 1,
                  "status": "BLOCKED", "alternatives": [], "frozen": None},
             ]},
        ],
    }


def _solution():
    return {"assignments": [
        {"operation_id": "J1-O1", "job_id": "J1", "machine_id": "M0",
         "worker_id": "W1", "start": 0, "end": 10, "processing_time": 10,
         "setup_time": 0, "is_frozen": True},
        {"operation_id": "J1-O2", "job_id": "J1", "machine_id": "M1",
         "worker_id": "W1", "start": 10, "end": 15, "processing_time": 5,
         "setup_time": 0, "is_frozen": False},
    ]}


def test_clean_solution_passes():
    assert check_solution(_payload(), _solution()) == []


def test_frozen_drift_detected():
    sol = _solution()
    sol["assignments"][0]["start"] = 1
    assert any("frozen drift" in v for v in check_solution(_payload(), sol))


def test_frozen_missing_detected():
    sol = _solution()
    sol["assignments"] = sol["assignments"][1:]
    assert any("missing" in v for v in check_solution(_payload(), sol))


def test_stripped_machine_detected():
    sol = _solution()
    sol["assignments"][1]["machine_id"] = "M99"
    assert any("unavailable machine" in v
               for v in check_solution(_payload(), sol))


def test_worker_and_duration_mismatch_detected():
    p = _payload()
    sol = _solution()
    sol["assignments"][1]["worker_id"] = "W2"
    assert any("ineligible" in v for v in check_solution(p, sol))
    sol2 = _solution()
    sol2["assignments"][1]["processing_time"] = 7
    assert any("duration" in v for v in check_solution(p, sol2))


def test_precedence_violation_detected():
    sol = _solution()
    a = sol["assignments"][1]
    a["start"], a["end"] = 0, 5          # starts before frozen predecessor ends
    msgs = check_solution(_payload(), sol)
    assert any("precedence" in v for v in msgs)


def test_blocked_operation_scheduled_detected():
    sol = _solution()
    sol["assignments"].append({
        "operation_id": "J9-O1", "job_id": "J9", "machine_id": "M0",
        "worker_id": None, "start": 20, "end": 25, "processing_time": 5,
        "setup_time": 0, "is_frozen": False})
    assert any("blocked" in v.lower()
               for v in check_solution(_payload(), sol))


def test_duplicate_assignment_detected():
    sol = _solution()
    sol["assignments"].append(dict(sol["assignments"][1]))
    assert any("duplicate" in v for v in check_solution(_payload(), sol))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/solver/test_invariants.py -q`
Expected: FAIL — ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `coe/solver/invariants.py`:

```python
"""Pure post-solve invariant checks (spec §6.2 gate list).

Shared verbatim by the Phase 2 committer and (later) Phase 3's pre-commit
gate + post-commit verifier, so the definition can never drift apart.
"""
from collections import defaultdict


def check_solution(payload: dict, solution: dict) -> list[str]:
    violations: list[str] = []
    machines = set(payload["machines"])

    frozen_expected: dict[str, dict] = {}
    blocked: set[str] = set()
    alts: dict[str, list] = {}
    seqs_by_job: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for job in payload["jobs"]:
        for op in job["operations"]:
            oid_ = op["operation_id"]
            if op.get("frozen") is not None:
                frozen_expected[oid_] = op["frozen"]
            if op["status"] == "BLOCKED":
                blocked.add(oid_)
            alts[oid_] = op["alternatives"]
            seqs_by_job[job["job_id"]].append((op["sequence"], oid_))

    seen: dict[str, dict] = {}
    for a in solution["assignments"]:
        oid_ = a["operation_id"]
        if oid_ in seen:
            violations.append(f"duplicate assignment for {oid_}")
            continue
        seen[oid_] = a
        if oid_ in blocked:
            violations.append(f"blocked operation {oid_} appears in schedule")
        if a["machine_id"] not in machines:
            violations.append(
                f"{oid_} assigned to unavailable machine {a['machine_id']}")
            continue
        exp = frozen_expected.get(oid_)
        if exp is not None:
            if (a["machine_id"] != exp["machine_id"]
                    or a.get("worker_id") != exp.get("worker_id")
                    or a["start"] != exp["start"]
                    or a["end"] != exp["end"]):
                violations.append(f"frozen drift on {oid_}")
            continue
        elig = [x for x in alts.get(oid_, [])
                if x["machine_id"] == a["machine_id"]]
        if not elig:
            violations.append(
                f"{oid_} ineligible on {a['machine_id']}")
            continue
        alt = elig[0]
        ws = alt.get("workers") or {}
        wid = a.get("worker_id")
        if ws:
            if wid not in ws:
                violations.append(f"{oid_} worker {wid} ineligible")
            elif a["processing_time"] != ws[wid]:
                violations.append(
                    f"{oid_} duration {a['processing_time']} != worker "
                    f"{wid} duration {ws[wid]}")
        elif wid is not None or a["processing_time"] != alt["processing_time"]:
            violations.append(
                f"{oid_} duration/worker mismatch vs machine-level alt")

    for oid_ in frozen_expected:
        if oid_ not in seen:
            violations.append(f"frozen operation {oid_} missing from solution")

    for seqs in seqs_by_job.values():
        prev_end = None
        for _, oid_ in sorted(seqs):
            a = seen.get(oid_)
            if a is None:
                continue
            if prev_end is not None and a["start"] < prev_end:
                violations.append(f"precedence violated before {oid_}")
            prev_end = a["end"]

    return violations
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/solver/test_invariants.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/solver/invariants.py tests/solver/test_invariants.py
git commit -m "feat(solver): pure solution invariant checks"
```

### Task 16: Committer + rollback (`coe/solver/committer.py`)

**Files:**
- Create: `coe/solver/committer.py`
- Test: `tests/solver/test_committer.py`

**Interfaces:**
- `payload_hash(payload: dict) -> str` — SHA-256 over `json.dumps(payload, sort_keys=True)`.
- `commit_solution(session, *, instance_row, payload, solution, failed_machine_names=(), now=None) -> ScheduleVersion` — raises `ValueError` unless status ∈ {OPTIMAL, FEASIBLE}; allocates `version_number = max+1` under `SELECT … FOR UPDATE` on the instance's latest version (UNIQUE constraint backs races); writes entries for every assignment (`FROZEN`/`SCHEDULED`); mirrors `operations.status` in the same transaction: `end<=now → COMPLETED`, `start<=now<end → IN_PROGRESS`, else `SCHEDULED`; `blocked_operations` → `BLOCKED`; `now=None` (baseline) ⇒ everything `SCHEDULED`. `failed_machine_ids` = sorted names for RECOVERY (None if empty), always None for BASELINE.
- `commit_solution_autocommit(instance_name, ...) -> int` — owns its session; returns version id (CLI convenience).
- `rollback_active(session, instance_row) -> tuple[int, int]` — marks newest non-rolled feasible version `rolled_back=true`; returns `(rolled_version_number, new_active_version_number)`; raises `RollbackFloor` when fewer than two candidates exist or none at all.
- Atomicity: callers own the transaction (`session_scope`); any exception rolls the whole commit back.

- [ ] **Step 1: Write failing tests**

Create `tests/solver/test_committer.py`:

```python
"""Committer behavior against the real schema (mk01 pipeline, no workers)."""
import pytest

from coe.solver.engine import solve
from coe.solver.payload_builder import build_payload

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def solved_mk01(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = session.query(Instance).filter(Instance.name == "mk01").one()
        payload = build_payload(session, instance_row=inst,
                                alpha=1.0, beta=1.0, time_limit_seconds=30)
        solution = solve(payload)
    return payload, solution


def _inst(session, name="mk01"):
    from coe.db.models.provenance import Instance

    return session.query(Instance).filter(Instance.name == name).one()


def test_commit_creates_version_entries_and_mirrors(built_db, solved_mk01):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution, payload_hash

    payload, solution = solved_mk01
    with session_scope() as session:
        version = commit_solution(session, instance_row=_inst(session),
                                  payload=payload, solution=solution)
        vid = version.id
        assert version.version_number == 1
        assert version.schedule_type == "BASELINE"
        assert version.parent_version_id is None
        assert version.failed_machine_ids is None
        assert version.payload_hash == payload_hash(payload)
        assert version.makespan == solution["makespan"]

    with session_scope() as session:
        n = session.query(ScheduleEntry).filter(
            ScheduleEntry.version_id == vid).count()
        assert n == len(solution["assignments"])
        bad = session.query(ScheduleEntry).filter(
            ScheduleEntry.version_id == vid,
            ScheduleEntry.status != "SCHEDULED").count()
        assert bad == 0                      # baseline: no clock -> SCHEDULED


def test_commit_refuses_infeasible(built_db, solved_mk01):
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    payload, _ = solved_mk01
    bogus = {"status": "INFEASIBLE", "objective_value": 0, "makespan": 0,
             "total_tardiness": 0, "assignments": [],
             "solve_duration_seconds": 0.0}
    with session_scope() as session:
        with pytest.raises(ValueError):
            commit_solution(session, instance_row=_inst(session),
                            payload=payload, solution=bogus)


def test_commit_atomic_on_garbage(built_db, solved_mk01):
    from sqlalchemy import select

    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    payload, solution = solved_mk01
    solution = dict(solution)
    solution["assignments"] = [dict(solution["assignments"][0])]
    solution["assignments"][0]["machine_id"] = "GHOST"

    before = None
    with session_scope() as session:
        before = session.query(ScheduleVersion).count()
    try:
        with session_scope() as session:
            commit_solution(session, instance_row=_inst(session),
                            payload=payload, solution=solution)
    except Exception:
        pass
    with session_scope() as session:
        assert session.query(ScheduleVersion).count() == before


def test_status_mirroring_with_clock(built_db, solved_mk01):
    from coe.db.models.fjsp import Operation
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    payload, solution = solved_mk01
    horizonish = max(a["end"] for a in solution["assignments"])
    now = horizonish // 2
    with session_scope() as session:
        commit_solution(session, instance_row=_inst(session),
                        payload=payload, solution=solution, now=now)
        statuses = set(session.query(Operation.status).filter(
            Operation.instance_id == _inst(session).id)).union()
    assert {"SCHEDULED"} <= statuses
    assert statuses & {"COMPLETED", "IN_PROGRESS"}


def test_rollback_chain_and_floor(built_db, solved_mk01):
    from sqlalchemy import func

    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import session_scope

    from coe.solver.committer import RollbackFloor, rollback_active

    payload, solution = solved_mk01
    with session_scope() as session:
        inst = _inst(session)
        top = session.query(
            func.coalesce(func.max(ScheduleVersion.version_number), 0)
        ).filter(ScheduleVersion.instance_id == inst.id).scalar_one()
        commit_solution(session, instance_row=inst, payload=payload,
                        solution=solution)                     # top+1
        commit_solution(session, instance_row=inst, payload=payload,
                        solution=solution)                     # top+2
        rolled, active = rollback_active(session, inst)
        assert (rolled, active) == (top + 2, top + 1)
        rolled, active = rollback_active(session, inst)
        assert (rolled, active) == (top + 1, top)
        with pytest.raises(RollbackFloor):
            rollback_active(session, inst)
```

Note: this module shares database state across tests (versions accumulate on mk01); every assertion is therefore written relative (`coalesce(max(...))`), never absolute. Do not parallelize this file.

- [ ] **Step 2: Run to verify failure**

Run: `docker compose up -d && uv run pytest tests/solver/test_committer.py -q`
Expected: FAIL — ModuleNotFoundError `coe.solver.committer`.

- [ ] **Step 3: Implement `coe/solver/committer.py`**

```python
"""Solution JSON -> versioned database rows (spec §3.3, §4, §8)."""
import hashlib
import json

from sqlalchemy import select

from coe.db.models.fjsp import Job, Machine, Operation
from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
from coe.db.models.workers import Worker
from coe.solver.identifier import parse_op_id


class RollbackFloor(Exception):
    """Refusing to roll back the last remaining active version (§8)."""


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()


def commit_solution(session, *, instance_row, payload, solution,
                    failed_machine_names=(), now=None) -> ScheduleVersion:
    if solution["status"] not in ("OPTIMAL", "FEASIBLE"):
        raise ValueError(
            f"refusing to commit {solution['status']} solution "
            "(only OPTIMAL/FEASIBLE are committed, §3.3)")
    iid = instance_row.id

    latest = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == iid)
        .order_by(ScheduleVersion.version_number.desc())
        .with_for_update().first()
    )
    version_number = latest.version_number + 1 if latest else 1

    cfg = payload["config"]
    failed_ids = None
    if payload["schedule_type"] == "RECOVERY":
        failed_ids = sorted(set(failed_machine_names)) or None

    version = ScheduleVersion(
        instance_id=iid,
        version_number=version_number,
        schedule_type=payload["schedule_type"],
        solver_status=solution["status"],
        objective_value=float(solution["objective_value"]),
        makespan=int(solution["makespan"]),
        total_tardiness=int(solution["total_tardiness"]),
        alpha_weight=float(cfg.get("alpha", 1.0)),
        beta_weight=float(cfg.get("beta", 1.0)),
        time_limit_seconds=int(cfg.get("time_limit_seconds", 60)),
        solve_duration_seconds=float(solution["solve_duration_seconds"]),
        failed_machine_ids=failed_ids,
        parent_version_id=payload.get("parent_version_id"),
        rolled_back=False,
        payload_hash=payload_hash(payload),
        payload_json=payload,
    )
    session.add(version)
    session.flush()

    machine_ids = dict(session.query(Machine.name, Machine.id)
                       .filter(Machine.instance_id == iid).all())
    worker_ids = dict(session.query(Worker.name, Worker.id)
                      .filter(Worker.instance_id == iid).all())
    job_ids = dict(session.query(Job.name, Job.id)
                   .filter(Job.instance_id == iid).all())
    ops_by_key: dict[tuple[str, int], Operation] = {}
    for jname, jid in job_ids.items():
        for o in session.query(Operation).filter(
                Operation.job_id == jid).all():
            ops_by_key[(jname, o.sequence_number)] = o

    for a in solution["assignments"]:
        key = parse_op_id(a["operation_id"])
        op = ops_by_key[key]
        session.add(ScheduleEntry(
            instance_id=iid, version_id=version.id, operation_id=op.id,
            machine_id=machine_ids[a["machine_id"]],
            worker_id=(worker_ids[a["worker_id"]]
                       if a.get("worker_id") else None),
            start_time=a["start"], end_time=a["end"],
            processing_time=a["processing_time"],
            setup_time=a.get("setup_time", 0),
            is_frozen=bool(a["is_frozen"]),
            status="FROZEN" if a["is_frozen"] else "SCHEDULED"))
        if now is None:
            op.status = "SCHEDULED"
        elif a["end"] <= now:
            op.status = "COMPLETED"
        elif a["start"] <= now < a["end"]:
            op.status = "IN_PROGRESS"
        else:
            op.status = "SCHEDULED"

    for b in payload.get("blocked_operations", []):
        ops_by_key[parse_op_id(b["operation_id"])].status = "BLOCKED"

    session.flush()
    return version


def commit_solution_autocommit(instance_name, payload, solution,
                               failed_machine_names=(), now=None) -> int:
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = commit_solution(session, instance_row=inst,
                                  payload=payload, solution=solution,
                                  failed_machine_names=failed_machine_names,
                                  now=now)
        return version.id


def rollback_active(session, instance_row) -> tuple[int, int]:
    rows = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == instance_row.id,
                ScheduleVersion.solver_status.in_("OPTIMAL", "FEASIBLE"),
                ScheduleVersion.rolled_back.is_(False))
        .order_by(ScheduleVersion.version_number.desc())
        .with_for_update().all()
    )
    if not rows:
        raise RollbackFloor("no active version to roll back")
    if len(rows) == 1:
        raise RollbackFloor(
            f"version {rows[0].version_number} is the last remaining active "
            "version; rollback refused (floor, spec §8)")
    victim, survivor = rows[0], rows[1]
    victim.rolled_back = True
    session.flush()
    return victim.version_number, survivor.version_number
```

Remove the unused `select` import if lint flags it.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/solver/test_committer.py -q`
Expected: 5 passed.

- [ ] **Step 5: Regression**

Run: `uv run pytest -q -m "not mqtt"`
Expected: green everywhere.

- [ ] **Step 6: Commit**

```bash
git add coe/solver/committer.py tests/solver/test_committer.py
git commit -m "feat(solver): transactional committer with mirroring and rollback floor"
```

---

## Part 5b — CLI wiring + failure injection (Tier 3)

### Task 17: `solve` / `machine` / `schedule` commands

**Files:**
- Modify: `coe/cli.py` (helpers + parser + dispatch)
- Test: `tests/solver/test_cli_solve.py`

**Interfaces:**
```bash
uv run python -m coe.cli solve baseline --instance factory_demo_01 \
    [--alpha F] [--beta F] [--time-limit N]
uv run python -m coe.cli solve recovery --instance NAME --failed-machine M [M...] \
    [--at MINUTE] [--alpha F] [--beta F] [--time-limit N]
uv run python -m coe.cli machine restore --instance NAME --machine M [--at MINUTE]
uv run python -m coe.cli schedule show --instance NAME
uv run python -m coe.cli schedule rollback --instance NAME
```
- Omitted weight flags fall back to Settings defaults (§9 layering).
- `solve recovery`: resolves the reference clock (`--at`, else latest telemetry, else loud ValueError), injects one FAILURE per named machine through `ingest_telemetry_event` with content-derived id `cli-{sha256(instance|machine)[:8]}` (idempotent re-runs), then builds a RECOVERY payload at that clock, solves, checks invariants, commits with `now` for mirroring.
- `machine restore`: closes open-ended windows at the resolved clock (`max(from+1, at)`), flips status ACTIVE; errors when no open window exists.
- `schedule show`: prints the active version summary + per-entry lines sorted by (machine, start).
- `schedule rollback`: prints `rolled back X -> active Y`; floor violations exit non-zero with the reason.

- [ ] **Step 1: Write failing tests**

Create `tests/solver/test_cli_solve.py`:

```python
"""Tier 3: end-to-end CLI flow over factory_demo_01 (spec §11 Tier 3).

Sequential by design: each command advances shared database state.
Do not parallelize this module.
"""
import subprocess

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def cli(*args):
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", *args],
        capture_output=True, text=True,
    )
    return r


def _sql(q, **params):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        return c.execute(text(q), params)


def test_baseline_commits_version_one():
    r = cli("solve", "baseline", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "version=1" in r.stdout
    row = _sql(
        "SELECT solver_status, schedule_type FROM schedule_versions "
        "WHERE version_number = 1").one()
    assert row[0] in ("OPTIMAL", "FEASIBLE") and row[1] == "BASELINE"
    n = _sql(
        "SELECT count(*) FROM operations o "
        "JOIN instances i ON i.id = o.instance_id "
        "WHERE i.name='factory_demo_01' AND o.status = 'SCHEDULED'").scalar_one()
    assert n > 0


def test_show_prints_active_schedule():
    r = cli("schedule", "show", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "version=1" in r.stdout and "M0" in r.stdout


def test_recovery_injects_through_ingest_and_commits():
    r = cli("solve", "recovery", "--instance", "factory_demo_01",
            "--failed-machine", "M3", "--at", "1000")
    assert r.returncode == 0, r.stderr
    assert "version=2" in r.stdout
    failed = _sql(
        "SELECT failed_machine_ids FROM schedule_versions "
        "WHERE version_number = 2").scalar_one()
    assert failed == ["M3"]
    parent = _sql(
        "SELECT parent_version_id FROM schedule_versions "
        "WHERE version_number = 2").scalar_one()
    assert parent == 1
    telemetry = _sql(
        "SELECT count(*) FROM telemetry_events te "
        "JOIN instances i ON i.id = te.instance_id "
        "WHERE i.name='factory_demo_01' AND te.event_type='FAILURE' "
        "AND te.message_id LIKE 'cli-%'").scalar_one()
    assert telemetry == 1
    status = _sql(
        "SELECT m.status FROM machines m "
        "JOIN instances i ON i.id = m.instance_id "
        "WHERE i.name='factory_demo_01' AND m.name='M3'").scalar_one()
    assert status == "FAILED"
    live_on_failed = _sql(
        "SELECT count(*) FROM schedule_entries se "
        "JOIN schedule_versions sv ON sv.id = se.version_id "
        "WHERE sv.version_number = 2 AND se.machine_id IN ("
        "  SELECT m.id FROM machines m JOIN instances i ON i.id=m.instance_id "
        "  WHERE i.name='factory_demo_01' AND m.name='M3')"
        "AND se.status = 'SCHEDULED'").scalar_one()
    assert live_on_failed == 0
    completed = _sql(
        "SELECT count(*) FROM operations o "
        "JOIN instances i ON i.id = o.instance_id "
        "WHERE i.name='factory_demo_01' AND o.status='COMPLETED'").scalar_one()
    assert completed > 0


def test_repeat_recovery_is_idempotent_on_telemetry():
    r = cli("solve", "recovery", "--instance", "factory_demo_01",
            "--failed-machine", "M3", "--at", "1000")
    assert r.returncode == 0, r.stderr
    assert "version=3" in r.stdout
    telemetry = _sql(
        "SELECT count(*) FROM telemetry_events te "
        "JOIN instances i ON i.id = te.instance_id "
        "WHERE i.name='factory_demo_01' AND te.message_id LIKE "
            "'cli-%' AND te.event_type='FAILURE'").scalar_one()
    assert telemetry == 1


def test_restore_closes_window_and_activates():
    r = cli("machine", "restore", "--instance", "factory_demo_01",
            "--machine", "M3")
    assert r.returncode == 0, r.stderr
    until, status = _sql(
        "SELECT w.downtime_until, m.status FROM machine_downtime_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "JOIN machines m ON m.id = w.machine_id AND m.instance_id = i.id "
        "WHERE i.name='factory_demo_01' AND m.name='M3' "
        "ORDER BY w.downtime_from LIMIT 1").one()
    assert until is not None and status == "ACTIVE"


def test_rollback_chain_then_floor():
    r = cli("schedule", "rollback", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "rolled back 3" in r.stdout and "active 2" in r.stdout
    r = cli("schedule", "rollback", "--instance", "factory_demo_01")
    assert r.returncode == 0, r.stderr
    assert "rolled back 2" in r.stdout and "active 1" in r.stdout
    r = cli("schedule", "rollback", "--instance", "factory_demo_01")
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "refused" in combined or "floor" in combined.lower()
    active = _sql(
        "SELECT version_number FROM schedule_versions sv "
        "JOIN instances i ON i.id = sv.instance_id "
        "WHERE i.name = 'factory_demo_01' "
        "AND sv.rolled_back = false AND sv.solver_status IN "
        "('OPTIMAL','FEASIBLE')").scalar_one()
    assert active == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose up -d && uv run pytest tests/solver/test_cli_solve.py -q`
Expected: FAIL — unrecognized arguments (`solve`) ⇒ non-zero return codes.

- [ ] **Step 3: Implement — three anchored edits to `coe/cli.py`**

**Edit A — helpers**, inserted directly above `def build_parser():`:

```python
def _weight_args(p):
    p.add_argument("--alpha", type=float, default=None)
    p.add_argument("--beta", type=float, default=None)
    p.add_argument("--time-limit", type=int, default=None,
                   dest="time_limit")


def _weight_overrides(args) -> dict:
    from coe.config import get_settings

    s = get_settings()
    return {
        "alpha": args.alpha if args.alpha is not None
        else s.solver_alpha_weight,
        "beta": args.beta if args.beta is not None else s.solver_beta_weight,
        "time_limit_seconds": args.time_limit if args.time_limit is not None
        else s.solver_time_limit_seconds,
    }


def _instance_or_die(session, name):
    from coe.db.models.provenance import Instance

    inst = (session.query(Instance)
            .filter(Instance.name == name).one_or_none())
    if inst is None:
        raise SystemExit(f"unknown instance '{name}'")
    return inst


def _cli_message_id(instance_name: str, machine_name: str) -> str:
    import hashlib

    digest = hashlib.sha256(
        f"{instance_name}|{machine_name}".encode()).hexdigest()
    return f"cli-{digest[:8]}"


def _solve_common(session, inst, payload, *, now=None,
                  failed=()):
    from coe.solver.committer import commit_solution
    from coe.solver.engine import solve
    from coe.solver.invariants import check_solution

    solution = solve(payload)
    problems = check_solution(payload, solution)
    if problems:
        raise SystemExit("INVARIANT VIOLATIONS:\n"
                         + "\n".join(problems))
    version = commit_solution(session, instance_row=inst,
                              payload=payload, solution=solution,
                              failed_machine_names=failed, now=now)
    print(f"solved {inst.name}: version={version.version_number} "
          f"status={version.solver_status} makespan={version.makespan} "
          f"tardiness={version.total_tardiness} "
          f"duration={version.solve_duration_seconds}s")


def _run_solve(args) -> None:
    from coe.db.session import session_scope

    from coe.solver.payload_builder import (
        build_payload,
        resolve_reference_clock,
    )

    w = _weight_overrides(args)
    if args.solve_cmd == "baseline":
        with session_scope() as session:
            inst = _instance_or_die(session, args.instance)
            payload = build_payload(session, instance_row=inst, **w)
            _solve_common(session, inst, payload)
        return

    # recovery
    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        now = resolve_reference_clock(session, inst.id, args.at)
    from coe.mqtt.ingest import ingest_telemetry_event

    for m in args.failed_machine:
        created = ingest_telemetry_event({
            "message_id": _cli_message_id(args.instance, m),
            "instance_id": args.instance,
            "resource_kind": "MACHINE",
            "machine_id": m,
            "event_type": "FAILURE",
            "occurred_at": now,
            "severity": "HIGH",
            "reason": "cli-recovery-injection"})
        print(f"injected FAILURE {m} at t={now} "
              f"({'new' if created[1] else 'duplicate-suppressed'})")
    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        payload = build_payload(session, instance_row=inst, **w,
                                schedule_type="RECOVERY", now=now,
                                failed_machine_names=tuple(
                                    args.failed_machine))
        _solve_common(session, inst, payload, now=now,
                      failed=tuple(args.failed_machine))


def _run_restore(args) -> None:
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine
    from coe.db.session import session_scope

    from coe.solver.payload_builder import resolve_reference_clock

    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        mrow = (session.query(Machine)
                .filter(Machine.instance_id == inst.id,
                        Machine.name == args.machine).one_or_none())
        if mrow is None:
            raise SystemExit(f"unknown machine '{args.machine}'")
        now = resolve_reference_clock(session, inst.id, args.at)
        opens = (session.query(MachineDowntimeWindow)
                 .filter(MachineDowntimeWindow.instance_id == inst.id,
                         MachineDowntimeWindow.machine_id == mrow.id,
                         MachineDowntimeWindow.downtime_until.is_(None))
                 .all())
        if not opens:
            raise SystemExit(f"no open outage window for {args.machine}")
        for w in opens:
            w.downtime_until = max(w.downtime_from + 1, now)
        mrow.status = "ACTIVE"
        print(f"restored {args.machine} at t={now}")


def _run_show(args) -> None:
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    from coe.solver.identifier import op_id
    from coe.solver.payload_builder import _load_active_snapshot

    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        ver, entries = _load_active_snapshot(session, inst.id)
        if ver is None:
            raise SystemExit("no active schedule")
        print(f"version={ver.version_number} type={ver.schedule_type} "
              f"status={ver.solver_status} makespan={ver.makespan} "
              f"tardiness={ver.total_tardiness}")
        mnames = dict(session.query(Machine.id, Machine.name)
                      .filter(Machine.instance_id == inst.id).all())
        wnames = dict(session.query(Worker.id, Worker.name)
                      .filter(Worker.instance_id == inst.id).all())
        ops = (session.query(Operation, Job.name)
               .join(Job, Job.id == Operation.job_id)
               .filter(Operation.instance_id == inst.id).all())
        opnames = {o.id: op_id(jname, o.sequence_number) for o, jname in ops}
        for e in sorted(entries.values(),
                        key=lambda x: (x.machine_id, x.start_time)):
            print(f"  {mnames[e.machine_id]:<6} "
                  f"W={wnames.get(e.worker_id, '-'):<6} "
                  f"{opnames[e.operation_id]:<10} "
                  f"[{e.start_time},{e.end_time}) "
                  f"proc={e.processing_time} setup={e.setup_time} "
                  f"{'FROZEN' if e.is_frozen else e.status}")


def _run_rollback(args) -> None:
    from coe.db.session import session_scope

    from coe.solver.committer import RollbackFloor, rollback_active

    with session_scope() as session:
        inst = _instance_or_die(session, args.instance)
        try:
            rolled, active = rollback_active(session, inst)
        except RollbackFloor as exc:
            raise SystemExit(str(exc))
        print(f"rolled back {rolled} -> active {active}")
```

**Edit B — parser**, inserted just before `    mq = sub.add_parser("mqtt")` inside `build_parser()`:

```python
    sv = sub.add_parser("solve")
    sv_sub = sv.add_subparsers(dest="solve_cmd", required=True)
    sb = sv_sub.add_parser("baseline")
    sb.add_argument("--instance", required=True)
    _weight_args(sb)
    sr = sv_sub.add_parser("recovery")
    sr.add_argument("--instance", required=True)
    sr.add_argument("--failed-machine", nargs="+", required=True,
                    dest="failed_machine")
    sr.add_argument("--at", type=int, default=None)
    _weight_args(sr)

    mc = sub.add_parser("machine")
    mc_sub = mc.add_subparsers(dest="machine_cmd", required=True)
    mr = mc_sub.add_parser("restore")
    mr.add_argument("--instance", required=True)
    mr.add_argument("--machine", required=True)
    mr.add_argument("--at", type=int, default=None)

    sch = sub.add_parser("schedule")
    sch_sub = sch.add_subparsers(dest="schedule_cmd", required=True)
    shw = sch_sub.add_parser("show")
    shw.add_argument("--instance", required=True)
    rback = sch_sub.add_parser("rollback")
    rback.add_argument("--instance", required=True)
```

**Edit C — dispatch**, inserted in `main()` immediately before the final `elif args.group == "mqtt":` branch:

```python
    elif args.group == "solve":
        _run_solve(args)

    elif args.group == "machine":
        if args.machine_cmd == "restore":
            _run_restore(args)

    elif args.group == "schedule":
        if args.schedule_cmd == "show":
            _run_show(args)
        if args.schedule_cmd == "rollback":
            _run_rollback(args)
```

Note: `_run_solve` reads `args.at` only on the recovery subcommand — argparse sets it there; baseline never touches it.

- [ ] **Step 4: Run the Tier 3 suite**

Run: `docker compose up -d && uv run pytest tests/solver/test_cli_solve.py -q`
Expected: 7 passed (runtime dominated by two full demo solves; allow minutes).

- [ ] **Step 5: Full regression**

Run: `uv run pytest -q -m "not mqtt"`
Expected: green everywhere.

- [ ] **Step 6: Commit**

```bash
git add coe/cli.py tests/solver/test_cli_solve.py
git commit -m "feat(cli): solve/machine/schedule commands with ingest-path injection"
```

<!-- PART-5-END -->

---

## Part 6 — Benchmark validation + acceptance sweep

> **Suite-ordering contract:** files under `tests/solver/` run alphabetically. `built_db` initializes once (reset + imports + scenario). `test_cli_solve` runs before the payload suites and mutates `factory_demo_01`; the recovery fixtures remain valid because the seeded version number (901) outranks CLI versions (1–3) and the clock test's telemetry (t=1234) outranks injected telemetry (t=1000). `test_z_acceptance.py` deliberately sorts last and audits end-state rows. Do not rename these files casually.

### Task 18: Tier 1 — MK01 published optimum

**Files:**
- Test: `tests/solver/test_benchmark_mk01.py`

**Interfaces:** none new. Pins spec §12 criterion 2 and Tier 1.

- [ ] **Step 1: Write the benchmark test**

Create `tests/solver/test_benchmark_mk01.py`:

```python
"""Tier 1: pure MK01 must solve to the published optimum (makespan = 40)."""
import pytest

pytestmark = [pytest.mark.db, pytest.mark.benchmark]


@pytest.fixture(scope="module")
def solved(built_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    from coe.solver.engine import solve
    from coe.solver.payload_builder import build_payload

    with session_scope() as session:
        inst = session.query(Instance).filter(Instance.name == "mk01").one()
        payload = build_payload(session, instance_row=inst,
                                alpha=1.0, beta=1.0, time_limit_seconds=120)
        return payload, solve(payload)


def test_mk01_optimal_makespan_40(solved):
    _, sol = solved
    assert sol["status"] == "OPTIMAL"
    assert sol["makespan"] == 40


def test_mk01_schedule_valid_and_overlap_free(solved):
    from coe.solver.invariants import check_solution

    payload, sol = solved
    assert check_solution(payload, sol) == []

    occupancy: dict[str, list[tuple[int, int]]] = {}
    for a in sol["assignments"]:
        start_busy = a["start"] - a.get("setup_time", 0)
        occupancy.setdefault(a["machine_id"], []).append(
            (start_busy, a["end"]))
    for m, ivs in occupancy.items():
        ivs.sort()
        for (_, e_prev), (s_next, _) in zip(ivs, ivs[1:]):
            assert s_next >= e_prev, f"overlap on {m}: {ivs}"
```

- [ ] **Step 2: Run**

Run: `docker compose up -d && uv run pytest tests/solver/test_benchmark_mk01.py -q`
Expected: 2 passed (solve proves optimality in seconds at this size).

- [ ] **Step 3: Commit**

```bash
git add tests/solver/test_benchmark_mk01.py
git commit -m "test(solver): tier 1 MK01 published optimum (makespan 40)"
```

---

### Task 19: Acceptance sweep (`test_z_acceptance.py`, runs last)

**Files:**
- Test: `tests/solver/test_z_acceptance.py`

**Interfaces:** none new; consolidates remaining §12 criteria that are cheapest to assert against end-state rows (worker-duration audit on the committed baseline, empty-pending commit, restore re-inclusion).

- [ ] **Step 1: Write the acceptance file**

Create `tests/solver/test_z_acceptance.py`:

```python
"""Spec §12 acceptance sweep — filename sorts last on purpose.

Audits the END STATE left by earlier solver suites (factory_demo_01 carries
CLI-created versions; mk01 carries committer versions) plus two standalone
checks. Criterion map (second-amendment numbering included):
  c3  -> cli stdout status line        (test_cli_solve::baseline)
  c5  -> recovery tests + invariants   (earlier files)
  c14 -> test_restore_reinclusion_here
  c16 -> test_empty_pending_commits_trivial_optimal
  c17 -> test_committed_durations_match_worker_times
Everything else maps 1:1 onto tasks recorded in this plan's appendix.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from coe.solver.engine import solve

pytestmark = pytest.mark.db

FIX = Path(__file__).resolve().parent / "fixtures"


def _sql(q, **params):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        return c.execute(text(q), params)


def test_committed_durations_match_worker_times():
    """c17: every active-version entry's duration equals the assigned
    worker's duration, or the machine-level alternative when workerless."""
    mismatches = _sql(
        """
        SELECT count(*) FROM schedule_entries se
        JOIN instances i ON i.id = se.instance_id
        JOIN schedule_versions sv ON sv.id = se.version_id
        WHERE i.name = 'factory_demo_01'
          AND sv.version_number = 1
          AND (
            CASE
              WHEN se.worker_id IS NOT NULL THEN
                se.processing_time != COALESCE((
                  SELECT wt.processing_time
                  FROM operation_machine_worker_times wt
                  WHERE wt.operation_id = se.operation_id
                    AND wt.machine_id = se.machine_id
                    AND wt.worker_id = se.worker_id), -1)
              ELSE
                se.processing_time != COALESCE((
                  SELECT a.processing_time
                  FROM operation_machine_alternatives a
                  WHERE a.operation_id = se.operation_id
                    AND a.machine_id = se.machine_id), -1)
            END
          )
        """
    ).scalar_one()
    assert mismatches == 0


def test_empty_pending_commits_trivial_optimal():
    """c16: all-frozen payload commits OPTIMAL with makespan = frozen end."""
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    from coe.solver.committer import commit_solution

    p = json.loads((FIX / "empty_pending.json").read_text())
    p["parent_version_id"] = None          # standalone commit; no FK anchor
    sol = solve(p)
    assert sol["status"] == "OPTIMAL"
    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == "factory_demo_01").one())
        version = commit_solution(session, instance_row=inst,
                                  payload=p, solution=sol)
        vid = version.id
    row = _sql(
        "SELECT makespan, solver_status, total_tardiness "
        "FROM schedule_versions WHERE id = :v", v=vid).one()
    assert tuple(row) == (15, "OPTIMAL", 5)


def test_restore_reinclusion(built_db):
    """c14: a stripped failed machine re-enters the next built payload after
    its window is closed and status flipped back."""
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    from coe.solver.payload_builder import build_payload

    sid = built_db["scenario_id"]
    with session_scope() as session:
        inst = session.get(Instance, sid)
        mid = session.query(Machine.id).filter(
            Machine.instance_id == sid, Machine.name == "M2").scalar_one()
        session.add(MachineDowntimeWindow(
            instance_id=sid, machine_id=mid, downtime_from=900,
            downtime_until=None, reason="FAILURE", source_event_ids=[]))

        p1 = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                           time_limit_seconds=30, schedule_type="RECOVERY",
                           now=1000, failed_machine_names=("M2",))
        assert "M2" not in p1["machines"]

        window = (session.query(MachineDowntimeWindow)
                  .filter(MachineDowntimeWindow.machine_id == mid,
                          MachineDowntimeWindow.downtime_until.is_(None))
                  .one())
        window.downtime_until = max(window.downtime_from + 1, 1100)

        p2 = build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                           time_limit_seconds=30, schedule_type="RECOVERY",
                           now=1000, failed_machine_names=("M2",))
        assert "M2" in p2["machines"]
```

- [ ] **Step 2: Run**

Run: `docker compose up -d && uv run pytest tests/solver/test_z_acceptance.py -q`
Expected: 3 passed.

- [ ] **Step 3: Full-suite gate**

Run: `docker compose up -d && uv run pytest -q`
Expected: entire suite green (MQTT included). First full run after Phase 2 will take noticeably longer than the Phase-1 ~85 s — two demo-scale solves dominate; that is expected and acceptable.

- [ ] **Step 4: Commit**

```bash
git add tests/solver/test_z_acceptance.py
git commit -m "test(solver): acceptance sweep - durations audit, empty-pending, reinclusion"
```

---

### Task 20: Drive-by — MAINTENANCE-event crash hardening (Phase 1 fix, user-approved)

**Files:**
- Modify: `coe/mqtt/ingest.py` (one branch)
- Test: `tests/mqtt/test_maintenance_hardening.py`

**Background:** `ingest_telemetry_event` line ~197 computes
`duration_until = occurred_at + (estimated_downtime or 0)` for non-FAILURE machine events; a `MAINTENANCE` event without `estimated_downtime` yields `until == from`, tripping the `downtime_interval_valid` CHECK at execute. Missing duration must mean **open-ended**, mirroring FAILURE semantics.

- [ ] **Step 1: Write the failing test**

Create `tests/mqtt/test_maintenance_hardening.py`:

```python
"""Regression: MAINTENANCE without estimated_downtime => open-ended window."""
import pytest

pytestmark = pytest.mark.db


def test_maintenance_without_duration_is_open_ended(demo_scenario):
    from sqlalchemy import select

    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.fjsp import Machine
    from coe.db.session import session_scope

    from coe.mqtt.ingest import ingest_telemetry_event

    sid = demo_scenario
    with session_scope() as session:
        mid = session.query(Machine.id).filter(
            Machine.instance_id == sid, Machine.name == "M5").scalar_one()
        telemetry_id, created = ingest_telemetry_event({
            "message_id": "maint-open-1",
            "instance_id": "factory_demo_01",
            "resource_kind": "MACHINE",
            "machine_id": "M5",
            "event_type": "MAINTENANCE",
            "occurred_at": 700,
            "severity": "LOW",
        })
        assert created and telemetry_id > 0
        row = session.execute(
            select(MachineDowntimeWindow)
            .where(MachineDowntimeWindow.instance_id == sid,
                   MachineDowntimeWindow.machine_id == mid)
            .order_by(MachineDowntimeWindow.id.desc())
        ).scalars().first()
        assert row is not None
        assert row.downtime_from == 700
        assert row.downtime_until is None      # open-ended, no CHECK crash
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose up -d && uv run pytest tests/mqtt/test_maintenance_hardening.py -q`
Expected: FAIL/ERROR — IntegrityError `downtime_interval_valid` (raised at flush inside ingest).

- [ ] **Step 3: One-line fix in `coe/mqtt/ingest.py`**

Replace (in the MACHINE branch):

```python
            else:
                duration_until = payload.occurred_at + (
                    payload.estimated_downtime or 0
                )
```

with:

```python
            else:
                duration_until = (
                    payload.occurred_at + payload.estimated_downtime
                    if payload.estimated_downtime is not None else None
                )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/mqtt/test_maintenance_hardening.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/mqtt/ingest.py tests/mqtt/test_maintenance_hardening.py
git commit -m "fix(ingest): maintenance without estimated_downtime opens window"
```

---

## Appendix A — Spec coverage matrix (self-review vs §12 + amendments)

| §12 criterion | Where pinned |
| --- | --- |
| 1 payload valid JSON from factory_demo_01 | `test_payload_baseline::shape` |
| 2 MK01 optimal 40 | Task 18 |
| 3 factory baseline FEASIBLE/OPTIMAL within limit | CLI baseline output (`status=`), benchmark fixture |
| 4 Tier 2b table pass | `test_engine_constraints` (18 tests) incl. amendment rows |
| 5 recovery excludes failed machines, frozen intact | `test_payload_recovery`, `test_cli_solve::recovery`, invariants |
| 6 version/entry rows populated | `test_committer`, CLI v1/v2 assertions |
| 7 active_schedule view latest | `tests/db/test_schedule_models::view_picks_latest_feasible` |
| 8 rollback via CLI | `test_rollback_chain_then_floor` |
| 9 determinism identical output | engine bytes test + builder bytes test |
| 10 INFEASIBLE ⇒ nothing committed | committer refuse + atomic-garbage + engine infeasible |
| 11 time_limit honored | engine property |
| 12 horizon covers frozen ends/releases | `test_horizon` + `frozen_respected` |
| 13 rollback floor | committer floor + CLI floor |
| 14 restore closes window, re-enters payload | Task 19 reinclusion + CLI restore |
| 15 clipping emits warnings | dropped/partial/worker-clip tests |
| 16 empty-pending trivial OPTIMAL commit | Task 19 |
| 17 worker-duration entries | Task 19 SQL audit + truncation tests |
| 18 status mirroring (2nd amend) | committer mirroring tests + CLI completed-count |
| 19 priority weights mean-preserving; null deadlines zero | weight unit tests + null_deadline fixtures |
| 20 injection lands in telemetry once | CLI idempotency test |
| 21 material capacity: inventory never negative; receipt-timed conflicts delay ops *(Amend 2026-08-24)* | Task 12B `material_timing` + reservoir floor |
| 22 permanent over-demand INFEASIBLE, nothing committed; advisory shortfall warnings persist *(Amend 2026-08-24)* | Task 12B `over_demand` + Task 12A warning assertions |

**Part 4.5 note (Amendment 2026-08-24):** Tasks 12A/12B implement material physics per amended spec §5/§6.11/§7. The reservoir primitive replaces the user-sketched AddCumulative because supply is time-varying — a fixed-capacity cumulative would let early operations borrow stock that has not arrived yet (the exact Gap-2 bug this amendment closes). Phase 3 owns the sacrifice decision via its own 2026-08-24 amendment.

Amendment-1 coverage: worker-duration combos (`no_worker_fallback`, `worker_unavailable`, truncation rescale), absence windows read directly (`env` fixture + clip tests).

Documented deviations from spec letter (all benign, reviewer-visible): `objective_value` stored as Float ratio (spec listed no type); single-column FKs on schedule tables matching newer model style; UNKNOWN/MODEL_INVALID reported as INFEASIBLE; frozen echo `setup_time=0`; `schedule show` imports the private snapshot helper (Phase 3 will promote it). The Phase 1 ingest crash fix ships as Task 20 per the user-approved drive-by decision.

2026-08-24 gap-sweep riders (user-approved, folded into Tasks 12A/12B/15/16): frozen-on-stripped-machine invariant exemption; span includes Σ machine downtime (old infeasible-downtime fixture flips to delayed-start OPTIMAL — honest physics); suspension memory via `jobs.status=BLOCKED` + root `suspended_jobs` + committer job-mirror; status truth (FAILED machines stripped without CLI args; UNAVAILABLE workers get full-horizon unavailability). Spec §6.7 errata remains OPEN with the user: H omits stacking/worker-unavailability terms — harmless in Phase 2 (`_schedule_span` governs domains; H only normalizes) but Phase 4's QAOA reuses H as its horizon seed and must not treat it as a hard bound.

## Appendix B — Running everything

```bash
docker compose up -d
uv run pytest -q                 # full suite, MQTT included
uv run pytest -q -m "not mqtt"   # brokerless environments
uv run pytest -m benchmark       # Tier 1 only
```

Manual smoke (mirrors spec §10):

```bash
uv run python -m coe.cli db reset
uv run python -m coe.cli import mk01
uv run python -m coe.cli import hutter --path data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt
uv run python -m coe.cli import gass
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
uv run python -m coe.cli solve baseline --instance factory_demo_01
uv run python -m coe.cli schedule show --instance factory_demo_01
uv run python -m coe.cli solve recovery --instance factory_demo_01 --failed-machine M3 --at 1000
uv run python -m coe.cli schedule rollback --instance factory_demo_01
uv run python -m coe.cli machine restore --instance factory_demo_01 --machine M3
```

<!-- PART-6-END -->







