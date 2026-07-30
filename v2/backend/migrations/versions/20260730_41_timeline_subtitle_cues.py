"""Freeze optional per-cue subtitle revisions in timeline contract v4.

Revision ID: 20260730_41
Revises: 20260729_40
"""

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "20260730_41"
down_revision = "20260729_40"
branch_labels = None
depends_on = None


def _json(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _contract_hash(connection, timeline, version: str) -> str:
    items = connection.execute(
        sa.text(
            """
            SELECT track_type, sequence_number, asset_id, label, gap_reason,
                   source_in_ms, source_out_ms, timeline_in_ms, timeline_out_ms, transform
            FROM timeline_items
            WHERE timeline_id = :timeline_id
            ORDER BY track_type, sequence_number
            """
        ),
        {"timeline_id": timeline["id"]},
    ).mappings()
    payload = {
        "contract_version": version,
        "project_id": timeline["project_id"],
        "snapshot_id": timeline["snapshot_id"],
        "version_number": timeline["version_number"],
        "output_spec": _json(timeline["output_spec"]),
        "track_config": _json(timeline["track_config"]),
        "items": [
            {
                "track_type": item["track_type"],
                "sequence_number": item["sequence_number"],
                "asset_id": item["asset_id"],
                "label": item["label"],
                "gap_reason": item["gap_reason"],
                "source_in_ms": item["source_in_ms"],
                "source_out_ms": item["source_out_ms"],
                "timeline_in_ms": item["timeline_in_ms"],
                "timeline_out_ms": item["timeline_out_ms"],
                "transform": _json(item["transform"]),
            }
            for item in items
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _rewrite(*, remove: bool) -> None:
    connection = op.get_bind()
    subtitle_rows = connection.execute(
        sa.text("SELECT id, transform FROM timeline_items WHERE track_type = 'subtitle'")
    ).mappings()
    for row in subtitle_rows:
        transform = _json(row["transform"])
        if remove:
            transform.pop("subtitle_cues", None)
        else:
            transform["subtitle_cues"] = None
        connection.execute(
            sa.text("UPDATE timeline_items SET transform = :transform WHERE id = :item_id"),
            {
                "item_id": row["id"],
                "transform": json.dumps(transform, ensure_ascii=False, separators=(",", ":")),
            },
        )
    timelines = list(connection.execute(
        sa.text(
            """
            SELECT id, project_id, snapshot_id, version_number, output_spec,
                   track_config, contract_hash
            FROM timelines
            """
        )
    ).mappings())
    version = "v2.timeline-contract.v3" if remove else "v2.timeline-contract.v4"
    for timeline in timelines:
        if timeline["contract_hash"] is None:
            continue
        connection.execute(
            sa.text("UPDATE timelines SET contract_hash = :contract_hash WHERE id = :timeline_id"),
            {
                "timeline_id": timeline["id"],
                "contract_hash": _contract_hash(connection, timeline, version),
            },
        )


def upgrade() -> None:
    _rewrite(remove=False)


def downgrade() -> None:
    _rewrite(remove=True)
