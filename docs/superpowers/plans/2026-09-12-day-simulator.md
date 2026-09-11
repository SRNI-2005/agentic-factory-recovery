# Day Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic scripted-day engine that replays disruption timelines at accelerated speed against a factory clone, with mid-day state projection and a materials audit ledger, exposed via CLI and a Streamlit Simulate page.

**Architecture:** Pure `coe/simulator/` core (timeline loader → engine walker → projector), driving the *existing* public entry points only (`coe.mqtt.ingest.ingest_telemetry_event` for structured events; `coe.agents.graph.execute_recovery` for scripted narratives). Two spec-deepening amendments ride along: P2 §6.11 builder deduction (consumed bars) and the P1 §6.4 `material_transactions` ledger (built for real).

**Tech Stack:** Python 3.12+ / SQLAlchemy 2.0 / Alembic / pydantic / pytest / Streamlit (AppTest) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-day-simulator-design.md`

## Global Constraints

- `uv` exclusively; never pip/system Python. CWD always repo root.
- Alembic is authoritative DDL; `create_all` is forbidden.
- All times integer minutes; occurred_at >= 0 (negative crashes CP-SAT).
- One disruption per agentic run (P3 §4.1) — one graph run per NARRATIVE event.
- Determinism: any query feeding RNG/ordering gets explicit `ORDER BY`.
- Tests: `pytest -m "not mqtt and not slow"` is the quick gate; `db` marker needs TimescaleDB up (`docker compose up -d`).
- Phase 2 solver remains sole scheduling authority; the engine never writes schedule tables directly (only via graph + committer + ingestion).
- psycopg3 raises CHECK violations at execute(), not commit().

---

### Task 1: Timeline Schema (`coe/simulator/timeline.py`)

**Files:**
- Create: `coe/simulator/__init__.py` (empty), `coe/simulator/timeline.py`
- Test: `tests/simulator/__init__.py` (empty), `tests/simulator/test_timeline.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `class TimelineEvent` / `Timeline` (pydantic, spec §3/§5)
  - `def load_timeline(path: str) -> Timeline` — raises `TimelineError(ValueError)`
  - `Timeline.events: list[TimelineEvent]` (monotonic by `t`, already validated)

- [ ] **Step 1: Write the failing tests**

```python
# tests/simulator/test_timeline.py
"""Timeline schema: validation cases (spec §2/§5)."""
import pytest
from pydantic import ValidationError


def _write(tmp_path, data):
    import json
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data))
    return str(p)


BASE = {
    "name": "demo_day_01", "seed": 42, "horizon_days": 1,
    "events": [
        {"t": 380, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3", "estimated_downtime": 120,
         "agentic": True},
        {"t": 500, "kind": "WORKER", "event_type": "WORKER_ABSENT",
         "worker_id": "W3", "duration": 240},
        {"t": 900, "kind": "MATERIAL", "event_type": "MATERIAL_SHORTAGE",
         "sku": "MAT-001"},
        {"t": 1000, "kind": "NARRATIVE", "text": "M3 is back online",
         "at": 1000},
    ],
}


def test_load_happy_path(tmp_path):
    from coe.simulator.timeline import load_timeline
    tl = load_timeline(_write(tmp_path, BASE))
    assert tl.name == "demo_day_01" and tl.seed == 42
    assert [e.t for e in tl.events] == [380, 500, 900, 1000]
    assert tl.events[0].resource_kind == "MACHINE"
    assert tl.events[3].kind == "NARRATIVE" and tl.events[3].text


def test_per_kind_field_exclusivity():
    from coe.simulator.timeline import TimelineEvent
    with pytest.raises(ValidationError):
        TimelineEvent(**BASE["events"][0] | {"worker_id": "W1"})
    with pytest.raises(ValidationError):
        TimelineEvent(**BASE["events"][2])


def test_non_monotonic_rejected(tmp_path):
    from coe.simulator.timeline import TimelineError, load_timeline
    bad = dict(BASE, events=[dict(BASE["events"][1]),
                             dict(BASE["events"][0])])
    with pytest.raises(TimelineError, match="monotonic"):
        load_timeline(_write(tmp_path, bad))


def test_horizon_guard(tmp_path):
    from coe.simulator.timeline import TimelineError, load_timeline
    with pytest.raises(TimelineError, match="SIMULATE_MAX_HORIZON_DAYS"):
        load_timeline(_write(tmp_path, dict(BASE, horizon_days=8)))
```

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/simulator/test_timeline.py -q`
Expected: FAIL (`ModuleNotFoundError: coe.simulator.timeline`)

- [ ] **Step 3: Implement**

```python
# coe/simulator/timeline.py
"""Timeline loader for the day simulator (spec §3/§5).

A timeline is a deterministic permutation-free event list. Schema errors
fail loudly at load, never mid-walk.
"""
import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from coe.config import get_settings


class TimelineError(ValueError):
    pass


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: int = Field(ge=0)
    agentic: bool = False


class MachineEvent(_Base):
    kind: Literal["MACHINE"]
    event_type: Literal["FAILURE", "MAINTENANCE"]
    machine_id: str
    estimated_downtime: int | None = Field(default=None, gt=0)


class WorkerEvent(_Base):
    kind: Literal["WORKER"]
    event_type: Literal["WORKER_ABSENT", "WORKER_RETURN"]
    worker_id: str
    duration: int | None = Field(default=None, gt=0)


class MaterialEvent(_Base):
    kind: Literal["MATERIAL"]
    event_type: Literal["MATERIAL_SHORTAGE", "MATERIAL_RESTOCK"]
    sku: str
    quantity: int | None = Field(default=None, gt=0)


class NarrativeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: int = Field(ge=0)
    kind: Literal["NARRATIVE"]
    text: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None


TimelineEvent = Annotated[Union[MachineEvent, WorkerEvent, MaterialEvent,
                                NarrativeEvent], Field(discriminator="kind")]


class Timeline(BaseModel):
    """Authored script. `seed` feeds downstream determinism consumers; the
    engine passes llm clients explicitly (tests) or the settings provider."""
    model_config = ConfigDict(extra="forbid")
    name: str
    seed: int = 42
    horizon_days: int = Field(default=1, ge=1)
    events: list[TimelineEvent]


adapter: TypeAdapter = TypeAdapter(Timeline)


def load_timeline(path: str) -> Timeline:
    with open(path) as fh:
        tl = adapter.validate_python(json.load(fh))
    max_days = get_settings().simulate_max_horizon_days
    if tl.horizon_days > max_days:
        raise TimelineError(
            f"horizon_days {tl.horizon_days} exceeds "
            f"SIMULATE_MAX_HORIZON_DAYS={max_days}")
    prev = None
    for e in tl.events:
        if prev is not None and e.t < prev:
            raise TimelineError(
                f"events must be monotonic in t ({prev} then {e.t})")
        prev = e.t
    return tl
