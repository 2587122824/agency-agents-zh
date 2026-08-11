"""Persist editor continuity review state in draft sessions.

Revision ID: 20260811_43
Revises: 20260730_42
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_43"
down_revision = "20260730_42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("editor_draft_sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "continuity_outcomes",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "continuity_issue_contexts",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v2', "
            "continuity_outcomes = '{}', continuity_issue_contexts = '{}'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE editor_draft_sessions "
            "SET schema_version = 'editor-draft-session.v1'"
        )
    )
    with op.batch_alter_table("editor_draft_sessions") as batch_op:
        batch_op.drop_column("continuity_issue_contexts")
        batch_op.drop_column("continuity_outcomes")
