# Multi-Resource Disruptions — Implementation Plan (Phase 1 delta)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the 2026-08-23 spec amendments — worker and material disruptions enter through the same telemetry pipeline as machine failure (`worker_absence_windows`, kind-routed subscriber, three MQTT topics, CLI proofs) — against the already-completed Phase 1 codebase.

**Architecture:** Pure delta on existing layers: one Alembic migration extends `telemetry_events` (kind columns + exactly-one-resource CHECK) and adds `worker_absence_windows`; `coe/mqtt/ingest.py` generalizes its payload model and routes state effects by kind (machine path byte-preserved); the subscriber's topic validator learns `worker`/`material` segment types; edge stub + CLI gain `test-absence`/`test-shortage` proofs; acceptance sweep gains a multi-resource criterion test. No solver, agent, or quantum changes.

**Tech Stack:** unchanged — Python 3.12+, uv, SQLAlchemy ≥ 2.0, Alembic, psycopg ≥ 3, paho-mqtt, pydantic-settings, pytest; TimescaleDB/Mosquitto via Docker Compose.

## Global Constraints

Carried verbatim from the Phase 1 plan/specs plus this amendment:

- **Amendment authority:** docs/superpowers/specs/2026-08-20-phase1-infrastructure-data-ingestion-design.md (Amendment 2026-08-23 markers), phase2 §3.1 bullet, phase3 §4.1 union.
- `resource_kind` ∈ `MACHINE | WORKER | MATERIAL`; telemetry CHECK enforces exactly one of `machine_id` / `worker_id` / `material_id` non-null.
- Event-type domains per kind: MACHINE `FAILURE|MAINTENANCE`, WORKER `WORKER_ABSENT|WORKER_RETURN`, MATERIAL `MATERIAL_SHORTAGE|MATERIAL_RESTOCK`.
- `worker_absence_windows` mirrors machine downtime semantics: nullable `absence_until` (open-ended), union of overlapping/touching intervals under a **per-worker advisory lock**, `source_event_ids` accumulate.
- `WORKER_ABSENT` sets worker status `UNAVAILABLE`; `WORKER_RETURN` closes open absences and restores `AVAILABLE`. MATERIAL events are telemetry-only — no receipts, no state.
- Wire format stays **flat** (existing MQTT payloads keep working): `resource_kind` optional; when absent and `machine_id` present, infer `MACHINE`. This legacy-inference rule is normative.
- Subscriber validates topic ≡ payload for ALL three segment types (`machine|worker|material`); malformed topics reject.
- Alembic migrations authoritative; raw SQL only for TimescaleDB-specific ops. Determinism discipline: every query feeding RNG or float summation gets explicit ORDER BY.
- Existing machine-path tests must keep passing unmodified (except where they gain coverage).
- CLI entrypoint `uv run python -m coe.cli`; argparse stdlib only. Do not push; work on branch `phase1`.

## File Map

| File | Responsibility |
| --- | --- |
| `coe/db/models/downtime.py` | Modified: TelemetryEvent kind columns + CHECKs; new WorkerAbsenceWindow |
| `alembic/versions/*_multi_resource.py` | New migration (column adds w/ staged default, constraint creates, new table) |
| `coe/mqtt/ingest.py` | Modified: ResourceEventPayload model (legacy inference), resource resolution, kind-routed effects, generalized interval merger |
| `coe/mqtt/subscriber.py` | Modified: topic validator accepts machine/worker/material segments |
| `coe/mqtt/edge_stub.py` | Modified: generic `publish_resource_event` (+ `publish_failure` preserved as wrapper) |
| `coe/cli.py` | Modified: `mqtt test-absence`, `mqtt test-shortage` |
| `tests/db/test_multi_resource_models.py` | New: schema-level kind/absence tests |
| `tests/db/test_ingest_routing.py` | New: kind-routing, absence union/close, legacy inference, material telemetry-only |
| `tests/db/test_downtime_union.py` | Unchanged (must stay green) |
| `tests/mqtt/test_multi_resource_roundtrip.py` | New: broker round-trips for worker + material |
| `tests/db/test_acceptance.py` | Modified: multi-resource criterion-11 companion test |

## Task Index

1. Schema: telemetry kind columns + `worker_absence_windows`
2. Ingest routing: kind-generalized payload, effects, topic validator
3. Edge stubs + CLI proofs + broker round-trips
4. Acceptance extension + final verification

### Task 1: Telemetry Kind Columns + `worker_absence_windows`

**Files:**
- Modify: `coe/db/models/downtime.py`
- Create: migration `alembic/versions/*_multi_resource_telemetry.py`
- Test: `tests/db/test_multi_resource_models.py`

**Interfaces:**
- Consumes: existing models `Worker`/`Material`/`Machine`.
- Produces: `TelemetryEvent` with nullable `machine_id`, new nullable `worker_id`/`material_id`, non-null `resource_kind`; new model `WorkerAbsenceWindow`. Task 2 inserts through these.

**TDD order matters:** failing tests first.

- [ ] **Step 1: Write failing tests `tests/db/test_multi_resource_models.py`**

