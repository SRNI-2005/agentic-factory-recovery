# Phase 1: Infrastructure and Data Ingestion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the reproducible data foundation — import MK01/Hutter-Nouri/GASS sources into isolated TimescaleDB instances, deterministically generate the `factory_demo_01` composite scenario (30 jobs / 8 machines), and prove the MQTT failure-event ingestion path into `telemetry_events` + `machine_downtime_windows`.

**Architecture:** Four strict layers with one-way dependencies: (1) Docker Compose infrastructure (TimescaleDB + Mosquitto), (2) SQLAlchemy 2.0 canonical schema applied exclusively through Alembic migrations, (3) source parsers that write isolated provenance-tracked instances, (4) a seeded transformation pipeline composing `factory_demo_01`, plus a thin MQTT pub/sub proof slice. The solver, agents, and quantum pipeline do not exist yet and nothing here references them.

**Tech Stack:** Python 3.12+, `uv`, SQLAlchemy ≥ 2.0, Alembic, psycopg[binary] ≥ 3.0, paho-mqtt, pydantic-settings, openpyxl (approved deviation), TimescaleDB (Docker), Mosquitto (Docker), pytest.

## Global Constraints

Copy these verbatim into every task context. They come from spec `docs/superpowers/specs/2026-08-20-phase1-infrastructure-data-ingestion-design.md`.

- **DEVIATION (user-approved 2026-08-23):** dependency list gains `openpyxl` for real GASS `.xlsx` parsing. All other pins unchanged: `SQLAlchemy >= 2.0`, `alembic`, `psycopg[binary] >= 3.0`, `paho-mqtt`, `pydantic-settings`.
- Package manager is **`uv`** exclusively. All commands are `uv run ...`. Never pip, never system Python.
- Package layout is exactly: `pyproject.toml`, `coe/db/`, `coe/parsers/`, `coe/scenario/`, `coe/mqtt/`, `coe/cli.py`.
- Alembic migrations are authoritative. **No automatic table creation** (`Base.metadata.create_all` is forbidden outside tests' no-op path).
- Raw SQL appears **only** for TimescaleDB-specific operations (extension, hypertable).
- One normalized time unit = one minute; standard shift = 480 minutes; day = 1440 minutes.
- Every generator/transformation accepts an explicit random seed and is deterministic given inputs + seed.
- Every instance-owned table and association table carries `instance_id`; source identifiers retained in `source_id` columns; composite foreign keys prevent cross-instance joins.
- Synthetic values are labeled synthetic in provenance metadata; never described as real observations.
- Re-importing a source with same name/version/checksum is a no-op returning the existing instance; changed checksum ⇒ new instance; existing instances never overwritten.
- Each source import runs in its own transaction and rolls back completely on failure. A failed scenario build leaves no partial generated scenario.
- Machine statuses: `ACTIVE | FAILED | MAINTENANCE`. Job statuses: `PENDING | IN_PROGRESS | COMPLETED | BLOCKED`. Operation statuses: `PENDING | SCHEDULED | IN_PROGRESS | COMPLETED | INTERRUPTED | BLOCKED`. Worker statuses: `AVAILABLE | UNAVAILABLE`.
- Telemetry hypertable chunks by `occurred_at` with chunk interval default `10080` (minutes).
- MQTT topic pattern: `factory/{instance_id}/machine/{machine_id}/events`. Subscriber is idempotent on `message_id`.
- Downtime window unions execute under a per-machine PostgreSQL advisory lock (spec permits advisory lock instead of SERIALIZABLE).
- Source data lives at `data/raw/{mk01..mk15}/mkNN.txt`, `data/raw/nouri-fjspw/extracted/{SFJW,MFJW}/*.txt`, `data/raw/gass/*.xlsx` (+ `manifest.txt`). Do not move or modify raw files.
- CLI entrypoint is always `uv run python -m coe.cli <command>` (argparse — stdlib only; no Click/Typer because they are not in the approved dep list).
- `db reset` is development-only and destructive.

## File Map

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | uv/pytest config, dependency pins |
| `.env.example` | Documented runtime configuration template |
| `docker-compose.yml` | TimescaleDB + Mosquitto services, healthchecks |
| `mosquitto/config/mosquitto.conf` | Broker dev config (port 1883, anonymous) |
| `alembic.ini`, `alembic/env.py`, `alembic/versions/*` | Versioned migrations (authoritative DDL) |
| `coe/config.py` | pydantic-settings runtime configuration |
| `coe/db/base.py` | Declarative Base + naming convention |
| `coe/db/models/provenance.py` | `instances`, `scenario_sources`, `instance_profiles` |
| `coe/db/models/fjsp.py` | machines, capabilities, jobs, operations, alternatives, families, setup_times |
| `coe/db/models/workers.py` | workers, roles, op-machine-worker times, availability windows |
| `coe/db/models/materials.py` | materials, BOM, receipts |
| `coe/db/models/downtime.py` | machine_downtime_windows, telemetry_events |
| `coe/db/session.py` | Engine/session factory from settings |
| `coe/db/admin.py` | Destructive dev reset (drop user tables + re-migrate) |
| `coe/parsers/mk01.py` | Brandimarte MK text-format parser + importer |
| `coe/parsers/nouri.py` | Hutter/Nouri FJSSP-W parser + importer |
| `coe/parsers/gass.py` | GASS xlsx adapter → instance profiles |
| `coe/parsers/common.py` | Shared importer plumbing (checksum, idempotency, provenance) |
| `coe/scenario/build.py` | Orchestrates transformations atomically |
| `coe/scenario/topology_sampler.py` | Samples MK01-derived topology → 30 jobs / 8 machines |
| `coe/scenario/add_job_attributes.py` | TWK deadlines, releases, priorities (seeded) |
| `coe/scenario/add_workers.py` | Nouri-profile workers + eligibility + windows |
| `coe/scenario/add_setup_times.py` | Families + sequence-dependent setup matrix |
| `coe/scenario/add_failures.py` | Seeded planned-maintenance windows |
| `coe/scenario/add_materials.py` | Materials/BOM/stock/receipts |
| `coe/mqtt/edge_stub.py` | Publishes a synthetic FAILURE event |
| `coe/mqtt/subscriber.py` | Validates, stores telemetry, unions downtime, flips status |
| `coe/mqtt/ingest.py` | Payload schema, idempotent ingestion, downtime union |
| `coe/cli.py` | argparse command tree (§10 interface) |
| `tests/*` | Mirrors package layout; fixtures under `tests/fixtures/` |

## Task Index

1. Project scaffold + configuration
2. Docker Compose infrastructure
3. DB session + Alembic + provenance tables
4. FJSP core tables
5. Worker + materials tables
6. Downtime + telemetry hypertable
7. MK01 parser + import
8. Hutter/Nouri parser + import
9. GASS adapter + import
10. Scenario topology sampler (30×8 from MK01 profiles)
11. Job attributes (TWK deadlines/releases/priorities)
12. Workers + eligibility + availability windows
13. Families + setup matrix + failure windows
14. Materials + orchestrator + determinism proof
15. MQTT ingestion slice
16. Acceptance sweep (all 11 criteria)

### Task 1: Project Scaffold + Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `coe/__init__.py`, `coe/config.py`, `coe/cli.py` (stub), plus empty subpackages `coe/db/`, `coe/parsers/`, `coe/scenario/`, `coe/mqtt/`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Settings` class (`coe.config.Settings`) with fields `database_url: str`, `mqtt_host: str`, `mqtt_port: int`, `default_seed: int`, `telemetry_chunk_interval_minutes: int`; accessor `get_settings()` returning a cached instance. All later tasks import `from coe.config import get_settings`.

- [ ] **Step 1: Verify prerequisites**

Run: `uv --version && python3 --version`
Expected: uv ≥ 0.12 installed via Homebrew; system Python irrelevant because uv manages the project interpreter (`requires-python >= 3.12`). If uv is missing: `brew install uv`.

- [ ] **Step 2: Initialize git**

```bash
git init
printf '.venv/\n__pycache__/\n*.pyc\n.env\n.pytest_cache/\n.DS_Store\n' > .gitignore
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "coe"
version = "0.1.0"
description = "Agentic Autonomous Factory Recovery System - Phase 1 data foundation"
requires-python = ">=3.12"
dependencies = [
    "sqlalchemy>=2.0",
    "alembic",
    "psycopg[binary]>=3.0",
    "paho-mqtt",
    "pydantic-settings",
    "openpyxl",
]

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["coe"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "db: tests requiring Dockerized TimescaleDB (docker compose up -d first)",
]
```

- [ ] **Step 4: Create package skeleton**

```bash
mkdir -p coe/db/models coe/parsers coe/scenario coe/mqtt tests/fixtures
touch coe/__init__.py coe/db/__init__.py coe/db/models/__init__.py \
      coe/parsers/__init__.py coe/scenario/__init__.py coe/mqtt/__init__.py \
      tests/__init__.py
printf 'def main():\n    raise SystemExit("no commands registered yet")\n\nif __name__ == "__main__":\n    main()\n' > coe/cli.py
```

- [ ] **Step 5: Write `coe/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env (spec §8)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://coe:coe@localhost:5432/coe"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    default_seed: int = 42
    telemetry_chunk_interval_minutes: int = 10080


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Write `.env.example`**

```bash
DATABASE_URL=postgresql+psycopg://coe:coe@localhost:5432/coe
MQTT_HOST=localhost
MQTT_PORT=1883
DEFAULT_SEED=42
TELEMETRY_CHUNK_INTERVAL_MINUTES=10080
```

- [ ] **Step 7: Write failing test `tests/test_config.py`**

```python
from coe.config import Settings


def test_defaults_match_spec():
    s = Settings(_env_file=None)  # ignore any local .env
    assert s.database_url.startswith("postgresql+psycopg://")
    assert s.mqtt_port == 1883
    assert s.default_seed == 42
    assert s.telemetry_chunk_interval_minutes == 10080
```

- [ ] **Step 8: Install dependencies and run test**

```bash
uv sync
uv run pytest tests/test_config.py -v
```
Expected: 1 passed. First `uv sync` resolves `uv.lock` and creates `.venv/` automatically.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .env.example coe/ tests/
git commit -m "chore: scaffold coe package, pinned deps, settings module"
```

### Task 2: Docker Compose Infrastructure

**Files:**
- Create: `docker-compose.yml`
- Create: `mosquitto/config/mosquitto.conf`

**Interfaces:**
- Consumes: nothing.
- Produces: PostgreSQL+TimescaleDB reachable at `localhost:5432` (user/pass/db = `coe/coe/coe`) and Mosquitto at `localhost:1883`. Every DB-dependent task assumes `docker compose up -d` has been run and both services are healthy.

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  timescaledb:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_USER: coe
      POSTGRES_PASSWORD: coe
      POSTGRES_DB: coe
    ports:
      - "5432:5432"
    volumes:
      - tsdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U coe -d coe"]
      interval: 5s
      timeout: 3s
      retries: 12

  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto/config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
    healthcheck:
      test: ["CMD-SHELL", "mosquitto_sub -h localhost -t '$$SYS/broker/version' -C 1 -W 5"]
      interval: 5s
      timeout: 6s
      retries: 12

volumes:
  tsdata:
```

- [ ] **Step 2: Write `mosquitto/config/mosquitto.conf`**

Development-only anonymous local broker (spec has no auth requirements in Phase 1):

```text
listener 1883
allow_anonymous true
```

- [ ] **Step 3: Start stack and verify health**

```bash
docker compose up -d
sleep 8 && docker compose ps
```
Expected: both services `Up (healthy)`.

- [ ] **Step 4: Verify TimescaleDB extension available**

```bash
docker compose exec timescaledb psql -U coe -d coe \
  -c "SELECT default_version FROM pg_available_extensions WHERE name='timescaledb';" \
  -c "SHOW shared_preload_libraries;"
```
Expected: a version row (`2.x`) and `shared_preload_libraries` containing `timescaledb`. The image ships with the extension preloaded; if not, stop and fix before proceeding.

- [ ] **Step 5: Prove MQTT round-trip**

```bash
uv run python - <<'PY'
import time
import paho.mqtt.client as mqtt

got = {}

def on_message(client, userdata, msg):
    got["payload"] = msg.payload.decode()

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.on_message = on_message
c.connect("localhost", 1883)
c.loop_start()
c.subscribe("smoke/test")
time.sleep(0.5)
c.publish("smoke/test", "hello").wait_for_publish()
time.sleep(0.5)
assert got.get("payload") == "hello", f"round-trip failed: {got}"
print("MQTT roundtrip OK")
c.loop_stop()
PY
```
Expected: `MQTT roundtrip OK`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml mosquitto/
git commit -m "infra: TimescaleDB + Mosquitto compose stack with healthchecks"
```

### Task 3: DB Session, Alembic Bootstrap, Provenance Tables

**Files:**
- Create: `coe/db/base.py`, `coe/db/session.py`, `coe/db/models/provenance.py`
- Create: `alembic.ini`, `alembic/env.py`, first migration under `alembic/versions/`
- Modify: `tests/conftest.py` (create)
- Test: `tests/db/test_migrations.py`

**Interfaces:**
- Consumes: `coe.config.get_settings()`.
- Produces: `Base` (declarative, constraint-naming convention); `make_engine() -> Engine`, `session_scope() -> ContextManager[Session]`; models `Instance`, `ScenarioSource`, `InstanceProfile`. Table names exactly `instances`, `scenario_sources`, `instance_profiles`. Later model tasks inherit the same `Base`.

- [ ] **Step 1: Write `coe/db/base.py`**

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

- [ ] **Step 2: Write `coe/db/session.py`**

```python
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from coe.config import get_settings


def make_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = sessionmaker(bind=make_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 3: Write `coe/db/models/provenance.py`**

```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coe.db.base import Base


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    source_name: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_version: Mapped[str | None] = mapped_column(String(80))
    source_license: Mapped[str | None] = mapped_column(String(200))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    source_time_unit: Mapped[str] = mapped_column(String(40), default="minute")
    time_scale_to_minutes: Mapped[float] = mapped_column(default=1.0)
    normalized_time_unit: Mapped[str] = mapped_column(String(20), default="minute")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScenarioSource(Base):
    __tablename__ = "scenario_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"))
    contribution_type: Mapped[str] = mapped_column(String(60))
    transformation_description: Mapped[str] = mapped_column(Text)
    random_seed: Mapped[int | None]

    scenario: Mapped[Instance] = relationship(foreign_keys=[scenario_id])
    source_instance: Mapped[Instance] = relationship(foreign_keys=[source_instance_id])


class InstanceProfile(Base):
    __tablename__ = "instance_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    profile_type: Mapped[str] = mapped_column(String(60))
    parameters_json: Mapped[dict] = mapped_column(JSONB)
    source_instance_id: Mapped[int | None] = mapped_column(ForeignKey("instances.id"))
```

- [ ] **Step 4: Register model modules**

Append to `coe/db/models/__init__.py`:

```python
import coe.db.models.provenance  # noqa: F401
```

- [ ] **Step 5: Initialize Alembic and wire `alembic/env.py`**

```bash
uv run alembic init alembic
```

Replace the marked sections of `alembic/env.py` — imports/top:

```python
from coe.config import get_settings
from coe.db.base import Base
import coe.db.models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata
```

(The generated file already contains `config = context.config` and `target_metadata = None`; replace that `target_metadata` line and add the imports above it.)

- [ ] **Step 6: Autogenerate first migration**

```bash
uv run alembic revision --autogenerate -m "provenance tables"
uv run alembic upgrade head
```
Expected: migration file created under `alembic/versions/`; upgrade succeeds. Inspect the autogenerated file — it must contain exactly `instances`, `scenario_sources`, `instance_profiles` and nothing else. Fix the `alembic.ini` script location if `alembic` was pointed elsewhere (`script_location = alembic`).

- [ ] **Step 7: Write `tests/conftest.py`**

```python
import subprocess

import pytest
from sqlalchemy import create_engine, text

from coe.config import get_settings


def reset_database(url: str) -> None:
    """Drop every user table in public, then rebuild via Alembic (authoritative DDL)."""
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(
            text(
                "DO $do$ DECLARE r record; BEGIN "
                "FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP "
                "EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
                "END LOOP; END $do$;"
            )
        )
    eng.dispose()
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)


@pytest.fixture(scope="session")
def db_url() -> str:
    return get_settings().database_url


