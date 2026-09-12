# tests/dashboard/test_simulate_page.py
"""AppTest smoke + paced-browser rerender sequence for the Simulate page."""
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
    p.write_text(json.dumps({
        "name": "tiny", "seed": 1, "horizon_days": 1,
        "events": [{"t": 100, "kind": "MACHINE", "event_type": "FAILURE",
                    "machine_id": "M3"}]}))
    return str(p)


def test_page_smoke(clean_db, demo_scenario, tmp_path):
    st = _fresh_streamlit()

    from coe.dashboard.pages import simulate as sim_page

    st.session_state["instance"] = "factory_demo_01"
    st.session_state["sim_script"] = _tiny_script(tmp_path)
    st.session_state["sim_speed"] = "instant"
    # direct-render convention used by tests/dashboard/test_cockpit_page.py
    sim_page.render()
    assert st.session_state.get("sim_last_idx", 0) >= 1


# ---------------------------------------------------------------------------
# paced-browser rerender sequence: Run → Pause → Resume → Terminal
# ---------------------------------------------------------------------------

def _two_event_script(tmp_path):
    p = tmp_path / "tiny2.json"
    p.write_text(json.dumps({
        "name": "tiny2", "seed": 1, "horizon_days": 1,
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
    col_run = MagicMock()
    col_pause = MagicMock()
    st.sidebar = types.SimpleNamespace(
        selectbox=MagicMock(return_value=None),
        columns=MagicMock(return_value=[col_run, col_pause]),
        _col_run=col_run, _col_pause=col_pause)
    st.status = MagicMock(return_value=MagicMock(
        __enter__=lambda s: s,
        __exit__=MagicMock(),
        update=MagicMock()))
    st.empty = MagicMock(return_value=MagicMock(markdown=MagicMock()))
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
