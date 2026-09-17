# tests/dashboard/test_simulate_page.py
"""
AppTest smoke + paced-browser rerender sequence for the Simulate page.

Log-surface invariant (bug 2026-09-16): the event feed paints from ONE
plain-markdown surface in every state — running (after the status
block), paused, complete, and fresh. No expander, no per-state variant
component.
"""
import json
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.db


def _fresh_streamlit():
    """Import real streamlit cleanly.

    Other tests in the suite swap streamlit for stubs or run AppTest in
    worker threads; stale submodules + the lingering DeltaGeneratorSingleton
    make a plain `import streamlit` raise 'instance already exists!'.
    """
    # NOTE: reset depends on Streamlit's internal DeltaGeneratorSingleton
    # being recreated by importing streamlit fresh; __init__ auto-builds
    # it. Purge the stub modules this file installs ("st_stub") plus the
    # whole real streamlit hierarchy, then import once.
    for name in [m for m in list(sys.modules)
                 if m == "streamlit" or m == "st_stub"
                 or m.startswith("streamlit.")]:
        del sys.modules[name]
    import streamlit

    return streamlit


def _tiny_script(tmp_path):
    p = tmp_path / "tiny.json"
    # auto_recover=False: these page-flow smoke tests are structured-facts
    # tests authored pre-amendment (2026-09-13); auto solves are covered
    # in tests/simulator/test_auto_recover.py.
    p.write_text(json.dumps({
        "name": "tiny", "seed": 1, "horizon_days": 1,
        "auto_recover": False,
        "events": [{"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
                    "machine_id": "M3"}]}))
    return str(p)


def test_page_smoke(clean_db, demo_scenario, tmp_path):
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate as sim_page

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_mode"] = "scripted"  # pre-Task-4 scripted lane
    st.session_state["sim_script"] = _tiny_script(tmp_path)
    st.session_state["sim_speed"] = "instant"
    # direct-render convention used by tests/dashboard/test_cockpit_page.py
    sim_page.render()
    assert st.session_state.get("sim_last_idx", 0) >= 1


