import math
import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from coe.db.models.fjsp import Operation
from coe.db.models.materials import Material, MaterialReceipt, OperationBom
from coe.db.models.provenance import ScenarioSource


def add_materials(
    session: Session,
    scenario_id: int,
    *,
    seed: int,
    n_materials: int = 8,
    receipt_at: int = 2000,
) -> dict:
    rng = random.Random(seed)
    op_ids = session.scalars(
        select(Operation.id).where(Operation.instance_id == scenario_id).order_by(Operation.id)
    ).all()

    materials = [
        Material(instance_id=scenario_id, sku=f"MAT-{k + 1:03d}", initial_stock=0)
        for k in range(n_materials)
    ]
    session.add_all(materials)
    session.flush()

    demand: dict[int, int] = defaultdict(int)
    n_bom = 0
    for op_id in op_ids:
        for m in rng.sample(materials, rng.randint(1, min(2, n_materials))):
            qty = rng.randint(1, 5)
            session.add(
                OperationBom(
                    instance_id=scenario_id,
                    operation_id=op_id,
                    material_id=m.id,
                    quantity_required=qty,
                )
            )
            demand[m.id] += qty
            n_bom += 1

    for m in materials:
        m.initial_stock = math.ceil(1.2 * demand[m.id])
        m.reorder_point = round(0.3 * m.initial_stock)
        for _ in range(2):
            session.add(
                MaterialReceipt(
                    instance_id=scenario_id,
                    material_id=m.id,
                    quantity=10,
                    available_at=receipt_at,
                    source="synthetic",
                )
            )

    session.add(
        ScenarioSource(
            scenario_id=scenario_id,
            source_instance_id=_self_id(session, scenario_id),
            contribution_type="materials_inventory",
            transformation_description=(
                f"{n_materials} SKUs, {n_bom} BOM rows, stock=1.2x demand "
                "(baseline unblocked), future receipts; synthetic"
            ),
            random_seed=seed,
        )
    )
    session.flush()
    return {"materials": n_materials, "bom_rows": n_bom}


def _self_id(session: Session, scenario_id: int) -> int:
    return scenario_id  # purely synthetic contribution attributes to itself
