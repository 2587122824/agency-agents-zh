"""delivery attempts and final asset authority

Revision ID: 20260716_10
Revises: 20260716_09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_10"
down_revision = "20260716_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("delivery_asset_id", sa.String(48), nullable=True))
        batch.create_foreign_key("fk_projects_delivery_asset", "assets", ["delivery_asset_id"], ["id"])
        batch.create_index("ix_projects_delivery_asset_id", ["delivery_asset_id"])

    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("snapshot_id", sa.String(48), sa.ForeignKey("production_snapshots.id"), nullable=False),
        sa.Column("timeline_id", sa.String(48), sa.ForeignKey("timelines.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("execution_kind", sa.String(32), nullable=False),
        sa.Column("request_manifest", sa.JSON(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("final_asset_id", sa.String(48), sa.ForeignKey("assets.id"), nullable=True, unique=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_detail", sa.JSON(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("output_registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("timeline_id", "attempt_number"),
    )
    op.create_index("ix_delivery_attempts_project_id", "delivery_attempts", ["project_id"])
    op.create_index("ix_delivery_attempts_snapshot_id", "delivery_attempts", ["snapshot_id"])
    op.create_index("ix_delivery_attempts_timeline_id", "delivery_attempts", ["timeline_id"])
    op.create_index("ix_delivery_attempts_status", "delivery_attempts", ["status"])
    op.create_index("ix_delivery_attempts_request_fingerprint", "delivery_attempts", ["request_fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_request_fingerprint", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_status", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_timeline_id", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_snapshot_id", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_project_id", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
    with op.batch_alter_table("projects") as batch:
        batch.drop_index("ix_projects_delivery_asset_id")
        batch.drop_constraint("fk_projects_delivery_asset", type_="foreignkey")
        batch.drop_column("delivery_asset_id")
