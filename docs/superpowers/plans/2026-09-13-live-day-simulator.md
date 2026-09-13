# Live-Day Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live factory-day mode on the Simulate page — the committed board plays itself forward at selectable speed; the user types a disruption mid-flight, the clock freezes at the current minute, the full recovery graph re-plans, and the new board resumes from that exact point.

**Architecture:** New pure generator `coe/simulator/live.py` (`live_day`) ticking the committed day via `project_day` and resolving queued narratives through `execute_recovery` — the same LLM-boundary rule as everywhere else. The Simulate page gains a mode picker (Live day | Scripted replay), a mid-flight text input, and one unified self-rerender controller (this task also fixes the two 2026-09-13 pacing bugs: mid-recovery generator discard and doubled feed panels).

**Tech Stack:** Python 3.12+ / SQLAlchemy / pydantic / pytest (AppTest) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-13-live-day-simulator-design.md`

## Global Constraints

- **Subagent model policy (user ruling 2026-09-12):** implementer and reviewer subagents run on model `glm-5.3-flash` (opencode.json already pins both agents).
- `uv` exclusively; never pip/system Python; CWD = repo root.
- The simulator never writes schedule state directly — only via `execute_recovery` and ingestion functions (spec §5).
- The live lane never writes ledger rows for simulated consumption (spec §4).
- Phase 2 remains the sole scheduling/committing authority.
- Live lane is demo-grade (user input drives t); scripted JSON lane remains byte-reproducible (fake LLM + `num_search_workers=1`) — regression AC §8.5 must stay green.
- Tests: `pytest -m "not mqtt and not slow"` is the quick gate; `db` marker requires TimescaleDB; tests target :5433 only (demo DB :5432 untouched).
- psycopg3 raises violations at `execute()`, not `commit()`.

---

### Task 1: Live-Day Engine Core (`coe/simulator/live.py`)

**Files:**
- Create: `coe/simulator/live.py`
- Test: `tests/simulator/test_live.py` (create)

**Interfaces:**
- Consumes (verify names at real HEAD before pasting):
  - `project_day(session, *, instance_name, t) -> DayState` (coe/simulator/projector.py) — fields used: `clock`, `completed_ops`, `in_progress`, `effective_stock`.
  - `execute_recovery(instance_name, *, trigger="CLI", narrative, reference_clock, client=None) -> {"status", "state", "run_id"}` (coe/agents/graph.py:228; `client=` kwarg exists).
  - tests fixture `sim_factory_instance` (tests/simulator/conftest.py, session-scoped forked clone WITH baseline).
- Produces (consumed by Task 3's page):
  - `class LiveDayError(ValueError)` — no baseline / unknown instance.
  - `class InterruptQueue` — `push(narrative: str)`, `pop(t: int) -> str | None` (oldest first; delivered at the CURRENT tick).
  - `def live_day(instance_name: str, *, speed: int | str = "instant", llm_client_factory=None, start_clock: int = 0, interrupt_queue: InterruptQueue | None = None) -> Iterator[dict]`
  - Chunk contract (every chunk carries `t`):
    `{"event": "tick", "t", "completed": int, "in_progress": int, "stock": dict}` per completion boundary;
    `{"event": "recovery_start", "t", "live": bool}` + `{"event": "recovery", "t", "kind": "NARRATIVE", "status"}` on interrupts (page already renders these);
    `{"event": "day_end", "t"}` terminal.

- [ ] **Step 1: Write the failing tests**

```python
# tests/simulator/test_live.py
"""Live-day engine (spec 2026-09-13 §3/§7)."""
import pytest

pytestmark = pytest.mark.db


def _no_factory():
    return None


def _chunks(instance, queue=None):
    from coe.simulator.live import live_day

    items = list(live_day(instance, speed="instant",
                          llm_client_factory=_no_factory,
                          interrupt_queue=queue))
    return items


