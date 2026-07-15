from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from v2.backend.app.db.models import (
    AgentInputManifest,
    AgentRun,
    Attachment,
    AttachmentBinding,
    ClarificationRequest,
    Decision,
    Entity,
    EntityVersion,
    Message,
    Project,
    ProjectEvent,
    RequirementCandidate,
    RequirementVersion,
    WorkItem,
    utc_now,
)
from v2.backend.app.db.session import Base
from v2.backend.app.repositories import (
    SqlAlchemyCommandRepository,
    SqlAlchemyCreationRepository,
    SqlAlchemyDecisionRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyProjectRepository,
)


def test_project_repository_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            repository = SqlAlchemyProjectRepository(session)
            earlier = Project(
                title="Earlier",
                core_topic="Repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=utc_now() - timedelta(minutes=1),
            )
            later = Project(
                title="Later",
                core_topic="Repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=utc_now(),
            )
            repository.add(earlier)
            repository.add(later)
            repository.flush()
            work_item = WorkItem(project_id=later.id, kind="contract_validation", payload={})
            repository.add_work_item(work_item)
            session.commit()

            assert [project.id for project in repository.list_recent()] == [later.id, earlier.id]
            loaded = repository.get(later.id, with_workspace=True)
            assert loaded is not None
            assert [item.id for item in loaded.work_items] == [work_item.id]
            assert repository.get("project_missing", with_workspace=True) is None
    finally:
        engine.dispose()


