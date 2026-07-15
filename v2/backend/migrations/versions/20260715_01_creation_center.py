"""add creation center authority records

Revision ID: 20260715_01
Revises:
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    # Early V2 builds used create_all. Upgrade that preview schema in place,
    # then let Alembic take authority without deleting local project data.
    if "creation_messages" in existing:
        attachment_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("attachments")}
        if "storage_path" not in attachment_columns:
            op.add_column("attachments", sa.Column("storage_path", sa.String(500), nullable=True))
            op.execute("UPDATE attachments SET storage_path = 'legacy/unavailable' WHERE storage_path IS NULL")
        return
    if "projects" not in existing:
        op.create_table(
            "projects",
            sa.Column("id", sa.String(48), primary_key=True),
            sa.Column("title", sa.String(160), nullable=False),
            sa.Column("core_topic", sa.String(500), nullable=False),
            sa.Column("duration_seconds", sa.Integer(), nullable=False),
            sa.Column("aspect_ratio", sa.String(16), nullable=False),
            sa.Column("audio_mode", sa.String(24), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_projects_status", "projects", ["status"])
        op.create_table(
            "decisions",
            sa.Column("id", sa.String(48), primary_key=True),
            sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("key", sa.String(120), nullable=False),
            sa.Column("label", sa.String(160), nullable=False),
            sa.Column("value", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("source", sa.String(24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_decisions_project_id", "decisions", ["project_id"])
        op.create_table(
            "work_items",
            sa.Column("id", sa.String(48), primary_key=True),
            sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("kind", sa.String(80), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_work_items_project_id", "work_items", ["project_id"])
        op.create_index("ix_work_items_status", "work_items", ["status"])
        op.create_table(
            "project_events",
            sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("event_type", sa.String(80), nullable=False),
            sa.Column("message", sa.String(500), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_project_events_project_id", "project_events", ["project_id"])
    op.create_table(
        "creation_messages",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to_message_id", sa.String(48), sa.ForeignKey("creation_messages.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_creation_messages_project_id", "creation_messages", ["project_id"])
    op.create_table(
        "requirement_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("candidate_id", sa.String(48), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "version_number"),
    )
    op.create_index("ix_requirement_versions_project_id", "requirement_versions", ["project_id"])
    op.create_index("ix_requirement_versions_is_active", "requirement_versions", ["is_active"])
    op.create_table(
        "agent_input_manifests",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("base_requirement_version_id", sa.String(48), sa.ForeignKey("requirement_versions.id"), nullable=False),
        sa.Column("message_ids", sa.JSON(), nullable=False),
        sa.Column("decision_ids", sa.JSON(), nullable=False),
        sa.Column("attachment_binding_ids", sa.JSON(), nullable=False),
        sa.Column("system_config_version", sa.String(48), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_input_manifests_project_id", "agent_input_manifests", ["project_id"])
    op.create_index("ix_agent_input_manifests_input_hash", "agent_input_manifests", ["input_hash"])
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("agent_role", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_manifest_id", sa.String(48), sa.ForeignKey("agent_input_manifests.id"), nullable=False),
        sa.Column("model_provider", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(80), nullable=False),
        sa.Column("prompt_contract_version", sa.String(48), nullable=False),
        sa.Column("output_schema_version", sa.String(48), nullable=False),
        sa.Column("parsed_candidate_id", sa.String(48), nullable=True),
        sa.Column("raw_output", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_runs_project_id", "agent_runs", ["project_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_table(
        "requirement_candidates",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("base_requirement_version_id", sa.String(48), sa.ForeignKey("requirement_versions.id"), nullable=False),
        sa.Column("agent_run_id", sa.String(48), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_requirement_candidates_project_id", "requirement_candidates", ["project_id"])
    op.create_index("ix_requirement_candidates_base_requirement_version_id", "requirement_candidates", ["base_requirement_version_id"])
    op.create_index("ix_requirement_candidates_status", "requirement_candidates", ["status"])
    op.create_table(
        "clarification_requests",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("candidate_id", sa.String(48), sa.ForeignKey("requirement_candidates.id"), nullable=True),
        sa.Column("field_key", sa.String(120), nullable=False),
        sa.Column("question", sa.String(500), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("resolution", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_clarification_requests_project_id", "clarification_requests", ["project_id"])
    op.create_index("ix_clarification_requests_status", "clarification_requests", ["status"])
    op.create_table(
        "attachments",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("message_id", sa.String(48), sa.ForeignKey("creation_messages.id"), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("verification_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_attachments_project_id", "attachments", ["project_id"])
    op.create_table(
        "attachment_bindings",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("attachment_id", sa.String(48), sa.ForeignKey("attachments.id"), nullable=False),
        sa.Column("binding_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=True),
        sa.Column("entity_version_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("confirmed_by", sa.String(48), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_attachment_bindings_project_id", "attachment_bindings", ["project_id"])
    op.create_index("ix_attachment_bindings_attachment_id", "attachment_bindings", ["attachment_id"])
    op.create_table(
        "command_receipts",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("command_id", sa.String(80), nullable=False),
        sa.Column("command_type", sa.String(80), nullable=False),
        sa.Column("result_type", sa.String(80), nullable=False),
        sa.Column("result_id", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "command_id"),
    )
    op.create_index("ix_command_receipts_project_id", "command_receipts", ["project_id"])


def downgrade() -> None:
    for table in (
        "command_receipts", "attachment_bindings", "attachments", "clarification_requests",
        "requirement_candidates", "agent_runs", "agent_input_manifests", "requirement_versions",
        "creation_messages",
    ):
        op.drop_table(table)
