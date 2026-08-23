# Phase 1: Infrastructure and Data Ingestion

**Status:** Approved — Amended 2026-08-23 (multi-resource disruption ingestion)
**Date:** 2026-08-20
**Phase:** Infrastructure and Data Ingestion

> **Amendment 2026-08-23 (user-approved):** disruptions are no longer machine-only. Worker absence and material shortage enter through the same telemetry pipeline as machine failure. See amended §6.4, §6.5, §9, and §12.11. Rationale: PRD §5 already listed worker-absentee and inventory-shortage scenarios; the DisruptionRecord must represent them.

## 1. Purpose

Phase 1 establishes the reproducible data and infrastructure foundation for the factory recovery system. It must prove that source data can be imported, normalized, augmented into a complete factory scenario, and observed through an MQTT-to-database ingestion path.

The phase does not implement optimization or agents. It prepares the canonical data contract those later phases consume.

## 2. Scope

### Included

- Docker Compose for TimescaleDB and Mosquitto.
- SQLAlchemy 2.0 models with Alembic migrations.
- PostgreSQL-compatible canonical schema with TimescaleDB support.
- Independent parsers for MK01, Hutter/Nouri worker-flexibility data, and GASS data.
- A deterministic scenario builder that creates `factory_demo_01`.
- Job-family and sequence-dependent setup-time modeling.
- Synthetic materials, inventory, worker availability, and MQTT failure events.
- Dataset provenance and transformation records.
- A minimal MQTT publisher/subscriber path.
- Reproducible seeds, database reset, loading, and validation commands.

### Excluded

- CP-SAT optimization.
- Discrete-event schedule execution.
- LangGraph and LLM calls.
- QUBO/QAOA.
- Sensor-level predictive-maintenance modeling.
- Thermodynamic or physical machine simulation.
- Final recovery workflow and schedule commit behavior.

### 2.1 Project Structure

The project uses `uv` as the package manager and strictly adheres to the following dependency and layout structure:

- **Tooling:** Python 3.12+, `uv` (dependency management), `pytest` (testing).
- **Core Dependencies:** `SQLAlchemy >= 2.0`, `alembic`, `psycopg[binary] >= 3.0`, `paho-mqtt`, `pydantic-settings`.
- **Package Layout:**
  - `pyproject.toml` (Dependency pins and metadata)
  - `coe/` (Root package)
    - `coe/db/` (SQLAlchemy models, session management, Alembic migrations)
    - `coe/parsers/` (MK01, Hutter/Nouri, and GASS import logic)
    - `coe/scenario/` (Synthetic data generators, e.g., `add_workers.py`)
    - `coe/mqtt/` (Mosquitto subscriber and publisher stubs)
    - `coe/cli.py` (Command-line entry points)

## 3. Source and Scenario Strategy

The source datasets are not merged row-by-row. Each source is imported as an independent database instance with its own identifiers and provenance.

### 3.1 MK01

MK01 is the first parser-validation instance. It provides the canonical FJSP structure:

- Jobs
- Operations and precedence order
- Machines
- Operation-machine eligibility
- Machine-dependent processing times

MK01 is also the structural base for the primary generated demo scenario.

Kacem is not required for Phase 1. Its parser is deferred because MK01 provides the initial parser-validation coverage; the canonical schema remains extensible to Kacem later.

### 3.2 Hutter/Nouri FJSSP-W

Hutter/Nouri data provides worker-flexibility behavior:

- Worker identifiers
- Worker eligibility for operation-machine combinations
- Worker-dependent processing times

Its literal worker and operation rows remain in a separate source instance. The scenario builder uses its worker-flexibility structure to generate worker assignments for `factory_demo_01`.

### 3.3 GASS Flexible-Packaging Data

GASS provides industrial scheduling and disruption behavior where supported by the released data (if public data is unavailable or incomplete, synthetic GASS-derived fallback profiles are used):

- Machine and routing characteristics
- Setup/changeover behavior
- Job priorities and timing attributes
- Machine downtime and failure behavior

When available in the source data, setup/changeover behavior is normalized into job families and sequence-dependent setup times.