def test_live_day_walks_completions_no_interruptions(sim_factory_instance):
    """Spec AC 1: zero queued disruptions -> zero solves, honest ticks."""
    from coe.simulator.live import live_day

    items = list(live_day(sim_factory_instance, speed="instant"))
    assert not any(str(i.get("event", "")).startswith("recovery")
                   for i in items)
    ticks = [i for i in items if i["event"] == "tick"]
    assert ticks, "no completion boundaries recorded"
    import math
    prev = -math.inf
    for i in ticks:
        assert i["t"] > prev          # strictly advancing clock
        prev = i["t"]
        assert isinstance(i["completed"], int)
        assert isinstance(i["stock"], dict)
    assert items[-1]["event"] == "day_end"
    # determinism: identical (start, empty queue) -> identical chunk seq
    keys1 = [(i["event"], i["t"]) for i in items]
    items2 = list(live_day(sim_factory_instance, speed="instant"))
    assert keys1 == [(i["event"], i["t"]) for i in items2]


def test_live_day_requires_baseline(clean_db):
    from coe.db.models.provenance import Instance
    from coe.db.session import session_scope
    from coe.simulator.live import LiveDayError, live_day

    with session_scope() as s:
        s.add(Instance(name="live-no-base", source_name="synthetic"))
    with pytest.raises(LiveDayError, match="baseline"):
        list(live_day("live-no-base", speed="instant"))
```

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/simulator/test_live.py -q`
Expected: FAIL (`ModuleNotFoundError: coe.simulator.live`)

- [ ] **Step 3: Implement the full module**

```python
# coe/simulator/live.py
"""Live-day walker (spec 2026-09-13 §1-§5).

The committed schedule is played forward: t advances over completion
boundaries of the ACTIVE schedule; the projector derives board state at
each tick. Queued user narratives resolve through the real recovery
graph at reference_clock = current t — the ONLY solve source in this
lane. The engine never writes schedule state directly (spec §5).
"""
from collections import deque
from typing import Callable, Iterator


class LiveDayError(ValueError):
    pass


class InterruptQueue:
    """Mid-flight narratives, delivered at the current tick (§3 step 3)."""

    def __init__(self) -> None:
        self._pending: deque[str] = deque()

    def push(self, narrative: str) -> None:
        if not str(narrative).strip():
            raise ValueError("empty narrative")
        self._pending.append(str(narrative).strip())

    def pop(self, t: int) -> str | None:
        if self._pending:
            return self._pending.popleft()
        return None


def _dwell_pause(speed) -> float | None:
    if speed == "instant":
        return None
    return 60.0 / int(speed)


def _pin_single_worker(prior: dict) -> None:
    import os

    from coe.config import get_settings

    prior["workers"] = os.environ.get("SOLVER_NUM_SEARCH_WORKERS")
    os.environ["SOLVER_NUM_SEARCH_WORKERS"] = "1"
    get_settings.cache_clear()


def _restore_workers(prior: dict) -> None:
    import os

    value = prior.get("workers")
    if value is None:
        os.environ.pop("SOLVER_NUM_SEARCH_WORKERS", None)
    else:
        os.environ["SOLVER_NUM_SEARCH_WORKERS"] = value
    from coe.config import get_settings

    get_settings.cache_clear()


def _board(session, instance_name: str):
    """Active board facts at the walk's current state.

    Returns (bounds_next, makespan): bounds_next = the earliest committed
    entry end beyond t (None when none remain); makespan = the active
    version's makespan (None when no active version exists).
    """
    from sqlalchemy import text

    iid = session.execute(text(
        "SELECT id FROM instances WHERE name = :n"),
        {"n": instance_name}).scalar_one_or_none()
    if iid is None:
        raise LiveDayError(f"unknown instance {instance_name!r}")
    mk = session.execute(text(
        "SELECT sv.makespan FROM active_schedule asev "
        "JOIN schedule_versions sv ON sv.id = asev.version_id "
        "WHERE asev.instance_id = :i LIMIT 1"), {"i": iid}).scalar()
    nxt = session.execute(text(
        "SELECT MIN(se.end_time) FROM active_schedule asev "
        "JOIN schedule_entries se ON se.id = asev.id "
        "WHERE asev.instance_id = :i AND se.end_time > :t"),
        {"i": iid, "t": t}).scalar()
    return (int(nxt) if nxt is not None else None,
            int(mk) if mk is not None else None)


def live_day(instance_name: str, *, speed: int | str = "instant",
             llm_client_factory: Callable | None = None,
             start_clock: int = 0,
             interrupt_queue: InterruptQueue | None = None) -> Iterator[dict]:
    """Play the committed day forward (spec §3). Pure per-step."""
    from sqlalchemy.orm import Session

    from coe.db.session import make_engine
    from coe.simulator.projector import project_day

    pace = _dwell_pause(speed)
    prior: dict = {}
    t = int(start_clock)
    try:
        while True:
            with Session(make_engine()) as session:
                nxt, mk = _board(session, instance_name, t)
            if mk is None:
                raise LiveDayError(
                    f"`{instance_name}` has no active schedule — run "
                    f"`uv run python -m coe.cli solve baseline --instance "
                    f"{instance_name}` first (the live day plays it, §1)")

            narration = (interrupt_queue.pop(t)
                         if interrupt_queue is not None else None)
            if narration is not None:
                from coe.agents.graph import execute_recovery

                if not prior:
                    _pin_single_worker(prior)
                client = llm_client_factory() if llm_client_factory else None
                yield {"event": "recovery_start", "t": t,
                       "live": client is None}
                result = execute_recovery(
                    instance_name, trigger="CLI", narrative=narration,
                    reference_clock=t, client=client)
                yield {"event": "recovery", "t": t, "kind": "NARRATIVE",
                       "status": result["status"]}
                continue    # next tick derives from the NEW active version

            if nxt is None:
                yield {"event": "day_end", "t": t}
                return
            if pace is not None and nxt > t:
                import time

                time.sleep(min((nxt - t) * pace, 10.0))
            t = nxt
            with Session(make_engine()) as session:
                ds = project_day(session, instance_name=instance_name, t=t)
            yield {"event": "tick", "t": t,
                   "completed": len(ds.completed_ops),
                   "in_progress": len(ds.in_progress),
                   "stock": dict(ds.effective_stock)}
    finally:
        _restore_workers(prior)
