"""snapshot activation and execution authorization

Revision ID: 20260715_07
Revises: 20260715_06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_07"
down_revision = "20260715_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("production_snapshots") as batch:
        batch.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("active_snapshot_id", sa.String(48), nullable=True))
        batch.create_foreign_key("fk_projects_active_snapshot", "production_snapshots", ["active_snapshot_id"], ["id"])
        batch.create_index("ix_projects_active_snapshot_id", ["active_snapshot_id"])

    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("snapshot_id", sa.String(48), nullable=True))
        batch.add_column(sa.Column("dag_node_id", sa.String(48), nullable=True))
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="100"))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch.add_column(sa.Column("current_attempt_id", sa.String(48), nullable=True))
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_work_items_snapshot", "production_snapshots", ["snapshot_id"], ["id"])
        batch.create_foreign_key("fk_work_items_dag_node", "dag_nodes", ["dag_node_id"], ["id"])
        batch.create_index("ix_work_items_snapshot_id", ["snapshot_id"])
        batch.create_index("ix_work_items_dag_node_id", ["dag_node_id"])
        batch.create_index("ix_work_items_request_fingerprint", ["request_fingerprint"])
        batch.create_unique_constraint("uq_work_items_snapshot_dag_node", ["snapshot_id", "dag_node_id"])
    op.execute("UPDATE work_items SET available_at = created_at WHERE available_at IS NULL")
    op.execute("UPDATE work_items SET updated_at = created_at WHERE updated_at IS NULL")

    op.create_table(
        "work_attempts",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("work_item_id", sa.String(48), sa.ForeignKey("work_items.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("provider_task_id", sa.String(160), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_manifest", sa.JSON(), nullable=False),
        sa.Column("response_manifest", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("execution_lock_owner", sa.String(120), nullable=True),
        sa.Column("execution_lock_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("work_item_id", "attempt_number"),
        sa.UniqueConstraint("provider", "provider_task_id"),
    )
    for column in ("work_item_id", "request_fingerprint", "state"):
        op.create_index(f"ix_work_attempts_{column}", "work_attempts", [column])


def downgrade() -> None:
    op.drop_table("work_attempts")
    with op.batch_alter_table("work_items") as batch:
        batch.drop_constraint("uq_work_items_snapshot_dag_node", type_="unique")
        batch.drop_index("ix_work_items_request_fingerprint")
        batch.drop_index("ix_work_items_dag_node_id")
        batch.drop_index("ix_work_items_snapshot_id")
        batch.drop_constraint("fk_work_items_dag_node", type_="foreignkey")
        batch.drop_constraint("fk_work_items_snapshot", type_="foreignkey")
        for column in ("updated_at", "row_version", "available_at", "current_attempt_id", "request_fingerprint", "priority", "dag_node_id", "snapshot_id"):
            batch.drop_column(column)
    with op.batch_alter_table("projects") as batch:
        batch.drop_index("ix_projects_active_snapshot_id")
        batch.drop_constraint("fk_projects_active_snapshot", type_="foreignkey")
        batch.drop_column("active_snapshot_id")
    with op.batch_alter_table("production_snapshots") as batch:
        batch.drop_column("activated_at")
