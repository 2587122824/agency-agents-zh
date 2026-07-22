"""Add explicit image production phase approval.

Revision ID: 20260722_30
Revises: 20260722_29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_30"
down_revision = "20260722_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("production_snapshots", sa.Column("image_phase_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("production_snapshots", sa.Column("image_phase_approval_manifest", sa.JSON(), nullable=True))
    op.add_column("production_snapshots", sa.Column("image_phase_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("production_snapshots", sa.Column("image_phase_approved_by", sa.String(length=48), nullable=True))

    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE production_snapshots
        SET image_phase_required = 1
        WHERE EXISTS (
            SELECT 1 FROM dag_nodes
            WHERE dag_nodes.snapshot_id = production_snapshots.id
              AND dag_nodes.kind = 'generate_keyframe'
        )
    """))
    connection.execute(sa.text("""
        UPDATE work_items
        SET status = 'waiting_phase'
        WHERE status = 'queued'
          AND kind != 'generate_keyframe'
          AND snapshot_id IN (
              SELECT id FROM production_snapshots WHERE image_phase_required = 1
          )
    """))


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE work_items SET status = 'queued' WHERE status = 'waiting_phase'"))
    op.drop_column("production_snapshots", "image_phase_approved_by")
    op.drop_column("production_snapshots", "image_phase_approved_at")
    op.drop_column("production_snapshots", "image_phase_approval_manifest")
    op.drop_column("production_snapshots", "image_phase_required")
