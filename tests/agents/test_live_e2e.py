# tests/agents/test_live_e2e.py
"""§11 Tier 5: real provider, temperature 0, opt-in via env config."""
import os

import pytest

_cfg = bool(os.environ.get("LLM_PROVIDER") and os.environ.get("LLM_MODEL"))
pytestmark = [pytest.mark.db, pytest.mark.llm,
              pytest.mark.skipif(not _cfg,
                                 reason="live provider not configured")]


@pytest.fixture(scope="module")
def demo(db_url):
    """factory_demo_01 + real CP-SAT baseline (acceptance fixture body)."""
    from pathlib import Path
    from types import SimpleNamespace

    from coe.cli import _run_solve
    from coe.db.admin import reset_database
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    reset_database(db_url)
    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))
    build_scenario("factory_demo_01", seed=42)
    _run_solve(SimpleNamespace(
        solve_cmd="baseline", instance="factory_demo_01", alpha=None,
        beta=None, time_limit=None, seed=None, workers=None,
        no_normalize=False))


def test_live_recovery_commits_and_explains(demo):
    from coe.agents.graph import execute_recovery
    from coe.agents.llm_client import make_llm_client

    out = execute_recovery(
        "factory_demo_01", trigger="CLI",
        narrative="M3 spindle seized with a bang, several hours of repair "
                  "expected", reference_clock=512,
        client=make_llm_client())
    assert out["status"] == "COMMITTED"
    assert out["state"].explanation            # committed AND explained
