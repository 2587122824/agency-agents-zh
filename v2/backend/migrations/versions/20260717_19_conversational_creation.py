"""Add conversational creation audit fields.

Revision ID: 20260717_19
Revises: 20260717_18
"""

from alembic import op
import sqlalchemy as sa


revision = "20260717_19"
down_revision = "20260717_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creation_messages") as batch:
        batch.add_column(sa.Column("agent_run_id", sa.String(length=48), nullable=True))
        batch.create_index("ix_creation_messages_agent_run_id", ["agent_run_id"])
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("production_config_version_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("model_config_version_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("provider_config_version_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("provider_request_id", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("token_usage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("token_usage")
        batch.drop_column("provider_request_id")
        batch.drop_column("provider_config_version_id")
        batch.drop_column("model_config_version_id")
        batch.drop_column("production_config_version_id")
    with op.batch_alter_table("creation_messages") as batch:
        batch.drop_index("ix_creation_messages_agent_run_id")
        batch.drop_column("agent_run_id")