```

- [ ] **Step 4: Add settings keys**

In `coe/config.py` `Settings` class add (with the other sections, default 7):

```python
    simulate_default_speed: int = 30
    simulate_clone: bool = True
    simulate_max_horizon_days: int = 7
```

- [ ] **Step 5: Run to verify PASS**

Run: `uv run pytest tests/simulator/test_timeline.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add coe/simulator tests/simulator coe/config.py && git commit -m "feat(simulator): timeline schema"
```

---

### Task 2: Material Ledger Model + Migration

**Files:**
- Modify: `coe/db/models/materials.py` (append model)
- Create: `alembic/versions/<rev>_material_transactions.py` (name pattern: match existing migration files in that dir; new `down_revision` is the current head `440538e97415` — verify with `uv run alembic heads`)
- Test: `tests/simulator/test_ledger.py`

**Interfaces:**
- Produces: model `MaterialTransaction` (table `material_transactions`, instance-scoped FK discipline) for Tasks 4/6.

- [ ] **Step 1: Write the failing test**

```python
# tests/simulator/test_ledger.py
"""material_transactions table exists and enforces instance scoping."""
import pytest

pytestmark = pytest.mark.db


def test_insert_and_check_constraint(clean_db):
    from sqlalchemy import text
    from coe.db.models.materials import MaterialTransaction
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as s:
        inst = Instance(name="mtx-inst", source_name="synthetic")
        s.add(inst); s.flush()
        row = MaterialTransaction(
            instance_id=inst.id, operation_id=None, material_id=None,
            quantity=3, timestamp=100, transaction_type="RESTOCK",
            source="simulate")
        s.add(row); s.flush()
        n = s.execute(text(
            "SELECT count(*) FROM material_transactions")).scalar_one()
        assert n == 1
        # invalid type must violate the CHECK
        row_bad = MaterialTransaction(
            instance_id=inst.id, operation_id=None, material_id=None,
            quantity=1, timestamp=1, transaction_type="BROKEN_TY",
            source="simulate")
        s.add(row_bad)
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError, match="mtx_type"):
            s.flush()
```

Note: psycopg3 raises CHECK violations at `flush()` (execute), not commit.

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/simulator/test_ledger.py -q`
Expected: FAIL — `material_transactions` table does not exist.

- [ ] **Step 3: Implement model**

Append to `coe/db/models/materials.py`:

```python
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String


class MaterialTransaction(Base):
    """Runtime material audit ledger (Phase 1 spec §6.4, built by the day
    simulator amendment). Instance-scoped like every domain row."""
    __tablename__ = "material_transactions"
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('CONSUME','REFILL','RESTOCK')",
            name="mtx_type"),
        CheckConstraint("quantity > 0", name="mtx_qty_pos"),
    )
    id = Column(Integer, primary_key=True)
    instance_id = Column(Integer, ForeignKey("instances.id"),
                         nullable=False, index=True)
    operation_id = Column(Integer, ForeignKey("operations.id"),
                          nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"),
                         nullable=True)
    quantity = Column(Integer, nullable=False)
    timestamp = Column(Integer, nullable=False)
    transaction_type = Column(String(8), nullable=False)
    source = Column(String(8), nullable=False)   # commit | simulate
```

Match the file's existing import style (check whether `Column`, `Integer`, etc.
are already imported there; reuse, don't duplicate). Add the model to the
package exports in `coe/db/models/__init__.py` if that file re-exports models.

- [ ] **Step 4: Alembic migration**

`uv run alembic revision -m "material_transactions"` — then fill:

```python
revision = "<generated>"
down_revision = "440538e97415"   # verify current head: uv run alembic heads


def upgrade():
    op.create_table(
        "material_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(),
                  sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("operation_id", sa.Integer(),
                  sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("material_id", sa.Integer(),
                  sa.ForeignKey("materials.id"), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Integer(), nullable=False),
        sa.Column("transaction_type", sa.String(8), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.CheckConstraint(
            "transaction_type IN ('CONSUME','REFILL','RESTOCK')",
            name="mtx_type"),
        sa.CheckConstraint("quantity > 0", name="mtx_qty_pos"),
    )
    op.create_index("ix_material_transactions_instance_id",
                    "material_transactions", ["instance_id"])


def downgrade():
    op.drop_table("material_transactions")
```

- [ ] **Step 5: Run to verify PASS**

