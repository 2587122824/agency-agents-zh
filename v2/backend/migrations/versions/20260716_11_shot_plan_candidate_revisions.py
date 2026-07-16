"""shot plan candidate revision lineage

Revision ID: 20260716_11
Revises: 20260716_10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_11"
down_revision = "20260716_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("shot_plan_candidates") as batch:
        batch.alter_column("agent_run_id", existing_type=sa.String(48), nullable=True)
        batch.add_column(sa.Column("supersedes_candidate_id", sa.String(48), nullable=True))
        batch.add_column(sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("source", sa.String(32), nullable=False, server_default="director_agent"))
        batch.add_column(sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("created_by", sa.String(48), nullable=False, server_default="system"))
        batch.create_foreign_key(
            "fk_shot_plan_candidates_supersedes",
            "shot_plan_candidates",
            ["supersedes_candidate_id"],
            ["id"],
        )
        batch.create_index("ix_shot_plan_candidates_supersedes_candidate_id", ["supersedes_candidate_id"])


def downgrade() -> None:
    with op.batch_alter_table("shot_plan_candidates") as batch:
        batch.drop_index("ix_shot_plan_candidates_supersedes_candidate_id")
        batch.drop_constraint("fk_shot_plan_candidates_supersedes", type_="foreignkey")
        batch.drop_column("created_by")
        batch.drop_column("row_version")
        batch.drop_column("source")
        batch.drop_column("revision_number")
        batch.drop_column("supersedes_candidate_id")
        batch.alter_column("agent_run_id", existing_type=sa.String(48), nullable=False)
