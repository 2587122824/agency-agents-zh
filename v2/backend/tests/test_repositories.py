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
    ConfigurationCommandReceipt,
    ConfigurationReference,
    CostEvent,
    CreativeBriefCandidate,
    DAGNode,
    Decision,
    DecisionChangeImpactAnalysis,
    DecisionChangeImpactTarget,
    DeliveryAttempt,
    DependencyEdge,
    Entity,
    EntityVersion,
    Message,
    PricingCatalogVersion,
    PricingRule,
    PlanVersion,
    Project,
    ProjectEvent,
    ProductionConfigComponent,
    ProductionConfigVersion,
    ProductionImpactAnalysis,
    ProductionSnapshot,
    ProviderConfigVersion,
    QCFinding,
    QCReport,
    RequirementCandidate,
    RequirementVersion,
    Shot,
    ShotPlanCandidate,
    SnapshotEntityVersion,
    Timeline,
    TimelineItem,
    WorkflowSlotVersion,
    WorkAttempt,
    WorkItem,
    utc_now,
)
from v2.backend.app.db.session import Base
from v2.backend.app.repositories import (
    SqlAlchemyCommandRepository,
    SqlAlchemyConfigurationRepository,
    SqlAlchemyContactSheetRepository,
    SqlAlchemyControlRepository,
    SqlAlchemyCreationRepository,
    SqlAlchemyDecisionRepository,
    SqlAlchemyDeliveryRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyImpactRepository,
    SqlAlchemyEditorRepository,
    SqlAlchemyPlanningRepository,
    SqlAlchemyProductionRepository,
    SqlAlchemyQualityRepository,
    SqlAlchemyRegistryRepository,
    SqlAlchemyWorkRepository,
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
            events.add(ProjectEvent(project_id=first.id, event_type="project.first.v1", aggregate_type="project", aggregate_id=first.id, actor_type="system", actor_id="test", message="first"))
            events.add(ProjectEvent(project_id=second.id, event_type="project.other.v1", aggregate_type="project", aggregate_id=second.id, actor_type="system", actor_id="test", message="other"))
            events.add(ProjectEvent(project_id=first.id, event_type="project.second.v1", aggregate_type="project", aggregate_id=first.id, actor_type="system", actor_id="test", message="second"))
            session.commit()

            rows = events.list_after(first.id, 0, limit=1)
            assert [row.event_type for row in rows] == ["project.first.v1"]
            remaining = events.list_after(first.id, rows[0].project_sequence, limit=100)
            assert [row.event_type for row in remaining] == ["project.second.v1"]
            assert [row.project_sequence for row in rows + remaining] == [1, 2]
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
            first_decision = Decision(
                project_id=first.id,
                key="visual_style",
                label="Style",
                value="documentary",
                status="resolved",
                created_at=utc_now() - timedelta(minutes=1),
            )
            later_resolved = Decision(
                project_id=first.id,
                key="audio_mode",
                label="Audio",
                value="off",
                status="resolved",
                created_at=utc_now(),
            )
            second_decision = Decision(project_id=second.id, key="visual_style", label="Other style")
            decisions.add(first_decision)
            decisions.add(later_resolved)
            decisions.add(second_decision)
            decisions.flush()
            session.commit()

            assert decisions.get_by_key(first.id, "visual_style").id == first_decision.id  # type: ignore[union-attr]
            assert decisions.get_by_key(second.id, "visual_style").id == second_decision.id  # type: ignore[union-attr]
            assert decisions.get_for_project(first.id, second_decision.id) is None
            assert decisions.get_by_key(first.id, "missing") is None
            assert [row.id for row in decisions.resolved_for_project(first.id)] == [
                first_decision.id,
                later_resolved.id,
            ]
            assert decisions.resolved_for_project(second.id) == []
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
            assert planning.transition_reviewable_shot_plan(
                current_shot_plan.id,
                1,
                "superseded",
                now,
            ) is True
            assert planning.transition_reviewable_shot_plan(
                current_shot_plan.id,
                1,
                "accepted",
                now,
            ) is False
            assert current_shot_plan.status == "superseded"
            assert current_shot_plan.row_version == 2
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


def test_delivery_repository_contract_preserves_confirmed_scope_attempts_and_uri_lookup() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            delivery = SqlAlchemyDeliveryRepository(session)
            quality = SqlAlchemyQualityRepository(session)
            project = Project(
                title="Delivery",
                core_topic="Delivery repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                active_snapshot_id="snapshot-delivery",
            )
            other = Project(
                title="Other",
                core_topic="Delivery isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                active_snapshot_id="snapshot-other",
            )
            projects.add(project)
            projects.add(other)
            projects.flush()
            input_asset = Asset(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/delivery-input.mp4",
                storage_backend="local",
                provider_output_manifest={},
                content_hash="a" * 64,
                state="used",
            )
            final_asset = Asset(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                output_index=0,
                asset_type="final_delivery",
                role="final_delivery",
                uri="runtime://assets/delivery-final.mp4",
                storage_backend="local",
                provider_output_manifest={},
                state="verified",
            )
            quality.add(input_asset)
            quality.add(final_asset)
            quality.flush()
            confirmed = Timeline(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                version_number=1,
                status="confirmed",
                source="user",
                output_spec={},
                track_config={},
                contract_hash="b" * 64,
            )
            delivery.add(confirmed)
            delivery.flush()
            exported = Timeline(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                version_number=2,
                status="exported",
                source="user",
                output_spec={},
                track_config={},
                supersedes_timeline_id=confirmed.id,
                contract_hash="c" * 64,
            )
            delivery.add(exported)
            delivery.flush()
            later_item = TimelineItem(
                timeline_id=confirmed.id,
                track_type="main_video",
                sequence_number=2,
                asset_id=input_asset.id,
                label="Second",
                source_in_ms=5000,
                source_out_ms=10000,
                timeline_in_ms=5000,
                timeline_out_ms=10000,
            )
            earlier_item = TimelineItem(
                timeline_id=confirmed.id,
                track_type="main_video",
                sequence_number=1,
                asset_id=input_asset.id,
                label="First",
                source_in_ms=0,
                source_out_ms=5000,
                timeline_in_ms=0,
                timeline_out_ms=5000,
            )
            delivery.add(later_item)
            delivery.add(earlier_item)
            first_attempt = DeliveryAttempt(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                timeline_id=confirmed.id,
                attempt_number=1,
                status="authorized",
                execution_kind="external_upload",
                request_manifest={},
                request_fingerprint="d" * 64,
            )
            second_attempt = DeliveryAttempt(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                timeline_id=exported.id,
                attempt_number=2,
                status="verified",
                execution_kind="external_upload",
                request_manifest={},
                request_fingerprint="e" * 64,
                final_asset_id=final_asset.id,
            )
            delivery.add(first_attempt)
            delivery.add(second_attempt)
            report_one = QCReport(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                asset_id=final_asset.id,
                report_number=1,
                ruleset_version="delivery.v1",
                status="blocked",
                analyzer="contract",
            )
            report_two = QCReport(
                project_id=project.id,
                snapshot_id="snapshot-delivery",
                asset_id=final_asset.id,
                report_number=2,
                ruleset_version="delivery.v1",
                status="passed",
                analyzer="contract",
            )
            delivery.add(report_one)
            delivery.add(report_two)
            session.commit()

            assert delivery.attempt(first_attempt.id).id == first_attempt.id  # type: ignore[union-attr]
            assert [row.id for row in delivery.confirmed_timelines(project.id, "snapshot-delivery")] == [
                confirmed.id
            ]
            assert [
                row.id
                for row in delivery.confirmed_timelines(
                    project.id,
                    "snapshot-delivery",
                    timeline_id=confirmed.id,
                )
            ] == [confirmed.id]
            assert delivery.confirmed_timelines(project.id, "snapshot-delivery", timeline_id=exported.id) == []
            assert [row.id for row in delivery.timeline_items(confirmed.id)] == [earlier_item.id, later_item.id]
            assert {row.id for row in delivery.assets_by_ids([input_asset.id, final_asset.id])} == {
                input_asset.id,
                final_asset.id,
            }
            assert delivery.has_attempt_for_timeline(confirmed.id) is True
            assert delivery.asset_by_uri("local", final_asset.uri).id == final_asset.id  # type: ignore[union-attr]
            assert delivery.next_report_number(final_asset.id) == 3
            assert delivery.asset(final_asset.id).id == final_asset.id  # type: ignore[union-attr]
            assert [row.id for row in delivery.delivery_timelines(project.id, "snapshot-delivery")] == [
                exported.id,
                confirmed.id,
            ]
            assert [row.id for row in delivery.project_attempts(project.id)] == [second_attempt.id, first_attempt.id]
            assert delivery.project_attempts(other.id) == []
            assert delivery.assets_by_ids([]) == []
    finally:
        engine.dispose()


def test_work_repository_contract_preserves_candidate_order_dependencies_and_atomic_claim() -> None:
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
            work_repository = SqlAlchemyWorkRepository(session)
            now = utc_now()
            project = Project(
                title="Worker",
                core_topic="Work repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(project)
            projects.flush()
            parent_node = DAGNode(
                snapshot_id="snapshot-worker",
                node_key="shot.001.keyframe",
                kind="generate_keyframe",
                input_contract={},
                output_contract={},
            )
            child_node = DAGNode(
                snapshot_id="snapshot-worker",
                node_key="shot.001.video",
                kind="generate_i2v_clip",
                input_contract={},
                output_contract={},
            )
            production.add(parent_node)
            production.add(child_node)
            production.flush()
            edge = DependencyEdge(
                snapshot_id="snapshot-worker",
                parent_node_id=parent_node.id,
                child_node_id=child_node.id,
                dependency_type="required",
                input_slot="source_image",
            )
            production.add(edge)
            parent = WorkItem(
                project_id=project.id,
                snapshot_id="snapshot-worker",
                dag_node_id=parent_node.id,
                kind=parent_node.kind,
                payload={},
                status="completed",
                priority=20,
                available_at=now - timedelta(minutes=1),
                created_at=now - timedelta(minutes=2),
            )
            child = WorkItem(
                project_id=project.id,
                snapshot_id="snapshot-worker",
                dag_node_id=child_node.id,
                kind=child_node.kind,
                payload={},
                status="queued",
                priority=10,
                request_fingerprint="a" * 64,
                available_at=now - timedelta(minutes=1),
                created_at=now - timedelta(minutes=1),
            )
            future = WorkItem(
                project_id=project.id,
                snapshot_id="snapshot-other",
                kind="future",
                payload={},
                status="queued",
                priority=1,
                available_at=now + timedelta(minutes=1),
            )
            production.add(parent)
            production.add(child)
            production.add(future)
            production.flush()
            attempt = WorkAttempt(
                work_item_id=child.id,
                attempt_number=1,
                trigger="explicit_submission",
                provider="mock",
                request_fingerprint=child.request_fingerprint,
                request_manifest={},
                state="created",
            )
            production.add(attempt)
            production.flush()
            child.current_attempt_id = attempt.id
            session.commit()

            assert [row.id for row in work_repository.lease_candidates(now)] == [child.id]
            assert [row.id for row in work_repository.required_parent_items(child)] == [parent.id]
            assert work_repository.required_parent_items(parent) == []
            assert set(work_repository.snapshot_work_states("snapshot-worker")) == {"completed", "queued"}
            assert work_repository.attempt(attempt.id).id == attempt.id  # type: ignore[union-attr]
            assert work_repository.project(project.id).id == project.id  # type: ignore[union-attr]
            assert work_repository.snapshot("missing") is None
            assert work_repository.claim(child, now) is True
            session.commit()
            claimed = work_repository.work_item(child.id)
            assert claimed is not None
            assert claimed.status == "in_progress"
            assert claimed.row_version == 2
            assert work_repository.claim(claimed, now) is False
    finally:
        engine.dispose()


def test_configuration_repository_contract_preserves_versions_history_and_scoped_deletion() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            repository = SqlAlchemyConfigurationRepository(session)
            now = utc_now()
            target = ProductionConfigVersion(
                id="config-target",
                config_key="studio",
                version_number=1,
                display_name="Target draft",
                created_at=now - timedelta(minutes=2),
            )
            retained = ProductionConfigVersion(
                id="config-retained",
                config_key="studio",
                version_number=2,
                display_name="Retained draft",
                created_at=now - timedelta(minutes=1),
            )
            repository.add(target)
            repository.add(retained)
            repository.flush()

            target_provider = ProviderConfigVersion(
                id="provider-target",
                production_config_version_id=target.id,
                provider_key="runninghub",
                version_number=1,
                display_name="Target provider",
                adapter_kind="runninghub",
                base_url="https://example.invalid",
                capabilities=["generate_i2v_clip"],
                request_timeout_seconds=30,
                poll_interval_seconds=2,
                max_concurrency=1,
                created_at=now - timedelta(minutes=2),
            )
            retained_provider = ProviderConfigVersion(
                id="provider-retained",
                production_config_version_id=retained.id,
                provider_key="runninghub",
                version_number=2,
                display_name="Retained provider",
                adapter_kind="runninghub",
                base_url="https://example.invalid",
                capabilities=["generate_i2v_clip"],
                request_timeout_seconds=30,
                poll_interval_seconds=2,
                max_concurrency=1,
                created_at=now - timedelta(minutes=1),
            )
            first_workflow = WorkflowSlotVersion(
                id="workflow-a",
                production_config_version_id=target.id,
                slot_key="first_frame_video",
                version_number=1,
                display_name="First frame A",
                operation_kind="generate_i2v_clip",
                provider_config_version_id=target_provider.id,
                provider_workflow_id="workflow-1",
                input_schema_version="v1",
                output_schema_version="v1",
                node_info_list=[],
                created_at=now - timedelta(minutes=2),
            )
            second_workflow = WorkflowSlotVersion(
                id="workflow-z",
                production_config_version_id=target.id,
                slot_key="alternate_video",
                version_number=1,
                display_name="Alternate",
                operation_kind="generate_i2v_clip",
                provider_config_version_id=target_provider.id,
                provider_workflow_id="workflow-2",
                input_schema_version="v1",
                output_schema_version="v1",
                node_info_list=[],
                created_at=now - timedelta(minutes=2),
            )
            retained_workflow = WorkflowSlotVersion(
                id="workflow-retained",
                production_config_version_id=retained.id,
                slot_key="first_frame_video",
                version_number=2,
                display_name="First frame B",
                operation_kind="generate_i2v_clip",
                provider_config_version_id=retained_provider.id,
                provider_workflow_id="workflow-3",
                input_schema_version="v1",
                output_schema_version="v1",
                node_info_list=[],
                created_at=now - timedelta(minutes=1),
            )
            catalog = PricingCatalogVersion(
                id="catalog-target",
                production_config_version_id=target.id,
                catalog_key="default",
                version_number=1,
                display_name="Target pricing",
                currency="CNY",
                confirmation_threshold=1,
            )
            repository.add(target_provider)
            repository.add(retained_provider)
            repository.add(first_workflow)
            repository.add(second_workflow)
            repository.add(retained_workflow)
            repository.add(catalog)
            repository.flush()
            later_rule = PricingRule(
                pricing_catalog_version_id=catalog.id,
                provider_config_version_id=target_provider.id,
                workflow_slot_version_id=second_workflow.id,
                operation_kind="generate_i2v_clip",
                unit="task",
                unit_price=2,
            )
            earlier_rule = PricingRule(
                pricing_catalog_version_id=catalog.id,
                provider_config_version_id=target_provider.id,
                workflow_slot_version_id=first_workflow.id,
                operation_kind="generate_i2v_clip",
                unit="task",
                unit_price=1,
            )
            repository.add(later_rule)
            repository.add(earlier_rule)
            for component_type, component_id in (
                ("provider", target_provider.id),
                ("workflow_slot", first_workflow.id),
                ("workflow_slot", second_workflow.id),
                ("pricing_catalog", catalog.id),
            ):
                repository.add(ProductionConfigComponent(
                    production_config_version_id=target.id,
                    component_type=component_type,
                    component_version_id=component_id,
                ))
            retained_link = ProductionConfigComponent(
                production_config_version_id=retained.id,
                component_type="provider",
                component_version_id=retained_provider.id,
            )
            repository.add(retained_link)
            earlier_ref = ConfigurationReference(
                production_config_version_id=target.id,
                ref_type="snapshot",
                ref_id="snapshot-1",
                created_at=now - timedelta(minutes=2),
            )
            later_ref = ConfigurationReference(
                production_config_version_id=target.id,
                ref_type="snapshot",
                ref_id="snapshot-2",
                created_at=now - timedelta(minutes=1),
            )
            receipt = ConfigurationCommandReceipt(
                command_id="configuration-command",
                command_type="configuration.create",
                result_type="production_config_version",
                result_id=target.id,
            )
            repository.add(later_ref)
            repository.add(earlier_ref)
            repository.add(receipt)
            session.commit()

            assert repository.receipt(receipt.command_id).id == receipt.id  # type: ignore[union-attr]
            assert repository.configuration(target.id).id == target.id  # type: ignore[union-attr]
            assert repository.next_configuration_version("studio") == 3
            assert repository.next_component_version("provider", "runninghub") == 3
            rows = repository.component_rows(target.id)
            assert [row.id for row in rows["workflow_slot"]] == [first_workflow.id, second_workflow.id]
            assert [row.id for row in repository.pricing_rules([catalog.id], ordered=True)] == [
                earlier_rule.id,
                later_rule.id,
            ]
            assert [row.id for row in repository.references(target.id)] == [earlier_ref.id, later_ref.id]
            assert [row.id for row in repository.configurations()] == [retained.id, target.id]
            assert [row.id for row in repository.workflow_slot_versions("first_frame_video")] == [
                retained_workflow.id,
                first_workflow.id,
            ]

            repository.delete_components(target.id)
            session.commit()

            assert all(not rows for rows in repository.component_rows(target.id).values())
            assert repository.pricing_rules([catalog.id]) == []
            assert session.get(ProductionConfigComponent, retained_link.id) is not None
            assert repository.configuration(retained.id) is not None
            assert [row.id for row in repository.all_components("provider")] == [retained_provider.id]
    finally:
        engine.dispose()


def test_registry_repository_contract_preserves_global_projection_order_and_exact_reads() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            registry = SqlAlchemyRegistryRepository(session)
            now = utc_now()
            earlier_project = Project(
                id="project-registry-earlier",
                title="Earlier",
                core_topic="Registry ordering",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=now - timedelta(minutes=1),
            )
            later_project = Project(
                id="project-registry-later",
                title="Later",
                core_topic="Registry ordering",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=now,
            )
            projects.add(earlier_project)
            projects.add(later_project)
            projects.flush()
            scene = Entity(
                id="entity-scene",
                project_id=later_project.id,
                entity_type="scene",
                display_name="Arena",
            )
            character = Entity(
                id="entity-character",
                project_id=later_project.id,
                entity_type="character",
                display_name="Zoe",
            )
            attachment = Attachment(
                id="attachment-registry",
                project_id=later_project.id,
                original_filename="reference.png",
                mime_type="image/png",
                byte_size=128,
                content_hash="a" * 64,
                storage_path="attachments/reference.png",
            )
            session.add_all([scene, character, attachment])
            session.flush()
            older_version = EntityVersion(
                id="version-character-1",
                project_id=later_project.id,
                entity_id=character.id,
                version_number=1,
                attributes={"name": "Zoe v1"},
                source_attachment_id=attachment.id,
                is_active=False,
            )
            active_version = EntityVersion(
                id="version-character-2",
                project_id=later_project.id,
                entity_id=character.id,
                version_number=2,
                attributes={"name": "Zoe v2"},
                source_attachment_id=attachment.id,
                is_active=True,
            )
            scene_version = EntityVersion(
                id="version-scene-1",
                project_id=later_project.id,
                entity_id=scene.id,
                version_number=1,
                attributes={"name": "Arena"},
                is_active=True,
            )
            session.add_all([older_version, active_version, scene_version])
            session.flush()
            later_binding = AttachmentBinding(
                id="binding-later",
                project_id=later_project.id,
                attachment_id=attachment.id,
                binding_type="character_reference",
                entity_id=character.id,
                entity_version_id=active_version.id,
                confirmed_at=now,
            )
            earlier_binding = AttachmentBinding(
                id="binding-earlier",
                project_id=later_project.id,
                attachment_id=attachment.id,
                binding_type="character_reference",
                entity_id=character.id,
                entity_version_id=active_version.id,
                confirmed_at=now - timedelta(minutes=1),
            )
            plan = PlanVersion(
                id="plan-registry",
                project_id=later_project.id,
                version_number=1,
                requirement_version_id="requirement-registry",
                shot_plan_candidate_id="shot-plan-registry",
                creative_brief={},
            )
            later_shot = Shot(
                id="shot-registry-2",
                project_id=later_project.id,
                plan_version_id=plan.id,
                shot_code="SH-002",
                sequence_number=2,
                duration_ms=5000,
                shot_type="action",
                character_entity_version_ids=[active_version.id],
                outfit_entity_version_ids=[],
                face_visibility="required",
                text_policy="forbidden",
                motion_requirement="high",
                composition="Medium",
                action="Run",
            )
            earlier_shot = Shot(
                id="shot-registry-1",
                project_id=later_project.id,
                plan_version_id=plan.id,
                shot_code="SH-001",
                sequence_number=1,
                duration_ms=5000,
                shot_type="establishing",
                scene_entity_version_id=scene_version.id,
                character_entity_version_ids=[],
                outfit_entity_version_ids=[],
                face_visibility="not_visible",
                text_policy="forbidden",
                motion_requirement="low",
                composition="Wide",
                action="Establish arena",
            )
            snapshot = ProductionSnapshot(
                id="snapshot-registry",
                project_id=later_project.id,
                plan_version_id=plan.id,
                production_config_version_id="config-registry",
                impact_analysis_id="impact-registry",
                snapshot_number=1,
                status="locked",
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="b" * 64,
            )
            snapshot_ref = SnapshotEntityVersion(
                id="snapshot-entity-registry",
                snapshot_id=snapshot.id,
                entity_version_id=active_version.id,
                role="character",
            )
            session.add_all([
                later_binding,
                earlier_binding,
                plan,
                later_shot,
                earlier_shot,
                snapshot,
                snapshot_ref,
            ])
            session.commit()

            assert [row.id for row in registry.projects()] == [later_project.id, earlier_project.id]
            assert [row.id for row in registry.entities()] == [character.id, scene.id]
            assert [row.id for row in registry.entity_versions()] == [
                active_version.id,
                older_version.id,
                scene_version.id,
            ]
            assert [row.id for row in registry.attachments_by_ids({attachment.id})] == [attachment.id]
            assert [row.id for row in registry.bindings_by_entity_version_ids({active_version.id})] == [
                earlier_binding.id,
                later_binding.id,
            ]
            assert [row.id for row in registry.snapshots()] == [snapshot.id]
            assert [row.id for row in registry.snapshot_entity_versions({active_version.id})] == [snapshot_ref.id]
            assert [row.id for row in registry.plans()] == [plan.id]
            assert [row.id for row in registry.shots()] == [earlier_shot.id, later_shot.id]
            assert registry.attachment(attachment.id).id == attachment.id  # type: ignore[union-attr]
            assert registry.attachment("attachment-missing") is None
            assert registry.attachments_by_ids(set()) == []
            assert registry.bindings_by_entity_version_ids(set()) == []
            assert registry.snapshot_entity_versions(set()) == []
    finally:
        engine.dispose()


def test_control_repository_contract_preserves_authority_scope_history_and_ordering() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            control = SqlAlchemyControlRepository(session)
            now = utc_now()
            project = Project(
                id="project-control",
                title="Control",
                core_topic="Control repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=now,
            )
            other = Project(
                id="project-control-other",
                title="Other",
                core_topic="Control isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=now - timedelta(minutes=1),
            )
            projects.add(project)
            projects.add(other)
            projects.flush()
            first_plan = PlanVersion(
                id="plan-control-1",
                project_id=project.id,
                version_number=1,
                requirement_version_id="requirement-control-1",
                shot_plan_candidate_id="shot-plan-control-1",
                creative_brief={},
                is_active=True,
            )
            latest_plan = PlanVersion(
                id="plan-control-2",
                project_id=project.id,
                version_number=2,
                requirement_version_id="requirement-control-2",
                shot_plan_candidate_id="shot-plan-control-2",
                creative_brief={},
                is_active=True,
            )
            session.add_all([first_plan, latest_plan])
            session.flush()
            first_snapshot = ProductionSnapshot(
                id="snapshot-control-1",
                project_id=project.id,
                plan_version_id=first_plan.id,
                production_config_version_id="config-control",
                impact_analysis_id="impact-control-1",
                snapshot_number=1,
                status="locked",
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="a" * 64,
            )
            latest_snapshot = ProductionSnapshot(
                id="snapshot-control-2",
                project_id=project.id,
                plan_version_id=latest_plan.id,
                production_config_version_id="config-control",
                impact_analysis_id="impact-control-2",
                snapshot_number=2,
                status="submitted",
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="b" * 64,
            )
            other_snapshot = ProductionSnapshot(
                id="snapshot-control-other",
                project_id=other.id,
                plan_version_id="plan-control-other",
                production_config_version_id="config-control",
                impact_analysis_id="impact-control-other",
                snapshot_number=1,
                status="submitted",
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="c" * 64,
            )
            session.add_all([first_snapshot, latest_snapshot, other_snapshot])
            session.flush()
            node = DAGNode(
                id="node-control",
                snapshot_id=latest_snapshot.id,
                node_key="shot.001.video",
                kind="generate_i2v_clip",
                input_contract={},
                output_contract={"media_type": "video"},
            )
            earlier_item = WorkItem(
                id="work-control-1",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                dag_node_id=node.id,
                kind=node.kind,
                payload={},
                status="completed",
                created_at=now - timedelta(minutes=2),
            )
            later_item = WorkItem(
                id="work-control-2",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                kind="contract_validation",
                payload={},
                status="blocked",
                created_at=now - timedelta(minutes=1),
            )
            historical_item = WorkItem(
                id="work-control-history",
                project_id=project.id,
                snapshot_id=first_snapshot.id,
                kind="contract_validation",
                payload={},
                status="completed",
                created_at=now - timedelta(minutes=3),
            )
            session.add_all([node, later_item, earlier_item, historical_item])
            session.flush()
            second_attempt = WorkAttempt(
                id="attempt-control-2",
                work_item_id=earlier_item.id,
                attempt_number=2,
                trigger="explicit_submission",
                provider="mock",
                request_fingerprint="d" * 64,
                request_manifest={},
                state="completed",
            )
            first_attempt = WorkAttempt(
                id="attempt-control-1",
                work_item_id=earlier_item.id,
                attempt_number=1,
                trigger="explicit_submission",
                provider="mock",
                request_fingerprint="e" * 64,
                request_manifest={},
                state="completed",
            )
            earlier_asset = Asset(
                id="asset-control-1",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                dag_node_id=node.id,
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/control-1.mp4",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
                created_at=now - timedelta(minutes=2),
            )
            later_asset = Asset(
                id="asset-control-2",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/control-2.mp4",
                storage_backend="local",
                provider_output_manifest={},
                state="archived",
                created_at=now - timedelta(minutes=1),
            )
            historical_asset = Asset(
                id="asset-control-history",
                project_id=project.id,
                snapshot_id=first_snapshot.id,
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/control-history.mp4",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
                created_at=now - timedelta(minutes=3),
            )
            session.add_all([
                second_attempt,
                first_attempt,
                earlier_asset,
                later_asset,
                historical_asset,
            ])
            session.flush()
            blocked_report_one = QCReport(
                id="report-control-1",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                asset_id=later_asset.id,
                report_number=1,
                ruleset_version="control.v1",
                status="blocked",
                analyzer="contract",
            )
            blocked_report_two = QCReport(
                id="report-control-2",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                asset_id=later_asset.id,
                report_number=2,
                ruleset_version="control.v1",
                status="blocked",
                analyzer="contract",
            )
            session.add_all([blocked_report_one, blocked_report_two])
            session.flush()
            later_finding = QCFinding(
                id="finding-control-2",
                qc_report_id=blocked_report_two.id,
                code="SECOND",
                severity="blocked",
                disposition="blocked",
                created_at=now,
            )
            earlier_finding = QCFinding(
                id="finding-control-1",
                qc_report_id=blocked_report_two.id,
                code="FIRST",
                severity="blocked",
                disposition="blocked",
                created_at=now - timedelta(minutes=1),
            )
            timeline_one = Timeline(
                id="timeline-control-1",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                version_number=1,
                status="candidate",
                source="user",
                output_spec={},
                track_config={},
            )
            timeline_two = Timeline(
                id="timeline-control-2",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                version_number=2,
                status="confirmed",
                source="user",
                output_spec={},
                track_config={},
            )
            delivery_one = DeliveryAttempt(
                id="delivery-control-1",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                timeline_id=timeline_one.id,
                attempt_number=1,
                status="authorized",
                execution_kind="external_upload",
                request_manifest={},
                request_fingerprint="f" * 64,
                created_at=now - timedelta(minutes=1),
            )
            delivery_two = DeliveryAttempt(
                id="delivery-control-2",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                timeline_id=timeline_two.id,
                attempt_number=1,
                status="verified",
                execution_kind="external_upload",
                request_manifest={},
                request_fingerprint="1" * 64,
                created_at=now,
            )
            candidate = CreativeBriefCandidate(
                id="brief-control",
                project_id=project.id,
                requirement_version_id="requirement-control-2",
                agent_run_id="agent-run-control",
                brief={},
            )
            cost = CostEvent(
                id="cost-control",
                project_id=project.id,
                snapshot_id=latest_snapshot.id,
                provider="mock",
                provider_operation="generate_i2v_clip",
                kind="estimated",
                amount=1.25,
                currency="CNY",
                status="confirmed",
            )
            event_one = ProjectEvent(
                project_id=project.id,
                project_sequence=1,
                event_type="control.one.v1",
                aggregate_type="project",
                aggregate_id=project.id,
                correlation_id="control-one",
                actor_type="system",
                actor_id="test",
                message="One",
            )
            event_two = ProjectEvent(
                project_id=project.id,
                project_sequence=2,
                event_type="control.two.v1",
                aggregate_type="project",
                aggregate_id=project.id,
                correlation_id="control-two",
                actor_type="system",
                actor_id="test",
                message="Two",
            )
            event_three = ProjectEvent(
                project_id=project.id,
                project_sequence=3,
                event_type="control.three.v1",
                aggregate_type="project",
                aggregate_id=project.id,
                correlation_id="control-three",
                actor_type="system",
                actor_id="test",
                message="Three",
            )
            session.add_all([
                later_finding,
                earlier_finding,
                timeline_one,
                timeline_two,
                delivery_one,
                delivery_two,
                candidate,
                cost,
                event_one,
                event_two,
                event_three,
            ])
            session.commit()

            assert control.active_plan(project.id).id == latest_plan.id  # type: ignore[union-attr]
            assert [row.id for row in control.snapshots(project.id)] == [latest_snapshot.id, first_snapshot.id]
            assert control.snapshot(latest_snapshot.id).id == latest_snapshot.id  # type: ignore[union-attr]
            assert [row.id for row in control.attempts_for_items([earlier_item.id])] == [
                first_attempt.id,
                second_attempt.id,
            ]
            assert [row.id for row in control.cost_events(project.id)] == [cost.id]
            assert [row.id for row in control.dag_nodes(latest_snapshot.id)] == [node.id]
            assert control.latest_blocked_report(later_asset.id).id == blocked_report_two.id  # type: ignore[union-attr]
            assert [row.id for row in control.findings(blocked_report_two.id)] == [
                earlier_finding.id,
                later_finding.id,
            ]
            assert [row.id for row in control.work_items(project.id, latest_snapshot.id)] == [
                earlier_item.id,
                later_item.id,
            ]
            assert [row.id for row in control.work_items(project.id, None)] == [
                historical_item.id,
                earlier_item.id,
                later_item.id,
            ]
            assert [row.id for row in control.assets(project.id, latest_snapshot.id)] == [
                earlier_asset.id,
                later_asset.id,
            ]
            assert [row.id for row in control.assets(project.id, None)] == [
                historical_asset.id,
                earlier_asset.id,
                later_asset.id,
            ]
            assert control.latest_timeline(project.id).id == timeline_two.id  # type: ignore[union-attr]
            assert control.latest_delivery(project.id).id == delivery_two.id  # type: ignore[union-attr]
            assert control.has_planning_candidate(project.id) is True
            assert control.has_planning_candidate(other.id) is False
            assert [row.sequence for row in control.events(project.id, limit=2)] == [
                event_three.sequence,
                event_two.sequence,
            ]
            assert [row.id for row in control.projects()] == [project.id, other.id]
            assert control.attempts_for_items([]) == []
    finally:
        engine.dispose()


def test_contact_sheet_repository_contract_preserves_snapshot_scope_and_evidence_order() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            contact_sheet = SqlAlchemyContactSheetRepository(session)
            now = utc_now()
            project = Project(
                id="project-contact",
                title="Contact sheet",
                core_topic="Contact repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            other = Project(
                id="project-contact-other",
                title="Other",
                core_topic="Contact isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(project)
            projects.add(other)
            projects.flush()
            snapshot = ProductionSnapshot(
                id="snapshot-contact",
                project_id=project.id,
                plan_version_id="plan-contact",
                production_config_version_id="config-contact",
                impact_analysis_id="impact-contact",
                snapshot_number=1,
                status="submitted",
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="a" * 64,
            )
            other_snapshot = ProductionSnapshot(
                id="snapshot-contact-other",
                project_id=other.id,
                plan_version_id="plan-contact-other",
                production_config_version_id="config-contact",
                impact_analysis_id="impact-contact-other",
                snapshot_number=1,
                status="submitted",
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="b" * 64,
            )
            session.add_all([snapshot, other_snapshot])
            session.flush()
            parent_node = DAGNode(
                id="node-contact-a",
                snapshot_id=snapshot.id,
                node_key="shot.001.image",
                kind="generate_keyframe",
                input_contract={},
                output_contract={"media_type": "image"},
            )
            child_node = DAGNode(
                id="node-contact-z",
                snapshot_id=snapshot.id,
                node_key="shot.001.video",
                kind="generate_i2v_clip",
                input_contract={},
                output_contract={"media_type": "video"},
            )
            other_node = DAGNode(
                id="node-contact-other",
                snapshot_id=other_snapshot.id,
                node_key="other",
                kind="generate_keyframe",
                input_contract={},
                output_contract={},
            )
            session.add_all([child_node, parent_node, other_node])
            session.flush()
            shot = Shot(
                id="shot-contact",
                project_id=project.id,
                plan_version_id=snapshot.plan_version_id,
                shot_code="SH-001",
                sequence_number=1,
                duration_ms=10000,
                shot_type="action",
                character_entity_version_ids=["version-contact"],
                outfit_entity_version_ids=[],
                face_visibility="required",
                text_policy="forbidden",
                motion_requirement="high",
                composition="Medium",
                action="Run",
            )
            other_shot = Shot(
                id="shot-contact-other",
                project_id=other.id,
                plan_version_id=snapshot.plan_version_id,
                shot_code="SH-002",
                sequence_number=2,
                duration_ms=10000,
                shot_type="action",
                character_entity_version_ids=[],
                outfit_entity_version_ids=[],
                face_visibility="not_visible",
                text_policy="forbidden",
                motion_requirement="low",
                composition="Wide",
                action="Other",
            )
            earlier_asset = Asset(
                id="asset-contact-1",
                project_id=project.id,
                snapshot_id=snapshot.id,
                dag_node_id=parent_node.id,
                output_index=0,
                asset_type="image",
                role="keyframe",
                uri="runtime://assets/contact-1.png",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
                created_at=now - timedelta(minutes=1),
            )
            later_asset = Asset(
                id="asset-contact-2",
                project_id=project.id,
                snapshot_id=snapshot.id,
                dag_node_id=child_node.id,
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/contact-2.mp4",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
                created_at=now,
            )
            other_asset = Asset(
                id="asset-contact-other",
                project_id=other.id,
                snapshot_id=other_snapshot.id,
                output_index=0,
                asset_type="image",
                role="keyframe",
                uri="runtime://assets/contact-other.png",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
            )
            first_edge = DependencyEdge(
                id="edge-contact-a",
                snapshot_id=snapshot.id,
                parent_node_id=parent_node.id,
                child_node_id=child_node.id,
                dependency_type="required",
                input_slot="first_frame",
            )
            second_edge = DependencyEdge(
                id="edge-contact-z",
                snapshot_id=snapshot.id,
                parent_node_id=parent_node.id,
                child_node_id=child_node.id,
                dependency_type="informational",
                input_slot="reference",
            )
            work_item = WorkItem(
                id="work-contact",
                project_id=project.id,
                snapshot_id=snapshot.id,
                dag_node_id=child_node.id,
                kind=child_node.kind,
                payload={},
                status="completed",
            )
            other_work_item = WorkItem(
                id="work-contact-other",
                project_id=other.id,
                snapshot_id=other_snapshot.id,
                dag_node_id=other_node.id,
                kind=other_node.kind,
                payload={},
                status="completed",
            )
            session.add_all([
                shot,
                other_shot,
                earlier_asset,
                later_asset,
                other_asset,
                second_edge,
                first_edge,
                work_item,
                other_work_item,
            ])
            session.flush()
            attempt = WorkAttempt(
                id="attempt-contact",
                work_item_id=work_item.id,
                attempt_number=1,
                trigger="explicit_submission",
                provider="mock",
                request_fingerprint="c" * 64,
                request_manifest={},
                state="completed",
            )
            other_attempt = WorkAttempt(
                id="attempt-contact-other",
                work_item_id=other_work_item.id,
                attempt_number=1,
                trigger="explicit_submission",
                provider="mock",
                request_fingerprint="d" * 64,
                request_manifest={},
                state="completed",
            )
            entity = Entity(
                id="entity-contact",
                project_id=project.id,
                entity_type="character",
                display_name="Runner",
            )
            other_entity = Entity(
                id="entity-contact-other",
                project_id=other.id,
                entity_type="character",
                display_name="Other",
            )
            attachment = Attachment(
                id="attachment-contact",
                project_id=project.id,
                original_filename="runner.png",
                mime_type="image/png",
                byte_size=128,
                content_hash="e" * 64,
                storage_path="attachments/runner.png",
            )
            other_attachment = Attachment(
                id="attachment-contact-other",
                project_id=other.id,
                original_filename="other.png",
                mime_type="image/png",
                byte_size=128,
                content_hash="f" * 64,
                storage_path="attachments/other.png",
            )
            session.add_all([attempt, other_attempt, entity, other_entity, attachment, other_attachment])
            session.flush()
            version = EntityVersion(
                id="version-contact",
                project_id=project.id,
                entity_id=entity.id,
                version_number=1,
                attributes={},
                source_attachment_id=attachment.id,
            )
            other_version = EntityVersion(
                id="version-contact-other",
                project_id=other.id,
                entity_id=other_entity.id,
                version_number=1,
                attributes={},
                source_attachment_id=other_attachment.id,
            )
            session.add_all([version, other_version])
            session.commit()

            assert contact_sheet.snapshot(snapshot.id).id == snapshot.id  # type: ignore[union-attr]
            assert contact_sheet.snapshot("snapshot-missing") is None
            assert [row.id for row in contact_sheet.nodes(snapshot.id)] == [parent_node.id, child_node.id]
            assert [row.id for row in contact_sheet.shots(project.id, snapshot.plan_version_id)] == [shot.id]
            assert [row.id for row in contact_sheet.assets(project.id, snapshot.id)] == [
                earlier_asset.id,
                later_asset.id,
            ]
            assert [row.id for row in contact_sheet.edges(snapshot.id)] == [first_edge.id, second_edge.id]
            assert [row.id for row in contact_sheet.work_items(project.id, snapshot.id)] == [work_item.id]
            assert [row.id for row in contact_sheet.attempts_for_items({work_item.id})] == [attempt.id]
            assert [row.id for row in contact_sheet.entity_versions(
                project.id,
                {version.id, other_version.id},
            )] == [version.id]
            assert [row.id for row in contact_sheet.entities(
                project.id,
                {entity.id, other_entity.id},
            )] == [entity.id]
            assert [row.id for row in contact_sheet.attachments(
                project.id,
                {attachment.id, other_attachment.id},
            )] == [attachment.id]
            assert contact_sheet.attempts_for_items(set()) == []
            assert contact_sheet.entity_versions(project.id, set()) == []
            assert contact_sheet.entities(project.id, set()) == []
            assert contact_sheet.attachments(project.id, set()) == []
    finally:
        engine.dispose()


def test_impact_repository_contract_preserves_project_lineage_and_exact_scope() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            impact = SqlAlchemyImpactRepository(session)
            project = Project(
                id="project-impact",
                title="Impact",
                core_topic="Impact repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            other = Project(
                id="project-impact-other",
                title="Other",
                core_topic="Impact isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(project)
            projects.add(other)
            projects.flush()
            decision = Decision(
                id="decision-impact",
                project_id=project.id,
                key="visual_style",
                label="Style",
                value="documentary",
                status="resolved",
            )
            other_decision = Decision(
                id="decision-impact-other",
                project_id=other.id,
                key="visual_style",
                label="Other style",
                value="cinematic",
                status="resolved",
            )
            requirement = RequirementVersion(
                id="requirement-impact",
                project_id=project.id,
                version_number=1,
                fields={},
                candidate_id="candidate-impact",
            )
            manifest = AgentInputManifest(
                id="manifest-impact",
                project_id=project.id,
                base_requirement_version_id=requirement.id,
                decision_ids=[decision.id],
                input_hash="a" * 64,
                payload={},
            )
            run = AgentRun(
                id="run-impact",
                project_id=project.id,
                agent_role="creative",
                status="succeeded",
                input_manifest_id=manifest.id,
            )
            candidate = RequirementCandidate(
                id="candidate-impact",
                project_id=project.id,
                base_requirement_version_id=requirement.id,
                agent_run_id=run.id,
                status="accepted",
                fields={},
            )
            brief = CreativeBriefCandidate(
                id="brief-impact",
                project_id=project.id,
                requirement_version_id=requirement.id,
                agent_run_id=run.id,
                status="accepted",
                brief={},
            )
            shot_plan = ShotPlanCandidate(
                id="shot-plan-impact",
                project_id=project.id,
                requirement_version_id=requirement.id,
                creative_brief_candidate_id=brief.id,
                agent_run_id=run.id,
                status="accepted",
                shots=[],
            )
            plan = PlanVersion(
                id="plan-impact",
                project_id=project.id,
                version_number=1,
                requirement_version_id=requirement.id,
                shot_plan_candidate_id=shot_plan.id,
                creative_brief={},
            )
            shot = Shot(
                id="shot-impact",
                project_id=project.id,
                plan_version_id=plan.id,
                shot_code="SH-001",
                sequence_number=1,
                duration_ms=10000,
                shot_type="action",
                character_entity_version_ids=[],
                outfit_entity_version_ids=[],
                face_visibility="required",
                text_policy="forbidden",
                motion_requirement="high",
                composition="Medium",
                action="Run",
            )
            snapshot = ProductionSnapshot(
                id="snapshot-impact",
                project_id=project.id,
                plan_version_id=plan.id,
                production_config_version_id="config-impact",
                impact_analysis_id="analysis-impact",
                snapshot_number=1,
                status="submitted",
                audio_mode="off",
                output_spec={},
                selection={},
                contract={},
                contract_hash="b" * 64,
            )
            node = DAGNode(
                id="node-impact",
                snapshot_id=snapshot.id,
                node_key="shot.001.video",
                kind="generate_i2v_clip",
                shot_id=shot.id,
                input_contract={},
                output_contract={"media_type": "video"},
            )
            work_item = WorkItem(
                id="work-impact",
                project_id=project.id,
                snapshot_id=snapshot.id,
                dag_node_id=node.id,
                kind=node.kind,
                payload={},
            )
            asset = Asset(
                id="asset-impact",
                project_id=project.id,
                snapshot_id=snapshot.id,
                dag_node_id=node.id,
                output_index=0,
                asset_type="video",
                role="clip",
                uri="runtime://assets/impact.mp4",
                storage_backend="local",
                provider_output_manifest={},
                state="approved",
            )
            timeline = Timeline(
                id="timeline-impact",
                project_id=project.id,
                snapshot_id=snapshot.id,
                version_number=1,
                status="confirmed",
                source="user",
                output_spec={},
                track_config={},
            )
            timeline_item = TimelineItem(
                id="timeline-item-impact",
                timeline_id=timeline.id,
                track_type="main_video",
                sequence_number=1,
                asset_id=asset.id,
                label="SH-001",
                source_in_ms=0,
                source_out_ms=10000,
                timeline_in_ms=0,
                timeline_out_ms=10000,
            )
            entity = Entity(
                id="entity-impact",
                project_id=project.id,
                entity_type="character",
                display_name="Runner",
            )
            entity_version = EntityVersion(
                id="entity-version-impact",
                project_id=project.id,
                entity_id=entity.id,
                version_number=1,
                status="confirmed",
                is_active=True,
            )
            change_analysis = DecisionChangeImpactAnalysis(
                id="decision-change-impact",
                project_id=project.id,
                decision_id=decision.id,
                status="completed",
                scope="observed_lineage_with_active_cost",
                current_value="documentary",
                proposed_value="cinematic",
                observed_manifest_ids=[manifest.id],
                target_counts={"shot": 1},
                estimated_work_count=1,
                cost_status="estimated",
                estimated_cost=0.25,
                currency="CNY",
                analysis_hash="c" * 64,
                active_snapshot_id=snapshot.id,
            )
            change_target = DecisionChangeImpactTarget(
                id="decision-change-target",
                analysis_id=change_analysis.id,
                record_type="shot",
                record_id=shot.id,
                label=shot.shot_code,
                record_status="contract",
                authority="recorded",
                included_in_estimate=True,
                estimated_work_units=1,
                estimated_cost=0.25,
                currency="CNY",
                evidence={"node_id": f"shot:{shot.id}"},
            )
            session.add_all([
                decision,
                other_decision,
                requirement,
                manifest,
                run,
                candidate,
                brief,
                shot_plan,
                plan,
                shot,
                snapshot,
                node,
                work_item,
                asset,
                timeline,
                timeline_item,
                entity,
                entity_version,
                change_analysis,
                change_target,
            ])
            session.commit()

            assert [row.id for row in impact.decisions(project.id)] == [decision.id]
            assert [row.id for row in impact.manifests(project.id)] == [manifest.id]
            assert [row.id for row in impact.agent_runs(project.id)] == [run.id]
            assert [row.id for row in impact.requirement_candidates(project.id)] == [candidate.id]
            assert [row.id for row in impact.requirement_versions(project.id)] == [requirement.id]
            assert [row.id for row in impact.creative_briefs(project.id)] == [brief.id]
            assert [row.id for row in impact.shot_plans(project.id)] == [shot_plan.id]
            assert [row.id for row in impact.plans(project.id)] == [plan.id]
            assert [row.id for row in impact.shots(project.id)] == [shot.id]
            assert [row.id for row in impact.snapshots(project.id)] == [snapshot.id]
            assert [row.id for row in impact.dag_nodes({snapshot.id})] == [node.id]
            assert [row.id for row in impact.work_items(project.id)] == [work_item.id]
            assert [row.id for row in impact.assets(project.id)] == [asset.id]
            assert [row.id for row in impact.timelines(project.id)] == [timeline.id]
            assert [row.id for row in impact.timeline_items({timeline.id})] == [timeline_item.id]
            assert [row.id for row in impact.entities(project.id)] == [entity.id]
            assert [row.id for row in impact.entity_versions(project.id)] == [entity_version.id]
            assert impact.decision(project.id, decision.id).id == decision.id  # type: ignore[union-attr]
            assert impact.decision(other.id, decision.id) is None
            assert impact.change_analysis(project.id, change_analysis.id).id == change_analysis.id  # type: ignore[union-attr]
            assert impact.change_analysis(other.id, change_analysis.id) is None
            assert [row.id for row in impact.change_analysis_history(project.id)] == [change_analysis.id]
            assert [row.id for row in impact.change_analysis_targets(change_analysis.id)] == [change_target.id]
            assert [row.id for row in impact.decisions(other.id)] == [other_decision.id]
            assert impact.manifests(other.id) == []
            assert impact.dag_nodes(set()) == []
            assert impact.timeline_items(set()) == []
    finally:
        engine.dispose()