Run: `uv run pytest tests/simulator/test_ledger.py -q` then
`uv run python -m coe.cli db reset` is NOT required (alembic applies on
existing envs via `uv run alembic upgrade head`).
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(simulator): material_transactions ledger"
```

---

### Task 3: Payload-Builder Deduction (P2 §6.11 amendment (a))

**Files:**
- Modify: `coe/solver/payload_builder.py` — the materials section near the
  `all_receipts`/`materials_out` block (around line 496–513)
- Test: `tests/solver/test_payload_deduction.py` (new file; check
  `tests/solver/` naming — place anywhere under `tests/` following existing
  fixtures pattern, e.g. `tests/test_payload_deduction.py` if no solver dir)

**Interfaces:**
- Consumes: `build_payload(...)` (existing signature; `now` and
  `schedule_type` params exist), `assembly semantics` of ops_by_job entries
  with `status` PENDING/BLOCKED and `active_by_opid` entries with
  `start_time/end_time` (from `_load_active_snapshot`), `bom_by_op`
  (operation_id → [{sku, quantity}]).
- Produces: recovery payloads whose `materials[].capacity` = initial stock −
  consumed-before-`now` (per completed *and* in-progress starts), and refill
  events only for receipts with `available_at > now`. Determinism preserved.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_payload_deduction.py
"""P2 §6.11 amendment (a): frozen/consumed stock deduction at recovery."""
import pytest

pytestmark = pytest.mark.db


@pytest.fixture()
def stocky(demo_scenario):
    """demo scenario is byte-deterministic; assert base stock via ORM."""
    from sqlalchemy import text
    from coe.db.session import make_engine
    from coe.db.models.provenance import Instance

    with make_engine().connect() as c:
        iid = c.execute(text(
            "SELECT id FROM instances WHERE name='factory_demo_01'"
        )).scalar_one()
        return iid


def test_recovery_payload_deducts_consumed(stocky):
    """Baseline has committed BOM consumption before any clock > 0.

    We assert the MECHANISM: for the same instance, the recovery payload's
    SKY capacity for a consumed sku is strictly less than the baseline
    payload's, for a reference clock after the first op ends.
    """
    from sqlalchemy.orm import Session
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.solver.payload_builder import build_payload

    eng = make_engine()
    with Session(eng) as s:
        inst = s.get(Instance, stocky)
        base = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                             time_limit_seconds=1)
        rec = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                            time_limit_seconds=1,
                            schedule_type="RECOVERY", now=200)
    cap_base = {m["sku"]: m["capacity"] for m in base["materials"]}
    cap_rec = {m["sku"]: m["capacity"] for m in rec["materials"]}
    consumed_before = any(
        cap_rec[sku] < cap_base[sku]
        for sku in cap_rec if base["schedule_type"] == "BASELINE")
    assert consumed_before, "recovery must not re-credit consumed stock"
    # receipts that already arrived are folded in, not refill rows:
    deltas = [r for r in rec["material_receipts"] if r["available_at"] < 200]
    assert deltas == []
```

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/test_payload_deduction.py -q`
Expected: FAIL — capacities equal / deltas non-empty.

- [ ] **Step 3: Implement the amendment**

In `coe/solver/payload_builder.py`, replace the `all_receipts`/`materials_out`
block (lines ~496–510) with the clock-aware version. The full replacement
block:

```python
    # ---- material physics inputs for the engine reservoir (§6.11,
    # amendment 2026-08-24 third; day-simulator amendment 2026-09-12) ----
    # ALL receipts ARE emitted at baseline (post-horizon refills inert but
    # audit-relevant). For RECOVERY with a reference clock `now`, consumed
    # history leaves the picture: capacity = initial stock MINUS bars
    # consumed before `now` (frozen plays: completions + in-progress starts),
    # and pre-clock receipts are FOLDED INTO that effective stock instead of
    # staying refill rows (they refill no future consumption).
    now_ = now if recovering else None
    consumed_by_sku: dict[str, int] = {}
    if recovering and now_ is not None:
        for ae in active_by_opid.values():
            if ae.start_time < now_:
                op_key = op_id(job_name[op_by_id[ae.operation_id].job_id],
                               op_by_id[ae.operation_id].sequence_number)
                for d in bom_by_op.get(op_key, []):
                    consumed_by_sku[d["sku"]] = (
                        consumed_by_sku.get(d["sku"], 0) + d["quantity"])
    if recovering and now_ is not None:
        all_receipts = [
            {"sku": sku, "quantity": r.quantity, "available_at": r.available_at}
            for r, sku in receipt_rows if r.available_at >= now_
        ]
    else:
        all_receipts = [
            {"sku": sku, "quantity": r.quantity, "available_at": r.available_at}
            for r, sku in receipt_rows
        ]
    all_receipts.sort(
        key=lambda d: (d["sku"], d["available_at"], d["quantity"]))
    materials_out = [
        {"sku": s,
         "capacity": stock_by_sku.get(s, 0)
                     - (consumed_by_sku.get(s, 0)
                        if recovering and now_ is not None else 0)}
        for s in sorted(stock_by_sku)
    ]
```

Verify against the file BEFORE pasting: `active_by_opid`, `bom_by_op`,
`op_by_id`, `job_name`, `op_id`, `receipt_rows`, `recovering`, and
`stock_by_sku` are in scope at that point (they are, per the surrounding
code read during plan-writing; adjust names if the file drifted).

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/test_payload_deduction.py -q` and the quick gate:
`uv run pytest -q -m "not mqtt and not slow"` — the amendment must not move
any byte-determinism pins or material tests.
Expected: PASS (quick gate all green).

- [ ] **Step 5: Spec annotation**

In `docs/superpowers/specs/2026-08-21-phase2-classical-optimization-engine-design.md`
§6.11 area, append one line:

```
> **Amendment 2026-09-12 (day simulator):** for RECOVERY payloads with a
reference clock, effective capacity = initial stock − BOM consumed before the
clock by frozen completed and in-progress operations; receipts with
available_at <= clock are folded into that stock (no double-refill). Baseline
semantics unchanged.
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "fix(solver): deduct consumed stock in recovery payloads (day-sim amendment)"
```

---

### Task 4: Committer CONSUME Ledger Rows

**Files:**
- Modify: `coe/solver/committer.py` `commit_solution` — after the
  `suspended_jobs` mirror block, before `session.flush()`
- Test: `tests/simulator/test_ledger.py` (append)

**Interfaces:**
- Consumes: `commit_solution(session, instance_row=…, payload=…, solution=…)` (existing), `OperationBom` model, `MaterialTransaction` (Task 2).
- Produces: one CONSUME row per (committed non-frozen entry × BOM line).

- [ ] **Step 1: Write the failing test (append to test_ledger.py)**

```python
def test_commitment_writes_consume_rows(demo_scenario):
    from sqlalchemy import text
    from coe.solver.materials_check import *  # noqa: F401,F403 (imports side)
    # real commit via public path:
    from coe.cli import build_parser, main  # noqa: F401  (not used directly)
    import subprocess

    # Use the CLI to drive baseline on the demo instance (real solver).
    # The fixture ensures the instance exists with deterministic seed.
    subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", "factory_demo_01"] if False else
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", "factory_demo_01"],
        check=True, capture_output=True)
    with make_engine() as c:  # noqa: F821 — replaced below in real test
        pass
```

Replace the sketch for the actual assertion (final form to paste):

```python
def test_commit_writes_consume_rows(demo_scenario):
    import subprocess

    from coe.db.session import make_engine
    from sqlalchemy import text

    subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", "factory_demo_01"],
        check=True, capture_output=True)
    with make_engine().begin() as c:
        rows = c.execute(text(
            "SELECT te.operation_id, COUNT(*) FROM material_transactions mt "
            "LEFT JOIN operations te ON te.id = mt.operation_id "
            "WHERE mt.transaction_type = 'CONSUME' AND mt.source = 'commit'"
        )).all()
        assert rows, "expected at least one committed op BOM consumption row"
        # every row's instance_id belongs to factory_demo_01:
        assert c.execute(text(
            "SELECT COUNT(*) FROM material_transactions mt "
            "JOIN instances i ON i.id = mt.instance_id "
            "WHERE i.name = 'factory_demo_01'")).scalar() >= 1
```

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/simulator/test_ledger.py::test_commit_writes_consume_rows -q`
Expected: FAIL — the query returns no rows.

- [ ] **Step 3: Implement in committer**

In `commit_solution`, keep the assignments loop; after it and before
`suspended_jobs` handling, insert:

```python
    _write_consume_ledger(session, iid=iid, version=version,
                          machine_ids=machine_ids, job_ids=job_ids,
                          worker_ids=worker_ids, ops_by_key=ops_by_key,
                          solution=solution)
