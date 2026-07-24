"""Add structured issue codes to asset revision requests.

Revision ID: 20260724_33
Revises: 20260723_32
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_33"
down_revision = "20260723_32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "asset_revision_requests",
        sa.Column("issue_code", sa.String(length=40), nullable=True),
    )
    op.execute("UPDATE asset_revision_requests SET issue_code = 'other'")
    with op.batch_alter_table("asset_revision_requests") as batch_op:
        batch_op.alter_column("issue_code", existing_type=sa.String(length=40), nullable=False)
        batch_op.create_index("ix_asset_revision_requests_issue_code", ["issue_code"])


def downgrade() -> None:
    with op.batch_alter_table("asset_revision_requests") as batch_op:
        batch_op.drop_index("ix_asset_revision_requests_issue_code")
        batch_op.drop_column("issue_code")
