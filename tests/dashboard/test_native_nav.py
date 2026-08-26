"""Regression: app.py must use native st.navigation, not a custom radio router."""
import ast
import re
from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parents[2] / "coe" / "dashboard" / "app.py"

_source = APP_PY.read_text()
_tree = ast.parse(_source)


def test_no_importlib_import():
    """app.py must not import importlib."""
    for node in ast.walk(_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "importlib", "importlib must not be imported"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "importlib", "importlib must not be imported"


def test_no_sidebar_radio():
    """app.py must not use st.sidebar.radio for navigation."""
    assert "sidebar.radio" not in _source, (
        "st.sidebar.radio must not appear in app.py"
    )


def test_uses_st_navigation():
    """app.py must call st.navigation."""
    assert "st.navigation" in _source, (
        "app.py must call st.navigation for page routing"
    )


def test_four_pages_registered():
    """The four page titles must appear as st.Page calls to st.navigation."""
    for title in ("Cockpit", "Configure", "Runs", "Benchmarks"):
        assert title in _source, f"Page title {title!r} missing from app.py"


def test_pages_receive_render_callables():
    """Each page module's render function must be passed to st.Page."""
    for mod in ("cockpit", "configure", "runs", "benchmarks"):
        assert (
            f"pages.{mod}.render" in _source
            or f"render_{mod}" in _source
        ), f"pages.{mod}.render not found in app.py"


def test_instance_selectbox_before_navigation():
    """The instance selectbox must appear before st.navigation in source order."""
    idx_nav = _source.index("st.navigation")
    idx_select = _source.index("st.sidebar.selectbox")
    assert idx_select < idx_nav, (
        "Instance selectbox must appear before st.navigation"
    )


def test_navigation_run_called():
    """The returned page object must have .run() called."""
    assert ".run()" in _source, "Page.run() must be called after st.navigation"


def test_each_page_has_unique_url_path():
    """Every st.Page(callable) must carry an explicit url_path to avoid
    pathname collisions when all callables share __name__ == 'render'.

    Without this, Streamlit infers url_path from callable.__name__ and
    all four pages get url_path='render', producing blank pages on
    navigation.
    """
    # Extract every st.Page(...) call via regex (handles multi-line)
    page_calls = re.findall(r"st\.Page\(([^)]+)\)", _source, re.DOTALL)
    assert len(page_calls) >= 4, f"Expected ≥4 st.Page calls, found {len(page_calls)}"
    for call in page_calls:
        assert "url_path=" in call, (
            f"st.Page call missing explicit url_path:\n  st.Page({call.strip()})"
        )
