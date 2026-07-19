"""Add quality-agent report candidates.

Revision ID: 20260719_25
Revises: 20260719_24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260719_25"
down_revision = "20260719_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qc_report_candidates",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("project_id", sa.String(length=48), nullable=False),
        sa.Column("snapshot_id", sa.String(length=48), nullable=False),
        sa.Column("asset_id", sa.String(length=48), nullable=False),
        sa.Column("agent_run_id", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("overall_recommendation", sa.String(length=32), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("analyzer_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["production_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id"),
    )
    op.create_index("ix_qc_report_candidates_project_id", "qc_report_candidates", ["project_id"])
    op.create_index("ix_qc_report_candidates_snapshot_id", "qc_report_candidates", ["snapshot_id"])
    op.create_index("ix_qc_report_candidates_asset_id", "qc_report_candidates", ["asset_id"])
    op.create_index("ix_qc_report_candidates_agent_run_id", "qc_report_candidates", ["agent_run_id"])
    op.create_index("ix_qc_report_candidates_status", "qc_report_candidates", ["status"])


def downgrade() -> None:
    op.drop_index("ix_qc_report_candidates_status", table_name="qc_report_candidates")
    op.drop_index("ix_qc_report_candidates_agent_run_id", table_name="qc_report_candidates")
    op.drop_index("ix_qc_report_candidates_asset_id", table_name="qc_report_candidates")
    op.drop_index("ix_qc_report_candidates_snapshot_id", table_name="qc_report_candidates")
    op.drop_index("ix_qc_report_candidates_project_id", table_name="qc_report_candidates")
    op.drop_table("qc_report_candidates")