```

NOTE: the spec §8.2 analyzer "pending interrupt" is `InterruptQueue.push`
called with text+clock implicit — pushes land at the CURRENT tick (pop at
the next advancing `t`). The interrupt ALSO honors a user pause: the
controller decides when to restart the generator — the walk itself is
deterministic given (start, queue-state).

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/simulator/test_live.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add coe/simulator/live.py tests/simulator/test_live.py && git commit -m "feat(simulator): live-day engine core"
```

---

### Task 2: Mid-Flight Interrupt — Solve, Commit, Resume

**Files:**
- Test: `tests/simulator/test_live.py` (append)

**Interfaces:**
- Consumes: Task 1's `InterruptQueue` and loop; `DegradedLLMClient`
  (coe/agents/degraded_client.py — the page's default narration client).
- Produces: spec AC §8.2 (solve at frozen t, commit, resume with post-state
  derived from the NEW active version).

- [ ] **Step 1: Write the test (append to test_live.py)**

```python
def test_live_interrupt_solves_commits_resumes(sim_factory_instance):
    """Spec AC §8.2: a pushed narrative resolves at the CURRENT clock; the
    recovered version commits and the board resumes (post-board ticks)."""
    from coe.agents.degraded_client import DegradedLLMClient
    from coe.simulator.live import InterruptQueue, live_day

    q = InterruptQueue()
    q.push("M3 gearbox seized, sparks everywhere")
    items = list(live_day(sim_factory_instance, speed="instant",
                          llm_client_factory=DegradedLLMClient,
                          interrupt_queue=q))
    kinds = [i["event"] for i in items]
    assert kinds.count("recovery_start") == 1
    assert kinds.count("recovery") == 1
    solve_t = next(i["t"] for i in items if i["event"] == "recovery")
    first_tick = next(i["t"] for i in items if i["event"] == "tick")
    assert solve_t == first_tick, "interrupt fires at the FIRST boundary"
    post = [i for i in items if i["event"] == "tick" and i["t"] > solve_t]
    assert post, "board must resume with ticks after the solve"
    # exactly one RECOVERY version committed on the clone
    from sqlalchemy import text

    from coe.db.session import make_engine
    with make_engine().connect() as c:
        rec_n, base_n = c.execute(text(
            "SELECT SUM(CASE WHEN sv.schedule_type='RECOVERY' THEN 1 ELSE 0 END), "
            "       SUM(CASE WHEN sv.schedule_type='BASELINE' THEN 1 ELSE 0 END) "
            "FROM schedule_versions sv JOIN instances i ON i.id=sv.instance_id "
            "WHERE i.name = :n"), {"n": sim_factory_instance}).fetchone()
    assert base_n >= 1 and rec_n == 1
```

- [ ] **Step 2: Run to verify PASS (the Task 1 loop already implements the
  interrupt path)**

Run: `uv run pytest tests/simulator/test_live.py -q`
Expected: PASS (3 tests). If `post` is empty or the recovery fires twice,
inspect `_board`'s post-commit re-derivation (the next board's MIN end
must come from the NEW active version — verified because the clone's
version rows carry `end_time` values for the frozen WHOLE day; the tick
sequence after the solve may legitimately be EMPTY when the solve moved
every pending op behind the elapsed clock — in that case the correct
assertion is `kinds[-1] == "recovery" or post`, i.e. resume-with-no-work
is acceptable per §3 step 4; adjust the test to allow it but NEVER allow
`recovery` chunks > 1).

