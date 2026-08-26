"""Tests for coe.dashboard.pages.runs — focused import + render checks."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
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
    st.subheader = MagicMock()
    st.json = MagicMock()
    st.plotly_chart = MagicMock()
    st.expander = MagicMock(return_value=MagicMock(__enter__=lambda s: s,
                                                   __exit__=MagicMock()))
    return st


def _make_run(**overrides):
    base = {
        "id": 1,
        "trigger": "CLI",
        "status": "COMMITTED",
        "started_at": datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 1, 2, 12, 0, 30, tzinfo=timezone.utc),
        "disruption_record_json": {"type": "MACHINE", "machine": "M1"},
        "node_timings_json": None,
        "quantum_shadow_json": None,
        "final_status_version_id": None,
    }
    base.update(overrides)
    return base


def _install_st(st):
    """Inject a stub as ``streamlit`` so lazy ``import streamlit as st`` picks it up."""
    sys.modules["streamlit"] = st
    return st


def _uninstall_st():
    sys.modules.pop("streamlit", None)


# ---------------------------------------------------------------------------
# import smoke
# ---------------------------------------------------------------------------

def test_runs_page_imports():
    import coe.dashboard.pages.runs as mod

    assert hasattr(mod, "render")


# ---------------------------------------------------------------------------
# _render_timings — dict shape (current test-fixture)
# ---------------------------------------------------------------------------

def test_render_timings_dict_shape():
    from coe.dashboard.pages.runs import _render_timings

    st = _install_st(_make_st())
    try:
        timings = {"translate": 0.42, "solve_node": 1.23}
        _render_timings(timings)

        st.subheader.assert_called_once_with("Per-node wall-clock")
        st.plotly_chart.assert_called_once()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# _render_timings — list-of-dicts shape (spec target)
# ---------------------------------------------------------------------------

def test_render_timings_list_shape():
    from coe.dashboard.pages.runs import _render_timings

    st = _install_st(_make_st())
    try:
        timings = [
            {"node": "translate", "started_at": 100.0, "ended_at": 100.42},
            {"node": "solve_node", "started_at": 100.42, "ended_at": 101.65},
        ]
        _render_timings(timings)

        st.subheader.assert_called_once_with("Per-node wall-clock")
        st.plotly_chart.assert_called_once()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# _render_timings — None / empty
# ---------------------------------------------------------------------------

def test_render_timings_none():
    from coe.dashboard.pages.runs import _render_timings

    st = _install_st(_make_st())
    try:
        _render_timings(None)
        st.subheader.assert_not_called()
        st.plotly_chart.assert_not_called()
    finally:
        _uninstall_st()


def test_render_timings_empty_dict():
    from coe.dashboard.pages.runs import _render_timings

    st = _install_st(_make_st())
    try:
        _render_timings({})
        st.subheader.assert_not_called()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — no instance selected
# ---------------------------------------------------------------------------

def test_render_no_instance():
    from coe.dashboard.pages.runs import render

    st = _install_st(_make_st())
    st.session_state = {}
    try:
        with pytest.raises(SystemExit):
            render()
        st.warning.assert_called_once()
        st.stop.assert_called_once()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — instance not found
# ---------------------------------------------------------------------------

def test_render_instance_not_found():
    from coe.dashboard.pages.runs import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "no-such"}
    try:
        mock_session = MagicMock()
        mock_session.execute.return_value.mappings.return_value.first.return_value = None

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("coe.db.session.session_scope",
                   return_value=mock_ctx), \
             patch("coe.dashboard.data.recovery_runs", return_value=[]):
            with pytest.raises(SystemExit):
                render()

        st.error.assert_called_once()
        st.stop.assert_called_once()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — no runs
# ---------------------------------------------------------------------------

def test_render_no_runs():
    from coe.dashboard.pages.runs import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "test-inst"}
    try:
        mock_session = MagicMock()
        mock_session.execute.return_value.mappings.return_value.first.return_value = {"id": 42}

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("coe.db.session.session_scope",
                   return_value=mock_ctx), \
             patch("coe.dashboard.data.recovery_runs", return_value=[]):
            render()

        st.info.assert_called_with("No recovery runs yet.")
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — runs present, COMMITTED green, failed red
# ---------------------------------------------------------------------------

def test_render_committed_and_failed():
    from coe.dashboard.pages.runs import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "test-inst"}
    try:
        committed = _make_run(id=1, status="COMMITTED")
        failed = _make_run(id=2, status="GATE_FAILED")

        mock_session = MagicMock()
        mock_session.execute.return_value.mappings.return_value.first.return_value = {"id": 1}

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("coe.db.session.session_scope",
                   return_value=mock_ctx), \
             patch("coe.dashboard.data.recovery_runs",
                   return_value=[committed, failed]):
            render()

        assert st.expander.call_count == 2
        headers = [call.args[0] for call in st.expander.call_args_list]
        assert "🟢" in headers[0]
        assert "COMMITTED" in headers[0]
        assert "🔴" in headers[1]
        assert "GATE_FAILED" in headers[1]
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — quantum shadow rendered when present
# ---------------------------------------------------------------------------

def test_render_quantum_shadow():
    from coe.dashboard.pages.runs import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "test-inst"}
    try:
        run = _make_run(id=1, status="COMMITTED",
                        quantum_shadow_json={"q": "shadow"})

        mock_session = MagicMock()
        mock_session.execute.return_value.mappings.return_value.first.return_value = {"id": 1}

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("coe.db.session.session_scope",
                   return_value=mock_ctx), \
             patch("coe.dashboard.data.recovery_runs", return_value=[run]):
            render()

        json_calls = [call.args[0] for call in st.json.call_args_list]
        assert {"q": "shadow"} in json_calls
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — node_timings chart rendered when present
# ---------------------------------------------------------------------------

def test_render_node_timings_chart():
    from coe.dashboard.pages.runs import render

    st = _install_st(_make_st())
    st.session_state = {"instance": "test-inst"}
    try:
        run = _make_run(id=1, status="COMMITTED",
                        node_timings_json={"translate": 0.5, "solve": 1.2})

        mock_session = MagicMock()
        mock_session.execute.return_value.mappings.return_value.first.return_value = {"id": 1}

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("coe.db.session.session_scope",
                   return_value=mock_ctx), \
             patch("coe.dashboard.data.recovery_runs", return_value=[run]):
            render()

        st.plotly_chart.assert_called_once()
    finally:
        _uninstall_st()
