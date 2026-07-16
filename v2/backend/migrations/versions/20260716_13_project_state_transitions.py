"""add authoritative project state transition fields

Revision ID: 20260716_13
Revises: 20260716_12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_13"
down_revision = "20260716_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("state_actor_type", sa.String(16), nullable=False, server_default="system"))
        batch.add_column(sa.Column("state_changed_by", sa.String(80), nullable=False, server_default="migration"))
        batch.add_column(sa.Column("state_trigger", sa.String(80), nullable=False, server_default="migration_backfill"))
        batch.add_column(sa.Column("state_reason_code", sa.String(80), nullable=True))
        batch.add_column(sa.Column("blocked_from_state", sa.String(32), nullable=True))
        batch.add_column(sa.Column("blocked_responsible_aggregate_type", sa.String(40), nullable=True))
        batch.add_column(sa.Column("blocked_responsible_aggregate_id", sa.String(48), nullable=True))
        batch.add_column(sa.Column("blocked_allowed_commands", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE projects SET state_changed_at = updated_at WHERE state_changed_at IS NULL")
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("state_changed_at", existing_type=sa.DateTime(timezone=True), nullable=False)

    op.execute(
        """
        UPDATE projects
        SET status = 'contract_ready', state_trigger = 'migration_authority_backfill'
        WHERE status NOT IN ('blocked', 'completed', 'cancelled', 'confirmed', 'queued', 'review_required')
          AND EXISTS (
              SELECT 1 FROM plan_versions p
              WHERE p.project_id = projects.id AND p.is_active = 1 AND p.status = 'confirmed'
          )
        """
    )
    op.execute(
        """
        UPDATE projects
        SET status = 'production_ready', state_trigger = 'migration_authority_backfill'
        WHERE status NOT IN ('blocked', 'completed', 'cancelled')
          AND active_snapshot_id IN (
              SELECT id FROM production_snapshots WHERE status IN ('locked', 'active')
          )
        """
    )
    op.execute(
        """
        UPDATE projects
        SET status = 'producing', state_trigger = 'migration_authority_backfill'
        WHERE status NOT IN ('blocked', 'completed', 'cancelled')
          AND active_snapshot_id IN (
              SELECT id FROM production_snapshots WHERE status = 'submitted'
          )
        """
    )
    op.execute(
        """
        UPDATE projects
        SET status = 'quality_review', state_trigger = 'migration_authority_backfill'
        WHERE status NOT IN ('blocked', 'completed', 'cancelled')
          AND active_snapshot_id IN (
              SELECT id FROM production_snapshots WHERE status = 'execution_completed'
          )
        """
    )
    op.execute(
        """
        UPDATE projects
        SET status = 'editing', state_trigger = 'migration_authority_backfill'
        WHERE status NOT IN ('blocked', 'completed', 'cancelled')
          AND EXISTS (
              SELECT 1 FROM timelines t
              WHERE t.project_id = projects.id AND t.status IN ('candidate', 'review')
          )
        """
    )
    op.execute(
        """
        UPDATE projects
        SET status = 'delivery_ready', state_trigger = 'migration_authority_backfill'
        WHERE status NOT IN ('blocked', 'completed', 'cancelled')
          AND EXISTS (
              SELECT 1 FROM timelines t
              WHERE t.project_id = projects.id AND t.status IN ('confirmed', 'exported')
          )
        """
    )
    op.execute(
        """
        UPDATE projects
        SET status = 'completed', state_trigger = 'migration_authority_backfill'
        WHERE delivery_asset_id IS NOT NULL
        """
    )

    op.execute(
        """
        UPDATE projects
        SET blocked_from_state = CASE
                WHEN EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.project_id = projects.id AND d.status = 'blocked') THEN 'delivery_ready'
                WHEN EXISTS (SELECT 1 FROM production_snapshots s WHERE s.project_id = projects.id AND s.status = 'execution_blocked') THEN 'producing'
                ELSE 'quality_review'
            END,
            state_reason_code = 'MIGRATED_EXISTING_BLOCK',
            blocked_responsible_aggregate_type = 'project',
            blocked_responsible_aggregate_id = id,
            blocked_at = updated_at
        WHERE status = 'blocked'
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("blocked_at")
        batch.drop_column("blocked_allowed_commands")
        batch.drop_column("blocked_responsible_aggregate_id")
        batch.drop_column("blocked_responsible_aggregate_type")
        batch.drop_column("blocked_from_state")
        batch.drop_column("state_reason_code")
        batch.drop_column("state_trigger")
        batch.drop_column("state_changed_by")
        batch.drop_column("state_actor_type")
        batch.drop_column("state_changed_at")
        batch.drop_column("row_version")
