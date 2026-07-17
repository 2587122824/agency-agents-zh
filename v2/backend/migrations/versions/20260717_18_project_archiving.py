"""add reversible project archiving metadata

Revision ID: 20260717_18
Revises: 20260717_17
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260717_18"
down_revision: str | None = "20260717_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("archived_by", sa.String(length=80), nullable=True))
        batch.create_index("ix_projects_archived_at", ["archived_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_index("ix_projects_archived_at")
        batch.drop_column("archived_by")
        batch.drop_column("archived_at")
