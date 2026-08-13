"""Persist editor boundary candidate review sessions.

Revision ID: 20260813_50
Revises: 20260811_49
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_50"
down_revision = "20260811_49"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "editor_draft_sessions",
        sa.Column("candidate_review_sessions", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v8', candidate_review_sessions = '{}'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v7'"
        )
    )
    with op.batch_alter_table("editor_draft_sessions") as batch_op:
        batch_op.drop_column("candidate_review_sessions")
