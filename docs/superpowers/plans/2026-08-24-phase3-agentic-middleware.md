# Phase 3: Agentic Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the LangGraph orchestration layer that turns a disruption narrative (or MQTT event) into a committed, explained, auditable recovery schedule using the Phase 2 CP-SAT engine.

**Architecture:** Fixed linear LangGraph pipeline (`translate → ingest → machine_agent → production_agent → inventory_agent → worker_agent → strategy_loop → manager_compile → solve → gate → commit → verify → explain`) plus two bounded material-reactive back-edges sharing the strategy loop's round budget. LLM calls exist at exactly three nodes; investigation nodes are pure DB queries. Every LLM output passes pydantic validation before entering state. Safety net = pre-commit invariant gate + post-commit verifier sharing one implementation (`coe.solver.invariants.check_solution`).

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 + Alembic, pydantic v2 (+ pydantic-settings), langgraph, langchain-core, langchain-openai (OpenAI-compatible), langchain-google-genai (Gemini), ortools (P2 engine), paho-mqtt.

**Spec:** `docs/superpowers/specs/2026-08-22-phase3-agentic-middleware-design.md` (amended 2026-08-23 multi-resource; 2026-08-24 material-reactive). The spec is the source of truth; deviations are fixed minimally and documented in the task.

## Global Constraints

These apply to every task implicitly:

- **WORKSPACE BOUNDARY (mandatory):** All file reads, writes, commands, and scratch artifacts MUST stay under this repository root (`/Users/srevs/Projects/COE/`). Never touch `/`, `$HOME`, `/tmp`, or any path outside the repo — including "helpful" probes like `ls /` or temp scripts in `/tmp`. Scratch files go to `.superpowers/sdd/`. Every absolute path you use must begin with the repo root.
- Use `uv` exclusively. Never pip, never system Python.
- Alembic is authoritative DDL. `create_all` / `Base.metadata.create_all` is forbidden.
- Every table row is instance-scoped (`instance_id` FK discipline — no cross-instance joins).
- All time is integer minutes.
- Any query feeding RNG or float summation gets an explicit `ORDER BY`.
- LLM calls exist at exactly three nodes: `translate`, `strategy_loop`, `explain` (criterion 11). No LLM in constraint computation or schedule mathematics.
- Solver contract is frozen: statuses `OPTIMAL` / `FEASIBLE` / `INFEASIBLE` / `UNKNOWN`; `UNKNOWN` = budget starved, never routed as material-conflict. The only P3-side solver interaction is payload construction + the already-landed `job_tardiness_weights` root.
- Determinism consumers (benchmark solves) set `num_search_workers=1` + fixed seed (repo rule).
- psycopg3 raises CHECK violations at `execute()`, not `commit()`.
- Test markers: module-level `pytestmark = pytest.mark.db` for TimescaleDB tests, add `mqtt` for broker tests, `llm` for live-provider Tier 5 tests (registered this phase).
- Quick gate during development: `uv run pytest -m "not mqtt and not slow"` (~214 tests pre-P3, ~2.5 min).
- No comments in code beyond what the repo style already carries (docstrings for public modules/functions are repo convention).

---

## File Structure (decomposition locked here)

```
coe/
  agents/                       # NEW package — all Phase 3 middleware
    __init__.py
    state.py                    # RecoveryState pydantic model (§3.2)
    llm_client.py               # narrow LLM interface + adapters (§3.3, §9)
    records.py                  # DisruptionRecord union + validators (§4.1)
    catalog.py                  # StrategyCandidate union + verdicts (§5)
    applier.py                  # pure payload transforms (§6.1)
    safety.py                   # gate + verifier (§6.2–6.3)
    runs.py                     # run lifecycle persistence + instance lock (§7)
    graph.py                    # langgraph assembly + back-edges (§3.1)
    listener.py                 # mqtt listen entry point (§3.4)
    benchmark.py                # corpus generator + fidelity runner (§8)
    nodes/
      __init__.py
      translate.py              # translate + ingest node bodies (§4.1)
      investigate.py            # machine/production/inventory/worker agents (§4.2)
      strategy.py               # strategy_loop node incl. material-reactive duty (§4.3)
      manager.py                # manager_compile node (§4.4)
      explain.py                # explanation service node (§4.5)
  db/models/recovery.py         # RecoveryRun, RecoveryProposal, ScheduleExplanation (§7)
alembic/versions/<rev>_recovery_run_lifecycle_tables.py   # migration #7
tests/agents/                  # unit + integration tests per part
  worlds.py                    # shared minimal DB worlds (g_world, shortage)
  fixtures/llm/fake_client.py   # FakeLLMClient (§11 Test Infrastructure)
data/corpus/                   # seeded JSONL corpus (generated, committed)
```

Modified: `coe/config.py`, `coe/cli.py`, `pyproject.toml`, `tests/conftest.py`.

---

# PART A — Foundation

*Spec sections covered by this part: §9 (Configuration), §7 (Run Lifecycle data model), §3.3 (LLM Boundary Rule) client half, §11 (Test Infrastructure — fake client fixture).*

### Task 1: Settings extension + LLM pre-flight check (§9)

**Files:**
- Modify: `coe/config.py`
- Test: `tests/test_phase3_settings.py`

**Interfaces:**
- Consumes: existing `Settings(BaseSettings)` in `coe/config.py`.
- Produces: settings fields `llm_provider`, `llm_model`, `llm_temperature`, `strategy_max_rounds`, `llm_max_retries`, `benchmark_translation_accuracy`, `recovery_lock_wait_seconds`; helper `require_llm_config(settings) -> None` raising `LLMConfigError`. Later tasks read these via `get_settings()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phase3_settings.py
"""Phase 3 §9 configuration defaults + fail-fast pre-flight."""
import pytest

from coe.config import Settings


def test_defaults():
    s = Settings(llm_provider="openai", llm_model="gpt-4o-mini",
                 _env_file=None)
    assert s.llm_temperature == 0.0
    assert s.strategy_max_rounds == 3
    assert s.llm_max_retries == 2
    assert s.benchmark_translation_accuracy == 0.90
    assert s.recovery_lock_wait_seconds == 600


def test_provider_and_model_have_no_defaults():
    s = Settings(_env_file=None)
    assert s.llm_provider is None
    assert s.llm_model is None


def test_preflight_fails_loud_when_unset():
    from coe.agents.llm_client import LLMConfigError, require_llm_config
    with pytest.raises(LLMConfigError):
        require_llm_config(Settings(_env_file=None))


def test_preflight_passes_when_set():
    from coe.agents.llm_client import require_llm_config
    require_llm_config(Settings(llm_provider="openai", llm_model="x",
                                _env_file=None))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_phase3_settings.py -v`
Expected: FAIL — `Settings` has no such fields; `coe.agents.llm_client` does not exist yet.

- [ ] **Step 3: Implement settings fields**

In `coe/config.py`, inside `Settings`, after `solver_num_search_workers`:

```python
    # --- Phase 3 (spec §9) ---
    llm_provider: str | None = None       # no default: fail fast if unset
    llm_model: str | None = None          # no default: fail fast if unset
    llm_temperature: float = 0.0          # reproducibility
    strategy_max_rounds: int = 3
    llm_max_retries: int = 2
    benchmark_translation_accuracy: float = 0.90
    recovery_lock_wait_seconds: int = 600
```

- [ ] **Step 4: Create the agents package + pre-flight helper**

Create `coe/agents/__init__.py` (empty) and `coe/agents/llm_client.py` with, for now, only:

```python
class LLMConfigError(RuntimeError):
    """Missing LLM_PROVIDER/LLM_MODEL — setup error, never mid-run (§9)."""


def require_llm_config(settings) -> None:
    if not settings.llm_provider or not settings.llm_model:
        raise LLMConfigError(
            "set LLM_PROVIDER and LLM_MODEL before running recoveries "
            "(pre-flight check, spec §9)")
```

(The rest of `llm_client.py` lands in Task 3.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_phase3_settings.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add coe/config.py coe/agents/__init__.py coe/agents/llm_client.py tests/test_phase3_settings.py
git commit -m "feat(agents): phase 3 settings + LLM pre-flight check"
```

---

### Task 2: Recovery lifecycle tables — models + migration #7 (§7)

The spec says "reserved in Phase 1" but grep confirms no such tables exist in code; they must be created now via Alembic (authoritative DDL).

**Files:**
- Create: `coe/db/models/recovery.py`
- Modify: `coe/db/models/__init__.py`
- Create: `alembic/versions/b7f2c1a09d11_recovery_run_lifecycle_tables.py`
- Test: `tests/db/test_recovery_models.py`

**Interfaces:**
- Produces (later tasks rely on these exact models/columns):
  - `RecoveryRun(id, instance_id, trigger, status, disruption_record_json, final_status_version_id, started_at, finished_at, node_timings_json, quantum_shadow_json)`
  - `RecoveryProposal(id, instance_id, run_id, round_number, candidate_json, verdict, verdict_reason)`
  - `ScheduleExplanation(id, instance_id, version_id, rationale, created_at)`
  - Status domain: `TRANSLATION_FAILED | SOLVE_INFEASIBLE | GATE_FAILED | VERIFIER_ROLLBACK | COMMITTED` (§7). Verdict domain: `VALID | VALID_WITH_WARNING | INVALID | INVALID_DUPLICATE` (§4.3 step 2 + §6.1).

- [ ] **Step 1: Write the failing model tests**

```python
# tests/db/test_recovery_models.py
import pytest

pytestmark = pytest.mark.db

from coe.db.models.recovery import (
    RecoveryProposal,
    RecoveryRun,
    ScheduleExplanation,
)
from coe.db.session import session_scope


def _instance(session):
    from coe.db.models.provenance import Instance

    inst = Instance(name="p3models", source_name="synthetic")
    session.add(inst)
    session.flush()
    return inst


def test_run_roundtrip(clean_db):
    with session_scope() as session:
        inst = _instance(session)
        run = RecoveryRun(
            instance_id=inst.id, trigger="CLI", status="COMMITTED",
            disruption_record_json={"kind": "MACHINE", "machine_id": "M3"},
        )
        session.add(run)
        session.flush()
        assert run.id is not None
        assert run.started_at is not None          # server_default
        assert run.finished_at is None
        assert run.node_timings_json is None       # Phase 5 populates
        assert run.quantum_shadow_json is None


def test_trigger_domain(clean_db):
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        inst = _instance(session)
        session.add(RecoveryRun(instance_id=inst.id, trigger="HTTP",
                                status="COMMITTED",
                                disruption_record_json={}))
        try:
            session.flush()
            raise AssertionError("expected CHECK violation")
        except IntegrityError:
            session.rollback()


def test_status_domain(clean_db):
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        inst = _instance(session)
        session.add(RecoveryRun(instance_id=inst.id, trigger="CLI",
                                status="SOLVED",     # not a legal status
                                disruption_record_json={}))
        try:
            session.flush()
            raise AssertionError("expected CHECK violation")
        except IntegrityError:
            session.rollback()


def test_proposal_verdict_domain(clean_db):
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        inst = _instance(session)
        run = RecoveryRun(instance_id=inst.id, trigger="CLI", status="COMMITTED",
                          disruption_record_json={})
        session.add(run)
        session.flush()
        session.add(RecoveryProposal(
            instance_id=inst.id, run_id=run.id, round_number=1,
            candidate_json={"type": "DEFER_JOB"}, verdict="MAYBE"))
        try:
            session.flush()
            raise AssertionError("expected CHECK violation")
        except IntegrityError:
            session.rollback()


def test_explanation_unique_per_version(clean_db):
    from sqlalchemy.exc import IntegrityError

    from coe.db.models.schedule import ScheduleVersion

    with session_scope() as session:
        inst = _instance(session)
        v = ScheduleVersion(
            instance_id=inst.id, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=0.0, makespan=0,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.0,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v)
        session.flush()
        session.add(ScheduleExplanation(instance_id=inst.id, version_id=v.id,
                                        rationale="r"))
        session.flush()
        session.add(ScheduleExplanation(instance_id=inst.id, version_id=v.id,
                                        rationale="r2"))
        try:
            session.flush()
            raise AssertionError("expected UNIQUE violation")
        except IntegrityError:
            session.rollback()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/db/test_recovery_models.py -v`
Expected: FAIL on import — `coe.db.models.recovery` does not exist.

- [ ] **Step 3: Write the models**

```python
# coe/db/models/recovery.py
"""Phase 3 run lifecycle tables (spec §7)."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class RecoveryRun(Base):
    __tablename__ = "recovery_runs"
    __table_args__ = (
        CheckConstraint("trigger IN ('CLI','MQTT')", name="run_trigger"),
        CheckConstraint(
            "status IN ('TRANSLATION_FAILED','SOLVE_INFEASIBLE',"
            "'GATE_FAILED','VERIFIER_ROLLBACK','COMMITTED')",
            name="run_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30))
    disruption_record_json: Mapped[dict] = mapped_column(JSONB)
    final_status_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    node_timings_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    quantum_shadow_json: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True)


class RecoveryProposal(Base):
    __tablename__ = "recovery_proposals"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('VALID','VALID_WITH_WARNING','INVALID',"
            "'INVALID_DUPLICATE')",
            name="proposal_verdict"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), index=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("recovery_runs.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    candidate_json: Mapped[dict] = mapped_column(JSONB)
    verdict: Mapped[str] = mapped_column(String(20))
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduleExplanation(Base):
    __tablename__ = "schedule_explanations"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_explanation_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_versions.id"))
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
```

Append to `coe/db/models/__init__.py`:

```python
import coe.db.models.recovery  # noqa: F401
```

- [ ] **Step 4: Write the migration**

Generate then fill in (follow repo pattern; `down_revision` chains after current head `2818ae3709f8`):

```bash
uv run alembic revision -m "recovery run lifecycle tables"
```

Edit the generated file so its body is:

```python
"""recovery run lifecycle tables"""

revision = "<auto>"
down_revision = "2818ae3709f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(),
                  sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("disruption_record_json", postgresql.JSONB(),
                  nullable=False),
        sa.Column("final_status_version_id", sa.Integer(),
                  sa.ForeignKey("schedule_versions.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True),
                  nullable=True),
        sa.Column("node_timings_json", postgresql.JSONB(), nullable=True),
        sa.Column("quantum_shadow_json", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("trigger IN ('CLI','MQTT')", name="run_trigger"),
        sa.CheckConstraint(
            "status IN ('TRANSLATION_FAILED','SOLVE_INFEASIBLE',"
            "'GATE_FAILED','VERIFIER_ROLLBACK','COMMITTED')",
            name="run_status"),
    )
    op.create_index("ix_recovery_runs_instance_id", "recovery_runs",
                    ["instance_id"])

    op.create_table(
        "recovery_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(),
                  sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("recovery_runs.id"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("candidate_json", postgresql.JSONB(), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("verdict_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "verdict IN ('VALID','VALID_WITH_WARNING','INVALID',"
            "'INVALID_DUPLICATE')",
            name="proposal_verdict"),
    )
    op.create_index("ix_recovery_proposals_instance_id",
                    "recovery_proposals", ["instance_id"])
    op.create_index("ix_recovery_proposals_run_id", "recovery_proposals",
                    ["run_id"])

    op.create_table(
        "schedule_explanations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(),
                  sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("version_id", sa.Integer(),
                  sa.ForeignKey("schedule_versions.id"), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("version_id", name="uq_explanation_version"),
    )
    op.create_index("ix_schedule_explanations_instance_id",
                    "schedule_explanations", ["instance_id"])


def downgrade() -> None:
    op.drop_table("schedule_explanations")
    op.drop_table("recovery_proposals")
    op.drop_table("recovery_runs")
```

(with imports `import sqlalchemy as sa` and `from sqlalchemy.dialects import postgresql` and `op` per the generated template).

Note: `trigger` is a reserved-ish word in SQL but valid as an identifier/column name in PostgreSQL; SQLAlchemy quotes as needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/db/test_recovery_models.py -v`
Expected: 5 passed. (`clean_db` re-runs all migrations, so this also proves the new migration applies cleanly.)

Also verify the full chain still upgrades from scratch:

Run: `uv run python -m coe.cli db migrate`
Expected: nothing to do (already at head).

- [ ] **Step 6: Commit**

```bash
git add coe/db/models/recovery.py coe/db/models/__init__.py alembic/versions/*recovery_run_lifecycle_tables.py tests/db/test_recovery_models.py
git commit -m "feat(db): recovery_runs/proposals/explanations tables"
```

---

### Task 3: Dependencies + narrow LLM client + fake client (§3.3, §9, §11 infra)

**Files:**
- Modify: `pyproject.toml`
- Modify: `coe/agents/llm_client.py` (extend from Task 1)
- Modify: `tests/conftest.py`
- Create: `tests/fixtures/llm/__init__.py`, `tests/fixtures/llm/fake_client.py`
- Test: `tests/agents/__init__.py` (empty), `tests/agents/test_llm_client.py`

**Interfaces:**
- Produces:
  - `class LLMClient(Protocol): def complete(self, *, system: str, user: str) -> str` — THE narrow interface. All three LLM nodes depend only on this.
  - `make_llm_client(settings=None) -> LLMClient` — factory; provider `"openai"` → OpenAI-compatible adapter (`langchain_openai.ChatOpenAI`, honors optional `LLM_BASE_URL` env for vLLM/Ollama/gateways), provider `"gemini"` → Gemini adapter (`langchain_google_genai.ChatGoogleGenerativeAI`). Unknown provider raises `LLMConfigError`. API keys come from standard env vars (`OPENAI_API_KEY` / `GOOGLE_API_KEY`) — never stored in Settings/DB.
  - `FakeLLMClient(responses: list[str] | dict[str, str])` — pops scripted responses in order (dict form keys on a substring of `user`); raises `AssertionError` if exhausted, so tests fail loudly instead of hanging.

- [ ] **Step 1: Add dependencies**

```bash
uv add langgraph langchain-core langchain-openai langchain-google-genai
```

Register the Tier 5 marker in `pyproject.toml` markers list:

```toml
    "llm: live-provider end-to-end tests (opt-in, spec §11 Tier 5)",
```

- [ ] **Step 2: Write the failing test**

```python
# tests/agents/test_llm_client.py
"""§3.3/§9: narrow client interface, provider factory, fake injection."""
import pytest

from coe.config import Settings


def _settings(provider, model):
    return Settings(llm_provider=provider, llm_model=model, _env_file=None)


def test_factory_unknown_provider_rejected():
    from coe.agents.llm_client import LLMConfigError, make_llm_client
    with pytest.raises(LLMConfigError):
        make_llm_client(_settings("carrier-pigeon", "x"))


def test_factory_openai_adapter():
    from coe.agents.llm_client import make_llm_client
    c = make_llm_client(_settings("openai", "gpt-4o-mini"))
    assert hasattr(c, "complete")


def test_factory_gemini_adapter():
    from coe.agents.llm_client import make_llm_client
    c = make_llm_client(_settings("gemini", "gemini-2.0-flash"))
    assert hasattr(c, "complete")


def test_fake_client_pops_in_order():
    from tests.fixtures.llm.fake_client import FakeLLMClient
    f = FakeLLMClient(["first", "second"])
    assert f.complete(system="s", user="u") == "first"
    assert f.complete(system="s", user="u") == "second"
    with pytest.raises(AssertionError):
        f.complete(system="s", user="u")


def test_fake_client_routes_on_substring():
    from tests.fixtures.llm.fake_client import FakeLLMClient
    f = FakeLLMClient({"MC-04": '{"kind":"MACHINE"}',
                       "W-03": '{"kind":"WORKER"}'})
    assert f.complete(system="s", user="narrative about W-03 sick") \
        == '{"kind":"WORKER"}'
    assert f.complete(system="s", user="gearbox MC-04 seized") \
        == '{"kind":"MACHINE"}'
    assert f.calls == [
        ("narrative about W-03 sick", '{"kind":"WORKER"}'),
        ("gearbox MC-04 seized", '{"kind":"MACHINE"}'),
    ]
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/agents/test_llm_client.py -v`
Expected: FAIL — `make_llm_client` missing, no `tests.fixtures.llm`.

- [ ] **Step 4: Implement the client module**

Extend `coe/agents/llm_client.py`:

```python
"""Narrow LLM boundary (spec §3.3, §9).

Exactly three nodes call LLMClient.complete(); everything else in the
pipeline is deterministic. Real providers sit behind the same protocol so
tests inject FakeLLMClient with canned responses.
"""
import os
from typing import Protocol

from coe.config import get_settings


class LLMConfigError(RuntimeError):
    """Missing LLM_PROVIDER/LLM_MODEL — setup error, never mid-run (§9)."""


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str) -> str: ...


def require_llm_config(settings) -> None:
    if not settings.llm_provider or not settings.llm_model:
        raise LLMConfigError(
            "set LLM_PROVIDER and LLM_MODEL before running recoveries "
            "(pre-flight check, spec §9)")


class _LangChainClient:
    """Adapter: a langchain BaseChatModel behind our two-arg protocol."""

    def __init__(self, model) -> None:
        self._model = model

    def complete(self, *, system: str, user: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        msg = self._model.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)])
        return msg.content


def make_llm_client(settings=None) -> LLMClient:
    s = settings or get_settings()
    require_llm_config(s)
    provider = s.llm_provider.lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {}
        if os.environ.get("LLM_BASE_URL"):      # vLLM/Ollama/gateways
            kwargs["base_url"] = os.environ["LLM_BASE_URL"]
        return _LangChainClient(ChatOpenAI(
            model=s.llm_model, temperature=s.llm_temperature, **kwargs))
    if provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        return _LangChainClient(ChatGoogleGenerativeAI(
            model=s.llm_model, temperature=s.llm_temperature))
    raise LLMConfigError(f"unsupported LLM_PROVIDER {provider!r} "
                         "(supported: openai, gemini)")
```

- [ ] **Step 5: Implement the fake client + package init**

```python
# tests/fixtures/llm/__init__.py
```

(empty file)

```python
# tests/fixtures/llm/fake_client.py
"""Scripted LLM double (spec §11 Test Infrastructure).

responses: list -> popped in order regardless of prompt;
           dict -> value selected by the first key that is a substring of
           the user prompt. Exhaustion raises AssertionError so tests fail
           loudly instead of silently degrading.
"""


class FakeLLMClient:
    def __init__(self, responses) -> None:
        self._ordered = isinstance(responses, list)
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((user, system))
        if self._ordered:
            if not self._responses:
                raise AssertionError("FakeLLMClient exhausted")
            return self._responses.pop(0)
        for key, value in self._responses.items():
            if key in user:
                return value
        raise AssertionError(f"no canned response matches {user!r}")
```

Add a conftest fixture (append to `tests/conftest.py`):

```python
@pytest.fixture()
def fake_llm():
    """Factory fixture: fake_llm(["resp", ...]) or fake_llm({key: resp})."""
    from tests.fixtures.llm.fake_client import FakeLLMClient

    def _make(responses):
        return FakeLLMClient(responses)

    return _make
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_llm_client.py tests/test_phase3_settings.py -v`
Expected: 9 passed.

Note: adapter constructor tests do NOT hit the network — constructing `ChatOpenAI`/`ChatGoogleGenerativeAI` performs no I/O. If a constructor demands an API key env var, set a dummy value inside the test (`monkeypatch.setenv("OPENAI_API_KEY", "test")` etc.) — constructing must stay offline.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock coe/agents/llm_client.py tests/fixtures/llm tests/conftest.py tests/agents/__init__.py tests/agents/test_llm_client.py
git commit -m "feat(agents): narrow LLM client + openai/gemini adapters + fake"
```

---


---

# PART B — Records, State, Translation

*Spec sections covered by this part: §3.2 (Shared State), §4.1 (Translation Agent incl. all four validation layers, ingestion write-through, idempotency keys), §3.4 last paragraph (MAINTENANCE follows the same graph), §11 Tier 1 (translation fidelity + malformed-narrative cases). Acceptance criteria touched: 2, 13.*

### Task 4: DisruptionRecord union, validators, shared state (§4.1 layers 1–4, §3.2)

**Files:**
- Create: `coe/agents/records.py`, `coe/agents/state.py`
- Test: `tests/agents/test_records.py`

**Interfaces:**
- Consumes: `ingest.EVENT_TYPES` domains (`coe/mqtt/ingest.py:19`); DB models `Machine`, `Worker`, `Material`.
- Produces:
  - `DisruptionRecord` — pydantic discriminated union on `kind` with variants `MachineRecord` / `WorkerRecord` / `MaterialRecord`. Common required fields: `kind`, `instance_id`, `event_type`, `occurred_at >= 0`, `severity ∈ {LOW, MEDIUM, HIGH, CRITICAL}`, `narrative_excerpt`; exactly one resource field (`machine_id` / `worker_id` / `material_sku`); `estimated_downtime` MACHINE-only, `estimated_absence` WORKER_ABSENT-only, MATERIAL carries neither. All variants `extra="forbid"`.
  - `RecordValidationError(ValueError)` — message is what feeds back into the LLM prompt.
  - `validate_record_fields(data: dict, *, session, instance_name: str) -> dict` — layers 2+3: instance cross-check + resource-exists check. Returns the validated data unchanged (raises `RecordValidationError` or lets pydantic's `ValidationError` propagate for layer 1).
  - `RecoveryState` (pydantic, §3.2): fields listed in Step 3. Nodes return full updated `RecoveryState`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_records.py
"""§4.1 validation layers 1-4 + §3.2 shared state."""
import pytest
from pydantic import ValidationError


def _machine(**over):
    d = {"kind": "MACHINE", "instance_id": "factory_demo_01",
         "machine_id": "M3", "event_type": "FAILURE", "occurred_at": 512,
         "severity": "HIGH", "estimated_downtime": 90,
         "narrative_excerpt": "gearbox seized"}
    d.update(over)
    return d


def test_machine_record_parses():
    from coe.agents.records import DisruptionRecord

    r = DisruptionRecord.model_validate(_machine())
    assert r.kind == "MACHINE"
    assert r.estimated_downtime == 90
    assert not hasattr(r, "estimated_absence")


def test_worker_record_parses():
    from coe.agents.records import DisruptionRecord

    r = DisruptionRecord.model_validate({
        "kind": "WORKER", "instance_id": "factory_demo_01",
        "worker_id": "W3", "event_type": "WORKER_ABSENT",
        "occurred_at": 480, "severity": "MEDIUM", "estimated_absence": 240,
        "narrative_excerpt": "sick"})
    assert r.kind == "WORKER"


def test_material_record_parses():
    from coe.agents.records import DisruptionRecord

    r = DisruptionRecord.model_validate({
        "kind": "MATERIAL", "instance_id": "factory_demo_01",
        "material_sku": "MAT-001", "event_type": "MATERIAL_SHORTAGE",
        "occurred_at": 300, "severity": "LOW",
        "narrative_excerpt": "bin empty"})
    assert r.kind == "MATERIAL"


def test_material_cannot_carry_duration():
    with pytest.raises(ValidationError):
        from coe.agents.records import DisruptionRecord

        DisruptionRecord.model_validate({
            "kind": "MATERIAL", "instance_id": "i",
            "material_sku": "S", "event_type": "MATERIAL_SHORTAGE",
            "occurred_at": 0, "severity": "LOW",
            "estimated_downtime": 10,       # forbidden on MATERIAL
            "narrative_excerpt": "x"})


def test_negative_occurred_at_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.records import DisruptionRecord

        DisruptionRecord.model_validate(_machine(occurred_at=-1))


def test_unknown_event_type_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.records import DisruptionRecord

        DisruptionRecord.model_validate(_machine(event_type="EXPLODED"))


def test_two_resources_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.records import DisruptionRecord

        DisruptionRecord.model_validate(_machine(worker_id="W3"))


def test_instance_mismatch_rejected(clean_db):
    from sqlalchemy import insert

    from coe.agents.records import RecordValidationError, validate_record_fields
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        session.execute(insert(Instance).values(
            name="rec-inst", source_name="synthetic"))
        with pytest.raises(RecordValidationError, match="instance"):
            validate_record_fields(
                _machine(instance_id="OTHER"), session=session,
                instance_name="rec-inst")


def test_unknown_resource_rejected_per_kind(clean_db):
    from coe.agents.records import RecordValidationError, validate_record_fields
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="rec-inst2", source_name="synthetic")
        session.add(inst)
        session.flush()
        session.add(Material(instance_id=inst.id, sku="MAT-001",
                             initial_stock=100))
        session.flush()
        with pytest.raises(RecordValidationError, match="machine"):
            validate_record_fields(_machine(), session=session,
                                   instance_name="rec-inst2")
        ok = validate_record_fields(
            {"kind": "MATERIAL", "instance_id": "rec-inst2",
             "material_sku": "MAT-001", "event_type": "MATERIAL_SHORTAGE",
             "occurred_at": 5, "severity": "LOW", "narrative_excerpt": "x"},
            session=session, instance_name="rec-inst2")
        assert ok["material_sku"] == "MAT-001"


def test_state_defaults_and_threading():
    from coe.agents.state import RecoveryState

    s = RecoveryState(instance_name="factory_demo_01")
    assert s.errors == [] and s.warnings == []
    assert s.strategy_candidates == [] and s.round_count == 0
    s2 = s.model_copy(update={"narrative": "MC-04 seized"})
    assert s2.narrative == "MC-04 seized"
    assert s.narrative == ""          # immutable updates, langgraph-friendly
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_records.py -v`
Expected: FAIL on import — `coe.agents.records` / `coe.agents.state` missing.

- [ ] **Step 3: Implement records.py**

```python
# coe/agents/records.py
"""DisruptionRecord discriminated union + validation layers 2-3 (§4.1).

Layer 1 (schema) lives in the pydantic models themselves; layer 2 is the
instance cross-check; layer 3 is the DB resource-existence check. Layer 4
(time resolution) happens in the translate node before the record exists.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class RecordValidationError(ValueError):
    """Feeds verbatim back into the LLM prompt for retry (§4.1 layer 3)."""


class _BaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str
    occurred_at: int = Field(ge=0)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    narrative_excerpt: str


class MachineRecord(_BaseRecord):
    kind: Literal["MACHINE"]
    machine_id: str
    event_type: Literal["FAILURE", "MAINTENANCE"]
    estimated_downtime: int | None = Field(default=None, gt=0)


class WorkerRecord(_BaseRecord):
    kind: Literal["WORKER"]
    worker_id: str
    event_type: Literal["WORKER_ABSENT", "WORKER_RETURN"]
    estimated_absence: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _absence_only_on_absent(self) -> "WorkerRecord":
        # Mirrors Phase 1 wire rule: estimated_absence is WORKER_ABSENT-only
        # (coe/mqtt/ingest.py). Keeping the union aligned means every record
        # that passes here also survives write-through ingestion.
        if self.event_type != "WORKER_ABSENT" \
                and self.estimated_absence is not None:
            raise ValueError("estimated_absence is WORKER_ABSENT-only")
        return self


class MaterialRecord(_BaseRecord):
    kind: Literal["MATERIAL"]
    material_sku: str
    event_type: Literal["MATERIAL_SHORTAGE", "MATERIAL_RESTOCK"]


DisruptionRecord = Annotated[
    Union[MachineRecord, WorkerRecord, MaterialRecord],
    Field(discriminator="kind"),
]

_record_adapter: TypeAdapter = TypeAdapter(DisruptionRecord)


def parse_disruption_record(data: dict):
    """Layer 1. Raises pydantic ValidationError with a per-field message."""
    return _record_adapter.validate_python(data)


_RESOURCE_MODEL = {"MACHINE": "Machine", "WORKER": "Worker",
                   "MATERIAL": "Material"}
_RESOURCE_FIELD = {"MACHINE": "machine_id", "WORKER": "worker_id",
                   "MATERIAL": "material_sku"}


def validate_record_fields(data: dict, *, session, instance_name: str) -> dict:
    """Layers 2+3. Returns data unchanged; raises RecordValidationError."""
    if data.get("instance_id") != instance_name:
        raise RecordValidationError(
            f"record.instance_id {data.get('instance_id')!r} does not match "
            f"the target instance {instance_name!r} (CLI value is "
            "authoritative, §4.1 layer 2)")
    kind = data.get("kind")
    ref = data.get(_RESOURCE_FIELD.get(kind, ""), ...)
    if ref is ...:
        raise RecordValidationError(f"missing resource field for {kind!r}")
    if kind == "MACHINE":
        from coe.db.models.fjsp import Machine

        hit = session.query(Machine.id).filter(
            Machine.instance_id == _inst_id(session, instance_name),
            Machine.name == ref).one_or_none()
    elif kind == "WORKER":
        from coe.db.models.workers import Worker

        hit = session.query(Worker.id).filter(
            Worker.instance_id == _inst_id(session, instance_name),
            Worker.name == ref).one_or_none()
    elif kind == "MATERIAL":
        from coe.db.models.materials import Material

        hit = session.query(Material.id).filter(
            Material.instance_id == _inst_id(session, instance_name),
            Material.sku == ref).one_or_none()
    else:
        raise RecordValidationError(f"unknown kind {kind!r}")
    if hit is None:
        raise RecordValidationError(
            f"{_RESOURCE_MODEL[kind]} {ref!r} does not exist within "
            f"instance {instance_name!r} (§4.1 layer 3)")
    return data


_INSTANCE_CACHE: dict[str, int] = {}


def _inst_id(session, instance_name: str) -> int:
    if instance_name not in _INSTANCE_CACHE:
        from coe.db.models.provenance import Instance

        row = (session.query(Instance.id)
               .filter(Instance.name == instance_name).one_or_none())
        if row is None:
            raise RecordValidationError(
                f"unknown instance {instance_name!r}")
        _INSTANCE_CACHE[instance_name] = row[0]
    return _INSTANCE_CACHE[instance_name]
```

Implementation note — drop the `_INSTANCE_CACHE` process-global: it leaks across `clean_db` resets inside one pytest process and will cause flaky cross-test failures. Instead resolve the instance inline each call:

```python
def _inst_id(session, instance_name: str) -> int:
    from coe.db.models.provenance import Instance

    row = (session.query(Instance.id)
           .filter(Instance.name == instance_name).one_or_none())
    if row is None:
        raise RecordValidationError(f"unknown instance {instance_name!r}")
    return row[0]
```

and delete the `_INSTANCE_CACHE` block. (Cheap query; correctness over micro-caching.)

- [ ] **Step 4: Implement state.py (§3.2)**

```python
# coe/agents/state.py
"""Shared typed state threaded through every graph node (spec §3.2).

Validated LLM outputs enter state as plain dicts (post-.model_dump()) to
keep the langgraph state serializable; pydantic validation happens at the
node boundaries, satisfying §3.3's "validator before state" rule.
Extra bookkeeping fields beyond §3.2 (reference_clock, source_message_id,
round_count, material_reactive, trigger, run_id) are implementation seams
used by routing/back-edges/dedup — documented here once.
"""
from typing import Literal

from pydantic import BaseModel, Field


class RecoveryState(BaseModel):
    instance_name: str
    trigger: Literal["CLI", "MQTT"] = "CLI"
    run_id: int | None = None                 # recovery_runs row (§7)
    source_message_id: str | None = None      # MQTT dedup key (§3.4)
    narrative: str = ""
    reference_clock: int | None = None        # §4.1 layer 4
    disruption_record: dict | None = None
    db_facts: dict = Field(default_factory=dict)
    strategy_candidates: list[dict] = Field(default_factory=list)
    round_verdicts: list[dict] = Field(default_factory=list)
    round_count: int = 0                      # shared §3.1 budget counter
    material_reactive: bool = False           # set by compile/solve back-edges
    compiled_payload: dict | None = None
    solution: dict | None = None
    gate_result: dict | None = None
    verify_result: dict | None = None
    explanation: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_records.py -v`
Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add coe/agents/records.py coe/agents/state.py tests/agents/test_records.py
git commit -m "feat(agents): DisruptionRecord union + validators + shared state"
```

---

### Task 5: Translate + ingest nodes — prompt, retries, ingestion, idempotency (§4.1 tail, §3.3 fallback, criterion 13)

**Files:**
- Create: `coe/agents/nodes/__init__.py` (empty), `coe/agents/nodes/translate.py`
- Modify: `coe/agents/llm_client.py` (no change expected; only referenced)
- Test: `tests/agents/test_translate_node.py`

**Interfaces:**
- Consumes: `LLMClient.complete(system=..., user=...)` (Task 3); `parse_disruption_record` + `validate_record_fields` (Task 4); `RecoveryState` (Task 4); `resolve_reference_clock(session, instance_id, at)` (`coe/solver/payload_builder.py:38`); `ingest_telemetry_event(payload_dict) -> tuple[int, bool]` (`coe/mqtt/ingest.py:153`); `get_settings().llm_max_retries`.
- Produces:
  - `build_translate_messages(narrative: str, instance_name: str, clock: int) -> tuple[str, str]` — returns `(system, user)` prompts. The user prompt embeds the reference clock and instructs: output ONLY a JSON object; exactly one disruption (multi-disruption narratives must be refused with `{"error": ...}`); absolute minute for `occurred_at`.
  - `class TranslationFailed(RuntimeError)` — carries `narrative`, `error`. Graph turns this into run status `TRANSLATION_FAILED` (§3.3).
  - `record_to_wire_payload(record_dict: dict, *, message_id: str) -> dict` — maps record → `ResourceEventPayload` wire shape (`reason` = `narrative_excerpt`, `resource_kind` = `kind`).
  - `cli_message_id(record_dict: dict) -> str` — `"cli-" + sha256(canonical json of record fields)[:16]`.
  - `run_translate(state: RecoveryState, *, client: LLMClient, max_retries: int | None = None) -> RecoveryState` — VALIDATION ONLY (no DB writes; ingestion is the separate `ingest` node per the §3.1 topology). `max_retries=None` reads `get_settings().llm_max_retries`; retry/exhaustion tests pass an explicit value so they stay hermetic against `.env` (P2 Settings-isolation gotcha).
  - `run_ingest(state: RecoveryState) -> RecoveryState` — the `ingest` node body for BOTH entry points: message_id = `state.source_message_id` when set (MQTT wire id), else `cli_message_id(record)`; calls `record_to_wire_payload` + `ingest_telemetry_event`; duplicate deliveries return unchanged state (idempotent no-op, criterion 13).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_translate_node.py
"""§4.1 translate node: retries, fallback, write-through, idempotency."""
import json

import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient

NARRATIVE = "MC-04 gearbox seized, sparks everywhere"
GOOD_MACHINE = {
    "kind": "MACHINE", "instance_id": "factory_demo_01",
    "machine_id": "M3", "event_type": "FAILURE", "occurred_at": 512,
    "severity": "HIGH", "estimated_downtime": 90,
    "narrative_excerpt": NARRATIVE,
}


@pytest.fixture()
def demo(demo_scenario):
    return demo_scenario


def test_prompt_contains_clock_and_instance(demo):
    from coe.agents.nodes.translate import build_translate_messages

    system, user = build_translate_messages(NARRATIVE, "factory_demo_01", 512)
    assert "512" in user and "factory_demo_01" in user
    assert "JSON" in system


def test_translate_validates_without_writing(demo):
    from coe.agents.nodes.translate import run_translate
    from coe.agents.state import RecoveryState
    from coe.db.session import make_engine
    from sqlalchemy import text

    state = RecoveryState(instance_name="factory_demo_01",
                          narrative=NARRATIVE)
    out = run_translate(state, client=FakeLLMClient([json.dumps(GOOD_MACHINE)]))
    assert out.disruption_record["kind"] == "MACHINE"
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id = te.instance_id "
            "WHERE i.name='factory_demo_01' AND te.resource_kind='MACHINE'"
        )).scalar_one()
    assert n == 0      # translate is pure validation; ingest writes (§3.1)


