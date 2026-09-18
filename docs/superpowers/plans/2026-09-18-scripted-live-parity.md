# Scripted Lane Live-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Scripted replay behave exactly like Live day on the Simulate page — the board clock sweeps 0 → board horizon at the user's speed, authored events pause the clock mid-flight (solve inline, ⏳ painted immediately, board re-lays after commit), the day ends at the makespan (not the last event), and both lanes emit one unified log format.

**Architecture:** 100% page-level display. `walk_timeline` (engine), `live_day`, and the recovery graph stay untouched (deterministic lane §3b); the page gains a display-clock interpolator keyed on the authored event schedule, and `_paint_board` classifies against the interpolated minute. The unified log format is one pure helper both `_render_live` and the scripted walker call. Terminus clock = last boundary of the final active version.

**Tech Stack:** Streamlit page stub tests; pytest; no new deps.

**Spec:** `docs/superpowers/specs/2026-09-18-live-day-gantt-preview-design.md` §10 (A2) — supersedes §9.2.

## Global Constraints

- `uv` exclusively; tests on :5433; TDD for every behavior; quick gate during dev `uv run pytest -m "not mqtt and not slow"`.
- `walk_timeline`, `live_day`, recovery graph **untouched** — page-only changes; `test_replay_idempotent_and_deterministic` must stay green untouched.
- TREE-STABILITY: `[clock][gantt][log]` stanza, zero conditional elements anywhere above; messages ride caption slots.
- LANE-SCOPED feeds (`sim_feed_live` / `sim_feed_scripted`) and `sim_run_pressed`/chat seams unchanged.
- Unified log format (both lanes): `[t=<4>] done=N running=M · <detail>`.
- No `st.fragment`, no threads, no autorefresh — the dwell controller stays.
- `sim_display_driver` session keys are page-owned; the scripted arm may share the live-lane key naming (`sim_live_clock`, `sim_live_active` — used by `_paint_board`); do not introduce new global state beyond `sim_display_*`.

---

### Task 1: Display-clock driver — `board_display_clock()` (pure)

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` (new top-level helper; no engine file)
- Test: `tests/dashboard/test_simulate_page.py` (append)

**Interfaces:**
- Consumes: nothing external (pure math on wall-time + a sweep descriptor).
- Produces: `class DisplayClock` —
  - `__init__(speed)` where speed is "instant" or positive int.
  - `arm(target_t: int)` — the sweep sector [prev_t, target_t]; page calls when an event chunk dictates "next authored target".
  - `advance(now) -> int` returns the display minute given wall-clock; caps at `target_t`; `instant` jumps straight to target's final board boundary (drives to a terminators list `0..horizon` boundary intervals — see Task 2's boundary walk: instant's `target_t` IS the next boundary, so jump is honest).
  - `paused: bool` flag for solve-in-flight dwell (page-level; the clock holds while the solve block runs — nothing to do, the interpolator's `now` just stops advancing? NO — paused holds the SEGMENT until resume: segment must SURVIVE pausing, so `arm()` snapshots wallbase; re-arm on resume only for the REMAINING gap).

- [ ] **Step 1: Write the failing test**

```python
def test_display_clock_paces_and_freezes_at_target():
    from coe.dashboard.pages.simulate import DisplayClock
    import types

    fake_time = {"now": 0.0}
    clock = DisplayClock(speed=30)
    clock.prev_t = 0
    clock.arm(target_t=90)          # 90 min gap at 30x = 3.0 wall-seconds
    fake_time["now"] = 1.5
    assert clock.advance(fake_time["now"]) == 45
    fake_time["now"] = 2.9
    assert clock.advance(fake_time["now"]) == 87
    fake_time["now"] = 3.0
    assert clock.advance(fake_time["now"]) == 90    # exactly target
    fake_time["now"] = 9.0
    assert clock.advance(fake_time["now"]) == 90    # holds AT target


