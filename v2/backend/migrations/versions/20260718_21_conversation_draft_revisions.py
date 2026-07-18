"""bind requirement draft revisions to creative conversations.

Revision ID: 20260718_21
Revises: 20260718_20
"""

from alembic import op
import sqlalchemy as sa


revision = "20260718_21"
down_revision = "20260718_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("requirement_candidates") as batch:
        batch.add_column(sa.Column("conversation_session_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("supersedes_candidate_id", sa.String(length=48), nullable=True))
        batch.create_foreign_key(
            "fk_requirement_candidates_conversation_session_id",
            "conversation_sessions",
            ["conversation_session_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_requirement_candidates_supersedes_candidate_id",
            "requirement_candidates",
            ["supersedes_candidate_id"],
            ["id"],
        )
        batch.create_index("ix_requirement_candidates_conversation_session_id", ["conversation_session_id"])
        batch.create_index("ix_requirement_candidates_supersedes_candidate_id", ["supersedes_candidate_id"])
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE requirement_candidates SET conversation_session_id = ("
        "SELECT conversation_session_id FROM creation_messages "
        "JOIN agent_input_manifests ON creation_messages.id = json_extract(agent_input_manifests.message_ids, '$[0]') "
        "JOIN agent_runs ON agent_runs.input_manifest_id = agent_input_manifests.id "
        "WHERE agent_runs.id = requirement_candidates.agent_run_id LIMIT 1)"
    ))
    connection.execute(sa.text(
        "UPDATE requirement_candidates SET conversation_session_id = ("
        "SELECT id FROM conversation_sessions WHERE project_id = requirement_candidates.project_id "
        "ORDER BY started_at DESC LIMIT 1) WHERE conversation_session_id IS NULL"
    ))
    with op.batch_alter_table("requirement_candidates") as batch:
        batch.alter_column("conversation_session_id", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("requirement_candidates") as batch:
        batch.drop_index("ix_requirement_candidates_supersedes_candidate_id")
        batch.drop_index("ix_requirement_candidates_conversation_session_id")
        batch.drop_constraint("fk_requirement_candidates_supersedes_candidate_id", type_="foreignkey")
        batch.drop_constraint("fk_requirement_candidates_conversation_session_id", type_="foreignkey")
        batch.drop_column("supersedes_candidate_id")
        batch.drop_column("conversation_session_id")