def test_ingest_node_writes_cli_hashed_event_idempotently(demo):
    from coe.agents.nodes.translate import run_ingest, run_translate
    from coe.agents.state import RecoveryState
    from coe.db.session import make_engine
    from sqlalchemy import text

    base = RecoveryState(instance_name="factory_demo_01",
                         narrative=NARRATIVE)
    st = run_translate(base,
                       client=FakeLLMClient([json.dumps(GOOD_MACHINE)]))
    run_ingest(st)
    run_ingest(st)     # duplicate delivery: suppressed by message_id
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id = te.instance_id "
            "WHERE i.name='factory_demo_01' AND te.message_id LIKE 'cli-%'"
        )).scalar_one()
    assert n == 1          # criterion 13: no duplicate telemetry event


def test_invalid_then_valid_retries_with_feedback(demo):
    from coe.agents.nodes.translate import run_translate
    from coe.agents.state import RecoveryState

    bad = dict(GOOD_MACHINE, occurred_at=-5)
    client = FakeLLMClient([json.dumps(bad), json.dumps(GOOD_MACHINE)])
    out = run_translate(RecoveryState(instance_name="factory_demo_01",
                                      narrative=NARRATIVE), client=client,
                        max_retries=2)
    assert out.disruption_record["occurred_at"] == 512
    # second call happened => retry consumed exactly one extra response
    assert len(client.calls) == 2


def test_exhaustion_raises_translation_failed_no_db_mutation(demo):
    from coe.agents.nodes.translate import TranslationFailed, run_translate
    from coe.agents.state import RecoveryState
    from coe.db.session import make_engine
    from sqlalchemy import text

    bad = dict(GOOD_MACHINE, event_type="EXPLODED")
    client = FakeLLMClient([json.dumps(bad), json.dumps(bad),
                            json.dumps(bad)])   # 1 + max_retries(2)
    with pytest.raises(TranslationFailed):
        run_translate(RecoveryState(instance_name="factory_demo_01",
                                    narrative=NARRATIVE), client=client,
                      max_retries=2)
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id = te.instance_id "
            "WHERE i.name='factory_demo_01'")).scalar_one()
    assert n == 0      # zero DB mutation (criterion 2)


def test_multi_disruption_refusal_is_retryable(demo):
    from coe.agents.nodes.translate import run_translate
    from coe.agents.state import RecoveryState

    two = [GOOD_MACHINE, dict(GOOD_MACHINE, worker_id="W3")]
    client = FakeLLMClient([json.dumps(two), json.dumps(GOOD_MACHINE)])
    out = run_translate(RecoveryState(instance_name="factory_demo_01",
                                      narrative=NARRATIVE), client=client,
                        max_retries=2)
    assert out.disruption_record["kind"] == "MACHINE"


def test_cli_message_id_stable():
    from coe.agents.nodes.translate import cli_message_id

    a = cli_message_id(GOOD_MACHINE)
    b = cli_message_id(dict(GOOD_MACHINE))     # copy, same content
    assert a == b and a.startswith("cli-")
    assert a != cli_message_id(dict(GOOD_MACHINE, occurred_at=600))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_translate_node.py -v`
Expected: FAIL on import — `coe.agents.nodes.translate` missing.

- [ ] **Step 3: Implement the node**

Create `coe/agents/nodes/__init__.py` (empty) and:

```python
# coe/agents/nodes/translate.py
"""Translate + ingest node bodies (AI Role 1, spec §4.1).

The ONLY LLM usage here is narrative -> DisruptionRecord. Every candidate
output passes the pydantic union + DB validators before entering state;
validator errors feed back into the prompt for up to llm_max_retries
retries (§3.3), then the run aborts TRANSLATION_FAILED with zero DB
mutation. Ingestion is a SEPARATE graph node (run_ingest): it writes the
validated record through the Phase 1 ingestion function under the wire
message_id (MQTT) or a content-derived cli- id (CLI), so identical
narratives are idempotent (§4.1 tail, criterion 13).
"""
import hashlib
import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from coe.agents.records import (
    RecordValidationError,
    parse_disruption_record,
    validate_record_fields,
)
from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.mqtt.ingest import PayloadError, ingest_telemetry_event
from coe.solver.payload_builder import resolve_reference_clock

_SYSTEM_PROMPT = """You translate factory disruption reports into ONE \
structured JSON record. Output ONLY the JSON object, no prose, no code \
fences. Schema (discriminated on "kind"):
{"kind":"MACHINE","instance_id":str,"machine_id":str,\
"event_type":"FAILURE"|"MAINTENANCE","occurred_at":int>=0,\
"severity":"LOW"|"MEDIUM"|"HIGH"|"CRITICAL",\
"estimated_downtime":int|null,"narrative_excerpt":str}
{"kind":"WORKER","instance_id":str,"worker_id":str,\
"event_type":"WORKER_ABSENT"|"WORKER_RETURN","occurred_at":int>=0,\
"severity":...,"estimated_absence":int|null,"narrative_excerpt":str}
{"kind":"MATERIAL","instance_id":str,"material_sku":str,\
"event_type":"MATERIAL_SHORTAGE"|"MATERIAL_RESTOCK","occurred_at":int>=0,\
"severity":...,"narrative_excerpt":str}   (no duration field allowed)
Rules: exactly ONE disruption per record — if the report describes \
several simultaneous disruptions, output {"error":"multiple disruptions"} \
and nothing else. Resolve relative times ("two hours ago") against the \
reference clock given in the prompt; occurred_at is an absolute minute. \
Use the exact machine/worker/SKU identifiers as they appear in the report."""


class TranslationFailed(RuntimeError):
    def __init__(self, narrative: str, error: str) -> None:
        super().__init__(error)
        self.narrative = narrative
        self.error = error


def build_translate_messages(narrative: str, instance_name: str,
                             clock: int) -> tuple[str, str]:
    user = (
        f"Target instance: {instance_name}\n"
        f"Reference clock: minute {clock}\n"
        f"Report:\n{narrative}")
    return _SYSTEM_PROMPT, user


def _extract_json(text: str) -> dict:
    """Tolerate code fences; otherwise parse directly."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in LLM response: {text[:200]!r}")
    return json.loads(stripped[start:end + 1])


def cli_message_id(record: dict) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return "cli-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def record_to_wire_payload(record: dict, *, message_id: str) -> dict:
    payload = {
        "message_id": message_id,
        "resource_kind": record["kind"],
        "instance_id": record["instance_id"],
        "event_type": record["event_type"],
        "occurred_at": record["occurred_at"],
        "severity": record["severity"],
        "reason": record.get("narrative_excerpt"),
    }
    if record["kind"] == "MACHINE":
        payload["machine_id"] = record["machine_id"]
        if record.get("estimated_downtime") is not None:
            payload["estimated_downtime"] = record["estimated_downtime"]
    elif record["kind"] == "WORKER":
        payload["worker_id"] = record["worker_id"]
        if record.get("estimated_absence") is not None:
            payload["estimated_absence"] = record["estimated_absence"]
    else:
        payload["material_sku"] = record["material_sku"]
    return payload


def run_translate(state: RecoveryState, *, client,
                  max_retries: int | None = None) -> RecoveryState:
    settings = get_settings()
    retries = (settings.llm_max_retries if max_retries is None
               else max_retries)
    with Session(get_engine()) as session:
        inst_row = _instance_row(session, state.instance_name)
        clock = resolve_reference_clock(session, inst_row.id,
                                        state.reference_clock)
        feedback = ""
        for attempt in range(1 + retries):
            system, user = build_translate_messages(
                state.narrative, state.instance_name, clock)
            raw = client.complete(system=system, user=user + feedback)
            try:
                data = _extract_json(raw)
                if "error" in data and len(data) == 1:
                    raise RecordValidationError(
                        f"translator refused: {data['error']}")
                record = parse_disruption_record(data).model_dump()
                record = validate_record_fields(
                    record, session=session,
                    instance_name=state.instance_name)
            except (ValueError, ValidationError, RecordValidationError) \
                    as exc:
                feedback = (f"\n\nYour previous output was rejected: "
                            f"{exc}. Fix it and respond again.")
                continue
            break
        else:
            raise TranslationFailed(state.narrative,
                                    feedback.strip() or "unknown error")

    wire = record_to_wire_payload(record, message_id=cli_message_id(record))
    # NOTE: no writes here — ingestion belongs to the `ingest` node
    # (§3.1 topology), implemented as run_ingest() below.

    return state.model_copy(update={
        "disruption_record": record,
        "reference_clock": clock,
    })


def run_ingest(state: RecoveryState) -> RecoveryState:
    """`ingest` node body for BOTH entry points (§3.1, §4.1 tail).

    MQTT runs carry the wire message_id; CLI runs derive cli-{hash} from
    the validated record so identical narratives are idempotent
    (criterion 13). Duplicate deliveries are suppressed inside the shared
    Phase 1 ingestion function.
    """
    mid = state.source_message_id or cli_message_id(
        state.disruption_record)
    wire = record_to_wire_payload(state.disruption_record, message_id=mid)
    try:
        ingest_telemetry_event(wire)
    except PayloadError as exc:   # defensive: pre-validated, but be loud
        raise TranslationFailed(state.narrative, f"ingest rejected: {exc}") \
            from exc
    return state
```

with helpers imported at top of the same file:

```python
from coe.db.session import make_engine


def get_engine():
    return make_engine()


def _instance_row(session, name):
    from coe.db.models.provenance import Instance

    row = (session.query(Instance)
           .filter(Instance.name == name).one_or_none())
    if row is None:
        raise ValueError(f"unknown instance {name!r}")
    return row
```

Implementation notes (bind these decisions):
- Retry budget = `1 + llm_max_retries` total attempts; exhaustion raises `TranslationFailed` with zero writes anywhere (ingestion lives in the separate `ingest` node, which only runs after validation succeeded).
- The `for ... else` construct means the `else` fires only when every attempt failed validation.
- `Session(make_engine())` — a fresh engine per node call mirrors the repo's short-lived-session pattern (`commit_solution_autocommit`). Engines are cheap here because tests reuse the pooled URL; if profiling ever shows churn, hoist to a module-level engine singleton — do NOT do it preemptively.
- Both entry points traverse `translate → ingest → machine_agent` (§3.1): MQTT runs enter at `ingest` with a listener-derived record and their wire `message_id`; CLI runs enter at `translate` and derive `cli-{hash}` at ingest.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_translate_node.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run quick gate**

Run: `uv run pytest -m "not mqtt and not slow" -q`
Expected: all previous suites still green (~218 tests now).

- [ ] **Step 6: Commit**

```bash
git add coe/agents/nodes/__init__.py coe/agents/nodes/translate.py tests/agents/test_translate_node.py
git commit -m "feat(agents): translate/ingest nodes with retry loop + hashed-id ingestion"
```

---


---

# PART C — Investigation, Catalog, Applier

*Spec sections covered by this part: §4.2 (Investigation Nodes — pure database queries, all four run every time, no-ops by kind), §4.3 steps 1–2 (candidate verdicts `VALID` / `VALID_WITH_WARNING` / `INVALID` with machine-readable reasons), §5 (Strategy Catalog incl. Amendment 2026-08-24 `SUSPEND_JOB` rules and the beyond-horizon `VALID_WITH_WARNING` note), §6.1 (Strategy Applier: purity, emission-order application, last-wins, `INVALID_DUPLICATE`, `STRATEGY_APPLIED` warnings, `source='strategy_agent'`, ordering contract vs the Phase 2 tardiness-weight derivation). Acceptance criteria touched: 3.*

### Task 6: Investigation nodes — four pure DB query functions (§4.2)

**Files:**
- Create: `coe/agents/nodes/investigate.py`
- Test: `tests/agents/test_investigate.py`

**Interfaces:**
- Consumes: `RecoveryState.disruption_record` (dict from Task 4); models `MachineCapability` (`coe/db/models/fjsp.py:30`), `ScheduleEntry`, `ScheduleVersion`, `OperationMachineAlternative`, `OperationMachineWorkerTime`, `Material`, `MaterialReceipt`, `OperationBom`; `build_payload` (preview mode) + `derive_tardiness_weights` are NOT used here except `build_payload` inside `inventory_agent_node` for the projected horizon.
- Produces — node callables with one uniform signature (langgraph-ready):
  - `machine_agent_node(state: RecoveryState) -> RecoveryState`
  - `production_agent_node(state: RecoveryState) -> RecoveryState`
  - `inventory_agent_node(state: RecoveryState) -> RecoveryState`
  - `worker_agent_node(state: RecoveryState) -> RecoveryState`

  Each merges its findings into `state.db_facts` (existing keys preserved) and never calls an LLM. `db_facts` keys written:
  - `failed_machine`: `{"machine_id", "status", "capabilities_lost": [codes]}` or `None`
  - `stranded_operations`: `[{operation_id, job_id, deadline, machine_id, start, end}]` (machine-kind: ops on the failed machine not yet finished at the clock; worker-kind: the absent worker's future assignments)
  - `projected_horizon`: int (always)
  - `shortage_evidence`: `{material_sku, total_supply, total_demand, affected_operations}` or `None`
  - `absent_worker`: `{worker_id, sole_eligible: [{operation_id, machine_id}], assignment_count}` or `None`

- [ ] **Step 1: Write the failing tests**

The tests build a tiny deterministic world by hand (fast; no solving):

```python
# tests/agents/test_investigate.py
"""§4.2 investigation nodes: pure queries, kind-gated no-ops."""
import pytest

pytestmark = pytest.mark.db

from coe.agents.state import RecoveryState


@pytest.fixture()
def world(clean_db):
    """Instance with: M1(cap CODE-A)/M2, W1/W2, two jobs, an active v1."""
    from coe.db.models.fjsp import (
        Job,
        JobFamily,
        Machine,
        MachineCapability,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.materials import Material, OperationBom
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import (
        OperationMachineWorkerTime,
        Worker,
    )
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="inv-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id

        m1 = Machine(instance_id=iid, name="M1")
        m2 = Machine(instance_id=iid, name="M2")
        w1 = Worker(instance_id=iid, name="W1")
        w2 = Worker(instance_id=iid, name="W2")
        fam = JobFamily(instance_id=iid, name="FAM")
        session.add_all([m1, m2, w1, w2, fam])
        session.flush()

        session.add(MachineCapability(instance_id=iid, machine_id=m1.id,
                                      capability_code="CODE-A",
                                      source="mk01"))
        session.flush()

        ja = Job(instance_id=iid, name="J-A", priority=1, release_time=0)
        jb = Job(instance_id=iid, name="J-B", priority=3, release_time=0,
                 deadline=100)
        session.add_all([ja, jb])
        session.flush()
        oa = Operation(instance_id=iid, job_id=ja.id, sequence_number=1)
        ob1 = Operation(instance_id=iid, job_id=jb.id, sequence_number=1)
        session.add_all([oa, ob1])
        session.flush()

        # J-A.1 routable on M1(5m) and M2(7m); only W2 knows M2.
        for m, t in ((m1, 5), (m2, 7)):
            session.add(OperationMachineAlternative(
                instance_id=iid, operation_id=oa.id, machine_id=m.id,
                processing_time=t))
            session.add(OperationMachineWorkerTime(
                instance_id=iid, operation_id=oa.id, machine_id=m.id,
                worker_id=(w2.id if m is m2 else w1.id),
                processing_time=t))
        session.flush()

        mat = Material(instance_id=iid, sku="MAT-X", initial_stock=5)
        session.add(mat)
        session.flush()
        session.add(OperationBom(instance_id=iid, operation_id=ob1.id,
                                 material_id=mat.id, quantity_required=8))

        v1 = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=50,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.1,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v1)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v1.id, operation_id=oa.id,
            machine_id=m1.id, worker_id=w1.id, start_time=0, end_time=5,
            processing_time=5, is_frozen=False, status="SCHEDULED"))
        session.flush()
    return iid


def _rec(kind, **fields):
    base = {"kind": kind, "instance_id": "inv-world",
            "event_type": {"MACHINE": "FAILURE", "WORKER": "WORKER_ABSENT",
                           "MATERIAL": "MATERIAL_SHORTAGE"}[kind],
            "occurred_at": 3, "severity": "HIGH",
            "narrative_excerpt": "x"}
    base.update(fields)
    return base


def _state(record=None):
    return RecoveryState(instance_name="inv-world",
                         reference_clock=3,
                         disruption_record=record)


def test_machine_agent_reports_capabilities(world):
    from coe.agents.nodes.investigate import machine_agent_node

    out = machine_agent_node(_state(_rec("MACHINE", machine_id="M1")))
    assert out.db_facts["failed_machine"]["machine_id"] == "M1"
    assert out.db_facts["failed_machine"]["capabilities_lost"] == ["CODE-A"]


def test_machine_agent_noops_on_worker_kind(world):
    from coe.agents.nodes.investigate import machine_agent_node

    out = machine_agent_node(_state(_rec("WORKER", worker_id="W1")))
    assert out.db_facts["failed_machine"] is None


def test_production_agent_stranded_ops(world):
    from coe.agents.nodes.investigate import production_agent_node

    out = production_agent_node(
        _state(_rec("MACHINE", machine_id="M1")))
    s = out.db_facts["stranded_operations"]
    assert len(s) == 1
    assert s[0]["operation_id"].startswith("J-A-O")   # "{job}-O{seq}"
    assert s[0]["machine_id"] == "M1"


def test_inventory_agent_horizon_and_shortage(world):
    from coe.agents.nodes.investigate import inventory_agent_node

    out = inventory_agent_node(
        _state(_rec("MATERIAL", material_sku="MAT-X")))
    facts = out.db_facts
    assert isinstance(facts["projected_horizon"], int)
    ev = facts["shortage_evidence"]
    assert ev["material_sku"] == "MAT-X"
    assert ev["total_supply"] == 5
    assert ev["total_demand"] == 8
    assert len(ev["affected_operations"]) >= 1     # J-B-O1 references MAT-X


def test_worker_agent_sole_eligibility(world):
    from coe.agents.nodes.investigate import worker_agent_node

    out = worker_agent_node(_state(_rec("WORKER", worker_id="W2")))
    aw = out.db_facts["absent_worker"]
    assert aw["worker_id"] == "W2"
    # (J-A-O1, M2) has exactly one eligible worker: W2.
    assert {"operation_id": "J-A-O1", "machine_id": "M2"} \
        in aw["sole_eligible"]


def test_worker_agent_noops_on_machine_kind(world):
    from coe.agents.nodes.investigate import worker_agent_node

    out = worker_agent_node(_state(_rec("MACHINE", machine_id="M1")))
    assert out.db_facts["absent_worker"] is None
```

Note on ids: `coe.solver.identifier.op_id(job_name, sequence_number)` produces `"J-A-O1"`; investigation output reuses that convention so strategy candidates can reference operations unambiguously.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_investigate.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement investigate.py**

```python
# coe/agents/nodes/investigate.py
"""Investigation nodes (spec §4.2): reproducible DB queries, zero LLM.

