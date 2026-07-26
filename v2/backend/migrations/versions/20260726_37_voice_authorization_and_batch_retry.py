"""Add voice authorization versions and production retry batches.

Revision ID: 20260726_37
Revises: 20260726_36
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_37"
down_revision = "20260726_36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_clone_authorization_versions",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("project_id", sa.String(length=48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("authorization_key", sa.String(length=80), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_version_id", sa.String(length=48), sa.ForeignKey("voice_clone_authorization_versions.id"), nullable=True),
        sa.Column("sample_asset_id", sa.String(length=48), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("sample_content_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_name", sa.String(length=160), nullable=False),
        sa.Column("provider_voice_id", sa.String(length=160), nullable=False),
        sa.Column("authorization_basis", sa.String(length=32), nullable=False),
        sa.Column("authorization_scope", sa.JSON(), nullable=False),
        sa.Column("consent_evidence", sa.Text(), nullable=False),
        sa.Column("authorized_by", sa.String(length=160), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "authorization_key", "version_number"),
        sa.UniqueConstraint("provider_voice_id"),
        sa.UniqueConstraint("contract_hash"),
    )
    op.create_index("ix_voice_clone_authorization_versions_project_id", "voice_clone_authorization_versions", ["project_id"])
    op.create_index("ix_voice_clone_authorization_versions_authorization_key", "voice_clone_authorization_versions", ["authorization_key"])
    op.create_index("ix_voice_clone_authorization_versions_sample_asset_id", "voice_clone_authorization_versions", ["sample_asset_id"])
    op.create_index("ix_voice_clone_authorization_versions_status", "voice_clone_authorization_versions", ["status"])
    op.create_table(
        "production_retry_batches",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("project_id", sa.String(length=48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("snapshot_id", sa.String(length=48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("root_work_item_ids", sa.JSON(), nullable=False),
        sa.Column("retry_work_item_ids", sa.JSON(), nullable=False),
        sa.Column("affected_node_ids", sa.JSON(), nullable=False),
        sa.Column("preserved_asset_ids", sa.JSON(), nullable=False),
        sa.Column("request_fingerprints", sa.JSON(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("analysis_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("analysis_hash"),
    )
    op.create_index("ix_production_retry_batches_project_id", "production_retry_batches", ["project_id"])
    op.create_index("ix_production_retry_batches_snapshot_id", "production_retry_batches", ["snapshot_id"])
    op.create_index("ix_production_retry_batches_status", "production_retry_batches", ["status"])


def downgrade() -> None:
    op.drop_index("ix_production_retry_batches_status", table_name="production_retry_batches")
    op.drop_index("ix_production_retry_batches_snapshot_id", table_name="production_retry_batches")
    op.drop_index("ix_production_retry_batches_project_id", table_name="production_retry_batches")
    op.drop_table("production_retry_batches")
    op.drop_index("ix_voice_clone_authorization_versions_status", table_name="voice_clone_authorization_versions")
    op.drop_index("ix_voice_clone_authorization_versions_sample_asset_id", table_name="voice_clone_authorization_versions")
    op.drop_index("ix_voice_clone_authorization_versions_authorization_key", table_name="voice_clone_authorization_versions")
    op.drop_index("ix_voice_clone_authorization_versions_project_id", table_name="voice_clone_authorization_versions")
    op.drop_table("voice_clone_authorization_versions")