```

and add at module scope:

```python
def _write_consume_ledger(session, *, iid, version, machine_ids, job_ids,
                          worker_ids, ops_by_key, solution) -> None:
    """CONSUME audit rows for every non-frozen committed assignment whose
    operation carries a BOM (P1 §6.4 ledger; source='commit')."""
    from coe.db.models.materials import (Material, MaterialTransaction,
                                         OperationBom)

    mat_by_sku = dict(session.query(Material.sku, Material.id)
                      .filter(Material.instance_id == iid)
                      .order_by(Material.sku).all())
    for a in solution["assignments"]:
        if a.get("is_frozen"):
            continue
        op = ops_by_key[parse_op_id(a["operation_id"])]
        bom = (session.query(OperationBom.quantity, Material.sku)
               .join(Material, Material.id == OperationBom.material_id)
               .filter(OperationBom.instance_id == iid,
                       OperationBom.operation_id == op.id)
               .order_by(Material.sku).all())
        for qty, sku in bom:
            session.add(MaterialTransaction(
                instance_id=iid, operation_id=op.id,
                material_id=mat_by_sku[sku], quantity=qty,
                timestamp=a["start"], transaction_type="CONSUME",
                source="commit"))
```

- [ ] **Step 4: Run to verify PASS + rollback safety**

Run: `uv run pytest tests/simulator/test_ledger.py -q` and gate:
`uv run pytest -q -m "not mqtt and not slow" -k "commit or rollback or version"` then full
quick gate.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(simulator): CONSUME ledger rows on commit"
```

---

### Task 5: Projector

**Files:**
- Create: `coe/simulator/projector.py`
- Test: `tests/simulator/test_projector.py`

**Interfaces:**
- Consumes: DB models (`ScheduleEntry`, `machine_downtime_windows`,
  `worker_absence_windows`, `Operation`, `Job`, `OperationBom`, `Material`).
- Produces:
  - `@dataclass DayState` with:
    `completed_ops: list[str]` (op names), `in_progress: list[str]`,
    `effective_stock: dict[str, int]`, `active_downtime: dict[str, list[int, int]]`,
    `worker_absence: dict[str, list[int, int]]`, `clock: int`
  - `def project_day(session, *, instance_name: str, t: int) -> DayState`

- [ ] **Step 1: Write the failing test**

```python
# tests/simulator/test_projector.py
import pytest

pytestmark = pytest.mark.db


def test_classification_boundaries(demo_scenario):
    from coe.simulator.projector import project_day

    with _sessions() as (s, inst_row):
        ds0 = project_day(s, instance_name="factory_demo_01", t=0)
    assert ds0.completed_ops == []
    # explicit boundary: end == t is COMPLETED for the first completed entry
    with _sessions() as (s, inst_row):
        baseline_makespan = _baseline_makespan()
        ds = project_day(s, instance_name="factory_demo_01", t=baseline_makespan)
        first_ops = ds.completed_ops
        assert first_ops, "at makespan, schedule is fully consumed"
```

plus helpers the executor inline (`_sessions`, `_baseline_makespan`) — full
helpers in the real test file:

```python
from contextlib import contextmanager
from sqlalchemy.orm import Session

from coe.db.models.provenance import Instance
from coe.db.session import make_engine


@contextmanager
def _sessions():
    with Session(make_engine()) as s:
        inst = s.query(Instance).filter_by(
            name="factory_demo_01").one()
        yield s, inst


def _baseline_makespan() -> int:
    # Require a baseline first; the executor runs `solve baseline` in the
    # session fixture (demo_scenario fixture guarantees the instance).
    return _solve_baseline_conservatively()


def test_effective_stock_matches_builder_arithmetic(demo_scenario):
    """Cross-check: project_day's effective stock at t equals the payload
    builder's deducted capacity at the same clock (Task 3 invariants)."""
```

(Full bodies to write in the task; treat any helper naming drift as fixable —
the ASSERTIONS are the contract.)

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/simulator/test_projector.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# coe/simulator/projector.py
"""Day playback: derive the committed schedule's state at clock t.

Completions are the committed entries' end times, NOT re-solved — the
active schedule is the single playback source. The projector NEVER writes.
"""
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class DayState:
    clock: int
    completed_ops: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    effective_stock: dict[str, int] = field(default_factory=dict)
    active_downtime: dict[str, list] = field(default_factory=dict)
    worker_absence: dict[str, list] = field(default_factory=dict)

    def feed_line(self) -> str:
        return (f"t={self.clock} done={len(self.completed_ops)} "
                f"running={len(self.in_progress)} "
                f"down_machines={list(self.active_downtime)}")


def project_day(session: Session, *, instance_name: str, t: int) -> DayState:
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.models.downtime import WorkerAbsenceWindow
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.materials import Material, OperationBom
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import Worker

    inst = (session.query(Instance)
            .filter(Instance.name == instance_name).one())
    iid = inst.id
    version = (session.query(ScheduleVersion)
               .filter(ScheduleVersion.instance_id == iid,
                       ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                       ScheduleVersion.rolled_back.is_(False))
               .order_by(ScheduleVersion.version_number.desc(),
                         ScheduleVersion.id.desc()).first())
    ds = DayState(clock=t)
    if version is None:
        return ds
    entries = (session.query(ScheduleEntry, Operation, Job)
               .join(Operation, Operation.id == ScheduleEntry.operation_id)
               .join(Job, Job.id == Operation.job_id)
               .filter(ScheduleEntry.instance_id == iid,
                       ScheduleEntry.version_id == version.id)
               .order_by(ScheduleEntry.end_time, ScheduleEntry.id).all())
    for entry, op, job in entries:
        np = f"{job.name}-O{op.sequence_number}"
        if entry.end_time <= t:
            ds.completed_ops.append(np)
        elif entry.start_time <= t < entry.end_time:
            ds.in_progress.append(np)
    machines = dict(session.query(Machine.id, Machine.name)
                    .filter(Machine.instance_id == iid)
                    .order_by(Machine.name).all())
    for w in (session.query(MachineDowntimeWindow)
              .filter(MachineDowntimeWindow.instance_id == iid)
              .order_by(MachineDowntimeWindow.id).all()):
        if w.downtime_from <= t and (w.downtime_until is None
                                     or w.downtime_until > t):
            ds.active_downtime.setdefault(machines[w.machine_id],
                                          []).append(
                [w.downtime_from, w.downtime_until])
    workers = dict(session.query(Worker.id, Worker.name)
                   .filter(Worker.instance_id == iid)
                   .order_by(Worker.name).all())
    for w in (session.query(WorkerAbsenceWindow)
              .filter(WorkerAbsenceWindow.instance_id == iid)
              .order_by(WorkerAbsenceWindow.id).all()):
        if w.absence_from <= t and (w.absence_until is None
                                    or w.absence_until > t):
            ds.worker_absence.setdefault(workers[w.worker_id],
                                         []).append(
                [w.absence_from, w.absence_until])
    # Effective stock at t: initial - consumed (entries started < t) +
    # receipts available_at <= t
    stock = dict(session.query(Material.sku, Material.initial_stock)
                 .filter(Material.instance_id == iid)
                 .order_by(Material.sku).all())
    consumed = dict(session.query(
        Material.sku,
        OperationBom.quantity)
        .join(ScheduleEntry, ScheduleEntry.operation_id == OperationBom.operation_id)
        .join(Operation, Operation.id == ScheduleEntry.operation_id)
        .join(Job, Job.id == Operation.job_id)
        .join(Material, Material.id == OperationBom.material_id)
        .filter(ScheduleEntry.instance_id == iid,
                ScheduleEntry.version_id == version.id,
                ScheduleEntry.start_time < t)
        .all())