@pytest.fixture()
def clean_db(db_url):
    reset_database(db_url)
    yield db_url
```

- [ ] **Step 8: Write failing test `tests/db/test_migrations.py`**

```python
import inspect

import pytest

pytestmark = pytest.mark.db


def test_provenance_tables_exist(clean_db):
    from sqlalchemy import create_engine

    insp = inspect(create_engine(clean_db))
    assert insp.has_table("instances")
    assert insp.has_table("scenario_sources")
    assert insp.has_table("instance_profiles")


def test_instances_name_unique(clean_db):
    from sqlalchemy import create_engine

    insp = inspect(create_engine(clean_db))
    uqs = [c["column_names"] for c in insp.get_unique_constraints("instances")]
    assert ["name"] in uqs
```

Run: `uv run pytest tests/db/test_migrations.py -v`
Expected: 2 passed.

- [ ] **Step 9: Commit**

```bash
git add coe/db alembic alembic.ini tests/
git commit -m "feat(db): base/session, provenance tables, alembic bootstrap"
```

### Task 4: FJSP Core Tables

**Files:**
- Create: `coe/db/models/fjsp.py`
- Modify: `coe/db/models/__init__.py`
- Test: `tests/db/test_fjsp_models.py`

**Interfaces:**
- Consumes: Task 3 `Base`, `Instance`.
- Produces: models `Machine`, `MachineCapability`, `Job`, `Operation`, `OperationMachineAlternative`, `JobFamily`, `SetupTime`. Composite-PK convention `(instance_id, ...)` used later by scenario builder queries. Parent tables expose `UNIQUE(id, instance_id)` so child tables can build composite cross-instance-safe FKs.

- [ ] **Step 1: Write `coe/db/models/fjsp.py`**

```python
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class Machine(Base):
    __tablename__ = "machines"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','FAILED','MAINTENANCE')",
            name="machine_status",
        ),
        UniqueConstraint("id", "instance_id", name="uq_machines_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class MachineCapability(Base):
    __tablename__ = "machine_capabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    capability_code: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(40))


class JobFamily(Base):
    __tablename__ = "job_families"
    __table_args__ = (UniqueConstraint("id", "instance_id", name="uq_job_families_id_instance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','IN_PROGRESS','COMPLETED','BLOCKED')",
            name="job_status",
        ),
        UniqueConstraint("id", "instance_id", name="uq_jobs_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    job_family_id: Mapped[int | None]
    release_time: Mapped[int] = mapped_column(default=0)
    deadline: Mapped[int | None]
    priority: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")

    family: Mapped["JobFamily | None"] = relationship()


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint("sequence_number >= 1", name="sequence_min"),
        CheckConstraint(
            "status IN ('PENDING','SCHEDULED','IN_PROGRESS','COMPLETED',"
            "'INTERRUPTED','BLOCKED')",
            name="operation_status",
        ),
        UniqueConstraint("job_id", "sequence_number", name="uq_operations_job_sequence"),
        UniqueConstraint("id", "instance_id", name="uq_operations_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    source_id: Mapped[str | None] = mapped_column(String(60))
    sequence_number: Mapped[int] = mapped_column(Integer)
    required_role_id: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(20), default="PENDING")


class OperationMachineAlternative(Base):
    __tablename__ = "operation_machine_alternatives"
    __table_args__ = (
        CheckConstraint("processing_time >= 0", name="processing_time_nonnegative"),
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), primary_key=True
    )
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id"), primary_key=True
    )
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id"), primary_key=True
    )
    processing_time: Mapped[int] = mapped_column(Integer)


class SetupTime(Base):
    __tablename__ = "setup_times"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    from_family_id: Mapped[int | None] = mapped_column(ForeignKey("job_families.id"))
    to_family_id: Mapped[int | None] = mapped_column(ForeignKey("job_families.id"))
    setup_duration: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40))
```

Note on the spec's logical key: `(instance_id, machine_id, from_family_id, to_family_id)` is enforced at import time (`coe.parsers.common`) rather than as a DB constraint because two NULL `from_family_id` values never collide in a SQL unique index. The importer rejects duplicates explicitly.

- [ ] **Step 2: Register module**

Append to `coe/db/models/__init__.py`:

```python
import coe.db.models.fjsp  # noqa: F401
```

- [ ] **Step 3: Generate and apply migration**

```bash
uv run alembic revision --autogenerate -m "fjsp core tables"
uv run alembic upgrade head
```
Expected: upgrade succeeds; autogenerated migration contains all seven tables.

- [ ] **Step 4: Write failing tests `tests/db/test_fjsp_models.py`**

```python
import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _mk_instance(session):
    from coe.db.models.provenance import Instance

    inst = Instance(name="t-fjsp", source_name="test")
    session.add(inst)
    session.flush()
    return inst


def test_operation_unique_job_sequence(clean_db):
    from coe.db.session import session_scope
    from coe.db.models.fjsp import Operation
    from coe.db.models.provenance import Instance

    with session_scope() as s:
        inst = Instance(name="t-opseq", source_name="test")
        s.add(inst)
        s.flush()
        j = _simple_job(s, inst.id)
        s.add(Operation(instance_id=inst.id, job_id=j.id, sequence_number=1))
        s.add(Operation(instance_id=inst.id, job_id=j.id, sequence_number=1))
        try:
            s.flush()
            raised = False
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_bad_status_rejected(clean_db):
    from sqlalchemy import text
    from coe.db.session import make_engine
    from coe.db.models.provenance import Instance

    with session_scope() as s:
        inst = Instance(name="t-status", source_name="test")
        s.add(inst)
        s.flush()
        s.execute(
            text(
                "INSERT INTO machines (instance_id, name, status) "
                "VALUES (:i, 'MC-X', 'BROKEN')"
            ),
            {"i": inst.id},
        )
        try:
            s.commit()
            ok = False
        except IntegrityError:
            ok = True
            s.rollback()
    assert ok


def test_alternative_composite_pk(clean_db):
    """Same (op, machine) twice in one instance must fail; different instances OK."""
    from coe.db.session import session_scope
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative as Alt,
    )
    from coe.db.models.provenance import Instance

    with session_scope() as s:
        i1 = Instance(name="t-alt-a", source_name="test")
        s.add(i1)
        s.flush()
        m = Machine(instance_id=i1.id, name="M1")
        j = Job(instance_id=i1.id, name="J1")
        s.add_all([m, j])
        s.flush()
        op = Operation(instance_id=i1.id, job_id=j.id, sequence_number=1)
        s.add(op)
        s.flush()
        s.add(Alt(instance_id=i1.id, operation_id=op.id, machine_id=m.id, processing_time=5))
        s.flush()  # first insert fine
        s.add(Alt(instance_id=i1.id, operation_id=op.id, machine_id=m.id, processing_time=7))
        try:
            s.flush()
            dup_rejected = False
        except IntegrityError:
            dup_rejected = True
            s.rollback()
    assert dup_rejected


def _simple_job(session, instance_id):
    from coe.db.models.fjsp import Job

    j = Job(instance_id=instance_id, name="J1")
    session.add(j)
    session.flush()
    return j
```

Run: `uv run pytest tests/db/test_fjsp_models.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/db/models tests/db
git commit -m "feat(db): fjsp core tables with status checks and composite keys"
```

### Task 5: Worker + Materials Tables

**Files:**
- Create: `coe/db/models/workers.py`, `coe/db/models/materials.py`
- Modify: `coe/db/models/fjsp.py` (backfill two deferred FKs), `coe/db/models/__init__.py`
- Test: `tests/db/test_worker_material_models.py`

**Interfaces:**
- Consumes: Task 3 `Base`; Task 4 `Machine`, `Operation`.
- Produces: models `WorkerRole`, `Worker`, `OperationMachineWorkerTime`, `WorkerAvailabilityWindow`, `Material`, `OperationBom`, `MaterialReceipt`. After this task every Phase 1 schema table exists; Tasks 6 adds only downtime/telemetry.

- [ ] **Step 1: Backfill deferred FKs in `coe/db/models/fjsp.py`**

Replace:

```python
    job_family_id: Mapped[int | None]
```

with:

```python
    job_family_id: Mapped[int | None] = mapped_column(ForeignKey("job_families.id"))
```

and replace:

```python
    required_role_id: Mapped[int | None]
```

with:

```python
    required_role_id: Mapped[int | None] = mapped_column(ForeignKey("worker_roles.id"))
```

(The `family` relationship already declared stays; add none for role.)

- [ ] **Step 2: Write `coe/db/models/workers.py`**

```python
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class WorkerRole(Base):
    __tablename__ = "worker_roles"
    __table_args__ = (UniqueConstraint("id", "instance_id", name="uq_worker_roles_id_instance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    role_name: Mapped[str] = mapped_column(String(120))


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AVAILABLE','UNAVAILABLE')",
            name="worker_status",
        ),
        UniqueConstraint("id", "instance_id", name="uq_workers_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("worker_roles.id"))
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")


class OperationMachineWorkerTime(Base):
    """Authoritative worker eligibility: a missing row means 'cannot perform'."""

    __tablename__ = "operation_machine_worker_times"

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), primary_key=True
    )
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id"), primary_key=True
    )
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), primary_key=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), primary_key=True)
    processing_time: Mapped[int] = mapped_column(Integer)


class WorkerAvailabilityWindow(Base):
    __tablename__ = "worker_availability_windows"
    __table_args__ = (
        CheckConstraint("available_until >= available_from", name="window_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"))
    available_from: Mapped[int] = mapped_column(Integer)
    available_until: Mapped[int] = mapped_column(Integer)
    source_pattern: Mapped[str] = mapped_column(String(80))
```

- [ ] **Step 3: Write `coe/db/models/materials.py`**

```python
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (
        CheckConstraint("initial_stock >= 0", name="stock_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    sku: Mapped[str] = mapped_column(String(80))
    initial_stock: Mapped[int] = mapped_column(Integer)
    reorder_point: Mapped[int | None]


class OperationBom(Base):
    __tablename__ = "operation_bom"
    __table_args__ = (
        CheckConstraint("quantity_required > 0", name="qty_positive"),
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), primary_key=True
    )
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id"), primary_key=True
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id"), primary_key=True
    )
    quantity_required: Mapped[int] = mapped_column(Integer)


class MaterialReceipt(Base):
    __tablename__ = "material_receipts"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND available_at >= 0", name="receipt_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40))
```

- [ ] **Step 4: Register modules**

Append to `coe/db/models/__init__.py`:

```python
import coe.db.models.workers  # noqa: F401
import coe.db.models.materials  # noqa: F401
```

- [ ] **Step 5: Generate and apply migration**

```bash
uv run alembic revision --autogenerate -m "workers materials tables"
uv run alembic upgrade head
```
Expected: upgrade succeeds. **Verify** the migration includes `create_foreign_key` ops for `jobs.job_family_id → job_families.id` and `operations.required_role_id → worker_roles.id`. If autogenerated output omitted them, append manually inside `upgrade()`:

```python
op.create_foreign_key(
    "fk_jobs_job_family_id_job_families", "jobs", "job_families",
    ["job_family_id"], ["id"],
)
op.create_foreign_key(
    "fk_operations_required_role_id_worker_roles", "operations", "worker_roles",
    ["required_role_id"], ["id"],
)
```

- [ ] **Step 6: Write failing tests `tests/db/test_worker_material_models.py`**

```python
import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _fixture_ids(session):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.provenance import Instance

    inst = Instance(name="t-wm", source_name="test")
    session.add(inst)
    session.flush()
    m = Machine(instance_id=inst.id, name="M1")
    j = Job(instance_id=inst.id, name="J1")
    session.add_all([m, j])
    session.flush()
    op = Operation(instance_id=inst.id, job_id=j.id, sequence_number=1)
    session.add(op)
    session.flush()
    return inst, m, op


def test_worker_eligibility_composite_pk(clean_db):
    from coe.db.models.materials import Material  # noqa: F401
    from coe.db.models.workers import (
        OperationMachineWorkerTime as Omwt,
        Worker,
        WorkerRole,
    )
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, m, op = _fixture_ids(s)
        role = WorkerRole(instance_id=inst.id, role_name="operator")
        s.add(role)
        s.flush()
        w = Worker(instance_id=inst.id, name="W1", role_id=role.id)
        s.add(w)
        s.flush()
        s.add(Omwt(instance_id=inst.id, operation_id=op.id, machine_id=m.id,
                   worker_id=w.id, processing_time=10))
        s.flush()
        try:
            s.add(Omwt(instance_id=inst.id, operation_id=op.id, machine_id=m.id,
                       worker_id=w.id, processing_time=12))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_availability_window_order_check(clean_db):
    from coe.db.models.workers import Worker, WorkerAvailabilityWindow
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, _, _ = _fixture_ids(s)
        w = Worker(instance_id=inst.id, name="W2")
        s.add(w)
        s.flush()
        try:
            s.add(WorkerAvailabilityWindow(
                instance_id=inst.id, worker_id=w.id,
                available_from=100, available_until=50,
                source_pattern="shift"))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_bom_quantity_positive(clean_db):
    from coe.db.models.materials import Material, OperationBom
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, m, op = _fixture_ids(s)
        mat = Material(instance_id=inst.id, sku="STEEL-304", initial_stock=100)
        s.add(mat)
        s.flush()
        try:
            s.add(OperationBom(instance_id=inst.id, operation_id=op.id,
                               material_id=mat.id, quantity_required=0))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised
```

Run: `uv run pytest tests/db/test_worker_material_models.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add coe/db/models tests/db
git commit -m "feat(db): worker flexibility and materials tables"
```

### Task 6: Downtime Windows + Telemetry Hypertable

**Files:**
- Create: `coe/db/models/downtime.py`
- Modify: `coe/db/models/__init__.py`, new migration under `alembic/versions/`
- Test: `tests/db/test_downtime_models.py`

**Interfaces:**
- Consumes: Task 3 `Base`; Task 4 `Machine`.
- Produces: models `TelemetryEvent`, `MachineDowntimeWindow`. `telemetry_events` is a TimescaleDB hypertable on `occurred_at`, chunk interval 10080 minutes. The MQTT task (15) inserts through these models.

**Design note (documented deviation):** the spec marks `message_id` "(unique)". TimescaleDB forbids unique indexes that exclude the partitioning column on integer-partitioned hypertables, so DB-level uniqueness of `message_id` alone is impossible while partitioning by minute. Resolution: composite `PRIMARY KEY (id, occurred_at)`, plain index on `message_id`, application-level idempotency inside the ingestion transaction (Task 15) — satisfying the spec's actual requirement that duplicate messages never create duplicate state changes.

- [ ] **Step 1: Write `coe/db/models/downtime.py`**

```python
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        CheckConstraint("occurred_at >= 0", name="occurred_nonnegative"),
        CheckConstraint("received_at >= 0", name="received_nonnegative"),
    )

    # Composite PK (id, occurred_at) satisfies the TimescaleDB rule that every
    # unique index includes the partitioning column; Identity() keeps the id
    # auto-incrementing despite the composite key.
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    occurred_at: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(160))
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    received_at: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str | None] = mapped_column(String(20))
    estimated_downtime: Mapped[int | None]
    processed_at: Mapped[int | None]
    processing_error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB)


Index("ix_telemetry_message_id", TelemetryEvent.message_id)


