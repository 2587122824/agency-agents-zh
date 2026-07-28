"""Add immutable project production profile versions.

Revision ID: 20260728_39
Revises: 20260726_38
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260728_39"
down_revision = "20260726_38"
branch_labels = None
depends_on = None


def _hash(contract: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def upgrade() -> None:
    op.create_table(
        "project_production_profile_versions",
        sa.Column("id", sa.String(length=48), primary_key=True),
        sa.Column("project_id", sa.String(length=48), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("video_motion_strategy", sa.String(length=32), nullable=False),
        sa.Column("keyframe_strategy", sa.String(length=32), nullable=False),
        sa.Column("enforcement", sa.String(length=24), nullable=False),
        sa.Column("selected_by", sa.String(length=24), nullable=False),
        sa.Column("required_frame_roles", sa.JSON(), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "version_number"),
    )
    op.create_index(
        "ix_project_production_profile_versions_project_id",
        "project_production_profile_versions",
        ["project_id"],
    )
    op.create_index(
        "ix_project_production_profile_versions_contract_hash",
        "project_production_profile_versions",
        ["contract_hash"],
    )
    op.create_index(
        "ix_project_production_profile_versions_is_active",
        "project_production_profile_versions",
        ["is_active"],
    )

    connection = op.get_bind()
    project_rows = connection.execute(
        sa.text("SELECT id, created_at FROM projects ORDER BY created_at, id")
    ).mappings()
    for project in project_rows:
        contract = {
            "contract_version": "project-production-profile.v1",
            "project_id": project["id"],
            "version_number": 1,
            "video_motion_strategy": "adaptive",
            "keyframe_strategy": "adaptive",
            "enforcement": "required",
            "selected_by": "migration",
            "required_frame_roles": [],
        }
        connection.execute(
            sa.text(
                """
                INSERT INTO project_production_profile_versions (
                    id, project_id, version_number, contract_version,
                    video_motion_strategy, keyframe_strategy, enforcement,
                    selected_by, required_frame_roles, contract_hash,
                    is_active, created_by, created_at
                ) VALUES (
                    :id, :project_id, 1, :contract_version,
                    :video_motion_strategy, :keyframe_strategy, :enforcement,
                    :selected_by, :required_frame_roles, :contract_hash,
                    1, :created_by, :created_at
                )
                """
            ),
            {
                "id": f"production_profile_{uuid4().hex}",
                "project_id": project["id"],
                "contract_version": contract["contract_version"],
                "video_motion_strategy": contract["video_motion_strategy"],
                "keyframe_strategy": contract["keyframe_strategy"],
                "enforcement": contract["enforcement"],
                "selected_by": contract["selected_by"],
                "required_frame_roles": json.dumps(contract["required_frame_roles"]),
                "contract_hash": _hash(contract),
                "created_by": "migration.20260728_39",
                "created_at": project["created_at"],
            },
        )


def downgrade() -> None:
    op.drop_index(
        "ix_project_production_profile_versions_is_active",
        table_name="project_production_profile_versions",
    )
    op.drop_index(
        "ix_project_production_profile_versions_contract_hash",
        table_name="project_production_profile_versions",
    )
    op.drop_index(
        "ix_project_production_profile_versions_project_id",
        table_name="project_production_profile_versions",
    )
    op.drop_table("project_production_profile_versions")