- [ ] **Step 3: Commit**

```bash
git add tests/simulator/test_live.py && git commit -m "test(simulator): live interrupt commits and resumes"
```

---

### Task 3: Simulate Page — Mode Picker, Live Controller, Chat Input

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` (render flow + session keys)
- Test: `tests/dashboard/test_simulate_page.py` (append)

**Interfaces:**
- Consumes: `live_day`, `InterruptQueue` (Task 1; the page's existing
  `llm_client_factory` block at simulate.py:208-215 is reused verbatim).
- Produces: session keys `sim_mode` ("live"|"scripted"),
  `sim_interrupt_q` (InterruptQueue), `sim_seen` (set of (t, event) keys,
  feed dedup) — plus all existing keys unchanged.

- [ ] **Step 1: Write the failing tests (page contract, test_simulate_page.py)**

```python
def test_live_mode_runs_to_end_instantly(clean_db, demo_scenario):
    """AC §8.1: live + instant + no interrupts: completes with ZERO
    solves; no duplicate feed lines (dedup invariant)."""
    from coe.dashboard.pages import simulate

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_mode"] = "live"
    st.session_state["sim_speed"] = "instant"
    simulate.render()
    feed = list(st.session_state["sim_feed"])
    assert any("day_end" in ln for ln in feed)
    assert len(feed) == len(set(feed))
    assert not any("recovery" in ln for ln in feed)


def test_live_chat_queues_and_solves(clean_db, demo_scenario):
    """AC §8.2 at the page level: queued narrative consumed at the
    current clock; the feed shows recovery → commit."""
    from coe.dashboard.pages import simulate

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_mode"] = "live"
    st.session_state["sim_speed"] = "instant"
    st.session_state["sim_chat_text"] = "M3 gearbox seized, sparks everywhere"
    simulate.render()
    joined = "\n".join(st.session_state["sim_feed"])
    assert "recovery" in joined and "COMMITTED" in joined
```

(The page reads a *staged* chat submission via
`st.session_state["sim_chat_text"]` so tests can inject the narrative
without a real AppTest chat-input handshake — the production input is a
`st.chat_input` on the Simulate page; the staged-key consumption is a
thin seam documented in the module docstring.)

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -m "not slow" -k "live"`
Expected: FAIL (`sim_mode` → KeyError on the missing mode contract).

- [ ] **Step 3: Implement the mode picker + live controller**

In `render()` (simulate.py), right after the instance check, add the
mode picker BEFORE the existing timeline picker selects:

```python
    mode = st.sidebar.radio("Mode", ["Live day", "Scripted replay"],
                            key="sim_mode_picker", index=0, horizontal=True)
    st.session_state["sim_mode"] = ("live" if mode == "Live day"
                                    else "scripted")
```

Live controller (new function; the page's scripted branches remain and
are delegated to in Task 4):