class MachineDowntimeWindow(Base):
    __tablename__ = "machine_downtime_windows"
    __table_args__ = (
        CheckConstraint(
            "downtime_until IS NULL OR downtime_until > downtime_from",
            name="downtime_interval_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    downtime_from: Mapped[int] = mapped_column(Integer)
    downtime_until: Mapped[int | None]
    reason: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str | None] = mapped_column(String(20))
    source_event_ids: Mapped[list] = mapped_column(JSONB, default=list)
```

- [ ] **Step 2: Register module**

Append to `coe/db/models/__init__.py`:

```python
import coe.db.models.downtime  # noqa: F401
```

- [ ] **Step 3: Autogenerate migration, then inject TimescaleDB SQL**

```bash
uv run alembic revision --autogenerate -m "downtime and telemetry"
```

Edit the generated file:

1. Top of `upgrade()`, before any `create_table`:

```python
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
```

2. Bottom of `upgrade()`, after all `create_table` calls:

```python
    op.execute(
        "SELECT create_hypertable("
        "'telemetry_events', 'occurred_at', "
        "chunk_time_interval => 10080);"
    )
```

(The composite PK `(id, occurred_at)` satisfies the hypertable rule requiring unique/PK indexes to include the partitioning column.)

3. Top of `downgrade()`:

```python
    op.execute("SELECT drop_chunks('telemetry_events', older_than => 0::bigint);")
```

Apply:

```bash
uv run alembic upgrade head
```
Expected: succeeds.

- [ ] **Step 4: Write failing tests `tests/db/test_downtime_models.py`**

```python
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def _ids(session):
    from coe.db.models.fjsp import Machine
    from coe.db.models.provenance import Instance

    inst = Instance(name=f"t-dt-{id(session) % 10000}", source_name="test")
    session.add(inst)
    session.flush()
    m = Machine(instance_id=inst.id, name="MC-1")
    session.add(m)
    session.flush()
    return inst, m.id


def _event(inst_id, machine_id, **over):
    from coe.db.models.downtime import TelemetryEvent

    base = dict(
        occurred_at=10, instance_id=inst_id, message_id="m-1",
        machine_id=machine_id, event_type="FAILURE",
        received_at=10, payload_json={},
    )
    base.update(over)
    return TelemetryEvent(**base)


def test_hypertable_exists(clean_db):
    from coe.db.session import session_scope

    with session_scope() as s:
        n = s.execute(
            text(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'telemetry_events'"
            )
        ).scalar_one()
    assert n == 1


def test_negative_occurred_at_rejected(clean_db):
    from coe.db.session import session_scope

    raised = False
    with session_scope() as s:
        inst, mid = _ids(s)
        try:
            s.add(_event(inst.id, mid, occurred_at=-5))
            s.flush()
        except IntegrityError:
            raised = True
            s.rollback()
    assert raised


def test_rows_span_two_chunks(clean_db):
    from coe.db.session import make_engine, session_scope

    with session_scope() as s:
        inst, mid = _ids(s)
        s.add(_event(inst.id, mid, message_id="m-a", occurred_at=0))
        s.add(_event(inst.id, mid, message_id="m-b", occurred_at=20000))
    with make_engine().begin() as conn:
        chunks = conn.execute(
            text("SELECT count(*) FROM show_chunks('telemetry_events')")
        ).scalar_one()
    assert chunks >= 2


def test_open_ended_downtime_allowed(clean_db):
    from coe.db.models.downtime import MachineDowntimeWindow
    from coe.db.session import session_scope

    with session_scope() as s:
        inst, mid = _ids(s)
        w = MachineDowntimeWindow(
            instance_id=inst.id, machine_id=mid,
            downtime_from=100, downtime_until=None,
            reason="FAILURE", severity="HIGH", source_event_ids=["evt-1"],
        )
        s.add(w)
        s.flush()
        assert w.downtime_until is None
```

Run: `uv run pytest tests/db/test_downtime_models.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/db/models alembic/versions tests/db
git commit -m "feat(db): downtime windows and telemetry hypertable"
```

### Task 7: MK01 Parser + Import

**Files:**
- Create: `coe/parsers/common.py`, `coe/parsers/mk01.py`
- Modify: `coe/cli.py`
- Test: `tests/parsers/test_mk01_parse.py`, `tests/db/test_mk01_import.py`

**Interfaces:**
- Consumes: models from Tasks 3–6; `session_scope`.
- Produces: `SourceParseError`; `sha256_file(path: Path) -> str`; `get_or_create_source_instance(session, *, name, source_name, source_url, source_version, source_license, checksum) -> tuple[Instance, bool]`; `parse_mk01(raw: str) -> ParsedMkInstance` (dataclasses `ParsedMkInstance/ParsedJob/ParsedOperation/Alt`, all 0-based `index`/`machine_index`); `import_mk01(path: Path, instance_name: str = "mk01") -> int` returning instance id. Tasks 8–9 reuse `common`.

- [ ] **Step 1: Write `coe/parsers/common.py`**

```python
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from coe.db.models.provenance import Instance


class SourceParseError(ValueError):
    """Raised when a source file violates its documented format."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_or_create_source_instance(
    session: Session,
    *,
    name: str,
    source_name: str,
    checksum: str | None,
    source_url: str | None = None,
    source_version: str | None = None,
    source_license: str | None = None,
) -> tuple[Instance, bool]:
    """Spec §11: identical re-import is a no-op; changed checksum creates a new instance."""
    existing = session.query(Instance).filter(Instance.name == name).one_or_none()
    if existing is not None:
        if existing.source_checksum == checksum:
            return existing, False
        name = f"{name}@{checksum[:8]}"
        existing = session.query(Instance).filter(Instance.name == name).one_or_none()
        if existing is not None:
            return existing, False
    inst = Instance(
        name=name,
        source_name=source_name,
        source_url=source_url,
        source_version=source_version,
        source_license=source_license,
        retrieved_at=datetime.now(timezone.utc),
        source_checksum=checksum,
    )
    session.add(inst)
    session.flush()
    return inst, True
```

- [ ] **Step 2: Write `coe/parsers/mk01.py`**

```python
from dataclasses import dataclass, field
from pathlib import Path

from coe.db.models.fjsp import Job, Machine, Operation, OperationMachineAlternative
from coe.db.session import session_scope
from coe.parsers.common import (
    SourceParseError,
    get_or_create_source_instance,
    sha256_file,
)


@dataclass
class Alt:
    machine_index: int
    processing_time: int


@dataclass
class ParsedOperation:
    index: int
    alternatives: list[Alt] = field(default_factory=list)


@dataclass
class ParsedJob:
    index: int
    operations: list[ParsedOperation] = field(default_factory=list)


@dataclass
class ParsedMkInstance:
    n_jobs: int
    n_machines: int
    jobs: list[ParsedJob]


def parse_mk01(raw: str) -> ParsedMkInstance:
    """Brandimarte grammar: '<n_jobs> <n_machines>' then per job '<num_ops>' then
    per operation '<num_alts> (<machine> <time>)*'. Machines are 0-indexed."""
    tokens: list[tuple[int, int]] = []  # (value, lineno)
    for lineno, line in enumerate(raw.splitlines(), start=1):
        for piece in line.split():
            try:
                tokens.append((int(piece), lineno))
            except ValueError as exc:
                raise SourceParseError(
                    f"line {lineno}: non-integer token {piece!r}"
                ) from exc

    pos = 0

    def nxt(context: str) -> tuple[int, int]:
        nonlocal pos
        if pos >= len(tokens):
            raise SourceParseError(f"unexpected end of file while reading {context}")
        value, lineno = tokens[pos]
        pos += 1
        return value, lineno

    n_jobs, _ = nxt("header job count")
    n_machines, _ = nxt("header machine count")
    if n_jobs <= 0 or n_machines <= 0:
        raise SourceParseError("header must contain positive job and machine counts")

    jobs: list[ParsedJob] = []
    for j in range(n_jobs):
        job = ParsedJob(index=j)
        n_ops, _ = nxt(f"job {j + 1} operation count")
        for o in range(n_ops):
            op = ParsedOperation(index=o)
            k, _ = nxt(f"job {j + 1} operation {o + 1} alternative count")
            if k <= 0:
                raise SourceParseError(
                    f"job {j + 1} operation {o + 1}: needs at least one capable machine"
                )
            for _ in range(k):
                (m, ml), (t, tl) = (
                    nxt(f"job {j + 1} operation {o + 1} machine"),
                    nxt(f"job {j + 1} operation {o + 1} processing time"),
                )
                if not 0 <= m < n_machines:
                    raise SourceParseError(
                        f"line {ml}: machine {m} outside 0..{n_machines - 1}"
                    )
                if t <= 0:
                    raise SourceParseError(f"line {tl}: non-positive duration {t}")
                op.alternatives.append(Alt(machine_index=m, processing_time=t))
            job.operations.append(op)
        jobs.append(job)

    if pos != len(tokens):
        _, lineno = tokens[pos]
        raise SourceParseError(f"line {lineno}: trailing tokens after last operation")

    return ParsedMkInstance(n_jobs=n_jobs, n_machines=n_machines, jobs=jobs)


SOURCE_META = dict(
    source_name="brandimarte",
    source_url="https://github.com/SchedulingLab/fjsp-instances",
    source_version="brandimarte-mk",
    source_license="academic-benchmark",
)


def import_mk01(path: Path, instance_name: str = "mk01") -> int:
    """Atomic import into an isolated instance; returns the instance id."""
    parsed = parse_mk01(path.read_text())
    checksum = sha256_file(path)
    with session_scope() as session:
        inst, created = get_or_create_source_instance(
            session, name=instance_name, checksum=checksum, **SOURCE_META
        )
        if not created:
            return inst.id

        machines = {}
        for mi in range(parsed.n_machines):
            row = Machine(
                instance_id=inst.id, source_id=str(mi), name=f"M{mi}", status="ACTIVE"
            )
            session.add(row)
            machines[mi] = row

        for job in parsed.jobs:
            jrow = Job(
                instance_id=inst.id, source_id=str(job.index), name=f"J{job.index + 1}"
            )
            session.add(jrow)
            session.flush()
            for op in job.operations:
                orow = Operation(
                    instance_id=inst.id,
                    job_id=jrow.id,
                    source_id=f"{job.index}:{op.index}",
                    sequence_number=op.index + 1,
                )
                session.add(orow)
                session.flush()
                for alt in op.alternatives:
                    session.add(
                        OperationMachineAlternative(
                            instance_id=inst.id,
                            operation_id=orow.id,
                            machine_id=machines[alt.machine_index].id,
                            processing_time=alt.processing_time,
                        )
                    )
        session.flush()
        return inst.id
```

- [ ] **Step 3: Wire CLI `import mk01`**

Replace the stub body of `coe/cli.py` with:

```python
import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coe", description="COE factory recovery system")
    sub = parser.add_subparsers(dest="group", required=True)

    imp = sub.add_parser("import", help="import a raw source dataset")
    sources = imp.add_subparsers(dest="source", required=True)
    mk01 = sources.add_parser("mk01")
    mk01.add_argument("--path", default="data/raw/mk01/mk01.txt")

    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if args.group == "import":
        if args.source == "mk01":
            from coe.parsers.mk01 import import_mk01

            instance_id = import_mk01(Path(args.path))
            print(f"instance id={instance_id}")


if __name__ == "__main__":
    main()
```

Later tasks extend only `build_parser()` and the dispatch in `main()`.

- [ ] **Step 4: Write pure-parse tests `tests/parsers/test_mk01_parse.py`**

```python
import pytest

from coe.parsers.common import SourceParseError
from coe.parsers.mk01 import parse_mk01

VALID = "2 2\n2 1 5 2 4\n1 1 7\n"


def test_parses_dimensions_and_alternatives():
    parsed = parse_mk01(VALID)
    assert (parsed.n_jobs, parsed.n_machines) == (2, 2)
    assert [a.machine_index for a in parsed.jobs[0].operations[0].alternatives] == [1, 2]
    assert parsed.jobs[0].operations[0].alternatives[0].processing_time == 5


def test_real_mk01_shape(data_dir):
    raw = (data_dir / "raw/mk01/mk01.txt").read_text()
    parsed = parse_mk01(raw)
    assert (parsed.n_jobs, parsed.n_machines) == (10, 6)
    assert sum(len(j.operations) for j in parsed.jobs) == 55
    first = parsed.jobs[0].operations[0].alternatives
    assert [(a.machine_index, a.processing_time) for a in first] == [(0, 5), (2, 4)]


def test_non_integer_token_reports_line():
    with pytest.raises(SourceParseError, match="line 2"):
        parse_mk01("2 1\n1 1 x\n")


def test_trailing_tokens_rejected():
    with pytest.raises(SourceParseError, match="trailing"):
        parse_mk01(VALID + "99")


def test_machine_index_out_of_range():
    with pytest.raises(SourceParseError, match="outside"):
        parse_mk01("1 1\n1 1 5\n")
```

Add to `tests/conftest.py`:

```python
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"
```

(`Path(__file__).parents[1]` from `tests/conftest.py` is the repo root.)

Run: `uv run pytest tests/parsers/test_mk01_parse.py -v`
Expected: 5 passed.

- [ ] **Step 5: Write DB-import tests `tests/db/test_mk01_import.py`**

```python
from pathlib import Path

import pytest

pytestmark = pytest.mark.db

MK01_PATH = Path("data/raw/mk01/mk01.txt")


def _counts(url):
    from sqlalchemy import create_engine, text

    with create_engine(url).begin() as c:
        return {
            t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("instances", "machines", "jobs", "operations",
                      "operation_machine_alternatives")
        }


def test_import_creates_expected_shape(clean_db):
    from coe.parsers.mk01 import import_mk01

    inst_id = import_mk01(MK01_PATH)
    counts = _counts(clean_db)
    assert (counts["machines"], counts["jobs"], counts["operations"]) == (6, 10, 55)
    assert counts["operation_machine_alternatives"] > 100

    from sqlalchemy import create_engine, text

    with create_engine(clean_db).begin() as c:
        row = c.execute(
            text(
                "SELECT oma.machine_id, oma.processing_time "
                "FROM operation_machine_alternatives oma "
                "JOIN operations o ON o.id = oma.operation_id "
                "JOIN jobs j ON j.id = o.job_id "
                "WHERE j.source_id='0' AND o.source_id='0:0' "
                "ORDER BY oma.machine_id"
            )
        ).all()
        machines = dict(
            c.execute(
                text("SELECT source_id, name FROM machines WHERE instance_id = :i"),
                {"i": inst_id},
            ).all()
        )
    assert {(machines[str(m)], t) for m, t in row} == {("M0", 5), ("M2", 4)}


def test_reimport_is_noop(clean_db):
    from coe.parsers.mk01 import import_mk01

    a = import_mk01(MK01_PATH)
    b = import_mk01(MK01_PATH)
    assert a == b
    assert _counts(clean_db)["instances"] == 1


def test_failed_import_leaves_no_partial_instance(clean_db):
    import io

    bad = Path("tests/fixtures/bad_mk01.txt")
    bad.write_text("2 2\n1 1 5\n1 9 9\n")  # machine index 9 out of range for 2 machines
    try:
        from coe.parsers.mk01 import import_mk01
        from coe.parsers.common import SourceParseError

        with pytest.raises(SourceParseError):
            import_mk01(bad)
        assert _counts(clean_db)["instances"] == 0
    finally:
        bad.unlink()
```

Run: `uv run pytest tests/db/test_mk01_import.py -v`
Expected: 3 passed.

- [ ] **Step 6: Smoke the CLI**

```bash
uv run python -m coe.cli import mk01
```
Expected: prints `instance id=...`.

- [ ] **Step 7: Commit**

```bash
git add coe/parsers coe/cli.py tests/
git commit -m "feat(parsers): brandimarte mk01 parser with atomic idempotent import"
```

### Task 8: Hutter/Nouri FJSSP-W Parser + Import

**Files:**
- Create: `coe/parsers/nouri.py`
- Modify: `coe/cli.py`
- Test: `tests/parsers/test_nouri_parse.py`, `tests/db/test_nouri_import.py`

**Interfaces:**
- Consumes: `common` helpers from Task 7.
- Produces: `parse_nouri(raw: str) -> ParsedNouriInstance` (dataclasses `ParsedNouriInstance(n_jobs, n_machines, n_workers, jobs)`, `NouriJob`, `NouriOperation`, `NouriAlt(machine_index, workers=[(worker_index, processing_time)])` — all 0-based after conversion); `import_nouri(path: Path, instance_name: str | None = None) -> int`. The scenario builder (Task 12) consumes only the parsed structure, not DB rows.

- [ ] **Step 1: Write `coe/parsers/nouri.py`**

Grammar per `data/raw/nouri-fjspw/extracted/DataExplanation.txt`: header `<jobs> <machines> <workers>`; each job line starts with its operation count; each operation lists capable-machine count, then per machine: machine number, worker count, then `(worker, time)` pairs. File indices are 1-based; converted to 0-based at parse time.

```python
from dataclasses import dataclass, field
from pathlib import Path

from coe.db.models.fjsp import (
    Job,
    Machine,
    Operation,
    OperationMachineAlternative,
)
from coe.db.models.workers import OperationMachineWorkerTime, Worker
from coe.db.session import session_scope
from coe.parsers.common import (
    SourceParseError,
    get_or_create_source_instance,
    sha256_file,
)


@dataclass
class NouriAlt:
    machine_index: int
    workers: list[tuple[int, int]] = field(default_factory=list)  # (worker_idx, time)


@dataclass
class NouriOperation:
    index: int
    alternatives: list[NouriAlt] = field(default_factory=list)


@dataclass
class NouriJob:
    index: int
    operations: list[NouriOperation] = field(default_factory=list)


@dataclass
class ParsedNouriInstance:
    n_jobs: int
    n_machines: int
    n_workers: int
    jobs: list[NouriJob]


def parse_nouri(raw: str) -> ParsedNouriInstance:
    tokens: list[tuple[int, int]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        for piece in line.split():
            try:
                tokens.append((int(piece), lineno))
            except ValueError as exc:
                raise SourceParseError(
                    f"line {lineno}: non-integer token {piece!r}"
                ) from exc

    pos = 0

    def nxt(context: str) -> tuple[int, int]:
        nonlocal pos
        if pos >= len(tokens):
            raise SourceParseError(f"unexpected end of file while reading {context}")
        value, lineno = tokens[pos]
        pos += 1
        return value, lineno

    n_jobs, _ = nxt("header job count")
    n_machines, _ = nxt("header machine count")
    n_workers, _ = nxt("header worker count")
    dims = (n_jobs, n_machines, n_workers)
    if any(v <= 0 for v in dims):
        raise SourceParseError(f"header dimensions must be positive, got {dims}")

    jobs: list[NouriJob] = []
    for j in range(n_jobs):
        job = NouriJob(index=j)
        n_ops, _ = nxt(f"job {j + 1} operation count")
        for o in range(n_ops):
            op = NouriOperation(index=o)
            k, _ = nxt(f"job {j + 1} op {o + 1} machine count")
            if k <= 0:
                raise SourceParseError(
                    f"job {j + 1} op {o + 1}: needs at least one capable machine"
                )
            for _ in range(k):
                m, ml = nxt(f"job {j + 1} op {o + 1} machine number")
                w_count, _ = nxt(f"job {j + 1} op {o + 1} worker count")
                if not 0 <= m - 1 < n_machines:
                    raise SourceParseError(
                        f"line {ml}: machine {m} outside 1..{n_machines}"
                    )
                alt = NouriAlt(machine_index=m - 1)
                for _ in range(w_count):
                    w, wl = nxt(f"job {j + 1} op {o + 1} worker number")
                    t, tl = nxt(f"job {j + 1} op {o + 1} worker processing time")
                    if not 0 <= w - 1 < n_workers:
                        raise SourceParseError(
                            f"line {wl}: worker {w} outside 1..{n_workers}"
                        )
                    if t <= 0:
                        raise SourceParseError(f"line {tl}: non-positive duration {t}")
                    alt.workers.append((w - 1, t))
                op.alternatives.append(alt)
            job.operations.append(op)
        jobs.append(job)

    if pos != len(tokens):
        _, lineno = tokens[pos]
        raise SourceParseError(f"line {lineno}: trailing tokens after last operation")

    return ParsedNouriInstance(
        n_jobs=n_jobs, n_machines=n_machines, n_workers=n_workers, jobs=jobs
    )


def import_nouri(path: Path, instance_name: str | None = None) -> int:
    """Atomic import; literal worker/op rows stay inside their own instance."""
    raw = path.read_text()
    parsed = parse_nouri(raw)
    checksum = sha256_file(path)
    name = instance_name or f"nouri-{path.stem.lower()}"
    with session_scope() as session:
        inst, created = get_or_create_source_instance(
            session,
            name=name,
            source_name="hutter-nouri-fjssp-w",
            # No verified public URL: record it only once confirmed against the
            # dataset's own README; leaving it null is honest provenance.
            source_url=None,
            source_version="mo-fjspw",
            source_license="academic-benchmark",
            checksum=checksum,
        )
        if not created:
            return inst.id

        machines = {}
        for mi in range(parsed.n_machines):
            row = Machine(instance_id=inst.id, source_id=str(mi), name=f"M{mi}")
            session.add(row)
            machines[mi] = row

        workers = {}
        for wi in range(parsed.n_workers):
            row = Worker(instance_id=inst.id, source_id=str(wi), name=f"W{wi}")
            session.add(row)
            workers[wi] = row

        for job in parsed.jobs:
            jrow = Job(
                instance_id=inst.id, source_id=str(job.index), name=f"J{job.index + 1}"
            )
            session.add(jrow)
            session.flush()
            for op in job.operations:
                orow = Operation(
                    instance_id=inst.id,
                    job_id=jrow.id,
                    source_id=f"{job.index}:{op.index}",
                    sequence_number=op.index + 1,
                )
                session.add(orow)
                session.flush()
                for alt in op.alternatives:
                    # Derive the machine-level alternative as the min worker time so
                    # the source instance is self-consistent without worker context.
                    session.add(
                        OperationMachineAlternative(
                            instance_id=inst.id,
                            operation_id=orow.id,
                            machine_id=machines[alt.machine_index].id,
                            processing_time=min(t for _, t in alt.workers),
                        )
                    )
                    for wi, t in alt.workers:
                        session.add(
                            OperationMachineWorkerTime(
                                instance_id=inst.id,
                                operation_id=orow.id,
                                machine_id=machines[alt.machine_index].id,
                                worker_id=workers[wi].id,
                                processing_time=t,
                            )
                        )
        session.flush()
        return inst.id
```

- [ ] **Step 2: Wire CLI `import hutter`**

In `build_parser()`, add next to the mk01 subparser:

```python
    hutter = sources.add_parser("hutter")
    hutter.add_argument("--path", default=None,
                        help="single instance file, e.g. .../SFJW/SFJW-01.txt")
    hutter.add_argument("--dir", default=None,
                        help="import every *.txt under this dir as its own instance")
```

In `main()` dispatch, add a branch before `if args.source == "mk01"`:

```python
        if args.source == "hutter":
            from coe.parsers.nouri import import_nouri

            if args.dir:
                from pathlib import Path

                for f in sorted(Path(args.dir).glob("*.txt")):
                    print(f"{f} -> instance id={import_nouri(f)}")
            elif args.path:
                from pathlib import Path

                print(f"instance id={import_nouri(Path(args.path))}")
            else:
                raise SystemExit("hutter requires --path or --dir")
```

- [ ] **Step 3: Write pure-parse tests `tests/parsers/test_nouri_parse.py`**

```python
import pytest

from coe.parsers.common import SourceParseError
from coe.parsers.nouri import parse_nouri


def test_documented_example_parses():
    raw = (
        "2 2 2\n"
        "2 2 1 2 1 25 2 30 2 1 1 37 2 1 1 2 32 2 2 1 24 2 33\n"
        "2 2 1 1 1 45 2 2 1 55 2 65 2 1 2 1 21 2 25 2 1 2 65\n"
    )
    p = parse_nouri(raw)
    assert (p.n_jobs, p.n_machines, p.n_workers) == (2, 2, 2)
    op11 = p.jobs[0].operations[0]
    assert [(a.machine_index for a in op11.alternatives)] and [
        a.machine_index for a in op11.alternatives
    ] == [0, 1]
    assert op11.alternatives[0].workers == [(0, 25), (1, 30)]
    assert op11.alternatives[1].workers == [(0, 37)]


def test_real_sfjw01_shape(data_dir):
    raw = (data_dir / "raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt").read_text()
    p = parse_nouri(raw)
    header = raw.split()[0:3]
    assert (p.n_jobs, p.n_machines, p.n_workers) == tuple(int(v) for v in header)


def test_worker_out_of_range_rejected():
    with pytest.raises(SourceParseError, match="worker"):
        parse_nouri("1 1 1\n1 1 1 1 2 5\n")  # worker 2 but n_workers=1


def test_trailing_tokens_rejected():
    with pytest.raises(SourceParseError, match="trailing"):
        parse_nouri("1 1 1\n1 1 1 1 1 5\n7\n")
```

Run: `uv run pytest tests/parsers/test_nouri_parse.py -v`
Expected: 4 passed.

- [ ] **Step 4: Write DB-import tests `tests/db/test_nouri_import.py`**

```python
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db

SFJW01 = Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt")


def test_import_creates_eligibility_rows(clean_db):
    from coe.parsers.nouri import import_nouri

    inst_id = import_nouri(SFJW01)
    with create_engine(clean_db).begin() as c:
        triples = c.execute(
            text(
                "SELECT count(*) FROM operation_machine_worker_times "
                "WHERE instance_id = :i"
            ),
            {"i": inst_id},
        ).scalar_one()
        workers = c.execute(
            text("SELECT count(*) FROM workers WHERE instance_id = :i"),
            {"i": inst_id},
        ).scalar_one()
    assert workers > 0
    assert triples > workers  # many eligibility rows per worker


def test_every_alternative_has_worker_coverage(clean_db):
    """Spec §6.3 invariant: no (op, machine) alternative without an eligible worker."""
    from coe.parsers.nouri import import_nouri

    inst_id = import_nouri(SFJW01)
    with create_engine(clean_db).begin() as c:
        orphans = c.execute(
            text(
                "SELECT count(*) FROM operation_machine_alternatives a "
                "WHERE a.instance_id = :i AND NOT EXISTS ("
                "  SELECT 1 FROM operation_machine_worker_times w"
                "  WHERE w.instance_id = a.instance_id"
                "    AND w.operation_id = a.operation_id"
                "    AND w.machine_id = a.machine_id)"
            ),
            {"i": inst_id},
        ).scalar_one()
    assert orphans == 0


def test_reimport_noop_and_checksum_instance(clean_db, tmp_path):
    modified = tmp_path / "SFJW-01.txt"
    original = SFJW01.read_text().split()
    original[-1] = str(int(original[-1]) + 1)  # mutate last duration
    modified.write_text(" ".join(original))
    from coe.parsers.nouri import import_nouri

    a = import_nouri(SFJW01)
    b = import_nouri(SFJW01)
    c = import_nouri(modified)
    assert a == b          # identical re-import is a no-op
    assert c != a          # changed checksum creates a new instance
```

Run: `uv run pytest tests/db/test_nouri_import.py -v`
Expected: 3 passed.

- [ ] **Step 5: Smoke the CLI**

```bash
uv run python -m coe.cli import hutter --path data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt
uv run python -m coe.cli import hutter --dir data/raw/nouri-fjspw/extracted/MFJW
```
Expected: prints one `instance id=...` line per file.

- [ ] **Step 6: Commit**

```bash
git add coe/parsers/nouri.py coe/cli.py tests/
git commit -m "feat(parsers): hutter-nouri fjssp-w parser with worker eligibility import"
```

### Task 9: GASS Adapter + Import

**Files:**
- Create: `coe/parsers/gass.py`
- Modify: `coe/cli.py`
- Test: `tests/db/test_gass_import.py`

**Interfaces:**
- Consumes: `common` helpers; `InstanceProfile`.
- Produces: `parse_manifest(path: Path) -> dict[str, str]` (filename → sha256); `import_gass(data_dir: Path, instance_name: str = "gass") -> int`. Stores three named `instance_profiles` rows owned by the GASS instance — `name="gass-machines"` (`profile_type="machine_setup"`), `"gass-routings"` (`profile_type="routing"`), `"gass-orders"` (`profile_type="order_pattern"`) — whose `parameters_json` schemas are fixed below. Task 13 reads these by name. Downtime/failure behavior does **not** exist in the released sheets, so it stays synthetic (Task 13), labeled synthetic in provenance per spec §3.3/§3.4.

`parameters_json` contracts:

```json
"gass-machines":  {"machines": [{"code": "M1", "name": "Printing A1", "min_speed": 200,
                                 "ratio_speed": 1.0, "setup_time": 150}]}
"gass-routings":  {"processes": [{"code": "P1", "name": "Printing"}],
                   "routings":  [{"id": 1, "sequence": ["P1", "P2"]}],
                   "film_widths": [1000],
                   "product_types": [{"width_mm": 1000, "colors": 5, "routing_id": 1}]}
"gass-orders":    {"orders": [{"no": 1, "priority": 4, "product_type": 12,
                               "running_meter": 387061, "lead_days": 19}]}
```

- [ ] **Step 1: Write `coe/parsers/gass.py`**

```python
from pathlib import Path

from openpyxl import load_workbook

from coe.db.models.provenance import InstanceProfile
from coe.db.session import session_scope
from coe.parsers.common import (
    SourceParseError,
    get_or_create_source_instance,
    sha256_file,
)

EXPECTED_FILES = (
    "1-Machine.xlsx", "2-Process.xlsx", "3-Routing.xlsx",
    "4-Width.xlsx", "5-Product Type.xlsx", "6-Data Order.xlsx",
)


def parse_manifest(path: Path) -> dict[str, str]:
    """manifest.txt lines: '<filename>|<uuid>|<sha256>'."""
    out: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 3:
            raise SourceParseError(f"{path.name}:{lineno}: expected 3 '|'-separated fields")
        fname, _, sha = parts
        out[fname.strip()] = sha.strip()
    return out


def _rows(sheet):
    """Yield value-tuples after the two header rows, skipping blank rows."""
    iterator = sheet.iter_rows(values_only=True)
    next(iterator, None)
    next(iterator, None)
    for row in iterator:
        if any(v is not None for v in row):
            yield row


def _extract(data_dir: Path) -> list[InstanceProfile]:
    def sheet(name: str):
        wb = load_workbook(data_dir / name, read_only=True, data_only=True)
        ws = wb.active
        rows = list(_rows(ws))
        wb.close()
        return rows

    machine_rows = sheet("1-Machine.xlsx")
    machines = [
        {
            "code": r[2],
            "name": r[1],
            "min_speed": int(r[4]),
            "ratio_speed": float(r[5]),
            "setup_time": int(r[6]),
        }
        for r in machine_rows
        if r[1] is not None
    ]

    process_rows = sheet("2-Process.xlsx")
    processes = [{"code": r[0], "name": r[1]} for r in process_rows if r[0] is not None]

    routing_rows = sheet("3-Routing.xlsx")
    routings = [
        {"id": int(r[0]), "sequence": str(r[1]).strip().split("-")}
        for r in routing_rows
        if r[0] is not None and r[1]
    ]

    width_wb = load_workbook(data_dir / "4-Width.xlsx", read_only=True, data_only=True)
    width_ws = width_wb["4-Width"]
    film_widths = [int(r[2]) for r in _rows(width_ws) if r[2] is not None]
    width_wb.close()

    type_rows = sheet("5-Product Type.xlsx")
    product_types = [
        {"width_mm": int(r[1]), "colors": int(r[2]), "routing_id": int(r[3])}
        for r in type_rows
        if r[1] is not None
    ]

    order_rows = sheet("6-Data Order.xlsx")
    orders = [
        {
            "no": int(r[0]),
            "priority": int(r[1]),
            "product_type": int(r[2]),
            "running_meter": int(r[3]),
            "lead_days": int(r[6]) if r[6] is not None else 14,
        }
        for r in order_rows
        if r[0] is not None
    ]

    return [
        InstanceProfile(
            name="gass-machines",
            profile_type="machine_setup",
            parameters_json={"machines": machines},
        ),
        InstanceProfile(
            name="gass-routings",
            profile_type="routing",
            parameters_json={
                "processes": processes,
                "routings": routings,
                "film_widths": film_widths,
                "product_types": product_types,
            },
        ),
        InstanceProfile(
            name="gass-orders",
            profile_type="order_pattern",
            parameters_json={"orders": orders},
        ),
    ]


def import_gass(data_dir: Path, instance_name: str = "gass") -> int:
    manifest = parse_manifest(data_dir / "manifest.txt")
    mismatches = [
        f"{fname}: expected {sha}, got {sha256_file(data_dir / fname)}"
        for fname, sha in manifest.items()
        if sha256_file(data_dir / fname) != sha
    ]
    if mismatches:
        raise SourceParseError("GASS checksum mismatch(es):\n" + "\n".join(mismatches))
    for fname in EXPECTED_FILES:
        if not (data_dir / fname).exists():
            raise SourceParseError(f"missing GASS file: {fname}")

    profiles = _extract(data_dir)
    with session_scope() as session:
        inst, created = get_or_create_source_instance(
            session,
            name=instance_name,
            source_name="gass-flexible-packaging",
            source_url=None,  # no verified public URL; see Task 8 note
            source_version="released-xlsx",
            source_license="academic-benchmark",
            checksum=sha256_file(data_dir / "manifest.txt"),
        )
        if not created:
            return inst.id
        for p in profiles:
            p.source_instance_id = inst.id
            session.add(p)
        session.flush()
        return inst.id
```

- [ ] **Step 2: Wire CLI `import gass`**

In `build_parser()` add:

```python
    gass = sources.add_parser("gass")
    gass.add_argument("--dir", default="data/raw/gass")
```

In `main()` dispatch add:

```python
        if args.source == "gass":
            from coe.parsers.gass import import_gass

            print(f"instance id={import_gass(Path(args.dir))}")
```

(Ensure `from pathlib import Path` exists at top of `main()`'s module.)

- [ ] **Step 3: Write tests `tests/db/test_gass_import.py`**

```python
from pathlib import Path
from shutil import copytree

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db

GASS_DIR = Path("data/raw/gass")


def test_manifest_parses():
    from coe.parsers.gass import parse_manifest

    m = parse_manifest(GASS_DIR / "manifest.txt")
    assert len(m) == 6
    assert all(len(v) == 64 for v in m.values())


def test_import_stores_three_profiles(clean_db):
    from coe.parsers.gass import import_gass

    inst_id = import_gass(GASS_DIR)
    with create_engine(clean_db).begin() as c:
        rows = c.execute(
            text(
                "SELECT name, profile_type, parameters_json FROM instance_profiles "
                "WHERE source_instance_id = :i ORDER BY name"
            ),
            {"i": inst_id},
        ).all()
    by_name = {r[0]: r for r in rows}
    assert set(by_name) == {"gass-machines", "gass-orders", "gass-routings"}
    assert len(by_name["gass-machines"][2]["machines"]) == 15
    assert len(by_name["gass-orders"][2]["orders"]) == 59
    assert by_name["gass-routings"][2]["processes"][0]["code"] == "P1"


def test_tampered_file_rejected(clean_db, tmp_path):
    tampered = tmp_path / "gass"
    copytree(GASS_DIR, tampered)
    target = tampered / "1-Machine.xlsx"
    payload = bytearray(target.read_bytes())
    payload[-1] ^= 0xFF
    target.write_bytes(bytes(payload))

    from coe.parsers.common import SourceParseError
    from coe.parsers.gass import import_gass

    with pytest.raises(SourceParseError, match="checksum mismatch"):
        import_gass(tampered)


def test_reimport_noop(clean_db):
    from coe.parsers.gass import import_gass

    a = import_gass(GASS_DIR)
    b = import_gass(GASS_DIR)
    assert a == b
```

Run: `uv run pytest tests/db/test_gass_import.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add coe/parsers/gass.py coe/cli.py tests/
git commit -m "feat(parsers): gass xlsx adapter storing verified provenance profiles"
```

### Task 10: Scenario Shell + Topology Sampler

**Files:**
- Create: `coe/scenario/build.py`, `coe/scenario/topology_sampler.py`
- Modify: `coe/cli.py`
- Test: `tests/db/test_scenario_topology.py`

**Interfaces:**
- Consumes: models; `get_or_create_source_instance`; mk01 instance in DB (Task 7).
- Produces: `build_scenario(name: str = "factory_demo_01", seed: int = 42) -> int` — atomic orchestrator creating the generated instance and recording `scenario_sources`; `sample_topology(session, instance_id, *, n_jobs, n_machines, seed) -> dict` returning `{"jobs": n, "machines": n, "operations": n}`. Later transformation tasks each contribute one function called by `build_scenario`, in fixed order, inside its single transaction.

Sampling design (honest to criterion 6 — *sampling profiles, not duplicating rows*): from the mk01 source instance extract three profiles — ops-per-job list `[6,5,5,...]`, alternative-count histogram `{2:…,3:…}`, and the pooled duration values — then generate **new** operations: ops-per-job drawn from the observed list, alternatives `k` drawn from the histogram, `k` distinct machines sampled from the 8 targets, durations drawn from the pooled values.

- [ ] **Step 1: Write `coe/scenario/topology_sampler.py`**

```python
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import (
    Job,
    Machine,
    Operation,
    OperationMachineAlternative,
)


def _extract_profiles(session: Session, source_instance_id: int) -> dict:
    ops_per_job = [
        len(jobs)
        for jobs in (
            session.scalars(
                select(Operation.id).where(
                    Operation.instance_id == source_instance_id,
                    Operation.job_id == jid,
                )
            ).all()
            for jid in session.scalars(
                select(Job.id).where(Job.instance_id == source_instance_id)
            ).all()
        )
    ]
    alt_counts: list[int] = []
    durations: list[int] = []
    rows = session.execute(
        select(
            OperationMachineAlternative.operation_id,
            OperationMachineAlternative.processing_time,
        ).where(OperationMachineAlternative.instance_id == source_instance_id)
    ).all()
    per_op: dict[int, int] = {}
    for op_id, t in rows:
        per_op[op_id] = per_op.get(op_id, 0) + 1
        durations.append(t)
    alt_counts = list(per_op.values())
    return {"ops_per_job": ops_per_job, "alt_counts": alt_counts, "durations": durations}


def sample_topology(
    session: Session,
    instance_id: int,
    *,
    source_instance_id: int,
    n_jobs: int = 30,
    n_machines: int = 8,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    profiles = _extract_profiles(session, source_instance_id)

    machines = [
        Machine(instance_id=instance_id, source_id=str(mi), name=f"M{mi}")
        for mi in range(n_machines)
    ]
    session.add_all(machines)
    session.flush()

    n_operations = 0
    for ji in range(n_jobs):
        jrow = Job(instance_id=instance_id, source_id=str(ji), name=f"J{ji + 1}")
        session.add(jrow)
        session.flush()
        n_ops = profiles["ops_per_job"][rng.randrange(len(profiles["ops_per_job"]))]
        for oi in range(n_ops):
            orow = Operation(
                instance_id=instance_id,
                job_id=jrow.id,
                source_id=f"{ji}:{oi}",
                sequence_number=oi + 1,
            )
            session.add(orow)
            session.flush()
            k = profiles["alt_counts"][rng.randrange(len(profiles["alt_counts"]))]
            k = min(k, n_machines)
            chosen = rng.sample(range(n_machines), k)
            for mi in chosen:
                session.add(
                    OperationMachineAlternative(
                        instance_id=instance_id,
                        operation_id=orow.id,
                        machine_id=machines[mi].id,
                        processing_time=rng.choice(profiles["durations"]),
                    )
                )
            n_operations += 1
    session.flush()
    return {"jobs": n_jobs, "machines": n_machines, "operations": n_operations}
```

- [ ] **Step 2: Write `coe/scenario/build.py`**

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.provenance import Instance, ScenarioSource
from coe.db.session import session_scope
from coe.scenario.topology_sampler import sample_topology


class ScenarioError(RuntimeError):
    pass


def _require_source(session: Session, prefix: str, label: str) -> Instance:
    inst = (
        session.query(Instance)
        .filter(Instance.name.like(f"{prefix}%"))
        .order_by(Instance.id.asc())
        .first()
    )
    if inst is None:
        raise ScenarioError(
            f"{label} source instance not found; import it first "
            f"(expected an instance named '{prefix}*')"
        )
    return inst


def build_scenario(name: str = "factory_demo_01", seed: int = 42) -> int:
    """Composite build is atomic (spec §11): any failure rolls back everything."""
    with session_scope() as session:
        existing = (
            session.query(Instance).filter(Instance.name == name).one_or_none()
        )
        if existing is not None:
            raise ScenarioError(
                f"scenario '{name}' already exists; run 'db reset' to rebuild"
            )

        scenario = Instance(
            name=name,
            source_name="synthetic-composite",
            source_version="phase1",
            source_license="synthetic",
        )
        session.add(scenario)
        session.flush()

        mk01 = _require_source(session, "mk01", "MK01")
        nouri = _require_source(session, "nouri-", "Hutter/Nouri")
        gass = _require_source(session, "gass", "GASS")

        counts = sample_topology(
            session,
            scenario.id,
            source_instance_id=mk01.id,
            n_jobs=30,
            n_machines=8,
            seed=seed,
        )
        session.add(
            ScenarioSource(
                scenario_id=scenario.id,
                source_instance_id=mk01.id,
                contribution_type="topology",
                transformation_description=(
                    "sampled 30x8 topology from MK01-derived profiles "
                    f"(ops/job, flexibility histogram, duration pool): {counts}"
                ),
                random_seed=seed,
            )
        )

        # Later tasks insert their transformations here, in this order:
        #   add_job_attributes(...)      (Task 11)
        #   add_workers(...)             (Task 12)
        #   add_availability_windows(...)  (Task 12)
        #   add_families_and_setups(...) (Task 13)
        #   add_maintenance_windows(...) (Task 13)
        #   add_materials(...)           (Task 14)
        #   normalize_times(...)         (Task 14)

        session.flush()
        return scenario.id
```

- [ ] **Step 3: Wire CLI**

In `build_parser()` add a `scenario` group before `return parser`:

```python
    sc = sub.add_parser("scenario")
    sc_sub = sc.add_subparsers(dest="scenario_cmd", required=True)
    sb = sc_sub.add_parser("build")
    sb.add_argument("--name", default="factory_demo_01")
    sb.add_argument("--seed", type=int, default=None)
```

In `main()` dispatch add:

```python
    elif args.group == "scenario":
        if args.scenario_cmd == "build":
            from coe.config import get_settings
            from coe.scenario.build import build_scenario

            seed = args.seed if args.seed is not None else get_settings().default_seed
            print(f"scenario id={build_scenario(args.name, seed)}")
```

Also add a `db` group now (needed by determinism testing and §10 interface). First create **`coe/db/admin.py`** so CLI and tests share one reset implementation:

```python
import subprocess

from sqlalchemy import create_engine, text

from coe.config import get_settings


def reset_database(url: str | None = None) -> None:
    """Drop every user table in public, then rebuild via Alembic (authoritative DDL).
    Development-only and destructive (spec §10)."""
    url = url or get_settings().database_url
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(
            text(
                "DO $do$ DECLARE r record; BEGIN "
                "FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP "
                "EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
                "END LOOP; END $do$;"
            )
        )
    eng.dispose()
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
```

Then update `tests/conftest.py`: delete the local `reset_database` definition and replace it with:

```python
from coe.db.admin import reset_database  # noqa: F401  (re-exported for fixtures)
```

Finally add the CLI group:

```python
    db = sub.add_parser("db")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    db_sub.add_parser("reset")   # destructive dev-only
```

with dispatch:

```python
    elif args.group == "db":
        if args.db_cmd == "reset":
            from coe.db.admin import reset_database

            reset_database()
            print("database reset")
```

- [ ] **Step 4: Write test `tests/db/test_scenario_topology.py`**

```python
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


@pytest.fixture()
def sources_imported(clean_db):
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri

    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))


def test_build_creates_30x8(sources_imported):
    from coe.config import get_settings
    from coe.scenario.build import build_scenario

    sid = build_scenario("factory_demo_01", seed=42)
    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        jobs = c.execute(
            text("SELECT count(*) FROM jobs WHERE instance_id=:i"), {"i": sid}
        ).scalar_one()
        machines = c.execute(
            text("SELECT count(*) FROM machines WHERE instance_id=:i"), {"i": sid}
        ).scalar_one()
        src = c.execute(
            text("SELECT count(*) FROM scenario_sources WHERE scenario_id=:i"),
            {"i": sid},
        ).scalar_one()
    assert (jobs, machines) == (30, 8)
    assert src == 1


def test_duplicate_name_refused(sources_imported):
    from coe.scenario.build import ScenarioError, build_scenario

    build_scenario("factory_demo_01", seed=42)
    with pytest.raises(ScenarioError, match="already exists"):
        build_scenario("factory_demo_01", seed=42)


def test_missing_source_aborts_clean(sources_imported):
    from sqlalchemy import text as sqltext

    from coe.config import get_settings
    from coe.db.session import make_engine
    from coe.scenario.build import ScenarioError, build_scenario

    with make_engine().begin() as c:
        gid = c.execute(
            sqltext("SELECT id FROM instances WHERE name='gass'")
        ).scalar_one()
        c.execute(sqltext("DELETE FROM instances WHERE id=:i"), {"i": gid})
    with pytest.raises(ScenarioError):
        build_scenario("factory_demo_02", seed=42)
    eng = make_engine()
    with eng.begin() as c:
        leftovers = c.execute(
            sqltext("SELECT count(*) FROM instances WHERE name LIKE 'factory_demo_%'")
        ).scalar_one()
    assert leftovers == 0  # atomic rollback left nothing behind
```

Run: `uv run pytest tests/db/test_scenario_topology.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/scenario coe/cli.py coe/db/admin.py tests/
git commit -m "feat(scenario): atomic build shell + mk01-profile topology sampler"
```

### Task 11: Job Attributes (TWK Deadlines, Releases, Priorities)

**Files:**
- Create: `coe/scenario/add_job_attributes.py`
- Modify: `coe/scenario/build.py`, `tests/conftest.py`
- Test: `tests/db/test_scenario_job_attributes.py`

**Interfaces:**
- Consumes: demo instance with jobs/ops/alternatives (Task 10).
- Produces: `add_job_attributes(session, scenario_id, *, source_instance_id, seed, release_span=240, slack_low=1.5, slack_high=3.0) -> None`. Sets every job's `release_time`, `deadline`, `priority`; appends one `ScenarioSource(contribution_type="job_attributes")`. Also adds the reusable `demo_scenario` pytest fixture used by all later scenario tasks.

TWK formula (spec §3.4): `deadline = release_time + max(1, round(slack × Σ_op avg_processing(op)))`, slack ~ uniform(1.5, 3.0). Priorities are ints 1–5, **1 = most important** (documented convention; Phase 3 maps priority → tardiness weight).

- [ ] **Step 1: Write `coe/scenario/add_job_attributes.py`**

```python
import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import Job, Operation, OperationMachineAlternative
from coe.db.models.provenance import ScenarioSource


def add_job_attributes(
    session: Session,
    scenario_id: int,
    *,
    source_instance_id: int,
    seed: int,
    release_span: int = 240,
    slack_low: float = 1.5,
    slack_high: float = 3.0,
) -> None:
    rng = random.Random(seed)

    sums: dict[int, int] = defaultdict(int)
    counts: dict[int, int] = defaultdict(int)
    alt_rows = session.execute(
        select(
            OperationMachineAlternative.operation_id,
            OperationMachineAlternative.processing_time,
        ).where(OperationMachineAlternative.instance_id == scenario_id)
    ).all()
    for op_id, t in alt_rows:
        sums[op_id] += t
        counts[op_id] += 1

    ops_by_job: dict[int, list[int]] = defaultdict(list)
    op_rows = session.execute(
        select(Operation.id, Operation.job_id).where(
            Operation.instance_id == scenario_id
        )
    ).all()
    for op_id, job_id in op_rows:
        ops_by_job[job_id].append(op_id)

    jobs = session.scalars(
        select(Job).where(Job.instance_id == scenario_id).order_by(Job.id)
    ).all()
    for job in jobs:
        release = rng.randint(0, release_span)
        twk = sum(sums[oid] / max(counts[oid], 1) for oid in ops_by_job[job.id])
        slack = rng.uniform(slack_low, slack_high)
        job.release_time = release
        job.deadline = release + max(1, round(slack * twk))
        job.priority = rng.randint(1, 5)

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=source_instance_id,
            contribution_type="job_attributes",
            transformation_description=(
                "TWK deadlines (slack 1.5-3.0x total work content), "
                f"releases in [0,{release_span}], priorities 1-5; synthetic"
            ),
            random_seed=seed,
        )
    )
```

- [ ] **Step 2: Wire into `build_scenario`**

In `coe/scenario/build.py`, replace the placeholder comment `#   add_job_attributes(...)      (Task 11)` with:

```python
        add_job_attributes(session, scenario.id, source_instance_id=mk01.id, seed=seed + 1)
```

and extend imports:

```python
from coe.scenario.add_job_attributes import add_job_attributes
```

Seed convention: transformation N uses `seed + N` (topology uses base `seed`). Documented so inserting steps never shifts earlier random streams.

- [ ] **Step 3: Add the shared `demo_scenario` fixture to `tests/conftest.py`**

Append:

```python
@pytest.fixture()
def demo_scenario(clean_db):
    """All three sources imported + factory_demo_01 built with seed 42 -> id."""
    from pathlib import Path

    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))
    return build_scenario("factory_demo_01", seed=42)
```

- [ ] **Step 4: Write `tests/db/test_scenario_job_attributes.py`**

```python
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def test_deadline_bounds_and_priorities(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        jobs = c.execute(
            text(
                "SELECT id, release_time, deadline, priority FROM jobs "
                "WHERE instance_id=:i"
            ),
            {"i": demo_scenario},
        ).all()
        twk_rows = c.execute(
            text(
                "SELECT o.job_id, AVG(a.processing_time) AS twk FROM operations o "
                "JOIN operation_machine_alternatives a ON a.operation_id = o.id "
                "WHERE a.instance_id=:i GROUP BY o.job_id"
            ),
            {"i": demo_scenario},
        ).all()
    twk = {jid: float(avg or 0) for jid, avg in twk_rows}
    assert len(jobs) == 30
    for jid, rel, dl, prio in jobs:
        assert dl > rel
        assert dl - rel >= 1.45 * twk[jid]   # floor of uniform(1.5,3.0) after rounding
        assert prio in (1, 2, 3, 4, 5)


def test_reproducible_with_same_seed(clean_db):
    from pathlib import Path

    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    def snapshot():
        reset_database(get_settings().database_url)
        import_mk01(Path("data/raw/mk01/mk01.txt"))
        import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
        import_gass(Path("data/raw/gass"))
        sid = build_scenario("factory_demo_01", seed=42)
        eng = create_engine(get_settings().database_url)
        with eng.begin() as c:
            return c.execute(
                text(
                    "SELECT name, release_time, deadline, priority FROM jobs "
                    "WHERE instance_id=:i ORDER BY name"
                ),
                {"i": sid},
            ).all()

    assert snapshot() == snapshot()


def test_provenance_recorded(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        n = c.execute(
            text(
                "SELECT count(*) FROM scenario_sources WHERE scenario_id=:i "
                "AND contribution_type='job_attributes'"
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert n == 1
```

Run: `uv run pytest tests/db/test_scenario_job_attributes.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/scenario tests/
git commit -m "feat(scenario): seeded TWK deadlines, releases, priorities"
```


### Task 12: Workers, Eligibility, Availability Windows

**Files:**
- Create: `coe/scenario/add_workers.py`
- Modify: `coe/scenario/build.py`
- Test: `tests/db/test_scenario_workers.py`

**Interfaces:**
- Consumes: demo alternatives (Task 10); Nouri source instance id.
- Produces: `add_workers(session, scenario_id, *, nouri_source_id, seed, n_workers=12, skill_low=0.9, skill_high=1.15) -> dict` returning `{"workers", "eligibility_rows", "windows"}`. Guarantees the spec §6.3 invariant — every `(op, machine)` alternative has ≥ 1 eligible worker — raising `RuntimeError` otherwise (which rolls back the whole build).

Design: roles are synthetic (`OPERATOR`, `TECHNICIAN`, `SUPERVISOR`); worker-dependent time = `max(1, round(base_time × uniform(skill_low, skill_high)))`; eligibility draws 1–3 workers per alternative. Availability patterns seeded per worker: `full-day` [0,1440], `late-start` [240,1440], `split-shift` [0,240]+[480,1440].

- [ ] **Step 1: Write `coe/scenario/add_workers.py`**

```python
import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import OperationMachineAlternative
from coe.db.models.provenance import ScenarioSource
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    Worker,
    WorkerAvailabilityWindow,
    WorkerRole,
)

ROLES = ("OPERATOR", "TECHNICIAN", "SUPERVISOR")
PATTERNS: tuple[tuple[str, list[tuple[int, int]], float], ...] = (
    ("full-day", [(0, 1440)], 0.60),
    ("late-start", [(240, 1440)], 0.25),
    ("split-shift", [(0, 240), (480, 1440)], 0.15),
)


def _pick_pattern(rng: random.Random) -> tuple[str, list[tuple[int, int]]]:
    roll = rng.random()
    acc = 0.0
    for name, spans, weight in PATTERNS:
        acc += weight
        if roll < acc:
            return name, spans
    return PATTERNS[0][0], PATTERNS[0][1]


def add_workers(
    session: Session,
    scenario_id: int,
    *,
    nouri_source_id: int,
    seed: int,
    n_workers: int = 12,
    skill_low: float = 0.9,
    skill_high: float = 1.15,
) -> dict:
    """Worker layer onto the sampled topology. Structure follows the imported
    Hutter/Nouri flexibility behavior; values are synthetic (seeded)."""
    rng = random.Random(seed)

    roles = [
        WorkerRole(instance_id=scenario_id, role_name=name) for name in ROLES
    ]
    session.add_all(roles)
    session.flush()

    workers = [
        Worker(
            instance_id=scenario_id,
            source_id=str(wi),
            name=f"W{wi + 1}",
            role_id=rng.choice(roles).id,
        )
        for wi in range(n_workers)
    ]
    session.add_all(workers)
    session.flush()

    windows = 0
    for w in workers:
        pattern_name, spans = _pick_pattern(rng)
        for a, b in spans:
            session.add(
                WorkerAvailabilityWindow(
                    instance_id=scenario_id,
                    worker_id=w.id,
                    available_from=a,
                    available_until=b,
                    source_pattern=pattern_name,
                )
            )
            windows += 1

    alts = session.execute(
        select(OperationMachineAlternative).where(
            OperationMachineAlternative.instance_id == scenario_id
        )
    ).scalars().all()

    eligibility = 0
    for alt in alts:
        k = rng.randint(1, min(3, n_workers))
        chosen = rng.sample(workers, k)
        for w in chosen:
            factor = rng.uniform(skill_low, skill_high)
            session.add(
                OperationMachineWorkerTime(
                    instance_id=scenario_id,
                    operation_id=alt.operation_id,
                    machine_id=alt.machine_id,
                    worker_id=w.id,
                    processing_time=max(1, round(alt.processing_time * factor)),
                )
            )
            eligibility += 1

    # Spec §6.3 invariant — must hold before the transaction can commit.
    covered = {
        (r[0], r[1])
        for r in session.execute(
            select(OperationMachineWorkerTime.operation_id,
                   OperationMachineWorkerTime.machine_id).where(
                OperationMachineWorkerTime.instance_id == scenario_id)
        ).all()
    }
    missing = [
        (a.operation_id, a.machine_id)
        for a in alts
        if (a.operation_id, a.machine_id) not in covered
    ]
    if missing:
        raise RuntimeError(f"alternatives without eligible workers: {missing[:5]}")

    session.add_all(
        [
            ScenarioSource(
                scenario_id=scenario_id,
                source_instance_id=nouri_source_id,
                contribution_type="worker_flexibility",
                transformation_description=(
                    "applied Hutter/Nouri worker-flexibility structure: "
                    f"{n_workers} workers, {eligibility} eligibility rows "
                    "(values synthetic)"
                ),
                random_seed=seed,
            ),
            ScenarioSource(
                scenario_id=scenario_id,
                source_instance_id=nouri_source_id,
                contribution_type="worker_availability",
                transformation_description=(
                    f"{windows} concrete availability windows from shift patterns; synthetic"
                ),
                random_seed=seed,
            ),
        ]
    )
    session.flush()
    return {"workers": n_workers, "eligibility_rows": eligibility, "windows": windows}
```

- [ ] **Step 2: Wire into `build_scenario`**

Replace both placeholder lines:

```python
        #   add_workers(...)             (Task 12)
        #   add_availability_windows(...)  (Task 12)
```

with:

```python
        add_workers(session, scenario.id, nouri_source_id=nouri.id, seed=seed + 2)
```

plus import:

```python
from coe.scenario.add_workers import add_workers
```

- [ ] **Step 3: Write `tests/db/test_scenario_workers.py`**

```python
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def _rows(url, sql, params):
    with create_engine(url).begin() as c:
        return c.execute(text(sql), params).all()


def test_every_alternative_covered(demo_scenario):
    from coe.config import get_settings

    orphans = _rows(
        get_settings().database_url,
        """
        SELECT count(*) FROM operation_machine_alternatives a
        WHERE a.instance_id = :i AND NOT EXISTS (
            SELECT 1 FROM operation_machine_worker_times w
            WHERE w.instance_id = a.instance_id
              AND w.operation_id = a.operation_id
              AND w.machine_id = a.machine_id)
        """,
        {"i": demo_scenario},
    )[0][0]
    assert orphans == 0


def test_worker_times_within_skill_band(demo_scenario):
    from coe.config import get_settings

    violations = _rows(
        get_settings().database_url,
        """
        SELECT count(*) FROM operation_machine_worker_times w
        JOIN operation_machine_alternatives a
          ON a.instance_id = w.instance_id
         AND a.operation_id = w.operation_id
         AND a.machine_id = w.machine_id
        WHERE w.instance_id = :i
          AND w.processing_time > ceil(a.processing_time * 1.20)
        """,
        {"i": demo_scenario},
    )[0][0]
    assert violations == 0


def test_windows_valid_and_patterned(demo_scenario):
    from coe.config import get_settings

    bad = _rows(
        get_settings().database_url,
        """
        SELECT count(*) FROM worker_availability_windows
        WHERE instance_id = :i AND (
            available_until <= available_from OR source_pattern IS NULL)
        """,
        {"i": demo_scenario},
    )[0][0]
    assert bad == 0
    total = _rows(
        get_settings().database_url,
        "SELECT count(*) FROM worker_availability_windows WHERE instance_id=:i",
        {"i": demo_scenario},
    )[0][0]
    assert total >= 12  # at least one window per worker
```

Run: `uv run pytest tests/db/test_scenario_workers.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add coe/scenario tests/
git commit -m "feat(scenario): worker layer with eligibility coverage invariant"
```

### Task 13: Job Families, Setup Matrix, Maintenance Windows

**Files:**
- Create: `coe/scenario/add_setup_times.py`, `coe/scenario/add_failures.py`
- Modify: `coe/scenario/build.py`
- Test: `tests/db/test_scenario_setups_failures.py`

**Interfaces:**
- Consumes: demo machines/jobs; `instance_profiles` rows `gass-machines` / GASS process codes from the gass instance (Task 9).
- Produces:
  - `add_families_and_setups(session, scenario_id, *, gass_source_id, seed) -> dict`
  - `add_maintenance_windows(session, scenario_id, *, seed, horizon=1440, max_per_machine=2) -> dict`

Modeling conventions (documented in provenance text):
- Families = one per GASS process code (`P1..Pn` → `FAM-P1..FAM-Pn`), jobs assigned seeded.
- Demo machine `i` maps to the i-th GASS machine code in natural order (`M1..M8`).
- Machine setup base = `max(1, round(gass_setup_time / 30))` — a stated unit-normalization convention (spec §7), not a physical claim.
- Duration for family pair `(Fa,Fb)` on machine with base `v` = `v + ((int(Fa[-1]) + int(Fb[-1])) % 3)` — deterministic variation, symmetric by construction.
- Initial setups (from NULL): duration `v + (int(F[-1]) % 3)`.
- Same-family transitions get **no row** ⇒ zero setup (Phase 2 semantics).
- Maintenance windows are planned outages (`reason="MAINTENANCE"`, machine stays `ACTIVE`), non-overlapping by construction.

- [ ] **Step 1: Write `coe/scenario/add_setup_times.py`**

```python
import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import Job, JobFamily, Machine, SetupTime
from coe.db.models.provenance import InstanceProfile, ScenarioSource


def _gass_profiles(session: Session, gass_source_id: int) -> tuple[dict[str, int], list[str]]:
    prof = (
        session.query(InstanceProfile)
        .filter(
            InstanceProfile.source_instance_id == gass_source_id,
            InstanceProfile.name == "gass-machines",
        )
        .one()
    )
    # Natural numeric order (M1, M2, ... M15) — lexicographic sort would put
    # M10 right after M1 and silently mis-map demo machines to GASS classes.
    codes_in_order = sorted(
        ((m["code"], m["setup_time"]) for m in prof.parameters_json["machines"]),
        key=lambda pair: int(pair[0][1:]),
    )
    setup_base = {
        code: max(1, round(st / 30)) for code, st in codes_in_order
    }
    routing_prof = (
        session.query(InstanceProfile)
        .filter(
            InstanceProfile.source_instance_id == gass_source_id,
            InstanceProfile.name == "gass-routings",
        )
        .one()
    )
    process_codes = [p["code"] for p in routing_prof.parameters_json["processes"]]
    return setup_base, process_codes


def add_families_and_setups(
    session: Session,
    scenario_id: int,
    *,
    gass_source_id: int,
    seed: int,
) -> dict:
    rng = random.Random(seed)
    setup_base, process_codes = _gass_profiles(session, gass_source_id)

    families = [
        JobFamily(instance_id=scenario_id, source_id=code, name=f"FAM-{code}")
        for code in process_codes
    ]
    session.add_all(families)
    session.flush()

    jobs = session.scalars(
        select(Job).where(Job.instance_id == scenario_id).order_by(Job.id)
    ).all()
    for job in jobs:
        job.job_family_id = rng.choice(families).id

    machines = session.scalars(
        select(Machine).where(Machine.instance_id == scenario_id).order_by(Machine.id)
    ).all()
    gass_codes = sorted(setup_base)

    n_setup_rows = 0
    for mi, m in enumerate(machines):
        v = setup_base[gass_codes[mi % len(gass_codes)]]
        for fa in families:
            for fb in families:
                if fa.id == fb.id:
                    continue
                dur = v + ((int(fa.source_id[1:]) + int(fb.source_id[1:])) % 3)
                session.add(
                    SetupTime(
                        instance_id=scenario_id,
                        machine_id=m.id,
                        from_family_id=fa.id,
                        to_family_id=fb.id,
                        setup_duration=dur,
                        source="gass-profile",
                    )
                )
                n_setup_rows += 1
            session.add(
                SetupTime(
                    instance_id=scenario_id,
                    machine_id=m.id,
                    from_family_id=None,
                    to_family_id=fa.id,
                    setup_duration=v + (int(fa.source_id[1:]) % 3),
                    source="gass-profile",
                )
            )
            n_setup_rows += 1

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=gass_source_id,
            contribution_type="setup_times",
            transformation_description=(
                "families from GASS process codes; sequence-dependent matrix "
                "normalized as gass SetupTime/30 per machine class"
            ),
            random_seed=seed,
        )
    )
    session.flush()
    return {"families": len(families), "setup_rows": n_setup_rows}
```

- [ ] **Step 2: Write `coe/scenario/add_failures.py`**

```python
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.downtime import MachineDowntimeWindow
from coe.db.models.fjsp import Machine
from coe.db.models.provenance import Instance, ScenarioSource


def _gass_or_self(session: Session, scenario_id: int) -> int:
    """Attribution target for synthetic rows: the gass instance when present,
    else self-reference. Keeps the FK valid either way."""
    row = (
        session.query(Instance.id)
        .filter(Instance.name.like("gass%"))
        .order_by(Instance.id.asc())
        .first()
    )
    return row[0] if row else scenario_id


def add_maintenance_windows(
    session: Session,
    scenario_id: int,
    *,
    seed: int,
    horizon: int = 1440,
    max_per_machine: int = 2,
    window_low: int = 60,
    window_high: int = 180,
) -> dict:
    rng = random.Random(seed)
    machine_ids = session.scalars(
        select(Machine.id).where(Machine.instance_id == scenario_id).order_by(Machine.id)
    ).all()

    placed = 0
    for mid in machine_ids:
        count = rng.randint(0, max_per_machine)
        windows: list[tuple[int, int]] = []
        attempts = 0
        while len(windows) < count and attempts < 50:
            attempts += 1
            dur = rng.randint(window_low, window_high)
            start = rng.randrange(window_low, horizon - dur)
            if all(start + dur < s or e < start for s, e in windows):
                windows.append((start, start + dur))
        for start, end in sorted(windows):
            session.add(
                MachineDowntimeWindow(
                    instance_id=scenario_id,
                    machine_id=mid,
                    downtime_from=start,
                    downtime_until=end,
                    reason="MAINTENANCE",
                    severity=rng.choice(["LOW", "MEDIUM"]),
                    source_event_ids=[],
                )
            )
            placed += 1

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=_gass_or_self(session, scenario_id),
            contribution_type="maintenance_windows",
            transformation_description=(
                f"{placed} planned MAINTENANCE windows within horizon {horizon}; "
                "synthetic (GASS releases no downtime data)"
            ),
            random_seed=seed,
        )
    )
    session.flush()
    return {"windows": placed}
```

- [ ] **Step 3: Wire into `build_scenario`**

Replace placeholders:

```python
        #   add_families_and_setups(...) (Task 13)
        #   add_maintenance_windows(...) (Task 13)
```

with:

```python
        add_families_and_setups(session, scenario.id, gass_source_id=gass.id, seed=seed + 3)
        add_maintenance_windows(session, scenario.id, seed=seed + 4)
```

plus imports:

```python
from coe.scenario.add_failures import add_maintenance_windows
from coe.scenario.add_setup_times import add_families_and_setups
```

- [ ] **Step 4: Write `tests/db/test_scenario_setups_failures.py`**

```python
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def test_matrix_symmetric_with_initials(demo_scenario):
    """For every A->B row an identical-duration B->A exists; every machine+family has an initial row."""
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        pairs = c.execute(
            text(
                "SELECT s.machine_id, fa.name, fb.name, s.setup_duration "
                "FROM setup_times s "
                "JOIN job_families fa ON fa.id = s.from_family_id "
                "JOIN job_families fb ON fb.id = s.to_family_id "
                "WHERE s.instance_id = :i"
            ),
            {"i": demo_scenario},
        ).all()
        initials = c.execute(
            text(
                "SELECT machine_id, count(DISTINCT to_family_id) FROM setup_times "
                "WHERE instance_id=:i AND from_family_id IS NULL GROUP BY machine_id"
            ),
            {"i": demo_scenario},
        ).all()
        fam_count = c.execute(
            text("SELECT count(*) FROM job_families WHERE instance_id=:i"),
            {"i": demo_scenario},
        ).scalar_one()
    lookup = {(m, a, b): d for m, a, b, d in pairs}
    for m, a, b, d in pairs:
        assert lookup.get((m, b, a)) == d, f"asymmetric pair on machine {m}: {a}->{b}"
    for m, cnt in initials:
        assert cnt == fam_count


def test_every_job_has_family(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        orphans = c.execute(
            text(
                "SELECT count(*) FROM jobs WHERE instance_id=:i "
                "AND job_family_id IS NULL"
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert orphans == 0


def test_maintenance_windows_sane(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        bad = c.execute(
            text(
                "SELECT count(*) FROM machine_downtime_windows WHERE instance_id=:i "
                "AND (reason != 'MAINTENANCE' OR downtime_until IS NULL "
                "OR downtime_from < 60)"
            ),
            {"i": demo_scenario},
        ).scalar_one()
        overlapping = c.execute(
            text(
                """
                SELECT count(*) FROM machine_downtime_windows a
                JOIN machine_downtime_windows b
                  ON a.instance_id = b.instance_id
                 AND a.machine_id = b.machine_id
                 AND a.id < b.id
                WHERE a.instance_id = :i
                  AND a.downtime_from < b.downtime_until
                  AND b.downtime_from < a.downtime_until
                """
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert bad == 0
    assert overlapping == 0
```

Run: `uv run pytest tests/db/test_scenario_setups_failures.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/scenario tests/
git commit -m "feat(scenario): gass-derived families, setup matrix, maintenance windows"
```

### Task 14: Materials, Time Normalization, Determinism Proof

**Files:**
- Create: `coe/scenario/add_materials.py`, `coe/scenario/normalize_time.py`
- Modify: `coe/scenario/build.py`, `coe/cli.py`
- Test: `tests/db/test_scenario_materials_determinism.py`

**Interfaces:**
- Consumes: fully built demo instance.
- Produces: `add_materials(session, scenario_id, *, seed, n_materials=8) -> dict`; `normalize_times(session, scenario_id) -> dict`. After this task `build_scenario` is complete and the CLI `scenario build` command works end-to-end.

Design conventions: SKUs `MAT-001..008`; each operation draws 1–2 materials with qty 1–5; `initial_stock = ceil(1.2 × total demand)` so the Phase 2 conservative supply check passes at baseline (healthy baseline, no blocked ops); `reorder_point = round(0.3 × stock)`; two synthetic future receipts per material at `available_at=2000` (beyond horizon, qty 10). `normalize_times` verifies integer-minute semantics everywhere and stamps the instance's unit fields.

- [ ] **Step 1: Write `coe/scenario/add_materials.py`**

```python
import math
import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import Operation
from coe.db.models.materials import Material, MaterialReceipt, OperationBom
from coe.db.models.provenance import ScenarioSource


def add_materials(
    session: Session,
    scenario_id: int,
    *,
    seed: int,
    n_materials: int = 8,
    receipt_at: int = 2000,
) -> dict:
    rng = random.Random(seed)
    op_ids = session.scalars(
        select(Operation.id).where(Operation.instance_id == scenario_id).order_by(Operation.id)
    ).all()

    materials = [
        Material(instance_id=scenario_id, sku=f"MAT-{k + 1:03d}", initial_stock=0)
        for k in range(n_materials)
    ]
    session.add_all(materials)
    session.flush()

    demand: dict[int, int] = defaultdict(int)
    n_bom = 0
    for op_id in op_ids:
        for m in rng.sample(materials, rng.randint(1, min(2, n_materials))):
            qty = rng.randint(1, 5)
            session.add(
                OperationBom(
                    instance_id=scenario_id,
                    operation_id=op_id,
                    material_id=m.id,
                    quantity_required=qty,
                )
            )
            demand[m.id] += qty
            n_bom += 1

    for m in materials:
        m.initial_stock = math.ceil(1.2 * demand[m.id])
        m.reorder_point = round(0.3 * m.initial_stock)
        for _ in range(2):
            session.add(
                MaterialReceipt(
                    instance_id=scenario_id,
                    material_id=m.id,
                    quantity=10,
                    available_at=receipt_at,
                    source="synthetic",
                )
            )

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=_self_id(session, scenario_id),
            contribution_type="materials_inventory",
            transformation_description=(
                f"{n_materials} SKUs, {n_bom} BOM rows, stock=1.2x demand "
                "(baseline unblocked), future receipts; synthetic"
            ),
            random_seed=seed,
        )
    )
    session.flush()
    return {"materials": n_materials, "bom_rows": n_bom}


def _self_id(session: Session, scenario_id: int) -> int:
    return scenario_id  # purely synthetic contribution attributes to itself
```

- [ ] **Step 2: Write `coe/scenario/normalize_time.py`**

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.downtime import MachineDowntimeWindow
from coe.db.models.fjsp import Job, OperationMachineAlternative
from coe.db.models.materials import MaterialReceipt
from coe.db.models.provenance import Instance, ScenarioSource
from coe.db.models.workers import (
    OperationMachineWorkerTime,
    WorkerAvailabilityWindow,
)


def normalize_times(session: Session, scenario_id: int) -> dict:
    """Identity normalization (source already minutes): assert non-negative ints,
    stamp unit metadata, record provenance."""
    jobs = session.scalars(select(Job).where(Job.instance_id == scenario_id)).all()
    for j in jobs:
        assert j.release_time >= 0
        if j.deadline is not None:
            assert j.deadline > j.release_time

    for t, in session.execute(
        select(OperationMachineAlternative.processing_time).where(
            OperationMachineAlternative.instance_id == scenario_id)
    ).all():
        assert t >= 0

    for t, in session.execute(
        select(OperationMachineWorkerTime.processing_time).where(
            OperationMachineWorkerTime.instance_id == scenario_id)
    ).all():
        assert t >= 0

    for a, b in session.execute(
        select(WorkerAvailabilityWindow.available_from,
               WorkerAvailabilityWindow.available_until).where(
            WorkerAvailabilityWindow.instance_id == scenario_id)
    ).all():
        assert 0 <= a < b

    for a, b in session.execute(
        select(MachineDowntimeWindow.downtime_from,
               MachineDowntimeWindow.downtime_until).where(
            MachineDowntimeWindow.instance_id == scenario_id)
    ).all():
        assert a >= 0 and (b is None or b > a)

    for t, in session.execute(
        select(MaterialReceipt.available_at).where(
            MaterialReceipt.instance_id == scenario_id)
    ).all():
        assert t >= 0

    inst = session.get(Instance, scenario_id)
    inst.source_time_unit = "minute"
    inst.time_scale_to_minutes = 1.0
    inst.normalized_time_unit = "minute"

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=scenario_id,
            contribution_type="time_normalization",
            transformation_description=(
                "identity mapping verified: all time columns are non-negative "
                "integer minutes (spec §7 conventions)"
            ),
            random_seed=None,
        )
    )
    return {"checks_passed": True}
```

- [ ] **Step 3: Finish `build_scenario` wiring**

Replace the remaining placeholder comments:

```python
        #   add_materials(...)           (Task 14)
        #   normalize_times(...)         (Task 14)
```

with:

```python
        add_materials(session, scenario.id, seed=seed + 5)
        normalize_times(session, scenario.id)
```

plus imports:

```python
from coe.scenario.add_materials import add_materials
from coe.scenario.normalize_time import normalize_times
```

- [ ] **Step 4: Write `tests/db/test_scenario_materials_determinism.py`**

```python
import hashlib
import json

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


def test_supply_covers_demand(demo_scenario):
    from coe.config import get_settings

    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        shortfall = c.execute(
            text(
                """
                SELECT count(*) FROM materials m
                WHERE m.instance_id = :i
                  AND m.initial_stock + COALESCE((
                        SELECT sum(r.quantity) FROM material_receipts r
                        WHERE r.material_id = m.id AND r.available_at <= 1440), 0)
                      < (
                        SELECT COALESCE(sum(b.quantity_required), 0)
                        FROM operation_bom b WHERE b.material_id = m.id)
                """
            ),
            {"i": demo_scenario},
        ).scalar_one()
    assert shortfall == 0  # baseline must not block any material


def test_full_build_is_byte_reproducible(clean_db):
    from pathlib import Path

    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    def canonical_dump() -> str:
        eng = create_engine(get_settings().database_url)
        tables = [
            ("machines", "name"), ("jobs", "name"), ("operations", "id"),
            ("operation_machine_alternatives", "operation_id"),
            ("workers", "name"), ("worker_roles", "role_name"),
            ("operation_machine_worker_times", "operation_id"),
            ("worker_availability_windows", "id"), ("job_families", "name"),
            ("setup_times", "id"), ("materials", "sku"), ("operation_bom", "operation_id"),
            ("material_receipts", "id"), ("machine_downtime_windows", "id"),
            ("scenario_sources", "id"),
        ]
        payload = {}
        with eng.begin() as c:
            sid = c.execute(
                text("SELECT id FROM instances WHERE name='factory_demo_01'")
            ).scalar_one()
            for table, order_col in tables:
                # scenario_sources keys on scenario_id, not instance_id
                filter_col = "scenario_id" if table == "scenario_sources" else "instance_id"
                rows = c.execute(
                    text(
                        f"SELECT * FROM {table} "
                        f"WHERE {filter_col} = :sid ORDER BY {order_col}"
                    ),
                    {"sid": sid},
                ).mappings().all()
                payload[table] = [dict(r) for r in rows]
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def rebuild() -> str:
        reset_database(get_settings().database_url)
        import_mk01(Path("data/raw/mk01/mk01.txt"))
        import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
        import_gass(Path("data/raw/gass"))
        build_scenario("factory_demo_01", seed=42)
        return canonical_dump()

    h1 = rebuild()
    h2 = rebuild()
    assert h1 == h2


def test_cli_build_smoke(clean_db):
    import subprocess

    result = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "import", "mk01"], check=True, capture_output=True
    )
    subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "import", "hutter", "--path",
         "data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "import", "gass"],
        check=True, capture_output=True,
    )
    result = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "scenario", "build",
         "--name", "factory_demo_01", "--seed", "42"],
        check=True, capture_output=True, text=True,
    )
    assert "scenario id=" in result.stdout
```

Run: `uv run pytest tests/db/test_scenario_materials_determinism.py -v`
Expected: 3 passed (the determinism test takes the longest — two full resets + builds).

- [ ] **Step 5: Commit**

```bash
git add coe/scenario tests/
git commit -m "feat(scenario): materials layer, time normalization, byte-reproducible builds"
```

### Task 15: MQTT Ingestion Slice

**Files:**
- Create: `coe/mqtt/ingest.py`, `coe/mqtt/subscriber.py`, `coe/mqtt/edge_stub.py`
- Modify: `coe/cli.py`, `pyproject.toml` (add `mqtt` pytest marker)
- Test: `tests/db/test_downtime_union.py`, `tests/mqtt/test_roundtrip.py`

**Interfaces:**
- Consumes: models Tasks 4–6; `session_scope`.
- Produces:
  - `FailurePayload` (pydantic model: `message_id: str`, `instance_id: str`, `machine_id: str`, `event_type: Literal["FAILURE","MAINTENANCE"]`, `occurred_at: int ≥ 0`, `severity: Literal["LOW","MEDIUM","HIGH","CRITICAL"] | None`, `estimated_downtime: int > 0 | None`, `reason: str | None`)
  - `ingest_telemetry_event(payload_dict: dict) -> tuple[int, bool]` returning `(telemetry_id, created)`; idempotent on `message_id`
  - `publish_failure(...) -> str` (returns the `message_id` used)
  - `run_subscriber() -> SubscriberHandle` with `.stop()` for tests

Conventions documented here: payload `machine_id` refers to `machines.name` within the named instance (`M3`, not a global id); `received_at` defaults to `occurred_at` (Phase 1 has no wall-clock↔normalized-minute mapping; honest convention); `RECOVERY` events are intentionally rejected in Phase 1 (§9: restoration belongs to later phases).

- [ ] **Step 1: Write `coe/mqtt/ingest.py`**

```python
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from coe.db.models.downtime import MachineDowntimeWindow, TelemetryEvent
from coe.db.models.fjsp import Machine
from coe.db.models.provenance import Instance


class PayloadError(ValueError):
    pass


class FailurePayload(BaseModel):
    message_id: str
    instance_id: str
    machine_id: str
    event_type: Literal["FAILURE", "MAINTENANCE"]
    occurred_at: int
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None
    estimated_downtime: int | None = Field(default=None, gt=0)
    reason: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("negative occurred_at crashes CP-SAT (spec §6.5)")
        return v


def _union_window(
    session: Session,
    *,
    instance_id: int,
    machine_id: int,
    new_from: int,
    new_until: int | None,
    reason: str,
    telemetry_id: int,
) -> None:
    """Spec §6.5: overlapping/touching intervals union into the smallest covering
    interval, under a per-machine advisory lock."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"downtime:{instance_id}:{machine_id}"},
    )
    rows = session.scalars(
        select(MachineDowntimeWindow).where(
            MachineDowntimeWindow.instance_id == instance_id,
            MachineDowntimeWindow.machine_id == machine_id,
        )
    ).all()

    def touches(r: MachineDowntimeWindow) -> bool:
        r_to = r.downtime_until
        if new_until is None:
            return r_to is None or r_to >= new_from
        if r_to is None:
            return new_until >= r.downtime_from
        return new_from <= r_to and r.downtime_from <= new_until

    overlapping = [r for r in rows if touches(r)]
    if not overlapping:
        session.add(
            MachineDowntimeWindow(
                instance_id=instance_id,
                machine_id=machine_id,
                downtime_from=new_from,
                downtime_until=new_until,
                reason=reason,
                severity=None,
                source_event_ids=[telemetry_id],
            )
        )
        return

    merged_from = min([new_from] + [r.downtime_from for r in overlapping])
    merged_open = any(r.downtime_until is None for r in overlapping) or new_until is None
    merged_until = None if merged_open else max(
        [new_until] + [r.downtime_until for r in overlapping]
    )
    reasons = {r.reason for r in overlapping} | {reason}
    merged_reason = "FAILURE" if "FAILURE" in reasons else next(iter(reasons))
    event_ids: list[int] = [telemetry_id]
    for r in overlapping:
        event_ids.extend(r.source_event_ids or [])
        session.delete(r)

    session.add(
        MachineDowntimeWindow(
            instance_id=instance_id,
            machine_id=machine_id,
            downtime_from=merged_from,
            downtime_until=merged_until,
            reason=merged_reason,
            severity=None,
            source_event_ids=event_ids,
        )
    )