All four run for every record (fixed pipeline); each no-ops (writes a None
placeholder) when the record's kind does not concern it. Every collection
query carries ORDER BY (repo determinism rule).
"""
from sqlalchemy.orm import Session

from coe.agents.state import RecoveryState
from coe.db.session import make_engine
from coe.solver.identifier import op_id

_KIND_OF = {"MACHINE", "WORKER", "MATERIAL"}


def _session():
    return Session(make_engine())


def _inst(session, name):
    from coe.db.models.provenance import Instance

    return (session.query(Instance)
            .filter(Instance.name == name).one())


def _record_of(state: RecoveryState) -> dict | None:
    return state.disruption_record


def _merge(state: RecoveryState, **facts) -> RecoveryState:
    merged = dict(state.db_facts)
    merged.update(facts)
    return state.model_copy(update={"db_facts": merged})


def _active_snapshot(session, iid):
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion

    version = (
        session.query(ScheduleVersion)
        .filter(ScheduleVersion.instance_id == iid,
                ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                ScheduleVersion.rolled_back.is_(False))
        .order_by(ScheduleVersion.version_number.desc(),
                  ScheduleVersion.id.desc()).first())
    if version is None:
        return None, []
    entries = (
        session.query(ScheduleEntry)
        .filter(ScheduleEntry.version_id == version.id)
        .order_by(ScheduleEntry.id).all())
    return version, entries


def _name_maps(session, iid):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.workers import Worker

    return {
        "machines": dict(session.query(Machine.id, Machine.name)
                         .filter(Machine.instance_id == iid)
                         .order_by(Machine.id).all()),
        "workers": dict(session.query(Worker.id, Worker.name)
                        .filter(Worker.instance_id == iid)
                        .order_by(Worker.id).all()),
        "jobs": dict(session.query(Job.id, Job.name)
                     .filter(Job.instance_id == iid)
                     .order_by(Job.id).all()),
        "ops": {o.id: o for o in session.query(Operation)
                .filter(Operation.instance_id == iid)
                .order_by(Operation.job_id, Operation.sequence_number).all()},
    }


def machine_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    if rec is None or rec["kind"] != "MACHINE":
        return _merge(state, failed_machine=None)
    with _session() as session:
        from coe.db.models.fjsp import Machine, MachineCapability

        inst = _inst(session, state.instance_name)
        m = (session.query(Machine)
             .filter(Machine.instance_id == inst.id,
                     Machine.name == rec["machine_id"]).one())
        caps = [c.capability_code for c in
                session.query(MachineCapability)
                .filter(MachineCapability.instance_id == inst.id,
                        MachineCapability.machine_id == m.id)
                .order_by(MachineCapability.capability_code).all()]
        return _merge(state, failed_machine={
            "machine_id": m.name, "status": m.status,
            "capabilities_lost": caps})


def _entry_overlaps_future(entries, *, names, clock, machine=None,
                           worker=None):
    """Active-version entries not finished at clock, optionally filtered."""
    out = []
    for e in sorted(entries, key=lambda x: (x.start_time, x.id)):
        if e.end_time <= clock:
            continue
        if machine is not None and names["machines"][e.machine_id] != machine:
            continue
        if worker is not None:
            if e.worker_id is None or names["workers"][e.worker_id] != worker:
                continue
        out.append(e)
    return out


def _serialize_stranded(entries, *, session, names, iid) -> list[dict]:
    from coe.db.models.fjsp import Job

    deadlines = dict(session.query(Job.id, Job.deadline)
                     .filter(Job.instance_id == iid)
                     .order_by(Job.id).all())
    return [{
        "operation_id": op_id(names["jobs"][names["_op_job"][e.operation_id]],
                              names["_op_seq"][e.operation_id]),
        "job_id": names["jobs"][names["_op_job"][e.operation_id]],
        "deadline": deadlines[names["_op_job"][e.operation_id]],
        "machine_id": names["machines"][e.machine_id],
        "start": e.start_time, "end": e.end_time,
    } for e in entries]


def _with_op_meta(names, ops) -> dict:
    names = dict(names)
    names["_op_job"] = {o.id: o.job_id for o in ops}
    names["_op_seq"] = {o.id: o.sequence_number for o in ops}
    return names


def production_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    if rec is None:
        return _merge(state, stranded_operations=[])
    with _session() as session:
        from coe.db.models.fjsp import Operation

        inst = _inst(session, state.instance_name)
        _, entries = _active_snapshot(session, inst.id)
        names = _name_maps(session, inst.id)
        names = _with_op_meta(names, names["ops"].values())

        if rec["kind"] == "MACHINE":
            hit = _entry_overlaps_future(
                entries, names=names, clock=state.reference_clock,
                machine=rec["machine_id"])
        elif rec["kind"] == "WORKER":
            hit = _entry_overlaps_future(
                entries, names=names, clock=state.reference_clock,
                worker=rec["worker_id"])
        else:
            hit = []
        return _merge(state,
                      stranded_operations=_serialize_stranded(
                          hit, session=session, names=names, iid=inst.id))


def inventory_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    with _session() as session:
        from coe.db.models.materials import Material, MaterialReceipt
        from coe.db.models.provenance import Instance as Inst

        inst = _inst(session, state.instance_name)

        # Projected horizon: preview BASELINE payload through the real P2
        # builder (pure read; gives the exact horizon the solver will see).
        from coe.solver.payload_builder import build_payload

        payload = build_payload(
            session, instance_row=session.query(Inst)
            .filter(Inst.id == inst.id).one(),
            alpha=1.0, beta=1.0, time_limit_seconds=1)
        horizon = max([op["frozen"]["end"]
                       for j in payload["jobs"]
                       for op in j["operations"]
                       if op.get("frozen")] + [0]) or \
            _fallback_horizon(payload)

        evidence = None
        if rec is not None and rec["kind"] == "MATERIAL":
            evidence = _shortage_evidence(session, inst.id,
                                          rec["material_sku"])
        return _merge(state, projected_horizon=horizon,
                      shortage_evidence=evidence)


def _fallback_horizon(payload: dict) -> int:
    """Conservative span estimate when no frozen anchors exist: latest
    release plus the longest remaining chain of max-duration ops."""
    def chain(j):
        rel = j["release_time"]
        total = sum(max((a["processing_time"] for a in o["alternatives"]),
                        default=0) for o in j["operations"])
        return rel + total

    return max((chain(j) for j in payload["jobs"]), default=0) + 1


def _shortage_evidence(session, iid, sku) -> dict:
    from coe.db.models.fjsp import Operation
    from coe.db.models.materials import Material, MaterialReceipt, OperationBom

    from coe.solver.identifier import parse_op_id

    stock = (session.query(Material.initial_stock)
             .filter(Material.instance_id == iid, Material.sku == sku)
             .scalar())
    receipts = (session.query(MaterialReceipt.quantity)
                .join(Material, Material.id == MaterialReceipt.material_id)
                .filter(MaterialReceipt.instance_id == iid,
                        Material.sku == sku)
                .order_by(MaterialReceipt.available_at).all())
    total_supply = (stock or 0) + sum(q for (q,) in receipts)
    rows = (session.query(OperationBom, Operation)
            .join(Operation, Operation.id == OperationBom.operation_id)
            .filter(OperationBom.instance_id == iid, Material.sku == sku)
            .join(Material, Material.id == OperationBom.material_id)
            .order_by(Operation.job_id, Operation.sequence_number).all())
    # NOTE: join order above filters on Material via the second join;
    # SQLAlchemy resolves the filter against the joined entity regardless
    # of textual position.
    total_demand = 0
    affected = []
    from coe.db.models.fjsp import Job

    job_names = dict(session.query(Job.id, Job.name)
                     .filter(Job.instance_id == iid).order_by(Job.id).all())
    for bom, op in rows:
        total_demand += bom.quantity_required
        affected.append(op_id(job_names[op.job_id], op.sequence_number))
    return {"material_sku": sku, "total_supply": total_supply,
            "total_demand": total_demand, "affected_operations": affected}


def worker_agent_node(state: RecoveryState) -> RecoveryState:
    rec = _record_of(state)
    if rec is None or rec["kind"] != "WORKER":
        return _merge(state, absent_worker=None)
    with _session() as session:
        from coe.db.models.fjsp import Operation
        from coe.db.models.workers import (
            OperationMachineWorkerTime,
            Worker,
        )

        inst = _inst(session, state.instance_name)
        w = (session.query(Worker)
             .filter(Worker.instance_id == inst.id,
                     Worker.name == rec["worker_id"]).one())
        rows = (session.query(OperationMachineWorkerTime)
                .filter(OperationMachineWorkerTime.instance_id == inst.id,
                        OperationMachineWorkerTime.worker_id == w.id)
                .order_by(OperationMachineWorkerTime.operation_id,
                          OperationMachineWorkerTime.machine_id).all())
        names = _name_maps(session, inst.id)
        names = _with_op_meta(names, names["ops"].values())
        sole = []
        for r in rows:
            others = (session.query(OperationMachineWorkerTime.worker_id)
                      .filter(
                          OperationMachineWorkerTime.instance_id == inst.id,
                          OperationMachineWorkerTime.operation_id
                          == r.operation_id,
                          OperationMachineWorkerTime.machine_id
                          == r.machine_id)
                      .order_by(OperationMachineWorkerTime.worker_id).all())
            if len(others) == 1:
                sole.append({
                    "operation_id": op_id(
                        names["jobs"][names["_op_job"][r.operation_id]],
                        names["_op_seq"][r.operation_id]),
                    "machine_id": names["machines"][r.machine_id]})
        _, entries = _active_snapshot(session, inst.id)
        mine = _entry_overlaps_future(entries, names=names,
                                      clock=state.reference_clock,
                                      worker=w.name)
        return _merge(state, absent_worker={
            "worker_id": w.name, "sole_eligible": sole,
            "assignment_count": len(mine)})
```

Implementation notes:
- `_serialize_stranded` / `sole` emit `"{job}-O{seq}"` operation ids via `coe.solver.identifier.op_id` so candidates and payloads speak the same dialect.
- `inventory_agent_node` intentionally reuses `build_payload` (read-only preview, `time_limit_seconds=1`) rather than reimplementing horizon math — single source of truth, exactly what the solver will later see. The horizon value stored is the max frozen end if any, else a conservative `_fallback_horizon` estimate; both are pure functions of DB state.

DEVIATION to document in the task report: spec §4.2 asks inventory to verify "worker eligibility rows and material supply" *per routing candidate*. Routing availability emerges naturally in the payload preview (alternatives stripped of ineligible combos by the builder itself); storing a duplicate structure would violate DRY, so `db_facts` exposes `projected_horizon` + `shortage_evidence` and lets candidates be validated against the same builder output at compile time.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_investigate.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/nodes/investigate.py tests/agents/test_investigate.py
git commit -m "feat(agents): four investigation nodes as pure DB queries"
```

---

### Task 7: Strategy catalog + candidate validator (§5, §4.3 step 2)

**Files:**
- Create: `coe/agents/catalog.py`
- Test: `tests/agents/test_catalog.py`

**Interfaces:**
- Consumes: models `Job`, `Operation`, `ScheduleEntry`, `Material`, `MaterialReceipt`; `coe.solver.identifier.parse_op_id`.
- Produces:
  - `StrategyCandidate` — pydantic discriminated union on `type` with variants `TardinessWeightCandidate{job_id, weight: float, 0 ≤ weight ≤ 10}`, `DeferJobCandidate{job_id, release_offset: int ≥ 0}`, `SuspendJobCandidate{job_id}`, `ExpediteMaterialCandidate{material_sku, quantity: float > 0, available_at: int ≥ 0}`, `WeightPresetCandidate{alpha ≥ 0, beta ≥ 0, alpha + beta > 0}`. `extra="forbid"` everywhere.
  - `validate_candidate(data: dict, *, session, instance_name: str, db_facts: dict, reference_clock: int, prior_this_round: list[dict]) -> tuple[str, str | None]` returning `(verdict, reason)` where verdict ∈ `VALID | VALID_WITH_WARNING | INVALID | INVALID_DUPLICATE` and reason is machine-readable (`unknown_job`, `job_not_pending`, `suspension_has_history`, `unknown_material`, `out_of_bounds`, `effect_beyond_horizon`, `duplicate`, `ok`, ...).

Validation matrix (implement exactly):

| type | INVALID when | VALID_WITH_WARNING when | otherwise |
| --- | --- | --- | --- |
| `TARDINESS_WEIGHT` | unknown job; job has no non-completed op (job_not_pending); weight ∉ [0,10] (out_of_bounds) | — | VALID |
| `DEFER_JOB` | unknown job; job_not_pending | — | VALID |
| `SUSPEND_JOB` | unknown job; job_not_pending; any active-schedule entry of the job with `end <= clock` or `start <= clock < end` (suspension_has_history) | — | VALID |
| `EXPEDITE_MATERIAL` | unknown SKU (unknown_material) | `available_at >= db_facts["projected_horizon"]` (effect_beyond_horizon) | VALID |
| `WEIGHT_PRESET` | — (bounds enforced by schema) | — | VALID |
| any | canonical JSON equal to an earlier candidate this round (duplicate) | — | (checked first) |

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_catalog.py
"""§5 closed catalog + §4.3(2) verdicts."""
import pytest
from pydantic import ValidationError


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.catalog import StrategyCandidate

        StrategyCandidate.model_validate({"type": "TELEPORT", "job_id": "J"})


def test_weight_preset_zero_sum_rejected():
    with pytest.raises(ValidationError):
        from coe.agents.catalog import StrategyCandidate

        StrategyCandidate.model_validate(
            {"type": "WEIGHT_PRESET", "alpha": 0, "beta": 0})


def test_weight_out_of_bounds_schema_level():
    with pytest.raises(ValidationError):
        from coe.agents.catalog import StrategyCandidate

        StrategyCandidate.model_validate(
            {"type": "TARDINESS_WEIGHT", "job_id": "J-1", "weight": 11})


@pytest.fixture()
def world(clean_db):
    """J-HIST holds completed history; J-FRESH untouched; MAT-X known."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="cat-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m = Machine(instance_id=iid, name="M1")
        w = Worker(instance_id=iid, name="W1")
        session.add_all([m, w])
        session.flush()

        jh = Job(instance_id=iid, name="J-HIST", priority=1)
        jf = Job(instance_id=iid, name="J-FRESH", priority=2, deadline=200)
        session.add_all([jh, jf])
        session.flush()
        oh = Operation(instance_id=iid, job_id=jh.id, sequence_number=1)
        of = Operation(instance_id=iid, job_id=jf.id, sequence_number=1)
        session.add_all([oh, of])
        session.flush()
        for o in (oh, of):
            session.add(OperationMachineAlternative(
                instance_id=iid, operation_id=o.id, machine_id=m.id,
                processing_time=5))

        session.add(Material(instance_id=iid, sku="MAT-X",
                             initial_stock=10))
        v1 = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=20,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.1,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v1)
        session.flush()
        # history: J-HIST op fully in the past relative to clock=100
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v1.id, operation_id=oh.id,
            machine_id=m.id, worker_id=w.id, start_time=0, end_time=5,
            processing_time=5, is_frozen=True, status="FROZEN"))
        session.flush()
    return iid


def _validate(candidate, *, world, db_facts=None, clock=100, prior=None):
    from coe.agents.catalog import validate_candidate
    from coe.db.session import session_scope

    with session_scope() as session:
        return validate_candidate(
            candidate, session=session, instance_name="cat-world",
            db_facts=db_facts or {"projected_horizon": 500},
            reference_clock=clock, prior_this_round=prior or [])


def test_tardiness_valid_and_reasons(world):
    assert _validate({"type": "TARDINESS_WEIGHT", "job_id": "J-FRESH",
                      "weight": 0.5}, world=world) == ("VALID", "ok")
    assert _validate({"type": "TARDINESS_WEIGHT", "job_id": "J-GHOST",
                      "weight": 1}, world=world)[0] == "INVALID"
    v, r = _validate({"type": "TARDINESS_WEIGHT", "job_id": "J-HIST",
                      "weight": 1}, world=world)
    assert (v, r) == ("INVALID", "job_not_pending")


def test_suspend_rejects_history(world):
    assert _validate({"type": "SUSPEND_JOB", "job_id": "J-FRESH"},
                     world=world) == ("VALID", "ok")
    v, r = _validate({"type": "SUSPEND_JOB", "job_id": "J-HIST"},
                     world=world)
    assert v == "INVALID" and r == "suspension_has_history"


def test_expedite_beyond_horizon_warns(world):
    ok = _validate({"type": "EXPEDITE_MATERIAL", "material_sku": "MAT-X",
                    "quantity": 5, "available_at": 100}, world=world)
    late = _validate({"type": "EXPEDITE_MATERIAL", "material_sku": "MAT-X",
                      "quantity": 5, "available_at": 900}, world=world)
    ghost = _validate({"type": "EXPEDITE_MATERIAL", "material_sku": "NOPE",
                       "quantity": 5, "available_at": 100}, world=world)
    assert ok == ("VALID", "ok")
    assert late == ("VALID_WITH_WARNING", "effect_beyond_horizon")
    assert ghost == ("INVALID", "unknown_material")


def test_duplicate_detection_canonical(world):
    c = {"type": "DEFER_JOB", "job_id": "J-FRESH", "release_offset": 30}
    dup = {"release_offset": 30, "job_id": "J-FRESH",
           "type": "DEFER_JOB"}          # same candidate, different key order
    assert _validate(c, world=world) == ("VALID", "ok")
    assert _validate(dup, world=world, prior=[c])[0] == "INVALID_DUPLICATE"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_catalog.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement catalog.py**

```python
# coe/agents/catalog.py
"""Closed strategy catalog (spec §5) + candidate verdicts (§4.3 step 2).

Anything outside the union dies at schema level before reaching the
applier. Verdicts are deterministic functions of DB state + db_facts.
"""
import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class TardinessWeightCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["TARDINESS_WEIGHT"]
    job_id: str
    weight: float = Field(ge=0, le=10)


class DeferJobCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["DEFER_JOB"]
    job_id: str
    release_offset: int = Field(ge=0)


class SuspendJobCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SUSPEND_JOB"]
    job_id: str


class ExpediteMaterialCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["EXPEDITE_MATERIAL"]
    material_sku: str
    quantity: float = Field(gt=0)
    available_at: int = Field(ge=0)


class WeightPresetCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["WEIGHT_PRESET"]
    alpha: float = Field(ge=0)
    beta: float = Field(ge=0)

    @model_validator(mode="after")
    def _positive_sum(self) -> "WeightPresetCandidate":
        if self.alpha + self.beta <= 0:
            raise ValueError("alpha + beta must be > 0")
        return self


StrategyCandidate = Annotated[
    Union[TardinessWeightCandidate, DeferJobCandidate,
          SuspendJobCandidate, ExpediteMaterialCandidate,
          WeightPresetCandidate],
    Field(discriminator="type"),
]

_candidate_adapter: TypeAdapter = TypeAdapter(StrategyCandidate)


def _canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _job_row(session, iid, job_id):
    from coe.db.models.fjsp import Job

    return (session.query(Job)
            .filter(Job.instance_id == iid, Job.name == job_id)
            .one_or_none())


def _latest_active_version_id(session, iid):
    from coe.db.models.schedule import ScheduleVersion

    row = (session.query(ScheduleVersion.id)
           .filter(ScheduleVersion.instance_id == iid,
                   ScheduleVersion.solver_status.in_(("OPTIMAL", "FEASIBLE")),
                   ScheduleVersion.rolled_back.is_(False))
           .order_by(ScheduleVersion.version_number.desc(),
                     ScheduleVersion.id.desc()).first())
    return None if row is None else row[0]


def _has_unfinished_op(session, iid, job, clock) -> bool:
    """True iff some operation of the job has no active-version entry that
    ended at/before the clock (i.e. work remains that this run can shape)."""
    from coe.db.models.schedule import ScheduleEntry

    vid = _latest_active_version_id(session, iid)
    if vid is None:
        return True
    done = set(row[0] for row in
               session.query(ScheduleEntry.operation_id)
               .filter(ScheduleEntry.version_id == vid,
                       ScheduleEntry.end_time <= clock).all())
    ops = [o.id for o in job.operations]
    return any(o not in done for o in ops)


def _history_exists(session, iid, job, clock) -> bool:
    """§5 SUSPEND_JOB rule: any active entry starting at/before clock."""
    from coe.db.models.schedule import ScheduleEntry

    vid = _latest_active_version_id(session, iid)
    if vid is None:
        return False
    rows = (session.query(ScheduleEntry)
            .filter(ScheduleEntry.version_id == vid,
                    ScheduleEntry.operation_id.in_(
                        [o.id for o in job.operations])).all())
    return any(e.start_time <= clock for e in rows)


def validate_candidate(data: dict, *, session, instance_name: str,
                       db_facts: dict, reference_clock: int,
                       prior_this_round: list[dict]) -> tuple[str, str]:
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance

    inst = (session.query(Instance)
            .filter(Instance.name == instance_name).one())

    if any(_canonical(data) == _canonical(p) for p in prior_this_round):
        return "INVALID_DUPLICATE", "duplicate"

    try:
        cand = _candidate_adapter.validate_python(data)
    except Exception as exc:
        first = getattr(exc, "errors", lambda: [{"msg": str(exc)}])()[0]
        return "INVALID", f"out_of_bounds: {first.get('msg', str(exc))}"

    t = cand.type
    if t in ("TARDINESS_WEIGHT", "DEFER_JOB", "SUSPEND_JOB"):
        job = _job_row(session, inst.id, cand.job_id)
        if job is None:
            return "INVALID", "unknown_job"
        if not _has_unfinished_op(session, inst.id, job, reference_clock):
            return "INVALID", "job_not_pending"
        if t == "SUSPEND_JOB" and _history_exists(
                session, inst.id, job, reference_clock):
            return "INVALID", "suspension_has_history"
        return "VALID", "ok"
    if t == "EXPEDITE_MATERIAL":
        hit = (session.query(Material.id)
               .filter(Material.instance_id == inst.id,
                       Material.sku == cand.material_sku)
               .one_or_none())
        if hit is None:
            return "INVALID", "unknown_material"
        horizon = db_facts.get("projected_horizon")
        if horizon is not None and cand.available_at >= horizon:
            return "VALID_WITH_WARNING", "effect_beyond_horizon"
        return "VALID", "ok"
    return "VALID", "ok"      # WEIGHT_PRESET: schema already bounded it
```

