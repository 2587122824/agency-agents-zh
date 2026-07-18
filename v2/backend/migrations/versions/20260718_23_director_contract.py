"""Add structured director shot contract fields.

Revision ID: 20260718_23
Revises: 20260718_22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260718_23"
down_revision = "20260718_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("shots") as batch:
        batch.add_column(sa.Column("narrative_beat_code", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("continuity_group_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("action_count", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("audio_requirement", sa.String(length=24), nullable=False, server_default="off"))


def downgrade() -> None:
    with op.batch_alter_table("shots") as batch:
        batch.drop_column("audio_requirement")
        batch.drop_column("action_count")
        batch.drop_column("continuity_group_id")
        batch.drop_column("narrative_beat_code")
