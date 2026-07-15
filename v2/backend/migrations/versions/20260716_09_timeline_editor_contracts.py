"""timeline editor contracts

Revision ID: 20260716_09
Revises: 20260716_08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_09"
down_revision = "20260716_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "timelines",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_timeline_id", sa.String(48), sa.ForeignKey("timelines.id"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_agent_run_id", sa.String(48), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("output_spec", sa.JSON(), nullable=False),
        sa.Column("track_config", sa.JSON(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "version_number"),
    )
    op.create_index("ix_timelines_project_id", "timelines", ["project_id"])
    op.create_index("ix_timelines_snapshot_id", "timelines", ["snapshot_id"])
    op.create_index("ix_timelines_supersedes_timeline_id", "timelines", ["supersedes_timeline_id"])
    op.create_index("ix_timelines_status", "timelines", ["status"])
    op.create_index("ix_timelines_source_agent_run_id", "timelines", ["source_agent_run_id"])
    op.create_index("ix_timelines_contract_hash", "timelines", ["contract_hash"])

    op.create_table(
        "timeline_items",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("timeline_id", sa.String(48), sa.ForeignKey("timelines.id"), nullable=False),
        sa.Column("track_type", sa.String(24), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.String(48), sa.ForeignKey("assets.id"), nullable=True),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("gap_reason", sa.String(500), nullable=True),
        sa.Column("source_in_ms", sa.Integer(), nullable=True),
        sa.Column("source_out_ms", sa.Integer(), nullable=True),
        sa.Column("timeline_in_ms", sa.Integer(), nullable=False),
        sa.Column("timeline_out_ms", sa.Integer(), nullable=False),
        sa.Column("transform", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("timeline_id", "track_type", "sequence_number"),
    )
    op.create_index("ix_timeline_items_timeline_id", "timeline_items", ["timeline_id"])
    op.create_index("ix_timeline_items_track_type", "timeline_items", ["track_type"])
    op.create_index("ix_timeline_items_asset_id", "timeline_items", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_timeline_items_asset_id", table_name="timeline_items")
    op.drop_index("ix_timeline_items_track_type", table_name="timeline_items")
    op.drop_index("ix_timeline_items_timeline_id", table_name="timeline_items")
    op.drop_table("timeline_items")
    op.drop_index("ix_timelines_contract_hash", table_name="timelines")
    op.drop_index("ix_timelines_status", table_name="timelines")
    op.drop_index("ix_timelines_source_agent_run_id", table_name="timelines")
    op.drop_index("ix_timelines_supersedes_timeline_id", table_name="timelines")
    op.drop_index("ix_timelines_snapshot_id", table_name="timelines")
    op.drop_index("ix_timelines_project_id", table_name="timelines")
    op.drop_table("timelines")