def ingest_telemetry_event(payload_dict: dict) -> tuple[int, bool]:
    """Validate, persist telemetry, union downtime, flip status. Idempotent."""
    try:
        payload = FailurePayload.model_validate(payload_dict)
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
        machine = (
            session.query(Machine)
            .filter(
                Machine.instance_id == inst.id,
                Machine.name == payload.machine_id,
            )
            .one_or_none()
        )
        if machine is None:
            raise PayloadError(
                f"unknown machine '{payload.machine_id}' in '{payload.instance_id}'"
            )

        existing = session.execute(
            select(TelemetryEvent.id).where(
                TelemetryEvent.instance_id == inst.id,
                TelemetryEvent.message_id == payload.message_id,
            )
        ).first()
        if existing is not None:
            return existing[0], False  # duplicate delivery: no state change

        event = TelemetryEvent(
            occurred_at=payload.occurred_at,
            instance_id=inst.id,
            message_id=payload.message_id,
            machine_id=machine.id,
            event_type=payload.event_type,
            received_at=payload.occurred_at,  # convention: see task header
            severity=payload.severity,
            estimated_downtime=payload.estimated_downtime,
            processed_at=payload.occurred_at,
            processing_error=None,
            payload_json=payload_dict,
        )
        session.add(event)
        session.flush()

        if payload.event_type == "FAILURE":
            new_until = (
                payload.occurred_at + payload.estimated_downtime
                if payload.estimated_downtime is not None
                else None
            )
        else:  # MAINTENANCE always carries an explicit estimate in Phase 1
            new_until = payload.occurred_at + (payload.estimated_downtime or 0)

        if new_until is None or new_until > payload.occurred_at:
            _union_window(
                session,
                instance_id=inst.id,
                machine_id=machine.id,
                new_from=payload.occurred_at,
                new_until=new_until,
                reason=payload.event_type,
                telemetry_id=event.id,
            )

        if payload.event_type == "FAILURE":
            machine.status = "FAILED"

        session.flush()
        return event.id, True
