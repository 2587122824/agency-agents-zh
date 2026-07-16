"""persist prospective decision change impact analyses

Revision ID: 20260716_12
Revises: 20260716_11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_12"
down_revision = "20260716_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_change_impact_analyses",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("decision_id", sa.String(48), sa.ForeignKey("decisions.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("current_value", sa.JSON(), nullable=True),
        sa.Column("proposed_value", sa.JSON(), nullable=True),
        sa.Column("observed_manifest_ids", sa.JSON(), nullable=False),
        sa.Column("target_counts", sa.JSON(), nullable=False),
        sa.Column("estimated_work_count", sa.Integer(), nullable=False),
        sa.Column("cost_status", sa.String(32), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(12), nullable=True),
        sa.Column("analysis_hash", sa.String(64), nullable=False),
        sa.Column("active_snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=True),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_change_impact_analyses_project_id", "decision_change_impact_analyses", ["project_id"])
    op.create_index("ix_decision_change_impact_analyses_decision_id", "decision_change_impact_analyses", ["decision_id"])
    op.create_index("ix_decision_change_impact_analyses_status", "decision_change_impact_analyses", ["status"])
    op.create_index("ix_decision_change_impact_analyses_analysis_hash", "decision_change_impact_analyses", ["analysis_hash"])
    op.create_index("ix_decision_change_impact_analyses_active_snapshot_id", "decision_change_impact_analyses", ["active_snapshot_id"])

    op.create_table(
        "decision_change_impact_targets",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("analysis_id", sa.String(48), sa.ForeignKey("decision_change_impact_analyses.id"), nullable=False),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("record_id", sa.String(48), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("record_status", sa.String(40), nullable=False),
        sa.Column("authority", sa.String(24), nullable=False),
        sa.Column("impact_kind", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("included_in_estimate", sa.Boolean(), nullable=False),
        sa.Column("estimated_work_units", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(12), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.UniqueConstraint("analysis_id", "record_type", "record_id"),
    )
    op.create_index("ix_decision_change_impact_targets_analysis_id", "decision_change_impact_targets", ["analysis_id"])
    op.create_index("ix_decision_change_impact_targets_record_type", "decision_change_impact_targets", ["record_type"])
    op.create_index("ix_decision_change_impact_targets_record_id", "decision_change_impact_targets", ["record_id"])


def downgrade() -> None:
    op.drop_index("ix_decision_change_impact_targets_record_id", table_name="decision_change_impact_targets")
    op.drop_index("ix_decision_change_impact_targets_record_type", table_name="decision_change_impact_targets")
    op.drop_index("ix_decision_change_impact_targets_analysis_id", table_name="decision_change_impact_targets")
    op.drop_table("decision_change_impact_targets")
    op.drop_index("ix_decision_change_impact_analyses_active_snapshot_id", table_name="decision_change_impact_analyses")
    op.drop_index("ix_decision_change_impact_analyses_analysis_hash", table_name="decision_change_impact_analyses")
    op.drop_index("ix_decision_change_impact_analyses_status", table_name="decision_change_impact_analyses")
    op.drop_index("ix_decision_change_impact_analyses_decision_id", table_name="decision_change_impact_analyses")
    op.drop_index("ix_decision_change_impact_analyses_project_id", table_name="decision_change_impact_analyses")
    op.drop_table("decision_change_impact_analyses")
