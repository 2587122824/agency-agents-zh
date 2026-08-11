"""Persist exact editor continuity observation evidence.

Revision ID: 20260811_45
Revises: 20260811_44
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_45"
down_revision = "20260811_44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("editor_draft_sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "continuity_observations",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v3', "
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
            "SET schema_version = 'editor-draft-session.v2'"
        )
    )
    with op.batch_alter_table("editor_draft_sessions") as batch_op:
        batch_op.drop_column("continuity_observations")
