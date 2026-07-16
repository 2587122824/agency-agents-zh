"""backfill project planning states from persisted candidate authority

Revision ID: 20260716_14
Revises: 20260716_13
"""

from alembic import op


revision = "20260716_14"
down_revision = "20260716_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE projects
        SET status = 'planning',
            row_version = row_version + 1,
            state_changed_at = CURRENT_TIMESTAMP,
            state_actor_type = 'system',
            state_changed_by = 'migration',
            state_trigger = 'migration_planning_authority_backfill'
        WHERE status IN ('draft', 'collecting_requirements', 'planning')
          AND EXISTS (
              SELECT 1 FROM creative_brief_candidates b
              WHERE b.project_id = projects.id AND b.status = 'accepted'
          )
        """
    )
    op.execute(
        """
        UPDATE projects
        SET status = 'plan_review',
            row_version = row_version + 1,
            state_changed_at = CURRENT_TIMESTAMP,
            state_actor_type = 'system',
            state_changed_by = 'migration',
            state_trigger = 'migration_planning_authority_backfill'
        WHERE status IN ('draft', 'collecting_requirements', 'planning', 'plan_review')
          AND (
              EXISTS (
                  SELECT 1 FROM shot_plan_candidates s
                  WHERE s.project_id = projects.id AND s.status = 'awaiting_review'
              )
              OR EXISTS (
                  SELECT 1 FROM creative_brief_candidates b
                  WHERE b.project_id = projects.id AND b.status = 'awaiting_review'
              )
          )
        """
    )


def downgrade() -> None:
    # The prior ambiguous status cannot be reconstructed without inventing history.
    pass
