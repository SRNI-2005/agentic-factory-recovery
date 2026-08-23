# Agentic Factory Recovery System (COE)

LLM-agent middleware for event-driven FJSP (Flexible Job Shop Scheduling) recovery.
Agents own semantics (translate/propose/explain); deterministic solvers own math
(CP-SAT production engine; QAOA research benchmark). TimescaleDB + Mosquitto backbone.

## Project State

- **Phase 1 (Infrastructure & Data Ingestion): COMPLETE** — all 11 acceptance criteria green, whole-branch reviewed, merged.
- Phase 2 (Classical CP-SAT engine) is the next phase to plan and build. Specs for Phases 2–5 are approved in `docs/superpowers/specs/`.
- Specs were AMENDED 2026-08-23 (multi-resource disruptions: machine/worker/material). The amendment markers in the spec files are normative.

## Commands

```bash
docker compose up -d        # REQUIRED before db/mqtt tests or any DB work (TimescaleDB :5432, Mosquitto :1883, coe/coe/coe)
uv run pytest -q            # full suite (~80 tests, ~85s); requires compose stack up
uv run pytest -m "not mqtt" # skip broker-dependent tests

uv run python -m coe.cli db reset        # DESTRUCTIVE: drops user tables, re-runs all migrations
uv run python -m coe.cli import mk01     # also: import hutter --path FILE | --dir DIR, import gass
uv run python -m coe.cli scenario build --name factory_demo_01 --seed 42
uv run python -m coe.cli mqtt test-failure   # also: test-absence --worker W3, test-shortage --sku MAT-001
```

Use `uv` exclusively. Never pip, never system Python. Working from repo root is assumed (paths in tests are CWD-relative).

## Architecture

- `coe/db/` — SQLAlchemy 2.0 models + Alembic migrations (5 migrations; **Alembic is authoritative DDL — `create_all` is forbidden**). Raw SQL only for TimescaleDB-specific ops (hypertable, advisory locks).
- `coe/parsers/` — MK01 (Brandimarte), Nouri (FJSSP-W worker flexibility), GASS (xlsx → instance_profiles). Each import is atomic, checksum-idempotent (changed checksum ⇒ new `name@<8hex>` instance).
- `coe/scenario/` — seeded deterministic builder: `factory_demo_01` = 30 jobs / 8 machines / 168 ops sampled from MK01-derived profiles + Nouri worker layer + GASS setups + synthetic materials. Byte-reproducible for a given seed.
- `coe/mqtt/` — kind-routed ingestion: MACHINE (downtime windows + FAILED status), WORKER (absence windows + UNAVAILABLE, RETURN closes), MATERIAL (telemetry only). All events idempotent on `message_id`; interval unions under per-resource advisory locks; subscriber validates topic ≡ payload.

Every table row is instance-scoped (`instance_id` FK discipline — no cross-instance joins). All time is integer minutes (shift=480, day=1440).

## Conventions

- **Determinism:** any query feeding RNG or float summation gets an explicit `ORDER BY`. Transformation N uses `seed + N`. Same inputs + seed ⇒ byte-identical scenarios (tested via canonical dump hash).
- **TDD** for all work; plans live in `docs/superpowers/plans/`, specs in `docs/superpowers/specs/` — specs are the source of truth; plan code is a starting point, not authority (implementation reviews caught ~10 plan-code bugs; fix minimally and document deviations).
- Testing markers: `db` (needs TimescaleDB), `mqtt` (also needs Mosquitto). `tests/conftest.py` provides `clean_db`, `demo_scenario`, `data_dir` fixtures.
- psycopg3 raises CHECK violations at `execute()`, not `commit()` — structure test try/excepts accordingly.
- `telemetry_events.message_id` uniqueness is app-level (integer-partitioned hypertables forbid unique indexes excluding the partition column).
- Known data quirks: MFJW-05/06/07 are author-corrupted (rejected loudly); GASS process codes include lettered variants (`P2b`) — use `_fam_num()`.

## Gotchas

- `db reset` is destructive and wipes all instances/scenarios — rebuild via the command sequence above.
- Scenario builds refuse duplicate names; rebuild requires reset first.
- Ports are loopback-bound (127.0.0.1) — dev-only anonymous Mosquitto + coe/coe/coe DB creds; never deploy as-is.