def test_display_clock_instant_jumps():
    from coe.dashboard.pages.simulate import DisplayClock

    c = DisplayClock(speed="instant")
    c.prev_t = 0
    c.arm(target_t=90)
    assert c.advance(now=0.001) == 90
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/dashboard/test_simulate_page.py -q -k display_clock`
  Expected: FAIL (`ImportError: DisplayClock`).

- [ ] **Step 3: Implement**

```python
class DisplayClock:
    """Speed-paced display minute for the scripted arm (spec §10.1).

    Paces [prev_t, target_t] over (target-prev)/N wall-seconds; caps at
    target. Instant jumps. Survives solve pauses because the target is
    reached by 'time still materially pending'.
    """

    def __init__(self, *, speed):
        self._speed = speed
        self.prev_t = 0
        self._target = 0
        self._armed_at = 0.0

    def arm(self, target_t: int, now: float) -> None:
        self._armed_at = now
        self._target = int(target_t)

    def advance(self, now: float) -> int:
        gap = max(self._target - self.prev_t, 1)
        if self._speed == "instant":
            self.prev_t = self._target
            return self.prev_t
        # pace law (pinned by the plan's tests): N = display-minutes per wall-second —
    # "30x" plays a 90-min gap in ~3.0 wall-seconds (90/30)
    wall_seconds = max(gap, 1) / max(int(self._speed), 1)
        elapsed = max(now - self._armed_at, 0.0)
        self.prev_t = min(self._target, int(self.prev_t + gap * (elapsed / wall_seconds)))
        return self.prev_t
```

(NOTE: the arm/advance pair uses absolute wall timestamps handed in;
tests inject `now=`.)

- [ ] **Step 4: Run to verify PASS** — same command, expect 2 passed.

- [ ] **Step 5: Commit**

```bash
git add coe/dashboard/pages/simulate.py tests/dashboard/test_simulate_page.py
git commit -m "feat(simulator): DisplayClock — user-speed paced display minutes"
```

---

### Task 2: Scripted arm drives the DisplayClock (event pauses at authored minutes; sweep to makespan)

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` (scripted arm only; `_render_live` untouched)
- Test: `tests/dashboard/test_simulate_page.py` (append)

**Interfaces:**
- Consumes: Task 1 `DisplayClock` (attributes prev_t/_target; methods arm(target_t, now), advance(now)), engine chunks from `walk_timeline` (unchanged signatures), `_paint_board`, `_paint_idle_feed(lane, caption, full)`.
- Produces: session keys `sim_display_t` (interpolated minute), `_scripted_targets` (`[(t, event_kind, idx)]` remaining authored targets) — BOTH owned by the scripted arm; live lane keys untouched.

The scripted walk EXISTS as event chunks; re-shaping the page's pacing to display-clock means the paced arm must (per dwell pass): (a) pick the next target (next authored event t OR the board's next completion boundary, whichever is closer), (b) advance the display clock to that boundary inside THIS pass's dwell window, (c) if the boundary IS an authored event minute — pause, consume the event via the engine walk (ingest/auto-recover), repaint board+log, arm the next target. The event "chunks" drive the walk exactly as today; the DISPLAY interpolation merely paints intermediate board states between them.

- [ ] **Step 1: Failing tests (append)**

