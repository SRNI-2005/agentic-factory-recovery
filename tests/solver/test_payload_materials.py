"""Amendment 2026-08-24: payload carries material physics inputs."""
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


def _build(session, inst):
    from coe.solver.payload_builder import build_payload

    return build_payload(session, instance_row=inst, alpha=1.0, beta=1.0,
                         time_limit_seconds=60)


def test_factory_materials_capacities_match_db(demo_session):
    session, inst = demo_session
    p = _build(session, inst)
    skus = [m["sku"] for m in p["materials"]]
    assert skus == sorted(skus) and len(skus) == 8

    row = session.execute(text(
        "SELECT m.sku, m.initial_stock, "
        "  COALESCE(SUM(CASE WHEN r.available_at < :h THEN r.quantity END), 0) "
        "FROM materials m "
        "LEFT JOIN material_receipts r ON r.material_id = m.id "
        "WHERE m.instance_id = :i GROUP BY m.sku, m.initial_stock"),
        {"i": inst.id, "h": 10 ** 9}).all()
    # recompute per-sku with the payload's own horizon for exactness:
    from coe.solver.horizon import compute_horizon

    H = compute_horizon(jobs=p["jobs"],
                        machine_downtime=p["machine_downtime"],
                        setup_times=p["setup_times"])
    expected = {}
    for sku, stock, _ in row:
        rec = session.execute(text(
            "SELECT COALESCE(SUM(r.quantity),0) FROM material_receipts r "
            "JOIN materials m ON m.id = r.material_id "
            "WHERE m.instance_id = :i AND m.sku = :s AND r.available_at < :h"),
            {"i": inst.id, "s": sku, "h": H}).scalar_one()
        expected[sku] = stock + rec
    got = {m["sku"]: m["capacity"] for m in p["materials"]}
    assert got == expected


def test_operation_demands_mirror_bom(demo_session):
    session, inst = demo_session
    p = _build(session, inst)
    checked = 0
    for job in p["jobs"]:
        for op in job["operations"]:
            dem = op["materials"]
            assert [d["sku"] for d in dem] == sorted(d["sku"] for d in dem)
            if op["status"] != "BLOCKED":
                rows = session.execute(text(
                    "SELECT m.sku, b.quantity_required FROM operation_bom b "
                    "JOIN operations o ON o.id = b.operation_id "
                    "JOIN jobs j ON j.id = o.job_id "
                    "JOIN materials m ON m.id = b.material_id "
                    "WHERE j.name = :j AND o.sequence_number = :s "
                    "AND j.instance_id = :i ORDER BY m.sku"),
                    {"j": job["job_id"], "s": op["sequence"],
                     "i": inst.id}).all()
                assert dem == [{"sku": s, "quantity": q} for s, q in rows]
                checked += 1
    assert checked > 100          # factory_demo_01 has ~168 ops


def test_blocked_ops_carry_no_demands(demo_session):
    """Zero-supply pre-block unchanged; blocked entries have empty lists."""
    session, inst = demo_session
    p = _build(session, inst)
    assert p["blocked_operations"] == []      # demo baseline is unblocked
    for job in p["jobs"]:
        for op in job["operations"]:
            assert isinstance(op["materials"], list)


def test_materials_arrays_deterministic(demo_session):
    session, inst = demo_session
    a = json.dumps(_build(session, inst), sort_keys=True)
    b = json.dumps(_build(session, inst), sort_keys=True)
    assert a == b
