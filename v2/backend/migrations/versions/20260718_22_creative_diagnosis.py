"""Add structured creative diagnosis to conversation proposals.

Revision ID: 20260718_22
Revises: 20260718_21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260718_22"
down_revision = "20260718_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creative_turn_proposals") as batch:
        batch.add_column(sa.Column("creative_diagnosis", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("creative_turn_proposals") as batch:
        batch.drop_column("creative_diagnosis")
