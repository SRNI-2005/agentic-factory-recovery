# Live-Day Simulator — Design

**Status:** Approved (user-approved in conversation, 2026-09-13)
**Date:** 2026-09-13
**Phase:** Post-day-simulator capability; extends the Simulate page
**Supersedes:** the "live-day" threads of `2026-09-12-day-simulator-design.md`
(scoped there as explicitly-unbuilt design intent); the JSON replay lane of
that spec remains valid verbatim.

## 1. Purpose

The scripted-day simulator replays *authored* timelines. This spec adds the
mode the whole project was driving toward: a **live factory day** — the
baseline board plays itself forward at selectable speed, jobs complete per
the committed schedule, materials drain, and the user disrupts it with a
typed narrative at any moment. The clock pauses, the full recovery graph
runs at the frozen clock, and the new board resumes from that exact minute.
It is what the Simulate page means to be.

## 2. Scope

### Included

- **Live-day engine** (`coe/simulator/live.py`): a self-advancing generator
  that walks the *committed schedule forward* — no JSON input.
- **Mid-flight narrative capture** on the Simulate page: a text input that
  pauses the clock, queues the narrative, and triggers a full recovery at
  the current simulation minute (reference_clock = current t).
- **Unified page controller**: Resume (once) drives the day; Pause freezes;
  instant mode jumps event-to-event; N× dwells between slices. Corrects
  the broken paced path (2026-09-13 bug: per-click generators never
  advanced `sim_last_idx` and were discarded mid-recovery).
- Feed deduplication + single feed surface (the double-panel bug dies here).
- Scripted lane preserved: `walk_timeline` + JSON timelines unchanged as a
  reproducible benchmark lane; `auto_recover` (default true) governs only
  that lane — a disruption authored in JSON solves when the toggle is on;
  with it off, authored events write facts only. **The live lane has no
  such toggle: the user's typed narrative always resolves in a solve**
  (auto-fix or LLM per the existing narration toggle).

### Excluded

- Structured-event injection into live mode (Configure-page buttons feeding
  the played day) — future extension; live mode takes *typed narratives*
  only.
- Physical hardware, real-MQTT coupling (the engine calls the ingestion
  functions directly where scripted events exist).
- Cancellation of in-flight solves (Pause completes the running solve).
- Any scheduling authority for the simulator (Phase 2 remains sole
  committer; the engine only drives public entry points).

## 3. Behavior (the contract)

1. Select instance → press **Resume** once → `running = true`.
2. Each automatic page rerender advances **one dwell slice**:
   - instant: t jumps to the next "interesting" boundary (next completion
     cluster); no dwell. If no disruption is queued, the day walks to its
     end without any solve (nothing to re-plan — the board plays as-is).
   - N×: dwell = `gap × (60 / N)` wall-seconds between slices, N schedule-
     minutes per wall-minute.
3. On a queued narrative (user typed): clock freezes at the current t; the
   engine runs `execute_recovery(reference_clock=t, ...)`
   (translate → strategy → solve → gate → commit → verify → explain,
   identical to the CLI path — with the narration toggle choosing live LLM
   vs `DegradedLLMClient`).
4. On commit: the new version becomes the played board; the clock **does
   not rewind** — completed work per the frozen history remains done, and
   the projector re-derives completions from the *new* active version.
   Resumed from that exact t on the new board.
5. Pause: `running = false` — a queued solve in flight finishes first
   (solves cannot be cancelled), then the day holds at its commit t until
   Resume.
6. Feed: single surface, appends guarded by `(t, idx)`-style index keys —
   no line can ever duplicate; repeated identical lines are impossible by
   construction.

## 3b. Determinism note (normative)

The live lane is **demo-grade**: wall-clock user input drives t, so runs
are not byte-reproducible. The scripted JSON lane remains the reproducible
path (byte-identical commit chains under fake LLM + workers=1). Both are
first-class; the publication benchmark consumes the scripted lane.

