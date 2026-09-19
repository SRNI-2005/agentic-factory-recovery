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
import re

# Unified log schema across BOTH lanes (spec §10 / A3): every log line
# is "[t=…<4>] done=N running=M · <detail>" — state numbers from the
# SAME committed-entry classification the board uses.
_UNIFIED_SCHEMA = re.compile(
    r"\[t=\s*\d+\] done=\d+ running=\d+(?:\s·.*)?$")
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
    """Scripted-lane smoke: the lane bootstraps its walk state and stays
    idle (no walk without a Run press — same no-autostart rule as live,
    spec A3 §11)."""
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate as sim_page

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = _tiny_script(tmp_path)
    st.session_state["sim_speed"] = "instant"
    sim_page.render()
    # lane bootstrapped; idle (no walk)
    assert st.session_state["sim_scripted_active"] is not None
    assert st.session_state.get("sim_scripted_clock", 0) == 0
    assert st.session_state["sim_running"] is False


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
    feed = list(st.session_state["sim_feed_live"])
    assert len(feed) > 10, "baseline day must exceed the cap for the tail"

    simulate._paint_idle_feed("live", None)
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
        assert st.session_state["sim_scripted_clock"] == 0
    finally:
        sys.modules.pop("streamlit", None)
        sys.modules.pop("streamlit.errors", None)



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
    feed = list(st.session_state["sim_feed_live"])
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
    assert st.session_state["sim_feed_live"] == []
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
    joined = "\n".join(st.session_state["sim_feed_live"])
    assert "recovery" in joined and "COMMITTED" in joined


def test_live_lane_state_isolated_from_scripted(clean_db, demo_scenario,
                                                tmp_path):
    """Regression (cross-lane state bleed): the live lane must own
    sim_live_active/sim_live_clock and the scripted lane's
    sim_scripted_* — switching modes never shares a clock or a fork."""
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate

    source = _baseline_instance()

    # 1. Scripted lane bootstraps its OWN fork.
    st.session_state["instance"] = source
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = _tiny_script(tmp_path)
    st.session_state["sim_speed"] = "instant"
    simulate.render()
    scripted_active = st.session_state["sim_scripted_active"]
    scripted_clock = st.session_state["sim_scripted_clock"]
    assert scripted_active is not None

    # 2. Switch to live: the live lane forks its OWN fresh clone —
    #    never reuses the scripted fork.
    st.session_state["sim_mode"] = "live"
    simulate.render()
    live_active = st.session_state["sim_live_active"]
    assert live_active is not None
    assert live_active != scripted_active
    assert live_active.startswith("sim-live@")

    # 3. live → scripted → live: each lane restores its own keys.
    prev_live_active = live_active
    prev_live_clock = st.session_state["sim_live_clock"]
    st.session_state["sim_mode"] = "scripted"
    simulate.render()
    st.session_state["sim_mode"] = "live"
    simulate.render()
    assert st.session_state["sim_live_active"] == prev_live_active
    assert st.session_state["sim_live_clock"] == prev_live_clock
    assert st.session_state["sim_scripted_active"] == scripted_active


