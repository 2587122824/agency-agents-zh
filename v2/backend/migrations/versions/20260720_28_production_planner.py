"""Add production planning agent candidates.

Revision ID: 20260720_28
Revises: 20260720_27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_28"
down_revision = "20260720_27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_plan_candidates",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("project_id", sa.String(length=48), nullable=False),
        sa.Column("plan_version_id", sa.String(length=48), nullable=False),
        sa.Column("production_config_version_id", sa.String(length=48), nullable=False),
        sa.Column("video_spec_version_id", sa.String(length=48), nullable=False),
        sa.Column("agent_run_id", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("proposed_assignments", sa.JSON(), nullable=False),
        sa.Column("confirmed_assignments", sa.JSON(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["plan_version_id"], ["plan_versions.id"]),
        sa.ForeignKeyConstraint(["production_config_version_id"], ["production_config_versions.id"]),
        sa.ForeignKeyConstraint(["video_spec_version_id"], ["video_spec_versions.id"]),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id"),
    )
    for column in (
        "project_id", "plan_version_id", "production_config_version_id",
        "video_spec_version_id", "status",
    ):
        op.create_index(f"ix_production_plan_candidates_{column}", "production_plan_candidates", [column])


def downgrade() -> None:
    op.drop_table("production_plan_candidates")
