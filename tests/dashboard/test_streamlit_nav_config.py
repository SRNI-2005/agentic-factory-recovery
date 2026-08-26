"""Regression: Streamlit auto-discovers pages/ and renders duplicate sidebar nav.

The project uses a custom st.sidebar.radio router in app.py.  To suppress
the auto-discovered navigation the Streamlit project config must set
``client.showSidebarNavigation = false``.
"""
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_TOML = PROJECT_ROOT / ".streamlit" / "config.toml"


def test_streamlit_config_toml_exists():
    """The .streamlit/config.toml file must be present in the project root."""
    assert CONFIG_TOML.exists(), (
        f".streamlit/config.toml not found at {CONFIG_TOML} – Streamlit will "
        "render auto-discovered sidebar navigation alongside the custom router"
    )


@pytest.mark.skipif(not CONFIG_TOML.exists(), reason="config file absent")
def test_sidebar_navigation_disabled():
    """client.showSidebarNavigation must be explicitly set to false."""
    content = CONFIG_TOML.read_text()
    assert "showSidebarNavigation" in content, (
        "showSidebarNavigation key missing from .streamlit/config.toml"
    )
    # Parse the TOML ourselves to avoid importing tomllib on older Pythons
    # and to give a clear assertion message.
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("showSidebarNavigation"):
            # Accept both true/false variants; must be false
            value = stripped.split("=", 1)[1].strip()
            assert value.lower() == "false", (
                f"showSidebarNavigation should be false, got {value!r}"
            )
            return
    pytest.fail("showSidebarNavigation key not found in any [client] section")
