"""Smoke test: Streamlit cockpit boots, exposes native nav, defaults to factory_demo_01."""
from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest


pytestmark = pytest.mark.db


APP = Path(__file__).resolve().parents[2] / "coe" / "dashboard" / "app.py"


def test_app_boots_without_exception(demo_scenario):
    at = AppTest.from_file(APP).run()
    assert not at.exception, f"App raised: {at.exception}"


def test_native_navigation_exposed(demo_scenario):
    at = AppTest.from_file(APP).run()
    assert not at.exception
    for page in ("cockpit", "configure", "runs", "benchmarks"):
        at.switch_page(f"pages/{page}.py").run()
        assert not at.exception, f"page {page!r} raised: {at.exception}"


def test_factory_demo_01_selected_by_default(demo_scenario):
    at = AppTest.from_file(APP).run()
    assert not at.exception
    sb = at.sidebar.selectbox(key="instance_name")
    assert sb.value == "factory_demo_01"
