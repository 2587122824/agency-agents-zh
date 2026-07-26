"""Add deterministic audio mastering defaults to timeline contracts.

Revision ID: 20260726_36
Revises: 20260726_35
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "20260726_36"
down_revision = "20260726_35"
branch_labels = None
depends_on = None


def _rewrite(*, remove: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, track_config FROM timelines")).mappings()
    for row in rows:
        value = row["track_config"]
        config = json.loads(value) if isinstance(value, str) else dict(value or {})
        if remove:
            config.pop("audio_mastering", None)
        else:
            config["audio_mastering"] = {
                "loudness_target_lufs": -16.0,
                "true_peak_limit_dbtp": -1.0,
                "clipping_control": "limiter",
            }
        connection.execute(
            sa.text("UPDATE timelines SET track_config = :track_config WHERE id = :timeline_id"),
            {"timeline_id": row["id"], "track_config": json.dumps(config, separators=(",", ":"))},
        )


def upgrade() -> None:
    _rewrite(remove=False)


def downgrade() -> None:
    _rewrite(remove=True)