```python
def test_scripted_paces_between_events(
        clean_db, demo_scenario, tmp_path, monkeypatch, request):
    """Spec §10 AC 8: paced scripted sweep interpolates the display
    clock across event gaps instead of teleporting; recovers at the
    authored minute."""
    _live_pace_env(monkeypatch, request)
    script = json.dumps({
        "name": "parity", "seed": 1, "horizon_days": 2,
        "auto_recover": False,
        "events": [{"t": 90, "kind": "MACHINE", "event_type": "FAILURE",
                    "machine_id": "M3"},
                   {"t": 300, "kind": "MACHINE", "event_type": "FAILURE",
                    "machine_id": "M3"}],
    })
    p = tmp_path / "parity.json"; p.write_text(script)
    _scripted_pace_env(monkeypatch, str(p))
    st = _make_st(); sim_page = _stub_render_env(monkeypatch, st)
    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_speed"] = 30
    st.sidebar.selectbox = MagicMock(return_value="parity.json")
    col = st.sidebar._col_run
    pressed = {"run": True}; col.button = lambda label, **k: pressed["run"]
    sim_page.render()          # pass 1: arms sweep toward 90
    assert 0 < st.session_state["sim_display_t"] < 90, "pass must advance interpolation beyond 0 without reaching the event minute"
    # pass 2..n until sweep arrives at 90, then the engine consumes the
    # event: monotonic monkeypatch (stepping 5s per call) forces the
    # sweep; after# several passes the ingest completes.
    for _ in range(3):
        sim_page.render()
    assert st.session_state["sim_last_idx"] == 1   # event 1 consumed

def test_scripted_day_ends_at_board_horizon(
        clean_db, demo_scenario, tmp_path, monkeypatch, request):
    """Spec §10.1: after consuming ALL authored events, the sweep keeps
    going to the final active version's makespan; the terminal rail cites
    that day-end clock (NOT the last ingest's 300)."""
    _live_pace_env(monkeypatch, request)
    script = tmp_path / "horizon.json"
    script.write_text(json.dumps({
        "name": "horizon", "seed": 1, "horizon_days": 2,
        "auto_recover": False,
        "events": [{"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
                    "machine_id": "M3"},
                   {"t": 300, "kind": "MACHINE", "event_type": "FAILURE",
                    "machine_id": "M3"}]}))
    _scripted_pace_env(monkeypatch, str(script))
    st = _make_st(); sim_page = _stub_render_env(monkeypatch, st)
    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_speed"] = 60
    st.sidebar.selectbox = MagicMock(return_value="horizon.json")
    pressed = {"run": True}
    st.sidebar._col_run.button = (
        lambda label, **k: pressed["run"])
    # sweep passes (display-only) + terminator consumption until done:
    for _ in range(400):            # 406-min day at 60x = ~7s of passes
        try:
            sim_page.render()
        except SystemExit:          # spare SleepCap exceptions in bare
            pass
        pressed["run"] = False      # only the very first pass presses Run
        if st.session_state.get("sim_running") is False:
            break
    assert st.session_state["sim_last_idx"] == 2    # both events consumed
    assert st.session_state["sim_display_t"] > 300  # swept PAST the last
    # and the day never terminates before the display clock passes the
    # final ingest's clock (no stranding future ops at day end).
```

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -q -k "paces or horizon"`
Expected: FAIL (today's scripted pass jumps t straight to event time and terminates at last event).

- [ ] **Step 3: Implement — scripted paced arm redesign (page-level only)**

In `render()`'s scripted walk, replace the current `else:` paced block's
one-terminator-per-rerender loop with:

```python
        # --- sweep bookkeeping (the ONLY new logic) ------------------
        import time
        from coe.dashboard.pages.simulate import DisplayClock  # Task 1

        entries = _fetch_active_entries(active)
        makespan = max((int(e["end_time"]) for e in entries), default=0)
        authored = [int(ev.t) for ev in tl.events]     # engine data — TL
        last_idx = st.session_state["sim_last_idx"]
        upcoming = (authored[last_idx] if last_idx < len(authored)
                    else makespan)
        sweep_target = min(upcoming, makespan)
        display = st.session_state.setdefault(
            "sim_display", DisplayClock(speed=speed))
        display.prev_t = st.session_state.get("sim_display_t", 0)
        now = time.monotonic()
        display.arm(sweep_target, now)
        st.session_state["sim_display_t"] = display.advance(
            time.monotonic())                        # interpolate THIS pass
```
The chunk consumption loop stays LITERALLY AS TODAY (ingest,
auto_recover, recovery_start, recovery semantics untouched: sim_last_idx
advances per terminator; sim_clock jumps to the authored t on every
chunk kind — the one-line 330-freeze closure is
`st.session_state["sim_clock"] = chunk["t"]` moved OUT of the else
branch, so recovery chunks advance it too).

but wrap the dwell/per-pass time bookkeeping so the DISPLAY clock
(`sim_clock`) interpolates via `DisplayClock` toward the NEXT authored
target (or a board boundary inside the gap):

```python
            display = st.session_state.setdefault(
                "sim_display", DisplayClock(speed=speed))
            upcoming = next((t for t, idx in targets_while if idx > last_idx),
                            makespan)
            display.arm(target_t=int(min(upcoming, makespan)),
                        now=time.monotonic())
            interim = display.advance(time.monotonic())
            st.session_state["sim_display_t"] = interim
```

The `_paint_board` per pass uses `st.session_state["sim_display_t"]`
instead of `sim_clock` for the scripted arm; on event chunk consumption
the REAL sim_clock jumps to the authored minute (`sim_clock` advances on
every chunk kind — closes the 330-freeze) and the sweep re-arms from it.
When `sim_last_idx == total`, arm the FINAL segment target =
`_fetch_active_entries(active)[ends] max` (the board horizon) and
set the terminal rail line from the day-end clock. Instant speed jumps
`DisplayClock(speed="instant")` jumps to the immediate target — same
as the live lane's instant walk.

- [ ] **Step 4: Run to verify PASS (whole file, then the two new tests)**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -q`
Expected: PASS (existing 20 unweakened + 2 new + the unified-log Task 3 tests to come).

- [ ] **Step 5: Commit**

```bash
git add coe/dashboard/pages/simulate.py tests/dashboard/test_simulate_page.py
git commit -m "feat(simulator): scripted lane display-clock pacing + makespan terminus"
```

---

