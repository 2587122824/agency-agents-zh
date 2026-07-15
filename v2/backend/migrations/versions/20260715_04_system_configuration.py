"""add versioned system configuration registry

Revision ID: 20260715_04
Revises: 20260715_03
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_04"
down_revision = "20260715_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_config_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("config_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("supersedes_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("config_key", "version_number"),
    )
    op.create_index("ix_production_config_versions_config_key", "production_config_versions", ["config_key"])
    op.create_index("ix_production_config_versions_status", "production_config_versions", ["status"])

    op.create_table(
        "provider_config_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("provider_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("adapter_kind", sa.String(80), nullable=False),
        sa.Column("region", sa.String(120), nullable=True),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("credential_ref", sa.String(160), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider_key", "version_number"),
    )
    op.create_index("ix_provider_config_versions_production_config_version_id", "provider_config_versions", ["production_config_version_id"])
    op.create_index("ix_provider_config_versions_provider_key", "provider_config_versions", ["provider_key"])
    op.create_index("ix_provider_config_versions_status", "provider_config_versions", ["status"])

    op.create_table(
        "model_config_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("config_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("agent_role", sa.String(24), nullable=False),
        sa.Column("provider_config_version_id", sa.String(48), sa.ForeignKey("provider_config_versions.id"), nullable=False),
        sa.Column("provider_model_id", sa.String(200), nullable=False),
        sa.Column("input_contract_version", sa.String(80), nullable=False),
        sa.Column("output_schema_version", sa.String(80), nullable=False),
        sa.Column("prompt_contract_version", sa.String(80), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("sampling", sa.JSON(), nullable=False),
        sa.Column("capability_tags", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("config_key", "version_number"),
    )
    op.create_index("ix_model_config_versions_production_config_version_id", "model_config_versions", ["production_config_version_id"])
    op.create_index("ix_model_config_versions_config_key", "model_config_versions", ["config_key"])
    op.create_index("ix_model_config_versions_status", "model_config_versions", ["status"])

    op.create_table(
        "video_spec_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("spec_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("aspect_ratio", sa.String(16), nullable=False),
        sa.Column("fps", sa.Integer(), nullable=False),
        sa.Column("duration_min_seconds", sa.Integer(), nullable=False),
        sa.Column("duration_max_seconds", sa.Integer(), nullable=False),
        sa.Column("frame_count_rule", sa.JSON(), nullable=False),
        sa.Column("container", sa.String(24), nullable=False),
        sa.Column("video_codec", sa.String(40), nullable=False),
        sa.Column("pixel_format", sa.String(40), nullable=False),
        sa.Column("bitrate_policy", sa.JSON(), nullable=False),
        sa.Column("safe_crop", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("spec_key", "version_number"),
    )
    op.create_index("ix_video_spec_versions_production_config_version_id", "video_spec_versions", ["production_config_version_id"])
    op.create_index("ix_video_spec_versions_spec_key", "video_spec_versions", ["spec_key"])
    op.create_index("ix_video_spec_versions_status", "video_spec_versions", ["status"])

    op.create_table(
        "workflow_slot_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("slot_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("operation_kind", sa.String(80), nullable=False),
        sa.Column("provider_config_version_id", sa.String(48), sa.ForeignKey("provider_config_versions.id"), nullable=False),
        sa.Column("provider_workflow_id", sa.String(200), nullable=False),
        sa.Column("provider_workflow_version", sa.String(120), nullable=True),
        sa.Column("model_config_version_id", sa.String(48), sa.ForeignKey("model_config_versions.id"), nullable=True),
        sa.Column("input_schema_version", sa.String(80), nullable=False),
        sa.Column("output_schema_version", sa.String(80), nullable=False),
        sa.Column("node_info_list", sa.JSON(), nullable=False),
        sa.Column("supported_video_spec_ids", sa.JSON(), nullable=False),
        sa.Column("capability_tags", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slot_key", "version_number"),
    )
    op.create_index("ix_workflow_slot_versions_production_config_version_id", "workflow_slot_versions", ["production_config_version_id"])
    op.create_index("ix_workflow_slot_versions_slot_key", "workflow_slot_versions", ["slot_key"])
    op.create_index("ix_workflow_slot_versions_operation_kind", "workflow_slot_versions", ["operation_kind"])
    op.create_index("ix_workflow_slot_versions_status", "workflow_slot_versions", ["status"])

    op.create_table(
        "audio_config_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("config_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("supported_modes", sa.JSON(), nullable=False),
        sa.Column("tts_workflow_slot_version_id", sa.String(48), sa.ForeignKey("workflow_slot_versions.id"), nullable=True),
        sa.Column("default_voice_entity_version_id", sa.String(48), sa.ForeignKey("entity_versions.id"), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(24), nullable=False),
        sa.Column("speaking_rate_range", sa.JSON(), nullable=False),
        sa.Column("loudness_target", sa.Float(), nullable=True),
        sa.Column("temporary_upload_policy_version_id", sa.String(48), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("config_key", "version_number"),
    )
    op.create_index("ix_audio_config_versions_production_config_version_id", "audio_config_versions", ["production_config_version_id"])
    op.create_index("ix_audio_config_versions_config_key", "audio_config_versions", ["config_key"])
    op.create_index("ix_audio_config_versions_status", "audio_config_versions", ["status"])

    op.create_table(
        "storage_policy_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("policy_key", sa.String(120), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("backend_kind", sa.String(24), nullable=False),
        sa.Column("region_ref", sa.String(160), nullable=True),
        sa.Column("bucket_ref", sa.String(160), nullable=True),
        sa.Column("credential_ref", sa.String(160), nullable=True),
        sa.Column("allowed_mime_types", sa.JSON(), nullable=False),
        sa.Column("max_file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("public_url_policy", sa.String(40), nullable=False),
        sa.Column("lifecycle_days", sa.Integer(), nullable=True),
        sa.Column("local_root_ref", sa.String(160), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("policy_key", "version_number"),
    )
    op.create_index("ix_storage_policy_versions_production_config_version_id", "storage_policy_versions", ["production_config_version_id"])
    op.create_index("ix_storage_policy_versions_policy_key", "storage_policy_versions", ["policy_key"])
    op.create_index("ix_storage_policy_versions_status", "storage_policy_versions", ["status"])

    op.create_table(
        "production_config_components",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("component_type", sa.String(40), nullable=False),
        sa.Column("component_version_id", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("production_config_version_id", "component_type", "component_version_id"),
    )
    op.create_index("ix_production_config_components_production_config_version_id", "production_config_components", ["production_config_version_id"])
    op.create_index("ix_production_config_components_component_type", "production_config_components", ["component_type"])
    op.create_index("ix_production_config_components_component_version_id", "production_config_components", ["component_version_id"])

    op.create_table(
        "configuration_references",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("ref_type", sa.String(24), nullable=False),
        sa.Column("ref_id", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("production_config_version_id", "ref_type", "ref_id"),
    )
    op.create_index("ix_configuration_references_production_config_version_id", "configuration_references", ["production_config_version_id"])

    op.create_table(
        "configuration_command_receipts",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("command_id", sa.String(80), nullable=False),
        sa.Column("command_type", sa.String(80), nullable=False),
        sa.Column("result_type", sa.String(80), nullable=False),
        sa.Column("result_id", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("command_id"),
    )

    op.create_table(
        "configuration_events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("production_config_version_id", sa.String(48), sa.ForeignKey("production_config_versions.id"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.String(48), nullable=False),
        sa.Column("command_id", sa.String(80), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_configuration_events_production_config_version_id", "configuration_events", ["production_config_version_id"])
    op.create_index("ix_configuration_events_event_type", "configuration_events", ["event_type"])


def downgrade() -> None:
    for table in (
        "configuration_events",
        "configuration_command_receipts",
        "configuration_references",
        "production_config_components",
        "audio_config_versions",
        "workflow_slot_versions",
        "model_config_versions",
        "video_spec_versions",
        "provider_config_versions",
        "storage_policy_versions",
        "production_config_versions",
    ):
        op.drop_table(table)