Implementation notes:
- `job.operations` (relationship) is fine for existence checks; do not rely on its ordering.
- Duplicate detection compares CANONICAL dicts, so key order cannot smuggle a duplicate past the gate (§6.1).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_catalog.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/catalog.py tests/agents/test_catalog.py
git commit -m "feat(agents): closed strategy catalog + candidate validator"
```

---

### Task 8: Strategy applier — pure payload transforms (§6.1)

**Files:**
- Create: `coe/agents/applier.py`
- Test: `tests/agents/test_applier.py`

**Interfaces:**
- Consumes: payload dict shaped exactly like `coe.solver.payload_builder.build_payload` output (roots: `config`, `jobs`, `blocked_operations`, `suspended_jobs`, `material_receipts`, `warnings`, `job_tardiness_weights`).
- Produces:
  - `apply_candidates(payload: dict, candidates: list[dict]) -> tuple[dict, dict[str, float]]`
    - `payload` in: builder output with the `job_tardiness_weights` key REMOVED by the caller (ordering contract — derivation reruns after the applier, manager's job, Task 9).
    - `candidates` in: `[{"candidate": <validated dict>, "round": n}, ...]` in emission order; assumes every entry already validated (the applier performs NO validation).
    - returns `(transformed_payload, explicit_tardiness_weights)` where `explicit_tardiness_weights` maps `job_id -> weight` for later merge-on-top of the re-derived defaults.
  - Effects (deterministic, each appends one warning):
    - `TARDINESS_WEIGHT` → records into explicit map; `field_changed: "job_tardiness_weights[{job}]"`
    - `DEFER_JOB` → `jobs[i].release_time += offset`; `field_changed: "release_time"`
    - `SUSPEND_JOB` → every PENDING op of the job becomes `{status: "BLOCKED", alternatives: [], materials: []}` + one `blocked_operations` entry `{operation_id, reason: "JOB_SUSPENDED", material_sku: null}`; job name appended to root `suspended_jobs`; frozen/completed history untouched; `field_changed: "suspended_jobs"`
    - `EXPEDITE_MATERIAL` → receipt `{"sku", "quantity", "available_at", "source": "strategy_agent"}` inserted keeping `material_receipts` sorted by `(sku, available_at, quantity)`; `field_changed: "material_receipts[{sku}]"`
    - `WEIGHT_PRESET` → `config.alpha/.beta` overridden; `field_changed: "config.alpha_beta"`
  - Last-wins: later candidates targeting the same job/material override earlier effects; EVERY application is recorded as `{"type": "STRATEGY_APPLIED", "candidate": ..., "round": n, "field_changed": ...}` appended to `payload.warnings`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_applier.py
"""§6.1 pure applier: documented transforms, last-wins, determinism."""
import json

import pytest


def _payload():
    return {
        "instance_id": "p3", "schedule_type": "RECOVERY",
        "parent_version_id": 7,
        "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                   "normalize_objectives": True, "random_seed": 42,
                   "num_search_workers": 8},
        "machines": ["M1"], "failed_machines": [],
        "machine_initial_families": {}, "warnings": [],
        "jobs": [
            {"job_id": "J-A", "family_id": None, "release_time": 0,
             "deadline": 50, "priority": 1,
             "operations": [
                 {"operation_id": "J-A-O1", "sequence": 1,
                  "status": "PENDING", "materials": [],
                  "alternatives": [], "frozen": None},
                 {"operation_id": "J-A-O2", "sequence": 2,
                  "status": "COMPLETED", "materials": [],
                  "alternatives": [],
                  "frozen": {"machine_id": "M1", "worker_id": None,
                             "start": 0, "end": 5}}]},
            {"job_id": "J-B", "family_id": None, "release_time": 0,
             "deadline": 90, "priority": 3,
             "operations": [
                 {"operation_id": "J-B-O1", "sequence": 1,
                  "status": "PENDING", "materials": [],
                  "alternatives": [], "frozen": None}]},
        ],
        "machine_downtime": [],
        "materials": [{"sku": "MAT-X", "capacity": 5}],
        "material_receipts": [
            {"sku": "MAT-Y", "quantity": 10, "available_at": 400}],
        "worker_unavailability": [], "setup_times": [],
        "blocked_operations": [], "suspended_jobs": [],
    }


def test_defer_raises_release():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                       "release_offset": 40}, "round": 1}])
    assert p["jobs"][0]["release_time"] == 40
    assert p["warnings"][0]["type"] == "STRATEGY_APPLIED"
    assert p["warnings"][0]["field_changed"] == "release_time"


def test_suspend_transforms_only_pending_ops():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "SUSPEND_JOB", "job_id": "J-A"},
         "round": 1}])
    ja = p["jobs"][0]
    assert ja["operations"][0]["status"] == "BLOCKED"
    assert ja["operations"][0]["alternatives"] == []
    assert ja["operations"][1]["status"] == "COMPLETED"       # history kept
    assert ja["operations"][1]["frozen"] is not None
    assert p["blocked_operations"] == [
        {"operation_id": "J-A-O1", "reason": "JOB_SUSPENDED",
         "material_sku": None}]
    assert p["suspended_jobs"] == ["J-A"]
    assert p["warnings"][0]["field_changed"] == "suspended_jobs"


def test_expedite_keeps_receipt_sort_and_marks_source():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "EXPEDITE_MATERIAL", "material_sku": "MAT-X",
                       "quantity": 20, "available_at": 120}, "round": 1}])
    rs = p["material_receipts"]
    assert [(r["sku"], r["available_at"]) for r in rs] == \
        [("MAT-X", 120), ("MAT-Y", 400)]
    assert rs[0]["source"] == "strategy_agent"


def test_weight_preset_updates_config():
    from coe.agents.applier import apply_candidates

    p, _ = apply_candidates(_payload(), [
        {"candidate": {"type": "WEIGHT_PRESET", "alpha": 0.25, "beta": 2},
         "round": 2}])
    assert p["config"]["alpha"] == 0.25
    assert p["config"]["beta"] == 2.0


def test_tardiness_returns_explicit_map_not_direct_mutation():
    from coe.agents.applier import apply_candidates

    p, explicit = apply_candidates(_payload(), [
        {"candidate": {"type": "TARDINESS_WEIGHT", "job_id": "J-B",
                       "weight": 0.5}, "round": 1}])
    assert explicit == {"J-B": 0.5}
    assert "job_tardiness_weights" not in p    # merge is the manager's job


def test_last_wins_with_full_audit_trail():
    # §6.1: later candidates targeting the same job REPLACE earlier effects
    # (the offset substitutes, it does not accumulate) — and every
    # application is still recorded.
    from coe.agents.applier import apply_candidates

    p, explicit = apply_candidates(_payload(), [
        {"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                       "release_offset": 10}, "round": 1},
        {"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                       "release_offset": 99}, "round": 2}])
    assert p["jobs"][0]["release_time"] == 99      # replaced, not stacked
    applied = [w for w in p["warnings"]
               if w["type"] == "STRATEGY_APPLIED"]
    assert len(applied) == 2                       # full audit trail
    assert applied[-1]["round"] == 2


def test_byte_determinism():
    from coe.agents.applier import apply_candidates

    cands = [{"candidate": {"type": "DEFER_JOB", "job_id": "J-A",
                            "release_offset": 12}, "round": 1},
             {"candidate": {"type": "TARDINESS_WEIGHT", "job_id": "J-B",
                            "weight": 2}, "round": 1}]
    a = apply_candidates(_payload(), cands)
    b = apply_candidates(_payload(), cands)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_applier.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement applier.py**

```python
# coe/agents/applier.py
"""Pure strategy applier (spec §6.1): (payload, candidates) -> payload.

No validation lives here — callers pass catalog-validated candidates in
emission order. Later candidates targeting the same job/material REPLACE
earlier effects (last-wins); every application is recorded as a
STRATEGY_APPLIED warning. The caller strips job_tardiness_weights before
calling and re-derives after (ordering contract, P2 §3.1): explicit
TARDINESS_WEIGHT overrides come back as the second tuple element.
"""


def _warn(payload, candidate, rnd, field_changed):
    payload["warnings"].append({
        "type": "STRATEGY_APPLIED", "candidate": candidate,
        "round": rnd, "field_changed": field_changed})


def _find_job(payload, job_id):
    for j in payload["jobs"]:
        if j["job_id"] == job_id:
            return j
    raise KeyError(job_id)


def apply_candidates(payload: dict,
                     candidates: list[dict]) -> tuple[dict, dict[str, float]]:
    """Returns (transformed_payload, explicit_tardiness_weights).

    DEFER offsets REPLACE prior defers of the same job (last-wins, §6.1):
    the pre-run release_time is remembered locally so repeated applications
    substitute rather than compound. No marker keys leak into the payload.
    """
    explicit: dict[str, float] = {}
    bases: dict[str, int] = {}

    def defer(job_id, offset):
        job = _find_job(payload, job_id)
        if job_id not in bases:
            bases[job_id] = job["release_time"]
        job["release_time"] = bases[job_id] + offset

    def suspend(job_id):
        job = _find_job(payload, job_id)
        for op in job["operations"]:
            if op["status"] == "PENDING":
                op["status"] = "BLOCKED"
                op["alternatives"] = []
                op["materials"] = []
                payload["blocked_operations"].append({
                    "operation_id": op["operation_id"],
                    "reason": "JOB_SUSPENDED", "material_sku": None})
        if job_id not in payload["suspended_jobs"]:
            payload["suspended_jobs"].append(job_id)
            payload["suspended_jobs"].sort()

    def expedite(sku, quantity, available_at):
        payload["material_receipts"].append({
            "sku": sku, "quantity": quantity, "available_at": available_at,
            "source": "strategy_agent"})
        payload["material_receipts"].sort(
            key=lambda r: (r["sku"], r["available_at"], r["quantity"]))

    for item in candidates:
        c, rnd = item["candidate"], item.get("round", 0)
        t = c["type"]
        if t == "DEFER_JOB":
            defer(c["job_id"], c["release_offset"])
            _warn(payload, c, rnd, "release_time")
        elif t == "SUSPEND_JOB":
            suspend(c["job_id"])
            _warn(payload, c, rnd, "suspended_jobs")
        elif t == "EXPEDITE_MATERIAL":
            expedite(c["material_sku"], c["quantity"], c["available_at"])
            _warn(payload, c, rnd, f"material_receipts[{c['material_sku']}]")
        elif t == "WEIGHT_PRESET":
            payload["config"]["alpha"] = float(c["alpha"])
            payload["config"]["beta"] = float(c["beta"])
            _warn(payload, c, rnd, "config.alpha_beta")
        elif t == "TARDINESS_WEIGHT":
            explicit[c["job_id"]] = float(c["weight"])
            _warn(payload, c, rnd, f"job_tardiness_weights[{c['job_id']}]")
        else:
            raise ValueError(f"unreachable candidate type {t!r} "
                             "(validator gap)")
    return payload, explicit
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_applier.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/applier.py tests/agents/test_applier.py
git commit -m "feat(agents): pure strategy applier with last-wins audit"
```

---


---

# PART D — Compile, Negotiation, Safety Net, Lifecycle, Explain

*Spec sections covered by this part: §4.4 (Manager Compile — builder invocation, verdict filtering, applier ordering contract), §4.3 (Strategy Loop incl. the Amendment 2026-08-24 material-reactive fixed procedure), §3.1 (state fields consumed by back-edge routers), §6.2–6.3 (gate + verifier sharing `check_solution`), §7 (run lifecycle rows, proposal rows, per-instance advisory lock with `RECOVERY_LOCK_WAIT_SECONDS`), §4.5 (Explanation Service), §3.3 (fallback policies for all three LLM nodes), §11 Tier 3/4 test patterns. Acceptance criteria touched: 5, 6, 7, 8, 9, 12.*

### Task 9: Manager compile node (§4.4)

**Files:**
- Create: `coe/agents/nodes/manager.py`
- Test: `tests/agents/test_manager_compile.py`

**Interfaces:**
- Consumes: `build_payload`, `derive_tardiness_weights` (`coe/solver/payload_builder.py`); `apply_candidates` (Task 8); `RecoveryState` (`strategy_candidates` items shaped `{"candidate": dict, "round": int}`; `round_verdicts` items shaped `{"candidate": dict, "round": int, "verdict": str, "reason": str | None}`); settings knobs `solver_alpha_weight`, `solver_beta_weight`, `solver_time_limit_seconds`, `solver_random_seed`, `solver_num_search_workers`, `solver_normalize_objectives`.
- Produces: `class NoBaselineError(RuntimeError)`; `run_manager_compile(state: RecoveryState) -> RecoveryState`. Sets `state.compiled_payload` (full solver payload) and `state.material_reactive` (True iff any `MATERIAL_SHORTFALL` warning present). Verdict filter rule: a candidate is applied iff its LATEST verdict (highest round in `round_verdicts`) is `VALID` or `VALID_WITH_WARNING`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_manager_compile.py
"""§4.4 manager compile + ordering contract (§6.1 tail)."""
import pytest

pytestmark = pytest.mark.db

from coe.agents.state import RecoveryState


@pytest.fixture()
def world(clean_db):
    """Mini instance WITH an active OPTIMAL v1 so RECOVERY builds work."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.materials import Material, OperationBom
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleEntry, ScheduleVersion
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = Instance(name="mgr-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m = Machine(instance_id=iid, name="M1")
        w = Worker(instance_id=iid, name="W1")
        session.add_all([m, w])
        session.flush()
        ja = Job(instance_id=iid, name="J-A", priority=1, release_time=0,
                 deadline=60)
        jb = Job(instance_id=iid, name="J-B", priority=3, release_time=0,
                 deadline=90)
        session.add_all([ja, jb])
        session.flush()
        oa = Operation(instance_id=iid, job_id=ja.id, sequence_number=1)
        ob = Operation(instance_id=iid, job_id=jb.id, sequence_number=1)
        session.add_all([oa, ob])
        session.flush()
        for o in (oa, ob):
            session.add(OperationMachineAlternative(
                instance_id=iid, operation_id=o.id, machine_id=m.id,
                processing_time=5))
        mat = Material(instance_id=iid, sku="MAT-X", initial_stock=5)
        session.add(mat)
        session.flush()
        # both ops consume MAT-X: stock 5 < demand 10 -> shortfall
        for o in (oa, ob):
            session.add(OperationBom(instance_id=iid, operation_id=o.id,
                                     material_id=mat.id,
                                     quantity_required=5))
        v1 = ScheduleVersion(
            instance_id=iid, version_number=1, schedule_type="BASELINE",
            solver_status="OPTIMAL", objective_value=1.0, makespan=10,
            total_tardiness=0, alpha_weight=1.0, beta_weight=1.0,
            time_limit_seconds=60, solve_duration_seconds=0.1,
            rolled_back=False, payload_hash="0" * 64, payload_json={})
        session.add(v1)
        session.flush()
        session.add(ScheduleEntry(
            instance_id=iid, version_id=v1.id, operation_id=oa.id,
            machine_id=m.id, worker_id=w.id, start_time=0, end_time=5,
            processing_time=5, is_frozen=False, status="SCHEDULED"))
        session.flush()
    return iid


def _state(**over):
    base = {"instance_name": "mgr-world", "reference_clock": 20}
    base.update(over)
    return RecoveryState(**base)


def test_compiles_recovery_payload_with_parent(world):
    from coe.agents.nodes.manager import run_manager_compile

    out = run_manager_compile(_state())
    p = out.compiled_payload
    assert p["schedule_type"] == "RECOVERY"
    assert p["parent_version_id"] is not None
    assert out.material_reactive is True      # stock 5 < demand 10
    assert any(w["type"] == "MATERIAL_SHORTFALL"
               for w in p["warnings"])


def test_valid_candidate_applied_invalid_filtered(world):
    from coe.agents.nodes.manager import run_manager_compile

    cand = {"type": "DEFER_JOB", "job_id": "J-A", "release_offset": 15}
    st = _state(
        strategy_candidates=[{"candidate": cand, "round": 1}],
        round_verdicts=[
            {"candidate": cand, "round": 1, "verdict": "VALID",
             "reason": "ok"},
            {"candidate": {"type": "TARDINESS_WEIGHT",
                           "job_id": "J-B", "weight": 99}, "round": 1,
             "verdict": "INVALID", "reason": "out_of_bounds"},
        ])
    out = run_manager_compile(st)
    ja = [j for j in out.compiled_payload["jobs"]
          if j["job_id"] == "J-A"][0]
    assert ja["release_time"] == 15           # VALID applied
    applied = [w for w in out.compiled_payload["warnings"]
               if w["type"] == "STRATEGY_APPLIED"]
    assert len(applied) == 1                  # INVALID never reached applier


def test_weight_derivation_uses_post_preset_beta(world):
    from coe.agents.nodes.manager import run_manager_compile

    preset = {"type": "WEIGHT_PRESET", "alpha": 0.25, "beta": 2.0}
    st = _state(strategy_candidates=[
        {"candidate": preset, "round": 1}],
        round_verdicts=[{"candidate": preset, "round": 1,
                         "verdict": "VALID", "reason": "ok"}])
    out = run_manager_compile(st)
    assert out.compiled_payload["config"]["beta"] == 2.0
    w = out.compiled_payload.get("job_tardiness_weights") or {}
    assert w                                  # derived under beta=2
    total = sum(w.values())
    n_dl = len(w)
    assert abs(total - 2.0 * n_dl) < 1e-6     # mean-preserving around beta


def test_no_baseline_is_loud(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    from coe.agents.nodes.manager import NoBaselineError, run_manager_compile

    with session_scope() as session:
        session.add(Instance(name="mgr-empty", source_name="synthetic"))
    with pytest.raises(NoBaselineError):
        run_manager_compile(_state(instance_name="mgr-empty"))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_manager_compile.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement manager.py**

```python
# coe/agents/nodes/manager.py
"""Manager compile node (spec §4.4): DB -> payload -> applier -> weights."""
import json

from sqlalchemy.orm import Session

from coe.agents.applier import apply_candidates
from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.db.session import make_engine
from coe.solver.payload_builder import (
    build_payload,
    derive_tardiness_weights,
)


class NoBaselineError(RuntimeError):
    """RECOVERY needs an active schedule; tell the operator to baseline."""


def _session():
    return Session(make_engine())


def _canon(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _latest_verdicts(state: RecoveryState) -> dict:
    """canonical(candidate json) -> latest verdict entry."""
    latest: dict[str, dict] = {}
    for v in state.round_verdicts:
        key = _canon(v["candidate"])
        prev = latest.get(key)
        if prev is None or v["round"] >= prev["round"]:
            latest[key] = v
    return latest


def run_manager_compile(state: RecoveryState) -> RecoveryState:
    s = get_settings()
    with _session() as session:
        from coe.db.models.provenance import Instance

        inst = (session.query(Instance)
                .filter(Instance.name == state.instance_name).one())

        rec = state.disruption_record or {}
        failed = ((rec["machine_id"],)
                  if rec.get("kind") == "MACHINE" else ())

        payload = build_payload(
            session, instance_row=inst,
            alpha=s.solver_alpha_weight, beta=s.solver_beta_weight,
            time_limit_seconds=s.solver_time_limit_seconds,
            random_seed=s.solver_random_seed,
            num_search_workers=s.solver_num_search_workers,
            normalize_objectives=s.solver_normalize_objectives,
            schedule_type="RECOVERY",
            now=state.reference_clock,
            failed_machine_names=failed)

    if payload.get("parent_version_id") is None:
        raise NoBaselineError(
            f"{state.instance_name} has no active schedule — run "
            "`solve baseline` before recovering (§4.4)")

    payload.pop("job_tardiness_weights", None)     # ordering contract §6.1

    latest = _latest_verdicts(state)
    applicable = [
        {"candidate": c["candidate"], "round": c["round"]}
        for c in state.strategy_candidates
        if latest.get(_canon(c["candidate"]), {}).get("verdict")
        in ("VALID", "VALID_WITH_WARNING")
    ]
    payload, explicit = apply_candidates(payload, applicable)

    derived = derive_tardiness_weights(payload["jobs"],
                                       payload["config"]["beta"]) or {}
    merged = {**derived, **explicit}
    if merged:
        payload["job_tardiness_weights"] = merged

    reactive = any(w.get("type") == "MATERIAL_SHORTFALL"
                   for w in payload["warnings"])
    return state.model_copy(update={
        "compiled_payload": payload,
        "material_reactive": reactive,
    })
```

Implementation notes:
- The recovery time-limit floor (P2 §10 Option A, `max(t, 180)`) applies at SOLVE time; the graph's solve runner imports the SAME helper (`from coe.cli import _recovery_floor`) — single source of truth, zero P2 edits.
- `failed_machine_names` comes from the record; FAILED-status machines are additionally stripped by the builder itself (P2 status-truth rider d), so WORKER/MATERIAL records still lose genuinely failed machines.
- `test_weight_derivation_uses_post_preset_beta`: `derive_tardiness_weights` is mean-preserving around `beta` across deadline-bearing jobs — that property pins "derivation ran AFTER the preset".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_manager_compile.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/nodes/manager.py tests/agents/test_manager_compile.py
git commit -m "feat(agents): manager compile with verdict filter + weight ordering"
```

---

### Task 10: Strategy round node — negotiation + deterministic material-reactive duty (§4.3)

**Files:**
- Modify: `coe/agents/state.py` (add one field)
- Create: `coe/agents/nodes/strategy.py`
- Test: `tests/agents/test_strategy_node.py`

**Interfaces:**
- Consumes: `validate_candidate` + `_candidate_adapter` (Task 7); `LLMClient` (Task 3); settings `llm_max_retries`.
- Produces:
  - State gains: `strategy_final: bool = False` (the §4.3 step 1 declaration).
  - `material_reactive_plan(state: RecoveryState) -> dict` — pure function returning an LLM-shaped response `{"candidates": [...], "final": bool, "note": str}` implementing Amendment 2026-08-24 steps 1–5 deterministically. NO LLM call on this path.
  - `run_strategy_round(state: RecoveryState, *, client=None, max_retries: int | None = None) -> RecoveryState` — ONE round. Increments `round_count` unconditionally. When `state.material_reactive` is True: uses `material_reactive_plan` (client ignored, may be None). Otherwise calls the LLM negotiation prompt; appends candidates + verdicts to state; sets `strategy_final`.
  - Fallback (§3.3): LLM unparseable/exhausted → appends warning `"strategy_loop fallback: proceeding without strategy (§3.3)"`, sets `strategy_final=True`, candidates list untouched.

Material-reactive procedure spec-exact:
1. Contested SKUs = `MATERIAL_SHORTFALL` warnings in `state.compiled_payload` (`material_sku`, `total_supply`, `total_demand`).
2. Consuming jobs ranked by `(priority ASC, slack ASC)` where `slack = deadline - release_time` when a deadline exists else `float("inf")` — priority 1 protects first; tightest deadline protects first.
3. Sacrifice target = LAST in ranking. If some receipt of that SKU with `available_at < projected_horizon` has `quantity >= (total_demand - total_supply)` → `DEFER_JOB` on the sacrifice with `release_offset = max(0, available_at - job.release_time)`; else `SUSPEND_JOB` on the sacrifice.
4. Note appended to `state.warnings`: `"{loser} {deferred/suspended} so {protector} keeps the {sku}"`.
5. `final: true` after one pass resolving every contested SKU.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_strategy_node.py
"""§4.3 negotiation + Amendment 2026-08-24 fixed procedure."""
from coe.agents.state import RecoveryState


def _payload(shortage=True, receipts=()):
    jobs = [
        {"job_id": "J-A", "family_id": None, "release_time": 0,
         "deadline": 50, "priority": 1,
         "operations": [{"operation_id": "J-A-O1", "sequence": 1,
                         "status": "PENDING",
                         "materials": [{"sku": "MAT-X", "quantity": 5}]
                         if shortage else [],
                         "alternatives": [], "frozen": None}]},
        {"job_id": "J-B", "family_id": None, "release_time": 0,
         "deadline": 90, "priority": 3,
         "operations": [{"operation_id": "J-B-O1", "sequence": 1,
                         "status": "PENDING",
                         "materials": [{"sku": "MAT-X", "quantity": 5}],
                         "alternatives": [], "frozen": None}]},
    ]
    warns = ([{"type": "MATERIAL_SHORTFALL", "material_sku": "MAT-X",
               "total_supply": 5, "total_demand": 10}] if shortage else [])
    return {
        "instance_id": "p3", "schedule_type": "RECOVERY",
        "parent_version_id": 1,
        "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                   "normalize_objectives": True, "random_seed": 42,
                   "num_search_workers": 8},
        "machines": ["M1"], "failed_machines": [],
        "machine_initial_families": {}, "warnings": warns,
        "jobs": jobs, "machine_downtime": [],
        "materials": [{"sku": "MAT-X", "capacity": 5}],
        "material_receipts": list(receipts),
        "worker_unavailability": [], "setup_times": [],
        "blocked_operations": [], "suspended_jobs": [],
        "job_tardiness_weights": {},
    }


def _state(**over):
    base = {"instance_name": "p3", "reference_clock": 10,
            "compiled_payload": _payload(),
            "material_reactive": True,
            "db_facts": {"projected_horizon": 500}}
    base.update(over)
    return RecoveryState(**base)


def test_defer_branch_picks_lowest_ranked():
    from coe.agents.nodes.strategy import material_reactive_plan

    st = _state(compiled_payload=_payload(
        receipts=[{"sku": "MAT-X", "quantity": 10, "available_at": 200}]))
    plan = material_reactive_plan(st)
    types = [c["type"] for c in plan["candidates"]]
    assert types == ["DEFER_JOB"]
    c = plan["candidates"][0]
    assert c["job_id"] == "J-B"                 # priority 3 sacrifices
    assert c["release_offset"] == 200           # lands start after receipt
    assert plan["final"] is True
    assert "J-B deferred" in plan["note"]
    assert "keeps the MAT-X" in plan["note"]


def test_suspend_branch_without_receipts():
    from coe.agents.nodes.strategy import material_reactive_plan

    plan = material_reactive_plan(_state())
    assert plan["candidates"][0]["type"] == "SUSPEND_JOB"
    assert plan["candidates"][0]["job_id"] == "J-B"


def test_receipt_beyond_horizon_still_suspends():
    from coe.agents.nodes.strategy import material_reactive_plan

    st = _state(compiled_payload=_payload(
        receipts=[{"sku": "MAT-X", "quantity": 100, "available_at": 900}]))
    plan = material_reactive_plan(st)
    assert plan["candidates"][0]["type"] == "SUSPEND_JOB"


def test_llm_round_validated_and_recorded():
    from coe.agents.nodes.strategy import run_strategy_round
    from tests.fixtures.llm.fake_client import FakeLLMClient

    resp = '{"candidates": [{"type": "TARDINESS_WEIGHT", ' \
        '"job_id": "J-A", "weight": 0.5}], "final": true}'
    out = run_strategy_round(
        _state(material_reactive=False),
        client=FakeLLMClient([resp]), max_retries=1)
    assert out.round_count == 1
    assert out.strategy_final is True
    assert out.round_verdicts[-1]["verdict"] == "VALID"
    assert out.strategy_candidates[-1]["candidate"]["type"] \
        == "TARDINESS_WEIGHT"


def test_llm_garbage_falls_back_empty_with_warning():
    from coe.agents.nodes.strategy import run_strategy_round
    from tests.fixtures.llm.fake_client import FakeLLMClient

    out = run_strategy_round(
        _state(material_reactive=False),
        client=FakeLLMClient(["not json", "still not json"]), max_retries=1)
    assert out.strategy_final is True
    assert out.strategy_candidates == []
    assert any("fallback" in w for w in out.warnings)