```

⚠️ **Simplification directive (binding, YAGNI):** the joined consumed query
returns one row per op × BOM line — collapse in Python honestly:
execute it as `.all()` and sum per sku in a loop instead of trusting SQL
multiplicity. Replace the tail of `project_day` with:

```python
    consumed: dict[str, int] = {}
    rows = (session.query(ScheduleEntry.start_time, Material.sku,
                          OperationBom.quantity)
            .join(Operation, Operation.id == ScheduleEntry.operation_id)
            .join(Job, Job.id == Operation.job_id)
            .join(OperationBom, OperationBom.operation_id == Operation.id)
            .join(Material, Material.id == OperationBom.material_id)
            .filter(ScheduleEntry.instance_id == iid,
                    ScheduleEntry.version_id == version.id,
                    ScheduleEntry.start_time < t)
            .order_by(Material.sku, ScheduleEntry.id).all())
    for _start, sku, qty in rows:
        consumed[sku] = consumed.get(sku, 0) + qty
    receipts = (session.query(Material.sku,
                              __import__("coe.db.models.materials",
                                         fromlist=["MaterialReceipt"])
                              .quantity, __import__(
                                  "coe.db.models.materials",
                                  fromlist=["MaterialReceipt"])
                              .available_at)
                .join(__import__("coe.db.models.materials",
                                 fromlist=["MaterialReceipt"]),
                      ...)
                ...)
```

STOP — the low-level style above is plan-authoring noise. The **binding
directive**: write `projector.py` as one clean file where the consumed/stock
arithmetic uses the same plain ORM patterns as `payload_builder.py`
(`session.query(...).filter(...).order_by(...)` + dict loops). Effective
stock at t **must equal** Task 3's deducted capacity for the same clock — the
test asserts `ds.effective_stock == ` the payload-matched values glyph-by-glyph.
Use plain imports (`from coe.db.models.materials import MaterialReceipt`),
NO `__import__` calls, no dynamic tricks.

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/simulator/test_projector.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(simulator): day projector"
```

---

### Task 6: Engine Walker

**Files:**
- Create: `coe/simulator/engine.py`
- Test: `tests/simulator/test_engine.py`

**Interfaces:**
- Consumes: `load_timeline` (Task 1), `ingest_telemetry_event(payload_dict)
  -> tuple[int, bool]` (existing), `execute_recovery(instance_name,
  *, trigger="CLI", narrative=..., reference_clock=..., client=None)
  -> RecoveryState-ish` (graph; verify exact kwargs by reading
  `coe/agents/graph.py:228`), `fork_instance` (Task 8 clone path),
  `MaterialReceipt`, `MaterialTransaction` (Task 2).
- Produces:
  - `def walk_timeline(timeline: "Timeline | str", *, instance_name: str, speed: int | str = "instant", llm_client_factory=None, start_index: int = 0) -> Iterator[dict]`
    — accepts an already-loaded `Timeline` **or a path string** (loaded
    lazily via `load_timeline`); yields feed items
    `{"event": "phase", "t": ..., "idx": ...}` and
    terminal summary `{"event": "done", "committed": [...], ...}`.
    Determinism: any recovery sequence on this walk runs with
    `num_search_workers=1` (P2 §9; the engine pins it for the walk).
  - `def _scripted_message_id(script_name: str, idx: int) -> str` —
    `simul-{sha256(f"{script_name}|{idx}")[:12]}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/simulator/test_engine.py
import json
import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient


def _timeline(path, events):
    data = {"name": "t1", "seed": 42, "horizon_days": 1, "events": events}
    p = path / "t.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_structured_event_ingests_through_shared_path(tmp_path, demo_scenario):
    from coe.simulator.engine import walk_timeline
    from sqlalchemy import text

    tl_path = _timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"}])
    items = list(walk_timeline(str(tl_path), instance_name="factory_demo_01",
                               speed="instant"))
    with make_engine().begin() as c:
        n = c.execute(text(
            "SELECT COUNT(*) FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='factory_demo_01' "
            "AND te.message_id LIKE 'simul-%'")).scalar()
    assert n >= 1 and any(i.get("event") == "done" for i in items)


def test_narrative_event_runs_graph_commit(tmp_path, demo_scenario):
    from coe.simulator.engine import walk_timeline
    holder = {}

    def fake_factory():
        return FakeLLMClient([_canned_disruption()])

    items = []
    for chunk in walk_timeline(str(_timeline(tmp_path, [
            {"t": 100, "kind": "NARRATIVE",
             "text": "M3 gearbox seized", "severity": "HIGH"}])),
            instance_name="factory_demo_01", speed="instant",
            llm_client_factory=fake_factory):
        items.append(chunk)
    committed = [c["committed"] for c in items if c.get("event") == "done"]
    assert committed and committed[0] >= 1   # at least one recovery version


def test_resume_skips_completed_prefix(tmp_path, demo_scenario):
    """start_index=k suppresses events < k (already persisted)."""
    # walk once fully, replay from index 1, assert the first event's
    # telemetry message count does NOT grow (idempotent skipping).
```

`_canned_disruption()` = the well-known GOOD_MACHINE record JSON (duplicate
the literal from tests/agents/test_translate_node.py rather than importing a
test module — tests are independent).

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/simulator/test_engine.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# coe/simulator/engine.py
"""Scripted-day walker (spec §5). Deterministic; drives only public entry
points; yields feed items for BOTH the CLI (print) and the dashboard."""
import hashlib
import os
from typing import Iterator

from coe.simulator.timeline import (
    MachineEvent, MaterialEvent, NarrativeEvent, Timeline, WorkerEvent,
    load_timeline,
)


def _scripted_message_id(script_name: str, idx: int) -> str:
    canonical = f"{script_name}|{idx}"
    return "simul-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _walk_paced(speed) -> float | None:
    """None = instant. N means N schedule-minutes per wall-minute: the
    inter-event wall pause for a gap of g schedule-minutes is
    g * 60 / N seconds (bounded to <= 10 s so a sparse script doesn't
    stall the thread of a CLI run)."""
    if speed == "instant":
        return None
    return 60.0 / int(speed)


