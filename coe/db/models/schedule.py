from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class ScheduleVersion(Base):
    __tablename__ = "schedule_versions"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('BASELINE','RECOVERY')", name="schedule_version_type"
        ),
        CheckConstraint(
            "solver_status IN ('OPTIMAL','FEASIBLE','INFEASIBLE')",
            name="schedule_version_status",
        ),
        UniqueConstraint("instance_id", "version_number",
                         name="uq_schedule_versions_instance_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    schedule_type: Mapped[str] = mapped_column(String(20))
    solver_status: Mapped[str] = mapped_column(String(20))
    objective_value: Mapped[float] = mapped_column(Float)
    makespan: Mapped[int]
    total_tardiness: Mapped[int]
    alpha_weight: Mapped[float] = mapped_column(Float)
    beta_weight: Mapped[float] = mapped_column(Float)
    time_limit_seconds: Mapped[int]
    solve_duration_seconds: Mapped[float] = mapped_column(Float)
    failed_machine_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True
    )
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSONB)


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"
    __table_args__ = (
        CheckConstraint("status IN ('SCHEDULED','FROZEN')", name="entry_status"),
        CheckConstraint("end_time >= start_time", name="entry_interval_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_versions.id"), index=True
    )
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    start_time: Mapped[int] = mapped_column(Integer)
    end_time: Mapped[int] = mapped_column(Integer)
    processing_time: Mapped[int] = mapped_column(Integer)
    setup_time: Mapped[int] = mapped_column(Integer, default=0)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
