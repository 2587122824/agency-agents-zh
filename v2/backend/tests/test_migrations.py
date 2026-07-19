from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select
from sqlalchemy.orm import Session

from v2.backend.app.db.models import (
    AgentInputManifest,
    AgentRun,
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
        metadata.reflect(bind=engine, only=["projects", "agent_runs", "creative_brief_candidates"])
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
        run_id = "agent_run_migration_planning_authority"
        session.execute(metadata.tables["agent_runs"].insert().values(
            id=run_id,
            project_id=project_id,
            input_manifest_id=manifest.id,
            status="succeeded",
            agent_role="creative",
            model_provider="mock",
            model_name="deterministic-creative-v1",
            prompt_contract_version="creative.v1",
            output_schema_version="requirement-candidate.v1",
        ))
        brief_id = "brief_candidate_migration_planning_authority"
        session.execute(metadata.tables["creative_brief_candidates"].insert().values(
            id=brief_id,
            project_id=project_id,
            requirement_version_id=requirement.id,
            agent_run_id=run_id,
            status="accepted",
            brief={},
            field_sources={},
            validation_errors=[],
            created_at=utc_now(),
        ))
        session.add(ShotPlanCandidate(
            project_id=project_id,
            requirement_version_id=requirement.id,
            creative_brief_candidate_id=brief_id,
            agent_run_id=run_id,
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
    assert {"archived_at", "archived_by"}.issubset({
        column["name"] for column in inspect(upgraded_engine).get_columns("projects")
    })
    assert "agent_run_id" in {
        column["name"] for column in inspect(upgraded_engine).get_columns("creation_messages")
    }
    assert {
        "production_config_version_id", "model_config_version_id", "provider_config_version_id",
        "provider_request_id", "token_usage",
    }.issubset({column["name"] for column in inspect(upgraded_engine).get_columns("agent_runs")})
    assert {
        "event_id", "project_sequence", "aggregate_type", "aggregate_id",
        "correlation_id", "actor_type", "actor_id", "schema_version",
    }.issubset({column["name"] for column in inspect(upgraded_engine).get_columns("project_events")})
    assert "outbox_messages" in inspect(upgraded_engine).get_table_names()
    assert {
        "product_entity_version_ids",
        "primary_reference_entity_version_id",
        "visual_prompt",
        "negative_prompt",
    }.issubset({column["name"] for column in inspect(upgraded_engine).get_columns("shots")})
    with Session(upgraded_engine) as session:
        upgraded = session.scalar(select(Project).where(Project.id == project_id))
        assert upgraded is not None
        assert upgraded.status == "plan_review"
        assert upgraded.row_version == 3
        assert upgraded.state_actor_type == "system"
        assert upgraded.state_changed_by == "migration"
        assert upgraded.state_trigger == "migration_planning_authority_backfill"
        assert upgraded.state_reason_code is None
        assert upgraded.archived_at is None
        assert upgraded.archived_by is None
    upgraded_engine.dispose()
