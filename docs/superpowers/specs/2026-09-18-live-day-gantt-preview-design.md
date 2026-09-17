# Live-Day Gantt Preview — Design

**Status:** Approved in conversation (2026-09-18)
**Date:** 2026-09-18
**Phase:** Post-live-day capability; extends the Simulate page
**Sibling spec:** `2026-09-13-live-day-simulator-design.md` (walk engine —
unchanged); this spec adds the *board* that page currently lacks
(the "mini-Gantt evolution" intent of `2026-09-12`, now realized).

## 1. Purpose

The live day is currently playable but invisible: the Simulate page shows a
big clock, a 10-line log tail, and a completion-time diff chart. This spec
adds the board itself — a machines × time Gantt of the *active* schedule
The live day is currently playable but invisible: the Simulate page shows a
big clock, a 10-line log tail, and a completion-time diff chart. This spec
adds the board itself — a machines × time Gantt of the *active* schedule
that advances with the clock, highlights completed / in-progress /
future operations by the now-marker, re-lays out after every committed
recovery, and completes the day with a before→final diff.

## 2. Scope

### Included

- **Live board** on the Simulate page for BOTH lanes (Live day + Scripted
  replay). It renders the same commit chain both modes drive; the scripted
  lane is unchanged behaviorally (reproducible) and gets the board for
  free.
- **Clock hero** above the board: the current simulation minute t
  (self-updating with the pace in paced mode; static at instant).
- **State encoding on bars** (per-bar arrays on the existing numeric
  `go.Bar` figure; no new palette, no per-trace splitting):
  - bar **colour = job colour** (one colour per JOB across all its
    operations — as built by the Configure page, commit 889444c);
  - **completed** (`end_time <= t`) → same colour at **opacity 0.35**
    (dimmed: time passed);
  - **in-progress** (`start_time <= t < end_time`) → same colour at full
    opacity + **amber border** (`marker.line.color = amber, width 3`);
  - **future** (`start_time > t`) → full opacity, no border.
- **Now-marker**: one thin vertical line at the current minute
  (CSS `vline` shape annotation on the figure — no HTML overlay needed;
  Streamlit repaints the figure in-place at the stable path).
- **Rebuild discipline** (render cost, spec §4.3): the figure is
  re-serialized on EVERY pass at a CONSTANT element path — under
  Streamlit's whole-page-rerun model an unpainted slot vanishes, so
  repaint-per-pass is the only streamlit-native mechanism compatible
  with the "no fragments / no autorefresh / don't overcomplicate"
  constraints. Python-side build cost (≈168 bars of data prep) is
  10–30ms against a paced dwell of ≥2s; the heavy page cost stays
  capped by the already-shipped 10-line log tail. A future
  isolated-subtree design could add between-boundary skipping —
  explicitly out of scope.
- **Option A at day end**: the final board stays on screen; the existing
  `_render_diff` (before-baseline vs after-final frames, `_render_diff`
  reused verbatim) renders BELOW it.

### Excluded

- Bar splitting (proportional fill of in-progress bars) — user ruling
  2026-09-18: "keep it simple"; the marker line carries the progress
  sense.
- Structured-event injection into live mode (spec 2026-09-13 §2 exclusion,
  unchanged).
- Any change to `live_day`, `walk_timeline`, the recovery graph, or the
  scripted lane's timeline semantics — display-only work over data that
  already exists.

## 3. Page layout (tree-stable contract)

The Simulate page now renders, in BOTH live and scripted modes, this
element sequence — constant in every state:

```
[clock slot]      st.empty()  — "t = 123 min (30×)" text, always present
[gantt slot]      plotly chart at a FIXED element path
[log box]         existing 4-element painter stanza (unchanged)
```

- Idle pre-run: clock slot shows "t = 0"; the gantt slot shows the
  static board preview of the committed active version (same figure,
  state classes all "future").
- Pacing text, paused, day-complete notes ride in the log painter's
  caption slot or the clock slot — never as free-standing conditional
  elements (S lesson from the double-box bug: tree shape must not vary).
- Any conditional above/between these slots is FORBIDDEN (fails a new
  page-invariant test).

## 4. Data flow