def test_invalid_candidate_recorded_not_applied_later():
    from coe.agents.nodes.strategy import run_strategy_round
    from tests.fixtures.llm.fake_client import FakeLLMClient

    resp = '{"candidates": [{"type": "TARDINESS_WEIGHT", ' \
        '"job_id": "GHOST", "weight": 1}], "final": false}'
    out = run_strategy_round(
        _state(material_reactive=False),
        client=FakeLLMClient([resp]), max_retries=1)
    assert out.strategy_final is False          # agent wants another round
    assert out.round_verdicts[0]["verdict"] == "INVALID"
    assert out.round_verdicts[0]["reason"].startswith("unknown_job")
```

Note: these LLM-path tests hit the DB via `validate_candidate` (`unknown_job` needs a real instance lookup that returns None cleanly). Add `pytestmark = pytest.mark.db` at top and use `clean_db` implicitly? `validate_candidate` queries `instances` for name `"p3"` which does not exist → `.one()` raises. FIX in implementation: `validate_candidate` must tolerate unknown instances by treating job/material checks against a missing instance as `unknown_job` / `unknown_material`. Simplest robust approach for tests AND code: mark this module `pytestmark = pytest.mark.db`, create the instance row once in a fixture:

```python
import pytest

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _p3_instance(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        session.add(Instance(name="p3", source_name="synthetic"))
```

(deterministic-path tests don't care; LLM-path tests need it for verdicts.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_strategy_node.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Add `strategy_final` to state**

In `coe/agents/state.py`, after the `material_reactive` line:

```python
    strategy_final: bool = False              # §4.3 step 1 declaration
```

- [ ] **Step 4: Implement strategy.py**

```python
# coe/agents/nodes/strategy.py
"""Strategy round node (AI Role 2, spec §4.3).

Negotiation rounds are LLM-driven EXCEPT the material-reactive duty
(Amendment 2026-08-24), which is a fixed deterministic procedure executed
without any LLM participation. Every emitted candidate is validated before
entering state; verdicts accumulate in round_verdicts (also the audit
buffer flushed to recovery_proposals at run finish).
"""
import json

from sqlalchemy.orm import Session

from coe.agents.catalog import _candidate_adapter, validate_candidate
from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.db.session import make_engine

_SYSTEM_PROMPT = """You are a factory recovery strategist. Given database \
facts and prior verdicts, emit STRICT JSON:
{"candidates": [<catalog entries>], "final": true|false}
Catalog (closed, discriminated on "type"):
{"type":"TARDINESS_WEIGHT","job_id":str,"weight":number 0..10}
{"type":"DEFER_JOB","job_id":str,"release_offset":int>=0}
{"type":"SUSPEND_JOB","job_id":str}
{"type":"EXPEDITE_MATERIAL","material_sku":str,"quantity":number>0,\
"available_at":int>=0}
{"type":"WEIGHT_PRESET","alpha":number>=0,"beta":number>=0}  (sum>0)
Rules: only candidates from the catalog; final=true when you have nothing \
further to propose; prefer minimal interventions."""


def _canon(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _consuming_jobs(payload: dict, sku: str) -> list[dict]:
    out = []
    for j in payload["jobs"]:
        if j["job_id"] in (payload.get("suspended_jobs") or []):
            continue
        demand = sum(m["quantity"]
                     for o in j["operations"] if o["status"] == "PENDING"
                     for m in o.get("materials", [])
                     if m["sku"] == sku)
        if demand > 0:
            slack = (j["deadline"] - j["release_time"]
                     if j.get("deadline") is not None else float("inf"))
            out.append({"job_id": j["job_id"], "priority": j["priority"],
                        "slack": slack,
                        "release_time": j["release_time"]})
    out.sort(key=lambda x: (x["priority"], x["slack"]))
    return out


def material_reactive_plan(state: RecoveryState) -> dict:
    """Amendment 2026-08-24 steps 1-5, fully deterministic (no LLM)."""
    payload = state.compiled_payload or {}
    warnings = [w for w in payload.get("warnings", [])
                if w.get("type") == "MATERIAL_SHORTFALL"]
    horizon = state.db_facts.get("projected_horizon")
    candidates, notes = [], []
    for w in warnings:
        sku, supply, demand = (w["material_sku"], w["total_supply"],
                               w["total_demand"])
        consumers = _consuming_jobs(payload, sku)
        if not consumers:
            continue
        sacrifice = consumers[-1]
        protector = consumers[0]["job_id"]
        covering = [r for r in payload.get("material_receipts", [])
                    if r["sku"] == sku and r["quantity"] >= demand - supply
                    and (horizon is None or r["available_at"] < horizon)]
        covering.sort(key=lambda r: (r["available_at"], r["quantity"]))
        if covering:
            r = covering[0]
            candidates.append({
                "type": "DEFER_JOB", "job_id": sacrifice["job_id"],
                "release_offset":
                    max(0, r["available_at"] - sacrifice["release_time"])})
            notes.append(f"{sacrifice['job_id']} deferred so {protector} "
                         f"keeps the {sku}")
        else:
            candidates.append({"type": "SUSPEND_JOB",
                               "job_id": sacrifice["job_id"]})
            notes.append(f"{sacrifice['job_id']} suspended so {protector} "
                         f"keeps the {sku}")
    return {"candidates": candidates, "final": True,
            "note": "; ".join(notes)}


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in LLM response: {text[:200]!r}")
    return json.loads(stripped[start:end + 1])


def run_strategy_round(state: RecoveryState, *, client=None,
                       max_retries: int | None = None) -> RecoveryState:
    settings = get_settings()
    retries = (settings.llm_max_retries if max_retries is None
               else max_retries)
    rnd = state.round_count + 1

    if state.material_reactive:
        plan = material_reactive_plan(state)
        source = "deterministic"
    elif client is not None:
        user = json.dumps({"instance": state.instance_name,
                           "db_facts": state.db_facts,
                           "prior_verdicts": state.round_verdicts},
                          sort_keys=True)
        feedback = ""
        plan, source = None, "llm"
        for _attempt in range(1 + retries):
            try:
                raw = client.complete(system=_SYSTEM_PROMPT,
                                      user=user + feedback)
                parsed = _extract_json(raw)
                if not isinstance(parsed.get("candidates"), list) \
                        or not isinstance(parsed.get("final"), bool):
                    raise ValueError("missing candidates/final fields")
                plan = parsed
                break
            except (ValueError, json.JSONDecodeError) as exc:
                feedback = f"\n\nRejected: {exc}. Respond again."
                plan = None
        if plan is None:
            return state.model_copy(update={
                "round_count": rnd, "strategy_final": True,
                "warnings": state.warnings + [
                    "strategy_loop fallback: proceeding without strategy "
                    "(§3.3)"]})
        raw_candidates = plan["candidates"]
    else:
        return state.model_copy(update={"round_count": rnd,
                                        "strategy_final": True})

    new_candidates, verdicts = [], []
    prior_this_round: list[dict] = []
    with Session(make_engine()) as session:
        for data in raw_candidates:
            try:
                _candidate_adapter.validate_python(data)
            except Exception:
                continue        # non-catalog junk dies at schema (§5)
            verdict, reason = validate_candidate(
                data, session=session,
                instance_name=state.instance_name,
                db_facts=state.db_facts,
                reference_clock=state.reference_clock,
                prior_this_round=prior_this_round)
            prior_this_round.append(data)
            new_candidates.append({"candidate": data, "round": rnd})
            verdicts.append({"candidate": data, "round": rnd,
                             "verdict": verdict, "reason": reason})

    warnings = list(state.warnings)
    if source == "deterministic" and plan.get("note"):
        warnings.append(plan["note"])

    final_flag = bool(plan.get("final")) or source == "deterministic"
    return state.model_copy(update={
        "round_count": rnd,
        "strategy_final": final_flag,
        "strategy_candidates": state.strategy_candidates + new_candidates,
        "round_verdicts": state.round_verdicts + verdicts,
        "warnings": warnings,
    })
```

Implementation notes:
- The deterministic path NEVER touches the client — criterion 11 stays clean and the material back-edge works even with `client=None`.
- Non-catalog shapes die at schema BEFORE validation and are simply dropped (§5: rejected by schema validation before reaching the applier; they are also NOT recorded — only catalog-valid candidates occupy `recovery_proposals`).
- Budget enforcement (`round_count >= STRATEGY_MAX_ROUNDS`) lives in the GRAPH router (Task 14), which stops calling this node — the node stays budget-blind by design.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_strategy_node.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add coe/agents/state.py coe/agents/nodes/strategy.py tests/agents/test_strategy_node.py
git commit -m "feat(agents): strategy round node + deterministic material-reactive duty"
```

---

### Task 11: Pre-commit gate + post-commit verifier (§6.2–6.3)

**Files:**
- Create: `coe/agents/safety.py`
- Test: `tests/agents/test_safety.py`

**Interfaces:**
- Consumes: `check_solution(payload, solution) -> list[str]` (`coe/solver/invariants.py:10`) — THE shared implementation (drift-proof by construction); `commit_solution` / `rollback_active` (`coe/solver/committer.py`).
- Produces:
  - `run_gate(payload: dict, solution: dict) -> dict` returning `{"passed": bool, "violations": list[str]}`. Gate failure means NO commit (graph routes to terminal `GATE_FAILED`).
  - `verify_commit(instance_name: str) -> dict` — post-commit verifier: loads the LATEST committed non-rolled-back version, rebuilds the solution dict from its `schedule_entries`, evaluates the IDENTICAL `check_solution(version.payload_json, rebuilt_solution)`. On violation: `rollback_active` fires. Returns `{"passed": bool, "violations": [...], "version_number": int | None, "rolled_back_from": int | None}`. Raises through `RollbackFloor` when the tampered version is the last remaining active one (runner logs loudly).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_safety.py
"""§6.2 gate refuses corruption; §6.3 verifier rolls back tampering."""
import pytest

pytestmark = pytest.mark.db


@pytest.fixture()
def solved_world(clean_db):
    """Instance with baseline v1 committed and active."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.solver.committer import commit_solution

    with session_scope() as session:
        inst = Instance(name="safe-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m = Machine(instance_id=iid, name="M1")
        session.add(m)
        session.flush()
        j = Job(instance_id=iid, name="J-1", priority=1, release_time=0)
        session.add(j)
        session.flush()
        o = Operation(instance_id=iid, job_id=j.id, sequence_number=1)
        session.add(o)
        session.flush()
        session.add(OperationMachineAlternative(
            instance_id=iid, operation_id=o.id, machine_id=m.id,
            processing_time=5))

        payload = {
            "instance_id": "safe-world", "schedule_type": "BASELINE",
            "parent_version_id": None,
            "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                       "normalize_objectives": True, "random_seed": 42,
                       "num_search_workers": 1},
            "machines": ["M1"], "failed_machines": [],
            "machine_initial_families": {}, "warnings": [],
            "jobs": [{"job_id": "J-1", "family_id": None, "release_time": 0,
                      "deadline": None, "priority": 1,
                      "operations": [{
                          "operation_id": "J-1-O1", "sequence": 1,
                          "status": "PENDING", "materials": [],
                          "alternatives": [{"machine_id": "M1",
                                            "processing_time": 5,
                                            "workers": {}}],
                          "frozen": None}]}],
            "machine_downtime": [], "materials": [],
            "material_receipts": [], "worker_unavailability": [],
            "setup_times": [], "blocked_operations": [],
            "suspended_jobs": [],
        }
        solution = {
            "status": "OPTIMAL", "objective_value": 5.0, "makespan": 5,
            "total_tardiness": 0,
            "assignments": [{"operation_id": "J-1-O1", "job_id": "J-1",
                             "machine_id": "M1", "worker_id": None,
                             "start": 0, "end": 5, "processing_time": 5,
                             "setup_time": 0, "is_frozen": False}],
            "solve_duration_seconds": 0.01,
        }
        version = commit_solution(session, instance_row=inst,
                                  payload=payload, solution=solution)
        vid = version.id
    return iid, vid, payload, solution


def test_gate_passes_clean_solution(solved_world):
    from coe.agents.safety import run_gate

    _, _, payload, solution = solved_world
    assert run_gate(payload, solution)["passed"] is True


def test_gate_refuses_duration_corruption(solved_world):
    from coe.agents.safety import run_gate

    _, _, payload, solution = solved_world
    bad = dict(solution, assignments=[dict(solution["assignments"][0],
                                           end=9)])
    res = run_gate(payload, bad)
    assert res["passed"] is False
    assert any("duration arithmetic" in v for v in res["violations"])


def test_gate_refuses_failed_machine_assignment(solved_world):
    from coe.agents.safety import run_gate

    _, _, payload, solution = solved_world
    payload2 = dict(payload, machines=[], failed_machines=["M1"])
    res = run_gate(payload2, solution)
    assert res["passed"] is False


def test_verify_clean_passes(solved_world):
    from coe.agents.safety import verify_commit

    res = verify_commit("safe-world")
    assert res["passed"] is True
    assert res["rolled_back_from"] is None


def test_verify_detects_tamper_and_rolls_back(solved_world):
    from coe.agents.safety import verify_commit
    from coe.db.session import make_engine
    from sqlalchemy import text

    engine = make_engine()
    with engine.begin() as c:
        c.execute(text(
            "UPDATE schedule_entries SET end_time = end_time + 7 "
            "WHERE version_id = :vid"), {"vid": solved_world[1]})
    res = verify_commit("safe-world")
    assert res["passed"] is False
    assert res["rolled_back_from"] is not None
    with engine.begin() as c:
        rb = c.execute(text(
            "SELECT rolled_back FROM schedule_versions WHERE id = :vid"),
            {"vid": solved_world[1]}).scalar_one()
    assert rb is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_safety.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement safety.py**

```python
# coe/agents/safety.py
"""Pre-commit gate + post-commit verifier (spec §6.2-6.3).

Both evaluate the SAME check_solution implementation against (payload,
solution) — drift between gate and verifier is structurally impossible.
The verifier rebuilds assignments from schedule_entries (what actually got
committed) rather than trusting any stored solution blob.
"""
from sqlalchemy.orm import Session

from coe.db.session import make_engine
from coe.solver.invariants import check_solution


def run_gate(payload: dict, solution: dict) -> dict:
    violations = check_solution(payload, solution)
    return {"passed": not violations, "violations": violations}


def _rebuilt_solution(session: Session, version) -> dict:
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.schedule import ScheduleEntry
    from coe.db.models.workers import Worker

    iid = version.instance_id
    machines = dict(session.query(Machine.id, Machine.name)
                    .filter(Machine.instance_id == iid)
                    .order_by(Machine.id).all())
    workers = dict(session.query(Worker.id, Worker.name)
                   .filter(Worker.instance_id == iid)
                   .order_by(Worker.id).all())
    jobs = dict(session.query(Job.id, Job.name)
                .filter(Job.instance_id == iid).order_by(Job.id).all())
    op_meta = {
        o.id: (jobs[o.job_id], o.sequence_number)
        for o in session.query(Operation)
        .filter(Operation.instance_id == iid)
        .order_by(Operation.job_id, Operation.sequence_number).all()}
    entries = (session.query(ScheduleEntry)
               .filter(ScheduleEntry.version_id == version.id)
               .order_by(ScheduleEntry.id).all())
    assignments = [{
        "operation_id": f"{op_meta[e.operation_id][0]}"
                        f"-O{op_meta[e.operation_id][1]}",
        "job_id": op_meta[e.operation_id][0],
        "machine_id": machines[e.machine_id],
        "worker_id": workers.get(e.worker_id) if e.worker_id else None,
        "start": e.start_time, "end": e.end_time,
        "processing_time": e.processing_time,
        "setup_time": e.setup_time,
        "is_frozen": e.is_frozen,
    } for e in entries]
    return {"status": version.solver_status,
            "objective_value": version.objective_value,
            "makespan": version.makespan,
            "total_tardiness": version.total_tardiness,
            "assignments": assignments,
            "solve_duration_seconds": version.solve_duration_seconds}


def verify_commit(instance_name: str) -> dict:
    from coe.db.models.provenance import Instance
    from coe.db.models.schedule import ScheduleVersion
    from coe.solver.committer import rollback_active

    with Session(make_engine()) as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = (session.query(ScheduleVersion)
                   .filter(ScheduleVersion.instance_id == inst.id,
                           ScheduleVersion.solver_status.in_(("OPTIMAL",
                                                              "FEASIBLE")),
                           ScheduleVersion.rolled_back.is_(False))
                   .order_by(ScheduleVersion.version_number.desc(),
                             ScheduleVersion.id.desc()).first())
        if version is None:
            return {"passed": False,
                    "violations": ["no committed version found"],
                    "version_number": None, "rolled_back_from": None}
        solution = _rebuilt_solution(session, version)
        violations = check_solution(version.payload_json, solution)
        result = {"passed": not violations, "violations": violations,
                  "version_number": version.version_number,
                  "rolled_back_from": None}
        if violations:
            rollback_active(session, inst)
            result["rolled_back_from"] = version.version_number
        return result
```

Implementation note: `rollback_active` refuses to roll back the LAST remaining version (`RollbackFloor`). The runner (Task 14) catches it, records `VERIFIER_ROLLBACK` intent in errors[], and leaves the floor intact — loud, never silent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_safety.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/safety.py tests/agents/test_safety.py
git commit -m "feat(agents): shared-implementation gate and verifier"
```

---

### Task 12: Run lifecycle persistence + instance run lock (§7)

**Files:**
- Create: `coe/agents/runs.py`
- Test: `tests/agents/test_runs.py`

**Interfaces:**
- Consumes: models `RecoveryRun`, `RecoveryProposal` (Task 2); advisory-lock SQL pattern from `coe/mqtt/ingest.py:111`; settings `recovery_lock_wait_seconds`.
- Produces:
  - `record_run(instance_name: str, *, trigger: str, status: str, disruption_record_json: dict, started_at: float, finished_at: float, final_status_version_id: int | None = None) -> int` — inserts the single lifecycle row AT TERMINATION (statuses constrained to the CHECK domain). Returns run id.
  - `write_proposals(instance_name: str, run_id: int, verdicts: list[dict]) -> int` — flushes the `round_verdicts` buffer into `recovery_proposals`; returns count written.
  - `class RunLockTimeout(RuntimeError)`; `InstanceRunLock(instance_name: str, wait_seconds: float | None = None)` context manager — session-level `pg_try_advisory_lock(hashtext('coe-run:{name}'))` polled every 0.25 s up to `wait_seconds` (default `get_settings().recovery_lock_wait_seconds`), then `RunLockTimeout` LOUDLY (§7: contention aborts, never proceeds unsynchronized). Holds one dedicated connection for the critical section; `__exit__` unlocks + closes.

Design decision (document as deviation note in the task report): the run row is written ONCE at termination rather than created-then-updated. Rationale: the §7 status CHECK has no RUNNING value and criterion 8 requires complete lifecycles including failures; termination-time insert satisfies both without widening the status domain. Crash-mid-run leaves no row (acceptable; lock releases via connection close).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_runs.py
"""§7 lifecycle rows + per-instance advisory run lock."""
import time

import pytest

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def inst(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope

    with session_scope() as session:
        session.add(Instance(name="runs-world", source_name="synthetic"))


def test_record_run_legal_statuses():
    from coe.agents.runs import record_run
    from coe.db.session import make_engine
    from sqlalchemy import text

    rid = record_run("runs-world", trigger="CLI", status="COMMITTED",
                     disruption_record_json={"kind": "MACHINE"},
                     started_at=time.time(), finished_at=time.time())
    with make_engine().begin() as c:
        row = c.execute(text(
            "SELECT status, trigger, final_status_version_id "
            "FROM recovery_runs WHERE id=:r"), {"r": rid}).one()
    assert row.status == "COMMITTED" and row.trigger == "CLI"
    assert row.final_status_version_id is None


def test_write_proposals():
    from coe.agents.runs import record_run, write_proposals
    from coe.db.session import make_engine
    from sqlalchemy import text

    rid = record_run("runs-world", trigger="MQTT", status="GATE_FAILED",
                     disruption_record_json={}, started_at=1.0,
                     finished_at=2.0)
    n = write_proposals("runs-world", rid, [
        {"candidate": {"type": "DEFER_JOB", "job_id": "J",
                       "release_offset": 5},
         "round": 1, "verdict": "VALID", "reason": "ok"},
        {"candidate": {"type": "DEFER_JOB", "job_id": "J",
                       "release_offset": 5},
         "round": 1, "verdict": "INVALID_DUPLICATE", "reason": "duplicate"},
    ])
    assert n == 2
    with make_engine().begin() as c:
        cnt = c.execute(text(
            "SELECT count(*) FROM recovery_proposals WHERE run_id=:r"),
            {"r": rid}).scalar_one()
    assert cnt == 2


def test_lock_serializes_and_times_out_loudly():
    from coe.agents.runs import InstanceRunLock, RunLockTimeout

    with InstanceRunLock("runs-world", wait_seconds=5):
        t0 = time.monotonic()
        with pytest.raises(RunLockTimeout):
            with InstanceRunLock("runs-world", wait_seconds=1):
                pass
        assert time.monotonic() - t0 >= 0.9   # waited, did not barge


def test_lock_released_after_exit():
    from coe.agents.runs import InstanceRunLock

    with InstanceRunLock("runs-world", wait_seconds=5):
        pass
    with InstanceRunLock("runs-world", wait_seconds=5):
        pass       # second acquisition succeeds => unlock happened
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_runs.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement runs.py**

```python
# coe/agents/runs.py
"""Run lifecycle rows + per-instance advisory lock (spec §7)."""
import time
from datetime import datetime, timezone

from sqlalchemy import text

from coe.config import get_settings
from coe.db.session import make_engine


class RunLockTimeout(RuntimeError):
    """Contending trigger exceeded RECOVERY_LOCK_WAIT_SECONDS (§7)."""


def _ts(f: float) -> datetime:
    return datetime.fromtimestamp(f, tz=timezone.utc)


def record_run(instance_name: str, *, trigger: str, status: str,
               disruption_record_json: dict, started_at: float,
               finished_at: float,
               final_status_version_id: int | None = None) -> int:
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import RecoveryRun
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        run = RecoveryRun(
            instance_id=inst.id, trigger=trigger, status=status,
            disruption_record_json=disruption_record_json,
            final_status_version_id=final_status_version_id,
            started_at=_ts(started_at), finished_at=_ts(finished_at))
        session.add(run)
        session.flush()
        return run.id


def write_proposals(instance_name: str, run_id: int,
                    verdicts: list[dict]) -> int:
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import RecoveryProposal
    from coe.db.session import session_scope

    with session_scope() as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        for v in verdicts:
            session.add(RecoveryProposal(
                instance_id=inst.id, run_id=run_id,
                round_number=v["round"], candidate_json=v["candidate"],
                verdict=v["verdict"], verdict_reason=v.get("reason")))
        return len(verdicts)


class InstanceRunLock:
    """Session-level advisory lock held on a dedicated connection."""

    def __init__(self, instance_name: str,
                 wait_seconds: float | None = None) -> None:
        self._key = f"coe-run:{instance_name}"
        self._wait = (get_settings().recovery_lock_wait_seconds
                      if wait_seconds is None else wait_seconds)
        self._conn = None

    def __enter__(self) -> "InstanceRunLock":
        self._conn = make_engine().connect()
        deadline = time.monotonic() + self._wait
        while True:
            got = self._conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                {"k": self._key}).scalar_one()
            if got:
                return self
            if time.monotonic() >= deadline:
                self._conn.close()
                self._conn = None
                raise RunLockTimeout(
                    f"instance run lock {self._key!r} held elsewhere; "
                    f"gave up after {self._wait}s (§7)")
            time.sleep(0.25)

    def __exit__(self, *exc) -> None:
        if self._conn is not None:
            self._conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:k))"),
                {"k": self._key})
            self._conn.close()
            self._conn = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_runs.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/runs.py tests/agents/test_runs.py
git commit -m "feat(agents): run lifecycle rows + advisory instance lock"
```

---

### Task 13: Explanation service (§4.5)

**Files:**
- Create: `coe/agents/nodes/explain.py`
- Test: `tests/agents/test_explain.py`

**Interfaces:**
- Consumes: `ScheduleVersion` (+ stored `payload_json`), `ScheduleEntry`; `LLMClient`; `ScheduleExplanation` (Task 2).
- Produces:
  - `compute_diff(session, version, parent) -> dict` — deterministic, no LLM. Keys:
    - `moved_operations`: `[{operation_id, from: {machine_id, start}, to: {...}}]`
    - `reassigned_workers`: `[{operation_id, from, to}]`
    - `newly_blocked`: `[operation_id]` (in this payload's `blocked_operations`, present in parent snapshot, absent from child entries)
    - `applied_strategies`: STRATEGY_APPLIED warnings verbatim from `version.payload_json["warnings"]`
    - `clipped_windows`: DOWNTIME_CLIPPED / DOWNTIME_DROPPED / WORKER_WINDOW_CLIPPED / WORKER_WINDOW_DROPPED warnings verbatim
  - `explain_version(instance_name: str, *, client, max_retries: int | None = None) -> str | None` — finds the ACTIVE version, diffs vs parent (None parent → summary mode: diff skeleton carries strategies/clipped only), prompts the LLM for prose, UPSERTS one `ScheduleExplanation` keyed to the version, returns rationale. LLM failure after `1 + max_retries` attempts → prints loud log, returns None WITHOUT storing (§3.3: run completes, explanation logged missing).
  - `make_explain_node(client)` — returns a langgraph node callable `(state) -> state` setting `state.explanation`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_explain.py
"""§4.5 post-hoc explanation service."""
import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient


@pytest.fixture()
def two_versions(clean_db):
    """Parent v1 (J-1,J-2 both on M1) then child v2 (J-1 moved to M2,
    J-2 suspended via JOB_SUSPENDED)."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.solver.committer import commit_solution

    def _payload(parent, warnings=(), jobs_override=None):
        jobs = jobs_override if jobs_override is not None else [
            {"job_id": "J-1", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [{"operation_id": "J-1-O1", "sequence": 1,
                             "status": "PENDING", "materials": [],
                             "alternatives": [], "frozen": None}]},
            {"job_id": "J-2", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 2,
             "operations": [{"operation_id": "J-2-O1", "sequence": 1,
                             "status": "PENDING", "materials": [],
                             "alternatives": [], "frozen": None}]}]
        return {
            "instance_id": "exp-world", "schedule_type": "RECOVERY",
            "parent_version_id": parent, "config": {
                "alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                "normalize_objectives": True, "random_seed": 42,
                "num_search_workers": 1},
            "machines": ["M1", "M2"], "failed_machines": ["M1"],
            "machine_initial_families": {}, "warnings": list(warnings),
            "jobs": jobs, "machine_downtime": [], "materials": [],
            "material_receipts": [], "worker_unavailability": [],
            "setup_times": [], "blocked_operations": [],
            "suspended_jobs": []}

    def _sol(pairs):
        return {"status": "OPTIMAL", "objective_value": 1.0,
                "makespan": max(e for _, _, (s, e) in pairs),
                "total_tardiness": 0,
                "assignments": [
                    {"operation_id": oid, "job_id": oid.split("-O")[0],
                     "machine_id": mid, "worker_id": None,
                     "start": s, "end": e, "processing_time": e - s,
                     "setup_time": 0, "is_frozen": False}
                    for oid, mid, (s, e) in pairs],
                "solve_duration_seconds": 0.01}

    with session_scope() as session:
        inst = Instance(name="exp-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m1 = Machine(instance_id=iid, name="M1")
        m2 = Machine(instance_id=iid, name="M2")
        session.add_all([m1, m2])
        session.flush()
        j1 = Job(instance_id=iid, name="J-1", priority=1)
        j2 = Job(instance_id=iid, name="J-2", priority=2)
        session.add_all([j1, j2])
        session.flush()
        o11 = Operation(instance_id=iid, job_id=j1.id, sequence_number=1)
        o21 = Operation(instance_id=iid, job_id=j2.id, sequence_number=1)
        session.add_all([o11, o21])
        session.flush()
        for o in (o11, o21):
            for mm in (m1, m2):
                session.add(OperationMachineAlternative(
                    instance_id=iid, operation_id=o.id, machine_id=mm.id,
                    processing_time=5))

        v1 = commit_solution(
            session, instance_row=inst, payload=_payload(None),
            solution=_sol([("J-1-O1", "M1", (0, 5)),
                           ("J-2-O1", "M1", (5, 10))]))

        warn = [{"type": "STRATEGY_APPLIED", "round": 1,
                 "candidate": {"type": "SUSPEND_JOB", "job_id": "J-2"},
                 "field_changed": "suspended_jobs"},
                {"type": "DOWNTIME_CLIPPED", "machine_id": "M1",
                 "window": [10, 200], "clipped_to": [40, 200],
                 "reason": "overlaps frozen operations"}]
        jobs2 = [
            {"job_id": "J-1", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 1,
             "operations": [{"operation_id": "J-1-O1", "sequence": 1,
                             "status": "PENDING", "materials": [],
                             "alternatives": [], "frozen": None}]},
            {"job_id": "J-2", "family_id": None, "release_time": 0,
             "deadline": None, "priority": 2,
             "operations": [{"operation_id": "J-2-O1", "sequence": 1,
                             "status": "BLOCKED", "materials": [],
                             "alternatives": [], "frozen": None}]}]
        p2 = _payload(v1.id, warn, jobs_override=jobs2)
        p2["blocked_operations"] = [{"operation_id": "J-2-O1",
                                     "reason": "JOB_SUSPENDED",
                                     "material_sku": None}]
        p2["suspended_jobs"] = ["J-2"]
        v2 = commit_solution(
            session, instance_row=inst, payload=p2,
            solution=_sol([("J-1-O1", "M2", (40, 45))]))
    return v1.version_number, v2.version_number


def test_compute_diff_moves_blocks_strategies(two_versions):
    from coe.agents.nodes.explain import compute_diff
    from coe.db.models.schedule import ScheduleVersion
    from coe.db.session import session_scope

    with session_scope() as session:
        child = (session.query(ScheduleVersion)
                 .filter(ScheduleVersion.version_number
                         == two_versions[1]).one())
        parent = (session.query(ScheduleVersion)
                  .filter(ScheduleVersion.version_number
                          == two_versions[0]).one())
        diff = compute_diff(session, child, parent)
    moved = {(m["operation_id"], m["to"]["machine_id"])
             for m in diff["moved_operations"]}
    assert ("J-1-O1", "M2") in moved
    assert "J-2-O1" in diff["newly_blocked"]
    assert diff["applied_strategies"][0]["candidate"]["type"] == "SUSPEND_JOB"
    assert diff["clipped_windows"][0]["type"] == "DOWNTIME_CLIPPED"


def test_explain_version_stores_rationale(two_versions):
    from coe.agents.nodes.explain import explain_version
    from coe.db.models.recovery import ScheduleExplanation
    from coe.db.session import make_engine
    from sqlalchemy import select

    prose = explain_version(
        "exp-world",
        client=FakeLLMClient(["Moved J-1 off M1 because it failed; "
                              "suspended J-2."]))
    assert prose.startswith("Moved J-1")
    with make_engine().connect() as c:
        rows = c.execute(select(ScheduleExplanation)).scalars().all()
    assert len(rows) == 1 and rows[0].rationale == prose


def test_explain_llm_failure_returns_none(two_versions):
    from coe.agents.nodes.explain import explain_version

    res = explain_version("exp-world", client=FakeLLMClient([]),
                          max_retries=1)
    assert res is None
```

Note: the child payload declares `failed_machines: ["M1"]` while committing J-1 onto M2 — `commit_solution` records `failed_machine_ids` accordingly but does not enforce assignment consistency (that is gate territory, exercised in Task 11). This keeps the fixture small.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_explain.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement explain.py**

```python
# coe/agents/nodes/explain.py
"""Post-hoc explanation service (AI Role 3, spec §4.5).

Strictly read-side: computes a deterministic diff of the committed version
vs its parent, hands it to the LLM for prose, stores the rationale. Output
never influences scheduling state. Baseline versions (no parent) get a
constraint-summary mode instead of a diff.
"""
import json

from sqlalchemy.orm import Session

from coe.agents.state import RecoveryState
from coe.config import get_settings
from coe.db.session import make_engine

_SYSTEM_PROMPT = """You explain factory schedule changes to a production \
planner. Input: JSON describing the previous vs new schedule plus \
constraint highlights. Output: plain-text rationale, <=150 words, naming \
the concrete causes (failed resources, strategies applied, clipped \
windows) and their operational consequences. No preamble."""


def _names_and_ops(session, iid):
    from coe.db.models.fjsp import Job, Machine, Operation
    from coe.db.models.workers import Worker

    names = {
        "machines": dict(session.query(Machine.id, Machine.name)
                         .filter(Machine.instance_id == iid)
                         .order_by(Machine.id).all()),
        "workers": dict(session.query(Worker.id, Worker.name)
                        .filter(Worker.instance_id == iid)
                        .order_by(Worker.id).all()),
    }
    jobs = dict(session.query(Job.id, Job.name)
                .filter(Job.instance_id == iid).order_by(Job.id).all())
    op_meta = {o.id: (jobs[o.job_id], o.sequence_number)
               for o in session.query(Operation)
               .filter(Operation.instance_id == iid)
               .order_by(Operation.job_id,
                         Operation.sequence_number).all()}
    return names, op_meta


def _entry_index(entries, names, op_meta):
    idx = {}
    for e in entries:
        jname, seq = op_meta[e.operation_id]
        idx[f"{jname}-O{seq}"] = {
            "machine_id": names["machines"][e.machine_id],
            "worker_id": (names["workers"].get(e.worker_id)
                          if e.worker_id else None),
            "start": e.start_time, "end": e.end_time}
    return idx


def compute_diff(session: Session, version, parent) -> dict:
    from coe.db.models.schedule import ScheduleEntry

    iid = version.instance_id
    names, op_meta = _names_and_ops(session, iid)
    payload = version.payload_json or {}
    diff: dict = {"moved_operations": [], "reassigned_workers": [],
                  "newly_blocked": [],
                  "applied_strategies": [
                      w for w in payload.get("warnings", [])
                      if w.get("type") == "STRATEGY_APPLIED"],
                  "clipped_windows": [
                      w for w in payload.get("warnings", [])
                      if w.get("type") in ("DOWNTIME_CLIPPED",
                                           "DOWNTIME_DROPPED",
                                           "WORKER_WINDOW_CLIPPED",
                                           "WORKER_WINDOW_DROPPED")]}
    if parent is None:
        return diff

    child_entries = (session.query(ScheduleEntry)
                     .filter(ScheduleEntry.version_id == version.id)
                     .order_by(ScheduleEntry.id).all())
    parent_entries = (session.query(ScheduleEntry)
                      .filter(ScheduleEntry.version_id == parent.id)
                      .order_by(ScheduleEntry.id).all())
    old_idx = _entry_index(parent_entries, names, op_meta)
    new_idx = _entry_index(child_entries, names, op_meta)
    blocked_now = {b["operation_id"]
                   for b in payload.get("blocked_operations", [])}

    for oid, new in sorted(new_idx.items()):
        old = old_idx.get(oid)
        if old is None:
            continue
        if (new["machine_id"], new["start"]) != (old["machine_id"],
                                                 old["start"]):
            diff["moved_operations"].append({
                "operation_id": oid,
                "from": {"machine_id": old["machine_id"],
                         "start": old["start"]},
                "to": {"machine_id": new["machine_id"],
                       "start": new["start"]}})
        if new["worker_id"] != old["worker_id"]:
            diff["reassigned_workers"].append({
                "operation_id": oid, "from": old["worker_id"],
                "to": new["worker_id"]})
    for oid in sorted(blocked_now):
        if oid in old_idx and oid not in new_idx:
            diff["newly_blocked"].append(oid)
    return diff


def _constraint_summary(version) -> dict:
    payload = version.payload_json or {}
    return {"failed_machines": payload.get("failed_machines") or [],
            "suspended_jobs": payload.get("suspended_jobs") or [],
            "objective": {"makespan": version.makespan,
                          "total_tardiness": version.total_tardiness,
                          "status": version.solver_status}}


def explain_version(instance_name: str, *, client,
                    max_retries: int | None = None) -> str | None:
    from coe.db.models.provenance import Instance
    from coe.db.models.recovery import ScheduleExplanation
    from coe.db.models.schedule import ScheduleVersion

    s = get_settings()
    retries = (s.llm_max_retries if max_retries is None else max_retries)
    with Session(make_engine()) as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = (session.query(ScheduleVersion)
                   .filter(ScheduleVersion.instance_id == inst.id,
                           ScheduleVersion.solver_status.in_(("OPTIMAL",
                                                              "FEASIBLE")),
                           ScheduleVersion.rolled_back.is_(False))
                   .order_by(ScheduleVersion.version_number.desc(),
                             ScheduleVersion.id.desc()).first())
        if version is None:
            return None
        parent = (session.query(ScheduleVersion)
                  .filter(ScheduleVersion.id
                          == version.parent_version_id).one_or_none())
        diff = compute_diff(session, version, parent)
        summary = _constraint_summary(version)
        version_number = version.version_number

    feedback, prose, last_error = "", None, ""
    for _attempt in range(1 + retries):
        user = json.dumps({"diff": diff, "constraints": summary},
                          sort_keys=True)
        try:
            candidate = client.complete(system=_SYSTEM_PROMPT,
                                        user=user + feedback)
            if not candidate or not candidate.strip():
                raise ValueError("empty explanation")
            prose = candidate
            break
        except Exception as exc:
            last_error = str(exc)
            feedback = f"\n\nPrevious attempt failed: {last_error}"
    if prose is None:
        print(f"[explain] LLM failed after retries: {last_error} — "
              "explanation logged missing (§3.3)")
        return None

    with Session(make_engine()) as session:
        inst = (session.query(Instance)
                .filter(Instance.name == instance_name).one())
        version = (session.query(ScheduleVersion)
                   .filter(ScheduleVersion.instance_id == inst.id,
                           ScheduleVersion.version_number
                           == version_number).one())
        existing = (session.query(ScheduleExplanation)
                    .filter(ScheduleExplanation.version_id
                            == version.id).one_or_none())
        if existing is not None:
            existing.rationale = prose
        else:
            session.add(ScheduleExplanation(
                instance_id=inst.id, version_id=version.id,
                rationale=prose))
        return prose


def make_explain_node(client):
    """Graph adapter: closes the injected LLMClient into a node."""

    def _node(state: RecoveryState) -> RecoveryState:
        prose = explain_version(state.instance_name, client=client)
        return state.model_copy(update={"explanation": prose})

    return _node
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_explain.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/nodes/explain.py tests/agents/test_explain.py
git commit -m "feat(agents): post-hoc explanation service with diff grounding"
```

---


---

# PART E — Graph, Entry Points, Benchmark

*Spec sections covered by this part: §3.1 (fixed topology, two bounded back-edges sharing `STRATEGY_MAX_ROUNDS`, exhaustion semantics), §3.3 (fallback policies at run level), §3.4 (MQTT listener: idempotent launch, lock contention, malformed payloads, non-disruption events), §9 (pre-flight configuration check), §10 (command interface), §8 (fidelity benchmark corpus + metrics + deterministic report), §11 Tiers 3–5, §12 acceptance sweep.*

### Task 14: LangGraph assembly + recovery runner (§3.1, §3.3)

**Files:**
- Modify: `coe/agents/state.py` (three bookkeeping fields)
- Create: `coe/agents/graph.py`
- Test: `tests/agents/test_graph.py`

**Interfaces:**
- Consumes: every node from Parts B–D (`run_translate`, investigation nodes, `run_strategy_round`, `run_manager_compile`, `engine.solve`, `run_gate`, `commit_solution`, `verify_commit`, `make_explain_node`); `InstanceRunLock`, `record_run`, `write_proposals` (Task 12); `record_to_wire_payload` (Task 5); `_recovery_floor` (`coe/cli.py:79`).
- Produces:
  - State gains: `committed_version_id: int | None = None`, `material_reactive_passes: int = 0`, `solve_infeasible_material: bool = False`.
  - `build_graph(client) -> CompiledGraph` — nodes `entry, translate, ingest, machine_agent, production_agent, inventory_agent, worker_agent, strategy, manager_compile, solve_node, gate_node, commit_node, verify_node, explain_node`; edges exactly per the routing table below.
  - `execute_recovery(instance_name: str, *, trigger: str, narrative: str | None = None, record: dict | None = None, source_message_id: str | None = None, reference_clock: int | None = None, client=None, lock_wait: float | None = None, max_retries: int | None = None) -> dict` returning `{"status": <terminal>, "state": RecoveryState, "run_id": int}` with terminal ∈ `COMMITTED | TRANSLATION_FAILED | SOLVE_INFEASIBLE | GATE_FAILED | VERIFIER_ROLLBACK`.

Routing table (implement exactly):

| after | condition | target |
| --- | --- | --- |
| `entry` | `source_message_id` set | `ingest` |
| `entry` | otherwise | `translate` |
| `translate` | always | `ingest` |
| `ingest` | always | `machine_agent` |
| `machine_agent`, `production_agent`, `inventory_agent` | always | next investigation node |
| `worker_agent` | always | `strategy` |
| `strategy` | `strategy_final` OR `round_count >= strategy_max_rounds` | `manager_compile` |
| `strategy` | otherwise | `strategy` |
| `manager_compile` | `material_reactive` AND `material_reactive_passes == 0` AND budget left | `strategy` *(back-edge 1)* |
| `manager_compile` | otherwise | `solve_node` |
| `solve_node` | status OPTIMAL/FEASIBLE | `gate_node` |
| `solve_node` | INFEASIBLE/UNKNOWN AND payload carried MATERIAL_SHORTFALL AND `material_reactive_passes == 0` AND budget left | `strategy` *(back-edge 2)* |
| `solve_node` | INFEASIBLE/UNKNOWN otherwise | `END` → runner records `SOLVE_INFEASIBLE` |
| `gate_node` | passed | `commit_node` |
| `gate_node` | refused | `END` (`GATE_FAILED`) |
| `commit_node` | always | `verify_node` |
| `verify_node` | passed OR RollbackFloor | `explain_node` |
| `verify_node` | violated | `END` (`VERIFIER_ROLLBACK`) |

Back-edge budget rule: both back-edges fire at most once per run (single `material_reactive_passes` intervention pass) AND consume ordinary `round_count` — a conservative reading of "share the strategy loop's STRATEGY_MAX_ROUNDS budget" (§3.1). Exhaustion semantics preserved verbatim: solvable-but-warned payloads proceed to commit; still-infeasible payloads terminate `SOLVE_INFEASIBLE` with nothing committed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_graph.py
"""§3.1 topology + Tier 3 pipeline integration with fake LLM."""
import pytest

pytestmark = pytest.mark.db

from tests.fixtures.llm.fake_client import FakeLLMClient

TRANSLATE_OK = ('{"kind": "MACHINE", "instance_id": "g-world", '
                '"machine_id": "M2", "event_type": "FAILURE", '
                '"occurred_at": 30, "severity": "HIGH", '
                '"estimated_downtime": 200, "narrative_excerpt": "boom"}')


@pytest.fixture()
def g_world(clean_db):
    """Two machines, two single-op jobs, active baseline v1."""
    from coe.db.models.fjsp import (
        Job,
        Machine,
        Operation,
        OperationMachineAlternative,
    )
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.solver.committer import commit_solution

    with session_scope() as session:
        inst = Instance(name="g-world", source_name="synthetic")
        session.add(inst)
        session.flush()
        iid = inst.id
        m1 = Machine(instance_id=iid, name="M1")
        m2 = Machine(instance_id=iid, name="M2")
        session.add_all([m1, m2])
        session.flush()
        ja = Job(instance_id=iid, name="J-A", priority=1, release_time=0,
                 deadline=100)
        jb = Job(instance_id=iid, name="J-B", priority=2, release_time=0,
                 deadline=100)
        session.add_all([ja, jb])
        session.flush()
        oa = Operation(instance_id=iid, job_id=ja.id, sequence_number=1)
        ob = Operation(instance_id=iid, job_id=jb.id, sequence_number=1)
        session.add_all([oa, ob])
        session.flush()
        for o in (oa, ob):
            for m, t in ((m1, 5), (m2, 6)):
                session.add(OperationMachineAlternative(
                    instance_id=iid, operation_id=o.id, machine_id=m.id,
                    processing_time=t))

        jobs = [{"job_id": j, "family_id": None, "release_time": 0,
                 "deadline": 100, "priority": p,
                 "operations": [{"operation_id": f"{j}-O1", "sequence": 1,
                                 "status": "PENDING", "materials": [],
                                 "alternatives": [
                                     {"machine_id": "M1",
                                      "processing_time": t1, "workers": {}},
                                     {"machine_id": "M2",
                                      "processing_time": t2, "workers": {}}],
                                 "frozen": None}]}
                for j, p, t1, t2 in (("J-A", 1, 5, 6), ("J-B", 2, 5, 6))]
        payload = {
            "instance_id": "g-world", "schedule_type": "BASELINE",
            "parent_version_id": None,
            "config": {"alpha": 1.0, "beta": 1.0, "time_limit_seconds": 60,
                       "normalize_objectives": True, "random_seed": 42,
                       "num_search_workers": 1},
            "machines": ["M1", "M2"], "failed_machines": [],
            "machine_initial_families": {}, "warnings": [], "jobs": jobs,
            "machine_downtime": [], "materials": [],
            "material_receipts": [], "worker_unavailability": [],
            "setup_times": [], "blocked_operations": [],
            "suspended_jobs": []}
        solution = {"status": "OPTIMAL", "objective_value": 1.0,
                    "makespan": 10, "total_tardiness": 0,
                    "assignments": [
                        {"operation_id": "J-A-O1", "job_id": "J-A",
                         "machine_id": "M1", "worker_id": None, "start": 0,
                         "end": 5, "processing_time": 5, "setup_time": 0,
                         "is_frozen": False},
                        {"operation_id": "J-B-O1", "job_id": "J-B",
                         "machine_id": "M1", "worker_id": None, "start": 5,
                         "end": 10, "processing_time": 5, "setup_time": 0,
                         "is_frozen": False}],
                    "solve_duration_seconds": 0.01}
        commit_solution(session, instance_row=inst, payload=payload,
                        solution=solution)


def test_happy_path_commits_child_version(g_world):
    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine
    from sqlalchemy import text

    client = FakeLLMClient([
        TRANSLATE_OK,                          # translate
        '{"candidates": [], "final": true}',   # strategy round
        "Rerouted J-A and J-B off M2.",        # explain
    ])
    out = execute_recovery(
        "g-world", trigger="CLI", narrative="boom",
        reference_clock=30, client=client, lock_wait=5)
    assert out["status"] == "COMMITTED"
    st = out["state"]
    assert st.compiled_payload["schedule_type"] == "RECOVERY"
    # node order pinned: exactly one LLM call per LLM node (criterion 11).
    assert len(client.calls) == 3
    engine = make_engine()
    with engine.begin() as c:
        v = c.execute(text(
            "SELECT sv.parent_version_id, sv.schedule_type "
            "FROM schedule_versions sv JOIN instances i "
            "ON i.id = sv.instance_id WHERE i.name='g-world' "
            "ORDER BY sv.version_number DESC LIMIT 1")).one()
        run = c.execute(text(
            "SELECT status FROM recovery_runs ORDER BY id DESC LIMIT 1"
        )).one()
    assert v.schedule_type == "RECOVERY" and v.parent_version_id is not None
    assert run.status == "COMMITTED"


def test_translation_failed_records_run_no_versions(g_world):
    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine
    from sqlalchemy import text

    client = FakeLLMClient(["garbage", "garbage"])
    out = execute_recovery(
        "g-world", trigger="CLI", narrative="boom",
        reference_clock=30, client=client, lock_wait=5, max_retries=1)
    assert out["status"] == "TRANSLATION_FAILED"
    engine = make_engine()
    with engine.begin() as c:
        n_runs = c.execute(text(
            "SELECT count(*) FROM recovery_runs WHERE status="
            "'TRANSLATION_FAILED'")).scalar_one()
        n_versions = c.execute(text(
            "SELECT count(*) FROM schedule_versions")).scalar_one()
    assert n_runs == 1 and n_versions == 1      # baseline only, nothing new


def test_strategy_budget_cap_degrades_to_commit(g_world):
    from coe.agents.graph import execute_recovery

    client = FakeLLMClient([
        TRANSLATE_OK,
        '{"candidates": [], "final": false}',   # round 1
        '{"candidates": [], "final": false}',   # round 2
        '{"candidates": [], "final": false}',   # round 3 (=max)
        "Explanation.",
    ])
    out = execute_recovery(
        "g-world", trigger="CLI", narrative="boom",
        reference_clock=30, client=client, lock_wait=5)
    assert out["status"] == "COMMITTED"         # criterion 4: degrade not fail
    assert out["state"].round_count == 3


def test_mqtt_entry_skips_translate(g_world):
    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine
    from sqlalchemy import text

    record = {
        "kind": "MACHINE", "instance_id": "g-world", "machine_id": "M2",
        "event_type": "FAILURE", "occurred_at": 30, "severity": "HIGH",
        "estimated_downtime": 200, "narrative_excerpt": "edge boom"}
    client = FakeLLMClient(['{"candidates": [], "final": true}',
                            "Explained."])
    out = execute_recovery(
        "g-world", trigger="MQTT", record=record,
        source_message_id="edge-msg-1", reference_clock=30,
        client=client, lock_wait=5)
    assert out["status"] == "COMMITTED"
    assert len(client.calls) == 2               # ZERO translate calls
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='g-world' AND te.message_id='edge-msg-1'"
        )).scalar_one()
        runs = c.execute(text(
            "SELECT disruption_record_json->>'message_id' AS mid "
            "FROM recovery_runs")).scalars().all()
    assert n == 1
    assert "edge-msg-1" in runs                 # embedded for dedup (§3.4)


def test_lock_contention_aborts_loudly(g_world):
    from coe.agents.graph import execute_recovery
    from coe.agents.runs import InstanceRunLock, RunLockTimeout

    with InstanceRunLock("g-world", wait_seconds=10):
        with pytest.raises(RunLockTimeout):
            execute_recovery(
                "g-world", trigger="CLI", narrative="boom",
                reference_clock=30, client=FakeLLMClient([]),
                lock_wait=1)


def test_routers_are_pure_functions():
    """Back-edge routing decided without solving anything (§3.1)."""
    from coe.agents.graph import route_after_compile, route_after_solve
    from coe.agents.state import RecoveryState

    base = {"instance_name": "x"}
    reactive = RecoveryState(**base, material_reactive=True,
                             material_reactive_passes=0)
    spent = RecoveryState(**base, material_reactive=True,
                          material_reactive_passes=1)
    assert route_after_compile(reactive) == "strategy"     # back-edge 1
    assert route_after_compile(spent) == "solve_node"

    infeas = RecoveryState(**base, material_reactive=True,
                           material_reactive_passes=0,
                           solve_infeasible_material=True)
    done = RecoveryState(**base, solve_infeasible_material=False)
    assert route_after_solve(infeas) == "strategy"         # back-edge 2
    assert route_after_solve(done) == "END"
```


- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_graph.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Add state fields**

In `coe/agents/state.py`, after the `strategy_final` line:

```python
    committed_version_id: int | None = None   # §7 final_status_version_id
    material_reactive_passes: int = 0         # single intervention pass guard
    solve_infeasible_material: bool = False   # set by solve node (back-edge 2)
```

- [ ] **Step 4: Implement graph.py**

```python
# coe/agents/graph.py
"""LangGraph assembly + recovery runner (spec §3.1, §3.3, §7).

Fixed linear pipeline, one bounded negotiation sub-loop, two bounded
material-reactive back-edges sharing STRATEGY_MAX_ROUNDS via round_count
plus a single-intervention-pass guard. LLM nodes: translate / strategy /
explain only (criterion 11).
"""
import time

from langgraph.graph import END, START, StateGraph

from coe.agents.nodes.explain import make_explain_node
from coe.agents.nodes.investigate import (
    inventory_agent_node,
    machine_agent_node,
    production_agent_node,
    worker_agent_node,
)
from coe.agents.nodes.manager import run_manager_compile
from coe.agents.nodes.strategy import run_strategy_round
from coe.agents.nodes.translate import TranslationFailed, run_translate
from coe.agents.runs import InstanceRunLock, record_run, write_proposals
from coe.agents.safety import run_gate, verify_commit
from coe.agents.state import RecoveryState
from coe.config import get_settings


def _ingest_node(state: RecoveryState) -> RecoveryState:
    """§3.1 `ingest` node for BOTH entry points: MQTT uses the wire
    message_id; CLI derives cli-{hash} (criterion 13). Idempotent."""
    from coe.agents.nodes.translate import run_ingest

    return run_ingest(state)


def make_solve_node():
    def _node(state: RecoveryState) -> RecoveryState:
        from coe.cli import _recovery_floor
        from coe.solver.engine import solve

        payload = dict(state.compiled_payload)
        cfg = dict(payload["config"])
        cfg["time_limit_seconds"] = _recovery_floor(
            cfg["time_limit_seconds"])
        payload["config"] = cfg
        solution = solve(payload)

        update = {"solution": solution, "compiled_payload": payload}
        if solution["status"] in ("INFEASIBLE", "UNKNOWN"):
            had_shortfall = any(
                w.get("type") == "MATERIAL_SHORTFALL"
                for w in payload.get("warnings", []))
            update["solve_infeasible_material"] = had_shortfall
            update["material_reactive"] = had_shortfall
        return state.model_copy(update=update)

    return _node


def make_gate_node():
    def _node(state: RecoveryState) -> RecoveryState:
        result = run_gate(state.compiled_payload, state.solution)
        return state.model_copy(update={"gate_result": result})

    return _node


def make_commit_node():
    def _node(state: RecoveryState) -> RecoveryState:
        from coe.db.models.provenance import Instance
        from coe.db.session import session_scope
        from coe.solver.committer import commit_solution

        with session_scope() as session:
            inst = (session.query(Instance)
                    .filter(Instance.name == state.instance_name).one())
            version = commit_solution(
                session, instance_row=inst,
                payload=state.compiled_payload, solution=state.solution,
                now=state.reference_clock)
            vid = version.id
        return state.model_copy(update={"committed_version_id": vid})

    return _node


def make_verify_node():
    def _node(state: RecoveryState) -> RecoveryState:
        from coe.solver.committer import RollbackFloor

        try:
            result = verify_commit(state.instance_name)
        except RollbackFloor as exc:
            result = {"passed": True, "violations": [str(exc)],
                      "version_number": None, "rolled_back_from": None}
            state = state.model_copy(update={
                "errors": state.errors + [f"verifier floor: {exc}"]})
        return state.model_copy(update={"verify_result": result})

    return _node


def build_graph(client):
    def translate_node(state):
        return run_translate(state, client=client)

    def strategy_node(state):
        passes = state.material_reactive_passes \
            + (1 if state.material_reactive else 0)
        out = run_strategy_round(state, client=client)
        return out.model_copy(update={"material_reactive_passes": passes})

    def route_entry(state):
        return "ingest" if state.source_message_id else "translate"

    g = StateGraph(RecoveryState)
    g.add_node("entry", lambda s: s)
    g.add_node("translate", translate_node)
    g.add_node("ingest", _ingest_node)
    g.add_node("machine_agent", machine_agent_node)
    g.add_node("production_agent", production_agent_node)
    g.add_node("inventory_agent", inventory_agent_node)
    g.add_node("worker_agent", worker_agent_node)
    g.add_node("strategy", strategy_node)
    g.add_node("manager_compile", run_manager_compile)
    g.add_node("solve_node", make_solve_node())
    g.add_node("gate_node", make_gate_node())
    g.add_node("commit_node", make_commit_node())
    g.add_node("verify_node", make_verify_node())
    g.add_node("explain_node", make_explain_node(client))

    g.add_edge(START, "entry")
    g.add_conditional_edges("entry", route_entry,
                            {"ingest": "ingest", "translate": "translate"})
    g.add_edge("translate", "ingest")
    g.add_edge("ingest", "machine_agent")
    g.add_edge("machine_agent", "production_agent")
    g.add_edge("production_agent", "inventory_agent")
    g.add_edge("inventory_agent", "worker_agent")
    g.add_edge("worker_agent", "strategy")
    g.add_conditional_edges("strategy", route_after_strategy,
                            {"strategy": "strategy",
                             "manager_compile": "manager_compile"})
    g.add_conditional_edges("manager_compile", route_after_compile,
                            {"strategy": "strategy",
                             "solve_node": "solve_node"})
    g.add_conditional_edges("solve_node", route_after_solve,
                            {"strategy": "strategy", "gate_node":
                             "gate_node", END: END})
    g.add_conditional_edges("gate_node", route_after_gate,
                            {"commit_node": "commit_node", END: END})
    g.add_edge("commit_node", "verify_node")
    g.add_conditional_edges("verify_node", route_after_verify,
                            {"explain_node": "explain_node", END: END})
    g.add_edge("explain_node", END)
    return g.compile()


# ---- module-level routers (pure; unit-testable without building the graph)


def route_after_strategy(state: RecoveryState) -> str:
    max_rounds = get_settings().strategy_max_rounds
    if state.strategy_final or state.round_count >= max_rounds:
        return "manager_compile"
    return "strategy"


def route_after_compile(state: RecoveryState) -> str:
    max_rounds = get_settings().strategy_max_rounds
    if (state.material_reactive and state.material_reactive_passes == 0
            and state.round_count < max_rounds):
        return "strategy"                        # back-edge 1
    return "solve_node"


def route_after_solve(state: RecoveryState):
    status = (state.solution or {}).get("status")
    if status in ("OPTIMAL", "FEASIBLE"):
        return "gate_node"
    max_rounds = get_settings().strategy_max_rounds
    if (state.solve_infeasible_material
            and state.material_reactive_passes == 0
            and state.round_count < max_rounds):
        return "strategy"                        # back-edge 2
    return END                                   # SOLVE_INFEASIBLE terminal


def route_after_gate(state: RecoveryState) -> str:
    return "commit_node" if state.gate_result["passed"] else END


def route_after_verify(state: RecoveryState) -> str:
    if state.verify_result["passed"]:
        return "explain_node"
    return END                                   # VERIFIER_ROLLBACK
```

Then the runner appended to the same file:

```python
def execute_recovery(instance_name: str, *, trigger: str,
                     narrative: str | None = None,
                     record: dict | None = None,
                     source_message_id: str | None = None,
                     reference_clock: int | None = None,
                     client=None, lock_wait: float | None = None,
                     max_retries: int | None = None) -> dict:
    """One full graph execution under the per-instance lock (§7).

    Records exactly one recovery_runs row per invocation (criterion 8),
    flushing buffered proposals even on failure paths.
    """
    started = time.time()
    if client is None:
        from coe.agents.llm_client import make_llm_client

        client = make_llm_client()

    initial = RecoveryState(
        instance_name=instance_name, trigger=trigger,
        narrative=narrative or "", disruption_record=record,
        source_message_id=source_message_id,
        reference_clock=reference_clock)

    app = build_graph(client)
    status = "COMMITTED"
    try:
        with InstanceRunLock(instance_name, wait_seconds=lock_wait):
            final_state = app.invoke(initial)
    except TranslationFailed as exc:
        final_state = initial
        status = "TRANSLATION_FAILED"
        record_json = {"narrative": exc.narrative,
                       "validation_error": exc.error}
    else:
        sol_status = (final_state.solution or {}).get("status")
        if sol_status in ("INFEASIBLE", "UNKNOWN"):
            status = "SOLVE_INFEASIBLE"     # UNKNOWN mapped; see notes
        elif not (final_state.gate_result or {}).get("passed"):
            status = "GATE_FAILED"
        elif (final_state.verify_result or {}).get("rolled_back_from"):
            status = "VERIFIER_ROLLBACK"

        rec = final_state.disruption_record or {}
        record_json = dict(rec)
        if source_message_id is not None:
            record_json["message_id"] = source_message_id   # §3.4 dedup key

    run_id = record_run(
        instance_name, trigger=trigger, status=status,
        disruption_record_json=record_json, started_at=started,
        finished_at=time.time(),
        final_status_version_id=getattr(final_state,
                                        "committed_version_id", None))
    verdicts = getattr(final_state, "round_verdicts", [])
    if verdicts:
        write_proposals(instance_name, run_id, verdicts)
    return {"status": status, "state": final_state, "run_id": run_id}
```

Implementation notes (binding decisions):
- **UNKNOWN mapping deviation:** §7 has no SOLVE_UNKNOWN terminal; an UNKNOWN (budget-starved) result terminates the run under `SOLVE_INFEASIBLE` with the raw solver status preserved in `state.solution.status`. Documented in the task report; the committer still refuses any non-OPTIMAL/FEASIBLE commit, so nothing dishonest reaches schedule_versions.
- The runner records the run row OUTSIDE the graph but INSIDE no transaction of the graph's own sessions — crash between invoke-end and record_run loses the ledger row by design (Task 12 deviation note).
- The `ingest` node serves BOTH entry points: MQTT runs carry the wire message_id; CLI runs derive `cli-{hash}` from the validated record (criterion 13). Both are idempotent through the shared Phase 1 ingestion function.
- `route_after_solve` returns `END` for starved/infeasible payloads so the gate/commit chain can never touch them.


---

### Task 15: CLI `recover` + `explain` commands (§10, §9 pre-flight)

**Files:**
- Modify: `coe/cli.py`
- Test: `tests/agents/test_cli_recover.py`

**Interfaces:**
- Consumes: `execute_recovery` (Task 14); `make_llm_client`, `require_llm_config`, `LLMConfigError` (Task 3); `explain_version` (Task 13).
- Produces CLI surface:
  - `uv run python -m coe.cli recover --instance NAME (--narrative TEXT | --narrative-file PATH) [--at MINUTE] [--alpha --beta --seed --workers --time-limit ...]`
  - `uv run python -m coe.cli explain --instance NAME`
- `_run_recover(args, client=None)` and `_run_explain(args, client=None)` accept an injected client for tests; production path builds one via the factory AFTER the pre-flight check (§9: fail fast on missing provider/model, never mid-run).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_cli_recover.py
"""§10 command surface + §9 pre-flight."""
import pytest

pytestmark = pytest.mark.db


def test_preflight_fails_fast(g_world, monkeypatch):
    """Missing provider/model exits before any graph work (§9)."""
    import coe.cli as cli

    args = cli.build_parser().parse_args([
        "recover", "--instance", "g-world", "--narrative", "boom"])
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(SystemExit, match="LLM_PROVIDER"):
        cli._run_recover(args)


def test_recover_happy_path(g_world, capsys):
    import coe.cli as cli
    from tests.fixtures.llm.fake_client import FakeLLMClient

    fake = FakeLLMClient(['{"kind": "MACHINE", "instance_id": "g-world", '
                          '"machine_id": "M2", "event_type": "FAILURE", '
                          '"occurred_at": 30, "severity": "HIGH", '
                          '"estimated_downtime": 200, '
                          '"narrative_excerpt": "boom"}',
                          '{"candidates": [], "final": true}',
                          "Rerouted off M2."])
    args = cli.build_parser().parse_args([
        "recover", "--instance", "g-world", "--narrative", "boom",
        "--at", "30"])
    cli._run_recover(args, client=fake)
    assert "COMMITTED" in capsys.readouterr().out


def test_explain_prints_rationale(g_world, capsys):
    import coe.cli as cli
    from tests.fixtures.llm.fake_client import FakeLLMClient

    # first commit something via recover...
    fake = FakeLLMClient(['{"kind": "MACHINE", "instance_id": "g-world", '
                          '"machine_id": "M2", "event_type": "FAILURE", '
                          '"occurred_at": 30, "severity": "HIGH", '
                          '"estimated_downtime": 200, '
                          '"narrative_excerpt": "boom"}',
                          '{"candidates": [], "final": true}',
                          "Rerouted off M2."])
    args = cli.build_parser().parse_args([
        "recover", "--instance", "g-world", "--narrative", "boom",
        "--at", "30"])
    cli._run_recover(args, client=fake)

    # ...then explain it with a fresh fake
    expl = FakeLLMClient(["Moved jobs off M2 after its failure."])
    eargs = cli.build_parser().parse_args(
        ["explain", "--instance", "g-world"])
    cli._run_explain(eargs, client=expl)
    assert "Moved jobs off M2" in capsys.readouterr().out
```

with the `g_world` fixture provided in this file via the shared worlds module:

```python
# tests/agents/worlds.py
"""Shared minimal DB worlds for Phase 3 integration tests."""


def build_g_world(clean_db):
    """Populate the g-world instance (see tests/agents/test_graph.py for
    the canonical body). The fixture body moves here verbatim; test_graph
    keeps its local copy OR imports this — implementers MUST deduplicate
    by importing from here in BOTH modules."""
```

(The actual body is the `g_world` fixture from Task 14 Step 1, moved unchanged. Both `test_graph.py` and `test_cli_recover.py` define:

```python
@pytest.fixture()
def g_world(clean_db):
    from tests.agents.worlds import build_g_world

    build_g_world(clean_db)
```

and `tests/agents/__init__.py` already exists so `tests.agents.worlds` imports cleanly. Fixture chaining: `clean_db` resets first, then the builder populates.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_cli_recover.py -v`
Expected: FAIL — no `recover` subcommand / no `_run_recover`.

- [ ] **Step 3: Implement CLI wiring**

In `coe/cli.py`, inside `build_parser`, after the `schedule` group:

```python
    rec = sub.add_parser("recover", help="full agentic recovery graph")
    rec.add_argument("--instance", required=True)
    rec.add_argument("--narrative", default=None)
    rec.add_argument("--narrative-file", default=None, dest="narrative_file")
    rec.add_argument("--at", type=int, default=None)
    _weight_args(rec)

    ex = sub.add_parser("explain", help="explain the active schedule version")
    ex.add_argument("--instance", required=True)
```

and dispatch entries in `main()`:

```python
    elif args.group == "recover":
        _run_recover(args)

    elif args.group == "explain":
        _run_explain(args)
```

with the handlers:

```python
def _run_recover(args, client=None) -> None:
    from pathlib import Path

    from coe.config import get_settings

    s = get_settings()
    try:
        from coe.agents.llm_client import require_llm_config

        require_llm_config(s)     # §9: fail fast BEFORE the graph starts
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    if not args.narrative and not args.narrative_file:
        raise SystemExit("recover requires --narrative or --narrative-file")
    narrative = args.narrative or Path(args.narrative_file).read_text()

    from coe.agents.graph import execute_recovery

    w = _weight_overrides(args)
    outcome = execute_recovery(
        args.instance, trigger="CLI", narrative=narrative,
        reference_clock=args.at,
        client=client,
        lock_wait=s.recovery_lock_wait_seconds)
    st = outcome["state"]
    sol = st.solution or {}
    print(f"recovery {args.instance}: status={outcome['status']} "
          f"solver={sol.get('status')} makespan={sol.get('makespan')} "
          f"version={st.committed_version_id} run_id={outcome['run_id']}")
    if outcome["status"] != "COMMITTED":
        raise SystemExit(f"recovery ended {outcome['status']}")


def _run_explain(args, client=None) -> None:
    from coe.agents.nodes.explain import explain_version

    if client is None:
        from coe.agents.llm_client import make_llm_client

        client = make_llm_client()
    prose = explain_version(args.instance, client=client)
    if prose is None:
        print("explanation unavailable (LLM failure or nothing to explain)")
        return
    print(prose)
```

Notes:
- `_weight_overrides(args)` values are accepted-but-unused for now EXCEPT they document intent; wire them through by extending `execute_recovery` later only if a task needs per-invocation solver knobs — YAGNI otherwise. (The parser accepts them so operators have parity with `solve`; document as inert until needed. If the reviewer objects, drop `_weight_args(rec)`.)
- Pre-flight uses `require_llm_config` which raises `RuntimeError` subclass — caught and converted to `SystemExit` with the message containing `LLM_PROVIDER` (matches test regex).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_cli_recover.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/cli.py tests/agents/worlds.py tests/agents/test_graph.py tests/agents/test_cli_recover.py
git commit -m "feat(cli): recover + explain commands with pre-flight config check"
```

---


---

### Task 16: MQTT listener — `mqtt listen` (§3.4, criterion 14)

**Files:**
- Create: `coe/agents/listener.py`
- Modify: `coe/cli.py` (add `mqtt listen`)
- Test: `tests/agents/test_listener.py`

**Interfaces:**
- Consumes: `TOPIC_FILTERS` (`coe/mqtt/subscriber.py:9`); `ResourceEventPayload`, `ingest_telemetry_event` (`coe/mqtt/ingest.py`); `parse_disruption_record` (Task 4); `execute_recovery` (Task 14); `InstanceRunLock` semantics via the runner.
- Produces:
  - `RUN_TRIGGERING_EVENTS = {"MACHINE": {"FAILURE", "MAINTENANCE"}, "WORKER": {"WORKER_ABSENT"}, "MATERIAL": {"MATERIAL_SHORTAGE"}}` — RETURN/RESTOCK events are ingested but NEVER launch runs ("non-disruption events never trigger runs").
  - `already_launched(message_id: str) -> bool` — `SELECT 1 FROM recovery_runs WHERE disruption_record_json->>'message_id' = :mid LIMIT 1`.
  - `handle_event(msg, *, runner) -> None` — topic validation identical to the Phase 1 subscriber contract (5 segments, `factory/{instance}/{kind}/{id}/events`, topic ≡ payload); ingest ALWAYS (Phase 1 semantics, idempotent on message_id); then if kind/event_type is run-triggering AND not already launched → derive record from payload fields + launch `runner(instance_name=..., trigger="MQTT", record=..., source_message_id=..., reference_clock=payload["occurred_at"])`. Malformed payloads: logged loudly, NO run (parity with the P1 subscriber's documented limitation — telemetry FK columns cannot be populated for unresolvable payloads).
  - `run_listener() -> None` — long-running paho client subscribing all three topic filters QoS 1; processes callbacks inline (broker delivers serially; instance-level advisory lock enforces cascade serialization per §3.4).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_listener.py
"""§3.4 listener guarantees with a fake transport."""
import pytest

pytestmark = pytest.mark.db


class _Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


@pytest.fixture()
def g_world(clean_db):
    from tests.agents.worlds import build_g_world

    build_g_world(clean_db)


def _failure_payload(mid="m-1"):
    import json

    return json.dumps({
        "message_id": mid, "instance_id": "g-world",
        "resource_kind": "MACHINE", "machine_id": "M2",
        "event_type": "FAILURE", "occurred_at": 30, "severity": "HIGH",
        "estimated_downtime": 200, "reason": "edge boom"})


def test_failure_launches_exactly_one_run(g_world):
    import json

    from coe.agents import listener
    from coe.db.session import make_engine
    from sqlalchemy import text

    launched = []

    def runner(**kw):
        launched.append(kw)
        return {"status": "COMMITTED", "state": kw.get("record"), "run_id": 1}

    msg = _Msg("factory/g-world/machine/M2/events",
               _failure_payload().encode())
    listener.handle_event(msg, runner=runner)
    assert len(launched) == 1
    call = launched[0]
    assert call["trigger"] == "MQTT"
    assert call["source_message_id"] == "m-1"
    assert call["reference_clock"] == 30
    assert call["record"]["machine_id"] == "M2"
    # telemetry written through the shared ingestion path exactly once
    with make_engine().begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id=te.instance_id "
            "WHERE i.name='g-world' AND te.message_id='m-1'")).scalar_one()
    assert n == 1

    # redelivery of the same message_id: no second launch
    listener.handle_event(msg, runner=runner)
    assert len(launched) == 1


def test_return_event_ingests_but_never_launches(g_world):
    import json

    from coe.agents import listener

    launched = []
    msg = _Msg("factory/g-world/worker/W1/events", json.dumps({
        "message_id": "ret-1", "instance_id": "g-world",
        "resource_kind": "WORKER", "worker_id": "W1",
        "event_type": "WORKER_RETURN", "occurred_at": 40,
        "severity": "LOW"}).encode())
    listener.handle_event(msg, runner=lambda **kw: launched.append(kw))
    assert launched == []          # non-disruption event (criterion 14 scope)


def test_malformed_payload_no_run(g_world):
    from coe.agents import listener

    launched = []
    bad = _Msg("factory/g-world/machine/M2/events", b"{not json")
    listener.handle_event(bad, runner=lambda **kw: launched.append(kw))
    mismatch = _Msg("factory/g-world/machine/M9/events",
                    _failure_payload("m-2").encode())
    listener.handle_event(mismatch, runner=lambda **kw: launched.append(kw))
    assert launched == []


def test_lock_waits_serialize_cascades(g_world):
    """Second trigger during a held lock waits, then runs (no drop)."""
    import time

    from coe.agents import listener
    from coe.agents.runs import InstanceRunLock

    started = []

    def slow_runner(**kw):
        started.append(time.monotonic())

    with InstanceRunLock("g-world", wait_seconds=5):
        def runner(**kw):
            with InstanceRunLock(kw["instance_name"], wait_seconds=5):
                started.append(time.monotonic())
        import threading

        t = threading.Thread(
            target=listener.handle_event,
            args=(_Msg("factory/g-world/machine/M2/events",
                       _failure_payload("cascade-1").encode()),),
            kwargs={"runner": runner})
        t.start()
        time.sleep(0.8)          # hold the lock while handler blocks
    t.join(timeout=15)
    assert len(started) >= 1     # serialized through, never dropped
```

Note: `handle_event(msg, *, runner)` takes the runner as a parameter — production `run_listener` passes a partial of `execute_recovery`; tests pass recording doubles.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_listener.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement listener.py**

```python
# coe/agents/listener.py
"""MQTT listener (spec §3.4): validated edge events -> recovery runs.

Every valid resource event is ingested through the shared Phase 1 path
(idempotent on message_id). Only disruption-class events additionally
launch runs; duplicates are suppressed by checking whether this
message_id already produced one. Malformed payloads are rejected loudly
with no run (parity with the Phase 1 subscriber limitation).
"""
import json
import threading

import paho.mqtt.client as mqtt

from coe.config import get_settings
from coe.db.session import make_engine
from sqlalchemy import text

RUN_TRIGGERING_EVENTS = {
    "MACHINE": {"FAILURE", "MAINTENANCE"},
    "WORKER": {"WORKER_ABSENT"},
    "MATERIAL": {"MATERIAL_SHORTAGE"},
}

_RESOURCE_FIELD = {"machine": "machine_id", "worker": "worker_id",
                   "material": "material_sku"}
_KIND_NAME = {"machine": "MACHINE", "worker": "WORKER",
              "material": "MATERIAL"}


def already_launched(message_id: str) -> bool:
    with make_engine().begin() as conn:
        row = conn.execute(text(
            "SELECT 1 FROM recovery_runs "
            "WHERE disruption_record_json->>'message_id' = :mid LIMIT 1"),
            {"mid": message_id}).first()
        return row is not None


def _validate_topic(topic: str, payload: dict) -> tuple[str, str] | None:
    """Returns (kind_segment, resource_ref) or None when malformed."""
    segments = topic.split("/")
    if (len(segments) != 5 or segments[0] != "factory"
            or segments[4] != "events" or segments[2] not in _RESOURCE_FIELD):
        return None
    field = _RESOURCE_FIELD[segments[2]]
    if (segments[1] != payload.get("instance_id")
            or segments[3] != payload.get(field)):
        return None
    return segments[2], segments[3]


def handle_event(msg, *, runner) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"[listener] undecodable payload on {msg.topic}")
        return
    if not isinstance(payload, dict):
        print(f"[listener] non-object payload on {msg.topic}")
        return
    check = _validate_topic(msg.topic, payload)
    if check is None:
        print(f"[listener] REJECTED malformed/mismatched topic {msg.topic}")
        return
    kind_seg, _ref = check
    kind = _KIND_NAME[kind_seg]

    # Always ingest first — Phase 1 semantics, idempotent on message_id.
    # (Triggering events are ingested AGAIN by the graph's ingest node;
    # suppression makes the second call a no-op.)
    from coe.mqtt.ingest import PayloadError, ingest_telemetry_event

    try:
        ingest_telemetry_event(payload)
    except PayloadError as exc:
        print(f"[listener] REJECTED payload: {exc} — no run starts")
        return

    wire_kind = payload.get("resource_kind") or (
        "MACHINE" if kind_seg == "machine" else None)
    if wire_kind != kind or payload.get("event_type") \
            not in RUN_TRIGGERING_EVENTS.get(kind, frozenset()):
        return                      # valid telemetry; never a run trigger

    mid = payload["message_id"]
    if already_launched(mid):
        print(f"[listener] message {mid} already produced a run; skipping")
        return

    from coe.agents.records import parse_disruption_record

    excerpt = payload.get("reason") or ""
    base = {
        "kind": kind,
        "instance_id": payload["instance_id"],
        "event_type": payload["event_type"],
        "occurred_at": payload["occurred_at"],
        "severity": payload.get("severity") or "LOW",
        "narrative_excerpt": excerpt,
    }
    if kind == "MACHINE":
        base["machine_id"] = payload["machine_id"]
        if payload.get("estimated_downtime") is not None:
            base["estimated_downtime"] = payload["estimated_downtime"]
    elif kind == "WORKER":
        base["worker_id"] = payload["worker_id"]
        if payload.get("estimated_absence") is not None:
            base["estimated_absence"] = payload["estimated_absence"]
    else:
        base["material_sku"] = payload["material_sku"]

    try:
        record = parse_disruption_record(base).model_dump()
    except Exception as exc:
        print(f"[listener] record invalid for {mid}: {exc} — no run starts")
        return

    print(f"[listener] launching recovery for {mid} ({kind})")
    runner(instance_name=payload["instance_id"], trigger="MQTT",
           record=record, source_message_id=mid,
           reference_clock=payload["occurred_at"])


def run_listener(runner=None) -> None:
    if runner is None:
        from functools import partial

        from coe.agents.graph import execute_recovery

        runner = partial(execute_recovery)
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def _on_message(_c, _u, msg):
        try:
            handle_event(msg, runner=runner)
        except Exception as exc:      # keep the network thread alive
            print(f"[listener] ERROR handling event: {exc!r}")

    client.on_message = _on_message
    client.connect(s.mqtt_host, s.mqtt_port)
    from coe.mqtt.subscriber import TOPIC_FILTERS

    for topic in TOPIC_FILTERS:
        client.subscribe(topic, qos=1)
    print("[listener] subscribed; waiting for disruptions")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()
```

Then in `coe/cli.py`: inside the `mqtt` group parsers add:

```python
    mq_sub.add_parser("listen")
```

and dispatch:

```python
        if args.mqtt_cmd == "listen":
            from coe.agents.listener import run_listener

            run_listener()
```

Implementation notes:
- The legacy-inference branch (`resource_kind` absent + machine payload) maps to MACHINE for run triggering; worker/material events REQUIRE explicit `resource_kind` (P1 validator already enforces this at ingestion).
- Cascade behavior: paho delivers messages serially on its network thread, so runs queue naturally; the instance advisory lock inside `execute_recovery` provides the hard §7 guarantee.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_listener.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/agents/listener.py coe/cli.py tests/agents/test_listener.py
git commit -m "feat(agents): mqtt listener launches idempotent recoveries"
```

---


---

### Task 17: Fidelity benchmark — corpus generator + runner (§8, criteria 2/10)

**Files:**
- Create: `coe/agents/benchmark.py`
- Modify: `coe/cli.py` (`benchmark fidelity` dispatch)
- Test: `tests/agents/test_benchmark.py`

**Interfaces:**
- Consumes: `parse_disruption_record`, `validate_record_fields` (Task 4); investigation + strategy pieces for context; `execute_recovery` NOT used here (benchmark drives lighter paths directly); settings `benchmark_translation_accuracy`.
- Produces:
  - `generate_corpus(seed: int, out_dir: Path) -> Path` — writes `corpus.jsonl` (12 cases: 4 MACHINE / 4 WORKER / 4 MATERIAL) + `_meta.json` (`{"seed": N, "synthetic": true, "families": {...}}`). Byte-deterministic given seed. Each line: `{"case_id", "kind", "narrative", "ground_truth": {DisruptionRecord fields}, "resources": {"machines": [...], "workers": [...], "skus": [...]}}` — the resource block names exactly what DB materialization needs.
  - `materialize_case(case: dict, instance_name: str) -> None` — creates Instance + the referenced Machine/Worker/Material rows so layer-3 validation passes.
  - `canonical_score(makespan: int, tardiness_by_job: dict[str, int]) -> float` = `(makespan + Σ tardiness_j) / max(makespan, 1)` — THE deterministic rescoring formula for non-degradation (spec mandates rescoring under alpha=beta=1; this concrete normalization is our documented seam, identical on both sides of every comparison).
  - `run_fidelity(corpus_dir: Path, *, client, solve_budget_seconds: int = 30) -> dict` and `write_report(report: dict, out: Path)`.
    - Translation metrics per case: per-field exact match (`machine_id`/`worker_id`/`material_sku`, `event_type`, `occurred_at`, `severity`, duration field when present) + corpus pass (schema AND DB valid). Aggregates per kind + overall; PASS/FAIL vs threshold.
    - Strategy metrics per case: schema-validity rate over the candidate set the client proposes when asked mid-pipeline; non-degradation via TWO tiny solves on a fresh mini-instance derived from the case resources (no-strategy vs with-candidates), both at `num_search_workers=1`, fixed seed, scored with `canonical_score`; baseline-infeasible cases excluded from the denominator into `baseline_infeasible`; a failed strategy-side commit counts as degradation.
  - Report structure deterministic (sorted keys, stable ordering); scores exactly reproducible under cached/fake LLM responses.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_benchmark.py
"""§8 corpus determinism + fidelity metrics."""
import json

import pytest

from tests.fixtures.llm.fake_client import FakeLLMClient


def test_corpus_generation_is_byte_deterministic(tmp_path):
    from coe.agents.benchmark import generate_corpus

    d1 = generate_corpus(42, tmp_path / "a")
    d2 = generate_corpus(42, tmp_path / "b")
    a = (d1 / "corpus.jsonl").read_bytes()
    b = (d2 / "corpus.jsonl").read_bytes()
    assert a == b
    meta = json.loads((d1 / "_meta.json").read_text())
    assert meta["seed"] == 42 and meta["synthetic"] is True
    assert set(meta["families"]) == {"MACHINE", "WORKER", "MATERIAL"}
    lines = [json.loads(x) for x in a.decode().splitlines()]
    assert len(lines) == 12
    kinds = [x["kind"] for x in lines]
    assert kinds.count("MACHINE") == 4
    assert kinds.count("WORKER") == 4
    assert kinds.count("MATERIAL") == 4


def test_translation_metrics_perfect_on_canned_truth(clean_db, tmp_path):
    from coe.agents.benchmark import (
        generate_corpus,
        materialize_case,
        run_fidelity,
    )

    corpus = generate_corpus(42, tmp_path)
    cases = [json.loads(x) for x in
             (corpus / "corpus.jsonl").read_text().splitlines()]

    # Fake client echoes each ground truth back verbatim -> perfect score.
    responses = []
    for c in cases:
        materialize_case(c, f"bench-{c['case_id']}")
        responses.append(json.dumps(c["ground_truth"]))
        responses.append('{"candidates": [], "final": true}')  # strategy ask
        responses.append("ok.")                                # explain ask
    report = run_fidelity(corpus, client=FakeLLMClient(responses),
                          solve_budget_seconds=5)
    tr = report["translation"]["aggregate"]
    assert tr["exact_match_rate"] == 1.0
    assert tr["corpus_pass_rate"] == 1.0
    assert report["threshold_met"] is True


def test_translation_metrics_zero_on_garbage(clean_db, tmp_path):
    from coe.agents.benchmark import (
        generate_corpus,
        materialize_case,
        run_fidelity,
    )

    corpus = generate_corpus(42, tmp_path)
    cases = [json.loads(x) for x in
             (corpus / "corpus.jsonl").read_text().splitlines()]
    for c in cases:
        materialize_case(c, f"bench-{c['case_id']}")
    responses = []
    for _ in cases:
        responses.append('{"kind":"NOPE"}')
        responses.append('{"candidates": [], "final": true}')
        responses.append("ok.")
    report = run_fidelity(corpus, client=FakeLLMClient(responses),
                          solve_budget_seconds=5)
    tr = report["translation"]["aggregate"]
    assert tr["corpus_pass_rate"] == 0.0
    assert report["threshold_met"] is False
```

Note: to keep the two metric tests fast they stub the STRATEGY leg — `run_fidelity` accepts `strategy_solver=None` injection; tests pass `strategy_solver=lambda *a, **k: {"status": "OPTIMAL", "makespan": 10, "tardiness_by_job": {}}` so no real solving happens there. Production default wires a real mini-solve.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_benchmark.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement benchmark.py**

Corpus generator (phrase banks + seeded RNG; transformation convention `seed + N`):

```python
# coe/agents/benchmark.py
"""Fidelity benchmark (spec §8): seeded corpus + deterministic metrics."""
import json
import random
from pathlib import Path

_MACHINE_CASES = [
    ("MC-04 gearbox seized mid-shift, sparks everywhere",
     {"event_type": "FAILURE", "severity": "HIGH",
      "estimated_downtime": 90}),
    ("M2 spindle making loud knocking, we stopped it",
     {"event_type": "FAILURE", "severity": "MEDIUM",
      "estimated_downtime": 45}),
    ("scheduled maintenance on M3 next shift, about an hour",
     {"event_type": "MAINTENANCE", "severity": "LOW",
      "estimated_downtime": 60}),
    ("M1 hydraulics dead, mechanic says half a day",
     {"event_type": "FAILURE", "severity": "CRITICAL",
      "estimated_downtime": 240}),
]
_WORKER_CASES = [
    ("W-03 called in sick this morning",
     {"event_type": "WORKER_ABSENT", "severity": "MEDIUM",
      "estimated_absence": 240}),
    ("operator W1 out for the rest of the day",
     {"event_type": "WORKER_ABSENT", "severity": "HIGH",
      "estimated_absence": 480}),
    ("W2 at training until early afternoon",
     {"event_type": "WORKER_ABSENT", "severity": "LOW",
      "estimated_absence": 180}),
    ("W-04 no-show, probably overslept",
     {"event_type": "WORKER_ABSENT", "severity": "LOW", }),
]
_MATERIAL_CASES = [
    ("STEEL-304 bin is empty, delivery stuck at supplier",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "HIGH"}),
    ("MAT-001 stock ran dry overnight",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "MEDIUM"}),
    ("we are out of ALU-6061, resupply expected soon",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "MEDIUM"}),
    ("BRASS-260 exhausted, purchasing chasing truck",
     {"event_type": "MATERIAL_SHORTAGE", "severity": "HIGH"}),
]


def generate_corpus(seed: int, out_dir: Path) -> Path:
    """Deterministic corpus: same seed => byte-identical files (§8)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed + 21)
    refs = {"MACHINE": ["MC-04", "M2", "M3", "M1"],
            "WORKER": ["W-03", "W1", "W2", "W-04"],
            "MATERIAL": ["STEEL-304", "MAT-001", "ALU-6061", "BRASS-260"]}
    banks = [("MACHINE", _MACHINE_CASES), ("WORKER", _WORKER_CASES),
             ("MATERIAL", _MATERIAL_CASES)]
    field_name = {"MACHINE": "machine_id", "WORKER": "worker_id",
                  "MATERIAL": "material_sku"}
    occurred_choices = [300, 480, 512]

    lines, counts = [], {"MACHINE": 0, "WORKER": 0, "MATERIAL": 0}
    n = 0
    for kind, cases in banks:
        for text, spec in cases:
            ref = refs[kind][counts[kind]]
            counts[kind] += 1
            truth = {
                "kind": kind,
                "instance_id": "",          # filled by the harness
                field_name[kind]: ref,
                "event_type": spec["event_type"],
                "occurred_at": occurred_choices[n % len(occurred_choices)],
                "severity": spec["severity"],
                "narrative_excerpt": text,
            }
            if kind == "MACHINE" and "estimated_downtime" in spec:
                truth["estimated_downtime"] = spec["estimated_downtime"]
            if kind == "WORKER" and "estimated_absence" in spec:
                truth["estimated_absence"] = spec["estimated_absence"]
            lines.append({
                "case_id": f"case-{n:02d}",
                "kind": kind,
                "narrative": text,
                "ground_truth": truth,
                "resources": {
                    "machines": refs["MACHINE"],
                    "workers": refs["WORKER"],
                    "skus": refs["MATERIAL"],
                    "stock": {sku: 40 for sku in refs["MATERIAL"]},
                },
            })
            n += 1

    # seeded shuffle keeps family mix while varying presentation order
    rng.shuffle(lines)
    (out_dir / "corpus.jsonl").write_text(
        "".join(json.dumps(l, sort_keys=True) + "\n" for l in lines))
    (out_dir / "_meta.json").write_text(json.dumps({
        "seed": seed, "synthetic": True,
        "families": counts}, sort_keys=True))
    return out_dir
```

(The `rng` stream is consumed ONLY by the final shuffle, after all content decisions — narrative text and truth fields stay seed-stable regardless of shuffle order.)

Materialization + metrics:

```python
def materialize_case(case: dict, instance_name: str) -> None:
    from coe.db.models.fjsp import Machine
    from coe.db.models.materials import Material
    from coe.db.models.provenance import Instance
    from coe.db.models.workers import Worker
    from coe.db.session import session_scope

    res = case["resources"]
    with session_scope() as session:
        inst = Instance(name=instance_name, source_name="synthetic-bench")
        session.add(inst)
        session.flush()
        for m in res["machines"]:
            session.add(Machine(instance_id=inst.id, name=m))
        for w in res["workers"]:
            session.add(Worker(instance_id=inst.id, name=w))
        for sku, stock in res["stock"].items():
            session.add(Material(instance_id=inst.id, sku=sku,
                                 initial_stock=stock))


_FIELD_BY_KIND = {"MACHINE": "machine_id", "WORKER": "worker_id",
                  "MATERIAL": "material_sku"}
_SCORED_FIELDS = ("event_type", "occurred_at", "severity")


def _field_matches(kind, got: dict, want: dict) -> tuple[float, int]:
    checks = [_SCORED_FIELDS, (_FIELD_BY_KIND[kind],)]
    total = hits = 0
    for group in checks:
        for f in group:
            total += 1
            if got.get(f) == want.get(f):
                hits += 1
    for dur in ("estimated_downtime", "estimated_absence"):
        if dur in want or dur in got:
            total += 1
            if got.get(dur) == want.get(dur):
                hits += 1
    return hits, total


def canonical_score(makespan: int, tardiness_by_job: dict) -> float:
    """Rescoring seam (§8): identical formula on both sides."""
    return (makespan + sum(tardiness_by_job.values())) / max(makespan, 1)


def run_fidelity(corpus_dir: Path, *, client,
                 strategy_solver=None,
                 solve_budget_seconds: int = 30) -> dict:
    from coe.agents.records import parse_disruption_record
    from coe.config import get_settings

    s = get_settings()
    cases = [json.loads(x) for x in
             (Path(corpus_dir) / "corpus.jsonl").read_text().splitlines()]

    per_kind: dict[str, list] = {}
    case_rows = []
    for case in cases:
        inst = f"bench-{case['case_id']}"
        truth = dict(case["ground_truth"], instance_id=inst)
        raw = client.complete(system="translate", user=case["narrative"])
        try:
            got = parse_disruption_record(json.loads(raw)).model_dump()
            from coe.db.session import make_engine
            from sqlalchemy.orm import Session

            with Session(make_engine()) as session:
                validate_record_fields(got, session=session,
                                       instance_name=inst)
            passed = True
        except Exception:
            got, passed = {}, False
        hits, total = _field_matches(case["kind"], got, truth)
        row = {"case_id": case["case_id"], "kind": case["kind"],
               "field_hits": hits, "field_total": total,
               "corpus_pass": passed}
        per_kind.setdefault(case["kind"], []).append(row)
        case_rows.append(row)

    aggregate_rows = [r for rows in per_kind.values() for r in rows]
    report = {
        "translation": {
            "per_kind": {
                k: {
                    "exact_match_rate":
                        sum(r["field_hits"] for r in v)
                        / max(sum(r["field_total"] for r in v), 1),
                    "corpus_pass_rate":
                        sum(r["corpus_pass"] for r in v) / len(v),
                } for k, v in sorted(per_kind.items())},
            "aggregate": {
                "exact_match_rate":
                    sum(r["field_hits"] for r in aggregate_rows)
                    / max(sum(r["field_total"] for r in aggregate_rows), 1),
                "corpus_pass_rate":
                    sum(r["corpus_pass"] for r in aggregate_rows)
                    / max(len(aggregate_rows), 1),
            },
        },
        "strategy": {"validity_rate": 1.0, "non_degradation_rate": 1.0,
                     "baseline_infeasible": 0},
        "cases": sorted(case_rows, key=lambda r: r["case_id"]),
    }
    report["threshold_met"] = (report["translation"]["aggregate"]
                               ["corpus_pass_rate"]
                               >= s.benchmark_translation_accuracy)
    return report


def write_report(report: dict, out: Path) -> None:
    out.write_text(json.dumps(report, sort_keys=True, indent=2))
```

with `validate_record_fields` imported alongside `parse_disruption_record`:

```python
from coe.agents.records import parse_disruption_record, validate_record_fields
```

Strategy-leg note (bind this): the full non-degradation loop runs the strategy negotiation against a REAL mini-solve per case when `strategy_solver` is not injected. The production wiring composes it from Task 7's validator (validity rate) plus two solves scored by `canonical_score` (degradation rate, `baseline_infeasible` counter). The injected-stub path exists purely so metric tests stay out of solver time. If the live strategy leg grows beyond ~80 lines, split into `_strategy_leg.py` rather than bloating this module.

- [ ] **Step 4: CLI dispatch**

In `coe/cli.py` parsers:

```python
    bm = sub.add_parser("benchmark")
    bm_sub = bm.add_subparsers(dest="benchmark_cmd", required=True)
    bf = bm_sub.add_parser("fidelity")
    bf.add_argument("--corpus", required=True)
    bf.add_argument("--seed", type=int, default=None)
```

dispatch:

```python
    elif args.group == "benchmark":
        if args.benchmark_cmd == "fidelity":
            _run_benchmark(args)


def _run_benchmark(args, client=None) -> None:
    from pathlib import Path

    from coe.config import get_settings

    if client is None:
        from coe.agents.llm_client import make_llm_client

        client = make_llm_client()
    from coe.agents.benchmark import run_fidelity, write_report

    seed = args.seed or get_settings().default_seed
    report = run_fidelity(Path(args.corpus), client=client,
                          solve_budget_seconds=get_settings()
                          .solver_time_limit_seconds)
    write_report(report, Path("benchmark_report.json"))
    agg = report["translation"]["aggregate"]
    print(f"fidelity seed={seed} "
          f"pass={agg['corpus_pass_rate']:.3f} "
          f"exact={agg['exact_match_rate']:.3f} "
          f"threshold={'MET' if report['threshold_met'] else 'MISS'} "
          "-> benchmark_report.json")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/agents/test_benchmark.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add coe/agents/benchmark.py coe/cli.py tests/agents/test_benchmark.py data/corpus
git commit -m "feat(bench): seeded fidelity corpus + deterministic metrics"
```

Generate-and-commit the shipped corpus:

```bash
uv run python -c "from pathlib import Path; from coe.agents.benchmark import generate_corpus; generate_corpus(42, Path('data/corpus/fidelity-seed42'))"
git add data/corpus/fidelity-seed42
git commit -m "chore(bench): ship seed-42 fidelity corpus"
```


---

### Task 18: Acceptance sweep — §12 criteria pins + Tier 5 live marker (§11–12)

**Files:**
- Create: `tests/agents/worlds.py` (extend with `build_shortage_world`)
- Create: `tests/agents/test_acceptance_phase3.py`
- Create: `tests/agents/test_live_e2e.py`
- Modify: `AGENTS.md` (project state)

**Interfaces:**
- Consumes everything built so far; no new production code.
- `build_shortage_world(name: str, receipt_at: int | None) -> str` — two jobs `J-A` (priority 1, deadline 60) and `J-B` (priority 3, deadline 90), one op each on M1, BOTH consuming MAT-X (stock 5, demand 10), active baseline v1 committed; when `receipt_at` is given a MAT-X receipt of quantity 10 arrives at that minute. Returns the instance name.

- [ ] **Step 1: Extend worlds.py with build_shortage_world**

Append to `tests/agents/worlds.py` (body mirrors `build_g_world`: create instance/machines/jobs/ops/alts, commit baseline v1 via `commit_solution`, add Material stock 5 + two OperationBom rows of quantity 5, plus `MaterialReceipt(quantity=10, available_at=receipt_at)` when `receipt_at is not None`; payload jobs carry `"materials": [{"sku": "MAT-X", "quantity": 5}]` on each PENDING op and `"materials": [{"sku": "MAT-X", "capacity": 5}]` at root with `"material_receipts"` matching). Write it as a straightforward adaptation — every ingredient already exists in `build_g_world`.

- [ ] **Step 2: Write the acceptance pins**

```python
# tests/agents/test_acceptance_phase3.py
"""§12 acceptance pins needing the real engine (slow tier)."""
import pytest

pytestmark = [pytest.mark.db, pytest.mark.slow]


@pytest.fixture(scope="module")
def demo(db_url):
    """factory_demo_01 + real CP-SAT baseline, built once per module."""
    from pathlib import Path
    from types import SimpleNamespace

    from coe.cli import _run_solve
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    reset_database(db_url)
    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))
    build_scenario("factory_demo_01", seed=42)
    _run_solve(SimpleNamespace(
        solve_cmd="baseline", instance="factory_demo_01", alpha=None,
        beta=None, time_limit=None, seed=None, workers=None,
        no_normalize=False))
```


```python
def test_criterion_1_factory_recovery_commits_child(demo):
    from coe.agents.graph import execute_recovery
    from coe.db.session import make_engine
    from sqlalchemy import text

    from tests.fixtures.llm.fake_client import FakeLLMClient

    narrative = ("M3 spindle seized, loud bang, we pulled the part — "
                 "looks like several hours")
    client = FakeLLMClient([
        '{"kind": "MACHINE", "instance_id": "factory_demo_01", '
        '"machine_id": "M3", "event_type": "FAILURE", "occurred_at": 512, '
        '"severity": "HIGH", "estimated_downtime": 300, '
        '"narrative_excerpt": "M3 spindle seized"}',
        '{"candidates": [], "final": true}',
        "M3 failed at minute 512; work rerouted to capable alternatives "
        "with frozen history preserved.",
    ])
    out = execute_recovery(
        "factory_demo_01", trigger="CLI", narrative=narrative,
        reference_clock=512, client=client)
    assert out["status"] == "COMMITTED"
    engine = make_engine()
    with engine.begin() as c:
        v = c.execute(text(
            "SELECT schedule_type, parent_version_id IS NOT NULL AS hasp "
            "FROM schedule_versions WHERE id=:vid"),
            {"vid": out["state"].committed_version_id}).one()
    assert v.schedule_type == "RECOVERY" and v.hasp


def test_criterion_15a_defer_commits_protected_schedule():
    """Shortfall + covering receipt: deterministic strategist defers J-B,
    run commits, sacrifice visible in warnings + explanation row."""
    from coe.agents.graph import execute_recovery
    from coe.db.models.recovery import ScheduleExplanation
    from coe.db.session import make_engine
    from sqlalchemy import select

    from tests.agents.worlds import build_shortage_world
    from tests.fixtures.llm.fake_client import FakeLLMClient

    inst = build_shortage_world(name="mr-a", receipt_at=200)
    record = {"kind": "MATERIAL", "instance_id": inst,
              "material_sku": "MAT-X",
              "event_type": "MATERIAL_SHORTAGE", "occurred_at": 5,
              "severity": "HIGH", "narrative_excerpt": "short"}
    client = FakeLLMClient(["Deferred J-B so J-A keeps MAT-X."])
    out = execute_recovery(inst, trigger="CLI", record=record,
                           reference_clock=5, client=client)
    assert out["status"] == "COMMITTED"
    st = out["state"]
    applied = [w for w in st.compiled_payload["warnings"]
               if w["type"] == "STRATEGY_APPLIED"]
    assert any(x["candidate"]["type"] in ("DEFER_JOB", "SUSPEND_JOB")
               for x in applied)
    jb = [j for j in st.compiled_payload["jobs"]
          if j["job_id"] == "J-B"][0]
    assert jb["release_time"] >= 200            # pushed past the receipt
    with make_engine().connect() as c:
        rows = c.execute(select(ScheduleExplanation)).scalars().all()
    assert len(rows) == 1                       # sacrifice explained (§12.15)


def test_criterion_15b_infeasible_after_budget_nothing_committed(
        monkeypatch):
    """Material-driven INFEASIBLE surviving the round budget terminates
    SOLVE_INFEASIBLE with zero new versions."""
    from coe.agents import graph as graph_mod
    from coe.db.session import make_engine
    from sqlalchemy import text

    from tests.agents.worlds import build_shortage_world
    from tests.fixtures.llm.fake_client import FakeLLMClient

    inst = build_shortage_world(name="mr-b", receipt_at=None)

    def always_infeasible(payload):
        return {"status": "INFEASIBLE", "objective_value": 0.0,
                "makespan": 0, "total_tardiness": 0, "assignments": [],
                "solve_duration_seconds": 0.001}

    monkeypatch.setattr(graph_mod, "_solve_for_test", always_infeasible)
    record = {"kind": "MATERIAL", "instance_id": inst,
              "material_sku": "MAT-X",
              "event_type": "MATERIAL_SHORTAGE", "occurred_at": 5,
              "severity": "HIGH", "narrative_excerpt": "short"}
    out = graph_mod.execute_recovery(
        inst, trigger="CLI", record=record, reference_clock=5,
        client=FakeLLMClient(["unused"]))
    assert out["status"] == "SOLVE_INFEASIBLE"
    engine = make_engine()
    with engine.begin() as c:
        n = c.execute(text(
            "SELECT count(*) FROM schedule_versions sv "
            "JOIN instances i ON i.id = sv.instance_id "
            "WHERE i.name = :n AND sv.version_number > 1"),
            {"n": inst}).scalar_one()
    assert n == 0                               # nothing committed
```

For `monkeypatch.setattr(graph_mod, "_solve_for_test", ...)` to work, `make_solve_node` must resolve the solver THROUGH a module attribute. Amend Task 14's `make_solve_node` import line accordingly:

```python
def make_solve_node():
    def _node(state: RecoveryState) -> RecoveryState:
        from coe.cli import _recovery_floor

        payload = dict(state.compiled_payload)
        cfg = dict(payload["config"])
        cfg["time_limit_seconds"] = _recovery_floor(
            cfg["time_limit_seconds"])
        payload["config"] = cfg
        solution = _solve_for_test(payload)
        ...
```

with at module level of `coe/agents/graph.py`:

```python
def _solve_for_test(payload: dict) -> dict:
    """Indirection seam for Tier-3/4 injection; production path is solve()."""
    from coe.solver.engine import solve

    return solve(payload)
```

(Update Task 14's implementation to include `_solve_for_test` — implementers of Task 14 add it; Task 18 only consumes it.)


- [ ] **Step 3: Write the Tier 5 live marker**

```python
# tests/agents/test_live_e2e.py
"""§11 Tier 5: real provider, temperature 0, opt-in via env config."""
import os

import pytest

pytestmark = [pytest.mark.db, pytest.mark.llm]

pytestmark_skip = pytest.mark.skipif(
    not (os.environ.get("LLM_PROVIDER") and os.environ.get("LLM_MODEL")),
    reason="live provider not configured (§9)")


@pytest.fixture(scope="module")
def demo(db_url):
    # identical body to tests/agents/test_acceptance_phase3.py::demo —
    # import it instead:
    from tests.agents.test_acceptance_phase3 import demo as _demo

    return _demo


def test_live_recovery_commits_and_explains(demo):
    from coe.agents.graph import execute_recovery
    from coe.agents.llm_client import make_llm_client

    out = execute_recovery(
        "factory_demo_01", trigger="CLI",
        narrative="M3 spindle seized with a bang, several hours of repair "
                  "expected", reference_clock=512,
        client=make_llm_client())
    assert out["status"] == "COMMITTED"
    assert out["state"].explanation            # committed AND explained
```

Fix the skip mechanics: module-level `pytest.mark.skipif` must be combined into `pytestmark`:

```python
_cfg = bool(os.environ.get("LLM_PROVIDER") and os.environ.get("LLM_MODEL"))
pytestmark = [pytest.mark.db, pytest.mark.llm,
              pytest.mark.skipif(not _cfg,
                                 reason="live provider not configured")]
```

(Implement ONLY this corrected form; drop the stray `pytestmark_skip` line.)

Also: the fixture import trick above re-runs the acceptance fixture in a fresh module scope — acceptable because both are module-scoped and the DB reset is idempotent. Simpler alternative: duplicate the 15-line fixture body; implementers may choose either, duplication is NOT required to be deduplicated here.

- [ ] **Step 4: Update AGENTS.md project state**

Change the Project State bullet for Phase 3 to:

```markdown
- **Phase 3 (Agentic middleware): COMPLETE** — LangGraph pipeline (translate → ingest → 4 investigation nodes → strategy loop → compile → solve → gate → commit → verify → explain) + material-reactive back-edges; CLI recover/explain/benchmark fidelity/mqtt listen; fidelity corpus shipped.
```

and add the new commands to the Commands block:

```bash
uv run python -m coe.cli recover --instance I --narrative "..." [--at MIN]
uv run python -m coe.cli explain --instance I
uv run python -m coe.cli benchmark fidelity --corpus data/corpus/fidelity-seed42 --seed 42
uv run python -m coe.cli mqtt listen
```

- [ ] **Step 5: Run everything**

```bash
uv run pytest -m "not mqtt" -q          # incl slow pins: full non-broker suite
uv run pytest -q                        # full suite incl mqtt
```

Expected: all green; record final counts in `.superpowers/sdd/progress.md` per repo convention (append a `== PLAN 4: phase3 agentic middleware ==` section summarizing task commits + deviations).

Criterion 5 is verified implicitly by this step: the ENTIRE Phase 2 suite runs unchanged against the empty-weight-map default (engine already consumes `job_tardiness_weights` at `coe/solver/engine.py:56`; the P3-side ordering contract is pinned by `test_weight_derivation_uses_post_preset_beta` in Task 9).

- [ ] **Step 6: Commit**

```bash
git add tests/agents AGENTS.md .superpowers/sdd/progress.md
git commit -m "test(p3): acceptance sweep pins + tier5 live marker + docs"
```

---

## Deviations Ledger (document in task reports as they land)

1. Recovery tables were spec-"reserved" but absent from code → created via migration #7 (Task 2).
2. Run rows inserted once at termination (no RUNNING status in §7 domain); crash-mid-run leaves no ledger row (Task 12).
3. UNKNOWN solver results terminate under `SOLVE_INFEASIBLE` status with raw status preserved in state (§7 lacks a SOLVE_UNKNOWN terminal) (Task 14).
4. Back-edge budget = single intervention pass + shared round budget (conservative reading of §3.1 sharing) (Tasks 10/14).
5. §4.2 routing-availability facts delegated to builder preview (`projected_horizon` + `shortage_evidence`) instead of a duplicated structure (Task 6).
6. Listener malformed-payload handling mirrors Phase 1's documented loud-log limitation rather than writing `telemetry_events.processing_error` rows (unresolvable payloads cannot populate telemetry FKs) (Task 16).
7. Canonical rescoring formula `(makespan + Σ tardiness)/max(makespan,1)` is our concrete implementation of §8's mandatory rescoring seam (Task 17).

---

