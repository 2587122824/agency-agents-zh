from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select
from sqlalchemy.orm import Session

from v2.backend.app.db.models import (
    AgentInputManifest,
    AgentRun,
    CreativeBriefCandidate,
    Project,
    RequirementVersion,
    ShotPlanCandidate,
    utc_now,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def migration_config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    return config


def test_planning_authority_backfill_uses_persisted_candidate_status(tmp_path: Path) -> None:
    database = tmp_path / "planning-backfill.db"
    config = migration_config(database)
    command.upgrade(config, "20260716_13")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with Session(engine, expire_on_commit=False) as session:
        metadata = MetaData()
        metadata.reflect(bind=engine, only=["projects"])
        project_id = "project_migration_planning_authority"
        session.execute(metadata.tables["projects"].insert().values(
            id=project_id,
            title="Migration planning state",
            core_topic="Persisted candidate authority",
            duration_seconds=15,
            aspect_ratio="9:16",
            audio_mode="off",
            status="draft",
            row_version=1,
            state_changed_at=utc_now(),
            state_actor_type="system",
            state_changed_by="test",
            state_trigger="project_created",
            blocked_allowed_commands=[],
            created_at=utc_now(),
            updated_at=utc_now(),
        ))
        requirement = RequirementVersion(
            project_id=project_id,
            version_number=1,
            fields={},
            field_sources={},
        )
        session.add(requirement)
        session.flush()
        manifest = AgentInputManifest(
            project_id=project_id,
            base_requirement_version_id=requirement.id,
            input_hash="0" * 64,
            payload={},
        )
        session.add(manifest)
        session.flush()
        run = AgentRun(
            project_id=project_id,
            input_manifest_id=manifest.id,
            status="succeeded",
        )
        session.add(run)
        session.flush()
        brief = CreativeBriefCandidate(
            project_id=project_id,
            requirement_version_id=requirement.id,
            agent_run_id=run.id,
            status="accepted",
            brief={},
        )
        session.add(brief)
        session.flush()
        session.add(ShotPlanCandidate(
            project_id=project_id,
            requirement_version_id=requirement.id,
            creative_brief_candidate_id=brief.id,
            agent_run_id=run.id,
            status="awaiting_review",
            shots=[],
        ))
        session.commit()
    engine.dispose()

    command.upgrade(config, "head")

    upgraded_engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert "estimated_runtime_seconds" in {
        column["name"] for column in inspect(upgraded_engine).get_columns("pricing_rules")
    }
    assert "event_sequence" in {
        column["name"] for column in inspect(upgraded_engine).get_columns("projects")
    }
    assert {
        "event_id", "project_sequence", "aggregate_type", "aggregate_id",
        "correlation_id", "actor_type", "actor_id", "schema_version",
    }.issubset({column["name"] for column in inspect(upgraded_engine).get_columns("project_events")})
    assert "outbox_messages" in inspect(upgraded_engine).get_table_names()
    with Session(upgraded_engine) as session:
        upgraded = session.scalar(select(Project).where(Project.id == project_id))
        assert upgraded is not None
        assert upgraded.status == "plan_review"
        assert upgraded.row_version == 3
        assert upgraded.state_actor_type == "system"
        assert upgraded.state_changed_by == "migration"
        assert upgraded.state_trigger == "migration_planning_authority_backfill"
        assert upgraded.state_reason_code is None
    upgraded_engine.dispose()
