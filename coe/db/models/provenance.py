from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coe.db.base import Base


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    source_name: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_version: Mapped[str | None] = mapped_column(String(80))
    source_license: Mapped[str | None] = mapped_column(String(200))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    source_time_unit: Mapped[str] = mapped_column(String(40), default="minute")
    time_scale_to_minutes: Mapped[float] = mapped_column(default=1.0)
    normalized_time_unit: Mapped[str] = mapped_column(String(20), default="minute")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScenarioSource(Base):
    __tablename__ = "scenario_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"))
    contribution_type: Mapped[str] = mapped_column(String(60))
    transformation_description: Mapped[str] = mapped_column(Text)
    random_seed: Mapped[int | None]

    scenario: Mapped[Instance] = relationship(foreign_keys=[scenario_id])
    source_instance: Mapped[Instance] = relationship(foreign_keys=[source_instance_id])


class InstanceProfile(Base):
    __tablename__ = "instance_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    profile_type: Mapped[str] = mapped_column(String(60))
    parameters_json: Mapped[dict] = mapped_column(JSONB)
    source_instance_id: Mapped[int | None] = mapped_column(ForeignKey("instances.id"))