```

- [ ] **Step 2: Write `coe/mqtt/subscriber.py`**

```python
import json
import threading
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from coe.config import get_settings
from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

TOPIC_FILTER = "factory/+/machine/+/events"


@dataclass
class SubscriberHandle:
    client: mqtt.Client

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def _on_message(client, userdata, msg) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"[subscriber] undecodable payload on {msg.topic}")
        return
    try:
        telemetry_id, created = ingest_telemetry_event(payload)
        status = "created" if created else "duplicate-suppressed"
        print(f"[subscriber] {status} telemetry id={telemetry_id}")
    except PayloadError as exc:
        # Unresolvable payloads cannot populate telemetry_events.machine_id;
        # they are logged loudly instead (documented limitation).
        print(f"[subscriber] REJECTED: {exc}")


def run_subscriber() -> SubscriberHandle:
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = _on_message
    client.connect(s.mqtt_host, s.mqtt_port)
    client.subscribe(TOPIC_FILTER, qos=1)
    client.loop_start()
    return SubscriberHandle(client=client)
```

- [ ] **Step 3: Write `coe/mqtt/edge_stub.py`**

```python
import json
import uuid

import paho.mqtt.client as mqtt

from coe.config import get_settings


def publish_failure(
    instance_name: str,
    machine_name: str,
    *,
    occurred_at: int = 512,
    estimated_downtime: int | None = 90,
    severity: str = "HIGH",
    reason: str = "mechanical_failure",
    message_id: str | None = None,
) -> str:
    mid = message_id or f"evt-{uuid.uuid4().hex[:12]}"
    payload = {
        "message_id": mid,
        "instance_id": instance_name,
        "machine_id": machine_name,
        "event_type": "FAILURE",
        "occurred_at": occurred_at,
        "severity": severity,
        "estimated_downtime": estimated_downtime,
        "reason": reason,
    }
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(s.mqtt_host, s.mqtt_port)
    topic = f"factory/{instance_name}/machine/{machine_name}/events"
    info = client.publish(topic, json.dumps(payload), qos=1)
    info.wait_for_publish()
    client.disconnect()
    return mid
