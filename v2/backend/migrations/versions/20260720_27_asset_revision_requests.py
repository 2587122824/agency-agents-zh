"""Add explicit asset revision requests.

Revision ID: 20260720_27
Revises: 20260720_26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_27"
down_revision = "20260720_26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_revision_requests",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("project_id", sa.String(length=48), nullable=False),
        sa.Column("asset_id", sa.String(length=48), nullable=False),
        sa.Column("snapshot_id", sa.String(length=48), nullable=False),
        sa.Column("plan_version_id", sa.String(length=48), nullable=False),
        sa.Column("shot_id", sa.String(length=48), nullable=True),
        sa.Column("shot_code", sa.String(length=32), nullable=True),
        sa.Column("issue_scope", sa.String(length=24), nullable=False),
        sa.Column("rationale", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_asset_state", sa.String(length=32), nullable=False),
        sa.Column("source_asset_row_version", sa.Integer(), nullable=False),
        sa.Column("affected_downstream_node_keys", sa.JSON(), nullable=False),
        sa.Column("draft_candidate_id", sa.String(length=48), nullable=True),
        sa.Column("resulting_candidate_id", sa.String(length=48), nullable=True),
        sa.Column("resulting_plan_version_id", sa.String(length=48), nullable=True),
        sa.Column("created_by", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["production_snapshots.id"]),
        sa.ForeignKeyConstraint(["plan_version_id"], ["plan_versions.id"]),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"]),
        sa.ForeignKeyConstraint(["draft_candidate_id"], ["shot_plan_candidates.id"]),
        sa.ForeignKeyConstraint(["resulting_candidate_id"], ["shot_plan_candidates.id"]),
        sa.ForeignKeyConstraint(["resulting_plan_version_id"], ["plan_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "project_id", "asset_id", "snapshot_id", "plan_version_id", "shot_id", "issue_scope",
        "status", "draft_candidate_id", "resulting_candidate_id", "resulting_plan_version_id",
    ):
        op.create_index(f"ix_asset_revision_requests_{column}", "asset_revision_requests", [column])


def downgrade() -> None:
    op.drop_table("asset_revision_requests")
