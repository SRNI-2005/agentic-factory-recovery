# tests/dashboard/test_simulate_page.py
"""AppTest smoke: Simulate page renders and completes an instant walk."""
import json
import sys

import pytest

pytestmark = pytest.mark.db


def _fresh_streamlit():
    """Import real streamlit cleanly.

    Other tests in the suite swap streamlit for stubs or run AppTest in
    worker threads; stale submodules + the lingering DeltaGeneratorSingleton
    make a plain `import streamlit` raise 'instance already exists!'.
    """
    for name in [m for m in list(sys.modules)
                 if m == "streamlit" or m.endswith("st_stub")]:
        del sys.modules[name]
    from streamlit.delta_generator_singletons import DeltaGeneratorSingleton

    DeltaGeneratorSingleton._instance = None
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