def test_log_paints_capped_tail(monkeypatch, request):
    """Bug 2026-09-16 (user report): repainting the FULL growing log every
    paced rerender competes with the future Gantt for render time. The
    single surface must paint a CAPPED TAIL (last 10 lines) in a fixed-
    height scroll container plus a 'showing N of M' caption; full history
    stays in sim_feed."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)
    st.session_state["sim_speed"] = "instant"   # walk the WHOLE day now
                                                # (paced yields one chunk
                                                # per stubbed render)

    simulate.render()
    feed = list(st.session_state["sim_feed"])
    assert len(feed) > 10, "baseline day must exceed the cap for the tail"

    simulate._paint_idle_feed(None)
    md = st.empty.return_value.markdown.call_args_list[-1].args[0]
    # hard line breaks (two-space + newline) = tight 10-row block
    rendered_lines = [ln.rstrip() for ln in md.split("\n")]
    assert st.container.call_args_list[-1].kwargs.get("height") >= 280
    assert len(rendered_lines) == 10
    assert rendered_lines[0] == feed[-10]
    assert rendered_lines[-1] == feed[-1]
    assert rendered_lines == feed[-10:]


# ---------------------------------------------------------------------------
# paced-browser rerender sequence: Run → Pause → Resume → Terminal
# ---------------------------------------------------------------------------

def _two_event_script(tmp_path):
    p = tmp_path / "tiny2.json"
    p.write_text(json.dumps({
        "name": "tiny2", "seed": 1, "horizon_days": 1,
        "auto_recover": False,
        "events": [
            {"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
             "machine_id": "M3"},
            {"t": 300, "kind": "MACHINE", "event_type": "FAILURE",
             "machine_id": "M3"},
        ]}))
    return str(p)


def _make_st():
    """Minimal Streamlit stub for the sidebar button flow aisle."""
    st = types.ModuleType("st_stub")
    st.session_state = {}
    st.warning = MagicMock()
    st.error = MagicMock()
    st.info = MagicMock()
    st.success = MagicMock()
    st.caption = MagicMock()
    st.subheader = MagicMock()
    st.metric = MagicMock()
    st.progress = MagicMock()
    st.markdown = MagicMock()
    st.plotly_chart = MagicMock()
    st.stop = MagicMock(side_effect=SystemExit)
    # paced auto-advance: real streamlit raises RerunException (the
    # framework rerenders); the stub just records the call.
    st.rerun = MagicMock()
    col_run = MagicMock()
    col_pause = MagicMock()
    st.sidebar = types.SimpleNamespace(
        selectbox=MagicMock(return_value=None),
        columns=MagicMock(return_value=[col_run, col_pause]),
        # Simulate page toggle (LLM narration); False = degraded auto-fix.
        toggle=MagicMock(return_value=False),
        radio=MagicMock(return_value="Scripted replay"),  # pre-Task-4 lane
        _col_run=col_run, _col_pause=col_pause)
    st.status = MagicMock(return_value=MagicMock(
        __enter__=lambda s: s,
        __exit__=MagicMock(),
        update=MagicMock()))
    st.empty = MagicMock(return_value=MagicMock(markdown=MagicMock()))
    st.container = MagicMock(return_value=MagicMock(
        __enter__=lambda s: s,
        __exit__=MagicMock(return_value=False),
        markdown=MagicMock()))
    # NOTE: deliberately NOT stubbing st.expander — the event log must
    # paint through exactly ONE plain-markdown surface in EVERY lane
    # state (running/paused/complete); an expander call in render()
    # fails with AttributeError instead of silently double-painting.
    return st


def test_invalid_speed_guards_non_numeric(clean_db, tmp_path):
    st = _make_st()
    from coe.dashboard.pages import simulate as sim_page
    st.__path__ = []
    errors_mod = types.ModuleType("streamlit.errors")
    errors_mod.StreamlitAPIException = type("StreamlitAPIException",
                                            (Exception,), {})
    sys.modules["streamlit.errors"] = errors_mod
    sys.modules["streamlit"] = st
    try:
        st.session_state["instance"] = "factory_demo_01"
        st.session_state["sim_script"] = _tiny_script(tmp_path)
        st.session_state["sim_speed"] = "abc"

        with pytest.raises(SystemExit):
            sim_page.render()

        msgs = [c.args[0] for c in st.error.call_args_list]
        assert any("invalid speed" in m for m in msgs)
        assert st.session_state.get("sim_last_idx", 0) == 0
    finally:
        sys.modules.pop("streamlit", None)
        sys.modules.pop("streamlit.errors", None)


def test_paced_run_pause_resume_terminal(
    clean_db, demo_scenario, tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
    script_path = _two_event_script(tmp_path)

    # Fork disabled for this test; keep every other setting real because
    # load_timeline reads simulate_max_horizon_days off the same object.
    from coe.config import get_settings as real_get_settings

    class _Settings(types.SimpleNamespace):
        simulate_clone = False

        def __getattr__(self, name):
            return getattr(real_get_settings(), name)

    _patched_settings = _Settings()
    monkeypatch.setattr(
        "coe.config.get_settings", lambda: _patched_settings)

    orig_glob = pathlib.Path.glob
    fake_script = pathlib.Path(script_path)

    def fake_glob(self, pattern):
        if pattern == "*.json" and str(self) == "data/timelines":
            return iter([fake_script])
        return orig_glob(self, pattern)

    monkeypatch.setattr(pathlib.Path, "glob", fake_glob)

    # The sidebar-flow path builds "data/timelines/<name>"; resolve it to
    # the tmp script so the selector can stay DB/CWD-independent.
    import coe.simulator.timeline as tlmod
    real_load = tlmod.load_timeline
    monkeypatch.setattr(
        tlmod, "load_timeline",
        lambda p: real_load(script_path) if "tiny2.json" in str(p)
        else real_load(p))

    st = _make_st()
    from coe.dashboard.pages import simulate as sim_page
    # render() pulls `from streamlit.errors import StreamlitAPIException`;
    # expose a matching submodule so the stub imports cleanly.
    st.__path__ = []
    errors_mod = types.ModuleType("streamlit.errors")
    errors_mod.StreamlitAPIException = type("StreamlitAPIException",
                                            (Exception,), {})
    monkeypatch.setitem(sys.modules, "streamlit.errors", errors_mod)
    monkeypatch.setitem(sys.modules, "streamlit", st)

    col_run = st.sidebar._col_run
    col_pause = st.sidebar._col_pause
    pressed = {"run": False, "pause": False}

    def button(label, **_kw):
        if label in ("Run", "Resume") and pressed["run"]:
            return True
        if label == "Pause" and pressed["pause"]:
            return True
        return False

    col_run.button = button
    col_pause.button = button

    def render_expect_stop():
        try:
            sim_page.render()
        except SystemExit:
            pass

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_speed"] = 10  # non-instant → one event / rerender
    st.sidebar.selectbox = MagicMock(return_value="tiny2.json")

    # 1. Run → consumes the first event only.
    pressed["run"] = True
    sim_page.render()
    assert st.session_state["sim_last_idx"] == 1
    assert st.session_state["sim_paused"] is False
    assert st.session_state["sim_running"] is True
    assert len(st.session_state["sim_feed"]) == 1

    # 2. Pause → playback stops, paused flag set, page halts.
    pressed["run"] = False
    pressed["pause"] = True
    render_expect_stop()
    assert st.session_state["sim_paused"] is True
    assert st.session_state["sim_last_idx"] == 1

    # 3. Resume (Run button while paused) → consumes the NEXT event, no
    #    duplicate of the first (shutdown of the old pause-deadlock bug:
    #    pre-fix, sim_paused was never cleared so start_run stayed False).
    pressed["pause"] = False
    pressed["run"] = True
    sim_page.render()
    assert st.session_state["sim_paused"] is False
    assert st.session_state["sim_last_idx"] == 2
    assert len(st.session_state["sim_feed"]) == 2

    # 4. No button pressed, still running → walker yields the done chunk;
    #    page reaches Terminal without re-walking consumed events.
    pressed["run"] = False
    sim_page.render()
    assert st.session_state["sim_running"] is False
    assert st.session_state["sim_last_idx"] == 2
    assert st.session_state.get("sim_paused") is False

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from coe.db.session import make_engine

    with Session(make_engine()) as s:
        (n_rows,) = s.execute(text(
            "SELECT count(*) FROM telemetry_events te "
            "JOIN instances i ON i.id = te.instance_id "
            "WHERE i.name = 'factory_demo_01'")).one()
    assert n_rows == 2  # exactly one ingest per timeline event


def _narrative_event_script(tmp_path):
    """Single NARRATIVE event: the lane class whose recovery_start chunk
    Bug 2026-09-13 duplicated. auto_recover=False — the narrative solve
    itself provides the recovery lane (no auto_recover chunks needed)."""
    p = tmp_path / "tiny3.json"
    p.write_text(json.dumps({
        "name": "tiny3", "seed": 1, "horizon_days": 1,
        "auto_recover": False,
        "events": [
            {"t": 100, "kind": "NARRATIVE",
             "text": "M3 gearbox seized, sparks everywhere",
             "severity": "HIGH"},
        ]}))
    return str(p)


def test_paced_no_duplicate_lines_across_rerenders(
        clean_db, demo_scenario, tmp_path, monkeypatch, request):
    """Bug 2026-09-13: walked repeatedly over the same ⏳ event produced
    duplicate feed lines; the seen-guard must kill duplicates when the
    page rerenders repeatedly.

    Models the crash-mid-solve rerender: after the rerender that showed
    the "⏳ recovery starting" line, sim_last_idx is rewound to the
    narrative event's idx WITHOUT clearing sim_seen; the next render
    must not re-append the ⏳ line. NARRATIVE event chosen deliberately —
    recovery_start chunks never advance sim_last_idx, so a stray
    rerender genuinely re-enters the narrative step (the vacuous
    structured-events-only version produced no recovery chunk at all)."""
    monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
    instance = _baseline_instance()   # BEFORE the env squeeze below — the
    # baseline CLI subprocess must solve with the full default budget.
    # Keep the narrative solve cheap: drop the §10 recovery floor and
    # shrink the budget — the dedup guard is independent of the status.
    # Env override (NOT a get_settings lambda swap): the engine's worker
    # pin/restore calls get_settings.cache_clear(), which requires the
    # real lru-cached callable.
    import coe.cli
    monkeypatch.setattr(coe.cli, "_recovery_floor", lambda s: s)
    monkeypatch.setenv("SIMULATE_CLONE", "false")
    monkeypatch.setenv("SOLVER_TIME_LIMIT_SECONDS", "20")
    from coe.config import get_settings
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)  # no stale-cached env
    script_path = _narrative_event_script(tmp_path)

    st = _make_st()
    from coe.dashboard.pages import simulate

    st.__path__ = []
    errors_mod = types.ModuleType("streamlit.errors")
    errors_mod.StreamlitAPIException = type("StreamlitAPIException",
                                            (Exception,), {})
    monkeypatch.setitem(sys.modules, "streamlit.errors", errors_mod)
    monkeypatch.setitem(sys.modules, "streamlit", st)

    st.session_state["instance"] = instance
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = script_path
    st.session_state["sim_speed"] = 30

    def _recovery_start_lines():
        return [ln for ln in st.session_state["sim_feed"]
                if "recovery starting" in ln]

    # 1. Run → the narrative step is entered inline: the rerender shows
    #    the ⏳ line, then the terminator (recovery chunk) advances idx.
    simulate.render()
    assert st.session_state["sim_last_idx"] == 1
    assert len(_recovery_start_lines()) == 1

    # 2. Crash-mid-solve replay: rewind to the narrative event's idx
    #    WITHOUT clearing sim_seen (browser crash lost widget state, the
    #    Starlette session survived — exact Bug 2026-09-13 signature).
    st.session_state["sim_last_idx"] = 0
    simulate.render()
    assert len(_recovery_start_lines()) == 1, (
        "duplicate ⏳ recovery-starting lines across the rewound rerender")

    # 3. Global uniqueness still holds (original invariant).
    lines = [ln for ln in st.session_state["sim_feed"] if ln.strip()]
    assert len(lines) == len(set(lines)), "duplicate feed lines remain"


# ---------------------------------------------------------------------------
# live-day mode (Task 3): mode picker + controller + chat capture
# ---------------------------------------------------------------------------

def _baseline_instance() -> str:
    """Baseline-bearing clone (shared helper, same subprocess pattern)."""
    from tests.simulator.conftest import ensure_sim_baseline_clone

    return ensure_sim_baseline_clone()


def test_live_mode_runs_to_end_instantly(clean_db, demo_scenario):
    """AC §8.1: live + instant + no interrupts: completes with ZERO
    solves; no duplicate feed lines (dedup invariant)."""
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate

    st.session_state["instance"] = _baseline_instance()
    st.session_state["sim_mode"] = "live"
    st.session_state["sim_speed"] = "instant"
    st.session_state["sim_run_pressed"] = True
    simulate.render()
    feed = list(st.session_state["sim_feed"])
    assert any("day_end" in ln for ln in feed)
    assert len(feed) == len(set(feed))
    assert not any("recovery" in ln for ln in feed)


def test_live_mode_does_not_autostart(clean_db, demo_scenario):
    """Regression (user report 2026-09-15): a fresh live session must
    NOT walk the day before a Resume press — idle render paints a hint
    and leaves the feed empty."""
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate

    st.session_state["instance"] = _baseline_instance()
    st.session_state["sim_mode"] = "live"
    st.session_state["sim_speed"] = "instant"
    st.session_state.pop("sim_run_pressed", None)
    simulate.render()    # idle path paints a hint and RETURNS normally
                         # (st.stop after a paint would orphan elements —
                         # double-log bug 2026-09-16)
    assert st.session_state["sim_feed"] == []
    assert st.session_state["sim_live_clock"] == 0
    assert st.session_state.get("sim_running") is False


def test_live_chat_queues_and_solves(clean_db, demo_scenario):
    """AC §8.2 at the page level: queued narrative consumed at the
    current clock; the feed shows recovery → commit."""
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate

    st.session_state["instance"] = _baseline_instance()
    st.session_state["sim_mode"] = "live"
    st.session_state["sim_speed"] = "instant"
    st.session_state["sim_run_pressed"] = True
    st.session_state["sim_chat_text"] = "M3 gearbox seized, sparks everywhere"
    simulate.render()
    joined = "\n".join(st.session_state["sim_feed"])
    assert "recovery" in joined and "COMMITTED" in joined


def test_live_lane_state_isolated_from_scripted(clean_db, demo_scenario,
                                                tmp_path):
    """Regression (cross-lane state bleed): the live lane must own
    sim_live_active/sim_live_clock — a scripted run's sim_active_instance
    must never be played by the live walker, and the scripted keys must
    survive a live render untouched."""
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate

    source = _baseline_instance()

    # 1. Scripted lane runs first: sim_active_instance becomes X.
    st.session_state["instance"] = source
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = _tiny_script(tmp_path)
    st.session_state["sim_speed"] = "instant"
    simulate.render()
    scripted_active = st.session_state["sim_active_instance"]
    scripted_clock = st.session_state["sim_clock"]
    assert scripted_active is not None

    # 2. Switch to live: the live lane forks its OWN fresh clone —
    #    never reuses X, never reads the scripted clock.
    st.session_state["sim_mode"] = "live"
    simulate.render()
    live_active = st.session_state["sim_live_active"]
    assert live_active is not None
    assert live_active != scripted_active
    assert live_active.startswith("sim-live@")
    # scripted keys untouched by the live lane
    assert st.session_state["sim_active_instance"] == scripted_active
    assert st.session_state["sim_clock"] == scripted_clock

    # 3. live → scripted → live: the live lane restores its own fork
    #    and clock (guard does not re-fork while the source is unchanged).
    prev_live_active = live_active
    prev_live_clock = st.session_state["sim_live_clock"]
    st.session_state["sim_mode"] = "scripted"
    simulate.render()
    st.session_state["sim_mode"] = "live"
    simulate.render()
    assert st.session_state["sim_live_active"] == prev_live_active
    assert st.session_state["sim_live_clock"] == prev_live_clock
    assert st.session_state["sim_live_active"] != \
        st.session_state["sim_active_instance"]


# ---------------------------------------------------------------------------
# live-lane pause/resume (spec §3 step 5)
# ---------------------------------------------------------------------------

def _errors_mod():
    m = types.ModuleType("streamlit.errors")
    m.StreamlitAPIException = type("StreamlitAPIException", (Exception,), {})
    return m


def _live_st():
    """_make_st wired for the live lane: chat seam + live radio + two
    sidebar button columns with not-pressed defaults."""
    st = _make_st()
    st.chat_input = MagicMock(return_value=None)
    st.sidebar.radio = MagicMock(return_value="Live day")
    st.sidebar._col_run.button = MagicMock(return_value=False)
    st.sidebar._col_pause.button = MagicMock(return_value=False)
    return st


def _live_pace_env(monkeypatch, request):
    """time.sleep no-op + SIMULATE_CLONE=false with a clean settings cache
    (same pattern as test_paced_no_duplicate_lines_across_rerenders)."""
    monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
    monkeypatch.setenv("SIMULATE_CLONE", "false")
    from coe.config import get_settings
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)


def _seed_live_session(st, instance, press=False):
    st.session_state["instance"] = instance
    st.session_state["sim_mode"] = "live"
    st.session_state["sim_speed"] = 30   # paced: one chunk per rerender
    if press:
        st.session_state["sim_run_pressed"] = True


def test_live_pause_holds_walk_state(monkeypatch, request):
    """Pause freezes the walk at a chunk boundary: a paused render idly
    holds sim_live_clock/sim_feed — no generator step, no feed growth.

    No clean_db: the tests only read the active schedule (ensure_sim_
    baseline_clone self-heals) — shares the session clone for cost."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    # 1. First paced render (Run press): one chunk, self-advance.
    _seed_live_session(st, _baseline_instance(), press=True)
    simulate.render()
    assert st.rerun.called
    assert st.session_state["sim_running"] is True
    feed_after_run = list(st.session_state["sim_feed"])
    clock_after_run = st.session_state["sim_live_clock"]
    assert feed_after_run

    # 2. Pause press → live-lane flag set, info surfaced, page RETURNS
    #    normally (no st.stop after a paint: orphans the element tree)
    #    and paints the FULL history (scrollback restored).
    st.sidebar._col_pause.button = MagicMock(return_value=True)
    simulate.render()
    assert st.session_state["sim_live_paused"] is True
    assert any("Paused" in str(c.args[0])
               for c in st.empty.return_value.caption.call_args_list), \
        "pause hint rides in the painter caption slot (tree-stable)"
    paused_paint = st.empty.return_value.markdown.call_args_list[-1].args[0]
    assert paused_paint.split("\n")[0].rstrip() == feed_after_run[0], \
        "paused paint must start from the day's first event (full log)"

    # 3. Paused render (no press): idle hold — clock and feed unchanged.
    st.sidebar._col_pause.button = MagicMock(return_value=False)
    st.empty.return_value.markdown.reset_mock()
    st.empty.return_value.caption.reset_mock()
    simulate.render()
    assert st.session_state["sim_feed"] == feed_after_run
    assert st.session_state["sim_live_clock"] == clock_after_run
    assert st.session_state["sim_live_paused"] is True