## 4. Materials & ledger in live mode

- **Display:** the board shows the projector's effective stock — draining
  as completed operations pass the clock (read-only truth, already exists).
- **Ledger writes** happen only when a solve commits (committer CONSUME
  rows) or a scripted restock materializes (RESTOCK row). A live day's
  simulated consumption without a re-solve writes **no** ledger rows — the
  audit ledger records committed reality, never projected fantasy.

## 5. Architecture

```text
coe/simulator/live.py   (new)
    live_day(instance_name, *, speed, llm_client_factory, start_clock,
             interrupt_queue) -> Iterator[dict feed chunks]
    ── pure per-step; drives ONLY:
         coe.simulator.projector.project_day       (completed/in-progress/
                                                    effective-stock at t)
         coe.agents.graph.execute_recovery         (on queued narrative;
                                                    reference_clock = t)
         coe.mqtt.ingest (only if the user injects structured disruption —
         out of scope; narrative-only for v1)
Simulate page (coe/dashboard/pages/simulate.py)
    ├─ mode picker: Live day (default) | Scripted replay (JSON)
    ├─ controller: Resume once -> self-rerender dwell loop
    │    (st.rerun per slice; instant = zero dwell)
    ├─ sim_interrupt queue (session state): narrative text scheduled at
    │    the current clock; consumed by the next engine step
    └─ single deduplicated feed; ⏳ during in-flight solves
```

One-way flow preserved; the live engine never writes schedule state except
through the graph. **Bug-fix riders (same PR, same controller):** the
scripted lane's paced controller adopts the self-rerender semantics (Bug 1:
per-event generators discarded mid-recovery), and the feed becomes single-
surface with an `(t, idx)` dedup guard (Bug 2: doubled panels).

## 6. Page UX

- Mode picker: Live day (source = the selected instance) vs Scripted
  (timeline file picker).
- Speed selector governs both modes; the LLM toggle and auto-fix semantics
  are shared and unchanged.
- Resume once → the day runs itself. Pause freezes at the next slice
  boundary (mid-solve: freezes after the finish). Resume continues from
  the same t. Text input always visible while running; typing pauses t.

## 7. Testing strategy

- **Tier 1 (engine, unit):** live_day with a zero-dwell walk and an empty
  queue → pure projector progression (completion boundaries at ends), no
  solves, no commits, feed lines well-formed; determinism: identical
  (start, queue) ⇒ identical chunk sequence.
- **Tier 2 (interrupt):** queue a narrative before walking → the walk
  consumes it at the queued clock, commits exactly one version, resumes;
  the final projector state reflects the *new* active version.
- **Tier 3 (controller):** AppTest: Resume once → self-rerender advances
  (mocked clock) → Pause mid-flight → state frozen → Resume continues from
  the frozen t; instant mode completes.
- **Tier 4 (regression):** scripted path unchanged — walk_timeline tests
  stay green; prefixed-channel feed dedup proves no repeated lines.
- Quick gate remains green; live-mode tests use fake LLM + dwell=0.

## 8. Acceptance criteria

1. Live mode with no queued disruptions runs the day to its end with zero
   recovery solves (nothing to recover) and honest completion/stock
   feedback.
2. A narrative typed mid-flight freezes the clock at the current t, runs
   the full recovery (DegradedLLMClient by default), commits, and resumes
   — the board after resume equals a manual `recover --at t` outcome.
3. Pause pauses; Resume resumes from the frozen t. A solve in flight
   completes before Pause takes effect.
4. Speed instant jumps event-to-event; N× dwells per the scale.
5. The scripted lane (JSON) keeps byte-identical replay under fake LLM +
   `num_search_workers=1` (regression from the day-simulator spec).
6. No duplicate feed lines under any pacing (the double-panel and
   repeated-⏳ bugs from 2026-09-13 cannot reproduce).
7. Quick gate green.
