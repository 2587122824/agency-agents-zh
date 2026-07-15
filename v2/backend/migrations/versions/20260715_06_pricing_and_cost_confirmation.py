"""versioned pricing and cost confirmation

Revision ID: 20260715_06
Revises: 20260715_05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_06"
down_revision = "20260715_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pricing_catalog_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("catalog_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("currency", sa.String(12), nullable=False),
        sa.Column("confirmation_threshold", sa.Float(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("catalog_key", "version_number"),
    )
    for column in ("production_config_version_id", "catalog_key", "status"):
        op.create_index(f"ix_pricing_catalog_versions_{column}", "pricing_catalog_versions", [column])

    op.create_table(
        "pricing_rules",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("pricing_catalog_version_id", sa.String(48), sa.ForeignKey("pricing_catalog_versions.id"), nullable=False),
        sa.Column("provider_config_version_id", sa.String(48), sa.ForeignKey("provider_config_versions.id"), nullable=False),
        sa.Column("workflow_slot_version_id", sa.String(48), sa.ForeignKey("workflow_slot_versions.id"), nullable=False),
        sa.Column("operation_kind", sa.String(80), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("minimum_charge", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pricing_catalog_version_id", "workflow_slot_version_id"),
    )
    for column in ("pricing_catalog_version_id", "provider_config_version_id", "workflow_slot_version_id"):
        op.create_index(f"ix_pricing_rules_{column}", "pricing_rules", [column])

    with op.batch_alter_table("dag_nodes") as batch:
        batch.add_column(sa.Column("pricing_rule_id", sa.String(48), nullable=True))
        batch.add_column(sa.Column("pricing_quantity", sa.Float(), nullable=True))
        batch.add_column(sa.Column("pricing_unit", sa.String(32), nullable=True))
        batch.create_foreign_key("fk_dag_nodes_pricing_rule", "pricing_rules", ["pricing_rule_id"], ["id"])

    with op.batch_alter_table("production_snapshots") as batch:
        batch.add_column(sa.Column("pricing_catalog_version_id", sa.String(48), nullable=True))
        batch.create_foreign_key("fk_production_snapshots_pricing_catalog", "pricing_catalog_versions", ["pricing_catalog_version_id"], ["id"])
        batch.create_index("ix_production_snapshots_pricing_catalog_version_id", ["pricing_catalog_version_id"])

    with op.batch_alter_table("production_impact_analyses") as batch:
        batch.add_column(sa.Column("pricing_catalog_version_id", sa.String(48), nullable=True))
        batch.create_foreign_key("fk_production_impact_pricing_catalog", "pricing_catalog_versions", ["pricing_catalog_version_id"], ["id"])
        batch.create_index("ix_production_impact_analyses_pricing_catalog_version_id", ["pricing_catalog_version_id"])

    op.create_table(
        "cost_events",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("work_attempt_id", sa.String(48), nullable=True),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("provider_operation", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(12), nullable=False),
        sa.Column("provider_reference", sa.String(160), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cost_events_project_id", "cost_events", ["project_id"])
    op.create_index("ix_cost_events_snapshot_id", "cost_events", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("cost_events")
    with op.batch_alter_table("production_impact_analyses") as batch:
        batch.drop_index("ix_production_impact_analyses_pricing_catalog_version_id")
        batch.drop_constraint("fk_production_impact_pricing_catalog", type_="foreignkey")
        batch.drop_column("pricing_catalog_version_id")
    with op.batch_alter_table("production_snapshots") as batch:
        batch.drop_index("ix_production_snapshots_pricing_catalog_version_id")
        batch.drop_constraint("fk_production_snapshots_pricing_catalog", type_="foreignkey")
        batch.drop_column("pricing_catalog_version_id")
    with op.batch_alter_table("dag_nodes") as batch:
        batch.drop_constraint("fk_dag_nodes_pricing_rule", type_="foreignkey")
        batch.drop_column("pricing_unit")
        batch.drop_column("pricing_quantity")
        batch.drop_column("pricing_rule_id")
    op.drop_table("pricing_rules")
    op.drop_table("pricing_catalog_versions")
