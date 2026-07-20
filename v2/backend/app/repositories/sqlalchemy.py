from __future__ import annotations

from datetime import datetime
import re
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from ..db.models import (
    AgentInputManifest,
    AgentRun,
    Asset,
    AssetReviewDecision,
    Attachment,
    AttachmentBinding,
    ClarificationRequest,
    CommandReceipt,
    ConfigurationCommandReceipt,
    ConfigurationEvent,
    ConfigurationReference,
    ConversationSession,
    CostEvent,
    CreativeBriefCandidate,
    CreativeSuggestionSelection,
    CreativeTurnProposal,
    DAGNode,
    Decision,
    DecisionChangeImpactAnalysis,
    DecisionChangeImpactTarget,
    DeliveryAttempt,
    DependencyEdge,
    Entity,
    EntityVersion,
    Message,
    ModelConfigVersion,
    OutboxMessage,
    Project,
    ProjectEvent,
    PricingCatalogVersion,
    PricingRule,
    ProductionConfigComponent,
    ProductionConfigVersion,
    ProductionImpactAnalysis,
    ProductionSnapshot,
    ProviderConfigVersion,
    QCFinding,
    QCReport,
    QCReportCandidate,
    RequirementCandidate,
    RequirementVersion,
    PlanVersion,
    Shot,
    ShotPlanCandidate,
    SnapshotEntityVersion,
    StoragePolicyVersion,
    Timeline,
    TimelineItem,
    VideoSpecVersion,
    WorkflowSlotVersion,
    WorkAttempt,
    WorkItem,
    AudioConfigVersion,
    new_id,
    utc_now,
)
from .contracts import (
    ConfigurationComponentRecord,
    ConfigurationRecord,
    CreationRecord,
    DeliveryRecord,
    EditorRecord,
    ModelT,
    PlanningRecord,
    ProductionRecord,
    QualityRecord,
)


CONFIGURATION_COMPONENT_MODELS = {
    "provider": (ProviderConfigVersion, "provider_key"),
    "model": (ModelConfigVersion, "config_key"),
    "workflow_slot": (WorkflowSlotVersion, "slot_key"),
    "video_spec": (VideoSpecVersion, "spec_key"),
    "audio": (AudioConfigVersion, "config_key"),
    "storage": (StoragePolicyVersion, "policy_key"),
    "pricing_catalog": (PricingCatalogVersion, "catalog_key"),
}


class SqlAlchemyProjectRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_recent(self, *, include_archived: bool = False) -> list[Project]:
        statement = select(Project)
        if not include_archived:
            statement = statement.where(Project.archived_at.is_(None))
        return list(self.session.scalars(statement.order_by(Project.updated_at.desc())))

    def get(self, project_id: str, *, with_workspace: bool = False) -> Project | None:
        statement = select(Project).where(Project.id == project_id)
        if with_workspace:
            statement = statement.options(selectinload(Project.decisions), selectinload(Project.work_items))
        return self.session.scalar(statement)

    def add(self, project: Project) -> None:
        self.session.add(project)

    def add_work_item(self, item: WorkItem) -> None:
        self.session.add(item)

    def flush(self) -> None:
        self.session.flush()

    def refresh_work_item(self, item: WorkItem) -> None:
        self.session.refresh(item)

    def has_active_work(self, project_id: str) -> bool:
        count = self.session.scalar(
            select(func.count(WorkItem.id)).where(
                WorkItem.project_id == project_id,
                WorkItem.status.in_({"queued", "in_progress"}),
            )
        )
        return bool(count)

    def update_archive(
        self,
        project: Project,
        *,
        expected_row_version: int,
        archived_at: datetime | None,
        archived_by: str | None,
    ) -> bool:
        expected_archived = Project.archived_at.is_not(None) if archived_at is None else Project.archived_at.is_(None)
        changed_at = utc_now()
        result = self.session.execute(
            update(Project)
            .where(
                Project.id == project.id,
                Project.row_version == expected_row_version,
                expected_archived,
            )
            .values(
                archived_at=archived_at,
                archived_by=archived_by,
                row_version=expected_row_version + 1,
                updated_at=changed_at,
            )
        )
        if result.rowcount != 1:
            return False
        self.session.expire(project)
        self.session.refresh(project)
        return True


class SqlAlchemyProjectStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def transition_state(
        self,
        project: Project,
        *,
        expected_status: str,
        expected_row_version: int,
        target_status: str,
        changed_at: datetime,
        actor_type: str,
        actor_id: str,
        trigger: str,
        reason_code: str | None,
        blocked_from_state: str | None,
        responsible_aggregate_type: str | None,
        responsible_aggregate_id: str | None,
        allowed_commands: list[str],
        blocked_at: datetime | None,
    ) -> bool:
        self.session.flush()
        result = self.session.execute(
            update(Project)
            .where(
                Project.id == project.id,
                Project.status == expected_status,
                Project.row_version == expected_row_version,
            )
            .values(
                status=target_status,
                row_version=expected_row_version + 1,
                state_changed_at=changed_at,
                state_actor_type=actor_type,
                state_changed_by=actor_id,
                state_trigger=trigger,
                state_reason_code=reason_code,
                blocked_from_state=blocked_from_state,
                blocked_responsible_aggregate_type=responsible_aggregate_type,
                blocked_responsible_aggregate_id=responsible_aggregate_id,
                blocked_allowed_commands=allowed_commands,
                blocked_at=blocked_at,
            )
        )
        if result.rowcount != 1:
            return False
        self.session.expire(project)
        self.session.refresh(project)
        return True


class SqlAlchemyEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: ProjectEvent) -> None:
        if not re.fullmatch(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+\.v[1-9][0-9]*", event.event_type):
            raise ValueError(f"Project event type {event.event_type!r} is not versioned.")
        if not event.aggregate_type or not event.aggregate_id:
            raise ValueError("Project event aggregate_type and aggregate_id are required.")
        if not event.actor_type or not event.actor_id:
            raise ValueError("Project event actor_type and actor_id are required.")
        if event.data is None:
            event.data = {}
        if not isinstance(event.data, dict):
            raise ValueError("Project event payload must be an object.")
        if not event.event_id:
            event.event_id = new_id("event")
        if not event.correlation_id:
            event.correlation_id = event.event_id
        next_sequence = self.session.scalar(
            update(Project)
            .where(Project.id == event.project_id)
            .values(event_sequence=Project.event_sequence + 1)
            .returning(Project.event_sequence)
        )
        if next_sequence is None:
            raise ValueError(f"Project {event.project_id!r} does not exist for event allocation.")
        event.project_sequence = next_sequence
        self.session.add(event)
        self.session.add(OutboxMessage(event_id=event.event_id, project_id=event.project_id))

    def list_after(self, project_id: str, sequence: int, *, limit: int = 100) -> list[ProjectEvent]:
        statement = (
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project_id, ProjectEvent.project_sequence > sequence)
            .order_by(ProjectEvent.project_sequence)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def get_by_event_id(self, event_id: str) -> ProjectEvent | None:
        return self.session.scalar(select(ProjectEvent).where(ProjectEvent.event_id == event_id))


class SqlAlchemyOutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_pending(self, *, limit: int = 100, now: datetime | None = None) -> list[OutboxMessage]:
        current = now or utc_now()
        statement = (
            select(OutboxMessage)
            .where(OutboxMessage.status == "pending", OutboxMessage.available_at <= current)
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def mark_published(self, message_id: str, *, published_at: datetime) -> bool:
        result = self.session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id, OutboxMessage.status == "pending")
            .values(status="published", published_at=published_at)
        )
        return result.rowcount == 1


class SqlAlchemyDecisionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_key(self, project_id: str, key: str) -> Decision | None:
        return self.session.scalar(
            select(Decision).where(Decision.project_id == project_id, Decision.key == key)
        )

    def get_for_project(self, project_id: str, decision_id: str) -> Decision | None:
        return self.session.scalar(
            select(Decision).where(Decision.project_id == project_id, Decision.id == decision_id)
        )

    def resolved_for_project(self, project_id: str) -> list[Decision]:
        return list(self.session.scalars(
            select(Decision)
            .where(Decision.project_id == project_id, Decision.status == "resolved")
            .order_by(Decision.created_at, Decision.id)
        ))

    def add(self, decision: Decision) -> None:
        self.session.add(decision)

    def flush(self) -> None:
        self.session.flush()

    def refresh(self, decision: Decision) -> None:
        self.session.refresh(decision)


class SqlAlchemyCommandRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, project_id: str, command_id: str) -> CommandReceipt | None:
        return self.session.scalar(
            select(CommandReceipt).where(
                CommandReceipt.project_id == project_id,
                CommandReceipt.command_id == command_id,
            )
        )

    def add(
        self,
        project_id: str,
        command_id: str,
        command_type: str,
        result_type: str,
        result_id: str,
    ) -> CommandReceipt:
        receipt = CommandReceipt(
            project_id=project_id,
            command_id=command_id,
            command_type=command_type,
            result_type=result_type,
            result_id=result_id,
        )
        self.session.add(receipt)
        return receipt

    def get_result(self, model_type: type[ModelT], result_id: str) -> ModelT | None:
        return self.session.get(model_type, result_id)


class SqlAlchemyCreationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: CreationRecord) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def active_requirement(self, project_id: str) -> RequirementVersion | None:
        return self.session.scalar(
            select(RequirementVersion)
            .where(RequirementVersion.project_id == project_id, RequirementVersion.is_active.is_(True))
            .order_by(RequirementVersion.version_number.desc())
        )

    def active_conversation_session(self, project_id: str) -> ConversationSession | None:
        return self.session.scalar(
            select(ConversationSession)
            .where(
                ConversationSession.project_id == project_id,
                ConversationSession.status == "active",
            )
            .order_by(ConversationSession.started_at.desc())
        )

    def requirement_candidate(self, candidate_id: str) -> RequirementCandidate | None:
        return self.session.get(RequirementCandidate, candidate_id)

    def latest_conversation_candidate(
        self,
        project_id: str,
        conversation_session_id: str,
        base_requirement_version_id: str,
    ) -> RequirementCandidate | None:
        return self.session.scalar(
            select(RequirementCandidate)
            .where(
                RequirementCandidate.project_id == project_id,
                RequirementCandidate.conversation_session_id == conversation_session_id,
                RequirementCandidate.base_requirement_version_id == base_requirement_version_id,
                RequirementCandidate.status.in_(("awaiting_review", "no_change")),
            )
            .order_by(RequirementCandidate.created_at.desc(), RequirementCandidate.id.desc())
        )

    def agent_run(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)

    def agent_manifest(self, manifest_id: str) -> AgentInputManifest | None:
        return self.session.get(AgentInputManifest, manifest_id)

    def creative_proposal(self, proposal_id: str) -> CreativeTurnProposal | None:
        return self.session.get(CreativeTurnProposal, proposal_id)

    def active_creative_proposal(self, project_id: str) -> CreativeTurnProposal | None:
        return self.session.scalar(
            select(CreativeTurnProposal)
            .where(
                CreativeTurnProposal.project_id == project_id,
                CreativeTurnProposal.status == "active",
            )
            .order_by(CreativeTurnProposal.created_at.desc())
        )

    def creative_proposals(self, project_id: str) -> list[CreativeTurnProposal]:
        return list(self.session.scalars(
            select(CreativeTurnProposal)
            .where(CreativeTurnProposal.project_id == project_id)
            .order_by(CreativeTurnProposal.created_at.desc())
        ))

    def suggestion_selection(
        self,
        proposal_id: str,
        suggestion_set_id: str,
    ) -> CreativeSuggestionSelection | None:
        return self.session.scalar(select(CreativeSuggestionSelection).where(
            CreativeSuggestionSelection.proposal_id == proposal_id,
            CreativeSuggestionSelection.suggestion_set_id == suggestion_set_id,
        ))

    def suggestion_selections(self, project_id: str) -> list[CreativeSuggestionSelection]:
        return list(self.session.scalars(
            select(CreativeSuggestionSelection)
            .where(CreativeSuggestionSelection.project_id == project_id)
            .order_by(CreativeSuggestionSelection.selected_at)
        ))

    def pending_clarifications(self, project_id: str) -> list[ClarificationRequest]:
        return list(self.session.scalars(select(ClarificationRequest).where(
            ClarificationRequest.project_id == project_id,
            ClarificationRequest.status == "pending",
        )))

    def message(self, message_id: str) -> Message | None:
        return self.session.get(Message, message_id)

    def reviewable_candidates(
        self,
        project_id: str,
        *,
        exclude_id: str | None = None,
    ) -> list[RequirementCandidate]:
        statement = select(RequirementCandidate).where(
            RequirementCandidate.project_id == project_id,
            RequirementCandidate.status == "awaiting_review",
        )
        if exclude_id is not None:
            statement = statement.where(RequirementCandidate.id != exclude_id)
        return list(self.session.scalars(
            statement.order_by(RequirementCandidate.created_at.desc(), RequirementCandidate.id.desc())
        ))

    def manifest_messages(self, project_id: str, conversation_session_id: str) -> list[Message]:
        return list(self.session.scalars(
            select(Message).where(
                Message.project_id == project_id,
                Message.conversation_session_id == conversation_session_id,
            ).order_by(Message.created_at, Message.id)
        ))

    def confirmed_bindings(self, project_id: str) -> list[AttachmentBinding]:
        return list(self.session.scalars(
            select(AttachmentBinding).where(
                AttachmentBinding.project_id == project_id,
                AttachmentBinding.status == "confirmed",
            ).order_by(AttachmentBinding.confirmed_at, AttachmentBinding.id)
        ))

    def clarification(self, clarification_id: str) -> ClarificationRequest | None:
        return self.session.get(ClarificationRequest, clarification_id)

    def attachment(self, attachment_id: str) -> Attachment | None:
        return self.session.get(Attachment, attachment_id)

    def entity(self, entity_id: str) -> Entity | None:
        return self.session.get(Entity, entity_id)

    def entity_version(self, version_id: str) -> EntityVersion | None:
        return self.session.get(EntityVersion, version_id)

    def entity(self, entity_id: str) -> Entity | None:
        return self.session.get(Entity, entity_id)

    def active_entity_version(self, entity_id: str) -> EntityVersion | None:
        return self.session.scalar(select(EntityVersion).where(
            EntityVersion.entity_id == entity_id,
            EntityVersion.is_active.is_(True),
        ))

    def view_messages(self, project_id: str, conversation_session_id: str) -> list[Message]:
        return list(self.session.scalars(
            select(Message).where(
                Message.project_id == project_id,
                Message.conversation_session_id == conversation_session_id,
            ).order_by(Message.created_at)
        ))

    def candidate_history(self, project_id: str) -> list[RequirementCandidate]:
        return list(self.session.scalars(
            select(RequirementCandidate)
            .where(RequirementCandidate.project_id == project_id)
            .order_by(RequirementCandidate.created_at.desc())
        ))

    def agent_runs(self, project_id: str) -> list[AgentRun]:
        return list(self.session.scalars(
            select(AgentRun).where(AgentRun.project_id == project_id).order_by(AgentRun.started_at.desc())
        ))

    def attachments(self, project_id: str) -> list[Attachment]:
        return list(self.session.scalars(
            select(Attachment).where(Attachment.project_id == project_id).order_by(Attachment.created_at.desc())
        ))

    def bindings(self, project_id: str) -> list[AttachmentBinding]:
        return list(self.session.scalars(
            select(AttachmentBinding)
            .where(AttachmentBinding.project_id == project_id)
            .order_by(AttachmentBinding.confirmed_at)
        ))

    def active_pending_clarifications(
        self,
        project_id: str,
        requirement_version_id: str,
    ) -> list[ClarificationRequest]:
        return list(self.session.scalars(
            select(ClarificationRequest).where(
                ClarificationRequest.project_id == project_id,
                ClarificationRequest.status == "pending",
                ClarificationRequest.base_requirement_version_id == requirement_version_id,
            ).order_by(ClarificationRequest.created_at)
        ))


class SqlAlchemyPlanningRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: PlanningRecord) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def confirmed_binding_versions(self, project_id: str) -> list[AttachmentBinding]:
        return list(self.session.scalars(
            select(AttachmentBinding)
            .join(EntityVersion, EntityVersion.id == AttachmentBinding.entity_version_id)
            .where(
                AttachmentBinding.project_id == project_id,
                AttachmentBinding.status == "confirmed",
                EntityVersion.status == "confirmed",
            )
            .order_by(AttachmentBinding.confirmed_at, AttachmentBinding.id)
        ))

    def confirmed_binding_ids(self, project_id: str) -> list[str]:
        return list(self.session.scalars(
            select(AttachmentBinding.id).where(
                AttachmentBinding.project_id == project_id,
                AttachmentBinding.status == "confirmed",
                AttachmentBinding.entity_version_id.is_not(None),
            ).order_by(AttachmentBinding.confirmed_at, AttachmentBinding.id)
        ))

    def active_brief_for_requirement(
        self,
        project_id: str,
        requirement_version_id: str,
    ) -> CreativeBriefCandidate | None:
        return self.session.scalar(select(CreativeBriefCandidate).where(
            CreativeBriefCandidate.project_id == project_id,
            CreativeBriefCandidate.requirement_version_id == requirement_version_id,
            CreativeBriefCandidate.status.in_(("awaiting_review", "accepted")),
        ))

    def creative_brief(self, candidate_id: str) -> CreativeBriefCandidate | None:
        return self.session.get(CreativeBriefCandidate, candidate_id)

    def entity_version(self, version_id: str) -> EntityVersion | None:
        return self.session.get(EntityVersion, version_id)

    def attachment(self, attachment_id: str) -> Attachment | None:
        return self.session.get(Attachment, attachment_id)

    def reviewable_shot_plan_for_requirement(
        self,
        project_id: str,
        requirement_version_id: str,
    ) -> ShotPlanCandidate | None:
        return self.session.scalar(select(ShotPlanCandidate).where(
            ShotPlanCandidate.project_id == project_id,
            ShotPlanCandidate.requirement_version_id == requirement_version_id,
            ShotPlanCandidate.status == "awaiting_review",
        ))

    def shot_plan(self, candidate_id: str) -> ShotPlanCandidate | None:
        return self.session.get(ShotPlanCandidate, candidate_id)

    def transition_reviewable_shot_plan(
        self,
        candidate_id: str,
        expected_row_version: int,
        status: str,
        decided_at: datetime,
        validation_errors: list[dict] | None = None,
    ) -> bool:
        values: dict = {
            "status": status,
            "decided_at": decided_at,
            "row_version": ShotPlanCandidate.row_version + 1,
        }
        if validation_errors is not None:
            values["validation_errors"] = validation_errors
        result = self.session.execute(
            update(ShotPlanCandidate)
            .where(
                ShotPlanCandidate.id == candidate_id,
                ShotPlanCandidate.status.in_(("awaiting_review", "rejected")),
                ShotPlanCandidate.row_version == expected_row_version,
            )
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount == 1

    def active_plans(self, project_id: str) -> list[PlanVersion]:
        return list(self.session.scalars(select(PlanVersion).where(
            PlanVersion.project_id == project_id,
            PlanVersion.is_active.is_(True),
        )))

    def next_plan_version_number(self, project_id: str) -> int:
        current = self.session.scalar(select(func.max(PlanVersion.version_number)).where(
            PlanVersion.project_id == project_id,
        ))
        return (current or 0) + 1

    def shots(self, plan_version_id: str) -> list[Shot]:
        return list(self.session.scalars(
            select(Shot).where(Shot.plan_version_id == plan_version_id).order_by(Shot.sequence_number)
        ))

    def brief_history(self, project_id: str) -> list[CreativeBriefCandidate]:
        return list(self.session.scalars(
            select(CreativeBriefCandidate)
            .where(CreativeBriefCandidate.project_id == project_id)
            .order_by(CreativeBriefCandidate.created_at.desc())
        ))

    def shot_plan_history(self, project_id: str) -> list[ShotPlanCandidate]:
        return list(self.session.scalars(
            select(ShotPlanCandidate)
            .where(ShotPlanCandidate.project_id == project_id)
            .order_by(ShotPlanCandidate.created_at.desc())
        ))

    def plan_history(self, project_id: str) -> list[PlanVersion]:
        return list(self.session.scalars(
            select(PlanVersion)
            .where(PlanVersion.project_id == project_id)
            .order_by(PlanVersion.version_number.desc())
        ))

    def active_entity_versions(self, project_id: str) -> list[tuple[EntityVersion, Entity]]:
        return list(self.session.execute(
            select(EntityVersion, Entity)
            .join(Entity, Entity.id == EntityVersion.entity_id)
            .where(EntityVersion.project_id == project_id, EntityVersion.is_active.is_(True))
            .order_by(Entity.entity_type, Entity.id)
        ).all())


class SqlAlchemyProductionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: ProductionRecord) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def component(self, model_type: type[ModelT], component_id: str) -> ModelT | None:
        return self.session.get(model_type, component_id)

    def snapshot_entities(self, snapshot_id: str) -> list[SnapshotEntityVersion]:
        return list(self.session.scalars(
            select(SnapshotEntityVersion)
            .where(SnapshotEntityVersion.snapshot_id == snapshot_id)
            .order_by(SnapshotEntityVersion.role, SnapshotEntityVersion.entity_version_id)
        ))

    def snapshot_nodes(self, snapshot_id: str, *, ordered: bool = False) -> list[DAGNode]:
        statement = select(DAGNode).where(DAGNode.snapshot_id == snapshot_id)
        if ordered:
            statement = statement.order_by(DAGNode.node_key)
        return list(self.session.scalars(statement))

    def snapshot_edges(self, snapshot_id: str) -> list[DependencyEdge]:
        return list(self.session.scalars(
            select(DependencyEdge)
            .where(DependencyEdge.snapshot_id == snapshot_id)
            .order_by(DependencyEdge.parent_node_id, DependencyEdge.child_node_id)
        ))

    def pricing_rules(self, pricing_catalog_version_id: str) -> list[PricingRule]:
        return list(self.session.scalars(select(PricingRule).where(
            PricingRule.pricing_catalog_version_id == pricing_catalog_version_id
        )))

    def impact_analysis(self, analysis_id: str) -> ProductionImpactAnalysis | None:
        return self.session.get(ProductionImpactAnalysis, analysis_id)

    def plan(self, plan_id: str) -> PlanVersion | None:
        return self.session.get(PlanVersion, plan_id)

    def configuration(self, config_id: str) -> ProductionConfigVersion | None:
        return self.session.get(ProductionConfigVersion, config_id)

    def plan_shots(self, plan_id: str) -> list[Shot]:
        return list(self.session.scalars(
            select(Shot).where(Shot.plan_version_id == plan_id).order_by(Shot.sequence_number)
        ))

    def entity_version(self, version_id: str) -> EntityVersion | None:
        return self.session.get(EntityVersion, version_id)

    def attachment(self, attachment_id: str) -> Attachment | None:
        return self.session.get(Attachment, attachment_id)

    def snapshot_for_contract(self, project_id: str, contract_hash: str) -> ProductionSnapshot | None:
        return self.session.scalar(
            select(ProductionSnapshot)
            .where(
                ProductionSnapshot.project_id == project_id,
                ProductionSnapshot.contract_hash == contract_hash,
            )
            .order_by(ProductionSnapshot.snapshot_number)
        )

    def snapshot_for_impact(self, analysis_id: str) -> ProductionSnapshot | None:
        return self.session.scalar(select(ProductionSnapshot).where(
            ProductionSnapshot.impact_analysis_id == analysis_id
        ))

    def next_snapshot_number(self, project_id: str) -> int:
        current = self.session.scalar(select(func.max(ProductionSnapshot.snapshot_number)).where(
            ProductionSnapshot.project_id == project_id
        ))
        return (current or 0) + 1

    def snapshot(self, snapshot_id: str) -> ProductionSnapshot | None:
        return self.session.get(ProductionSnapshot, snapshot_id)

    def pricing_catalog(self, pricing_id: str) -> PricingCatalogVersion | None:
        return self.session.get(PricingCatalogVersion, pricing_id)

    def workflow(self, workflow_id: str) -> WorkflowSlotVersion | None:
        return self.session.get(WorkflowSlotVersion, workflow_id)

    def provider(self, provider_id: str) -> ProviderConfigVersion | None:
        return self.session.get(ProviderConfigVersion, provider_id)

    def has_work_items(self, snapshot_id: str) -> bool:
        return self.session.scalar(select(WorkItem.id).where(WorkItem.snapshot_id == snapshot_id)) is not None

    def work_items(self, snapshot_id: str) -> list[WorkItem]:
        return list(self.session.scalars(
            select(WorkItem)
            .where(WorkItem.snapshot_id == snapshot_id)
            .order_by(WorkItem.created_at, WorkItem.id)
        ))

    def work_attempts(self, work_item_ids: list[str]) -> list[WorkAttempt]:
        if not work_item_ids:
            return []
        return list(self.session.scalars(
            select(WorkAttempt)
            .where(WorkAttempt.work_item_id.in_(work_item_ids))
            .order_by(WorkAttempt.work_item_id, WorkAttempt.attempt_number)
        ))

    def active_plan(self, project_id: str) -> PlanVersion | None:
        return self.session.scalar(
            select(PlanVersion)
            .where(
                PlanVersion.project_id == project_id,
                PlanVersion.is_active.is_(True),
                PlanVersion.status == "confirmed",
            )
            .order_by(PlanVersion.version_number.desc())
        )

    def published_configurations(self) -> list[ProductionConfigVersion]:
        return list(self.session.scalars(
            select(ProductionConfigVersion)
            .where(ProductionConfigVersion.status == "published")
            .order_by(ProductionConfigVersion.published_at.desc(), ProductionConfigVersion.id.desc())
        ))

    def video_specs(self, config_id: str) -> list[VideoSpecVersion]:
        return list(self.session.scalars(select(VideoSpecVersion).where(
            VideoSpecVersion.production_config_version_id == config_id
        )))

    def workflows(self, config_id: str) -> list[WorkflowSlotVersion]:
        return list(self.session.scalars(select(WorkflowSlotVersion).where(
            WorkflowSlotVersion.production_config_version_id == config_id
        )))

    def pricing_catalogs(self, config_id: str) -> list[PricingCatalogVersion]:
        return list(self.session.scalars(select(PricingCatalogVersion).where(
            PricingCatalogVersion.production_config_version_id == config_id
        )))

    def storage_policies(self, config_id: str) -> list[StoragePolicyVersion]:
        return list(self.session.scalars(select(StoragePolicyVersion).where(
            StoragePolicyVersion.production_config_version_id == config_id
        )))

    def impact_history(self, project_id: str) -> list[ProductionImpactAnalysis]:
        return list(self.session.scalars(
            select(ProductionImpactAnalysis)
            .where(ProductionImpactAnalysis.project_id == project_id)
            .order_by(ProductionImpactAnalysis.created_at.desc())
        ))

    def snapshot_history(self, project_id: str) -> list[ProductionSnapshot]:
        return list(self.session.scalars(
            select(ProductionSnapshot)
            .where(ProductionSnapshot.project_id == project_id)
            .order_by(ProductionSnapshot.snapshot_number.desc())
        ))


class SqlAlchemyQualityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: QualityRecord) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def asset(self, asset_id: str) -> Asset | None:
        return self.session.get(Asset, asset_id)

    def snapshot(self, snapshot_id: str) -> ProductionSnapshot | None:
        return self.session.get(ProductionSnapshot, snapshot_id)

    def published_storage_policies(self, config_id: str) -> list[StoragePolicyVersion]:
        return list(self.session.scalars(select(StoragePolicyVersion).where(
            StoragePolicyVersion.production_config_version_id == config_id,
            StoragePolicyVersion.status == "published",
        )))

    def work_attempt(self, attempt_id: str) -> WorkAttempt | None:
        return self.session.get(WorkAttempt, attempt_id)

    def work_item(self, work_item_id: str) -> WorkItem | None:
        return self.session.get(WorkItem, work_item_id)

    def dag_node(self, node_id: str) -> DAGNode | None:
        return self.session.get(DAGNode, node_id)

    def asset_for_output(self, work_attempt_id: str, output_index: int) -> Asset | None:
        return self.session.scalar(select(Asset).where(
            Asset.work_attempt_id == work_attempt_id,
            Asset.output_index == output_index,
        ))

    def work_item_for_node(self, snapshot_id: str, dag_node_id: str) -> WorkItem | None:
        return self.session.scalar(select(WorkItem).where(
            WorkItem.snapshot_id == snapshot_id,
            WorkItem.dag_node_id == dag_node_id,
        ))

    def qc_report(self, report_id: str) -> QCReport | None:
        return self.session.get(QCReport, report_id)

    def qc_candidate(self, candidate_id: str) -> QCReportCandidate | None:
        return self.session.get(QCReportCandidate, candidate_id)

    def latest_qc_candidate(self, asset_id: str) -> QCReportCandidate | None:
        return self.session.scalar(
            select(QCReportCandidate)
            .where(QCReportCandidate.asset_id == asset_id)
            .order_by(QCReportCandidate.created_at.desc())
            .limit(1)
        )

    def latest_qc_agent_run(self, asset_id: str) -> AgentRun | None:
        return self.session.scalar(
            select(AgentRun)
            .join(QCReportCandidate, QCReportCandidate.agent_run_id == AgentRun.id, isouter=True)
            .join(AgentInputManifest, AgentInputManifest.id == AgentRun.input_manifest_id)
            .where(
                AgentRun.agent_role == "qc",
                AgentInputManifest.payload["asset"]["id"].as_string() == asset_id,
            )
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )

    def next_report_number(self, asset_id: str) -> int:
        current = self.session.scalar(select(func.max(QCReport.report_number)).where(
            QCReport.asset_id == asset_id
        ))
        return (current or 0) + 1

    def has_review_decision(self, report_id: str) -> bool:
        return self.session.scalar(select(AssetReviewDecision.id).where(
            AssetReviewDecision.qc_report_id == report_id
        )) is not None

    def findings(self, report_id: str) -> list[QCFinding]:
        return list(self.session.scalars(
            select(QCFinding)
            .where(QCFinding.qc_report_id == report_id)
            .order_by(QCFinding.created_at, QCFinding.id)
        ))

    def dependency_edges(self, snapshot_id: str) -> list[DependencyEdge]:
        return list(self.session.scalars(select(DependencyEdge).where(
            DependencyEdge.snapshot_id == snapshot_id
        )))

    def dag_nodes_by_ids(self, node_ids: set[str]) -> list[DAGNode]:
        if not node_ids:
            return []
        return list(self.session.scalars(select(DAGNode).where(DAGNode.id.in_(node_ids))))

    def latest_qc_report(self, asset_id: str) -> QCReport | None:
        return self.session.scalar(
            select(QCReport)
            .where(QCReport.asset_id == asset_id)
            .order_by(QCReport.report_number.desc())
            .limit(1)
        )

    def review_decisions(self, asset_id: str) -> list[AssetReviewDecision]:
        return list(self.session.scalars(
            select(AssetReviewDecision)
            .where(AssetReviewDecision.asset_id == asset_id)
            .order_by(AssetReviewDecision.created_at)
        ))

    def project_assets(self, project_id: str) -> list[Asset]:
        return list(self.session.scalars(
            select(Asset).where(Asset.project_id == project_id).order_by(Asset.created_at.desc())
        ))

    def snapshot_nodes(self, snapshot_id: str) -> list[DAGNode]:
        return list(self.session.scalars(
            select(DAGNode).where(DAGNode.snapshot_id == snapshot_id).order_by(DAGNode.node_key)
        ))


class SqlAlchemyEditorRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: EditorRecord) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def timeline(self, timeline_id: str) -> Timeline | None:
        return self.session.get(Timeline, timeline_id)

    def snapshot(self, snapshot_id: str) -> ProductionSnapshot | None:
        return self.session.get(ProductionSnapshot, snapshot_id)

    def agent_run(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)

    def timeline_items(self, timeline_id: str) -> list[TimelineItem]:
        return list(self.session.scalars(
            select(TimelineItem)
            .where(TimelineItem.timeline_id == timeline_id)
            .order_by(TimelineItem.track_type, TimelineItem.sequence_number)
        ))

    def has_timeline(self, project_id: str) -> bool:
        return self.session.scalar(select(Timeline.id).where(Timeline.project_id == project_id)) is not None

    def next_timeline_version(self, project_id: str) -> int:
        current = self.session.scalar(select(func.max(Timeline.version_number)).where(
            Timeline.project_id == project_id
        ))
        return (current or 0) + 1

    def asset(self, asset_id: str) -> Asset | None:
        return self.session.get(Asset, asset_id)

    def confirmed_timelines(self, project_id: str, *, exclude_id: str) -> list[Timeline]:
        return list(self.session.scalars(select(Timeline).where(
            Timeline.project_id == project_id,
            Timeline.status == "confirmed",
            Timeline.id != exclude_id,
        )))

    def timeline_asset_ids(self, timeline_id: str) -> list[str]:
        return list(self.session.scalars(select(TimelineItem.asset_id).where(
            TimelineItem.timeline_id == timeline_id,
            TimelineItem.asset_id.is_not(None),
        )))

    def assets_by_ids(self, asset_ids: list[str]) -> list[Asset]:
        if not asset_ids:
            return []
        return list(self.session.scalars(select(Asset).where(Asset.id.in_(asset_ids))))

    def available_assets(self, project_id: str, snapshot_id: str) -> list[Asset]:
        return list(self.session.scalars(
            select(Asset)
            .where(
                Asset.project_id == project_id,
                Asset.snapshot_id == snapshot_id,
                Asset.state.in_(["approved", "used"]),
                Asset.asset_type.in_(["video", "audio", "subtitle"]),
            )
            .order_by(Asset.asset_type, Asset.created_at, Asset.id)
        ))

    def dag_nodes_by_ids(self, node_ids: list[str]) -> list[DAGNode]:
        if not node_ids:
            return []
        return list(self.session.scalars(select(DAGNode).where(DAGNode.id.in_(node_ids))))

    def timeline_history(self, project_id: str) -> list[Timeline]:
        return list(self.session.scalars(
            select(Timeline).where(Timeline.project_id == project_id).order_by(Timeline.version_number.desc())
        ))


class SqlAlchemyDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: DeliveryRecord) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def attempt(self, attempt_id: str) -> DeliveryAttempt | None:
        return self.session.get(DeliveryAttempt, attempt_id)

    def confirmed_timelines(
        self,
        project_id: str,
        snapshot_id: str | None,
        *,
        timeline_id: str | None = None,
    ) -> list[Timeline]:
        statement = select(Timeline).where(
            Timeline.project_id == project_id,
            Timeline.snapshot_id == snapshot_id,
            Timeline.status == "confirmed",
        )
        if timeline_id:
            statement = statement.where(Timeline.id == timeline_id)
        return list(self.session.scalars(statement))

    def snapshot(self, snapshot_id: str) -> ProductionSnapshot | None:
        return self.session.get(ProductionSnapshot, snapshot_id)

    def timeline_items(self, timeline_id: str) -> list[TimelineItem]:
        return list(self.session.scalars(
            select(TimelineItem)
            .where(TimelineItem.timeline_id == timeline_id)
            .order_by(TimelineItem.track_type, TimelineItem.sequence_number)
        ))

    def assets_by_ids(self, asset_ids: list[str]) -> list[Asset]:
        if not asset_ids:
            return []
        return list(self.session.scalars(select(Asset).where(Asset.id.in_(asset_ids))))

    def has_attempt_for_timeline(self, timeline_id: str) -> bool:
        return self.session.scalar(select(DeliveryAttempt.id).where(
            DeliveryAttempt.timeline_id == timeline_id
        )) is not None

    def asset_by_uri(self, storage_backend: str, uri: str) -> Asset | None:
        return self.session.scalar(select(Asset).where(
            Asset.storage_backend == storage_backend,
            Asset.uri == uri,
        ))

    def next_report_number(self, asset_id: str) -> int:
        current = self.session.scalar(select(func.max(QCReport.report_number)).where(
            QCReport.asset_id == asset_id
        ))
        return (current or 0) + 1

    def asset(self, asset_id: str) -> Asset | None:
        return self.session.get(Asset, asset_id)

    def delivery_timelines(self, project_id: str, snapshot_id: str) -> list[Timeline]:
        return list(self.session.scalars(
            select(Timeline)
            .where(
                Timeline.project_id == project_id,
                Timeline.snapshot_id == snapshot_id,
                Timeline.status.in_(["confirmed", "exported"]),
            )
            .order_by(Timeline.version_number.desc())
        ))

    def project_attempts(self, project_id: str) -> list[DeliveryAttempt]:
        return list(self.session.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.project_id == project_id)
            .order_by(DeliveryAttempt.attempt_number.desc())
        ))


class SqlAlchemyWorkRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def required_parent_items(self, item: WorkItem) -> list[WorkItem]:
        parent_node_ids = list(self.session.scalars(select(DependencyEdge.parent_node_id).where(
            DependencyEdge.snapshot_id == item.snapshot_id,
            DependencyEdge.child_node_id == item.dag_node_id,
            DependencyEdge.dependency_type == "required",
        )))
        if not parent_node_ids:
            return []
        return list(self.session.scalars(select(WorkItem).where(
            WorkItem.snapshot_id == item.snapshot_id,
            WorkItem.dag_node_id.in_(parent_node_ids),
        )))

    def snapshot_work_states(self, snapshot_id: str) -> list[str]:
        return list(self.session.scalars(select(WorkItem.status).where(
            WorkItem.snapshot_id == snapshot_id
        )))

    def lease_candidates(self, available_at: datetime, *, limit: int = 50) -> list[WorkItem]:
        return list(self.session.scalars(
            select(WorkItem)
            .where(WorkItem.status == "queued", WorkItem.available_at <= available_at)
            .order_by(WorkItem.priority, WorkItem.created_at, WorkItem.id)
            .limit(limit)
        ))

    def poll_candidates(self, available_at: datetime, *, limit: int = 50) -> list[WorkItem]:
        return list(self.session.scalars(
            select(WorkItem)
            .join(WorkAttempt, WorkAttempt.id == WorkItem.current_attempt_id)
            .where(
                WorkItem.status == "in_progress",
                WorkItem.available_at <= available_at,
                WorkAttempt.state.in_(["submitting", "submitted"]),
                (WorkAttempt.execution_lock_expires_at.is_(None) | (WorkAttempt.execution_lock_expires_at <= available_at)),
            )
            .order_by(WorkItem.priority, WorkItem.created_at, WorkItem.id)
            .limit(limit)
        ))

    def attempt(self, attempt_id: str) -> WorkAttempt | None:
        return self.session.get(WorkAttempt, attempt_id)

    def project(self, project_id: str) -> Project | None:
        return self.session.get(Project, project_id)

    def snapshot(self, snapshot_id: str) -> ProductionSnapshot | None:
        return self.session.get(ProductionSnapshot, snapshot_id)

    def claim(self, item: WorkItem, started_at: datetime) -> bool:
        claimed = self.session.execute(
            update(WorkItem)
            .where(
                WorkItem.id == item.id,
                WorkItem.status == "queued",
                WorkItem.row_version == item.row_version,
            )
            .values(
                status="in_progress",
                started_at=started_at,
                row_version=item.row_version + 1,
                updated_at=started_at,
            )
        )
        return claimed.rowcount == 1

    def claim_attempt(self, attempt: WorkAttempt, owner: str, expires_at: datetime, now: datetime) -> bool:
        claimed = self.session.execute(
            update(WorkAttempt)
            .where(
                WorkAttempt.id == attempt.id,
                WorkAttempt.state.in_(["submitting", "submitted"]),
                (WorkAttempt.execution_lock_expires_at.is_(None) | (WorkAttempt.execution_lock_expires_at <= now)),
            )
            .values(execution_lock_owner=owner, execution_lock_expires_at=expires_at)
        )
        return claimed.rowcount == 1

    def work_item(self, work_item_id: str) -> WorkItem | None:
        return self.session.get(WorkItem, work_item_id)

    def flush(self) -> None:
        self.session.flush()


class SqlAlchemyConfigurationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: ConfigurationRecord) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def receipt(self, command_id: str) -> ConfigurationCommandReceipt | None:
        return self.session.scalar(select(ConfigurationCommandReceipt).where(
            ConfigurationCommandReceipt.command_id == command_id
        ))

    def configuration(self, config_id: str) -> ProductionConfigVersion | None:
        return self.session.get(ProductionConfigVersion, config_id)

    def next_configuration_version(self, config_key: str) -> int:
        current = self.session.scalar(select(func.max(ProductionConfigVersion.version_number)).where(
            ProductionConfigVersion.config_key == config_key
        ))
        return (current or 0) + 1

    def next_component_version(self, component_type: str, key: str) -> int:
        model, key_field_name = CONFIGURATION_COMPONENT_MODELS[component_type]
        key_column = getattr(model, key_field_name)
        current = self.session.scalar(select(func.max(model.version_number)).where(key_column == key))
        return (current or 0) + 1

    def component_rows(self, config_id: str) -> dict[str, list[ConfigurationComponentRecord]]:
        return {
            component_type: list(self.session.scalars(
                select(model)
                .where(model.production_config_version_id == config_id)
                .order_by(model.created_at, model.id)
            ))
            for component_type, (model, _) in CONFIGURATION_COMPONENT_MODELS.items()
        }

    def pricing_rules(self, catalog_ids: list[str], *, ordered: bool = False) -> list[PricingRule]:
        if not catalog_ids:
            return []
        statement = select(PricingRule).where(PricingRule.pricing_catalog_version_id.in_(catalog_ids))
        if ordered:
            statement = statement.order_by(PricingRule.workflow_slot_version_id, PricingRule.id)
        return list(self.session.scalars(statement))

    def delete_components(self, config_id: str) -> None:
        self.session.execute(delete(ProductionConfigComponent).where(
            ProductionConfigComponent.production_config_version_id == config_id
        ))
        pricing_ids = list(self.session.scalars(select(PricingCatalogVersion.id).where(
            PricingCatalogVersion.production_config_version_id == config_id
        )))
        if pricing_ids:
            self.session.execute(delete(PricingRule).where(
                PricingRule.pricing_catalog_version_id.in_(pricing_ids)
            ))
        deletion_order = (
            PricingCatalogVersion,
            AudioConfigVersion,
            WorkflowSlotVersion,
            ModelConfigVersion,
            VideoSpecVersion,
            ProviderConfigVersion,
            StoragePolicyVersion,
        )
        for model in deletion_order:
            self.session.execute(delete(model).where(model.production_config_version_id == config_id))
        self.session.flush()

    def references(self, config_id: str) -> list[ConfigurationReference]:
        return list(self.session.scalars(
            select(ConfigurationReference)
            .where(ConfigurationReference.production_config_version_id == config_id)
            .order_by(ConfigurationReference.created_at, ConfigurationReference.id)
        ))

    def configurations(self) -> list[ProductionConfigVersion]:
        return list(self.session.scalars(
            select(ProductionConfigVersion)
            .order_by(ProductionConfigVersion.created_at.desc(), ProductionConfigVersion.id.desc())
        ))

    def all_components(self, component_type: str) -> list[ConfigurationComponentRecord]:
        model, _ = CONFIGURATION_COMPONENT_MODELS[component_type]
        return list(self.session.scalars(
            select(model).order_by(model.created_at.desc(), model.id.desc())
        ))

    def workflow_slot_versions(self, slot_key: str) -> list[WorkflowSlotVersion]:
        return list(self.session.scalars(
            select(WorkflowSlotVersion)
            .where(WorkflowSlotVersion.slot_key == slot_key)
            .order_by(WorkflowSlotVersion.version_number.desc(), WorkflowSlotVersion.id.desc())
        ))


class SqlAlchemyRegistryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def projects(self) -> list[Project]:
        return list(self.session.scalars(
            select(Project).order_by(Project.updated_at.desc(), Project.id)
        ))

    def entities(self) -> list[Entity]:
        return list(self.session.scalars(
            select(Entity).order_by(Entity.entity_type, Entity.display_name, Entity.id)
        ))

    def entity_versions(self) -> list[EntityVersion]:
        return list(self.session.scalars(
            select(EntityVersion).order_by(EntityVersion.entity_id, EntityVersion.version_number.desc())
        ))

    def attachments_by_ids(self, attachment_ids: set[str]) -> list[Attachment]:
        if not attachment_ids:
            return []
        return list(self.session.scalars(select(Attachment).where(Attachment.id.in_(attachment_ids))))

    def bindings_by_entity_version_ids(self, entity_version_ids: set[str]) -> list[AttachmentBinding]:
        if not entity_version_ids:
            return []
        return list(self.session.scalars(
            select(AttachmentBinding)
            .where(AttachmentBinding.entity_version_id.in_(entity_version_ids))
            .order_by(AttachmentBinding.confirmed_at, AttachmentBinding.id)
        ))

    def snapshots(self) -> list[ProductionSnapshot]:
        return list(self.session.scalars(select(ProductionSnapshot)))

    def snapshot_entity_versions(self, entity_version_ids: set[str]) -> list[SnapshotEntityVersion]:
        if not entity_version_ids:
            return []
        return list(self.session.scalars(
            select(SnapshotEntityVersion)
            .where(SnapshotEntityVersion.entity_version_id.in_(entity_version_ids))
            .order_by(SnapshotEntityVersion.created_at, SnapshotEntityVersion.id)
        ))

    def plans(self) -> list[PlanVersion]:
        return list(self.session.scalars(select(PlanVersion)))

    def shots(self) -> list[Shot]:
        return list(self.session.scalars(
            select(Shot).order_by(Shot.plan_version_id, Shot.sequence_number)
        ))

    def attachment(self, attachment_id: str) -> Attachment | None:
        return self.session.get(Attachment, attachment_id)


class SqlAlchemyControlRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def active_plan(self, project_id: str) -> PlanVersion | None:
        return self.session.scalar(
            select(PlanVersion)
            .where(PlanVersion.project_id == project_id, PlanVersion.is_active.is_(True))
            .order_by(PlanVersion.version_number.desc())
            .limit(1)
        )

    def requirement_version(self, project_id: str, requirement_version_id: str) -> RequirementVersion | None:
        return self.session.scalar(
            select(RequirementVersion).where(
                RequirementVersion.id == requirement_version_id,
                RequirementVersion.project_id == project_id,
            )
        )

    def shots_for_plan(self, project_id: str, plan_version_id: str) -> list[Shot]:
        return list(self.session.scalars(
            select(Shot)
            .where(Shot.project_id == project_id, Shot.plan_version_id == plan_version_id)
            .order_by(Shot.sequence_number)
        ))

    def snapshots(self, project_id: str) -> list[ProductionSnapshot]:
        return list(self.session.scalars(
            select(ProductionSnapshot)
            .where(ProductionSnapshot.project_id == project_id)
            .order_by(ProductionSnapshot.snapshot_number.desc())
        ))

    def snapshot(self, snapshot_id: str) -> ProductionSnapshot | None:
        return self.session.get(ProductionSnapshot, snapshot_id)

    def attempts_for_items(self, work_item_ids: list[str]) -> list[WorkAttempt]:
        if not work_item_ids:
            return []
        return list(self.session.scalars(
            select(WorkAttempt)
            .where(WorkAttempt.work_item_id.in_(work_item_ids))
            .order_by(WorkAttempt.work_item_id, WorkAttempt.attempt_number)
        ))

    def cost_events(self, project_id: str) -> list[CostEvent]:
        return list(self.session.scalars(
            select(CostEvent)
            .where(CostEvent.project_id == project_id)
            .order_by(CostEvent.occurred_at.desc(), CostEvent.id.desc())
        ))

    def dag_nodes(self, snapshot_id: str) -> list[DAGNode]:
        return list(self.session.scalars(select(DAGNode).where(DAGNode.snapshot_id == snapshot_id)))

    def latest_blocked_report(self, asset_id: str) -> QCReport | None:
        return self.session.scalar(
            select(QCReport)
            .where(QCReport.asset_id == asset_id, QCReport.status == "blocked")
            .order_by(QCReport.report_number.desc())
            .limit(1)
        )

    def findings(self, report_id: str) -> list[QCFinding]:
        return list(self.session.scalars(
            select(QCFinding)
            .where(QCFinding.qc_report_id == report_id)
            .order_by(QCFinding.created_at)
        ))

    def work_items(self, project_id: str, snapshot_id: str | None) -> list[WorkItem]:
        statement = select(WorkItem).where(WorkItem.project_id == project_id)
        if snapshot_id is not None:
            statement = statement.where(WorkItem.snapshot_id == snapshot_id)
        return list(self.session.scalars(statement.order_by(WorkItem.created_at)))

    def assets(self, project_id: str, snapshot_id: str | None) -> list[Asset]:
        statement = select(Asset).where(Asset.project_id == project_id)
        if snapshot_id is not None:
            statement = statement.where(Asset.snapshot_id == snapshot_id)
        return list(self.session.scalars(statement.order_by(Asset.created_at)))

    def latest_timeline(self, project_id: str) -> Timeline | None:
        return self.session.scalar(
            select(Timeline)
            .where(Timeline.project_id == project_id)
            .order_by(Timeline.version_number.desc())
            .limit(1)
        )

    def latest_delivery(self, project_id: str) -> DeliveryAttempt | None:
        return self.session.scalar(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.project_id == project_id)
            .order_by(DeliveryAttempt.created_at.desc())
            .limit(1)
        )

    def has_planning_candidate(self, project_id: str) -> bool:
        brief_id = self.session.scalar(
            select(CreativeBriefCandidate.id).where(CreativeBriefCandidate.project_id == project_id).limit(1)
        )
        shot_plan_id = self.session.scalar(
            select(ShotPlanCandidate.id).where(ShotPlanCandidate.project_id == project_id).limit(1)
        )
        return bool(brief_id or shot_plan_id)

    def events(
        self,
        project_id: str,
        *,
        limit: int = 20,
        before_sequence: int | None = None,
    ) -> list[ProjectEvent]:
        statement = select(ProjectEvent).where(ProjectEvent.project_id == project_id)
        if before_sequence is not None:
            statement = statement.where(ProjectEvent.project_sequence < before_sequence)
        return list(self.session.scalars(
            statement
            .order_by(ProjectEvent.project_sequence.desc())
            .limit(limit)
        ))

    def projects(self, *, include_archived: bool = False) -> list[Project]:
        statement = select(Project)
        if not include_archived:
            statement = statement.where(Project.archived_at.is_(None))
        return list(self.session.scalars(statement.order_by(Project.updated_at.desc())))


class SqlAlchemyContactSheetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def snapshot(self, snapshot_id: str) -> ProductionSnapshot | None:
        return self.session.get(ProductionSnapshot, snapshot_id)

    def nodes(self, snapshot_id: str) -> list[DAGNode]:
        return list(self.session.scalars(
            select(DAGNode)
            .where(DAGNode.snapshot_id == snapshot_id)
            .order_by(DAGNode.node_key, DAGNode.id)
        ))

    def shots(self, project_id: str, plan_version_id: str) -> list[Shot]:
        return list(self.session.scalars(select(Shot).where(
            Shot.project_id == project_id,
            Shot.plan_version_id == plan_version_id,
        )))

    def assets(self, project_id: str, snapshot_id: str) -> list[Asset]:
        return list(self.session.scalars(
            select(Asset)
            .where(Asset.project_id == project_id, Asset.snapshot_id == snapshot_id)
            .order_by(Asset.created_at, Asset.id)
        ))

    def edges(self, snapshot_id: str) -> list[DependencyEdge]:
        return list(self.session.scalars(
            select(DependencyEdge)
            .where(DependencyEdge.snapshot_id == snapshot_id)
            .order_by(DependencyEdge.child_node_id, DependencyEdge.input_slot, DependencyEdge.id)
        ))

    def work_items(self, project_id: str, snapshot_id: str) -> list[WorkItem]:
        return list(self.session.scalars(select(WorkItem).where(
            WorkItem.project_id == project_id,
            WorkItem.snapshot_id == snapshot_id,
        )))

    def attempts_for_items(self, work_item_ids: set[str]) -> list[WorkAttempt]:
        if not work_item_ids:
            return []
        return list(self.session.scalars(select(WorkAttempt).where(
            WorkAttempt.work_item_id.in_(work_item_ids)
        )))

    def entity_versions(self, project_id: str, version_ids: set[str]) -> list[EntityVersion]:
        if not version_ids:
            return []
        return list(self.session.scalars(select(EntityVersion).where(
            EntityVersion.project_id == project_id,
            EntityVersion.id.in_(version_ids),
        )))

    def entities(self, project_id: str, entity_ids: set[str]) -> list[Entity]:
        if not entity_ids:
            return []
        return list(self.session.scalars(select(Entity).where(
            Entity.project_id == project_id,
            Entity.id.in_(entity_ids),
        )))

    def attachments(self, project_id: str, attachment_ids: set[str]) -> list[Attachment]:
        if not attachment_ids:
            return []
        return list(self.session.scalars(select(Attachment).where(
            Attachment.project_id == project_id,
            Attachment.id.in_(attachment_ids),
        )))


class SqlAlchemyImpactRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record) -> None:
        self.session.add(record)

    def flush(self) -> None:
        self.session.flush()

    def decision(self, project_id: str, decision_id: str) -> Decision | None:
        return self.session.scalar(select(Decision).where(
            Decision.project_id == project_id,
            Decision.id == decision_id,
        ))

    def decisions(self, project_id: str) -> list[Decision]:
        return list(self.session.scalars(
            select(Decision).where(Decision.project_id == project_id).order_by(Decision.created_at, Decision.id)
        ))

    def manifests(self, project_id: str) -> list[AgentInputManifest]:
        return list(self.session.scalars(
            select(AgentInputManifest)
            .where(AgentInputManifest.project_id == project_id)
            .order_by(AgentInputManifest.created_at, AgentInputManifest.id)
        ))

    def agent_runs(self, project_id: str) -> list[AgentRun]:
        return list(self.session.scalars(
            select(AgentRun).where(AgentRun.project_id == project_id).order_by(AgentRun.started_at, AgentRun.id)
        ))

    def requirement_candidates(self, project_id: str) -> list[RequirementCandidate]:
        return list(self.session.scalars(select(RequirementCandidate).where(
            RequirementCandidate.project_id == project_id
        )))

    def requirement_versions(self, project_id: str) -> list[RequirementVersion]:
        return list(self.session.scalars(select(RequirementVersion).where(
            RequirementVersion.project_id == project_id
        )))

    def creative_briefs(self, project_id: str) -> list[CreativeBriefCandidate]:
        return list(self.session.scalars(select(CreativeBriefCandidate).where(
            CreativeBriefCandidate.project_id == project_id
        )))

    def shot_plans(self, project_id: str) -> list[ShotPlanCandidate]:
        return list(self.session.scalars(select(ShotPlanCandidate).where(
            ShotPlanCandidate.project_id == project_id
        )))

    def plans(self, project_id: str) -> list[PlanVersion]:
        return list(self.session.scalars(select(PlanVersion).where(PlanVersion.project_id == project_id)))

    def shots(self, project_id: str) -> list[Shot]:
        return list(self.session.scalars(select(Shot).where(Shot.project_id == project_id)))

    def snapshots(self, project_id: str) -> list[ProductionSnapshot]:
        return list(self.session.scalars(select(ProductionSnapshot).where(
            ProductionSnapshot.project_id == project_id
        )))

    def dag_nodes(self, snapshot_ids: set[str]) -> list[DAGNode]:
        if not snapshot_ids:
            return []
        return list(self.session.scalars(select(DAGNode).where(DAGNode.snapshot_id.in_(snapshot_ids))))

    def work_items(self, project_id: str) -> list[WorkItem]:
        return list(self.session.scalars(select(WorkItem).where(WorkItem.project_id == project_id)))

    def assets(self, project_id: str) -> list[Asset]:
        return list(self.session.scalars(select(Asset).where(Asset.project_id == project_id)))

    def timelines(self, project_id: str) -> list[Timeline]:
        return list(self.session.scalars(select(Timeline).where(Timeline.project_id == project_id)))

    def timeline_items(self, timeline_ids: set[str]) -> list[TimelineItem]:
        if not timeline_ids:
            return []
        return list(self.session.scalars(select(TimelineItem).where(TimelineItem.timeline_id.in_(timeline_ids))))

    def entity_versions(self, project_id: str) -> list[EntityVersion]:
        return list(self.session.scalars(
            select(EntityVersion)
            .where(EntityVersion.project_id == project_id)
            .order_by(EntityVersion.entity_id, EntityVersion.version_number, EntityVersion.id)
        ))

    def entities(self, project_id: str) -> list[Entity]:
        return list(self.session.scalars(
            select(Entity)
            .where(Entity.project_id == project_id)
            .order_by(Entity.entity_type, Entity.display_name, Entity.id)
        ))

    def change_analysis(
        self,
        project_id: str,
        analysis_id: str,
    ) -> DecisionChangeImpactAnalysis | None:
        return self.session.scalar(select(DecisionChangeImpactAnalysis).where(
            DecisionChangeImpactAnalysis.project_id == project_id,
            DecisionChangeImpactAnalysis.id == analysis_id,
        ))

    def change_analysis_history(self, project_id: str) -> list[DecisionChangeImpactAnalysis]:
        return list(self.session.scalars(
            select(DecisionChangeImpactAnalysis)
            .where(DecisionChangeImpactAnalysis.project_id == project_id)
            .order_by(DecisionChangeImpactAnalysis.created_at.desc(), DecisionChangeImpactAnalysis.id.desc())
        ))

    def change_analysis_targets(self, analysis_id: str) -> list[DecisionChangeImpactTarget]:
        return list(self.session.scalars(
            select(DecisionChangeImpactTarget)
            .where(DecisionChangeImpactTarget.analysis_id == analysis_id)
            .order_by(DecisionChangeImpactTarget.record_type, DecisionChangeImpactTarget.record_id)
        ))
