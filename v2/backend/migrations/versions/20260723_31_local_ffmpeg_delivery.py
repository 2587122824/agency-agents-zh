"""add local ffmpeg delivery execution fields

Revision ID: 20260723_31
Revises: 20260722_30
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_31"
down_revision = "20260722_30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("delivery_attempts") as batch:
        batch.add_column(sa.Column("work_item_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("render_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("render_finished_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_delivery_attempts_work_item_id_work_items",
            "work_items",
            ["work_item_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_delivery_attempts_work_item_id",
            ["work_item_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("delivery_attempts") as batch:
        batch.drop_constraint("uq_delivery_attempts_work_item_id", type_="unique")
        batch.drop_constraint("fk_delivery_attempts_work_item_id_work_items", type_="foreignkey")
        batch.drop_column("render_finished_at")
        batch.drop_column("render_started_at")
        batch.drop_column("work_item_id")