```python
import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _ids(session):
    """Minimal instance + one machine + one worker + one material."""
    from coe.db.models.fjsp import Machine
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.models.workers import Worker

    inst = Instance(name=f"t-mr-{id(session) % 100000}", source_name="test")
    session.add(inst)
    session.flush()
    m = Machine(instance_id=inst.id, name="M1")
    w = Worker(instance_id=inst.id, name="W1")
    mat = Material(instance_id=inst.id, sku="SKU-1", initial_stock=10)
    session.add_all([m, w, mat])
    session.flush()
    return inst, m.id, w.id, mat.id


def _event(**kw):
    from coe.db.models.downtime import TelemetryEvent

    base = dict(
        occurred_at=10, instance_id=None, message_id="m",
        machine_id=None, worker_id=None, material_id=None,
        resource_kind="MACHINE", event_type="FAILURE",
        received_at=10, payload_json={},
    )
    base.update(kw)
    return TelemetryEvent(**base)


def test_two_resources_rejected(clean_db):
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, mid, wid, _mat = _ids(s)
        try:
            s.add(_event(instance_id=inst.id, message_id="a",
                         machine_id=mid, worker_id=wid))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_zero_resources_rejected(clean_db):
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, *_ = _ids(s)
        try:
            s.add(_event(instance_id=inst.id, message_id="b"))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_worker_kind_event_persists(clean_db):
    from coe.db.session import session_scope

    with session_scope() as s:
        inst, _, wid, _ = _ids(s)
        ev = _event(instance_id=inst.id, message_id="c",
                    resource_kind="WORKER", event_type="WORKER_ABSENT",
                    worker_id=wid)
        s.add(ev)
        s.flush()
        assert ev.machine_id is None and ev.worker_id == wid


def test_absence_window_open_ended_ok(clean_db):
    from coe.db.models.downtime import WorkerAbsenceWindow
    from coe.db.session import session_scope

    with session_scope() as s:
        inst, _, wid, _ = _ids(s)
        row = WorkerAbsenceWindow(
            instance_id=inst.id, worker_id=wid,
            absence_from=100, absence_until=None,
            reason="WORKER_ABSENT", severity="MEDIUM",
            source_event_ids=[7],
        )
        s.add(row)
        s.flush()
        assert row.absence_until is None


def test_absence_interval_order_enforced(clean_db):
    from coe.db.models.downtime import WorkerAbsenceWindow
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, _, wid, _ = _ids(s)
        try:
            s.add(WorkerAbsenceWindow(
                instance_id=inst.id, worker_id=wid,
                absence_from=100, absence_until=50,
                reason="WORKER_ABSENT", source_event_ids=[]))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised
```

Note: `_event` omits `resource_kind` in the two rejection tests deliberately — those must fail on the exactly-one CHECK before any kind-domain concern.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/db/test_multi_resource_models.py -v`
Expected: errors — unknown columns (`resource_kind`, `worker_id`) and unknown table `worker_absence_windows`.

- [ ] **Step 3: Modify `coe/db/models/downtime.py`**

Replace the `TelemetryEvent` class with:

```python
class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        CheckConstraint("occurred_at >= 0", name="occurred_nonnegative"),
        CheckConstraint("received_at >= 0", name="received_nonnegative"),
        CheckConstraint(
            "((machine_id IS NOT NULL)::int + (worker_id IS NOT NULL)::int "
            "+ (material_id IS NOT NULL)::int) = 1",
            name="exactly_one_resource",
        ),
        CheckConstraint(
            "resource_kind IN ('MACHINE','WORKER','MATERIAL')",
            name="resource_kind_domain",
        ),
    )

    # Composite PK (id, occurred_at) satisfies the TimescaleDB rule that every
    # unique index includes the partitioning column; Identity() keeps id
    # auto-incrementing despite the composite key.
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    occurred_at: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(160))
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    resource_kind: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(40))
    received_at: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str | None] = mapped_column(String(20))
    estimated_downtime: Mapped[int | None]
    processed_at: Mapped[int | None]
    processing_error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB)


Index("ix_telemetry_message_id", TelemetryEvent.message_id)
```

(`estimated_downtime` remains the only telemetry duration column; worker absence durations live in window rows.)

Append after `MachineDowntimeWindow`:

```python
class WorkerAbsenceWindow(Base):
    __tablename__ = "worker_absence_windows"
    __table_args__ = (
        CheckConstraint(
            "absence_until IS NULL OR absence_until > absence_from",
            name="absence_interval_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"))
    absence_from: Mapped[int] = mapped_column(Integer)
    absence_until: Mapped[int | None]
    reason: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str | None] = mapped_column(String(20))
    source_event_ids: Mapped[list] = mapped_column(JSONB, default=list)