Its literal rows remain in a separate source instance. The scenario builder uses documented GASS-derived profiles (or synthetic fallbacks) to generate setup and failure behavior for `factory_demo_01`.

### 3.4 Synthetic Data

No compatible public source provides the complete materials and inventory model. The following are generated deterministically:

- Worker availability windows
- Materials and BOM requirements
- Initial material stock
- Material receipts
- Runtime MQTT events

Synthetic values are marked as synthetic in provenance metadata and are not described as real factory observations.

NSPLib is not part of the core Phase 1 data path. It may be considered later as a reference for shift-pattern generation, but it is not treated as manufacturing workforce data.

## 4. Import and Scenario Pipeline

Each source adapter performs the same responsibilities:

1. Read its source format.
2. Validate source structure and required fields.
3. Preserve source identifiers.
4. Normalize values into the canonical schema.
5. Create an isolated `instance_id`.
6. Record source provenance.

The primary scenario is built explicitly rather than through a generic black-box generator:

```text
MK01 source instance
    -> clone topology into factory_demo_01
    -> apply Hutter/Nouri worker-flexibility profile
    -> apply GASS setup/failure profiles
    -> assign job families and setup times
    -> generate materials and inventory
    -> generate worker availability windows
    -> normalize time values
    -> record scenario_sources
```

`factory_demo_01` is a reproducible synthetic composite scenario. It is not presented as a single real-world dataset.

The scenario builder is divided into explicit transformations:

- `clone_instance.py`
- `add_job_attributes.py` (deterministically assigns deadlines using TWK or total-work-content × slack factor, release times, and priorities using the seed)
- `add_workers.py`
- `add_setup_times.py`
- `add_failures.py`
- `add_materials.py`
- `normalize_time.py`

Every transformation accepts an explicit random seed and produces auditable output.

## 5. Database Technology

- SQLAlchemy 2.0 is used for models, relationships, and application queries.
- Alembic is used for versioned migrations.
- Psycopg 3 is the PostgreSQL driver.
- Raw SQL is limited to TimescaleDB-specific operations, such as extension and hypertable creation.
- Alembic migrations, not automatic table creation, are authoritative.
- JSONB stores raw external payloads and boundary data where preserving the original representation is useful.

## 6. Canonical Schema

All instance-owned tables and association tables carry `instance_id`. Source identifiers are retained in `source_id` columns where applicable. Composite foreign keys prevent records from different instances being joined accidentally.

### 6.1 Instance and Provenance Tables

#### `instances`

- `id`
- `name`
- `source_name`
- `source_url`
- `source_version`
- `source_license`
- `retrieved_at`
- `source_checksum`
- `source_time_unit`
- `time_scale_to_minutes`
- `normalized_time_unit`
- `created_at`

#### `scenario_sources`

Records how each source influenced a generated scenario.

- `scenario_id` (FK to `instances.id`; the generated scenario is itself an instance)
- `source_instance_id`
- `contribution_type`
- `transformation_description`
- `random_seed`

#### `instance_profiles`

Stores named parameter profiles used by scenario transformations.

- `id`
- `name`
- `profile_type`
- `parameters_json`
- `source_instance_id`

### 6.2 FJSP Tables

#### `machines`

- `id`
- `instance_id`
- `source_id`
- `name`
- `status`

Valid machine statuses are `ACTIVE`, `FAILED`, and `MAINTENANCE`.

#### `machine_capabilities`

Operation-machine eligibility remains authoritative for solver feasibility. This table provides optional semantic labels for agents and explanations.

- `id`
- `instance_id`
- `machine_id`
- `capability_code`
- `display_name`
- `source`

#### `jobs`

- `id`
- `instance_id`
- `source_id`
- `name`
- `job_family_id` (nullable FK to `job_families.id`)
- `release_time`
- `deadline`
- `priority`
- `status`

Valid job statuses are `PENDING`, `IN_PROGRESS`, `COMPLETED`, and `BLOCKED`.

#### `operations`

- `id`
- `instance_id`
- `job_id`
- `source_id`
- `sequence_number` (Must be >= 1. The combination of `job_id` and `sequence_number` MUST be unique)
- `required_role_id` (nullable FK to `worker_roles.id`)
- `status`