### Task 3: Unified log format (both lanes)

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` (`_feed_line` / record sites)
- Test: `tests/dashboard/test_simulate_page.py` (append)

**Interfaces:**
- Consumes: `coe.dashboard.gantt.classify` + `_fetch_active_entries` (pure read-only, single query per chunk — cached per pass via a tiny memo keyed by (instance, t)).
- Produces: unified `_feed_line(instance, chunk, t)` format:
  `[t=<4>] done=N running=M · <detail>`.

- [ ] **Step 1: Failing test**

```python
def test_unified_log_schema_both_lanes(clean_db, demo_scenario,
                                        monkeypatch, request):
    """Spec §10 log unification: live tick lines and scripted detail
    lines both carry 'done=N running=M · <detail>'."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)
    st.session_state["sim_speed"] = "instant"

    simulate.render()
    feed = st.session_state["sim_feed_live"]
    tick_lines = [ln for ln in feed if ln.strip()]
    assert tick_lines, "live day must log something"
    assert all(re.search(r"done=\d+ running=\d+ ·", ln)
               for ln in tick_lines if not ln.startswith("[t=") is False
               or "done=" in ln), "live lines carry state numbers"
    # scripted lane: same schema on ingest lines. Reuse the offline
    # scripted scaffold (_scripted_pace_env + _stub_render_env like the
    # Task-2 tests): after the terminal pass, sim_feed_scripted lines
    # match `done=\d+ running=\d+ ·` for every ingest/recovery line.
```

- [ ] **Step 2: FAIL → implement → PASS**: `_feed_line` gains an
  `instance`/`t`-aware wrapper that prefixes `done=X running=Y · ` via
  `classify(_fetch_active_entries(instance), t)`; live `_record`,
  scripted append sites route through it. Keep dedup keys as-is.

- [ ] **Step 3: Commit**
```bash
git add coe/dashboard/pages/simulate.py tests/dashboard/test_simulate_page.py
git commit -m "feat(simulator): unified log schema — done/running on every line"
```

---

### Task 4: Day-end diff pair + docs + acceptance

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` (`_render_diff` call sites → paint `frames[0]` then `frames[-1]`)
- Modify: `README.md`, `AGENTS.md` (behavior claims: live-parity pacing + unified logs)
- Test: `tests/dashboard/test_simulate_page.py` (extend diff test: assert TWO plotly repaints at terminal beyond board repaints — or assert caption "Initial" + "Final")

- [ ] **Step 1: Failing test (extend the Task-3 day test)**

```python
    # inside test_day_end_final_board_stays_with_diff_below (after the
    # chart asserts), assert the transition section shows both frames:
    assert st.plotly_chart.call_count >= 3          # pre-walk, final, diff-before+final
```

and implement `_render_diff` to paint `frames[0]` and `frames[-1]` with
captions "Initial (baseline)" / "Final (after the last recovery)".

- [ ] **Step 2: Docs** — extend README's Simulate bullet with:
`Scripted replay carries the same live-day pacing (clock sweeps to the
board horizon at your speed; events pause and re-lay the board) and the
same log schema (done/running numbers on every line).`
AGENTS.md: extend the Simulate bullet with `Scripted replay now paces
like Live day (clock → board horizon).`

- [ ] **Step 3: Quick gate + manual AC walkthrough (AC 8/9 of §10.3)**

Run: `uv run pytest -q -m "not mqtt and not slow"` → 0 failures;
observe scripted parity on the dashboard against factory_demo_01.

- [ ] **Step 4: Commit**

```bash
git add coe/dashboard/pages/simulate.py tests/dashboard/test_simulate_page.py \
  README.md AGENTS.md
git commit -m "feat(simulator): scripted/live parity — diff pair, docs, acceptance"
```

---

## Cross-cutting notes

- The browser experience change is in `_paint_board`'s `t` argument
  source per lane: live keeps `sim_live_clock` (walk-derived), scripted
  switches to `sim_display_t` (sweeping display clock).
- `sim_display_t` MUST reset with the scripted arm's `sim_clock=0` (fresh
  Run) and the lane reset contract; leftover display states must never
  leak across instances (same rule as feeds, 2026-09-16).
- `display.arm()`'s "arm start now" uses `time.monotonic()`; test seams
  inject `now=` for determinism.
- `walk_timeline` remains the event engine; no chunk shape change; the
  330-freeze closes because `sim_clock` advances on every chunk kind
  (Task 2), and the sweep then interpolates display clocks beyond.
- If a recovery commits mid-gap, the board horizon changes: re-arm the
  sweep target to the NEW makespan (the same place the walker's
  terminal logic already queries). No extra state beyond what
  `_fetch_active_entries` + max(end_time) give per pass.