def test_event_repository_contract_preserves_project_cursor_order_and_limit() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            events = SqlAlchemyEventRepository(session)
            first = Project(
                title="First",
                core_topic="Event contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            second = Project(
                title="Second",
                core_topic="Event isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(first)
            projects.add(second)
            projects.flush()
            events.add(ProjectEvent(project_id=first.id, event_type="first", message="first"))
            events.add(ProjectEvent(project_id=second.id, event_type="other", message="other"))
            events.add(ProjectEvent(project_id=first.id, event_type="second", message="second"))
            session.commit()

            rows = events.list_after(first.id, 0, limit=1)
            assert [row.event_type for row in rows] == ["first"]
            remaining = events.list_after(first.id, rows[0].sequence, limit=100)
            assert [row.event_type for row in remaining] == ["second"]
    finally:
        engine.dispose()


def test_decision_repository_contract_scopes_keys_and_ids_to_project() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            decisions = SqlAlchemyDecisionRepository(session)
            first = Project(
                title="First",
                core_topic="Decision contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            second = Project(
                title="Second",
                core_topic="Decision isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(first)
            projects.add(second)
            projects.flush()
            first_decision = Decision(project_id=first.id, key="visual_style", label="Style")
            second_decision = Decision(project_id=second.id, key="visual_style", label="Other style")
            decisions.add(first_decision)
            decisions.add(second_decision)
            decisions.flush()
            session.commit()

            assert decisions.get_by_key(first.id, "visual_style").id == first_decision.id  # type: ignore[union-attr]
            assert decisions.get_by_key(second.id, "visual_style").id == second_decision.id  # type: ignore[union-attr]
            assert decisions.get_for_project(first.id, second_decision.id) is None
            assert decisions.get_by_key(first.id, "missing") is None
    finally:
        engine.dispose()


def test_command_repository_contract_preserves_receipt_and_project_isolation() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            commands = SqlAlchemyCommandRepository(session)
            first = Project(
                title="First",
                core_topic="Command contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            second = Project(
                title="Second",
                core_topic="Command isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(first)
            projects.add(second)
            projects.flush()
            commands.add(first.id, "command-001", "message.add", "message", "message-001")
            commands.add(second.id, "command-001", "asset.verify", "asset", "asset-001")
            session.commit()

            first_receipt = commands.get(first.id, "command-001")
            second_receipt = commands.get(second.id, "command-001")
            assert first_receipt is not None
            assert first_receipt.command_type == "message.add"
            assert first_receipt.result_type == "message"
            assert first_receipt.result_id == "message-001"
            assert second_receipt is not None
            assert second_receipt.command_type == "asset.verify"
            assert commands.get(first.id, "missing") is None
    finally:
        engine.dispose()


def test_creation_repository_contract_preserves_filters_order_and_project_scope() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            creation = SqlAlchemyCreationRepository(session)
            now = utc_now()
            project = Project(
                title="Creation",
                core_topic="Creation repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            other = Project(
                title="Other",
                core_topic="Project isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(project)
            projects.add(other)
            projects.flush()

            old_requirement = RequirementVersion(
                project_id=project.id,
                version_number=1,
                fields={"version": 1},
                is_active=False,
            )
            active_requirement = RequirementVersion(
                project_id=project.id,
                version_number=2,
                fields={"version": 2},
                is_active=True,
            )
            other_requirement = RequirementVersion(
                project_id=other.id,
                version_number=1,
                fields={"version": "other"},
                is_active=True,
            )
            for record in (old_requirement, active_requirement, other_requirement):
                creation.add(record)
            creation.flush()

            older_message = Message(
                project_id=project.id,
                content="older",
                created_at=now - timedelta(minutes=2),
            )
            newer_message = Message(
                project_id=project.id,
                content="newer",
                created_at=now - timedelta(minutes=1),
            )
            creation.add(newer_message)
            creation.add(older_message)
            creation.flush()

            manifest = AgentInputManifest(
                project_id=project.id,
                base_requirement_version_id=active_requirement.id,
                message_ids=[older_message.id, newer_message.id],
                input_hash="a" * 64,
                payload={},
            )
            creation.add(manifest)
            creation.flush()
            run = AgentRun(
                project_id=project.id,
                input_manifest_id=manifest.id,
                status="succeeded",
                started_at=now,
            )
            creation.add(run)
            creation.flush()
            candidate = RequirementCandidate(
                project_id=project.id,
                base_requirement_version_id=active_requirement.id,
                agent_run_id=run.id,
                fields={},
                created_at=now,
            )
            stale_candidate = RequirementCandidate(
                project_id=project.id,
                base_requirement_version_id=active_requirement.id,
                agent_run_id=run.id,
                fields={},
                status="stale",
                created_at=now - timedelta(minutes=1),
            )
            creation.add(candidate)
            creation.add(stale_candidate)

            current_clarification = ClarificationRequest(
                project_id=project.id,
                base_requirement_version_id=active_requirement.id,
                field_key="duration_seconds",
                question="Duration?",
                created_at=now,
            )
            old_clarification = ClarificationRequest(
                project_id=project.id,
                base_requirement_version_id=old_requirement.id,
                field_key="aspect_ratio",
                question="Ratio?",
                created_at=now - timedelta(minutes=1),
            )
            resolved_clarification = ClarificationRequest(
                project_id=project.id,
                base_requirement_version_id=active_requirement.id,
                field_key="audio_mode",
                question="Audio?",
                status="resolved",
            )
            creation.add(current_clarification)
            creation.add(old_clarification)
            creation.add(resolved_clarification)

            entity = Entity(
                id="character-main",
                project_id=project.id,
                entity_type="character",
                display_name="Main",
            )
            creation.add(entity)
            creation.flush()
            old_entity_version = EntityVersion(
                project_id=project.id,
                entity_id=entity.id,
                version_number=1,
                is_active=False,
            )
            active_entity_version = EntityVersion(
                project_id=project.id,
                entity_id=entity.id,
                version_number=2,
                is_active=True,
            )
            creation.add(old_entity_version)
            creation.add(active_entity_version)

            older_attachment = Attachment(
                project_id=project.id,
                original_filename="old.png",
                mime_type="image/png",
                byte_size=1,
                content_hash="b" * 64,
                storage_path="old.png",
                created_at=now - timedelta(minutes=2),
            )
            newer_attachment = Attachment(
                project_id=project.id,
                original_filename="new.png",
                mime_type="image/png",
                byte_size=1,
                content_hash="c" * 64,
                storage_path="new.png",
                created_at=now - timedelta(minutes=1),
            )
            creation.add(older_attachment)
            creation.add(newer_attachment)
            creation.flush()
            confirmed_binding = AttachmentBinding(
                project_id=project.id,
                attachment_id=older_attachment.id,
                binding_type="identity_reference",
                entity_id=entity.id,
                entity_version_id=active_entity_version.id,
                confirmed_at=now - timedelta(minutes=1),
            )
            pending_binding = AttachmentBinding(
                project_id=project.id,
                attachment_id=newer_attachment.id,
                binding_type="inspiration_only",
                status="pending",
                confirmed_at=now,
            )
            creation.add(confirmed_binding)
            creation.add(pending_binding)
            session.commit()

            assert creation.active_requirement(project.id).id == active_requirement.id  # type: ignore[union-attr]
            assert creation.active_requirement(other.id).id == other_requirement.id  # type: ignore[union-attr]
            assert creation.requirement_candidate(candidate.id).id == candidate.id  # type: ignore[union-attr]
            assert creation.agent_run(run.id).id == run.id  # type: ignore[union-attr]
            assert creation.agent_manifest(manifest.id).id == manifest.id  # type: ignore[union-attr]
            assert creation.message(newer_message.id).project_id == project.id  # type: ignore[union-attr]
            assert [row.id for row in creation.manifest_messages(project.id)] == [older_message.id, newer_message.id]
            assert [row.id for row in creation.view_messages(project.id)] == [older_message.id, newer_message.id]
            assert [row.id for row in creation.reviewable_candidates(project.id)] == [candidate.id]
            assert creation.reviewable_candidates(project.id, exclude_id=candidate.id) == []
            assert [row.id for row in creation.candidate_history(project.id)] == [candidate.id, stale_candidate.id]
            assert {row.id for row in creation.pending_clarifications(project.id)} == {
                current_clarification.id,
                old_clarification.id,
            }
            assert [row.id for row in creation.active_pending_clarifications(project.id, active_requirement.id)] == [
                current_clarification.id
            ]
            assert creation.clarification(current_clarification.id).id == current_clarification.id  # type: ignore[union-attr]
            assert creation.entity(entity.id).id == entity.id  # type: ignore[union-attr]
            assert creation.entity_version(old_entity_version.id).id == old_entity_version.id  # type: ignore[union-attr]
            assert creation.active_entity_version(entity.id).id == active_entity_version.id  # type: ignore[union-attr]
            assert [row.id for row in creation.attachments(project.id)] == [newer_attachment.id, older_attachment.id]
            assert creation.attachment(older_attachment.id).id == older_attachment.id  # type: ignore[union-attr]
            assert [row.id for row in creation.confirmed_bindings(project.id)] == [confirmed_binding.id]
            assert [row.id for row in creation.bindings(project.id)] == [confirmed_binding.id, pending_binding.id]
            assert [row.id for row in creation.agent_runs(project.id)] == [run.id]
            assert creation.view_messages(other.id) == []
    finally:
        engine.dispose()