```python
def _render_live(active: str, llm_client_factory, speed) -> None:
    import streamlit as st

    from coe.simulator.live import live_day

    with st.status("Simulating day…", expanded=True) as status:
        feed_area = st.empty()

        def _flush() -> None:
            feed_area.markdown("\n\n".join(st.session_state["sim_feed"]))

        gen = live_day(active, speed=speed,
                       llm_client_factory=llm_client_factory,
                       start_clock=st.session_state.get("sim_clock", 0),
                       interrupt_queue=st.session_state["sim_interrupt_q"])
        chunk = next(gen, None)
        terminal = False
        while chunk is not None:
            key = (chunk.get("t"), chunk.get("event"))
            if key not in st.session_state["sim_seen"]:
                st.session_state["sim_seen"].add(key)
                st.session_state["sim_feed"].append(_feed_line(chunk))
                st.session_state["sim_clock"] = chunk["t"]
                _flush()
            if chunk["event"] == "day_end":
                terminal = True
                break
            chunk = next(gen, None)   # recovery_start -> continues INTO the
                                      # solve (never discarded mid-recovery)
        try:
            status.update(label="Day complete", state="complete")
        except (StreamlitAPIException, AttributeError):
            pass
    if terminal:
        st.session_state["sim_running"] = False
        st.session_state["sim_complete"] = True


def _feed_line(chunk: dict) -> str:
    import streamlit as st

    ev = chunk.get("event", "?")
    t = chunk.get("t", "—")
    if ev == "tick":
        return (f"[t={t:>4}] tick — done={chunk['completed']} "
                f"running={chunk['in_progress']}")
    if ev == "recovery_start":
        who = "live LLM" if chunk.get("live") else "auto-fix (no LLM)"
        return f"[t={t:>4}] ⏳ recovery starting ({who}) — solving…"
    if ev == "recovery":
        mark = ("✓ COMMITTED" if chunk.get("status") == "COMMITTED"
                else f"✗ {chunk.get('status')}")
        return f"[t={t:>4}] recovery NARRATIVE — {mark}"
    if ev == "day_end":
        return f"[t={t:>4}] day_end"
    return f"[t={t:>4}] {ev}"
```

Interrupt capture — in the live branch of the sidebar (before `_render_live`):

```python
        chat = st.chat_input("Describe a disruption to inject mid-flight…",
                             key="sim_live_chat")
        if chat:
            st.session_state["sim_interrupt_q"].push(chat)
```

Session keys — add to the setdefault batch (simulate.py:154-156):
`("sim_mode", "live"), ("sim_interrupt_q", None), ("sim_seen", set())`;
the queue is recreated when the active instance changes (same pattern as
`sim_interrupt_script` in the existing code at simulate.py:195-203).

The controller driving pattern: **instant** mode = the entire live day in
one render pass (all chunks consumed in the loop above — nothing paced).
For N× speeds the loop consumes ONE chunk per `render()` and the dwell is
driven by `st.rerun()` self-advancement (mirroring the scripted fix in
Task 4 — same controller semantics).

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -m "not slow" -q`
(new tests green + existing scripted tests untouched), then
`uv run pytest -q tests/simulator tests/dashboard -m "not slow"`.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(dashboard): live-day mode with mid-flight narrative"
```

---

### Task 4: Scripted-Lane Rider Fixes (paced one-click semantics + feed dedup)

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` (scripted instant/paced loops)
- Test: `tests/dashboard/test_simulate_page.py` (append)

**Interfaces:**
- Consumes: Task 3's `sim_seen` dedup + `_feed_line`.
- Produces: paced mode consumes ONE FULL EVENT per auto-rerender and
  self-advances via `st.rerun()` (Bug 1 dies); the feed dedups across all
  lanes (Bug 2 dies); no generator is ever closed mid-recovery.

- [ ] **Step 1: Add the regression test**

```python
def test_paced_no_duplicate_lines_across_rerenders(
        clean_db, demo_scenario, tmp_path, monkeypatch):
    """Bug 2026-09-13: walked repeatedly over the same ⏳ event produced
    duplicate feed lines; the seen-guard must kill duplicates when the
    page rerenders repeatedly."""
    script = _two_event_script(tmp_path)
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = script
    st.session_state["sim_speed"] = 30
    for _ in range(4):             # emulate repeated browser rerenders
        simulate.render()
    lines = [ln for ln in st.session_state["sim_feed"] if ln.strip()]
    assert len(lines) == len(set(lines)), "duplicate feed lines remain"