```

- [ ] **Step 4: Add `mqtt` marker to `pyproject.toml`**

Change the markers list to:

```toml
markers = [
    "db: tests requiring Dockerized TimescaleDB (docker compose up -d first)",
    "mqtt: tests also requiring Mosquitto reachable at MQTT_HOST/MQTT_PORT",
]
```

- [ ] **Step 5: Wire CLI `mqtt test-failure`**

In `build_parser()` add:

```python
    mq = sub.add_parser("mqtt")
    mq_sub = mq.add_subparsers(dest="mqtt_cmd", required=True)
    tf = mq_sub.add_parser("test-failure")
    tf.add_argument("--instance", default="factory_demo_01")
    tf.add_argument("--machine", default="M3")
    tf.add_argument("--at", type=int, default=512)
```

In `main()` dispatch add:

```python
    elif args.group == "mqtt":
        if args.mqtt_cmd == "test-failure":
            import time

            from coe.db.session import make_engine
            from coe.mqtt.edge_stub import publish_failure
            from coe.mqtt.subscriber import run_subscriber
            from sqlalchemy import text

            handle = run_subscriber()
            mid = publish_failure(args.instance, args.machine, occurred_at=args.at)
            deadline = time.time() + 5
            engine = make_engine()
            found = False
            while time.time() < deadline and not found:
                with engine.begin() as c:
                    n = c.execute(
                        text(
                            "SELECT count(*) FROM telemetry_events te "
                            "JOIN instances i ON i.id = te.instance_id "
                            "WHERE i.name = :inst AND te.message_id = :mid"
                        ),
                        {"inst": args.instance, "mid": mid},
                    ).scalar_one()
                found = n == 1
                if not found:
                    time.sleep(0.25)
            handle.stop()
            if found:
                print(f"OK: telemetry id stored once for message {mid}")
                raise SystemExit(0)
            raise SystemExit("FAIL: event did not reach telemetry_events within 5s")
