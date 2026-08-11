"""Freeze continuity review evidence on timeline revisions.

Revision ID: 20260811_44
Revises: 20260811_43
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_44"
down_revision = "20260811_43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("timelines") as batch_op:
        batch_op.add_column(
            sa.Column(
                "continuity_review",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column("continuity_review_hash", sa.String(length=64), nullable=True)
        )
        batch_op.create_index(
            "ix_timelines_continuity_review_hash",
            ["continuity_review_hash"],
            unique=False,
        )
    op.execute(
        sa.text(
            "UPDATE timelines SET continuity_review = '{}', continuity_review_hash = NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("timelines") as batch_op:
        batch_op.drop_index("ix_timelines_continuity_review_hash")
        batch_op.drop_column("continuity_review_hash")
        batch_op.drop_column("continuity_review")
