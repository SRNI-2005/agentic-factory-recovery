# Task 1 Report: Timeline Schema

## Status
DONE

## Files Changed
- `coe/simulator/__init__.py` (created, empty)
- `coe/simulator/timeline.py` (created)
- `tests/simulator/__init__.py` (created, empty)
- `tests/simulator/test_timeline.py` (created)
- `coe/config.py` (modified — added 3 simulate_* settings)

## Test Evidence

**Step 2 — FAIL (expected):**
```
uv run pytest tests/simulator/test_timeline.py -q
4 failed in 0.03s
FAILED tests/simulator/test_timeline.py::test_load_happy_path - ModuleNotFoundError
FAILED tests/simulator/test_timeline.py::test_per_kind_field_exclusivity - ModuleNotFoundError
FAILED tests/simulator/test_timeline.py::test_non_monotonic_rejected - ModuleNotFoundError
FAILED tests/simulator/test_timeline.py::test_horizon_guard - ModuleNotFoundError
```

**Step 5 — PASS:**
```
uv run pytest tests/simulator/test_timeline.py -q
....                                                                     [100%]
4 passed in 0.02s
```

## Commit
`25d2970` — `feat(simulator): timeline schema`

## Deviations from Brief

1. **NarrativeEvent `at` field (implementation):** ~~Test data includes `"at": 1000` on the NARRATIVE event but the brief's `NarrativeEvent` model lacks the field. Added `at: int | None = Field(default=None, ge=0)` to the model. Without this, all tests that load the BASE fixture fail with `extra_forbidden`.~~ **FIXED.** `at` field removed from `NarrativeEvent`; `"at": 1000` removed from BASE fixture. Spec §5 NARRATIVE carries `t` only.

2. **`TimelineEvent` callable (implementation):** The brief defines `TimelineEvent` as `Annotated[Union[...], Field(discriminator="kind")]` which is not callable in pydantic v2 — `TimelineEvent(**data)` raises `TypeError`, not `ValidationError`. Replaced with a `_TimelineEventFactory` callable class that dispatches to the correct event subclass. The underlying union type is kept as `_TimelineEventUnion` for use in the TypeAdapter and `Timeline.events`.

3. **`resource_kind` property (implementation):** The test asserts `tl.events[0].resource_kind == "MACHINE"` but the brief's model only has `kind`. Added `@property resource_kind` returning `self.kind` on MachineEvent, WorkerEvent, and MaterialEvent.

4. **`test_per_kind_field_exclusivity` second assertion (test fix):** ~~The brief's test calls `TimelineEvent(**BASE["events"][2])` expecting `ValidationError`, but the input is a perfectly valid MaterialEvent (all required fields present, no extra fields). Changed to `TimelineEvent(**BASE["events"][2] | {"machine_id": "M1"})` to actually test field exclusivity (MACHINE field on a MATERIAL event).~~ **FIXED.** Both exclusivity assertions now use concrete classes (`MachineEvent`, `MaterialEvent`) instead of the non-callable Annotated union `TimelineEvent`.

## Self-Review Notes
- Module docstring quotes spec §3/§5 per convention.
- All times integer minutes, `t: int = Field(ge=0)`.
- `extra="forbid"` enforced on all event models and Timeline.
- Settings added as int/bool with defaults matching brief.
- No Alembic changes, no DB dependency — plain pydantic tests.

## Fix Report (25d2970→HEAD)

**What changed:**
1. `coe/simulator/timeline.py:62` — removed `at: int | None = Field(default=None, ge=0)` from `NarrativeEvent`.
2. `tests/simulator/test_timeline.py:24` — removed `"at": 1000` from `BASE["events"][3]` fixture.
3. `tests/simulator/test_timeline.py:39-43` — replaced `TimelineEvent(...)` factory calls with concrete classes (`MachineEvent`, `MaterialEvent`) in exclusivity test.

**Test output:**
```
uv run pytest tests/simulator/test_timeline.py -q
....                                                                     [100%]
4 passed in 0.01s
```
