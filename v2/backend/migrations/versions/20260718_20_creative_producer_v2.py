"""Add creative producer v2 proposals and selections.

Revision ID: 20260718_20
Revises: 20260717_19
"""

from alembic import op
import sqlalchemy as sa
from uuid import uuid4


revision = "20260718_20"
down_revision = "20260717_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("project_id", sa.String(length=48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("started_by", sa.String(length=48), nullable=False, server_default="migration"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_conversation_sessions_project_id", "conversation_sessions", ["project_id"])
    op.create_index("ix_conversation_sessions_status", "conversation_sessions", ["status"])
    with op.batch_alter_table("creation_messages") as batch:
        batch.add_column(sa.Column("conversation_session_id", sa.String(length=48), nullable=True))
        batch.create_index("ix_creation_messages_conversation_session_id", ["conversation_session_id"])
    connection = op.get_bind()
    project_ids = [row[0] for row in connection.execute(sa.text(
        "SELECT DISTINCT project_id FROM creation_messages"
    ))]
    for project_id in project_ids:
        session_id = f"conversation_{uuid4().hex}"
        connection.execute(sa.text(
            "INSERT INTO conversation_sessions (id, project_id, status, started_by, started_at) "
            "SELECT :id, :project_id, 'active', 'migration', MIN(created_at) "
            "FROM creation_messages WHERE project_id = :project_id"
        ), {"id": session_id, "project_id": project_id})
        connection.execute(sa.text(
            "UPDATE creation_messages SET conversation_session_id = :session_id WHERE project_id = :project_id"
        ), {"session_id": session_id, "project_id": project_id})
    with op.batch_alter_table("creation_messages") as batch:
        batch.alter_column("conversation_session_id", nullable=False)
        batch.create_foreign_key(
            "fk_creation_messages_conversation_session_id",
            "conversation_sessions",
            ["conversation_session_id"],
            ["id"],
        )

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("parsed_proposal_id", sa.String(length=48), nullable=True))

    op.create_table(
        "creative_turn_proposals",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("project_id", sa.String(length=48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("base_requirement_version_id", sa.String(length=48), sa.ForeignKey("requirement_versions.id"), nullable=False),
        sa.Column("agent_run_id", sa.String(length=48), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=48), sa.ForeignKey("creation_messages.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("suggestion_sets", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("explicit_updates", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("clarifying_question", sa.JSON(), nullable=True),
        sa.Column("prompt_contract_version", sa.String(length=48), nullable=False),
        sa.Column("output_schema_version", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_run_id"),
        sa.UniqueConstraint("assistant_message_id"),
    )
    op.create_index("ix_creative_turn_proposals_project_id", "creative_turn_proposals", ["project_id"])
    op.create_index("ix_creative_turn_proposals_base_requirement_version_id", "creative_turn_proposals", ["base_requirement_version_id"])
    op.create_index("ix_creative_turn_proposals_agent_run_id", "creative_turn_proposals", ["agent_run_id"])
    op.create_index("ix_creative_turn_proposals_status", "creative_turn_proposals", ["status"])

    op.create_table(
        "creative_suggestion_selections",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("project_id", sa.String(length=48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("proposal_id", sa.String(length=48), sa.ForeignKey("creative_turn_proposals.id"), nullable=False),
        sa.Column("suggestion_set_id", sa.String(length=48), nullable=False),
        sa.Column("option_id", sa.String(length=48), nullable=False),
        sa.Column("candidate_id", sa.String(length=48), sa.ForeignKey("requirement_candidates.id"), nullable=True),
        sa.Column("selected_by", sa.String(length=48), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("proposal_id", "suggestion_set_id"),
    )
    op.create_index("ix_creative_suggestion_selections_project_id", "creative_suggestion_selections", ["project_id"])
    op.create_index("ix_creative_suggestion_selections_proposal_id", "creative_suggestion_selections", ["proposal_id"])


def downgrade() -> None:
    op.drop_index("ix_creative_suggestion_selections_proposal_id", table_name="creative_suggestion_selections")
    op.drop_index("ix_creative_suggestion_selections_project_id", table_name="creative_suggestion_selections")
    op.drop_table("creative_suggestion_selections")
    op.drop_index("ix_creative_turn_proposals_status", table_name="creative_turn_proposals")
    op.drop_index("ix_creative_turn_proposals_agent_run_id", table_name="creative_turn_proposals")
    op.drop_index("ix_creative_turn_proposals_base_requirement_version_id", table_name="creative_turn_proposals")
    op.drop_index("ix_creative_turn_proposals_project_id", table_name="creative_turn_proposals")
    op.drop_table("creative_turn_proposals")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("parsed_proposal_id")
    with op.batch_alter_table("creation_messages") as batch:
        batch.drop_constraint("fk_creation_messages_conversation_session_id", type_="foreignkey")
        batch.drop_index("ix_creation_messages_conversation_session_id")
        batch.drop_column("conversation_session_id")
    op.drop_index("ix_conversation_sessions_status", table_name="conversation_sessions")
    op.drop_index("ix_conversation_sessions_project_id", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