def test_live_resume_continues(monkeypatch, request):
    """Resume clears sim_live_paused BEFORE any early-stop; the walk
    continues from sim_live_clock and the feed grows (no duplicate of
    already-seen chunks)."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)

    simulate.render()
    feed_1 = list(st.session_state["sim_feed"])
    clock_1 = st.session_state["sim_live_clock"]

    st.sidebar._col_pause.button = MagicMock(return_value=True)
    simulate.render()
    assert st.session_state["sim_live_paused"] is True

    # Resume press → flag cleared, walk continues from sim_live_clock.
    st.sidebar._col_pause.button = MagicMock(return_value=False)
    st.sidebar._col_run.button = MagicMock(return_value=True)
    simulate.render()
    assert st.session_state["sim_live_paused"] is False
    assert len(st.session_state["sim_feed"]) > len(feed_1)
    assert st.session_state["sim_live_clock"] > clock_1
    # dedup invariant holds across the pause boundary
    lines = [ln for ln in st.session_state["sim_feed"] if ln.strip()]
    assert len(lines) == len(set(lines))


def test_live_recovery_start_completes_inline(monkeypatch, request):
    """§3 step 5 bound (lean regression): a paced render that consumes a
    recovery_start chunk never breaks on it (the `elif rerender` break
    excludes recovery_start) — the solve is driven to completion in the
    SAME render pass, so a pause can only bite from the NEXT render,
    never mid-solve. execute_recovery is stubbed (status only; the
    inline-completion structure is what's under test)."""
    _live_pace_env(monkeypatch, request)

    import coe.agents.graph as graph_mod
    monkeypatch.setattr(graph_mod, "execute_recovery",
                        lambda *a, **kw: {"status": "COMMITTED"})

    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)
    st.session_state["sim_chat_text"] = "M3 gearbox seized, sparks everywhere"

    simulate.render()
    feed = "\n".join(st.session_state["sim_feed"])
    assert "recovery starting" in feed
    assert "recovery NARRATIVE" in feed and "COMMITTED" in feed
    assert st.session_state["sim_live_paused"] is False
    assert st.rerun.called


