"""Freeze the timeline snapping toggle in the track configuration.

Revision ID: 20260729_40
Revises: 20260728_39
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "20260729_40"
down_revision = "20260728_39"
branch_labels = None
depends_on = None


def _rewrite(*, remove: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, track_config FROM timelines")).mappings()
    for row in rows:
        value = row["track_config"]
        config = json.loads(value) if isinstance(value, str) else dict(value or {})
        if remove:
            config.pop("snap_enabled", None)
        else:
            config["snap_enabled"] = True
        connection.execute(
            sa.text("UPDATE timelines SET track_config = :track_config WHERE id = :timeline_id"),
            {"timeline_id": row["id"], "track_config": json.dumps(config, separators=(",", ":"))},
        )


def upgrade() -> None:
    _rewrite(remove=False)


def downgrade() -> None:
    _rewrite(remove=True)
