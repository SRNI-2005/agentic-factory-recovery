import sys
import types

import pytest

from coe import cli


def test_dashboard_parser_defaults_to_port_8501():
    args = cli.build_parser().parse_args(["dashboard"])

    assert args.group == "dashboard"
    assert args.port == 8501


def test_dashboard_parser_accepts_custom_port():
    args = cli.build_parser().parse_args(["dashboard", "--port", "9000"])

    assert args.port == 9000


def test_dashboard_dispatches_streamlit_on_loopback(monkeypatch):
    calls = []
    streamlit_cli = types.SimpleNamespace(main=lambda: calls.append(sys.argv))
    streamlit_web = types.ModuleType("streamlit.web")
    streamlit_web.cli = streamlit_cli
    streamlit = types.ModuleType("streamlit")
    streamlit.web = streamlit_web
    monkeypatch.setitem(sys.modules, "streamlit", streamlit)
    monkeypatch.setitem(sys.modules, "streamlit.web", streamlit_web)
    monkeypatch.setitem(sys.modules, "streamlit.web.cli", streamlit_cli)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["dashboard", "--port", "9000"])

    assert exc_info.value.code is None
    assert calls == [[
        "streamlit", "run", "coe/dashboard/app.py",
        "--server.port", "9000",
        "--server.address", "127.0.0.1",
        "--server.headless", "true",
    ]]
