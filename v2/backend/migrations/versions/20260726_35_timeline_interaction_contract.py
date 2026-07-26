"""Upgrade persisted timeline track configuration to interaction contract v2.

Revision ID: 20260726_35
Revises: 20260726_34
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "20260726_35"
down_revision = "20260726_34"
branch_labels = None
depends_on = None


def _rewrite(*, remove: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, track_config FROM timelines")).mappings()
    for row in rows:
        value = row["track_config"]
        config = json.loads(value) if isinstance(value, str) else dict(value or {})
        if remove:
            config.pop("pixels_per_second", None)
            config.pop("snap_interval_ms", None)
        else:
            config["pixels_per_second"] = 60
            config["snap_interval_ms"] = 100
        connection.execute(
            sa.text("UPDATE timelines SET track_config = :track_config WHERE id = :timeline_id"),
            {"timeline_id": row["id"], "track_config": json.dumps(config, separators=(",", ":"))},
        )


def upgrade() -> None:
    _rewrite(remove=False)


def downgrade() -> None:
    _rewrite(remove=True)
