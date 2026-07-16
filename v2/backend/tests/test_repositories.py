from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from v2.backend.app.db.models import (
    AgentInputManifest,
    AgentRun,
    Asset,
    AssetReviewDecision,
    Attachment,
    AttachmentBinding,
    ClarificationRequest,
    CreativeBriefCandidate,
    DAGNode,
    Decision,
    DependencyEdge,
    Entity,
    EntityVersion,
    Message,
    PlanVersion,
    Project,
    ProjectEvent,
    ProductionImpactAnalysis,
    ProductionSnapshot,
    QCFinding,
    QCReport,
    RequirementCandidate,
    RequirementVersion,
    Shot,
    ShotPlanCandidate,
    SnapshotEntityVersion,
    Timeline,
    TimelineItem,
    WorkAttempt,
    WorkItem,
    utc_now,
)
from v2.backend.app.db.session import Base
from v2.backend.app.repositories import (
    SqlAlchemyCommandRepository,
    SqlAlchemyCreationRepository,
    SqlAlchemyDecisionRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyEditorRepository,
    SqlAlchemyPlanningRepository,
    SqlAlchemyProductionRepository,
    SqlAlchemyQualityRepository,
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


def test_planning_repository_contract_preserves_versions_history_and_shot_order() -> None:
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
            planning = SqlAlchemyPlanningRepository(session)
            now = utc_now()
            project = Project(
                title="Planning",
                core_topic="Planning repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            other = Project(
                title="Other",
                core_topic="Planning isolation",
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
            requirement = RequirementVersion(
                project_id=project.id,
                version_number=2,
                fields={"version": 2},
                is_active=True,
            )
            creation.add(old_requirement)
            creation.add(requirement)
            creation.flush()
            manifest = AgentInputManifest(
                project_id=project.id,
                base_requirement_version_id=requirement.id,
                input_hash="d" * 64,
                payload={},
            )
            planning.add(manifest)
            planning.flush()
            run = AgentRun(
                project_id=project.id,
                input_manifest_id=manifest.id,
                status="succeeded",
                started_at=now,
            )
            planning.add(run)
            planning.flush()
            old_brief = CreativeBriefCandidate(
                project_id=project.id,
                requirement_version_id=old_requirement.id,
                agent_run_id=run.id,
                status="accepted",
                brief={"version": 1},
                created_at=now - timedelta(minutes=2),
            )
            current_brief = CreativeBriefCandidate(
                project_id=project.id,
                requirement_version_id=requirement.id,
                agent_run_id=run.id,
                brief={"version": 2},
                created_at=now - timedelta(minutes=1),
            )
            planning.add(old_brief)
            planning.add(current_brief)
            planning.flush()
            old_shot_plan = ShotPlanCandidate(
                project_id=project.id,
                requirement_version_id=old_requirement.id,
                creative_brief_candidate_id=old_brief.id,
                agent_run_id=run.id,
                status="accepted",
                shots=[],
                created_at=now - timedelta(minutes=2),
            )
            current_shot_plan = ShotPlanCandidate(
                project_id=project.id,
                requirement_version_id=requirement.id,
                creative_brief_candidate_id=current_brief.id,
                agent_run_id=run.id,
                shots=[],
                created_at=now - timedelta(minutes=1),
            )
            planning.add(old_shot_plan)
            planning.add(current_shot_plan)
            planning.flush()
            old_plan = PlanVersion(
                project_id=project.id,
                version_number=1,
                requirement_version_id=old_requirement.id,
                shot_plan_candidate_id=old_shot_plan.id,
                creative_brief=old_brief.brief,
                status="superseded",
                is_active=False,
            )
            active_plan = PlanVersion(
                project_id=project.id,
                version_number=2,
                requirement_version_id=requirement.id,
                shot_plan_candidate_id=current_shot_plan.id,
                creative_brief=current_brief.brief,
                is_active=True,
            )
            planning.add(old_plan)
            planning.add(active_plan)
            planning.flush()
            later_shot = Shot(
                project_id=project.id,
                plan_version_id=active_plan.id,
                shot_code="SH-002",
                sequence_number=2,
                duration_ms=5000,
                shot_type="concept",
                face_visibility="not_visible",
                text_policy="forbidden",
                motion_requirement="moderate",
                composition="Second",
                action="Second",
            )
            earlier_shot = Shot(
                project_id=project.id,
                plan_version_id=active_plan.id,
                shot_code="SH-001",
                sequence_number=1,
                duration_ms=5000,
                shot_type="concept",
                face_visibility="not_visible",
                text_policy="forbidden",
                motion_requirement="moderate",
                composition="First",
                action="First",
            )
            planning.add(later_shot)
            planning.add(earlier_shot)

            entity = Entity(
                id="scene-main",
                project_id=project.id,
                entity_type="scene",
                display_name="Scene",
            )
            creation.add(entity)
            creation.flush()
            entity_version = EntityVersion(
                project_id=project.id,
                entity_id=entity.id,
                version_number=1,
                status="confirmed",
                is_active=True,
            )
            creation.add(entity_version)
            attachment = Attachment(
                project_id=project.id,
                original_filename="scene.png",
                mime_type="image/png",
                byte_size=1,
                content_hash="e" * 64,
                storage_path="scene.png",
            )
            creation.add(attachment)
            creation.flush()
            binding = AttachmentBinding(
                project_id=project.id,
                attachment_id=attachment.id,
                binding_type="scene_reference",
                entity_id=entity.id,
                entity_version_id=entity_version.id,
            )
            creation.add(binding)
            session.commit()

            assert planning.active_brief_for_requirement(project.id, requirement.id).id == current_brief.id  # type: ignore[union-attr]
            assert planning.creative_brief(current_brief.id).project_id == project.id  # type: ignore[union-attr]
            assert planning.reviewable_shot_plan_for_requirement(project.id, requirement.id).id == current_shot_plan.id  # type: ignore[union-attr]
            assert planning.shot_plan(current_shot_plan.id).project_id == project.id  # type: ignore[union-attr]
            assert [row.id for row in planning.active_plans(project.id)] == [active_plan.id]
            assert planning.next_plan_version_number(project.id) == 3
            assert [row.id for row in planning.shots(active_plan.id)] == [earlier_shot.id, later_shot.id]
            assert [row.id for row in planning.brief_history(project.id)] == [current_brief.id, old_brief.id]
            assert [row.id for row in planning.shot_plan_history(project.id)] == [current_shot_plan.id, old_shot_plan.id]
            assert [row.id for row in planning.plan_history(project.id)] == [active_plan.id, old_plan.id]
            assert [row.id for row in planning.confirmed_binding_versions(project.id)] == [binding.id]
            assert planning.confirmed_binding_ids(project.id) == [binding.id]
            assert planning.entity_version(entity_version.id).id == entity_version.id  # type: ignore[union-attr]
            assert [(version.id, row.id) for version, row in planning.active_entity_versions(project.id)] == [
                (entity_version.id, entity.id)
            ]
            assert planning.brief_history(other.id) == []
            assert planning.next_plan_version_number(other.id) == 1
    finally:
        engine.dispose()


def test_production_repository_contract_preserves_snapshot_dag_and_work_order() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            production = SqlAlchemyProductionRepository(session)
            now = utc_now()
            project = Project(
                title="Production",
                core_topic="Production repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            other = Project(
                title="Other",
                core_topic="Production isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(project)
            projects.add(other)
            projects.flush()
            old_analysis = ProductionImpactAnalysis(
                project_id=project.id,
                plan_version_id="plan-old",
                production_config_version_id="config-1",
                selection={},
                manifest={},
                analysis_hash="a" * 64,
                created_at=now - timedelta(minutes=2),
            )
            current_analysis = ProductionImpactAnalysis(
                project_id=project.id,
                plan_version_id="plan-current",
                production_config_version_id="config-1",
                selection={},
                manifest={},
                analysis_hash="b" * 64,
                created_at=now - timedelta(minutes=1),
            )
            production.add(old_analysis)
            production.add(current_analysis)
            production.flush()
            old_snapshot = ProductionSnapshot(
                project_id=project.id,
                plan_version_id="plan-old",
                production_config_version_id="config-1",
                impact_analysis_id=old_analysis.id,
                snapshot_number=1,
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="c" * 64,
            )
            current_snapshot = ProductionSnapshot(
                project_id=project.id,
                plan_version_id="plan-current",
                production_config_version_id="config-1",
                impact_analysis_id=current_analysis.id,
                snapshot_number=2,
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="d" * 64,
            )
            production.add(old_snapshot)
            production.add(current_snapshot)
            production.flush()
            character_ref = SnapshotEntityVersion(
                snapshot_id=current_snapshot.id,
                entity_version_id="entity-character",
                role="character",
            )
            scene_ref = SnapshotEntityVersion(
                snapshot_id=current_snapshot.id,
                entity_version_id="entity-scene",
                role="scene",
            )
            production.add(scene_ref)
            production.add(character_ref)
            later_node = DAGNode(
                snapshot_id=current_snapshot.id,
                node_key="shot.002.video",
                kind="generate_i2v_clip",
                input_contract={},
                output_contract={},
            )
            earlier_node = DAGNode(
                snapshot_id=current_snapshot.id,
                node_key="shot.001.keyframe",
                kind="generate_keyframe",
                input_contract={},
                output_contract={},
            )
            production.add(later_node)
            production.add(earlier_node)
            production.flush()
            edge = DependencyEdge(
                snapshot_id=current_snapshot.id,
                parent_node_id=earlier_node.id,
                child_node_id=later_node.id,
                dependency_type="required",
                input_slot="source_image",
            )
            production.add(edge)
            work = WorkItem(
                project_id=project.id,
                snapshot_id=current_snapshot.id,
                dag_node_id=earlier_node.id,
                kind=earlier_node.kind,
                payload={},
                created_at=now,
            )
            production.add(work)
            production.flush()
            second_attempt = WorkAttempt(
                work_item_id=work.id,
                attempt_number=2,
                trigger="contract_fixture",
                provider="mock",
                request_fingerprint="f" * 64,
                request_manifest={},
                state="created",
            )
            first_attempt = WorkAttempt(
                work_item_id=work.id,
                attempt_number=1,
                trigger="explicit_submission",
                provider="mock",
                request_fingerprint="e" * 64,
                request_manifest={},
                state="created",
            )
            production.add(second_attempt)
            production.add(first_attempt)
            session.commit()

            assert production.impact_analysis(current_analysis.id).id == current_analysis.id  # type: ignore[union-attr]
            assert production.snapshot(current_snapshot.id).id == current_snapshot.id  # type: ignore[union-attr]
            assert production.component(ProductionSnapshot, current_snapshot.id).id == current_snapshot.id  # type: ignore[union-attr]
            assert production.snapshot_for_impact(current_analysis.id).id == current_snapshot.id  # type: ignore[union-attr]
            assert production.next_snapshot_number(project.id) == 3
            assert production.next_snapshot_number(other.id) == 1
            assert [row.id for row in production.snapshot_history(project.id)] == [current_snapshot.id, old_snapshot.id]
            assert [row.id for row in production.impact_history(project.id)] == [current_analysis.id, old_analysis.id]
            assert [row.id for row in production.snapshot_entities(current_snapshot.id)] == [character_ref.id, scene_ref.id]
            assert [row.id for row in production.snapshot_nodes(current_snapshot.id, ordered=True)] == [
                earlier_node.id,
                later_node.id,
            ]
            assert [row.id for row in production.snapshot_edges(current_snapshot.id)] == [edge.id]
            assert production.has_work_items(current_snapshot.id) is True
            assert production.has_work_items(old_snapshot.id) is False
            assert [row.id for row in production.work_items(current_snapshot.id)] == [work.id]
            assert [row.id for row in production.work_attempts([work.id])] == [first_attempt.id, second_attempt.id]
            assert production.work_attempts([]) == []
            assert production.snapshot_history(other.id) == []
    finally:
        engine.dispose()


def test_quality_repository_contract_preserves_asset_qc_review_and_dag_queries() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            quality = SqlAlchemyQualityRepository(session)
            production = SqlAlchemyProductionRepository(session)
            now = utc_now()
            project = Project(
                title="Quality",
                core_topic="Quality repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            other = Project(
                title="Other",
                core_topic="Quality isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(project)
            projects.add(other)
            projects.flush()
            parent_node = DAGNode(
                snapshot_id="snapshot-quality",
                node_key="shot.001.keyframe",
                kind="generate_keyframe",
                input_contract={},
                output_contract={"media_type": "image"},
            )
            child_node = DAGNode(
                snapshot_id="snapshot-quality",
                node_key="shot.001.video",
                kind="generate_i2v_clip",
                input_contract={},
                output_contract={"media_type": "video"},
            )
            production.add(parent_node)
            production.add(child_node)
            production.flush()
            edge = DependencyEdge(
                snapshot_id="snapshot-quality",
                parent_node_id=parent_node.id,
                child_node_id=child_node.id,
                dependency_type="required",
                input_slot="source_image",
            )
            production.add(edge)
            work = WorkItem(
                project_id=project.id,
                snapshot_id="snapshot-quality",
                dag_node_id=parent_node.id,
                kind=parent_node.kind,
                payload={},
            )
            production.add(work)
            production.flush()
            attempt = WorkAttempt(
                work_item_id=work.id,
                attempt_number=1,
                trigger="explicit_submission",
                provider="mock",
                request_fingerprint="a" * 64,
                request_manifest={},
                state="completed",
            )
            production.add(attempt)
            production.flush()
            older_asset = Asset(
                project_id=project.id,
                snapshot_id="snapshot-quality",
                work_attempt_id=attempt.id,
                dag_node_id=parent_node.id,
                output_index=0,
                asset_type="image",
                role="keyframe",
                uri="runtime://assets/old.png",
                storage_backend="local",
                provider_output_manifest={},
                created_at=now - timedelta(minutes=2),
            )
            newer_asset = Asset(
                project_id=project.id,
                snapshot_id="snapshot-quality",
                work_attempt_id=None,
                dag_node_id=child_node.id,
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/new.mp4",
                storage_backend="local",
                provider_output_manifest={},
                created_at=now - timedelta(minutes=1),
            )
            quality.add(older_asset)
            quality.add(newer_asset)
            quality.flush()
            old_report = QCReport(
                project_id=project.id,
                snapshot_id="snapshot-quality",
                asset_id=older_asset.id,
                report_number=1,
                ruleset_version="v1",
                status="review_required",
                analyzer="contract",
            )
            latest_report = QCReport(
                project_id=project.id,
                snapshot_id="snapshot-quality",
                asset_id=older_asset.id,
                report_number=2,
                ruleset_version="v1",
                status="passed",
                analyzer="contract",
            )
            quality.add(old_report)
            quality.add(latest_report)
            quality.flush()
            later_finding = QCFinding(
                qc_report_id=latest_report.id,
                code="SECOND",
                severity="review_required",
                disposition="manual_review",
                created_at=now,
            )
            earlier_finding = QCFinding(
                qc_report_id=latest_report.id,
                code="FIRST",
                severity="review_required",
                disposition="manual_review",
                created_at=now - timedelta(minutes=1),
            )
            decision = AssetReviewDecision(
                project_id=project.id,
                asset_id=older_asset.id,
                qc_report_id=old_report.id,
                decision="approved",
                rationale="Contract fixture",
                actor_id="tester",
            )
            quality.add(later_finding)
            quality.add(earlier_finding)
            quality.add(decision)
            session.commit()

            assert quality.asset(older_asset.id).id == older_asset.id  # type: ignore[union-attr]
            assert quality.work_attempt(attempt.id).id == attempt.id  # type: ignore[union-attr]
            assert quality.work_item(work.id).id == work.id  # type: ignore[union-attr]
            assert quality.dag_node(parent_node.id).id == parent_node.id  # type: ignore[union-attr]
            assert quality.asset_for_output(attempt.id, 0).id == older_asset.id  # type: ignore[union-attr]
            assert quality.work_item_for_node("snapshot-quality", parent_node.id).id == work.id  # type: ignore[union-attr]
            assert quality.qc_report(latest_report.id).id == latest_report.id  # type: ignore[union-attr]
            assert quality.next_report_number(older_asset.id) == 3
            assert quality.has_review_decision(old_report.id) is True
            assert quality.has_review_decision(latest_report.id) is False
            assert [row.id for row in quality.findings(latest_report.id)] == [earlier_finding.id, later_finding.id]
            assert [row.id for row in quality.dependency_edges("snapshot-quality")] == [edge.id]
            assert {row.id for row in quality.dag_nodes_by_ids({parent_node.id, child_node.id})} == {
                parent_node.id,
                child_node.id,
            }
            assert quality.latest_qc_report(older_asset.id).id == latest_report.id  # type: ignore[union-attr]
            assert [row.id for row in quality.review_decisions(older_asset.id)] == [decision.id]
            assert [row.id for row in quality.project_assets(project.id)] == [newer_asset.id, older_asset.id]
            assert [row.id for row in quality.snapshot_nodes("snapshot-quality")] == [parent_node.id, child_node.id]
            assert quality.project_assets(other.id) == []
            assert quality.dag_nodes_by_ids(set()) == []
    finally:
        engine.dispose()


def test_editor_repository_contract_preserves_timeline_versions_items_and_asset_bin() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            editor = SqlAlchemyEditorRepository(session)
            production = SqlAlchemyProductionRepository(session)
            quality = SqlAlchemyQualityRepository(session)
            now = utc_now()
            project = Project(
                title="Editor",
                core_topic="Editor repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            other = Project(
                title="Other",
                core_topic="Editor isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(project)
            projects.add(other)
            projects.flush()
            video_node = DAGNode(
                snapshot_id="snapshot-editor",
                node_key="shot.001.video",
                kind="generate_i2v_clip",
                input_contract={},
                output_contract={"media_type": "video"},
            )
            audio_node = DAGNode(
                snapshot_id="snapshot-editor",
                node_key="project.audio",
                kind="generate_tts",
                input_contract={},
                output_contract={"media_type": "audio"},
            )
            production.add(video_node)
            production.add(audio_node)
            production.flush()
            video_asset = Asset(
                project_id=project.id,
                snapshot_id="snapshot-editor",
                dag_node_id=video_node.id,
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/editor-video.mp4",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
                duration_ms=10000,
                created_at=now,
            )
            audio_asset = Asset(
                project_id=project.id,
                snapshot_id="snapshot-editor",
                dag_node_id=audio_node.id,
                output_index=0,
                asset_type="audio",
                role="voiceover",
                uri="runtime://assets/editor-audio.wav",
                storage_backend="local",
                provider_output_manifest={},
                state="used",
                duration_ms=10000,
                created_at=now,
            )
            excluded_asset = Asset(
                project_id=project.id,
                snapshot_id="snapshot-editor",
                output_index=0,
                asset_type="image",
                role="keyframe",
                uri="runtime://assets/editor-image.png",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
                created_at=now,
            )
            quality.add(video_asset)
            quality.add(audio_asset)
            quality.add(excluded_asset)
            quality.flush()
            confirmed = Timeline(
                project_id=project.id,
                snapshot_id="snapshot-editor",
                version_number=1,
                status="confirmed",
                source="user",
                output_spec={},
                track_config={},
            )
            editor.add(confirmed)
            editor.flush()
            candidate = Timeline(
                project_id=project.id,
                snapshot_id="snapshot-editor",
                version_number=2,
                status="candidate",
                source="user",
                output_spec={},
                track_config={},
                supersedes_timeline_id=confirmed.id,
            )
            editor.add(candidate)
            editor.flush()
            later_item = TimelineItem(
                timeline_id=candidate.id,
                track_type="main_video",
                sequence_number=2,
                asset_id=video_asset.id,
                label="Second",
                source_in_ms=5000,
                source_out_ms=10000,
                timeline_in_ms=5000,
                timeline_out_ms=10000,
            )
            earlier_item = TimelineItem(
                timeline_id=candidate.id,
                track_type="main_video",
                sequence_number=1,
                asset_id=video_asset.id,
                label="First",
                source_in_ms=0,
                source_out_ms=5000,
                timeline_in_ms=0,
                timeline_out_ms=5000,
            )
            editor.add(later_item)
            editor.add(earlier_item)
            session.commit()

            assert editor.timeline(candidate.id).id == candidate.id  # type: ignore[union-attr]
            assert editor.has_timeline(project.id) is True
            assert editor.has_timeline(other.id) is False
            assert editor.next_timeline_version(project.id) == 3
            assert editor.next_timeline_version(other.id) == 1
            assert [row.id for row in editor.timeline_items(candidate.id)] == [earlier_item.id, later_item.id]
            assert [row.id for row in editor.confirmed_timelines(project.id, exclude_id=candidate.id)] == [confirmed.id]
            assert editor.timeline_asset_ids(candidate.id) == [video_asset.id, video_asset.id]
            assert {row.id for row in editor.assets_by_ids([video_asset.id, audio_asset.id])} == {
                video_asset.id,
                audio_asset.id,
            }
            assert [row.id for row in editor.available_assets(project.id, "snapshot-editor")] == [
                audio_asset.id,
                video_asset.id,
            ]
            assert {row.id for row in editor.dag_nodes_by_ids([video_node.id, audio_node.id])} == {
                video_node.id,
                audio_node.id,
            }
            assert [row.id for row in editor.timeline_history(project.id)] == [candidate.id, confirmed.id]
            assert editor.timeline_history(other.id) == []
            assert editor.assets_by_ids([]) == []
            assert editor.dag_nodes_by_ids([]) == []
    finally:
        engine.dispose()