```

- [ ] **Step 4: Generate migration and hand-edit**

```bash
uv run alembic revision --autogenerate -m "multi resource telemetry"
```

Verify/edit the generated `upgrade()` so it contains (in order):

```python
    # telemetry_events kind columns; staged default backfills existing rows,
    # then the default is removed so new inserts must set it explicitly.
    op.add_column(
        "telemetry_events",
        sa.Column("resource_kind", sa.String(length=20), nullable=False,
                  server_default="MACHINE"),
    )
    op.alter_column(
        "telemetry_events", "resource_kind", existing_type=sa.String(length=20),
        existing_nullable=False, server_default=None,
    )
    op.add_column(
        "telemetry_events",
        sa.Column("worker_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_telemetry_events_worker_id_workers", "telemetry_events",
        "workers", ["worker_id"], ["id"],
    )
    op.add_column(
        "telemetry_events",
        sa.Column("material_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_telemetry_events_material_id_materials", "telemetry_events",
        "materials", ["material_id"], ["id"],
    )
    op.alter_column(
        "telemetry_events", "machine_id", existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_telemetry_events_exactly_one_resource", "telemetry_events",
        "((machine_id IS NOT NULL)::int + (worker_id IS NOT NULL)::int "
        "+ (material_id IS NOT NULL)::int) = 1",
    )
    op.create_check_constraint(
        "ck_telemetry_events_resource_kind_domain", "telemetry_events",
        "resource_kind IN ('MACHINE','WORKER','MATERIAL')",
    )
    op.create_table(
        "worker_absence_windows",
        sa.Column("id", sa.Integer(), identity_always=False, nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("absence_from", sa.Integer(), nullable=False),
        sa.Column("absence_until", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("source_event_ids", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"]),
        sa.CheckConstraint(
            "absence_until IS NULL OR absence_until > absence_from",
            name=op.f("ck_worker_absence_windows_absence_interval_valid"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_worker_absence_windows_instance_id"),
        "worker_absence_windows", ["instance_id"],
    )
```

If autogenerate emitted equivalent ops, keep theirs — the contract is the end state above. In `downgrade()`, reverse: drop index/table, drop both CHECKs, drop FKs + columns, restore `machine_id` nullable=False.

Apply and verify:

```bash
uv run alembic upgrade head
docker compose exec timescaledb psql -U coe -d coe -c "\d worker_absence_windows"
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/db/test_multi_resource_models.py tests/db/test_downtime_union.py -v`
Expected: all pass (existing machine union tests prove backward compatibility).

Then full suite once: `uv run pytest -q` — expect green.

- [ ] **Step 6: Commit**

```bash
git add coe/db/models/downtime.py alembic/versions tests/db/test_multi_resource_models.py
git commit -m "feat(db): multi-resource telemetry columns and worker absence windows"
```

### Task 2: Kind-Routed Ingestion

**Files:**
- Modify: `coe/mqtt/ingest.py`, `coe/mqtt/subscriber.py`
- Test: `tests/db/test_ingest_routing.py`, extend `tests/mqtt/test_multi_resource_roundtrip.py` (created in Task 3 — this task only adds the DB-level tests listed here)

**Interfaces:**
- Consumes: Task 1 models; existing `session_scope`.
- Produces:
  - `ResourceEventPayload` (pydantic, flat wire format with legacy inference)
  - `ingest_telemetry_event(payload_dict) -> tuple[int, bool]` — same signature as before; now routes by kind
  - `PayloadError` unchanged in role
  - `WorkerAbsenceWindow` union/close logic via generalized interval merger

**Wire contract (normative):**

```json
{"message_id": "...", "instance_id": "factory_demo_01", "resource_kind": "WORKER",
 "worker_id": "W3", "event_type": "WORKER_ABSENT", "occurred_at": 480,
 "severity": "MEDIUM", "estimated_absence": 240}
```

`resource_kind` optional **only** for legacy machine payloads: when absent and `machine_id` present ⇒ `MACHINE`; otherwise absent kind is a validation error. Duration fields are kind-exclusive (`estimated_downtime` ⇔ MACHINE, `estimated_absence` ⇔ WORKER, MATERIAL carries neither).

- [ ] **Step 1: Write failing tests `tests/db/test_ingest_routing.py`**

```python
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def _publish(kind_payload: dict):
    from coe.mqtt.ingest import ingest_telemetry_event

    return ingest_telemetry_event({"instance_id": "factory_demo_01",
                                   "occurred_at": 500,
                                   "message_id": "fixed-id-see-test", **kind_payload})


def _one(sql, params):
    from coe.config import get_settings

    with create_engine(get_settings().database_url).begin() as c:
        return c.execute(text(sql), params).scalar_one()


def test_worker_absent_creates_window_and_status(demo_scenario):
    tid, created = _publish({
        "message_id": "mr-w1", "resource_kind": "WORKER", "worker_id": "W3",
        "event_type": "WORKER_ABSENT", "severity": "MEDIUM",
    })
    assert created is True
    assert _one(
        "SELECT count(*) FROM worker_absence_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.worker_id = ("
        "  SELECT id FROM workers WHERE instance_id=i.id AND name='W3') "
        "AND w.absence_from <= 500 "
        "AND (w.absence_until IS NULL OR w.absence_until > 500)",
        {}) == 1
    assert _one(
        "SELECT status FROM workers w JOIN instances i ON i.id=w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.name='W3'", {}) == "UNAVAILABLE"


def test_worker_return_closes_open_absence(demo_scenario):
    _publish({"message_id": "mr-w2a", "resource_kind": "WORKER",
              "worker_id": "W4", "event_type": "WORKER_ABSENT"})
    _publish({"message_id": "mr-w2b", "resource_kind": "WORKER",
              "worker_id": "W4", "event_type": "WORKER_RETURN", "occurred_at": 700})
    assert _one(
        "SELECT absence_until FROM worker_absence_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.worker_id = ("
        "  SELECT id FROM workers WHERE instance_id=i.id AND name='W4')",
        {}) == 700
    assert _one(
        "SELECT status FROM workers w JOIN instances i ON i.id=w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.name='W4'", {}) == "AVAILABLE"


def test_touching_absences_merge(demo_scenario):
    _publish({"message_id": "mr-w3a", "resource_kind": "WORKER",
              "worker_id": "W5", "event_type": "WORKER_ABSENT",
              "estimated_absence": 60})
    _publish({"message_id": "mr-w3b", "resource_kind": "WORKER",
              "worker_id": "W5", "event_type": "WORKER_ABSENT",
              "occurred_at": 560, "estimated_absence": 30})
    assert _one(
        "SELECT count(*) FROM worker_absence_windows w "
        "JOIN instances i ON i.id = w.instance_id "
        "WHERE i.name='factory_demo_01' AND w.worker_id = ("
        "  SELECT id FROM workers WHERE instance_id=i.id AND name='W5')",
        {}) == 1


def test_material_shortage_is_telemetry_only(demo_scenario):
    before = _one("SELECT count(*) FROM material_receipts", {})
    tid, created = _publish({
        "message_id": "mr-m1", "resource_kind": "MATERIAL",
        "material_sku": "MAT-001", "event_type": "MATERIAL_SHORTAGE",
    })
    assert created is True
    assert _one(
        "SELECT count(*) FROM telemetry_events te "
        "JOIN instances i ON i.id=te.instance_id "
        "JOIN materials m ON m.id=te.material_id "
        "WHERE i.name='factory_demo_01' AND m.sku='MAT-001' "
        "AND te.resource_kind='MATERIAL'", {}) == 1
    assert _one("SELECT count(*) FROM material_receipts", {}) == before


def test_legacy_machine_payload_infers_kind(demo_scenario):
    """No resource_kind + machine_id present => MACHINE (wire backward compat)."""
    tid, created = _publish({
        "message_id": "mr-legacy", "machine_id": "M7",
        "event_type": "FAILURE", "estimated_downtime": 45,
    })
    assert created is True
    assert _one(
        "SELECT resource_kind FROM telemetry_events te "
        "JOIN instances i ON i.id=te.instance_id "
        "WHERE i.name='factory_demo_01' AND te.message_id='mr-legacy'",
        {}) == "MACHINE"


def test_unknown_worker_rejected(demo_scenario):
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    raised = False
    try:
        ingest_telemetry_event({
            "message_id": "mr-bad", "instance_id": "factory_demo_01",
            "resource_kind": "WORKER", "worker_id": "NOPE",
            "event_type": "WORKER_ABSENT", "occurred_at": 10,
        })
    except PayloadError:
        raised = True
    assert raised


def test_wrong_duration_field_for_kind_rejected(demo_scenario):
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    raised = False
    try:
        ingest_telemetry_event({
            "message_id": "mr-bad2", "instance_id": "factory_demo_01",
            "resource_kind": "MATERIAL", "material_sku": "MAT-001",
            "event_type": "MATERIAL_SHORTAGE", "occurred_at": 10,
            "estimated_absence": 30,
        })
    except PayloadError:
        raised = True
    assert raised
```

Run: `uv run pytest tests/db/test_ingest_routing.py -v`
Expected: collection/import errors or failures — `ResourceEventPayload` doesn't exist yet and worker events raise `PayloadError`.

- [ ] **Step 2: Rewrite `coe/mqtt/ingest.py`**

Replace the whole module with:

```python
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from coe.db.models.downtime import (
    MachineDowntimeWindow,
    TelemetryEvent,
    WorkerAbsenceWindow,
)
from coe.db.models.fjsp import Machine
from coe.db.models.materials import Material
from coe.db.models.provenance import Instance
from coe.db.models.workers import Worker
from coe.db.session import session_scope

Kind = Literal["MACHINE", "WORKER", "MATERIAL"]
EVENT_TYPES: dict[str, frozenset[str]] = {
    "MACHINE": frozenset({"FAILURE", "MAINTENANCE"}),
    "WORKER": frozenset({"WORKER_ABSENT", "WORKER_RETURN"}),
    "MATERIAL": frozenset({"MATERIAL_SHORTAGE", "MATERIAL_RESTOCK"}),
}


class PayloadError(ValueError):
    pass


class ResourceEventPayload(BaseModel):
    """Flat wire format shared by all three resource kinds.

    Legacy compatibility: `resource_kind` may be omitted when `machine_id`
    is present (pre-amendment machine payloads) — inferred as MACHINE.
    """

    model_config = {"extra": "forbid"}

    message_id: str
    instance_id: str
    resource_kind: Kind | None = None
    machine_id: str | None = None
    worker_id: str | None = None
    material_sku: str | None = None
    event_type: str
    occurred_at: int
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None
    estimated_downtime: int | None = Field(default=None, gt=0)
    estimated_absence: int | None = Field(default=None, gt=0)
    reason: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("negative occurred_at crashes CP-SAT (spec §6.5)")
        return v

    @model_validator(mode="after")
    def _resolve(self) -> "ResourceEventPayload":
        refs = {
            "MACHINE": self.machine_id,
            "WORKER": self.worker_id,
            "MATERIAL": self.material_sku,
        }
        present = {k for k, v in refs.items() if v is not None}
        if self.resource_kind is None:
            if present == {"MACHINE"}:
                self.resource_kind = "MACHINE"  # legacy inference
            else:
                raise ValueError(
                    "resource_kind required (legacy inference only applies "
                    "to machine payloads)"
                )
        if len(present) != 1 or self.resource_kind not in present:
            raise ValueError(
                "payload must carry exactly the one resource reference "
                "matching its resource_kind"
            )
        kind = self.resource_kind
        if self.event_type not in EVENT_TYPES[kind]:
            raise ValueError(
                f"{self.event_type!r} is not a valid {kind} event_type"
            )
        if kind != "MACHINE" and self.estimated_downtime is not None:
            raise ValueError("estimated_downtime is MACHINE-only")
        if kind != "WORKER" and self.estimated_absence is not None:
            raise ValueError("estimated_absence is WORKER-only")
        if kind == "MATERIAL" and (self.estimated_downtime or self.estimated_absence):
            raise ValueError("MATERIAL events carry no duration")
        return self


def _merge_intervals(
    session: Session,
    *,
    model,
    instance_id: int,
    res_attr: str,
    res_id: int,
    lock_key: str,
    new_from: int,
    new_until: int | None,
    reason: str,
    telemetry_id: int,
    from_attr: str,
    until_attr: str,
) -> None:
    """Shared union logic (spec §6.5 + Amendment): overlapping OR touching
    intervals merge into the smallest covering interval under an advisory lock."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
    col = getattr(model, res_attr)
    rows = session.scalars(
        select(model).where(model.instance_id == instance_id, col == res_id)
    ).all()

    def touches(r) -> bool:
        r_to = getattr(r, until_attr)
        if new_until is None:
            return r_to is None or r_to >= new_from
        if r_to is None:
            return new_until >= getattr(r, from_attr)
        return new_from <= r_to and getattr(r, from_attr) <= new_until

    overlapping = [r for r in rows if touches(r)]
    if not overlapping:
        session.add(model(
            instance_id=instance_id, **{res_attr: res_id},
            **{from_attr: new_from, until_attr: new_until},
            reason=reason, severity=None, source_event_ids=[telemetry_id],
        ))
        return

    merged_from = min([new_from] + [getattr(r, from_attr) for r in overlapping])
    merged_open = any(getattr(r, until_attr) is None for r in overlapping) or new_until is None
    merged_until = None if merged_open else max(
        [new_until] + [getattr(r, until_attr) for r in overlapping]
    )
    reasons = {getattr(r, "reason") for r in overlapping} | {reason}
    merged_reason = "FAILURE" if "FAILURE" in reasons else next(iter(reasons))
    event_ids: list[int] = [telemetry_id]
    for r in overlapping:
        event_ids.extend(getattr(r, "source_event_ids") or [])
        session.delete(r)

    session.add(model(
        instance_id=instance_id, **{res_attr: res_id},
        **{from_attr: merged_from, until_attr: merged_until},
        reason=merged_reason, severity=None, source_event_ids=event_ids,
    ))


def ingest_telemetry_event(payload_dict: dict) -> tuple[int, bool]:
    try:
        payload = ResourceEventPayload.model_validate(payload_dict)
    except Exception as exc:
        raise PayloadError(str(exc)) from exc

    with session_scope() as session:
        inst = (
            session.query(Instance)
            .filter(Instance.name == payload.instance_id)
            .one_or_none()
        )
        if inst is None:
            raise PayloadError(f"unknown instance '{payload.instance_id}'")

        existing = session.execute(
            select(TelemetryEvent.id).where(
                TelemetryEvent.instance_id == inst.id,
                TelemetryEvent.message_id == payload.message_id,
            )
        ).first()
        if existing is not None:
            return existing[0], False  # duplicate delivery: no state change

        kind = payload.resource_kind
        machine_id = worker_id = material_id = None
        duration_until = None

        if kind == "MACHINE":
            row = (
                session.query(Machine)
                .filter(Machine.instance_id == inst.id,
                        Machine.name == payload.machine_id)
                .one_or_none()
            )
            if row is None:
                raise PayloadError(f"unknown machine '{payload.machine_id}'")
            machine_id = row.id
            if payload.event_type == "FAILURE":
                duration_until = (
                    payload.occurred_at + payload.estimated_downtime
                    if payload.estimated_downtime is not None else None
                )
            else:
                duration_until = payload.occurred_at + (
                    payload.estimated_downtime or 0
                )
        elif kind == "WORKER":
            row = (
                session.query(Worker)
                .filter(Worker.instance_id == inst.id,
                        Worker.name == payload.worker_id)
                .one_or_none()
            )
            if row is None:
                raise PayloadError(f"unknown worker '{payload.worker_id}'")
            worker_id = row.id
            duration_until = (
                payload.occurred_at + payload.estimated_absence
                if payload.estimated_absence is not None else None
            ) if payload.event_type == "WORKER_ABSENT" else 0
        else:  # MATERIAL — telemetry only; resolve for the FK
            row = (
                session.query(Material)
                .filter(Material.instance_id == inst.id,
                        Material.sku == payload.material_sku)
                .one_or_none()
            )
            if row is None:
                raise PayloadError(f"unknown material '{payload.material_sku}'")
            material_id = row.id

        event = TelemetryEvent(
            occurred_at=payload.occurred_at,
            instance_id=inst.id,
            message_id=payload.message_id,
            machine_id=machine_id,
            worker_id=worker_id,
            material_id=material_id,
            resource_kind=kind,
            event_type=payload.event_type,
            received_at=payload.occurred_at,  # convention: see Phase 1 plan
            severity=payload.severity,
            estimated_downtime=payload.estimated_downtime,
            processed_at=payload.occurred_at,
            processing_error=None,
            payload_json=payload_dict,
        )
        session.add(event)
        session.flush()

        if kind == "MACHINE":
            if duration_until is None or duration_until > payload.occurred_at:
                _merge_intervals(
                    session, model=MachineDowntimeWindow,
                    instance_id=inst.id, res_attr="machine_id",
                    res_id=machine_id,
                    lock_key=f"downtime:{inst.id}:{machine_id}",
                    new_from=payload.occurred_at, new_until=duration_until,
                    reason=payload.event_type, telemetry_id=event.id,
                    from_attr="downtime_from", until_attr="downtime_until",
                )
            if payload.event_type == "FAILURE":
                row.status = "FAILED"
        elif kind == "WORKER":
            if payload.event_type == "WORKER_ABSENT":
                _merge_intervals(
                    session, model=WorkerAbsenceWindow,
                    instance_id=inst.id, res_attr="worker_id",
                    res_id=worker_id,
                    lock_key=f"absence:{inst.id}:{worker_id}",
                    new_from=payload.occurred_at, new_until=duration_until,
                    reason="WORKER_ABSENT", telemetry_id=event.id,
                    from_attr="absence_from", until_attr="absence_until",
                )
                row.status = "UNAVAILABLE"
            else:  # WORKER_RETURN closes open absences, restores status
                open_rows = session.scalars(
                    select(WorkerAbsenceWindow).where(
                        WorkerAbsenceWindow.instance_id == inst.id,
                        WorkerAbsenceWindow.worker_id == worker_id,
                        WorkerAbsenceWindow.absence_until.is_(None),
                    )
                ).all()
                for r in open_rows:
                    r.absence_until = max(r.absence_from + 1, payload.occurred_at)
                row.status = "AVAILABLE"

        session.flush()
        return event.id, True
```

- [ ] **Step 3: Generalize the subscriber topic validator**

In `coe/mqtt/subscriber.py`, replace `_on_message` with:

```python
_RESOURCE_FIELDS = {"machine": "machine_id", "worker": "worker_id",
                    "material": "material_sku"}


def _on_message(client, userdata, msg) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"[subscriber] undecodable payload on {msg.topic}")
        return

    # Topic contract: factory/{instance}/{resource_type}/{resource_id}/events
    segments = msg.topic.split("/")
    if (len(segments) != 5 or segments[0] != "factory" or segments[4] != "events"
            or segments[2] not in _RESOURCE_FIELDS):
        print(f"[subscriber] REJECTED: malformed topic {msg.topic!r}")
        return

    if not isinstance(payload, dict):
        print(f"[subscriber] REJECTED: non-object payload on {msg.topic}")
        return

    expected_instance = segments[1]
    field = _RESOURCE_FIELDS[segments[2]]
    if (payload.get("instance_id") != expected_instance
            or payload.get(field) != segments[3]):
        print(f"[subscriber] REJECTED: topic/payload mismatch on {msg.topic}")
        return

    try:
        telemetry_id, created = ingest_telemetry_event(payload)
        status = "created" if created else "duplicate-suppressed"
        print(f"[subscriber] {status} telemetry id={telemetry_id}")
    except PayloadError as exc:
        # Unresolvable payloads cannot populate telemetry FK columns;
        # they are logged loudly instead (documented limitation).
        print(f"[subscriber] REJECTED: {exc}")
    except Exception as exc:  # keep the network thread alive no matter what
        print(f"[subscriber] ERROR ingesting event: {exc!r}")
```

(The existing `TOPIC_FILTER` becomes `factory/+/+/*/events`? **No** — MQTT wildcards cannot mix `+` and `*`. Use three explicit subscriptions in `run_subscriber()`:

```python
    for topic in ("factory/+/machine/+/events",
                  "factory/+/worker/+/events",
                  "factory/+/material/+/events"):
        client.subscribe(topic, qos=1)
```

replacing the single `client.subscribe(TOPIC_FILTER, qos=1)` line; delete the now-stale `TOPIC_FILTER` constant.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/db/test_ingest_routing.py tests/db/test_downtime_union.py -v`
Expected: all pass — including every pre-existing machine union test (backward compatibility proof).

Then full suite once: `uv run pytest -q` — expect green.

- [ ] **Step 5: Commit**

```bash
git add coe/mqtt/ingest.py coe/mqtt/subscriber.py tests/db/test_ingest_routing.py
git commit -m "feat(mqtt): kind-routed ingestion with worker absence and material events"
```

### Task 3: Edge Stubs, CLI Proofs, Broker Round-Trips

**Files:**
- Modify: `coe/mqtt/edge_stub.py`, `coe/cli.py`
- Test: `tests/mqtt/test_multi_resource_roundtrip.py`

**Interfaces:**
- Consumes: Task 2 ingestion; existing `publish_failure` callers (unchanged signature).
- Produces:
  - `publish_resource_event(*, instance_name, resource_kind, resource_id, event_type, occurred_at=512, severity=None, reason=None, duration=None, message_id=None) -> str` — generic publisher; maps `resource_kind` to the correct topic segment and payload field (`machine_id` / `worker_id` / `material_sku`) and duration field (`estimated_downtime` for MACHINE-FAILURE/MAINTENANCE when `duration` given, `estimated_absence` for WORKER_ABSENT).
  - CLI `mqtt test-absence --instance X --worker W3 [--at 480] [--duration 240]`
  - CLI `mqtt test-shortage --instance X --sku MAT-001 [--at 300]`

- [ ] **Step 1: Add the generic publisher**

In `coe/mqtt/edge_stub.py`, keep `publish_failure` but re-implement it as a wrapper and add:

```python
_KIND_FIELD = {"MACHINE": "machine_id", "WORKER": "worker_id",
               "MATERIAL": "material_sku"}
_TOPIC_SEGMENT = {"MACHINE": "machine", "WORKER": "worker",
                  "MATERIAL": "material"}


def publish_resource_event(
    *,
    instance_name: str,
    resource_kind: str,
    resource_id: str,
    event_type: str,
    occurred_at: int = 512,
    severity: str | None = None,
    reason: str | None = None,
    duration: int | None = None,
    message_id: str | None = None,
) -> str:
    mid = message_id or f"evt-{uuid.uuid4().hex[:12]}"
    payload = {
        "message_id": mid,
        "instance_id": instance_name,
        "resource_kind": resource_kind,
        _KIND_FIELD[resource_kind]: resource_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
    }
    if severity is not None:
        payload["severity"] = severity
    if reason is not None:
        payload["reason"] = reason
    if duration is not None and resource_kind == "MACHINE":
        payload["estimated_downtime"] = duration
    if duration is not None and resource_kind == "WORKER" \
            and event_type == "WORKER_ABSENT":
        payload["estimated_absence"] = duration

    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(s.mqtt_host, s.mqtt_port)
    client.loop_start()
    topic = (f"factory/{instance_name}/"
             f"{_TOPIC_SEGMENT[resource_kind]}/{resource_id}/events")
    info = client.publish(topic, json.dumps(payload), qos=1)
    ok = info.wait_for_publish(timeout=10)
    client.loop_stop()
    client.disconnect()
    if ok is False or not info.is_published():
        raise RuntimeError(f"publish not acknowledged on {topic}")
    return mid


def publish_failure(instance_name, machine_name, *, occurred_at=512,
                    estimated_downtime=None, severity="HIGH",
                    reason="mechanical_failure", message_id=None) -> str:
    return publish_resource_event(
        instance_name=instance_name, resource_kind="MACHINE",
        resource_id=machine_name, event_type="FAILURE",
        occurred_at=occurred_at, severity=severity, reason=reason,
        duration=estimated_downtime, message_id=message_id,
    )
```

(Replace the old body of `publish_failure` entirely with this wrapper — one code path, no duplication.)

- [ ] **Step 2: Wire the two CLI proofs**

In `build_parser()` inside the mqtt subparsers add:

```python
    ta = mq_sub.add_parser("test-absence")
    ta.add_argument("--instance", default="factory_demo_01")
    ta.add_argument("--worker", default="W3")
    ta.add_argument("--at", type=int, default=480)
    ta.add_argument("--duration", type=int, default=None)

    ts = mq_sub.add_parser("test-shortage")
    ts.add_argument("--instance", default="factory_demo_01")
    ts.add_argument("--sku", default="MAT-001")
    ts.add_argument("--at", type=int, default=300)
```

In `main()` dispatch add branches that mirror `test-failure`'s structure:

```python
        if args.mqtt_cmd == "test-absence":
            import time

            from coe.db.session import make_engine
            from coe.mqtt.edge_stub import publish_resource_event
            from coe.mqtt.subscriber import run_subscriber
            from sqlalchemy import text

            handle = run_subscriber()
            try:
                mid = publish_resource_event(
                    instance_name=args.instance, resource_kind="WORKER",
                    resource_id=args.worker, event_type="WORKER_ABSENT",
                    occurred_at=args.at, severity="MEDIUM",
                    reason="cli_proof", duration=args.duration,
                )
                deadline = time.time() + 5
                engine = make_engine()
                found = False
                while time.time() < deadline and not found:
                    with engine.begin() as c:
                        n = c.execute(text(
                            "SELECT count(*) FROM telemetry_events te "
                            "JOIN instances i ON i.id = te.instance_id "
                            "WHERE i.name = :inst AND te.message_id = :mid"),
                            {"inst": args.instance, "mid": mid}).scalar_one()
                        win = c.execute(text(
                            "SELECT count(*) FROM worker_absence_windows w "
                            "JOIN instances i ON i.id = w.instance_id "
                            "JOIN workers wk ON wk.instance_id = i.id "
                            "AND wk.name = :w WHERE i.name = :inst "
                            "AND w.absence_from <= :at AND "
                            "(w.absence_until IS NULL OR w.absence_until > :at)"),
                            {"inst": args.instance, "w": args.worker,
                             "at": args.at}).scalar_one()
                    found = n == 1 and win >= 1
                    if not found:
                        time.sleep(0.25)
                if found:
                    print(f"OK: WORKER_ABSENT stored once with absence window ({mid})")
                    raise SystemExit(0)
                raise SystemExit("FAIL: absence not fully ingested within 5s")
            finally:
                handle.stop()

        if args.mqtt_cmd == "test-shortage":
            import time

            from coe.db.session import make_engine
            from coe.mqtt.edge_stub import publish_resource_event
            from coe.mqtt.subscriber import run_subscriber
            from sqlalchemy import text

            handle = run_subscriber()
            try:
                mid = publish_resource_event(
                    instance_name=args.instance, resource_kind="MATERIAL",
                    resource_id=args.sku, event_type="MATERIAL_SHORTAGE",
                    occurred_at=args.at, severity="LOW", reason="cli_proof",
                )
                deadline = time.time() + 5
                engine = make_engine()
                found = False
                while time.time() < deadline and not found:
                    with engine.begin() as c:
                        n = c.execute(text(
                            "SELECT count(*) FROM telemetry_events te "
                            "JOIN instances i ON i.id = te.instance_id "
                            "WHERE i.name = :inst AND te.message_id = :mid "
                            "AND te.resource_kind = 'MATERIAL'"),
                            {"inst": args.instance, "mid": mid}).scalar_one()
                    found = n == 1
                    if not found:
                        time.sleep(0.25)
                if found:
                    print(f"OK: MATERIAL_SHORTAGE stored once ({mid})")
                    raise SystemExit(0)
                raise SystemExit("FAIL: shortage not ingested within 5s")
            finally:
                handle.stop()
```

Note both new commands use `try/finally: handle.stop()` — fixing the leak pattern the machine path has; leave `test-failure` as-is (existing behavior frozen).

- [ ] **Step 3: Write broker round-trip tests `tests/mqtt/test_multi_resource_roundtrip.py`**

```python
import time

import pytest

pytestmark = [pytest.mark.db, pytest.mark.mqtt]


def test_worker_absence_roundtrip(demo_scenario):
    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_resource_event
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        mid = publish_resource_event(
            instance_name="factory_demo_01", resource_kind="WORKER",
            resource_id="W6", event_type="WORKER_ABSENT", occurred_at=800)
        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 5
        ok = False
        while time.time() < deadline and not ok:
            with eng.begin() as c:
                n = c.execute(text(
                    "SELECT count(*) FROM worker_absence_windows w "
                    "JOIN instances i ON i.id=w.instance_id "
                    "WHERE i.name='factory_demo_01' AND w.absence_from <= 800 "
                    "AND (w.absence_until IS NULL OR w.absence_until > 800)"
                ), ).scalar_one()
                st = c.execute(text(
                    "SELECT status FROM workers w JOIN instances i ON i.id=w.instance_id "
                    "WHERE i.name='factory_demo_01' AND w.name='W6'"
                )).scalar_one()
            ok = n >= 1 and st == "UNAVAILABLE"
            if not ok:
                time.sleep(0.2)
        assert ok
    finally:
        handle.stop()


def test_material_shortage_roundtrip(demo_scenario):
    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_resource_event
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        mid = publish_resource_event(
            instance_name="factory_demo_01", resource_kind="MATERIAL",
            resource_id="MAT-002", event_type="MATERIAL_SHORTAGE", occurred_at=810)
        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 5
        stored = False
        while time.time() < deadline and not stored:
            with eng.begin() as c:
                n = c.execute(text(
                    "SELECT count(*) FROM telemetry_events te "
                    "JOIN instances i ON i.id=te.instance_id "
                    "WHERE i.name='factory_demo_01' AND te.message_id=:m"
                ), {"m": mid}).scalar_one()
            stored = n == 1
            if not stored:
                time.sleep(0.2)
        assert stored
    finally:
        handle.stop()


def test_worker_topic_mismatch_rejected(demo_scenario):
    """Valid worker payload on the WRONG worker's topic must be rejected."""
    import json as _json

    import paho.mqtt.client as mqtt

    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_resource_event
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        # control: matching topic lands exactly once
        mid_ok = publish_resource_event(
            instance_name="factory_demo_01", resource_kind="WORKER",
            resource_id="W7", event_type="WORKER_ABSENT", occurred_at=820,
            message_id="evt-mr-ok")

        # mismatch: W7's payload published on W8's topic
        s = get_settings()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(s.mqtt_host, s.mqtt_port)
        bad_payload = {
            "message_id": "evt-mr-bad", "instance_id": "factory_demo_01",
            "resource_kind": "WORKER", "worker_id": "W7",
            "event_type": "WORKER_ABSENT", "occurred_at": 820,
        }
        client.publish("factory/factory_demo_01/worker/W8/events",
                       _json.dumps(bad_payload), qos=1).wait_for_publish(timeout=10)
        client.disconnect()

        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 3
        landed = 0
        rejected = 0
        while time.time() < deadline:
            with eng.begin() as c:
                landed = c.execute(text(
                    "SELECT count(*) FROM telemetry_events te "
                    "JOIN instances i ON i.id=te.instance_id "
                    "WHERE i.name='factory_demo_01' AND te.message_id IN "
                    "('evt-mr-ok','evt-mr-bad')")).scalar_one()
            if landed >= 1:
                break
            time.sleep(0.2)
        with eng.begin() as c:
            rejected = c.execute(text(
                "SELECT count(*) FROM telemetry_events te "
                "JOIN instances i ON i.id=te.instance_id "
                "WHERE i.name='factory_demo_01' AND te.message_id='evt-mr-bad'"
            )).scalar_one()
        assert rejected == 0  # mismatched delivery never ingested
    finally:
        handle.stop()
```

Run: `uv run pytest tests/mqtt/test_multi_resource_roundtrip.py -v`
Expected: 3 passed.

Then full suite once: `uv run pytest -q`.

Smoke CLIs:

```bash
uv run python -m coe.cli mqtt test-absence --worker W3 --at 480
uv run python -m coe.cli mqtt test-shortage --sku MAT-001 --at 300
```

Expected: both exit 0 with `OK:` lines.

- [ ] **Step 4: Commit**

```bash
git add coe/mqtt/edge_stub.py coe/cli.py tests/mqtt/
git commit -m "feat(mqtt): multi-resource publishers, CLI proofs, broker round-trips"
```

### Task 4: Acceptance Extension + Final Verification

**Files:**
- Modify: `tests/db/test_acceptance.py`
- Modify: nothing else

**Interfaces:**
- Consumes: everything.
- Produces: `test_criterion_11_multi_resource` proving amended criterion 11 across all three kinds through the real subscriber.

- [ ] **Step 1: Append the test to `tests/db/test_acceptance.py`**

```python
def test_criterion_11_multi_resource(full_pipeline):
    """Amended criterion 11: each kind stores once; worker flips status;
    material stays telemetry-only."""
    import time

    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_resource_event
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text as sqltext

    handle = run_subscriber()
    try:
        mids = {
            "machine": publish_resource_event(
                instance_name="factory_demo_01", resource_kind="MACHINE",
                resource_id="M4", event_type="FAILURE", occurred_at=950,
                severity="HIGH", message_id="acc-mr-machine"),
            "worker": publish_resource_event(
                instance_name="factory_demo_01", resource_kind="WORKER",
                resource_id="W8", event_type="WORKER_ABSENT", occurred_at=951,
                severity="MEDIUM", message_id="acc-mr-worker"),
            "material": publish_resource_event(
                instance_name="factory_demo_01", resource_kind="MATERIAL",
                resource_id="MAT-003", event_type="MATERIAL_SHORTAGE",
                occurred_at=952, message_id="acc-mr-material"),
        }
        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 6
        ok = False
        while time.time() < deadline and not ok:
            with eng.begin() as c:
                # NB: text() does not auto-expand tuples for IN clauses;
                # query per message_id instead.
                counts = {
                    kind: c.execute(sqltext(
                        "SELECT count(*) FROM telemetry_events te "
                        "JOIN instances i ON i.id = te.instance_id "
                        "WHERE i.name='factory_demo_01' AND te.message_id = :m"
                    ), {"m": mid}).scalar_one()
                    for kind, mid in mids.items()
                }
                wstatus = c.execute(sqltext(
                    "SELECT status FROM workers w JOIN instances i ON i.id=w.instance_id "
                    "WHERE i.name='factory_demo_01' AND w.name='W8'"
                )).scalar_one()
                absence = c.execute(sqltext(
                    "SELECT count(*) FROM worker_absence_windows w "
                    "JOIN instances i ON i.id=w.instance_id "
                    "WHERE i.name='factory_demo_01'"
                )).scalar_one()
            ok = (counts.get("machine") == 1 and counts.get("worker") == 1
                  and counts.get("material") == 1
                  and wstatus == "UNAVAILABLE" and absence >= 1)
            if not ok:
                time.sleep(0.25)

        # duplicate suppression per kind
        time.sleep(1.0)
        publish_resource_event(
            instance_name="factory_demo_01", resource_kind="MACHINE",
            resource_id="M4", event_type="FAILURE", occurred_at=950,
            severity="HIGH", message_id=mids["machine"])
        with eng.begin() as c:
            dup = c.execute(sqltext(
                "SELECT count(*) FROM telemetry_events te "
                "JOIN instances i ON i.id = te.instance_id "
                "WHERE i.name='factory_demo_01' AND te.message_id = :m"
            ), {"m": mids["machine"]}).scalar_one()
        assert ok and dup == 1
    finally:
        handle.stop()
```

- [ ] **Step 2: Run everything**

```bash
uv run pytest tests/db/test_acceptance.py -v
uv run pytest -q
```
Expected: all green (suite now ~64).

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_acceptance.py
git commit -m "test: multi-resource acceptance coverage for amended criterion 11"
```

---

## Self-Review Notes

Checklist run post-write: spec coverage — amendment §6.5 columns/CHECK (T1), absence table (T1), topics+routing (T2), subscriber validation (T2), stubs+CLI proofs (T3), criterion 11 extension (T4); phase2 payload-builder line is Phase 3-era work (no Phase 2 implementation exists yet — nothing to do now); phase3 changes are future-phase scope by definition. Placeholder scan clean. Type consistency: `_merge_intervals` kwargs match call sites; `publish_resource_event` signature matches all three consumers.

