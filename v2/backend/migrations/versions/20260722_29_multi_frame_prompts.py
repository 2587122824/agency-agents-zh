"""Add explicit multi-frame guide prompts.

Revision ID: 20260722_29
Revises: 20260720_28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_29"
down_revision = "20260720_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shots", sa.Column("guide_frame_prompts", sa.JSON(), nullable=True))
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE plan_versions SET contract_schema_version = 'shot-plan.v4' "
        "WHERE contract_schema_version = 'shot-plan.v3'"
    ))


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE plan_versions SET contract_schema_version = 'shot-plan.v3' "
        "WHERE contract_schema_version = 'shot-plan.v4'"
    ))
    op.drop_column("shots", "guide_frame_prompts")
