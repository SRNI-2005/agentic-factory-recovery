"""Phase 3 run lifecycle tables (spec §7)."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class RecoveryRun(Base):
    __tablename__ = "recovery_runs"
    __table_args__ = (
        CheckConstraint("trigger IN ('CLI','MQTT')", name="run_trigger"),
        CheckConstraint(
            "status IN ('TRANSLATION_FAILED','SOLVE_INFEASIBLE',"
            "'GATE_FAILED','VERIFIER_ROLLBACK','COMMITTED')",
            name="run_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30))
    disruption_record_json: Mapped[dict] = mapped_column(JSONB)
    final_status_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    node_timings_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    quantum_shadow_json: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True)


class RecoveryProposal(Base):
    __tablename__ = "recovery_proposals"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('VALID','VALID_WITH_WARNING','INVALID',"
            "'INVALID_DUPLICATE')",
            name="proposal_verdict"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), index=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("recovery_runs.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    candidate_json: Mapped[dict] = mapped_column(JSONB)
    verdict: Mapped[str] = mapped_column(String(20))
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduleExplanation(Base):
    __tablename__ = "schedule_explanations"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_explanation_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), index=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_versions.id"))
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
