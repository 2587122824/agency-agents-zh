from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db.models import (
    AgentInputManifest,
    AgentRun,
    Attachment,
    AttachmentBinding,
    ClarificationRequest,
    CommandReceipt,
    CreativeBriefCandidate,
    Decision,
    Entity,
    EntityVersion,
    Message,
    Project,
    ProjectEvent,
    RequirementCandidate,
    RequirementVersion,
    PlanVersion,
    Shot,
    ShotPlanCandidate,
    WorkItem,
)
from .contracts import CreationRecord, ModelT, PlanningRecord


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
