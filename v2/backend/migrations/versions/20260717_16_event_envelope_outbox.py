"""add project event envelope and transactional outbox

Revision ID: 20260717_16
Revises: 20260716_15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260717_16"
down_revision = "20260716_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("event_sequence", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("project_events") as batch:
        batch.add_column(sa.Column("event_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("project_sequence", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("snapshot_id", sa.String(length=48), nullable=True))
        batch.add_column(sa.Column("aggregate_type", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("aggregate_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("causation_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("correlation_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("actor_type", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("actor_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("schema_version", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE project_events AS event
        SET event_id = 'event_legacy_' || event.sequence,
            project_sequence = (
                SELECT COUNT(*) FROM project_events AS previous
                WHERE previous.project_id = event.project_id
                  AND previous.sequence <= event.sequence
            ),
            aggregate_type = 'project',
            aggregate_id = event.project_id,
            correlation_id = 'event_legacy_' || event.sequence,
            actor_type = 'system',
            actor_id = 'migration',
            schema_version = 1
        """
    )
    op.execute(
        """
        UPDATE projects
        SET event_sequence = COALESCE((
            SELECT MAX(project_events.project_sequence)
            FROM project_events
            WHERE project_events.project_id = projects.id
        ), 0)
        """
    )
    with op.batch_alter_table("project_events") as batch:
        batch.alter_column("event_id", existing_type=sa.String(length=48), nullable=False)
        batch.alter_column("project_sequence", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("aggregate_type", existing_type=sa.String(length=40), nullable=False)
        batch.alter_column("aggregate_id", existing_type=sa.String(length=80), nullable=False)
        batch.alter_column("correlation_id", existing_type=sa.String(length=80), nullable=False)
        batch.alter_column("actor_type", existing_type=sa.String(length=24), nullable=False)
        batch.alter_column("actor_id", existing_type=sa.String(length=80), nullable=False)
        batch.alter_column("schema_version", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_project_events_event_id", ["event_id"])
        batch.create_unique_constraint("uq_project_events_project_sequence", ["project_id", "project_sequence"])
        batch.create_index("ix_project_events_snapshot_id", ["snapshot_id"])
        batch.create_index("ix_project_events_aggregate_type", ["aggregate_type"])
        batch.create_index("ix_project_events_aggregate_id", ["aggregate_id"])
        batch.create_index("ix_project_events_correlation_id", ["correlation_id"])

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("event_id", sa.String(length=48), nullable=False),
        sa.Column("project_id", sa.String(length=48), nullable=False),
        sa.Column("topic", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["project_events.event_id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_outbox_messages_event_id", "outbox_messages", ["event_id"], unique=True)
    op.create_index("ix_outbox_messages_project_id", "outbox_messages", ["project_id"])
    op.create_index("ix_outbox_messages_status", "outbox_messages", ["status"])
    op.create_index("ix_outbox_messages_available_at", "outbox_messages", ["available_at"])
    op.execute(
        """
        INSERT INTO outbox_messages (
            id, event_id, project_id, topic, status, available_at, published_at, created_at
        )
        SELECT
            'outbox_legacy_' || sequence,
            event_id,
            project_id,
            'project.events',
            'published',
            created_at,
            created_at,
            created_at
        FROM project_events
        """
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_messages_available_at", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_status", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_project_id", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_event_id", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    with op.batch_alter_table("project_events") as batch:
        batch.drop_index("ix_project_events_correlation_id")
        batch.drop_index("ix_project_events_aggregate_id")
        batch.drop_index("ix_project_events_aggregate_type")
        batch.drop_index("ix_project_events_snapshot_id")
        batch.drop_constraint("uq_project_events_project_sequence", type_="unique")
        batch.drop_constraint("uq_project_events_event_id", type_="unique")
        batch.drop_column("schema_version")
        batch.drop_column("actor_id")
        batch.drop_column("actor_type")
        batch.drop_column("correlation_id")
        batch.drop_column("causation_id")
        batch.drop_column("aggregate_id")
        batch.drop_column("aggregate_type")
        batch.drop_column("snapshot_id")
        batch.drop_column("project_sequence")
        batch.drop_column("event_id")
    op.drop_column("projects", "event_sequence")
