"""Upgrade normalized shots to the shot-plan v3 contract.

Revision ID: 20260720_26
Revises: 20260719_25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260720_26"
down_revision = "20260719_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("shots") as batch:
        batch.add_column(sa.Column("brief_segment_codes", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("continuity_relation", sa.String(length=32), nullable=False, server_default="same_moment"))
        batch.add_column(sa.Column("shot_purpose", sa.String(length=32), nullable=False, server_default="develop"))
        batch.add_column(sa.Column("framing", sa.String(length=32), nullable=False, server_default="medium"))
        batch.add_column(sa.Column("camera_angle", sa.String(length=32), nullable=False, server_default="eye_level"))
        batch.add_column(sa.Column("camera_motion", sa.String(length=32), nullable=False, server_default="locked"))
        batch.add_column(sa.Column("subject_motion", sa.String(length=32), nullable=False, server_default="moderate"))
        batch.add_column(sa.Column("face_subject_entity_version_ids", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("required_on_screen_text", sa.Text(), nullable=True))
        batch.add_column(sa.Column("new_information", sa.String(length=1000), nullable=False, server_default="未迁移的开发数据"))
        batch.add_column(sa.Column("generation_requirements", sa.JSON(), nullable=False, server_default='{"reference_image_required":false,"multi_frame_required":false,"identity_consistency_required":false,"precise_text_required":false}'))
        batch.drop_column("shot_type")
        batch.drop_column("motion_requirement")


def downgrade() -> None:
    with op.batch_alter_table("shots") as batch:
        batch.add_column(sa.Column("shot_type", sa.String(length=40), nullable=False, server_default="concept"))
        batch.add_column(sa.Column("motion_requirement", sa.String(length=24), nullable=False, server_default="moderate"))
        batch.drop_column("generation_requirements")
        batch.drop_column("new_information")
        batch.drop_column("required_on_screen_text")
        batch.drop_column("face_subject_entity_version_ids")
        batch.drop_column("subject_motion")
        batch.drop_column("camera_motion")
        batch.drop_column("camera_angle")
        batch.drop_column("framing")
        batch.drop_column("shot_purpose")
        batch.drop_column("continuity_relation")
        batch.drop_column("brief_segment_codes")
