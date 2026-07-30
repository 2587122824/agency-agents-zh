"""Persist mutable editor draft sessions independently from exports.

Revision ID: 20260730_42
Revises: 20260730_41
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_42"
down_revision = "20260730_41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "editor_draft_sessions",
        sa.Column("project_id", sa.String(length=48), nullable=False),
        sa.Column("snapshot_id", sa.String(length=48), nullable=False),
        sa.Column("base_timeline_id", sa.String(length=48), nullable=False),
        sa.Column("base_timeline_row_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("track_config", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("playhead_ms", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=48), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["base_timeline_id"], ["timelines.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["production_snapshots.id"]),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index(
        op.f("ix_editor_draft_sessions_snapshot_id"),
        "editor_draft_sessions",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_editor_draft_sessions_base_timeline_id"),
        "editor_draft_sessions",
        ["base_timeline_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_editor_draft_sessions_base_timeline_id"), table_name="editor_draft_sessions")
    op.drop_index(op.f("ix_editor_draft_sessions_snapshot_id"), table_name="editor_draft_sessions")
    op.drop_table("editor_draft_sessions")
