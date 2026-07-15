"""add typed entities and planning candidates

Revision ID: 20260715_03
Revises: 20260715_02
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_03"
down_revision = "20260715_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "id"),
    )
    op.create_index("ix_entities_project_id", "entities", ["project_id"])
    op.create_index("ix_entities_entity_type", "entities", ["entity_type"])
    op.create_table(
        "entity_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("entity_id", sa.String(80), sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source_attachment_id", sa.String(48), sa.ForeignKey("attachments.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entity_id", "version_number"),
    )
    op.create_index("ix_entity_versions_project_id", "entity_versions", ["project_id"])
    op.create_index("ix_entity_versions_entity_id", "entity_versions", ["entity_id"])
    op.create_index("ix_entity_versions_is_active", "entity_versions", ["is_active"])
    op.execute(
        "UPDATE attachment_bindings SET status = 'legacy_unresolved' "
        "WHERE status = 'confirmed' AND binding_type != 'inspiration_only'"
    )
    op.create_table(
        "creative_brief_candidates",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("requirement_version_id", sa.String(48), sa.ForeignKey("requirement_versions.id"), nullable=False),
        sa.Column("agent_run_id", sa.String(48), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("brief", sa.JSON(), nullable=False),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_creative_brief_candidates_project_id", "creative_brief_candidates", ["project_id"])
    op.create_index("ix_creative_brief_candidates_requirement_version_id", "creative_brief_candidates", ["requirement_version_id"])
    op.create_index("ix_creative_brief_candidates_status", "creative_brief_candidates", ["status"])
    op.create_table(
        "shot_plan_candidates",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("requirement_version_id", sa.String(48), sa.ForeignKey("requirement_versions.id"), nullable=False),
        sa.Column("creative_brief_candidate_id", sa.String(48), sa.ForeignKey("creative_brief_candidates.id"), nullable=False),
        sa.Column("agent_run_id", sa.String(48), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("shots", sa.JSON(), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_shot_plan_candidates_project_id", "shot_plan_candidates", ["project_id"])
    op.create_index("ix_shot_plan_candidates_requirement_version_id", "shot_plan_candidates", ["requirement_version_id"])
    op.create_index("ix_shot_plan_candidates_status", "shot_plan_candidates", ["status"])
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("requirement_version_id", sa.String(48), sa.ForeignKey("requirement_versions.id"), nullable=False),
        sa.Column("shot_plan_candidate_id", sa.String(48), sa.ForeignKey("shot_plan_candidates.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("creative_brief", sa.JSON(), nullable=False),
        sa.Column("contract_schema_version", sa.String(48), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.String(48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "version_number"),
    )
    op.create_index("ix_plan_versions_project_id", "plan_versions", ["project_id"])
    op.create_index("ix_plan_versions_requirement_version_id", "plan_versions", ["requirement_version_id"])
    op.create_index("ix_plan_versions_status", "plan_versions", ["status"])
    op.create_index("ix_plan_versions_is_active", "plan_versions", ["is_active"])
    op.create_table(
        "shots",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("project_id", sa.String(48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("plan_version_id", sa.String(48), sa.ForeignKey("plan_versions.id"), nullable=False),
        sa.Column("shot_code", sa.String(32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("shot_type", sa.String(40), nullable=False),
        sa.Column("scene_entity_version_id", sa.String(48), sa.ForeignKey("entity_versions.id"), nullable=True),
        sa.Column("character_entity_version_ids", sa.JSON(), nullable=False),
        sa.Column("outfit_entity_version_ids", sa.JSON(), nullable=False),
        sa.Column("face_visibility", sa.String(24), nullable=False),
        sa.Column("text_policy", sa.String(24), nullable=False),
        sa.Column("motion_requirement", sa.String(24), nullable=False),
        sa.Column("composition", sa.String(500), nullable=False),
        sa.Column("action", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_version_id", "shot_code"),
        sa.UniqueConstraint("plan_version_id", "sequence_number"),
    )
    op.create_index("ix_shots_project_id", "shots", ["project_id"])
    op.create_index("ix_shots_plan_version_id", "shots", ["plan_version_id"])


def downgrade() -> None:
    for table in (
        "shots", "plan_versions", "shot_plan_candidates", "creative_brief_candidates",
        "entity_versions", "entities",
    ):
        op.drop_table(table)
