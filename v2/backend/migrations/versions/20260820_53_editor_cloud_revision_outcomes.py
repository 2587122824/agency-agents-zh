"""Add an explicit adopted outcome for cloud revision A/B review.

Revision ID: 20260820_53
Revises: 20260816_52
"""

from alembic import op
import sqlalchemy as sa


revision = "20260820_53"
down_revision = "20260816_52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v11', "
            "candidate_review_sessions = '{}'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v10', "
            "candidate_review_sessions = '{}'"
        )
    )
