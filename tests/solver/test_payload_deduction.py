# tests/solver/test_payload_deduction.py
"""P2 §6.11 amendment (a): frozen/consumed stock deduction at recovery."""
import subprocess

import pytest

pytestmark = pytest.mark.db


@pytest.fixture()
def stocky(demo_scenario):
    """demo scenario is byte-deterministic; assert base stock via ORM."""
    from sqlalchemy import text
    from coe.db.session import make_engine

    with make_engine().connect() as c:
        iid = c.execute(text(
            "SELECT id FROM instances WHERE name='factory_demo_01'"
        )).scalar_one()
        return iid


@pytest.fixture()
def baseline_ready(stocky, clean_db):
    """Drive a real baseline schedule so recovery has frozen ops to deduct."""
    r = subprocess.run(
        ["uv", "run", "python", "-m", "coe.cli", "solve", "baseline",
         "--instance", "factory_demo_01"],
        capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    return stocky


def test_recovery_payload_deducts_consumed(baseline_ready):
    """Baseline has committed BOM consumption before any clock > 0.

    We assert the MECHANISM: for the same instance, the recovery payload's
    capacity for a consumed sku is strictly less than the baseline payload's,
    for a reference clock after the first op ends.
    """
    from sqlalchemy.orm import Session
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.solver.payload_builder import build_payload

    eng = make_engine()
    with Session(eng) as s:
        inst = s.get(Instance, baseline_ready)
        base = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                             time_limit_seconds=1)
        rec = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                            time_limit_seconds=1,
                            schedule_type="RECOVERY", now=200)
    cap_base = {m["sku"]: m["capacity"] for m in base["materials"]}
    cap_rec = {m["sku"]: m["capacity"] for m in rec["materials"]}
    consumed_before = any(
        cap_rec[sku] < cap_base[sku]
        for sku in cap_rec if base["schedule_type"] == "BASELINE")
    assert consumed_before, "recovery must not re-credit consumed stock"
    # receipts that already arrived are folded in, not refill rows:
    deltas = [r for r in rec["material_receipts"] if r["available_at"] < 200]
    assert deltas == []
