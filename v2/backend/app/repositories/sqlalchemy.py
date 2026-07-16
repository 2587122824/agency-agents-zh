from __future__ import annotations

from sqlalchemy import func, select
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
    ConfigurationReference,
    CostEvent,
    CreativeBriefCandidate,
    DAGNode,
    Decision,
    DependencyEdge,
    Entity,
    EntityVersion,
    Message,
    Project,
    ProjectEvent,
    PricingCatalogVersion,
    PricingRule,
    ProductionConfigVersion,
    ProductionImpactAnalysis,
    ProductionSnapshot,
    ProviderConfigVersion,
    QCFinding,
    QCReport,
    RequirementCandidate,
    RequirementVersion,
    PlanVersion,
    Shot,
    ShotPlanCandidate,
    SnapshotEntityVersion,
    StoragePolicyVersion,
    VideoSpecVersion,
    WorkflowSlotVersion,
    WorkAttempt,
    WorkItem,
)
from .contracts import CreationRecord, ModelT, PlanningRecord, ProductionRecord, QualityRecord


class SqlAlchemyProjectRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_recent(self) -> list[Project]:
        return list(self.session.scalars(select(Project).order_by(Project.updated_at.desc())))

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


class SqlAlchemyEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: ProjectEvent) -> None:
        self.session.add(event)

    def list_after(self, project_id: str, sequence: int, *, limit: int = 100) -> list[ProjectEvent]:
        statement = (
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project_id, ProjectEvent.sequence > sequence)
            .order_by(ProjectEvent.sequence)
            .limit(limit)
        )
        return list(self.session.scalars(statement))


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

    def requirement_candidate(self, candidate_id: str) -> RequirementCandidate | None:
        return self.session.get(RequirementCandidate, candidate_id)

    def agent_run(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)

    def agent_manifest(self, manifest_id: str) -> AgentInputManifest | None:
        return self.session.get(AgentInputManifest, manifest_id)

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
        return list(self.session.scalars(statement))

    def manifest_messages(self, project_id: str) -> list[Message]:
        return list(self.session.scalars(
            select(Message).where(Message.project_id == project_id).order_by(Message.created_at, Message.id)
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

    def active_entity_version(self, entity_id: str) -> EntityVersion | None:
        return self.session.scalar(select(EntityVersion).where(
            EntityVersion.entity_id == entity_id,
            EntityVersion.is_active.is_(True),
        ))

    def view_messages(self, project_id: str) -> list[Message]:
        return list(self.session.scalars(
            select(Message).where(Message.project_id == project_id).order_by(Message.created_at)
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
            .order_by(ProductionConfigVersion.published_at.desc())
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
