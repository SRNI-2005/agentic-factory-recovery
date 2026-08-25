from coe.solver.materials_check import evaluate_materials


def _run(stock, receipts, bom):
    return evaluate_materials(initial_stock=stock, receipts=receipts,
                              bom_by_op=bom)


def test_zero_supply_blocks_ops():
    blocks, warns = _run({"STEEL": 0}, [], {"O1": [{"sku": "STEEL", "quantity": 2}]})
    assert blocks == {"O1": {"reason": "MATERIAL_UNAVAILABLE", "material_sku": "STEEL"}}
    assert warns == []


def test_sufficient_supply_passes_silently():
    blocks, warns = _run({"STEEL": 5}, [], {"O1": [{"sku": "STEEL", "quantity": 2}]})
    assert blocks == {} and warns == []


def test_receipt_at_any_time_counts():
    """Amendment 2026-08-24 (third): timing no longer gates supply — a
    delivery at t=5000 enables a deferred operation, so it is never
    'absent supply'."""
    for available_at in (500, 1000, 5000):
        blocks, _ = _run({"STEEL": 0},
                         [{"sku": "STEEL", "quantity": 10,
                           "available_at": available_at}],
                         {"O1": [{"sku": "STEEL", "quantity": 2}]})
        assert blocks == {}, available_at


def test_partial_shortfall_warns_but_never_blocks():
    blocks, warns = _run({"STEEL": 3}, [], {"O1": [{"sku": "STEEL", "quantity": 2}],
                                            "O2": [{"sku": "STEEL", "quantity": 4}]})
    assert blocks == {}
    assert warns == [{"type": "MATERIAL_SHORTFALL", "material_sku": "STEEL",
                      "total_supply": 3, "total_demand": 6}]


def test_multi_material_reports_first_dead_sku():
    blocks, _ = _run({"AAA": 0, "ZZZ": 0}, [],
                     {"O1": [{"sku": "ZZZ", "quantity": 1}, {"sku": "AAA", "quantity": 1}]})
    assert blocks["O1"]["material_sku"] == "AAA"


# ---- Amendment 2026-08-25 (fifth): time-phased shortfall detection ----


def test_time_phased_shortfall_warns_despite_sufficient_totals():
    """With release_by_op, a release-prefix deficit warns even when stock
    plus ALL receipts cover the grand total demand."""
    blocks, warns = evaluate_materials(
        initial_stock={"STEEL": 5},
        receipts=[{"sku": "STEEL", "quantity": 10, "available_at": 200}],
        bom_by_op={"O1": [{"sku": "STEEL", "quantity": 5}],
                   "O2": [{"sku": "STEEL", "quantity": 5}]},
        release_by_op={"O1": 0, "O2": 0})
    assert blocks == {}
    assert warns == [{"type": "MATERIAL_SHORTFALL", "material_sku": "STEEL",
                      "total_supply": 15, "total_demand": 10}]


def test_deferred_release_clears_warning():
    """Same totals as above, but the second op releases at the receipt time:
    every prefix is covered, so no warning fires."""
    blocks, warns = evaluate_materials(
        initial_stock={"STEEL": 5},
        receipts=[{"sku": "STEEL", "quantity": 10, "available_at": 200}],
        bom_by_op={"O1": [{"sku": "STEEL", "quantity": 5}],
                   "O2": [{"sku": "STEEL", "quantity": 5}]},
        release_by_op={"O1": 0, "O2": 200})
    assert blocks == {} and warns == []


def test_no_receipts_no_time_phased_extra():
    """The absolute rule already covers a plain shortfall: time-phased
    detection dedupes per SKU instead of emitting a second warning."""
    blocks, warns = evaluate_materials(
        initial_stock={"STEEL": 5},
        receipts=[],
        bom_by_op={"O1": [{"sku": "STEEL", "quantity": 6}]},
        release_by_op={"O1": 0})
    assert blocks == {}
    assert len(warns) == 1
    assert warns == [{"type": "MATERIAL_SHORTFALL", "material_sku": "STEEL",
                      "total_supply": 5, "total_demand": 6}]
