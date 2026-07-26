"""Add versioned audio execution selection fields.

Revision ID: 20260726_34
Revises: 20260724_33
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_34"
down_revision = "20260724_33"
branch_labels = None
depends_on = None


VOICE_PRESETS = (
    '[{"key":"warm_female","display_name":"温暖女声","provider_voice_id":"longxiaochun",'
    '"description":"自然、温暖，适合品牌旁白","preview_text":"欢迎来到片场 V2 配音试听。"},'
    '{"key":"bright_female","display_name":"明亮女声","provider_voice_id":"longxiaoxia",'
    '"description":"清晰、明快，适合产品介绍","preview_text":"欢迎来到片场 V2 配音试听。"},'
    '{"key":"steady_male","display_name":"沉稳男声","provider_voice_id":"longxiaocheng",'
    '"description":"稳定、可信，适合解说","preview_text":"欢迎来到片场 V2 配音试听。"},'
    '{"key":"youthful","display_name":"青春声线","provider_voice_id":"longxiaobai",'
    '"description":"轻快、有活力，适合短视频","preview_text":"欢迎来到片场 V2 配音试听。"},'
    '{"key":"friendly_male","display_name":"亲和男声","provider_voice_id":"longlaotie",'
    '"description":"亲切、口语化，适合生活内容","preview_text":"欢迎来到片场 V2 配音试听。"}]'
)


def upgrade() -> None:
    with op.batch_alter_table("audio_config_versions") as batch_op:
        batch_op.add_column(sa.Column("voice_presets", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("default_voice_key", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("speaking_rate_default", sa.Float(), nullable=False, server_default="1.0"))
        batch_op.add_column(sa.Column("volume_range", sa.JSON(), nullable=False, server_default='{"min":0,"max":100}'))
        batch_op.add_column(sa.Column("volume_default", sa.Integer(), nullable=False, server_default="50"))
        batch_op.add_column(sa.Column("duration_tolerance_ms", sa.Integer(), nullable=False, server_default="1500"))
    op.execute(
        sa.text(
            "UPDATE audio_config_versions "
            "SET voice_presets = :presets, default_voice_key = 'warm_female', "
            "speaking_rate_default = 1.0, volume_range = :volume_range, "
            "volume_default = 50, duration_tolerance_ms = 1500"
        ).bindparams(presets=VOICE_PRESETS, volume_range='{"min":0,"max":100}')
    )


def downgrade() -> None:
    with op.batch_alter_table("audio_config_versions") as batch_op:
        batch_op.drop_column("duration_tolerance_ms")
        batch_op.drop_column("volume_default")
        batch_op.drop_column("volume_range")
        batch_op.drop_column("speaking_rate_default")
        batch_op.drop_column("default_voice_key")
        batch_op.drop_column("voice_presets")