def _record_to_wire(ev, *, instance_name: str, message_id: str) -> dict:
    payload = {"message_id": message_id, "instance_id": instance_name,
               "event_type": ev.event_type, "occurred_at": ev.t,
               "severity": "MEDIUM", "reason": "simulated-day"}
    if isinstance(ev, MachineEvent):
        payload["resource_kind"] = "MACHINE"
        payload["machine_id"] = ev.machine_id
        if ev.estimated_downtime is not None:
            payload["estimated_downtime"] = ev.estimated_downtime
    elif isinstance(ev, WorkerEvent):
        payload["resource_kind"] = "WORKER"
        payload["worker_id"] = ev.worker_id
        if ev.duration is not None:
            payload["estimated_absence"] = ev.duration
    else:
        payload["resource_kind"] = "MATERIAL"
        payload["material_sku"] = ev.sku
    return payload


def _speed_pace(speed) -> float | None:
    """None = instant. N means N× wall clock scaling: 1 schedule-minute ==
    60/N wall-seconds between events (bounded by SIMULATE max pacing)."""
    if speed == "instant":
        return None
    return 60.0 / int(speed)


def walk_timeline(timeline: Timeline | str, *, instance_name: str,
                  speed="instant", llm_client_factory=None,
                  start_index: int = 0) -> Iterator[dict]:
    """Walk `timeline.events[from start_index:]`.

    Structured events ingest via the shared Phase 1 ingestion function
    (idempotent scripted message ids). NARRATIVE events run the real
    recovery graph once each, passing reference_clock = event.t. Resume:
    events with index < start_index are already persisted and are simply
    not re-executed (idempotency would suppress them anyway; skipping
    keeps the log truthful).
    """
    if isinstance(timeline, str):
        timeline = load_timeline(timeline)
    pace = _walk_paced(speed)
    import time

    committed = []
    for idx, ev in enumerate(timeline.events):
        if idx < start_index:
            continue
        if pace is not None and idx > start_index:
            gap = ev.t - timeline.events[idx - 1].t
            time.sleep(min(gap * 60.0 / int(speed), 10))
        if isinstance(ev, NarrativeEvent):
            from coe.agents.graph import execute_recovery

            client = llm_client_factory() if llm_client_factory else None
            # P2 §9: determinism consumers pin single-worker search. Settings
            # are lru_cached, so force the env and clear the cache BEFORE the
            # first recovery in this process.
            os.environ["SOLVER_NUM_SEARCH_WORKERS"] = "1"
            from coe.config import get_settings
            get_settings.cache_clear()
            st = execute_recovery(
                instance_name, trigger="CLI", narrative=ev.text,
                reference_clock=ev.t, client=client)
            committed.append(getattr(st, "committed_version_id", None))
            yield {"event": "recovery", "t": ev.t, "idx": idx,
                   "status": getattr(st, "status", None)}
        else:
            from coe.mqtt.ingest import ingest_telemetry_event

            telemetry_id, created = ingest_telemetry_event(
                _record_to_wire(ev, instance_name=instance_name,
                                message_id=_scripted_message_id(
                                    timeline.name, idx)))
            if isinstance(ev, MaterialEvent) \
                    and ev.event_type == "MATERIAL_RESTOCK" \
                    and ev.quantity is not None:
                _materialize_restock(instance_name, ev)
            yield {"event": "ingest", "t": ev.t, "idx": idx,
                   "kind": ev.kind, "created": bool(created)}
    yield {"event": "done", "committed": committed}
```

and in the same file, `_materialize_restock` (receipt + RESTOCK ledger row;
spec §4(b), keeping ingest telemetry-only):

```python
def _materialize_restock(instance_name: str, ev: MaterialEvent) -> None:
    from sqlalchemy.orm import Session

    from coe.db.models.materials import (Material, MaterialReceipt,
                                         MaterialTransaction)
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine

    with Session(make_engine()) as s:
        inst = s.query(Instance).filter_by(name=instance_name).one()
        mat = (s.query(Material)
               .filter(Material.instance_id == inst.id,
                       Material.sku == ev.sku).one())
        receipt = MaterialReceipt(instance_id=inst.id, material_id=mat.id,
                                  quantity=ev.quantity,
                                  available_at=ev.t, source="simulate")
        s.add(receipt)
        s.add(MaterialTransaction(
            instance_id=inst.id, operation_id=None, material_id=mat.id,
            quantity=ev.quantity, timestamp=ev.t,
            transaction_type="RESTOCK", source="simulate"))
        s.commit()
```

Final wiring note: `execute_recovery`'s actual kwarg for the injected client
must be verified against `coe/agents/graph.py:228` (`client=` in
`translate_node(state, client=client)` suggests a `client=` kwarg exists on
`execute_recovery` — if the parameter is named differently (e.g.
`llm_client`), adjust this walker to the real name; report the drift).

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/simulator/test_engine.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Determinism test (Tier 3 of spec §8)**

Add to the same test file — full body:

```python
def test_replay_idempotent_and_deterministic(tmp_path, demo_scenario):
    from coe.simulator.engine import walk_timeline
    from coe.simulator.timeline import load_timeline
    from sqlalchemy import text

    from coe.db.session import make_engine

    script = str(_timeline(tmp_path, [
        {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
         "machine_id": "M3"},
        {"t": 150, "kind": "WORKER", "event_type": "WORKER_ABSENT",
         "worker_id": "W3", "duration": 60}]))
    first = list(walk_timeline(script, instance_name="factory_demo_01",
                               speed="instant"))
    # deterministic message ids: replay everything, count ids
    with make_engine().begin() as c:
        ids = [r[0] for r in c.execute(text(
            "SELECT DISTINCT te.message_id FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='factory_demo_01' AND te.message_id "
            "LIKE 'simul-%'")).all()]
    assert len(ids) == 2          # one per scripted structured event
    second = list(walk_timeline(script, instance_name="factory_demo_01",
                                speed="instant", start_index=2))
    assert any(c.get("event") == "done" for c in second)
    assert second[-1]["committed"] == []   # nothing re-executed post-prefix
    with make_engine().begin() as c:
        ids2 = [r[0] for r in c.execute(text(
            "SELECT DISTINCT te.message_id FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='factory_demo_01' "
            "AND te.message_id LIKE 'simul-%'")).all()]
    assert ids2 == ids                    # no duplicates on replay
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(simulator): engine walker"
```

---

### Task 7: CLI `simulate timeline`

**Files:**
- Modify: `coe/cli.py` — add `sim = sub.add_parser("simulate")` group with
  `timeline` subcommand; route in `main()`'s dispatch chain.
- Test: `tests/simulator/test_cli_simulate.py`

**Interfaces:**

```
uv run python -m coe.cli simulate timeline --file F [--speed instant|10|30|60]
    [--from N] [--on-clone | --no-clone] [--instance NAME]