def test_recovery_start_paints_before_inline_solve(
        clean_db, demo_scenario, tmp_path, monkeypatch, request):
    """Bug 2026-09-18 (user report): a render pass that consumes a
    recovery_start chunk appended the ⏳ line but painted NOTHING until
    the walk consumed its terminator — the inline solve (minutes, 180s
    floor) ran against a frozen t=0 with no feedback. execute_recovery
    is stubbed to RECORD the log markdown already painted at call time:
    the ⏳ line must be visible the moment the solve BEGINS."""
    instance = _baseline_instance()   # BEFORE the env squeeze: the
    # baseline CLI subprocess must solve with the full default budget;
    # the narrative pre-flight (§4.4) needs an active schedule anyway.
    monkeypatch.setenv("SIMULATE_CLONE", "false")
    from coe.config import get_settings
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)  # no stale-cached env

    p = tmp_path / "tiny2.json"
    p.write_text(json.dumps({
        "name": "tiny2", "seed": 1, "horizon_days": 1,
        "auto_recover": True,
        "events": [
            {"t": 100, "kind": "NARRATIVE",
             "text": "M3 gearbox seized, sparks everywhere",
             "severity": "HIGH"},
        ]}))

    st = _make_st()
    import coe.agents.graph as graph_mod

    def _stub_recovery(*a, **kw):
        # record what the log surface showed when the solve BEGAN
        st._md_at_solve = [
            c.args[0] for c in
            st.empty.return_value.markdown.call_args_list]
        return {"status": "COMMITTED",
                "state": types.SimpleNamespace(committed_version_id=None)}

    monkeypatch.setattr(graph_mod, "execute_recovery", _stub_recovery)

    from coe.dashboard.pages import simulate
    sim_page = _stub_render_env(monkeypatch, st)
    # A3 sidebar wiring: Run pressed, Pause not (stub buttons must not
    # be truthy Magics or a paused render eats the walk).
    st.sidebar._col_run.button = MagicMock(return_value=True)
    st.sidebar._col_pause.button = MagicMock(return_value=False)

    st.session_state["instance"] = instance
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = str(p)
    st.session_state["sim_speed"] = "instant"
    # A3: the scripted lane no longer auto-runs — a Run press starts the
    # scheduled day (same no-autostart rule as the live lane).
    st.session_state["sim_run_pressed"] = True

    sim_page.render()

    feed = "\n".join(st.session_state["sim_feed_scripted"])
    assert "recovery starting" in feed
    assert "recovery NARRATIVE" in feed and "✓" in feed
    assert any("recovery starting" in m for m in st._md_at_solve), (
        "the ⏳ recovery-starting line must be painted BEFORE the inline "
        "solve runs (pre-fix the log only painted after the terminator)")

    # Final-review Fix 2 (AC 8), now via the shared walker: the scripted
    # day ends AT THE BOARD HORIZON — `sim_scripted_clock` carries the
    # day-end clock (rail-parity), never the last ingest's t.
    makespan = max(int(e["end_time"]) for e in
                   sim_page._fetch_active_entries(
                       st.session_state["sim_scripted_active"]))
    assert makespan > 100, "baseline day must extend past the last ingest"
    assert st.session_state["sim_scripted_clock"] >= makespan - 1, (
        "scripted day must end at the true board horizon, not the last "
        "event's clock")


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
    feed_after_run = list(st.session_state["sim_feed_live"])
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
    assert st.session_state["sim_feed_live"] == feed_after_run
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
    feed_1 = list(st.session_state["sim_feed_live"])
    clock_1 = st.session_state["sim_live_clock"]

    st.sidebar._col_pause.button = MagicMock(return_value=True)
    simulate.render()
    assert st.session_state["sim_live_paused"] is True

    # Resume press → flag cleared, walk continues from sim_live_clock.
    st.sidebar._col_pause.button = MagicMock(return_value=False)
    st.sidebar._col_run.button = MagicMock(return_value=True)
    simulate.render()
    assert st.session_state["sim_live_paused"] is False
    assert len(st.session_state["sim_feed_live"]) > len(feed_1)
    assert st.session_state["sim_live_clock"] > clock_1
    # dedup invariant holds across the pause boundary
    lines = [ln for ln in st.session_state["sim_feed_live"] if ln.strip()]
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
    feed = "\n".join(st.session_state["sim_feed_live"])
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
        lambda p: (
            real_load(script_path) if pathlib.Path(str(p)).name
            == fake_script.name else real_load(p)))


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


def test_lane_feeds_are_scoped_and_survive_switches(
        clean_db, demo_scenario, tmp_path, monkeypatch, request):
    """Bug 2026-09-18 (user report): live and scripted lanes SHARED one
    sim_feed list — switching modes showed the other lane's events and
    hid your own. Feeds must be lane-scoped: sim_feed_live vs
    sim_feed_scripted, each surviving mode switches."""
    _live_pace_env(monkeypatch, request)
    script_path = _two_event_script(tmp_path)

    st = _make_st()
    sim_page = _stub_render_env(monkeypatch, st)
    # the radio must follow the staged sim_mode (mode-switch seam)
    st.sidebar.radio = MagicMock(side_effect=lambda _label, _opts, **kw:
                                 "Live day"
                                 if st.session_state.get("sim_mode") == "live"
                                 else "Scripted replay")
    st.sidebar.selectbox = MagicMock(return_value=30)   # pace law 30x
    st.chat_input = MagicMock(return_value=None)
    st.sidebar._col_run.button = MagicMock(return_value=False)
    st.sidebar._col_pause.button = MagicMock(return_value=False)

    # 1. Scripted lane (manual_entry): run the tiny script under A3 —
    #    a Run press starts the scheduled-interrupt day. The scripted
    #    events fire as narrations through the stubbed recovery graph
    #    (lane-feed scoping, not solving, is what this test binds). The
    #    instance needs an active schedule: the shared walker plays a
    #    committed board (A3 contract; factory_demo_01 alone has none).
    st.session_state["instance"] = _baseline_instance()
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = script_path
    st.session_state["sim_speed"] = "instant"
    import coe.agents.graph as graph_mod
    monkeypatch.setattr(graph_mod, "execute_recovery",
                        lambda *a, **kw: {"status": "COMMITTED",
                                          "state": types.SimpleNamespace(
                                              committed_version_id=None)})
    st.sidebar._col_run.button = MagicMock(return_value=True)   # Run press
    sim_page.render()
    scripted_feed = list(st.session_state["sim_feed_scripted"])
    assert scripted_feed
    assert all("[t=" in ln for ln in scripted_feed)

    # 2. Switch to live — idle lane (no run press): its log must NOT
    #    show the scripted lines and its feed stays empty, while the
    #    scripted feed is retained untouched.
    st.empty.return_value.markdown.reset_mock()
    st.sidebar._col_run.button = MagicMock(return_value=False)  # no press
    st.session_state["sim_mode"] = "live"
    sim_page.render()
    assert st.session_state["sim_feed_live"] == []
    assert st.session_state["sim_feed_scripted"] == scripted_feed
    md_calls = [c.args[0]
                for c in st.empty.return_value.markdown.call_args_list]
    assert not any("[t=" in m for m in md_calls), (
        "live lane's log must not show the scripted lane's events")

    # 3. Switch back to scripted: the scripted log repaints its lines.
    st.empty.return_value.markdown.reset_mock()
    st.session_state["sim_mode"] = "scripted"
    sim_page.render()
    md_calls = [c.args[0]
                for c in st.empty.return_value.markdown.call_args_list]
    assert any("[t=" in m for m in md_calls), (
        "scripted lane's log must repaint its own lines after the "
        "round-trip")
    assert all(ln in "\n".join(md_calls) for ln in scripted_feed)




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
    # clock hero metric painted (same hero both lanes — spec A3 §11.3)
    assert any("Day clock" in str(c.args[0])
               for c in st.empty.return_value.metric.call_args_list)
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


