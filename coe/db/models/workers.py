from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class WorkerRole(Base):
    __tablename__ = "worker_roles"
    __table_args__ = (UniqueConstraint("id", "instance_id", name="uq_worker_roles_id_instance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    role_name: Mapped[str] = mapped_column(String(120))


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AVAILABLE','UNAVAILABLE')",
            name="worker_status",
        ),
        UniqueConstraint("id", "instance_id", name="uq_workers_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("worker_roles.id"))
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")


class OperationMachineWorkerTime(Base):
    """Authoritative worker eligibility: a missing row means 'cannot perform'."""

    __tablename__ = "operation_machine_worker_times"

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), primary_key=True
    )
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id"), primary_key=True
    )
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), primary_key=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), primary_key=True)
    processing_time: Mapped[int] = mapped_column(Integer)


class WorkerAvailabilityWindow(Base):
    __tablename__ = "worker_availability_windows"
    __table_args__ = (
        CheckConstraint("available_until >= available_from", name="window_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"))
    available_from: Mapped[int] = mapped_column(Integer)
    available_until: Mapped[int] = mapped_column(Integer)
    source_pattern: Mapped[str] = mapped_column(String(80))
