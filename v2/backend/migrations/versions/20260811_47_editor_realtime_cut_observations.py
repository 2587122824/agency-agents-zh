"""Require real-time sequential cut observation evidence.

Revision ID: 20260811_47
Revises: 20260811_46
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_47"
down_revision = "20260811_46"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v5', "
            "continuity_outcomes = '{}', continuity_issue_contexts = '{}', "
            "continuity_observations = '{}'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE timelines SET continuity_review = '{}', continuity_review_hash = NULL "
            "WHERE continuity_review_hash IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v4', "
            "continuity_outcomes = '{}', continuity_issue_contexts = '{}', "
            "continuity_observations = '{}'"
        )
    )