Valid operation statuses are `PENDING`, `SCHEDULED`, `IN_PROGRESS`, `COMPLETED`, `INTERRUPTED`, and `BLOCKED`. When `required_role_id` is populated, it references `worker_roles.id`; worker-operation eligibility remains authoritative.

#### `operation_machine_alternatives`

Composite primary key: (`instance_id`, `operation_id`, `machine_id`).

- `instance_id`
- `operation_id`
- `machine_id`
- `processing_time`

#### `job_families`

- `id`
- `instance_id`
- `source_id`
- `name`

#### `setup_times`

Sequence-dependent setup duration for a machine when changing product family.

`id` is the primary key. The logical key is (`instance_id`, `machine_id`, `from_family_id`, `to_family_id`); `from_family_id` is nullable for an initial setup row, and the importer enforces at most one initial setup per machine and target family.

- `id`
- `instance_id`
- `machine_id`
- `from_family_id` (nullable for initial setup)
- `to_family_id`
- `setup_duration`
- `source`

### 6.3 Worker Tables

#### `workers`

- `id`
- `instance_id`
- `source_id`
- `name`
- `role_id` (nullable FK to `worker_roles.id`)
- `status`

Valid worker statuses are `AVAILABLE` and `UNAVAILABLE`.

#### `operation_machine_worker_times`

Authoritative worker eligibility and duration data.

Composite primary key: (`instance_id`, `operation_id`, `machine_id`, `worker_id`).

- `instance_id`
- `operation_id`
- `machine_id`
- `worker_id`
- `processing_time`

A missing row means that the worker cannot perform that operation-machine combination. This table is authoritative for feasibility; the scenario builder and importer must validate that every `(operation_id, machine_id)` pair in `operation_machine_alternatives` has at least one eligible worker row, otherwise the infeasible alternative must be dropped and flagged with a provenance warning.

#### `worker_roles`

Optional semantic classification for worker-facing explanations and generated scenarios.

- `id`
- `instance_id`
- `role_name`

#### `worker_availability_windows`

- `instance_id`
- `worker_id`
- `available_from`
- `available_until`
- `source_pattern`

Concrete windows are used instead of recurring shift rules so later solver constraints can consume them directly.

### 6.4 Materials Tables

#### `materials`

- `id`
- `instance_id`
- `sku`
- `initial_stock`
- `reorder_point` (optional)

#### `operation_bom`

Composite primary key: (`instance_id`, `operation_id`, `material_id`).

- `instance_id`
- `operation_id`
- `material_id`
- `quantity_required`

#### `material_receipts`

Represents material arriving at a known time.

- `id`
- `instance_id`
- `material_id`
- `quantity`
- `available_at`
- `source`

#### `material_transactions` (future phase)

Runtime inventory audit log. It is not populated by Phase 1 because no schedule is executed yet.

- `id`
- `instance_id`
- `operation_id`
- `material_id`
- `quantity`
- `timestamp`
- `transaction_type`

### 6.5 Machine Downtime and Telemetry

#### `machine_downtime_windows`

- `id`
- `instance_id`
- `machine_id`
- `downtime_from`
- `downtime_until`
- `reason`
- `severity`
- `source_event_ids`

An open-ended outage uses a null `downtime_until`. For the same instance and machine, overlapping or touching intervals are unioned into the smallest covering interval. **Note on Concurrency:** To prevent race conditions resulting in duplicate overlapping rows when multiple MQTT events arrive simultaneously, the interval union logic must be executed under a `SERIALIZABLE` transaction isolation level or use a strict advisory lock per `machine_id`. 

Unplanned failure takes precedence over planned maintenance as the primary reason, and all contributing event IDs are retained in `source_event_ids`. A recovery event closes the open interval. An event carrying `estimated_downtime` creates a finite window (`downtime_until = occurred_at + estimated_downtime`); an event without one creates an open-ended outage (`downtime_until = null`) closed only by a later recovery or restore action. The solver later treats these rows as unavailable time. Worker windows intentionally use positive availability semantics because they originate from rosters; the Phase 2 constraint adapter converts both representations into unavailable intervals before solving.

#### `worker_absence_windows` (Amendment 2026-08-23)

