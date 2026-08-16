"""Separate sequence-only continuity observations.

Revision ID: 20260816_52
Revises: 20260813_51
"""

from alembic import op
import sqlalchemy as sa


revision = "20260816_52"
down_revision = "20260813_51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v10', "
            "continuity_outcomes = '{}', continuity_issue_contexts = '{}', "
            "continuity_observations = '{}'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v9', "
            "continuity_outcomes = '{}', continuity_issue_contexts = '{}', "
            "continuity_observations = '{}'"
        )
    )