1. The board's source is the **active schedule** of the played instance
   (`active_schedule` view → `_fetch_active_entries` — unchanged query).
   The projector is *not* involved; the board is a read-only projection
   of the committed schedule at minute t. Phase 2 stays the sole
   scheduling authority (§1 rule, all specs).
2. Classification per entry: `end <= t` completed; `start <= t < end`
   in-progress; `start > t` future. At a recovery commit the new active
   version's entries re-derive entirely; `t` does not rewind (same rule
   as the walk, spec 2026-09-13 §3.4).
 3. Rebuild rule: NO fingerprint gate (it contradicts full-page rebuild
    semantics — an unpainted slot disappears). The figure repaints every
    pass at the SAME slot; the builder is a pure (entries, t) function so
    intermediate frames can never be wrong; `sim_gantt_fingerprint` does
    NOT exist as a session key.
4. The diff figure at day end uses the SAME session capture
   (`sim_before_entries`) as today, so commit-at-end produces the
   before→final comparison without new plumbing.

## 5. Architecture

```text
coe/dashboard/pages/simulate.py (modified)
    coe/dashboard/gantt.py (new, pure)
        classify(entries, t) -> {completed|in_progress|future: ids}
        build_board_figure(entries, t, jobs_palette) -> go.Figure
            (go.Bar, numeric minute axis, per-job colors,
             per-bar dim/border state arrays, vline marker)
            — pure: no DB, no solver, no session access
    render():
        tree-stable slots: [clock][gantt][log]
        clock: st.empty() slot, text = f"t = {t} min · {speed}×"
        gantt: st.plotly_chart(...) repainted EVERY pass (same path)
    (log painter unchanged)
```

Everything lives in the page module; no new files except tests. Build
helpers re-used as-is: `_fetch_active_entries`, the Configure page's
figure vocabulary (numeric bars, hh:mm axis labels, hover = J9/opN
Start/End/Worker/Durations — same builder functions where importable;
otherwise duplicated glue kept minimal and pointed at the Configure
module).

## 6. UX

- Clock line updates in place (`st.empty()`), so no dead repaint between
  boundaries.
- While paced: board visual changes only at boundaries (bar crossing
  marker), and after commits. The rest is clock movement + marker.
- Pause freezes everything; the last board remains; Resume continues.
- A new instance selection re-forks the lane and resets the board
  (existing `_ensure_live_lane` reset — extend to clear
  (No fingerprint reset needed in `_ensure_live_lane` — no such key
  exists.)
- Speed changes take effect on the next dwell (no re-decoration).

## 7. Testing strategy

- **Unit (engine-side, no UI)**: `_render_board` classification — feed
  fixture entries + t, assert (a) state class of each op, (b) opacity /
  border arrays (completed dimmed, in-progress bordered), (c) figure is
  serializable (returns a `go.Figure`).
- **Repaint discipline**: builder purity test — the same (entries, t)
  always yields the same figure data; a paced pass repaints at the
  constant slot (counting spy asserts ≥1 plotly paint per pass).
- **Page-invariant test (the double-box lesson)**: render() in every
  state emits the same element sequence for the live lane — clock slot,
  gantt slot, log stanza; ZERO conditional elements above the gantt;
  no `st.info`/free `st.caption` between slots.
- **Regression**: log painter invariants (single surface, tail/full
  split) and scripted-lane determinism tests stay green untouched.
- Quick gate green; no solver-time regressions (board read is a
  straight SELECT — reuse, no new solve paths).

## 8. Acceptance criteria

1. Live-paced day: board visible immediately after Resume; bar colours
   read job identity; completed ops dim behind the moving marker;
   in-progress draws an amber border.
2. A narrative mid-flight: solve at frozen t; the board re-lays out from
   the NEW active version only *after t* (history bars identical);
   clock never rewinds; log box untouched during the solve.
3. The board never vanishes or duplicates across paced rerenders
   (constant slot path; repaint-per-pass); a recovery commit shows the
   re-laid board immediately on the next pass.
4. Day end: final board remains; the before→final diff figure appears
   below it (reused `_render_diff`; option A).
5. Scripted lane: same board present; byte-reproducible lane unchanged
   (existing determinism regression stays green).
6. Tree-stable page invariant test green (no conditional elements in
   the clock/gantt/log sequence in any state).
7. Quick gate (`pytest -m "not mqtt and not slow"`) green.
