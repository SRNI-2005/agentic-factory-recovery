"""Tests for coe.dashboard.pages.benchmarks — focused import + render checks."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_column():
    c = MagicMock()
    c.metric = MagicMock()
    return c


def _make_st():
    """Build a minimal Streamlit stub capturing calls."""
    st = types.ModuleType("streamlit")
    st.session_state = {}
    st.warning = MagicMock()
    st.error = MagicMock()
    st.info = MagicMock()
    st.stop = MagicMock(side_effect=SystemExit)
    st.subheader = MagicMock()
    st.metric = MagicMock()
    st.columns = MagicMock(return_value=[_make_column(), _make_column(), _make_column()])
    st.dataframe = MagicMock()
    st.caption = MagicMock()
    return st


def _install_st(st):
    sys.modules["streamlit"] = st
    return st


def _uninstall_st():
    sys.modules.pop("streamlit", None)


def _all_metrics(st):
    """Collect (label, value) from st.metric and all column containers."""
    pairs = {}
    for call in st.metric.call_args_list:
        pairs[call.args[0]] = call.args[1] if len(call.args) > 1 else call.kwargs.get("value")
    for col in st.columns.return_value:
        for call in col.metric.call_args_list:
            pairs[call.args[0]] = call.args[1] if len(call.args) > 1 else call.kwargs.get("value")
    return pairs


def _make_report():
    return {
        "translation": {
            "per_kind": {
                "MACHINE": {"exact_match_rate": 0.95, "corpus_pass_rate": 1.0},
                "WORKER": {"exact_match_rate": 0.88, "corpus_pass_rate": 0.75},
                "MATERIAL": {"exact_match_rate": 0.92, "corpus_pass_rate": 1.0},
            },
            "aggregate": {"exact_match_rate": 0.916, "corpus_pass_rate": 0.917},
        },
        "strategy": {
            "validity_rate": 0.8,
            "non_degradation_rate": 1.0,
            "baseline_infeasible": 0,
            "measured": False,
        },
        "cases": [
            {"case_id": "case-00", "kind": "MACHINE", "field_hits": 3,
             "field_total": 3, "corpus_pass": True},
            {"case_id": "case-01", "kind": "WORKER", "field_hits": 2,
             "field_total": 3, "corpus_pass": False},
        ],
        "threshold_met": True,
    }


# ---------------------------------------------------------------------------
# import smoke
# ---------------------------------------------------------------------------

def test_benchmarks_page_imports():
    import coe.dashboard.pages.benchmarks as mod

    assert hasattr(mod, "render")


# ---------------------------------------------------------------------------
# render — missing report shows command hint
# ---------------------------------------------------------------------------

def test_render_missing_report():
    from coe.dashboard.pages.benchmarks import render

    st = _install_st(_make_st())
    try:
        with patch("coe.dashboard.data.fidelity_report", return_value=None):
            with pytest.raises(SystemExit):
                render()
        st.warning.assert_called_once()
        assert "benchmark fidelity" in st.warning.call_args[0][0]
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — present report with threshold MET
# ---------------------------------------------------------------------------

def test_render_report_met():
    from coe.dashboard.pages.benchmarks import render

    st = _install_st(_make_st())
    try:
        report = _make_report()
        with patch("coe.dashboard.data.fidelity_report", return_value=report):
            render()

        metrics = _all_metrics(st)
        assert "Corpus pass rate" in metrics
        assert "Exact match rate" in metrics
        assert "Threshold" in metrics
        assert metrics["Threshold"] == "MET"
        st.dataframe.assert_called_once()
        st.caption.assert_called_once()
        assert "P4/P5" in st.caption.call_args[0][0]
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — threshold MISS
# ---------------------------------------------------------------------------

def test_render_report_miss():
    from coe.dashboard.pages.benchmarks import render

    st = _install_st(_make_st())
    try:
        report = _make_report()
        report["threshold_met"] = False
        with patch("coe.dashboard.data.fidelity_report", return_value=report):
            render()

        metrics = _all_metrics(st)
        assert metrics.get("Threshold") == "MISS"
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — no cases skips dataframe
# ---------------------------------------------------------------------------

def test_render_no_cases():
    from coe.dashboard.pages.benchmarks import render

    st = _install_st(_make_st())
    try:
        report = _make_report()
        report["cases"] = []
        with patch("coe.dashboard.data.fidelity_report", return_value=report):
            render()

        st.dataframe.assert_not_called()
    finally:
        _uninstall_st()


# ---------------------------------------------------------------------------
# render — strategy measured shows strategy metrics
# ---------------------------------------------------------------------------

def test_render_strategy_measured():
    from coe.dashboard.pages.benchmarks import render

    st = _install_st(_make_st())
    try:
        report = _make_report()
        report["strategy"]["measured"] = True
        with patch("coe.dashboard.data.fidelity_report", return_value=report):
            render()

        metrics = _all_metrics(st)
        assert "Validity rate" in metrics
        assert "Non-degradation rate" in metrics
        assert "Baseline infeasible" in metrics
    finally:
        _uninstall_st()
