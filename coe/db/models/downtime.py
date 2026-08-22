from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        CheckConstraint("occurred_at >= 0", name="occurred_nonnegative"),
        CheckConstraint("received_at >= 0", name="received_nonnegative"),
    )

    # Composite PK (id, occurred_at) satisfies the TimescaleDB rule that every
    # unique index includes the partitioning column; Identity() keeps the id
    # auto-incrementing despite the composite key.
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    occurred_at: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(160))
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    received_at: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str | None] = mapped_column(String(20))
    estimated_downtime: Mapped[int | None]
    processed_at: Mapped[int | None]
    processing_error: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB)


Index("ix_telemetry_message_id", TelemetryEvent.message_id)


class MachineDowntimeWindow(Base):
    __tablename__ = "machine_downtime_windows"
    __table_args__ = (
        CheckConstraint(
            "downtime_until IS NULL OR downtime_until > downtime_from",
            name="downtime_interval_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    downtime_from: Mapped[int] = mapped_column(Integer)
    downtime_until: Mapped[int | None]
    reason: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str | None] = mapped_column(String(20))
    source_event_ids: Mapped[list] = mapped_column(JSONB, default=list)
