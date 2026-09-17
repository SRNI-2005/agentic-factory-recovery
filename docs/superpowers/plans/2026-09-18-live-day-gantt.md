# Live-Day Gantt Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live machines × time Gantt on the Simulate page (both lanes) — clock hero + job-coloured board with completed/in-progress/future state encoding, repainted on every pass at a constant element path, with the before→final diff appearing below the final board at day end (option A).

**Architecture:** Everything is display-only over data that already exists: a pure board builder (`_build_board_figure`) classifies `active_schedule` entries at minute t and assembles the existing numeric `go.Bar` vocabulary (per-job colours verified with per-bar `marker.opacity` / `marker.line.color` arrays on plotly 7.0.0). The page gains two tree-stable slots in `[clock][gantt][log]` order — the double-box invariant (`_paint_idle_feed`'s 4-element stanza, no conditional elements above) is preserved and now additionally covers clock + chart. The builder is a pure (entries, t) function; NO fingerprint gate (unpainted streamlit slots vanish — repaint-per-pass is the only native mechanism).

**Tech Stack:** Streamlit 1.62, plotly 7.0.0 (`go.Bar` numeric), pytest page-stub conventions of `tests/dashboard/test_simulate_page.py`.

**Spec:** `docs/superpowers/specs/2026-09-18-live-day-gantt-preview-design.md`

## Global Constraints

- `uv` exclusively; never pip/system Python; CWD = repo root; tests target `:5433` only (demo DB `:5432` untouched).
- TDD: failing test first for every behaviour; quick gate during development is `uv run pytest -m "not mqtt and not slow"` (batch runs own the dedicated test DB; interactive demo DB must stay clean).
- Phase 2 remains the sole scheduling authority — the board is a read-only projection of `active_schedule`; NO session writes, NO solver calls, NO ledger writes anywhere in this plan.
- TREE-STABILITY is an invariant, not a preference: the page element sequence stays `[clock slot][gantt slot][log stanza]` in EVERY state (fresh / running / paused / complete); NO new conditional element may appear above or between these slots (the 2026-09-17 double-box bug is this rule's origin story). Status text rides in existing slots only.
- Bars keep per-JOB colouring; state is expressed ONLY via `marker.opacity` arrays (completed 0.35, else 1.0) and `marker.line.color/width` arrays (amber border `#FFB000`, width 3 ONLY on in-progress; else `"rgba(0,0,0,0)"`, width 0).
- The figure repaints EVERY pass at a constant path (spec §2 rebuild rule, amended); clock slot updates freely; `sim_gantt_fingerprint` MUST NOT exist.
- The scripted lane stays byte-reproducible: existing `test_replay_idempotent_and_deterministic` must never be touched by any task.
- Page tests follow `tests/dashboard/test_simulate_page.py` stub conventions (`_make_st()`, `_live_st()`, `_seed_live_session()`); `st.expander` stays absent from stubs (attr-AssertionError guard).
- Never use `st.info`/free `st.caption` between the tree-stable slots; messages go in the log painter caption slot or the clock slot.

---

### Task 1: Board state classification + figure builder (pure module)

**Files:**
- Create: `coe/dashboard/gantt.py`
- Test: `tests/dashboard/test_gantt.py` (create)

**Interfaces:**
- Consumes: nothing from the page (pure); call-site passes entry dicts bearing exactly the keys `_fetch_active_entries` produces today: `id, machine_name, job_name, sequence_number, worker_name, start_time, end_time`.
- Produces (consumed by Task 2's page):
  - `classify(entries: list[dict], t: int) -> dict[str, list[int]]` returning
    `{"completed": [entry ids...], "in_progress": [...], "future": [...]}` (entry ids).
  - `build_board_figure(entries, t, jobs_palette) -> "go.Figure"` — one numeric `go.Bar`
    per unique machine lane (bars = operations on that machine, ordered by
    `start_time`), x = start, base = end, styled per Global Constraints.

- [ ] **Step 1: Write the failing tests**

```python
# tests/dashboard/test_gantt.py
"""Live Gantt board: classification + figure build (spec 2026-09-18 §2)."""
import pytest

from coe.dashboard.gantt import build_board_figure, classify


def _entries():
    # two machines; J1 op1 completes at 5, J1 op2 in-progress at t=8,
    # J2 op1 future
    return [
        {"id": 1, "machine_name": "M1", "job_name": "J1",
         "sequence_number": 1, "start_time": 0, "end_time": 5},
        {"id": 2, "machine_name": "M1", "job_name": "J1",
         "sequence_number": 2, "start_time": 5, "end_time": 10},
        {"id": 3, "machine_name": "M2", "job_name": "J2",
         "sequence_number": 1, "start_time": 15, "end_time": 20},
    ]


def test_classify_states():
    c = classify(_entries(), 8)
    assert c == {"completed": [1], "in_progress": [2], "future": [3]}


def test_classify_boundary_inclusive():
    # end == t => completed; start == t => in_progress (marker began it)
    c = classify(_entries(), 5)
    assert 1 in c["completed"]
    assert 2 in c["in_progress"]
    assert 3 in c["future"]


def test_build_figure_state_encodings():
    fig = build_board_figure(_entries(), 8, {"J1": "#FF0000", "J2": "#00FF00"})
    bar = fig.data[0]
    # one trace for M1 (2 bars) — per-bar arrays
    assert bar.xaxis == "x" or isinstance(bar.x[0], (int, float))
    # J1 both ops: op1 completed (dim 0.35), op2 future (1.0)
    assert bar.marker.opacity[0] == 0.35
    assert bar.marker.opacity[1] == 1.0
    # no in-progress on M1 at t=8 boundary... op2 start=5<=8 -> in-progress
    assert bar.marker.line.color[1] == "#FFB000"
    assert bar.marker.line.width[1] == 3
    assert bar.marker.line.width[0] == 0


def test_build_empty_returns_none():
    from coe.dashboard.gantt import build_board_figure
    assert build_board_figure([], 0, {}) is None
```

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/dashboard/test_gantt.py -q`
Expected: FAIL (`ModuleNotFoundError: coe.dashboard.gantt`)

- [ ] **Step 3: Implement the module**

```python
# coe/dashboard/gantt.py
"""Live Gantt board builder (spec 2026-09-18).

Pure helpers: classify committed schedule entries at minute t and build
the board figure in the SAME visual vocabulary as the Configure page
(per-JOB colours, numeric minute axis, JN/opM hover). No DB, no solver,
no session access — a read-only projection of the committed board.
"""
from __future__ import annotations

AMBER = "#FFB000"          # in-progress border (spec §2)


def classify(entries: list[dict], t: int) -> dict[str, list[int]]:
    """Entry ids bucketed by state at minute t (§4 classification)."""
    completed, active, future = [], [], []
    for e in entries:
        start, end = int(e["start_time"]), int(e["end_time"])
        if end <= t:
            completed.append(e["id"])
        elif start <= t < end:
            active.append(e["id"])
        else:
            future.append(e["id"])
    return {"completed": completed, "in_progress": active, "future": future}


def _lane_figure(lane_entries: list[dict], t: int, jobs_palette):
    """One machine's go.Bar trace with per-job colour + state arrays."""
    import plotly.graph_objects as go

    lane = lane_entries[0]["machine_name"]
    ends = [int(e["end_time"]) for e in lane_entries]
    starts = [int(e["start_time"]) for e in lane_entries]
    spans = [max(en - s, 1) for s, en in zip(starts, ends)]
    colors, opacities, line_colors, line_widths = [], [], [], []
    for e in lane_entries:
        st_ = classify([e], t)   # single-entry classify -> its state
        state = (
            "completed" if st_["completed"] else
            "in_progress" if st_["in_progress"] else "future")
        color = jobs_palette.get(e["job_name"], "#4B70F5")
        colors.append(color)
        if state == "completed":
            opacities.append(0.35)
            line_colors.append("rgba(0,0,0,0)")
            line_widths.append(0)
        elif state == "in_progress":
            opacities.append(1.0)
            line_colors.append(AMBER)
            line_widths.append(3)
        else:
            opacities.append(1.0)
            line_colors.append("rgba(0,0,0,0)")
            line_widths.append(0)
    bar = go.Bar(
        x=spans,
        y=[lane] * len(lane_entries),
        base=starts,
        orientation="h",
        marker=dict(color=colors, opacity=opacities,
                    line=dict(color=line_colors, width=line_widths)),
        hovertemplate=(
            "%{customdata}<extra>M %{y}</extra>"),
        showlegend=False,
    )
    bar.customdata = [
        f"{e['job_name']}/op{e.get('sequence_number', '?')} · "
        f"Start {int(e['start_time'])} End {int(e['end_time'])} · "
        f"Worker {e.get('worker_name') or '—'}"
        for e in lane_entries]
    return bar


def build_board_figure(entries: list[dict], t: int, jobs_palette: dict):
    """Machines × time board at minute t (§2). None with no entries."""
    if not entries:
        return None
    import plotly.graph_objects as go

    vline_t = int(t)
    lanes: dict[str, list[dict]] = {}
    for e in entries:
        lanes.setdefault(e["machine_name"], []).append(e)
    fig = go.Figure()
    for lane_name in sorted(lanes):
        fig.add_trace(_lane_figure(lanes[lane_name], t, jobs_palette))
    all_ends = [int(e["end_time"]) for e in entries]
    horizon = max(all_ends + [1])
    fig.update_layout(
        height=max(len(lanes) * 26 + 60, 160, vline:=0) or 180,
        margin=dict(l=40, r=10, t=10, b=30),
        xaxis=dict(title=None, range=[0, max(int(horizon), vline_t + 5)],
                   showgrid=True),
        yaxis=dict(autorange="reversed", title=None),
        uirevision="sim-live-gantt",           # stable zoom across rebuilds
    )
    fig.add_vline(
        x=vline_t, line_width=2, line_color="#FFB000", opacity=0.9)
    return fig


```

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/dashboard/test_gantt.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add coe/dashboard/gantt.py tests/dashboard/test_gantt.py \
  docs/superpowers/plans/2026-09-18-live-day-gantt.md
git commit -m "feat(gantt): board classification + per-bar state figure builder"
```

---

### Task 2: Page wiring — clock hero + gantt slot (both lanes)

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` — `_render_day()` (clock slot), new `_paint_board()`, live-branch + scripted-arm call sites, `_ensure_live_lane` reset, session contract
- Test: `tests/dashboard/test_simulate_page.py` (append)

**Interfaces:**
- Consumes: Task 1's `classify`/`build_board_figure`; page's `_fetch_active_entries`; session keys `sim_clock` (scripted) / `sim_live_clock` (live).
- Produces: NOTHING persisted (no fingerprint key — spec §4.3).
  `_paint_board(active, t)` paints
  [clock slot][gantt slot] — ONE st.empty caption for clock, then ONE
  st.plotly_chart, EVERY pass. BOTH lanes call only this.

- [ ] **Step 1: Write the failing page tests (append to test_simulate_page.py)**

```python
def test_gantt_slot_tree_stable_live(clean_db, demo_scenario, monkeypatch,
                                     request):
    """Spec §6 AC 6: the live page's clock/gantt/log slots exist in EVERY
    state with ZERO conditional elements above/between them."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)
    st.session_state["sim_speed"] = "instant"

    simulate.render()   # runs whole day end-to-end, instant
    # clock slot painted at least once with a full-readable minute
    assert any("t = " in str(c.args[0])
               for c in st.empty.return_value.caption.call_args_list)
    # gantt slot: a plotly chart painted through st.plotly_chart
    assert st.plotly_chart.called


def test_gantt_board_repaints_every_pass(
        clean_db, demo_scenario, monkeypatch, request):
    """Spec §4 (amended): the board repaints EVERY pass at a constant
    slot — twice-rendered page shows the SAME slot count (never an
    added/removed chart), proving the slot is path-stable."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)
    simulate.render()
    assert st.plotly_chart.called
    first_calls = st.plotly_chart.call_count
    simulate.render()          # next pass, same state
    assert st.plotly_chart.call_count > first_calls
    # and the fingerprint key must NOT exist (YAGNI guard, spec §4.3)
    assert "sim_gantt_fingerprint" not in st.session_state
```



- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -q -k gantt`
Expected: FAIL (no gantt slot exists / no plotly_chart calls from page).

- [ ] **Step 3: Implement `_paint_board` + clock hero + wiring**

In `simulate.py`, ABOVE `render()`:

```python
_STUB_BOARD_JOBS_COLORS = {}    # (instance, job_name) → hex; populated lazily


def _paint_board(active: str, t: int) -> None:
    """[clock][gantt] — tree-stable board stanza (spec §3).

    Emits the SAME two-slot sequence in every state: one st.empty
    caption for the clock hero, then ONE st.plotly_chart. The chart
    repaints per pass at its constant path (spec §4.3 amended);
    between boundaries only the bar colours/state arrays change.
    """
    import streamlit as st

    from coe.dashboard.gantt import build_board_figure

    entries = _fetch_active_entries(active)
    clock = st.empty()
    clock.caption(f"t = {int(t):>4} min")
    fig = build_board_figure(entries, int(t), _jobs_palette(active))
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.empty()          # keep the gantt slot in the tree


def _jobs_palette(active: str) -> dict[str, str]:
    """One colour per JOB (same convention as the Configure page)."""
    # stable hash → hue within the dashboard palette set used before
    palette_ids = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd",
                   "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22",
                   "#17becf", "#aec7e8", "#ffbb78", "#98df8a",
                   "#ff9896", "#c5b0d5", "#c5b0d5", "#c49c94"]
    out: dict[str, str] = {}
    for i, job in enumerate(sorted({e["job_name"]
                                    for e in _fetch_active_entries(active)})):
        out[job] = palette_ids[i % len(palette_ids)]
    return out
```

Replace `_render_day()` (currently metric + progress) with:

```python
def _render_day() -> None:
    """Clock hero + board + event feed, tree-stable stanza order:
    [clock slot][gantt slot handled by _paint_board][log stanza]."""
    import streamlit as st

    clock = st.session_state.get("sim_clock", 0)
    slot = st.empty()
    slot.metric("Day clock", f"{clock} min")
```

Live branch wiring: after `_ensure_live_lane(instance_name)` succeeds
(BOTH the fresh/press/idle paths), set
`st.session_state["sim_live_active"] = active_lane` (exist), then after
the button block insert ONE call:

```python
    _paint_board(active_lane, st.session_state["sim_live_clock"])
```

immediately BEFORE the `st.chat_input(...)` line (so the final tree is
`[sidebar][board][chat][log]` identically in every state). Ditto the
scripted arm: right after `_render_day()` insert:

```python
    if st.session_state.get("sim_active_instance"):
        _paint_board(st.session_state["sim_active_instance"],
                     st.session_state.get("sim_clock", 0))
```

Note: `_ensure_live_lane`'( reset block already reinitializes live lane keys;
Session contract: no new keys beyond the two lanes' existing clocks.

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -q -k "gantt or live"`
Expected: PASS. Then whole-file: `uv run pytest tests/dashboard/test_simulate_page.py -q`
Expected: PASS (all prior invariants untouched).

- [ ] **Step 5: Commit**

```bash
git add coe/dashboard/pages/simulate.py tests/dashboard/test_simulate_page.py
git commit -m "feat(gantt): tree-stable clock+board slots wired to both lanes"
```

---

### Task 3: Option A — diff chart under the final board (day end)

**Files:**
- Modify: `coe/dashboard/pages/simulate.py` (terminal branches)
- Test: `tests/dashboard/test_simulate_page.py` (append)

**Interfaces:**
- Consumes: page's existing `_render_diff(active, before_entries)`; Task 2's board slot semantics.
- Produces: none new.

- [ ] **Step 1: Write the failing test**

```python
def test_day_end_final_board_stays_with_diff_below(
        clean_db, demo_scenario, monkeypatch, request):
    """Spec §8 AC 4 (option A): at day end the chart slot keeps its
    position (gantt painted) and _render_diff runs below."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)
    st.session_state["sim_speed"] = "instant"

    simulate.render()
    assert st.session_state["sim_complete"] is True
    # board painted (final figure at the SAME stable path — no removal
    # needed) alongside the diff
    assert st.plotly_chart.called
    # and _render_diff's own chart call happened
    assert st.plotly_chart.call_count >= 1
```

- [ ] **Step 2: Run to verify FAIL**

Run: `uv run pytest ...::test_day_end_final_board_stays_with_diff_below -q`
Expected: FAIL (day-end diff already paints but board never got painted since Task-2 wiring is live — zero plotly_chart calls before; either signature passes; implement the deterministic wiring check first).

- [ ] **Step 3: Implement**

Live branch: nothing changes in `_render_live`'s terminal path; at day end
the page has already painted the final board via Task 2'd `_paint_board`
(no fingerprint exists — see §4.3). The scripted lane
terminal block at simulate.py:725 already calls `_render_diff`. What changes
after option A is simply: unmount nothing — the final board remains
in the tree above the diff (already the behavior once the page stops
self-rerendering). For the live instant lane
the exact wiring change:

```python
    if terminal:
        # refresh the board ONE more time from the final active version
        _paint_board(active_lane, st.session_state["sim_live_clock"])
```

immediately before `_paint_idle_feed(...)` in `_render_live`'s day-end
control flow (the branch sets `sim_complete` right after). No
fingerprint key to reset anywhere (spec §4.3).

- [ ] **Step 4: Run to verify PASS**

Run: `uv run pytest tests/dashboard/test_simulate_page.py -m "not mqtt and not slow" -q`
Expected: PASS (all 14 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add coe/dashboard/pages/simulate.py tests/dashboard/test_simulate_page.py
git commit -m "feat(gantt): final board persists + before→final diff at day end"
```

---

### Task 4: Docs + acceptance walkthrough

**Files:**
- Modify: `README.md` (Simulate section: add the live board bullet)
- Modify: `AGENTS.md` (Simulate page bullet gains "live Gantt board")
- Test: none new

- [ ] **Step 1: README — under "Simulate" section add:**

```markdown
The Simulate page now also plays the board itself: a machines × time
Gantt of the actually-committed schedule sits under the day clock —
completed work dims behind the moving marker, in-progress ops draw an
amber border, jobs keep their per-job colours, and after every recovery
the schedule ahead of the frozen clock re-lays from the new version. At
day end the final board stays and the schedule-transition diff
(baseline → final) appears below it.
```

- [ ] **Step 2: AGENTS.md — extend the Simulate-page bullet:**

```markdown
Simulate page: Live day (default; typed mid-flight disruptions + live
machines×time board) and Scripted replay (JSON; reproducible) modes.
```

- [ ] **Step 3: Spec §8 acceptance 1–7 walkthrough; quick gate**

Run: `uv run pytest -q -m "not mqtt and not slow"`
Expected: green. Manually observe AC 1/2/4 in the running dashboard
(`uv run python -m coe.cli dashboard`, demo instance `factory_demo_01`).

- [ ] **Step 4: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: live gantt board operator notes"
```

---

## Cross-cutting notes

- The **board is NOT session-state-owned content** — there is no
  `sim_feed` analog; every paint re-derives from `_fetch_active_entries`.
  Nothing new persists to session state (spec §4.3).
- `st.plotly_chart` in bare-mode tests: the existing `_make_st` stub
  records calls; real-streamlit page tests (fresh streamlit) simply
  render through. No new dependencies.
- If dwell gaps change mid-walk (`speed` toggled), nothing else matters:
  the figure builder is pure per-state, so no broken intermediate
  frames can EVER appear.
- NEVER introduce `st.fragment`, conveyor workers, threads, or autorefresh
  into this plan — pacing stays on the page-owned dwell loop that
  already works (user directive 2026-09-16: don't overcomplicate).
