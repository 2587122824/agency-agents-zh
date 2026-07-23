"""store provider API keys in configuration versions

Revision ID: 20260723_32
Revises: 20260723_31
Create Date: 2026-07-23
"""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "20260723_32"
down_revision = "20260723_31"
branch_labels = None
depends_on = None

_ENV_REFERENCE = re.compile(r"^env://([A-Z][A-Z0-9_]{1,127})$")


def upgrade() -> None:
    with op.batch_alter_table("provider_config_versions") as batch:
        batch.add_column(sa.Column("api_key", sa.Text(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, credential_ref FROM provider_config_versions")
    ).mappings()
    for row in rows:
        match = _ENV_REFERENCE.fullmatch(str(row["credential_ref"] or "").strip())
        if not match:
            continue
        api_key = str(os.getenv(match.group(1), "")).strip()
        if api_key:
            connection.execute(
                sa.text(
                    "UPDATE provider_config_versions SET api_key = :api_key WHERE id = :provider_id"
                ),
                {"api_key": api_key, "provider_id": row["id"]},
            )

    with op.batch_alter_table("provider_config_versions") as batch:
        batch.drop_column("credential_ref")


def downgrade() -> None:
    with op.batch_alter_table("provider_config_versions") as batch:
        batch.add_column(sa.Column("credential_ref", sa.String(length=160), nullable=True))
        batch.drop_column("api_key")