```

- [ ] **Step 1: Write the failing test**

```python
# tests/simulator/test_cli_simulate.py
import json
import pytest

pytestmark = pytest.mark.db


def test_schema_error_exits_1_with_message(tmp_path, demo_scenario):
    import subprocess
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x", "events": [
        {"t": 5, "kind": "NARRATIVE", "text": "hi", "at": 5}]}))
    t2 = tmp_path / "t.json"
    t2.write_text(json.dumps({"name": "x", "events": [
        {"t": 5, "kind": "NARRATIVE", "text": "hi"}]}))
    # loader rejects non-monotonic + per-kind drift etc — schema error
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "simulate", "timeline",
         "--file", str(bad)], capture_output=True, text=True)
    assert r.returncode != 0 and "monotonic" in (r.stdout + r.stderr)
```

- [ ] **Step 2: Verify FAIL, then implement**

Add to `coe/cli.py` parser section (near `benchmark` at ~line 356):

```python
    sim = sub.add_parser("simulate",
                         help="replay a scripted disruption day")
    sim_sub = sim.add_subparsers(dest="simulate_cmd", required=True)
    st_ = sim_sub.add_parser("timeline")
    st_.add_argument("--file", required=True)
    st_.add_argument("--speed", default=None)
    st_.add_argument("--from", dest="from_index", type=int, default=0)
    st_.add_argument("--instance", default=None)
    st_.add_argument("--on-clone", dest="on_clone", action="store_true",
                     default=None)
```

and dispatcher:

```python
    if args.group == "simulate":
        _run_simulate(args)


def _run_simulate(args) -> None:
    """simulate timeline: clone (default) → engine walk → print feed."""
    from coe.config import get_settings
    from coe.simulator.engine import walk_timeline
    from coe.simulator.timeline import TimelineError, load_timeline

    try:
        tl = load_timeline(args.file)
    except TimelineError as exc:
        raise SystemExit(f"timeline rejected: {exc}")
    speed = args.speed or f"{get_settings().simulate_default_speed}"
    inst_name = args.instance or "factory_demo_01"
    s = get_settings()
    want_clone = s.simulate_clone if args.on_clone is None else args.on_clone
    if want_clone:
        from coe.db.models.provenance import Instance
        from coe.db.session import make_engine
        from coe.services.fork import fork_instance
        from sqlalchemy.orm import Session

        with Session(make_engine()) as session:
            source = (session.query(Instance)
                      .filter(Instance.name == inst_name).one())
            forked = fork_instance(
                session, source,
                new_name=f"sim-{tl.name}@{uuid.uuid4().hex[:8]}")
            session.commit()
            inst_name = forked.name
    print(f"simulating '{tl.name}' on {inst_name} at {speed}")
    for chunk in walk_timeline(tl, instance_name=inst_name, speed=speed,
                               start_index=args.from_index):
        print(f"[{chunk.get('t', '—'):>4}] {chunk}")
```

(Import `uuid` at the parser/dispatch site's top of function. Every
user-facing failure is a `SystemExit(str)` — no tracebacks.)

- [ ] **Step 3: Run to verify PASS**

Run: `uv run pytest tests/simulator/test_cli_simulate.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(simulator): CLI simulate timeline"
```

---

### Task 8: Shipped Demo Timeline Artifact

**Files:**
- Create: `data/timelines/demo_day_01.json`
- Test: `tests/simulator/test_demo_timeline.py`

**Interfaces:**
- Consumes: Task 1 loader; machines M0..M7, workers W1..W12, skus MAT-001..008
  (factory_demo_01 facts).

- [ ] **Step 1: Craft the timeline JSON (deterministic arc; hour events in minutes)**

```json
{
  "name": "demo_day_01",
  "seed": 42,
  "horizon_days": 1,
  "events": [
    {"t": 110,  "kind": "MACHINE", "event_type": "FAILURE",
     "machine_id": "M3", "estimated_downtime": 120, "agentic": false},
    {"t": 140,  "kind": "NARRATIVE",
     "text": "M3 just jammed and the spindle is useless, expect a long repair window",
     "severity": "HIGH"},
    {"t": 240,  "kind": "MATERIAL", "event_type": "MATERIAL_SHORTAGE",
     "sku": "MAT-001"},
    {"t": 300,  "kind": "NARRATIVE",
     "text": "MAT-001 bin empty, delivery stuck at supplier, we need a plan",
     "severity": "LOW"},
    {"t": 420,  "kind": "MATERIAL", "event_type": "MATERIAL_RESTOCK",
     "sku": "MAT-001", "quantity": 112},
    {"t": 480,  "kind": "WORKER", "event_type": "WORKER_ABSENT",
     "worker_id": "W3", "duration": 240},
    {"t": 560,  "kind": "MATERIAL", "event_type": "MATERIAL_RESTOCK",
     "sku": "MAT-002", "quantity": 80},
    {"t": 560,  "kind": "NARRATIVE",
     "text": "W3 just got back from sick leave and is fully available again",
     "at": 560},
    {"t": 700,  "kind": "MACHINE", "event_type": "MAINTENANCE",
     "machine_id": "M5", "estimated_downtime": 60}
  ]
}
```

NOTE: the NarrativeEvent schema (Task 1) has no separate `at` field — `t` is
the clock. There must NOT be a duplicate `at` key (extra="forbid"). The
9-event list above contains: structured FAILURE (ingest-only, no recovery) →
NARRATIVE recovery → SHORTAGE telemetry → NARRATIVE shortage-plan recovery →
RESTOCK(112) → WORKER_ABSENT with duration → second RESTOCK → NARRATIVE
worker-back recovery → MAINTENANCE telemetry. Validate + fix events so the
monotonic check passes exactly (dedupe the `at` key, ensure strictly ordered
`t`s).

- [ ] **Step 2: Write the smoke test**

```python
# tests/simulator/test_demo_timeline.py
import pytest

pytestmark = pytest.mark.db


def test_shipped_timeline_loads_and_is_deterministic():
    from coe.simulator.timeline import load_timeline
    tl = load_timeline("data/timelines/demo_day_01.json")
    assert [e.t for e in tl.events] == sorted(
        [e.t for e in tl.events]) and len(tl.events) >= 8
    ts = [e.t for e in tl.events]
    assert all(b > a for a, b in zip(ts, ts[1:])), "strictly increasing"