Mirrors machine downtime semantics for people, so absences announced as events get the same union/close/open-ended treatment as machine outages:

- `id`
- `instance_id`
- `worker_id`
- `absence_from`
- `absence_until` (null = open-ended; closed by a later `WORKER_RETURN`)
- `reason`
- `severity`
- `source_event_ids`

For the same instance and worker, overlapping or touching intervals are unioned into the smallest covering interval under a per-worker advisory lock (same concurrency rule as machine downtime). The solver treats these rows directly as unavailability intervals — unlike roster-derived `worker_availability_windows`, no positive-to-negative conversion is needed.

#### `telemetry_events`

This is a TimescaleDB hypertable partitioned by `occurred_at`.

- `id`
- `instance_id`
- `message_id` (unique)
- `machine_id` (nullable as of Amendment 2026-08-23; set when `resource_kind = MACHINE`)
- `worker_id` (nullable FK to workers.id; set when `resource_kind = WORKER` — Amendment 2026-08-23)
- `material_id` (nullable FK to materials.id; set when `resource_kind = MATERIAL` — Amendment 2026-08-23)
- `resource_kind` (`MACHINE | WORKER | MATERIAL`; CHECK constraint: exactly one of the three resource references is non-null — Amendment 2026-08-23)
- `event_type`
- `occurred_at` (Must be >= 0. Negative time crashes CP-SAT.)
- `received_at` (Must be >= 0)
- `severity`
- `estimated_downtime`
- `processed_at`
- `processing_error`
- `payload_json`

The subscriber is idempotent: duplicate MQTT messages with the same `message_id` do not create duplicate state changes.

Valid `event_type` values per resource kind (Amendment 2026-08-23):

| resource_kind | event_type values |
| --- | --- |
| `MACHINE` | `FAILURE`, `MAINTENANCE` |
| `WORKER` | `WORKER_ABSENT`, `WORKER_RETURN` |
| `MATERIAL` | `MATERIAL_SHORTAGE`, `MATERIAL_RESTOCK` |

The MQTT topic patterns are:

```text
factory/{instance_id}/machine/{machine_id}/events
factory/{instance_id}/worker/{worker_id}/events
factory/{instance_id}/material/{material_sku}/events
```

Incoming failure payloads use this schema (machine-kind shown; worker- and material-kind payloads substitute `worker_id` / `material_sku` and their event types — Amendment 2026-08-23):

```json
{
  "message_id": "evt-0001",
  "instance_id": "factory_demo_01",
  "machine_id": "MC-04",
  "event_type": "FAILURE",
  "occurred_at": 512,
  "severity": "HIGH",
  "estimated_downtime": 90,
  "reason": "mechanical_failure"
}
```

The subscriber validates that the topic and payload identify the same instance and resource (machine, worker, or material per the topic pattern — Amendment 2026-08-23). The complete original payload is stored in `payload_json`.

**Subscriber routing by resource kind (Amendment 2026-08-23):**

- `MACHINE` events: unchanged behavior — downtime-window union; `FAILURE` sets the machine's status to `FAILED`.
- `WORKER_ABSENT` / `WORKER_RETURN` events: union into `worker_absence_windows` (§6.5b) under a per-worker advisory lock; `WORKER_ABSENT` sets the worker's status to `UNAVAILABLE`, `WORKER_RETURN` closes an open absence and restores `AVAILABLE`.
- `MATERIAL_SHORTAGE` / `MATERIAL_RESTOCK` events: telemetry-only. Shortages are *conditions*, not state flips — blocking happens at solve time via the Phase 2 supply check, and receipts remain scenario/strategy-owned (no auto-created receipt rows; runtime inventory transactions stay reserved for a later phase).

### 6.6 Future-Phase Tables

The following tables are reserved in the schema contract but populated later:

- `schedule_versions`
- `schedule_entries`
- `recovery_runs`
- `solver_payloads`
- `recovery_proposals`
- `schedule_explanations`

An `active_schedule` view will later select entries from the current schedule version.

## 7. Time and Reproducibility

Each instance records its source time unit and conversion factor. The application normalizes imported values to integer minutes before scenario construction.

