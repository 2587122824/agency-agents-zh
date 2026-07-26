"""Persist immutable CosyVoice paid validation evidence.

Revision ID: 20260726_38
Revises: 20260726_37
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_38"
down_revision = "20260726_37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cosyvoice_validation_runs",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column(
            "production_config_version_id",
            sa.String(length=48),
            sa.ForeignKey("production_config_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "provider_config_version_id",
            sa.String(length=48),
            sa.ForeignKey("provider_config_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_slot_version_id",
            sa.String(length=48),
            sa.ForeignKey("workflow_slot_versions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("network_probe_performed", sa.Boolean(), nullable=False),
        sa.Column("validation_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_text_character_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=200), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_cosyvoice_validation_runs_production_config_version_id",
        "cosyvoice_validation_runs",
        ["production_config_version_id"],
    )
    op.create_index(
        "ix_cosyvoice_validation_runs_provider_config_version_id",
        "cosyvoice_validation_runs",
        ["provider_config_version_id"],
    )
    op.create_index(
        "ix_cosyvoice_validation_runs_workflow_slot_version_id",
        "cosyvoice_validation_runs",
        ["workflow_slot_version_id"],
    )
    op.create_index(
        "ix_cosyvoice_validation_runs_status",
        "cosyvoice_validation_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_cosyvoice_validation_runs_status", table_name="cosyvoice_validation_runs")
    op.drop_index(
        "ix_cosyvoice_validation_runs_workflow_slot_version_id",
        table_name="cosyvoice_validation_runs",
    )
    op.drop_index(
        "ix_cosyvoice_validation_runs_provider_config_version_id",
        table_name="cosyvoice_validation_runs",
    )
    op.drop_index(
        "ix_cosyvoice_validation_runs_production_config_version_id",
        table_name="cosyvoice_validation_runs",
    )
    op.drop_table("cosyvoice_validation_runs")