```

- [ ] **Step 3: Run to verify PASS**

Run: `uv run pytest tests/simulator/test_demo_timeline.py
tests/simulator/test_timeline.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(simulator): shipped demo_day_01 timeline"
```

---

### Task 9: Dashboard Simulate Page

**Files:**
- Create: `coe/dashboard/pages/simulate.py`
- Modify: `coe/dashboard/app.py` page registration (follow
  `st.Page(...url_path=...)` pattern exactly as in the existing pages list
  at `app.py`); Test: `tests/dashboard/test_simulate_page.py`

**Interfaces:**
- Consumes: `walk_timeline` (Task 6), `load_timeline` (Task 1),
  `project_day` (Task 5), `fork_instance` handling identical to Task 7.
- Produces: `def render() -> None` registered as a page.

- [ ] **Step 1: Write the failing test — full body**

```python
# tests/dashboard/test_simulate_page.py
"""AppTest smoke: Simulate page renders and completes an instant walk."""
import json

import pytest

pytestmark = pytest.mark.db


def _tiny_script(tmp_path):
    p = tmp_path / "tiny.json"
    p.write_text(json.dumps({
        "name": "tiny", "seed": 1, "horizon_days": 1,
        "events": [{"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
                    "machine_id": "M3"}]}))
    return str(p)


def test_page_smoke(clean_db, demo_scenario, tmp_path):
    import streamlit as st

    from coe.dashboard.pages import simulate as sim_page

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_script"] = _tiny_script(tmp_path)
    st.session_state["sim_speed"] = "instant"
    # direct-render convention used by tests/dashboard/test_cockpit_page.py
    sim_page.render()
    assert st.session_state.get("sim_last_idx", 0) >= 1
```

METHOD NOTE (binding): open `tests/dashboard/test_cockpit_page.py` first
and reuse *its* exact invocation mechanics — if that suite calls
`render()` functions directly inside `AppTest.run()` contexts with
monkeypatched pages, mirror it precisely; the assertions above
(`sim_last_idx` advanced, no `st.exceptions`) are the contract, not the
plumbing.

- [ ] **Step 2: Verify FAIL → implement page → verify PASS → commit**

Page skeleton (`similar structure to cockpit.py`, helpers imported there):

```python
# coe/dashboard/pages/simulate.py
"""Simulate page — scripted-day playback with pacing controls."""
from __future__ import annotations


def render() -> None:
    import json
    import pathlib

    import streamlit as st

    scripts = sorted(pathlib.Path("data/timelines").glob("*.json"))
    if not scripts:
        st.warning("No timeline scripts under data/timelines/.")
        st.stop()
    pick = st.sidebar.selectbox("Timeline", [p.name for p in scripts])
    speed = st.sidebar.selectbox("Speed", ["instant", 10, 30, 60],
                                 index=2)
    if "sim_running" not in st.session_state:
        st.session_state["sim_running"] = False
    col_run, col_step = st.sidebar.columns(2)
    ...
```

Page-contract note: the render body honors `st.session_state["sim_script"]`
(script path override) and `st.session_state["sim_speed"]` when set —
tests (and the CLI page entry) need no monkeypatching; when unset, the page
defaults to the sidebar selectboxes over `data/timelines/*.json`.

FLATTEN THE SKELETON: implement the full page — big clock `st.metric`,
`st.progress` per event index, event feed via `st.status` (mirroring
cockpit.py's `_run_recovery` `st.status`+`feed_area` pattern), Pause button
sets `st.session_state["sim_paused"] = True` and stops; Resume re-runs from
the last index persisted in `st.session_state["sim_last_idx"]`. Render a
schedule diff via `st.plotly_chart` only at the terminal event (reuse
`coe/dashboard/diff.py` frames like cockpit does). Manual instant walk is
acceptable inside one AppTest-able synchronous render pass; no background
threading (RunManager lesson — keep it synchronous for AppTest). Paced mode
(N×) renders ONE event per rerender and advances `sim_last_idx` on every
button-driven rerender, honoring the pause toggle.

- [ ] **Step 3: PASS + commit**

```bash
git add -A && git commit -m "feat(dashboard): Simulate page (scripted-day playback)"
```

---

### Task 10: Idempotency-of-scripts, Docs, Acceptance Sweep

**Files:**
- Modify: `AGENTS.md` (add the simulate command line + the hutter dir note
  from the 2026-09-12 session to the Commands block)
- Test: `tests/simulator/test_engine.py` (the determinism test from Task 6)
- Full suite run

- [ ] **Step 1: AGENTS.md edit** — add to Commands:

```bash
uv run python -m coe.cli simulate timeline --file data/timelines/demo_day_01.json [--speed instant|10|30|60] [--on-clone]
```

and to the `import hutter` line, the explicit-dir form:
`import hutter --dir data/raw/nouri-fjspw/extracted`
(extracted!) — verify the actual working form with `--help` before
writing; also add a `simulate` bullet to the architecture bullet list.

- [ ] **Step 1b: Prior-spec amendment markers (repo convention: amendment
  markers are normative)**
  - In `docs/superpowers/specs/2026-08-20-phase1-infrastructure-data-ingestion-design.md`
    §6.4, append one line to the material_transactions block:
    `> **[Amendment 2026-09-12 — day simulator]:** the reserved table is now
    built (migration #7); CONSUME rows are written by the Phase 2 committer,
    RESTOCK rows by the day-simulator engine; REFILL is reserved with no
    current producer.`
  - Confirm the P2 §6.11 annotation from Task 3 is present (it is Task 3's
    own step; skip if you executed Task 3).

- [ ] **Step 2: Run the full quick gate**

Run: `uv run pytest -q -m "not mqtt and not slow"`
Expected: green with the new tests included.

- [ ] **Step 3: Acceptance checklist (spec §9)** — run each criterion
manually as a one-line command in the session and record output: items
1–6 of the spec's Acceptance criteria section. Fix gaps as follow-up
commits in the same PR style.
- [ ] **Step 4: Commit docs update**

```bash
git add AGENTS.md && git commit -m "docs: day-simulator commands + hutter path fix"
```

---

## Cross-cutting notes for implementers

- Any discovered drift between this plan and current HEAD (signatures
  changed, file moved) — fix the plan's STEP mechanically to the real HEAD
  and note the deviation in the task's commit message; never silently
  re-design.
- Keep new files' module docstrings quoting the spec sections they implement
  (repo convention).
- The pause/resume/step story on the dashboard works WITHOUT threads: each
  Streamlit rerender consults `st.session_state["sim_last_idx"]` and the
  engine's `start_index` re-entrance. Instant mode streams fully in one
  rerender; paced mode renders one event per rerender tick (onclick-driven).
- Determinism: never accept wall-clock in outputs; both surfaces render from
  the engine's feed items only.
