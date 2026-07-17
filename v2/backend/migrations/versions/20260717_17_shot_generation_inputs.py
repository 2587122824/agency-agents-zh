"""add explicit shot generation inputs

Revision ID: 20260717_17
Revises: 20260717_16
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260717_17"
down_revision: str | None = "20260717_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shots") as batch:
        batch.add_column(sa.Column("product_entity_version_ids", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("primary_reference_entity_version_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("visual_prompt", sa.Text(), nullable=True))
        batch.add_column(sa.Column("negative_prompt", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_shots_primary_reference_entity_version_id",
            "entity_versions",
            ["primary_reference_entity_version_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("shots") as batch:
        batch.drop_constraint("fk_shots_primary_reference_entity_version_id", type_="foreignkey")
        batch.drop_column("negative_prompt")
        batch.drop_column("visual_prompt")
        batch.drop_column("primary_reference_entity_version_id")
        batch.drop_column("product_entity_version_ids")
