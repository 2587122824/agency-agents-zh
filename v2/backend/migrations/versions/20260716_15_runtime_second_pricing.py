"""add explicit runtime-second pricing estimate

Revision ID: 20260716_15
Revises: 20260716_14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_15"
down_revision = "20260716_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pricing_rules",
        sa.Column("estimated_runtime_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pricing_rules", "estimated_runtime_seconds")
