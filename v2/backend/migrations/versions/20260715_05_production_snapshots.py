"""production impact, immutable snapshots, and deterministic dag

Revision ID: 20260715_05
Revises: 20260715_04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_05"
down_revision = "20260715_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_impact_analyses",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("plan_version_id", sa.String(48), sa.ForeignKey("plan_versions.id"), nullable=False),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("analysis_hash", sa.String(64), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("execution_blockers", sa.JSON(), nullable=False),
        sa.Column("estimated_call_count", sa.Integer(), nullable=False),
        sa.Column("cost_status", sa.String(32), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(12), nullable=True),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("project_id", "plan_version_id", "production_config_version_id", "status", "analysis_hash"):
        op.create_index(f"ix_production_impact_analyses_{column}", "production_impact_analyses", [column])

    op.create_table(
        "production_snapshots",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("plan_version_id", sa.String(48), sa.ForeignKey("plan_versions.id"), nullable=False),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("impact_analysis_id", sa.String(48), sa.ForeignKey("production_impact_analyses.id"), nullable=False, unique=True),
        sa.Column("snapshot_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("audio_mode", sa.String(24), nullable=False),
        sa.Column("output_spec", sa.JSON(), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("contract", sa.JSON(), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("estimated_call_count", sa.Integer(), nullable=False),
        sa.Column("cost_status", sa.String(32), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(12), nullable=True),
        sa.Column("execution_blockers", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "snapshot_number"),
    )
    for column in ("project_id", "plan_version_id", "production_config_version_id", "status", "contract_hash"):
        op.create_index(f"ix_production_snapshots_{column}", "production_snapshots", [column])

    op.create_table(
        "snapshot_entity_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("entity_version_id", sa.String(48), sa.ForeignKey("entity_versions.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", "entity_version_id", "role"),
    )
    op.create_index("ix_snapshot_entity_versions_snapshot_id", "snapshot_entity_versions", ["snapshot_id"])
    op.create_index("ix_snapshot_entity_versions_entity_version_id", "snapshot_entity_versions", ["entity_version_id"])

    op.create_table(
        "dag_nodes",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("node_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("shot_id", sa.String(48), sa.ForeignKey("shots.id"), nullable=True),
        sa.Column("input_contract", sa.JSON(), nullable=False),
        sa.Column("output_contract", sa.JSON(), nullable=False),
        sa.Column("workflow_slot_version_id", sa.String(48), sa.ForeignKey("workflow_slot_versions.id"), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(12), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", "node_key"),
    )
    for column in ("snapshot_id", "kind", "shot_id"):
        op.create_index(f"ix_dag_nodes_{column}", "dag_nodes", [column])

    op.create_table(
        "dependency_edges",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("parent_node_id", sa.String(48), sa.ForeignKey("dag_nodes.id"), nullable=False),
        sa.Column("child_node_id", sa.String(48), sa.ForeignKey("dag_nodes.id"), nullable=False),
        sa.Column("dependency_type", sa.String(24), nullable=False),
        sa.Column("input_slot", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", "parent_node_id", "child_node_id", "input_slot"),
    )
    for column in ("snapshot_id", "parent_node_id", "child_node_id"):
        op.create_index(f"ix_dependency_edges_{column}", "dependency_edges", [column])


def downgrade() -> None:
    for table in ("dependency_edges", "dag_nodes", "snapshot_entity_versions", "production_snapshots", "production_impact_analyses"):
        op.drop_table(table)