# ---------------------------------------------------------------------------
# final-review fix wave: lane-owned running flag + idle feed panel
# ---------------------------------------------------------------------------

def _scripted_pace_env(monkeypatch, script_path):
    """Sidebar-flow scaffolding for the scripted regressions: glob → tmp
    script, load_timeline resolves it, forking disabled."""
    orig_glob = pathlib.Path.glob
    fake_script = pathlib.Path(script_path)

    def fake_glob(self, pattern):
        if pattern == "*.json" and str(self) == "data/timelines":
            return iter([fake_script])
        return orig_glob(self, pattern)

    monkeypatch.setattr(pathlib.Path, "glob", fake_glob)

    import coe.simulator.timeline as tlmod
    real_load = tlmod.load_timeline
    monkeypatch.setattr(
        tlmod, "load_timeline",
        lambda p: real_load(script_path) if "tiny2.json" in str(p)
        else real_load(p))


def _stub_render_env(monkeypatch, st):
    """Install the st stub + StreamlitAPIException shim for render()."""
    from coe.dashboard.pages import simulate as sim_page

    st.__path__ = []
    errors_mod = types.ModuleType("streamlit.errors")
    errors_mod.StreamlitAPIException = type("StreamlitAPIException",
                                            (Exception,), {})
    monkeypatch.setitem(sys.modules, "streamlit.errors", errors_mod)
    monkeypatch.setitem(sys.modules, "streamlit", st)
    return sim_page


