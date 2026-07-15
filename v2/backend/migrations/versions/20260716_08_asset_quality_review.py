"""asset lifecycle and quality review

Revision ID: 20260716_08
Revises: 20260715_07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_08"
down_revision = "20260715_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("work_attempt_id", sa.String(48), sa.ForeignKey("work_attempts.id"), nullable=True),
        sa.Column("dag_node_id", sa.String(48), sa.ForeignKey("dag_nodes.id"), nullable=True),
        sa.Column("output_index", sa.Integer(), nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("uri", sa.String(500), nullable=False),
        sa.Column("storage_backend", sa.String(40), nullable=False),
        sa.Column("provider_output_manifest", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("mime_type", sa.String(120), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("work_attempt_id", "output_index"),
        sa.UniqueConstraint("storage_backend", "uri"),
    )
    for column in ("project_id", "snapshot_id", "work_attempt_id", "dag_node_id", "asset_type", "content_hash", "state"):
        op.create_index(f"ix_assets_{column}", "assets", [column])

    op.create_table(
        "qc_reports",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("asset_id", sa.String(48), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("report_number", sa.Integer(), nullable=False),
        sa.Column("ruleset_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("analyzer", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(48), nullable=True),
        sa.UniqueConstraint("asset_id", "report_number"),
    )
    for column in ("project_id", "snapshot_id", "asset_id", "status"):
        op.create_index(f"ix_qc_reports_{column}", "qc_reports", [column])

    op.create_table(
        "qc_findings",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("qc_report_id", sa.String(48), sa.ForeignKey("qc_reports.id"), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("contract_field", sa.String(160), nullable=True),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_qc_findings_qc_report_id", "qc_findings", ["qc_report_id"])
    op.create_index("ix_qc_findings_code", "qc_findings", ["code"])

    op.create_table(
        "asset_review_decisions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("asset_id", sa.String(48), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("qc_report_id", sa.String(48), sa.ForeignKey("qc_reports.id"), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("rationale", sa.String(1000), nullable=False),
        sa.Column("actor_id", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("project_id", "asset_id", "qc_report_id"):
        op.create_index(f"ix_asset_review_decisions_{column}", "asset_review_decisions", [column])


def downgrade() -> None:
    op.drop_table("asset_review_decisions")
    op.drop_table("qc_findings")
    op.drop_table("qc_reports")
    op.drop_table("assets")