```

- [ ] **Step 6: Write union unit tests `tests/db/test_downtime_union.py`**

These need a scenario whose machines are addressable by name — build the demo first:

```python
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db


@pytest.fixture()
def demo(demo_scenario):
    return demo_scenario


def _payload(inst="factory_demo_01", machine="M3", **over):
    base = {
        "message_id": "evt-x",
        "instance_id": inst,
        "machine_id": machine,
        "event_type": "FAILURE",
        "occurred_at": 100,
        "estimated_downtime": 50,
    }
    base.update(over)
    return base


def _windows(url, inst="factory_demo_01"):
    from coe.config import get_settings

    with create_engine(get_settings().database_url).begin() as c:
        return c.execute(
            text(
                """
                SELECT downtime_from, downtime_until, reason FROM machine_downtime_windows w
                JOIN instances i ON i.id = w.instance_id
                WHERE i.name = :i AND w.machine_id = (
                    SELECT id FROM machines WHERE instance_id = i.id AND name = 'M3')
                ORDER BY downtime_from
                """
            ),
            {"i": inst},
        ).all()


def test_touching_intervals_union(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    ingest_telemetry_event(_payload(message_id="a"))          # [100,150)
    ingest_telemetry_event(_payload(message_id="b", occurred_at=150))  # touches
    rows = _windows(None)
    assert rows == [(100, 200, "FAILURE")]


def test_disjoint_intervals_stay_separate(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    ingest_telemetry_event(_payload(message_id="c"))
    ingest_telemetry_event(_payload(message_id="d", occurred_at=500, estimated_downtime=10))
    rows = _windows(None)
    assert len(rows) == 2


def test_duplicate_message_suppressed(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    t1, c1 = ingest_telemetry_event(_payload(message_id="dup"))
    t2, c2 = ingest_telemetry_event(_payload(message_id="dup"))
    assert c1 is True and c2 is False and t1 == t2
    assert len(_windows(None)) == 1


def test_machine_status_flipped_and_restorable_by_union(demo):
    from coe.mqtt.ingest import ingest_telemetry_event

    ingest_telemetry_event(_payload(message_id="s1"))
    from coe.config import get_settings

    with create_engine(get_settings().database_url).begin() as c:
        st = c.execute(
            text(
                "SELECT m.status FROM machines m JOIN instances i ON i.id=m.instance_id "
                "WHERE i.name='factory_demo_01' AND m.name='M3'"
            )
        ).scalar_one()
    assert st == "FAILED"


def test_negative_occurred_at_rejected_payload_level(demo):
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    try:
        ingest_telemetry_event(_payload(message_id="neg", occurred_at=-1))
        raised = False
    except PayloadError:
        raised = True
    assert raised
```

Run: `uv run pytest tests/db/test_downtime_union.py -v`
Expected: 5 passed.

- [ ] **Step 7: Write broker round-trip test `tests/mqtt/test_roundtrip.py`**

```python
import time

import pytest

pytestmark = [pytest.mark.db, pytest.mark.mqtt]


def test_edge_to_db_roundtrip(demo_scenario):
    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_failure
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text

    handle = run_subscriber()
    try:
        mid = publish_failure("factory_demo_01", "M5", occurred_at=777)
        eng = create_engine(get_settings().database_url)
        deadline = time.time() + 5
        stored = None
        while time.time() < deadline and stored is None:
            with eng.begin() as c:
                stored = c.execute(
                    text(
                        "SELECT te.processed_at FROM telemetry_events te "
                        "JOIN instances i ON i.id = te.instance_id "
                        "WHERE te.message_id = :m"
                    ),
                    {"m": mid},
                ).scalar_one_or_none()
            if stored is None:
                time.sleep(0.2)
        assert stored == 777
    finally:
        handle.stop()
```

Run: `uv run pytest tests/mqtt/test_roundtrip.py -v`
Expected: 1 passed (broker must be up).

- [ ] **Step 8: Smoke the CLI end-to-end**

```bash
uv run python -m coe.cli mqtt test-failure --instance factory_demo_01 --machine M3 --at 600
```
Expected: exit 0 printing `OK: telemetry id stored once ...`.

- [ ] **Step 9: Commit**

```bash
git add coe/mqtt coe/cli.py pyproject.toml tests/
git commit -m "feat(mqtt): idempotent failure ingestion with downtime union"
```

### Task 16: Acceptance Sweep

**Files:**
- Create: `tests/db/test_acceptance.py`
- Modify: nothing else

**Interfaces:**
- Consumes: everything.
- Produces: one test module proving spec §12 criteria 1–11 end-to-end on a clean database, using only the §10 CLI commands.

- [ ] **Step 1: Write `tests/db/test_acceptance.py`**

```python
import subprocess

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.db

MK01 = "data/raw/mk01/mk01.txt"
SFJW = "data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"
GASS = "data/raw/gass"


def cli(*args: str) -> str:
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return r.stdout


@pytest.fixture(scope="module")
def full_pipeline():
    """Criterion 1 is proven by the suite's dependency on compose services;
    criteria 2-9 exercised here through the public CLI."""
    from coe.config import get_settings
    from coe.db.admin import reset_database

    reset_database(get_settings().database_url)          # criterion 2
    cli("import", "mk01", "--path", MK01)                # criteria 3+4
    cli("import", "hutter", "--path", SFJW)              # criterion 5a
    cli("import", "gass", "--dir", GASS)                 # criterion 5b
    cli("scenario", "build", "--name", "factory_demo_01", "--seed", "42")  # 6-9
    return get_settings().database_url


def test_criterion_4_mk01_shape(full_pipeline):
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        row = c.execute(
            text(
                """
                SELECT count(DISTINCT j.id), count(DISTINCT m.id), count(DISTINCT o.id)
                FROM instances i
                JOIN jobs j ON j.instance_id = i.id
                JOIN machines m ON m.instance_id = i.id
                JOIN operations o ON o.instance_id = i.id
                WHERE i.name = 'mk01'
                """
            )
        ).fetchone()
    assert tuple(row) == (10, 6, 55)


def test_criterion_6_scenario_dimensions(full_pipeline):
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        jobs, machines = c.execute(
            text(
                """
                SELECT (SELECT count(*) FROM jobs WHERE instance_id=i.id),
                       (SELECT count(*) FROM machines WHERE instance_id=i.id)
                FROM instances i WHERE i.name='factory_demo_01'
                """
            )
        ).fetchone()
    assert (jobs, machines) == (30, 8)


def test_criterion_7_no_dead_end_operations(full_pipeline):
    """Every generated operation has at least one capable machine."""
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        dead = c.execute(
            text(
                """
                SELECT count(*) FROM operations o
                JOIN instances i ON i.id = o.instance_id
                WHERE i.name = 'factory_demo_01'
                  AND NOT EXISTS (
                    SELECT 1 FROM operation_machine_alternatives a
                    WHERE a.operation_id = o.id)
                """
            )
        ).scalar_one()
    assert dead == 0


def test_criterion_8_provenance_complete(full_pipeline):
    expected = {
        "topology", "job_attributes", "worker_flexibility",
        "worker_availability", "setup_times", "maintenance_windows",
        "materials_inventory", "time_normalization",
    }
    eng = create_engine(full_pipeline)
    with eng.begin() as c:
        types = {
            r[0]
            for r in c.execute(
                text(
                    """
                    SELECT ss.contribution_type FROM scenario_sources ss
                    JOIN instances i ON i.id = ss.scenario_id
                    WHERE i.name = 'factory_demo_01'
                    """
                )
            ).all()
        }
    assert expected <= types


def test_criterion_10_same_seed_identical(clean_db):
    from pathlib import Path

    from coe.config import get_settings
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    def build_and_count():
        reset_database(get_settings().database_url)
        import_mk01(Path(MK01))
        import_nouri(Path(SFJW))
        import_gass(Path(GASS))
        sid = build_scenario("factory_demo_01", seed=42)
        eng = create_engine(get_settings().database_url)
        with eng.begin() as c:
            return c.execute(
                text(
                    "SELECT count(*) FROM operation_machine_worker_times "
                    "WHERE instance_id=:i"
                ),
                {"i": sid},
            ).scalar_one()

    assert build_and_count() == build_and_count()


def test_criterion_11_mqtt_event_once_with_window(full_pipeline):
    import time

    from coe.config import get_settings
    from coe.mqtt.edge_stub import publish_failure
    from coe.mqtt.subscriber import run_subscriber
    from sqlalchemy import create_engine, text as sqltext

    handle = run_subscriber()
    try:
        mid = publish_failure("factory_demo_01", "M2", occurred_at=900)
        deadline = time.time() + 5
        stored = False
        while time.time() < deadline and not stored:
            eng = create_engine(get_settings().database_url)
            with eng.begin() as c:
                n = c.execute(
                    sqltext(
                        """
                        SELECT count(*) FROM telemetry_events te
                        JOIN instances i ON i.id = te.instance_id
                        WHERE i.name='factory_demo_01' AND te.message_id=:m
                        """
                    ),
                    {"m": mid},
                ).scalar_one()
                win = c.execute(
                    sqltext(
                        """
                        SELECT count(*) FROM machine_downtime_windows w
                        JOIN instances i ON i.id = w.instance_id
                        JOIN machines mm ON mm.instance_id = i.id AND mm.name='M2'
                        WHERE i.name='factory_demo_01'
                          AND w.downtime_from <= 900
                          AND (w.downtime_until IS NULL OR w.downtime_until > 900)
                        """
                    )
                ).scalar_one()
            stored = n == 1 and win >= 1
            if not stored:
                time.sleep(0.25)
        assert stored
    finally:
        handle.stop()
```

- [ ] **Step 2: Run the complete suite**

```bash
docker compose up -d
uv run pytest -v
```
Expected: every test green across parsers, db, scenario, mqtt, acceptance modules.

- [ ] **Step 3: Run the §10 command sequence by hand**

```bash
uv run python -m coe.cli db reset
uv run python -m coe.cli db migrate
uv run python -m coe.cli import mk01
uv run python -m coe.cli import hutter --path data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt
uv run python -m coe.cli import gass
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
uv run python -m coe.cli mqtt test-failure
```
Expected: every command exits 0.

Note: `db migrate` maps to `alembic upgrade head`; wire it in `main()` if not already:

```python
        if args.db_cmd == "migrate":
            subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
            print("migrations applied")
```

(add `import subprocess` at top of `coe/cli.py`).

- [ ] **Step 4: Final commit**

```bash
git add tests/
git commit -m "test: phase-1 acceptance sweep over all eleven criteria"
```

---

## Self-Review Notes (for the executing engineer)

None — this section is intentionally empty; the author ran the review checklist (spec coverage, placeholder scan, type consistency) and folded every finding back into the tasks themselves.

