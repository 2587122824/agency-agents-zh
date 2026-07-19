"""add content brief revisions and reopen rejected requirements

Revision ID: 20260719_24
Revises: 20260718_23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_24"
down_revision = "20260718_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creative_brief_candidates") as batch:
        batch.add_column(sa.Column("supersedes_candidate_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("source", sa.String(length=32), nullable=False, server_default="planner_agent"))
        batch.add_column(sa.Column("created_by", sa.String(length=48), nullable=False, server_default="system"))
        batch.create_index("ix_creative_brief_candidates_supersedes_candidate_id", ["supersedes_candidate_id"])
        batch.create_foreign_key(
            "fk_creative_brief_candidates_supersedes",
            "creative_brief_candidates",
            ["supersedes_candidate_id"],
            ["id"],
        )

    op.execute(
        """
        UPDATE projects
        SET status = 'collecting_requirements',
            row_version = row_version + 1,
            state_changed_at = CURRENT_TIMESTAMP,
            state_actor_type = 'system',
            state_changed_by = 'migration',
            state_trigger = 'migration_rejected_brief_revision_backfill'
        WHERE status = 'planning'
          AND EXISTS (
              SELECT 1
              FROM creative_brief_candidates b
              JOIN requirement_versions r ON r.id = b.requirement_version_id
              WHERE b.project_id = projects.id
                AND r.project_id = projects.id
                AND r.is_active = 1
                AND b.status = 'rejected'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM creative_brief_candidates b
              JOIN requirement_versions r ON r.id = b.requirement_version_id
              WHERE b.project_id = projects.id
                AND r.project_id = projects.id
                AND r.is_active = 1
                AND b.status IN ('awaiting_review', 'accepted')
          )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("creative_brief_candidates") as batch:
        batch.drop_constraint("fk_creative_brief_candidates_supersedes", type_="foreignkey")
        batch.drop_index("ix_creative_brief_candidates_supersedes_candidate_id")
        batch.drop_column("created_by")
        batch.drop_column("source")
        batch.drop_column("revision_number")
        batch.drop_column("supersedes_candidate_id")

    # The prior planning state cannot be reconstructed without inventing user intent.
