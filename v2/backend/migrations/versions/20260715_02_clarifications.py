"""add clarification contracts

Revision ID: 20260715_02
Revises: 20260715_01
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_02"
down_revision = "20260715_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("clarification_requests")}
    if "base_requirement_version_id" not in columns:
        op.add_column("clarification_requests", sa.Column("base_requirement_version_id", sa.String(48), nullable=True))
        op.create_index(
            "ix_clarification_requests_base_requirement_version_id",
            "clarification_requests",
            ["base_requirement_version_id"],
        )
    if "reason_code" not in columns:
        op.add_column("clarification_requests", sa.Column("reason_code", sa.String(80), nullable=True))
    if "options" not in columns:
        op.add_column("clarification_requests", sa.Column("options", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clarification_requests") as batch:
        batch.drop_column("options")
        batch.drop_column("reason_code")
        batch.drop_index("ix_clarification_requests_base_requirement_version_id")
        batch.drop_column("base_requirement_version_id")
