"""Tests for coe.dashboard.pages.cockpit — focused stub-based checks."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_st():
    """Build a minimal Streamlit stub capturing calls."""
    st = types.ModuleType("streamlit")
    st.session_state = {}
    st.warning = MagicMock()
    st.error = MagicMock()
    st.info = MagicMock()
    st.stop = MagicMock(side_effect=SystemExit)
    st.markdown = MagicMock()
    st.chat_message = MagicMock(return_value=MagicMock(
        __enter__=lambda s: s, __exit__=MagicMock()))
    st.chat_input = MagicMock(return_value=None)
    st.status = MagicMock(return_value=MagicMock(
        __enter__=lambda s: s,
        __exit__=MagicMock(),
        update=MagicMock()))
    st.empty = MagicMock(return_value=MagicMock(markdown=MagicMock()))
    st.columns = MagicMock(return_value=[MagicMock() for _ in range(3)])
    st.metric = MagicMock()
    st.subheader = MagicMock()
    return st


def _install_st(st):
    sys.modules["streamlit"] = st
    return st


def _uninstall_st():
    sys.modules.pop("streamlit", None)


# ---------------------------------------------------------------------------
# import smoke
# ---------------------------------------------------------------------------

def test_cockpit_page_imports():
    import coe.dashboard.pages.cockpit as mod
    assert hasattr(mod, "render")


# ---------------------------------------------------------------------------
# render — no instance selected
# ---------------------------------------------------------------------------

def test_render_no_instance():
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {}
    try:
        with pytest.raises(SystemExit):
            render()
        st.warning.assert_called_once()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — LLM not configured
# ---------------------------------------------------------------------------

def test_render_no_llm_config():
    from coe.dashboard.pages.cockpit import render
    from coe.agents.llm_client import LLMConfigError

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    try:
        with patch("coe.agents.llm_client.require_llm_config",
                   side_effect=LLMConfigError("no key")), \
             patch("coe.config.get_settings"):
            with pytest.raises(SystemExit):
                render()
        st.warning.assert_called_once()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — idle (no chat input)
# ---------------------------------------------------------------------------

def test_render_idle_no_input():
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    st.chat_input = MagicMock(return_value=None)
    try:
        with patch("coe.agents.llm_client.require_llm_config"), \
             patch("coe.config.get_settings"):
            render()
        st.status.assert_not_called()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — user submits prompt, recovery COMMITTED
# ---------------------------------------------------------------------------

def test_render_committed_recovery():
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    st.chat_input = MagicMock(return_value="Machine M1 failed")

    mock_solution = {
        "makespan": 120, "total_tardiness": 5, "status": "OPTIMAL"
    }
    mock_state = MagicMock()
    mock_state.solution = mock_solution
    mock_state.committed_version_id = 7
    mock_state.explanation = "Reassigned ops due to M1 failure."
    mock_result = {"status": "COMMITTED", "state": mock_state, "run_id": 3}

    def _fake_streaming(*args, **kwargs):
        yield {"node": "entry"}
        yield {"node": "translate"}
        yield {"node": "ingest"}
        yield {"node": "machine_agent"}
        yield {"node": "production_agent"}
        yield {"node": "inventory_agent"}
        yield {"node": "worker_agent"}
        yield {"node": "strategy"}
        yield {"node": "manager_compile"}
        yield {"node": "solve_node"}
        yield {"node": "gate_node"}
        yield {"node": "commit_node"}
        yield {"node": "verify_node"}
        yield {"node": "explain_node"}
        yield mock_result

    try:
        with patch("coe.agents.llm_client.require_llm_config"), \
             patch("coe.config.get_settings"), \
             patch("coe.agents.graph.execute_recovery_streaming",
                   side_effect=_fake_streaming) as mock_exec, \
             patch("sqlalchemy.orm.Session") as mock_sess_cls, \
             patch("coe.db.session.make_engine"):
            mock_sess = MagicMock()
            mock_sess_cls.return_value.__enter__ = MagicMock(return_value=mock_sess)
            mock_sess_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sess.get.return_value = MagicMock(id=7)
            mock_query = MagicMock()
            mock_sess.query.return_value = mock_query
            mock_query.filter.return_value.first.return_value = MagicMock(
                rationale="Reassigned ops due to M1 failure."
            )

            render()

        mock_exec.assert_called_once_with(
            "demo", trigger="CLI", narrative="Machine M1 failed"
        )
        assert len(st.session_state["cockpit_messages"]) == 2
        assert st.session_state["cockpit_messages"][0]["role"] == "user"
        assert st.session_state["cockpit_messages"][1]["role"] == "assistant"
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — UNKNOWN outcome shows budget-starved explanation
# ---------------------------------------------------------------------------

def test_render_unknown_outcome():
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    st.chat_input = MagicMock(return_value="Worker absent")

    mock_state = MagicMock()
    mock_state.solution = {"makespan": None, "total_tardiness": None,
                           "status": "UNKNOWN"}
    mock_result = {"status": "UNKNOWN", "state": mock_state, "run_id": 4}

    def _fake_streaming(*args, **kwargs):
        yield {"node": "entry"}
        yield mock_result

    try:
        with patch("coe.agents.llm_client.require_llm_config"), \
             patch("coe.config.get_settings"), \
             patch("coe.agents.graph.execute_recovery_streaming",
                   side_effect=_fake_streaming):
            render()

        info_calls = [c.args[0] for c in st.info.call_args_list]
        assert any("budget-starved" in msg for msg in info_calls)
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — INFEASIBLE outcome
# ---------------------------------------------------------------------------

def test_render_infeasible_outcome():
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    st.chat_input = MagicMock(return_value="Critical failure")

    mock_state = MagicMock()
    mock_state.solution = {"makespan": None, "total_tardiness": None,
                           "status": "INFEASIBLE"}
    mock_result = {"status": "INFEASIBLE", "state": mock_state, "run_id": 5}

    def _fake_streaming(*args, **kwargs):
        yield {"node": "entry"}
        yield mock_result

    try:
        with patch("coe.agents.llm_client.require_llm_config"), \
             patch("coe.config.get_settings"), \
             patch("coe.agents.graph.execute_recovery_streaming",
                   side_effect=_fake_streaming):
            render()

        md_calls = [c.args[0] for c in st.markdown.call_args_list]
        assert any("INFEASIBLE" in m for m in md_calls)
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — no explanation when committed_version_id is None
# ---------------------------------------------------------------------------

def test_render_no_explanation():
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    st.chat_input = MagicMock(return_value="Another disruption")

    mock_state = MagicMock()
    mock_state.solution = {"makespan": 100, "total_tardiness": 0,
                           "status": "OPTIMAL"}
    mock_state.committed_version_id = None
    mock_state.explanation = None
    mock_result = {"status": "COMMITTED", "state": mock_state, "run_id": 6}

    def _fake_streaming(*args, **kwargs):
        yield {"node": "entry"}
        yield mock_result

    try:
        with patch("coe.agents.llm_client.require_llm_config"), \
             patch("coe.config.get_settings"), \
             patch("coe.agents.graph.execute_recovery_streaming",
                   side_effect=_fake_streaming):
            render()

        st.subheader.assert_not_called()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# _outcome_text helper
# ---------------------------------------------------------------------------

def test_outcome_text_committed_with_explanation():
    from coe.dashboard.pages.cockpit import _outcome_text

    mock_state = MagicMock()
    mock_state.solution = {"makespan": 120, "total_tardiness": 5,
                           "status": "OPTIMAL"}
    mock_state.explanation = "M1 failure handled."

    text = _outcome_text("COMMITTED", mock_state)
    assert "COMMITTED" in text
    assert "M1 failure handled." in text
    assert "120" in text


def test_outcome_text_unknown():
    from coe.dashboard.pages.cockpit import _outcome_text

    mock_state = MagicMock()
    mock_state.solution = {"makespan": None, "total_tardiness": None,
                           "status": "UNKNOWN"}
    mock_state.explanation = None

    text = _outcome_text("UNKNOWN", mock_state)
    assert "UNKNOWN" in text
    assert "budget-exhausted" in text


# ---------------------------------------------------------------------------
# C15 — node label coverage & streaming feed
# ---------------------------------------------------------------------------

def test_node_labels_cover_all_graph_nodes():
    from coe.dashboard.pages.cockpit import _NODE_LABELS

    expected = {
        "entry", "translate", "ingest",
        "machine_agent", "production_agent", "inventory_agent", "worker_agent",
        "strategy", "manager_compile", "solve_node",
        "gate_node", "commit_node", "verify_node", "explain_node",
    }
    assert set(_NODE_LABELS) == expected


def test_solve_label_communicates_180s_floor():
    from coe.dashboard.pages.cockpit import _NODE_LABELS

    assert "2 min" in _NODE_LABELS["solve_node"]


def test_streaming_feed_renders_progressive_lines():
    """Streaming feed appends one friendly line per yielded node."""
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    st.chat_input = MagicMock(return_value="Tool break")

    mock_state = MagicMock()
    mock_state.solution = None
    mock_result = {"status": "COMMITTED", "state": mock_state, "run_id": 9}

    def _fake_streaming(*args, **kwargs):
        yield {"node": "entry"}
        yield {"node": "translate"}
        yield {"node": "solve_node"}
        yield mock_result

    empty_stub = MagicMock()
    st.empty = MagicMock(return_value=empty_stub)

    try:
        with patch("coe.agents.llm_client.require_llm_config"), \
             patch("coe.config.get_settings"), \
             patch("coe.agents.graph.execute_recovery_streaming",
                   side_effect=_fake_streaming):
            render()

        # empty().markdown called once per node (progressive accumulation)
        assert empty_stub.markdown.call_count == 3
        feed_texts = [c.args[0] for c in empty_stub.markdown.call_args_list]
        # first call: 1 line
        assert feed_texts[0].count("- ") == 1
        # second call: 2 lines
        assert feed_texts[1].count("- ") == 2
        # third call: 3 lines
        assert feed_texts[2].count("- ") == 3
        assert "Initializing" in feed_texts[0]
        assert "Solving schedule" in feed_texts[2]
    finally:
        _uninstall_st()


def test_streaming_unknown_preserves_budget_starved_info():
    from coe.dashboard.pages.cockpit import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "demo"}
    st.chat_input = MagicMock(return_value="Budget exhaustion")

    mock_state = MagicMock()
    mock_state.solution = {"status": "UNKNOWN"}
    mock_result = {"status": "UNKNOWN", "state": mock_state, "run_id": 10}

    def _fake_streaming(*args, **kwargs):
        yield {"node": "entry"}
        yield {"node": "solve_node"}
        yield mock_result

    try:
        with patch("coe.agents.llm_client.require_llm_config"), \
             patch("coe.config.get_settings"), \
             patch("coe.agents.graph.execute_recovery_streaming",
                   side_effect=_fake_streaming):
            render()

        info_calls = [c.args[0] for c in st.info.call_args_list]
        assert any("budget-starved" in msg for msg in info_calls)
    finally:
        _uninstall_st()
