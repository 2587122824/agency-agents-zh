"""Persist editor alternative review outcomes.

Revision ID: 20260813_51
Revises: 20260813_50
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_51"
down_revision = "20260813_50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v9', candidate_review_sessions = '{}'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v8', candidate_review_sessions = '{}'"
        )
    )
