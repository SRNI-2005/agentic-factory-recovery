"""Shared simulator test fixtures.

`sim_factory_instance` forks factory_demo_01 once per pytest session and
commits one baseline on the clone via a single CLI subprocess. Read-only
seam tests (projector playback, payload deduction) share the clone instead
of forking + solving per test (quick-gate cost containment). The cache is
re-verified per test because unrelated db tests reset the database
mid-session; if the clone is gone it is rebuilt (re-forked + re-solved).
"""
import subprocess

import pytest

_CACHE: dict = {"clone": None}
_SOLVE_TIMEOUT = 900


def ensure_sim_baseline_clone() -> str:
    """Name of a forked factory_demo_01 clone with a committed baseline.

    Cached across tests in the session; rebuilt if a clean_db reset wiped
    the scenario or the clone. Returns the clone *name* (string).
    """
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.services.fork import fork_instance

    with make_engine().connect() as c:
        has_source = c.execute(text(
            "SELECT 1 FROM instances WHERE name='factory_demo_01'"
        )).scalar_one_or_none()
    if not has_source:
        _rebuild_scenario()
        _CACHE["clone"] = None
    if _CACHE["clone"] is not None:
        with make_engine().connect() as c:
            alive = c.execute(text(
                "SELECT COUNT(*) FROM instances WHERE name=:n"),
                {"n": _CACHE["clone"]}).scalar()
        if alive:
            return _CACHE["clone"]

    with Session(make_engine()) as s:
        source = (s.query(Instance)
                  .filter(Instance.name == "factory_demo_01").one())
        forked = fork_instance(s, source)
        s.commit()
        clone_name = forked.name

    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", clone_name],
        check=True, capture_output=True, text=True, timeout=_SOLVE_TIMEOUT)
    if r.returncode != 0:  # pragma: no cover - subprocess.run(check=True) guards
        raise AssertionError(r.stderr)
    _CACHE["clone"] = clone_name
    return clone_name


def _rebuild_scenario():
    """Same imports + build as tests/conftest.py:demo_scenario."""
    from pathlib import Path

    from coe.db.admin import reset_database
    from coe.config import get_settings
    from coe.parsers.gass import import_gass
    from coe.parsers.mk01 import import_mk01
    from coe.parsers.nouri import import_nouri
    from coe.scenario.build import build_scenario

    reset_database(get_settings().database_url)
    import_mk01(Path("data/raw/mk01/mk01.txt"))
    import_nouri(Path("data/raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt"))
    import_gass(Path("data/raw/gass"))
    build_scenario("factory_demo_01", seed=42)


@pytest.fixture()
def sim_factory_instance():
    """Shared baseline-bearing clone name for read-only seam tests."""
    return ensure_sim_baseline_clone()