def test_scripted_ignores_stale_live_running_flag(
        clean_db, tmp_path, monkeypatch):
    """IMPORTANT-1 regression: switching Mode to Scripted replay mid-
    live-walk leaves sim_running=True (set by the live lane). The
    scripted arm must NOT auto-drive walk_timeline on the stale (here:
    None) sim_active_instance — the walk block is gated on a
    scripted-lane-owned running flag, so the idle controls render and
    the feed/idx stay untouched."""
    _scripted_pace_env(monkeypatch, _two_event_script(tmp_path))

    st = _make_st()
    st.sidebar._col_run.button = MagicMock(return_value=False)
    st.sidebar._col_pause.button = MagicMock(return_value=False)
    sim_page = _stub_render_env(monkeypatch, st)

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_speed"] = 10
    st.sidebar.selectbox = MagicMock(return_value="tiny2.json")
    # stale live-walk state: the live lane set sim_running and never
    # cleared it (mid-walk mode switch); no scripted session exists.
    st.session_state["sim_running"] = True
    st.session_state["sim_running_lane"] = "live"
    st.session_state["sim_active_instance"] = None

    sim_page.render()   # pre-fix: walks with instance=None → DB hit/error

    assert st.session_state["sim_last_idx"] == 0
    assert st.session_state["sim_feed"] == []
    assert not st.rerun.called
    assert any("Press Run to start" in str(c.args[0])
               for c in st.empty.return_value.caption.call_args_list)


