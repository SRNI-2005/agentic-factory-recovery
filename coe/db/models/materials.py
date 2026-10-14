from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (
        CheckConstraint("initial_stock >= 0", name="stock_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    sku: Mapped[str] = mapped_column(String(80))
    initial_stock: Mapped[int] = mapped_column(Integer)
    reorder_point: Mapped[int | None]


class OperationBom(Base):
    __tablename__ = "operation_bom"
    __table_args__ = (
        CheckConstraint("quantity_required > 0", name="qty_positive"),
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), primary_key=True
    )
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id"), primary_key=True
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id"), primary_key=True
    )
    quantity_required: Mapped[int] = mapped_column(Integer)


class MaterialReceipt(Base):
    __tablename__ = "material_receipts"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND available_at >= 0", name="receipt_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40))


class MaterialTransaction(Base):
    """Runtime material audit ledger (Phase 1 spec §6.4, built by the day
    simulator amendment). Instance-scoped like every domain row."""
    __tablename__ = "material_transactions"
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('CONSUME','REFILL','RESTOCK')",
            name="mtx_type"),
        CheckConstraint("quantity > 0", name="mtx_qty_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id"))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[int] = mapped_column(Integer)
    transaction_type: Mapped[str] = mapped_column(String(12))
    source: Mapped[str] = mapped_column(String(8))