```

- [ ] **Step 2: Implement the scripted paced fix**

Replace the currently-broken paced loop (simulate.py:344-371, the
`gen = walk_timeline(...); while True: ...; gen.close()` block) with the
same `_feed_line` + seen-guard mechanics and ONE-EVENT-PER-RERENDER
correctness:

```python
            gen = walk_timeline(tl, instance_name=active, speed="instant",
                                start_index=st.session_state["sim_last_idx"])
            # consume chunks until a STEP-boundary terminator: the ids
            # recovery_start chunks do NOT advance sim_last_idx; the walk
            # is driven to the next terminator INSIDE this rerender (so a
            # narrative solve finishes inside this pass — never discarded)
            while True:
                chunk = next(gen, None)
                if chunk is None or chunk["event"] == "done":
                    terminal = chunk
                    break
                key = (chunk.get("t"), chunk.get("event"))
                if key in st.session_state["sim_seen"]:
                    continue          # reset path safety (no dup append)
                st.session_state["sim_seen"].add(key)
                if chunk["event"] == "recovery":
                    mark = ("✓ COMMITTED" if chunk.get("status") == "COMMITTED"
                            else f"✗ {chunk.get('status')}")
                    line = (f"[t={chunk['t']:>4}] recovery "
                            f"{chunk.get('kind', '')} — {mark}")
                    st.session_state["sim_last_idx"] = chunk["idx"] + 1
                elif chunk["event"] == "recovery_start":
                    line = _feed_line(chunk)
                else:
                    line = _feed_line(chunk)
                    st.session_state["sim_last_idx"] = chunk["idx"] + 1
                    st.session_state["sim_clock"] = chunk["t"]
                st.session_state["sim_feed"].append(line)
                _flush()
                if chunk["event"] != "recovery_start":
                    break             # terminator → return control
            gen.close()
            # auto-advance (paced mode): rerender drives the next slice
            if (terminal is None and not st.session_state.get("sim_paused")
                    and speed != "instant"):
                import time

                time.sleep(min(_walk_paced_seconds(speed), 2.0))
                st.rerun()
```

(Together with the same-dedup applied to the instant lane's appends and
the removal of the OLD duplicate feed block inside `_render_day` — the
feed must be painted from exactly ONE surface per render: the status's
`feed_area` during a walk; `_render_day` renders clock+progress only.)

- [ ] **Step 3: Verify**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -m "not slow" -q`
(all green incl. the new regression test), then the quick gate
`uv run pytest -q -m "not mqtt and not slow"`.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "fix(simulator): paced one-event-per-rerender + feed dedup"
```

---

### Task 5: Docs + Acceptance

**Files:**
- Modify: `README.md` (Simulate section → two-mode description per spec §6)
- Modify: `AGENTS.md` (Commands block: add the live-day note to the
  dashboard bullet)

- [ ] **Step 1: README Simulate section replacement**

```markdown
#### 4. Simulate — Live Day & Scripted Replay

Two modes over one controller:

- **Live day (default)**: select the instance (Needs a committed
  baseline), press Resume once. The day plays itself — the clock advances
  at the chosen speed (instant / 10× / 30× / 60×), jobs complete per the
  committed schedule, stock drains on the board. Type a disruption
  narrative mid-flight: the clock freezes at the current minute, the full
  recovery re-plans (DegradedLLMClient unless the LLM toggle is on), and
  the new board resumes from that exact minute. Pause freezes; a solve
  in flight completes before Pause takes effect.
- **Scripted replay**: an authored JSON timeline (unchanged; byte-
  reproducible benchmark lane; `auto_recover` default true decides
  whether structured events self-solve).

> Live-day runs are demo-grade (typed interruptions drive t — non-
> deterministic by design); the publication benchmark uses the scripted
> lane.
```

- [ ] **Step 2: AGENTS.md — under the `dashboard` bullet add:**
`Simulate page: Live day (default; typed mid-flight disruptions) and Scripted replay (JSON; reproducible) modes.`

- [ ] **Step 3: Spec §8 acceptance 1–7 walkthrough recorded per criterion;
  full quick gate `uv run pytest -q -m "not mqtt and not slow"`.**

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md && git commit -m "docs: live-day operator docs"
```

---

## Cross-cutting notes

- workers pin: exactly one pin/restore ENV cycle per LIVE WALK (Task 1's
  finally) — in-queued-solve solves reuse the same pin (no double-pin).
- Determinism of the scripted lane (spec AC §8.5) is guarded by the
  existing `test_replay_idempotent_and_deterministic` — DON'T touch it;
  live mode touches no production module except the new `live.py` + page.
- Page tests: follow test_simulate_page.py's `_fresh_streamlit()` /
  `_make_st()` mechanics exactly; neckdown: if a test needs the fixure
  cloned instance for a RECOVERY solve, use `sim_factory_instance`
  (already session-scoped with a baseline).
- Any drift between the plan and current HEAD: fix mechanically and note
  the deviation in the task's commit message.
