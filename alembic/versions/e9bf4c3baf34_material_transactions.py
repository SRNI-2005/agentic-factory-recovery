"""material_transactions

Revision ID: e9bf4c3baf34
Revises: 440538e97415
Create Date: 2026-09-12 03:34:00.353660

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9bf4c3baf34'
down_revision: Union[str, Sequence[str], None] = '440538e97415'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'material_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('instance_id', sa.Integer(), nullable=False),
        sa.Column('operation_id', sa.Integer(), nullable=True),
        sa.Column('material_id', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.Integer(), nullable=False),
        sa.Column('transaction_type', sa.String(length=12), nullable=False),
        sa.Column('source', sa.String(length=8), nullable=False),
        sa.CheckConstraint(
            "transaction_type IN ('CONSUME','REFILL','RESTOCK')",
            name=op.f('ck_material_transactions_mtx_type')),
        sa.CheckConstraint(
            'quantity > 0',
            name=op.f('ck_material_transactions_mtx_qty_pos')),
        sa.ForeignKeyConstraint(
            ['instance_id'], ['instances.id'],
            name=op.f('fk_material_transactions_instance_id_instances')),
        sa.ForeignKeyConstraint(
            ['operation_id'], ['operations.id'],
            name=op.f('fk_material_transactions_operation_id_operations')),
        sa.ForeignKeyConstraint(
            ['material_id'], ['materials.id'],
            name=op.f('fk_material_transactions_material_id_materials')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_material_transactions')),
    )
    op.create_index(
        op.f('ix_material_transactions_instance_id'),
        'material_transactions', ['instance_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_material_transactions_instance_id'),
        table_name='material_transactions')
    op.drop_table('material_transactions')