The scenario builder requires an explicit seed. The same source instances, profiles, configuration, and seed must produce the same generated scenario.

The project convention is:

- One normalized unit = one minute
- One standard shift = 480 minutes
- One day = 1,440 minutes

For benchmark data whose units are abstract, the conversion is a documented modeling convention rather than a claim about physical time.

## 8. Configuration

Runtime configuration is loaded from environment variables using `pydantic-settings`, with `.env` support for local development and explicit environment overrides in Docker Compose. Configuration includes the database URL, MQTT broker host and port, default random seed, and TimescaleDB telemetry chunk interval (default: `10080` minutes, representing 7 days of normalized time to ensure balanced partition sizing).

## 9. MQTT Ingestion Slice

Phase 1 proves the minimal event path (Amendment 2026-08-23: three resource kinds):

```text
edge_stub.py
    -> Mosquitto
    -> subscriber.py
    -> telemetry_events
    -> machine_downtime_windows   (MACHINE events)
    -> worker_absence_windows     (WORKER events)
    -> telemetry only             (MATERIAL events)
```

The subscriber stores both normalized fields and the original JSON payload. On machine `FAILURE` events it also sets the affected machine's status to `FAILED`; on `WORKER_ABSENT` it sets the worker to `UNAVAILABLE`; material events create no state beyond telemetry. Restoration is handled by later phases. The full narrative-to-structured translation agent belongs to Phase 3.

CLI test commands cover all three kinds: `mqtt test-failure` (machine), `mqtt test-absence` (worker), `mqtt test-shortage` (material).

## 10. Command Interface

Development commands use the following interface:

```bash
uv run python -m coe.cli db reset
uv run python -m coe.cli db migrate
uv run python -m coe.cli import mk01
uv run python -m coe.cli import hutter
uv run python -m coe.cli import gass
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
uv run python -m coe.cli mqtt test-failure
uv run pytest
```

`db reset` is development-only and destructive.

## 11. Validation and Error Handling

- Source parsers fail with source file and line context.
- Each source import runs in its own transaction and rolls back completely on failure.
- A composite scenario build is atomic: source instances remain intact, while a failed scenario build leaves no partial generated scenario.
- Foreign keys and instance boundaries are enforced by the database.
- Re-importing a source with the same source name, version, and checksum is a no-op and returns the existing instance. A changed checksum creates a new source instance; existing instances are never overwritten.
- Dataset checksums and source metadata are recorded.
- Generator transformations reject invalid parameters.
- MQTT processing errors are stored in `telemetry_events.processing_error`.
- Database reset and reseeding are supported through project commands.

## 12. Acceptance Criteria

Phase 1 is complete when:

1. Docker Compose starts TimescaleDB and Mosquitto.
2. Alembic migrations apply from an empty database.
3. MK01 imports into an isolated source instance.
4. MK01 validation confirms 10 jobs, 6 machines, 55 operations, and Job 1 Operation 1 alternatives `{machine 0: 5, machine 2: 4}`.
5. Hutter/Nouri and GASS adapters can load isolated instances (or synthetic GASS-derived fallback profiles if public data is unavailable) without cross-instance joins.
6. The scenario builder creates exactly 30 jobs and 8 machines in `factory_demo_01` by sampling MK01-derived topology profiles, not by duplicating source rows.
7. Every generated operation has at least one eligible machine and every setup row references valid machine and family IDs.
8. `scenario_sources` records every source contribution and transformation.
9. Worker, material, availability, setup, and telemetry tables populate with deterministic seeded data.
10. Re-running the builder with the same seed produces identical output; a failed transformation leaves no partial scenario.
11. A test MQTT event is stored once, partitions by `occurred_at`, and creates the expected machine downtime interval. *(Amendment 2026-08-23: equivalently for the other kinds — a `WORKER_ABSENT` event is stored once, unions into `worker_absence_windows`, and flips the worker to `UNAVAILABLE`; a `MATERIAL_SHORTAGE` event is stored once as telemetry with no derived state.)*

## 13. Phase Boundary

Phase 1 produces a validated, solver-ready data model and a reproducible composite scenario. It does not solve the schedule, execute the schedule, invoke agents, or perform recovery.
