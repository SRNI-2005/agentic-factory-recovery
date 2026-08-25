"""recovery run lifecycle tables

Revision ID: 4a1da6fbad28
Revises: 2818ae3709f8
Create Date: 2026-08-25 01:04:11.306534

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4a1da6fbad28'
down_revision: Union[str, Sequence[str], None] = '2818ae3709f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "recovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(),
                  sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("disruption_record_json", postgresql.JSONB(),
                  nullable=False),
        sa.Column("final_status_version_id", sa.Integer(),
                  sa.ForeignKey("schedule_versions.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True),
                  nullable=True),
        sa.Column("node_timings_json", postgresql.JSONB(), nullable=True),
        sa.Column("quantum_shadow_json", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("trigger IN ('CLI','MQTT')", name="run_trigger"),
        sa.CheckConstraint(
            "status IN ('TRANSLATION_FAILED','SOLVE_INFEASIBLE',"
            "'GATE_FAILED','VERIFIER_ROLLBACK','COMMITTED')",
            name="run_status"),
    )
    op.create_index("ix_recovery_runs_instance_id", "recovery_runs",
                    ["instance_id"])

    op.create_table(
        "recovery_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(),
                  sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("recovery_runs.id"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("candidate_json", postgresql.JSONB(), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("verdict_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "verdict IN ('VALID','VALID_WITH_WARNING','INVALID',"
            "'INVALID_DUPLICATE')",
            name="proposal_verdict"),
    )
    op.create_index("ix_recovery_proposals_instance_id",
                    "recovery_proposals", ["instance_id"])
    op.create_index("ix_recovery_proposals_run_id", "recovery_proposals",
                    ["run_id"])

    op.create_table(
        "schedule_explanations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(),
                  sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("version_id", sa.Integer(),
                  sa.ForeignKey("schedule_versions.id"), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("version_id", name="uq_explanation_version"),
    )
    op.create_index("ix_schedule_explanations_instance_id",
                    "schedule_explanations", ["instance_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("schedule_explanations")
    op.drop_table("recovery_proposals")
    op.drop_table("recovery_runs")
