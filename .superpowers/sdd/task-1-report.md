# Task 1 Report: DisplayClock — user-speed paced display minutes

## Status
DONE_WITH_CONCERNS (implementation done, tests green, committed; the brief's class code contained three internal contradictions with its own tests — resolved minimally, documented below)

## Files Changed
- `coe/dashboard/pages/simulate.py` — added module-level `class DisplayClock` immediately after `_walk_paced_seconds` (before `_feed_line`); not wired into any lane; no other functions added.
- `tests/dashboard/test_simulate_page.py` — appended the brief's two tests verbatim (`test_display_clock_paces_and_freezes_at_target`, `test_display_clock_instant_jumps`) at end of file; existing helpers/tests untouched.

## TDD Evidence
1. **Tests written first** (verbatim from brief), run:
   `uv run pytest tests/dashboard/test_simulate_page.py -q -k display_clock`
   → `2 failed` with `ImportError: cannot import name 'DisplayClock'` (expected FAIL per brief Step 2).
2. **Implemented** `DisplayClock` in the page file.
3. **Verified PASS**: same command → `2 passed, 20 deselected`.
4. **Whole file regression**: `uv run pytest tests/dashboard/test_simulate_page.py -q`
   → `22 passed in 931.44s (0:15:31)` — 20 baseline + 2 new, nothing regressed.
   (Note: first attempt hit the 15-min shell timeout at 900s; the file legitimately takes ~15.5 min because `clean_db` forces baseline-clone re-solves. Rerun with 60-min timeout passed clean.)
5. Docker (both TimescaleDB containers + Mosquitto) confirmed up before test runs.

## Commit
- `d88c9e5` — `feat(simulator): DisplayClock — user-speed paced display minutes` (exactly the prescribed message; only the two brief-named files staged).

## Deviations from the brief's verbatim class code (each forced by the brief's own tests)
The brief's test code was used 100% verbatim. The brief's class code, used verbatim, FAILS those tests in three places; per the plan discipline (tests are the acceptance criteria; "fix minimally and document deviations"), the class was corrected minimally:

1. **`arm()` signature** — brief: `arm(self, target_t, now)`; but both brief tests call `arm(target_t=90)` with no `now` → verbatim code raises `TypeError`. Fix: `now: float = 0.0` default. Semantics unchanged for callers that pass `now` explicitly (Task 2's intended usage).
2. **Progress interpolation base** — brief's `advance` computes `int(self.prev_t + gap * (elapsed / wall_seconds))` where `prev_t` was already mutated by the previous call, so progress compounds (at 2.9s: 45 + 87 = 132 → capped at 90, test expects 87). Fix: `arm()` snapshots `self._start_t = self.prev_t` and `advance` interpolates from `_start_t`. This also matches the brief's prose intent ("arm() snapshots wallbase").
3. **Pace formula** — brief's `(gap / speed) * 60.0` wall-seconds gives 180s for the 90-min gap at 30×, but the brief's own test demands 3.0 wall-seconds (45 display-minutes at 1.5s), and the brief's test comment says "90 min gap at 30x = 3.0 wall-seconds". Fix: `wall_seconds = gap / max(int(speed), 1)` — i.e. speed = display-minutes per wall-second. Note this contradicts the one-line speed contract in the task context ("paces gap over (gap/N)*60 wall-seconds"); the test comment + assertions are self-consistent and were treated as authoritative per "both numbers verified by the two tests".

Final class (in `coe/dashboard/pages/simulate.py`):

```python
class DisplayClock:
    """Speed-paced display minute for the scripted arm (spec §10.1). ..."""

    def __init__(self, *, speed):
        self._speed = speed
        self.prev_t = 0
        self._target = 0
        self._start_t = 0
        self._armed_at = 0.0

    def arm(self, target_t: int, now: float = 0.0) -> None:
        self._armed_at = now
        self._target = int(target_t)
        self._start_t = self.prev_t

    def advance(self, now: float) -> int:
        gap = max(self._target - self._start_t, 1)
        if self._speed == "instant":
            self.prev_t = self._target
            return self.prev_t
        wall_seconds = gap / max(int(self._speed), 1)
        elapsed = max(now - self._armed_at, 0.0)
        self.prev_t = min(self._target, int(
            self._start_t + gap * (elapsed / wall_seconds)))
        return self.prev_t
```

## Concerns
1. **Brief vs test contradictions (the 3 deviations above)** — the most material is the pace formula: the task context's stated contract (`(gap/N)*60` wall-seconds) and the test disagree by a factor of 60. If Task 2's boundary walk assumes the `*60` variant, the sweep will run 60× slower than the tests pin. **The Task 2 implementer must treat `gap/speed` (test-pinned: 90 display-min at 30× = 3.0 wall-s) as the contract.**
2. The brief's `paused: bool` bullet (`Interfaces` section) describes solve-in-flight dwell semantics as "nothing to do — arm() snapshots wallbase; re-arm on resume only for the REMAINING gap". The `_start_t` snapshot supports the remaining-gap re-arm (re-arm with `now=resume_wall` and the unchanged target; gap recomputes from `prev_t` at re-arm). Task 2 should call `arm()` again on resume rather than relying on a `paused` attribute — no `paused` flag was added (not in the verbatim class; YAGNI until a lane consumes it).
3. Whole-file runtime is ~15.5 min (baseline re-solve per `clean_db` test) — future iteration on this file should use `-k` filters, and any "expect 20 passed" expectation is now 22.
4. Repo hygiene (not touched, per scope): the working tree contained an uncommitted stale `task-1-report.md` from a different plan ("Board classification + figure builder"); it was overwritten by this report as the designated report path, and it was excluded from the commit.

## Not done (by instruction)
- No wiring into either lane; no other new functions; whole test suite not run; no docker/server interaction beyond status check.