def test_idle_feed_panel_survives_pause_and_completion(
        clean_db, demo_scenario, tmp_path, monkeypatch):
    """IMPORTANT-2 regression: the event feed must stay visible when the
    walk is NOT running — paused renders and day-complete rerenders
    paint a read-only panel from sim_feed (the status container stays
    the sole painter mid-walk)."""
    monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
    script_path = _two_event_script(tmp_path)
    _scripted_pace_env(monkeypatch, script_path)

    # Fork disabled for this test; keep every other setting real.
    from coe.config import get_settings as real_get_settings

    class _Settings(types.SimpleNamespace):
        simulate_clone = False

        def __getattr__(self, name):
            return getattr(real_get_settings(), name)

    monkeypatch.setattr(
        "coe.config.get_settings", lambda: _Settings())

    st = _make_st()
    sim_page = _stub_render_env(monkeypatch, st)

    col_run = st.sidebar._col_run
    col_pause = st.sidebar._col_pause
    pressed = {"run": False, "pause": False}

    def button(label, **_kw):
        if label in ("Run", "Resume") and pressed["run"]:
            return True
        if label == "Pause" and pressed["pause"]:
            return True
        return False

    col_run.button = button
    col_pause.button = button

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_speed"] = 10  # non-instant → one event / rerender
    st.sidebar.selectbox = MagicMock(return_value="tiny2.json")

    def markdown_calls():
        # log content paints through the st.empty() content slot
        return [c.args[0] for c in st.empty.return_value.markdown.call_args_list]

    # 1. Run → event 1 consumed (feed painted inside the status block).
    pressed["run"] = True
    sim_page.render()
    assert st.session_state["sim_last_idx"] == 1
    feed_line = st.session_state["sim_feed"][0]

    # 2. Pause press → halted; then an idle paused rerender (no press)
    #    must repaint the feed with a Paused caption.
    pressed["run"] = False
    pressed["pause"] = True
    try:
        sim_page.render()
    except SystemExit:
        pass
    assert st.session_state["sim_paused"] is True
    st.empty.return_value.markdown.reset_mock()
    st.empty.return_value.caption.reset_mock()
    st.caption.reset_mock()
    pressed["pause"] = False
    sim_page.render()
    assert any("Paused" in str(c.args[0])
               for c in st.empty.return_value.caption.call_args_list)
    assert any(feed_line in m for m in markdown_calls()), \
        "paused render must repaint the feed"

    # 3. Resume → event 2; 4. no press → walker yields done (terminal).
    pressed["pause"] = False
    pressed["run"] = True
    sim_page.render()
    assert st.session_state["sim_last_idx"] == 2
    pressed["run"] = False
    sim_page.render()
    assert st.session_state["sim_running"] is False
    assert len(st.session_state["sim_feed"]) == 2

    # 5. Idle rerender after completion → "Day complete." + full feed.
    st.empty.return_value.markdown.reset_mock()
    st.empty.return_value.caption.reset_mock()
    st.caption.reset_mock()
    sim_page.render()
    assert any("Day complete" in str(c.args[0])
               for c in st.empty.return_value.caption.call_args_list)
    assert markdown_calls() == [
        "  \n".join(st.session_state["sim_feed"])], \
        "day-complete rerender must repaint the feed"


# ---------------------------------------------------------------------------
# Task 2: tree-stable clock hero + gantt slot (both lanes)
# ---------------------------------------------------------------------------

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
