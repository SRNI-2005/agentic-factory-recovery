"""add streaming error status to recovery_runs

Revision ID: 440538e97415
Revises: 4a1da6fbad28
Create Date: 2026-08-27 00:22:32.611862

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '440538e97415'
down_revision: Union[str, Sequence[str], None] = '4a1da6fbad28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATUSES = (
    "'TRANSLATION_FAILED','SOLVE_INFEASIBLE',"
    "'GATE_FAILED','VERIFIER_ROLLBACK','COMMITTED'"
)
_NEW_STATUSES = (
    "'TRANSLATION_FAILED','SOLVE_INFEASIBLE',"
    "'GATE_FAILED','VERIFIER_ROLLBACK','COMMITTED','STREAMING_ERROR'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE recovery_runs DROP CONSTRAINT ck_recovery_runs_run_status")
    op.execute(
        f"ALTER TABLE recovery_runs ADD CONSTRAINT ck_recovery_runs_run_status "
        f"CHECK (status IN ({_NEW_STATUSES}))")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE recovery_runs DROP CONSTRAINT ck_recovery_runs_run_status")
    op.execute(
        f"ALTER TABLE recovery_runs ADD CONSTRAINT ck_recovery_runs_run_status "
        f"CHECK (status IN ({_OLD_STATUSES}))")
