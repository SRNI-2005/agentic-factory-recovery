"""Pre-solve absolute-supply material gatekeeping (spec §7).

Deliberately loose: allocation order is the solver's job. We catch only
genuinely impossible shortages (zero total supply) and report partial
shortfalls as advisory warnings for the Phase 3 agents.
"""

DEAD = "MATERIAL_UNAVAILABLE"
SHORTFALL = "MATERIAL_SHORTFALL"


def evaluate_materials(*, initial_stock, receipts, bom_by_op, horizon):
    supply = dict(initial_stock)
    for r in receipts:
        if r["available_at"] < horizon:
            supply[r["sku"]] = supply.get(r["sku"], 0) + r["quantity"]

    demand: dict[str, int] = {}
    for items in bom_by_op.values():
        for it in items:
            demand[it["sku"]] = demand.get(it["sku"], 0) + it["quantity"]

    dead = {sku for sku, d in demand.items() if supply.get(sku, 0) == 0}
    short = {sku for sku, d in demand.items() if 0 < supply.get(sku, 0) < d}

    blocks: dict[str, dict] = {}
    for op_id in sorted(bom_by_op):
        hits = sorted({it["sku"] for it in bom_by_op[op_id]} & dead)
        if hits:
            blocks[op_id] = {"reason": DEAD, "material_sku": hits[0]}

    warnings = [
        {"type": SHORTFALL, "material_sku": sku,
         "total_supply": supply.get(sku, 0), "total_demand": demand[sku]}
        for sku in sorted(short)
    ]
    return blocks, warnings
