# tests/solver/test_payload_deduction.py
"""P2 §6.11 amendment (a): frozen/consumed stock deduction at recovery."""
import pytest

pytestmark = pytest.mark.db


@pytest.fixture()
def baseline_ready():
    """Shared session clone (fork of factory_demo_01 with a committed
    baseline) instead of a per-test canonical-instance solve."""
    from sqlalchemy.orm import Session

    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from tests.simulator.conftest import ensure_sim_baseline_clone

    clone_name = ensure_sim_baseline_clone()
    with Session(make_engine()) as s:
        return s.query(Instance.id).filter(
            Instance.name == clone_name).scalar_one()


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
    # receipts that already arrived are folded in, not refill rows
    # (boundary: available_at <= now folds, per P2 §6.11):
    deltas = [r for r in rec["material_receipts"] if r["available_at"] <= 200]
    assert deltas == []


def test_recovery_folds_pre_clock_receipts(baseline_ready):
    """Synthetic receipts prove fold semantics end-to-end (P2 §6.11).

    Baseline payload keeps receipt rows in material_receipts. Recovery at
    now=200 does NOT emit them as refills when available_at <= 200
    (boundary: 200 itself folds); the quantity reappears in capacity.
    """
    from sqlalchemy.orm import Session
    from coe.db.models.materials import Material, MaterialReceipt
    from coe.db.models.provenance import Instance
    from coe.db.session import make_engine
    from coe.solver.payload_builder import build_payload

    NOW = 200
    Q_EARLY, AT_EARLY = 7, 100
    Q_BOUNDARY, AT_BOUNDARY = 5, NOW  # available_at == now → folds too

    eng = make_engine()
    with Session(eng) as s:
        inst = s.get(Instance, baseline_ready)

        sku = (s.query(Material.sku)
                 .filter(Material.instance_id == baseline_ready)
                 .order_by(Material.sku)
                 .first()[0])
        mat = (s.query(Material)
                 .filter(Material.instance_id == baseline_ready,
                         Material.sku == sku)
                 .one())

        # pre-insert reference: recovery capacity before synthetic receipts
        rec0 = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                             time_limit_seconds=1,
                             schedule_type="RECOVERY", now=NOW)
        cap0 = {m["sku"]: m["capacity"] for m in rec0["materials"]}

        s.add_all([
            MaterialReceipt(instance_id=baseline_ready, material_id=mat.id,
                            quantity=Q_EARLY, available_at=AT_EARLY,
                            source="day_simulator"),
            MaterialReceipt(instance_id=baseline_ready, material_id=mat.id,
                            quantity=Q_BOUNDARY, available_at=AT_BOUNDARY,
                            source="day_simulator"),
        ])
        s.flush()

        base = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                             time_limit_seconds=1)
        rec = build_payload(s, instance_row=inst, alpha=1.0, beta=1.0,
                            time_limit_seconds=1,
                            schedule_type="RECOVERY", now=NOW)

        # baseline keeps RECEIPT ROWS (post-horizon refills inert but
        # audit-relevant); recovery folds them away.
        base_skus = ((r["sku"], r["available_at"])
                     for r in base["material_receipts"])
        assert (sku, AT_EARLY) in base_skus
        rec_pairs = [(r["sku"], r["available_at"])
                     for r in rec["material_receipts"]]
        assert (sku, AT_EARLY) not in rec_pairs
        assert (sku, AT_BOUNDARY) not in rec_pairs

        # capacity = initial − consumed + folded pre-clock receipts
        cap = {m["sku"]: m["capacity"] for m in rec["materials"]}
        assert cap[sku] == cap0[sku] + Q_EARLY + Q_BOUNDARY
