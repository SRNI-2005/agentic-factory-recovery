"""Pre-solve absolute-supply material gatekeeping (spec §7).

Deliberately loose: allocation order is the solver's job. We catch only
genuinely impossible shortages (zero stock AND zero receipts at any time)
and report partial shortfalls as advisory warnings for the Phase 3 agents.

Since Amendment 2026-08-24 (third), receipts count regardless of timing:
a delivery at t=5000 legitimately enables a deferred operation, so it must
not be treated as absent supply.

Amendment 2026-08-25 (fifth): with ``release_by_op`` (op_id -> earliest
minute), detection becomes TIME-PHASED — a SKU also warns when some release
prefix's cumulative demand exceeds stock plus timely receipts, even when the
grand totals suffice. This restores Phase 3 §4.3 step-3 DEFER reachability
("only timing is wrong" presupposes a warning that survives sufficient
totals). Without ``release_by_op`` behavior is byte-identical to the
pre-amendment rule. Dead-block (MATERIAL_UNAVAILABLE) semantics are
untouched; the warning shape is unchanged (totals still aggregate ALL
receipts); the absolute and time-phased rules union with per-SKU dedupe —
never two warnings for one SKU.
"""

DEAD = "MATERIAL_UNAVAILABLE"
SHORTFALL = "MATERIAL_SHORTFALL"


def _time_phased_short_skus(initial_stock, receipts, bom_by_op,
                            release_by_op):
    """SKUs whose early-released demand outruns timely supply at some
    release prefix. Deterministic: ops sorted by (release, op_id), receipts
    sorted by (available_at, quantity), prefixes walked in ascending order.
    Ops missing from release_by_op count as released at 0 (earliest, i.e.
    the conservative reading)."""
    recs_by_sku: dict[str, list[tuple[int, int]]] = {}
    for r in receipts:
        recs_by_sku.setdefault(r["sku"], []).append(
            (r.get("available_at", 0), r["quantity"]))
    skus = {it["sku"] for items in bom_by_op.values() for it in items}
    short: set[str] = set()
    for sku in sorted(skus):
        ops = []
        for oid_, items in bom_by_op.items():
            qty = sum(it["quantity"] for it in items if it["sku"] == sku)
            if qty:
                ops.append((release_by_op.get(oid_, 0), oid_, qty))
        if not ops:
            continue
        ops.sort()
        recs = sorted(recs_by_sku.get(sku, []))
        stock = initial_stock.get(sku, 0)
        cum_dem = cum_rec = ri = i = 0
        for t in sorted({rel for rel, _, _ in ops}):
            while ri < len(recs) and recs[ri][0] <= t:
                cum_rec += recs[ri][1]
                ri += 1
            while i < len(ops) and ops[i][0] <= t:
                cum_dem += ops[i][2]
                i += 1
            if cum_dem > stock + cum_rec:
                short.add(sku)
                break
    return short


def evaluate_materials(*, initial_stock, receipts, bom_by_op,
                       release_by_op=None):
    supply = dict(initial_stock)
    for r in receipts:
        supply[r["sku"]] = supply.get(r["sku"], 0) + r["quantity"]

    demand: dict[str, int] = {}
    for items in bom_by_op.values():
        for it in items:
            demand[it["sku"]] = demand.get(it["sku"], 0) + it["quantity"]

    dead = {sku for sku, d in demand.items() if supply.get(sku, 0) == 0}
    short = {sku for sku, d in demand.items() if 0 < supply.get(sku, 0) < d}
    if release_by_op is not None:
        short |= _time_phased_short_skus(initial_stock, receipts,
                                         bom_by_op, release_by_op)

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