# ---------------------------------------------------------------------------
# Task 3: option A — final board persists at day end (+ diff below)
# ---------------------------------------------------------------------------

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
    assert st.session_state["sim_live_complete"] is True
    # board painted (final figure at the SAME stable path — no removal
    # needed) alongside the diff
    assert st.plotly_chart.called
    # and _render_diff's own chart call happened
    assert st.plotly_chart.call_count >= 1
    # the DAY-END pass repaints the board itself (pre-walk paint + a
    # terminal refresh from the final active version)
    assert st.plotly_chart.call_count >= 2

    # Task 4: the transition section is a PAIR — initial baseline frame
    # painted FIRST, final frame SECOND, each with its caption.
    assert st.plotly_chart.call_count >= 3
    captions = [str(c.args[0]) for c in st.caption.call_args_list
                if c.args]
    assert "Initial (baseline)" in captions
    assert "Final (after the last recovery)" in captions

    # completed-state rerender: the board must STILL be painted above
    # the full log (the early return used to skip the board entirely —
    # on a completed rerender the board vanished)
    st.plotly_chart.reset_mock()
    simulate.render()
    assert st.plotly_chart.called


def test_live_day_end_diff_below_final_board(
        clean_db, demo_scenario, monkeypatch, request):
    """Spec (live-day diff, 2026-09-18 review): at live-day end the
    before→final diff renders below the final board, just like the
    scripted lane."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)
    _seed_live_session(st, _baseline_instance(), press=True)
    st.session_state["sim_speed"] = "instant"

    simulate.render()   # runs whole day end-to-end, instant
    assert st.session_state["sim_live_complete"] is True
    # before-capture captured at lane entry (fork of committed baseline)
    assert st.session_state["sim_live_before_entries"]
    # terminal pass: board repaint + the diff chart (end-to-end
    # _render_diff, no monkeypatch of the seam)
    assert st.plotly_chart.call_count >= 2






def test_unified_log_schema_both_lanes(clean_db, demo_scenario,
                                        tmp_path, monkeypatch, request):
    """Spec §10 log unification: live tick lines and scripted detail
    lines both carry 'done=N running=M · <detail>', computed by the SAME
    entry classification the board uses (classify over
    _fetch_active_entries, read-only)."""
    _live_pace_env(monkeypatch, request)
    st = _live_st()
    from coe.dashboard.pages import simulate

    monkeypatch.setitem(sys.modules, "streamlit.errors", _errors_mod())
    monkeypatch.setitem(sys.modules, "streamlit", st)

    # --- live lane: whole instant day -------------------------------------
    _seed_live_session(st, _baseline_instance(), press=True)
    st.session_state["sim_speed"] = "instant"

    simulate.render()
    feed = st.session_state["sim_feed_live"]
    lines = [ln for ln in feed if ln.strip()]
    assert lines, "live day must log something"
    for ln in lines:
        assert _UNIFIED_SCHEMA.match(ln), \
            f"live line lacks state numbers: {ln!r}"
    # tick lines drop the word "tick" — the state numbers ARE the detail
    assert not any("tick" in ln for ln in lines)

    # --- scripted lane: instant terminal pass with a recovery -------------
    # NARRATIVE event (auto_recover=False): the recovery_start line and
    # the COMMITTED recovery line must carry state numbers too.
    import coe.agents.graph as graph_mod

    monkeypatch.setattr(
        graph_mod, "execute_recovery",
        lambda *a, **kw: {"status": "COMMITTED",
                          "state": types.SimpleNamespace(
                              committed_version_id=None)})
    script = tmp_path / "unified.json"
    script.write_text(json.dumps({
        "name": "unified", "seed": 1, "horizon_days": 1,
        "auto_recover": False,
        "events": [{"t": 100, "kind": "NARRATIVE",
                    "text": "M3 gearbox seized, sparks everywhere",
                    "severity": "HIGH"}]}))
    st.sidebar.radio = MagicMock(return_value="Scripted replay")
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = str(script)
    st.session_state["sim_speed"] = "instant"
    # A3: a Run press starts the scheduled day (no auto-run);
    # the scripted lane's day runs the shared walker end-to-end.
    st.session_state["sim_run_pressed"] = True

    simulate.render()
    assert st.session_state["sim_scripted_complete"] is True
    scripted_lines = [ln for ln in st.session_state["sim_feed_scripted"]
                      if ln.strip()]
    assert scripted_lines
    joined = "\n".join(scripted_lines)
    # NARRATIVE steps log the recovery pair only (no structured ingest)
    assert "recovery starting" in joined
    assert "recovery NARRATIVE" in joined and "✓" in joined
    for ln in scripted_lines:
        assert _UNIFIED_SCHEMA.match(ln), \
            f"scripted line lacks state numbers: {ln!r}"


# ---------------------------------------------------------------------------
# spec A3 §11: scripted replay IS the live walker (ScheduledInterruptQueue)
# ---------------------------------------------------------------------------

def test_scheduled_queue_fires_by_minute():
    """Unit: ScheduledInterruptQueue reuses InterruptQueue's contract —
    authored events pop in authored order once the walk minute reaches
    them, and stay queued before that."""
    from coe.simulator.live import ScheduledInterruptQueue

    q = ScheduledInterruptQueue([(90, "Machine M3 failed"),
                                 (300, "Machine M3 failed again")])
    assert q.pop(28) is None          # before the first authored minute
    assert q.pop(90) == "Machine M3 failed"       # due at its minute
    assert q.pop(120) is None         # nothing due until 300
    assert q.pop(999) == "Machine M3 failed again"
    assert q.remaining == 0
    assert q.pop(999) is None         # quiet after the schedule runs out

def test_scripted_lane_reuses_live_walker(
        clean_db, demo_scenario, tmp_path, monkeypatch, request):
    """Spec A3 §11: the scripted page lane is `_render_live` over
    `live_day` with scheduled interrupts — the shared walker resolves
    authored events at their minutes, the day terminates at the board
    horizon (folded from the review-fix AC 8), and the lane's private
    keys (`sim_scripted_*`) never touch the live lane's."""
    sim_page = (lambda: __import__("coe.dashboard.pages.simulate",
                                   fromlist=["x"]))
    sim_page = sim_page()
    st = _make_st()
    sim_page = _stub_render_env(monkeypatch, st)

    import coe.agents.graph as graph_mod
    monkeypatch.setattr(graph_mod, "execute_recovery",
                        lambda *a, **kw: {"status": "COMMITTED",
                                          "state": types.SimpleNamespace(
                                              committed_version_id=None)})
    from coe.config import get_settings
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)

    # A3 sidebar wiring: the stub buttons must not evaluate truthy —
    # an unwired col_pause.button MagicMock reads "pressed" and eats
    # the walk through the paused path (same probe as the other lanes).
    st.sidebar._col_run.button = MagicMock(return_value=False)
    st.sidebar._col_pause.button = MagicMock(return_value=False)

    st.session_state["instance"] = _baseline_instance()  # committed board
    st.session_state["sim_mode"] = "scripted"
    st.session_state["sim_script"] = _tiny_script(tmp_path)
    st.session_state["sim_speed"] = "instant"
    st.session_state["sim_run_pressed"] = True

    sim_page.render()

    # the scripted lane ran the live walker end-to-end: scheduled event
    # resolved, day terminated at the board horizon, lane keys clean.
    assert st.session_state["sim_scripted_complete"] is True
    assert st.session_state["sim_scripted_clock"] >= 100
    feed = "\n".join(st.session_state["sim_feed_scripted"])
    assert "recovery" in feed and "COMMITTED" in feed
    # and the queue drained: the clock ended at the walker's terminus
    q = st.session_state["sim_scripted_interrupt_q"]
    assert q.remaining == 0
