from __future__ import annotations

from typing import Protocol, TypeVar

from ..db.models import (
    AgentInputManifest,
    AgentRun,
    Attachment,
    AttachmentBinding,
    ClarificationRequest,
    CommandReceipt,
    Decision,
    Entity,
    EntityVersion,
    Message,
    Project,
    ProjectEvent,
    RequirementCandidate,
    RequirementVersion,
    WorkItem,
)


ModelT = TypeVar("ModelT")
CreationRecord = (
    AgentInputManifest
    | AgentRun
    | Attachment
    | AttachmentBinding
    | ClarificationRequest
    | Entity
    | EntityVersion
    | Message
    | RequirementCandidate
    | RequirementVersion
)


class ProjectRepository(Protocol):
    def list_recent(self) -> list[Project]: ...

    def get(self, project_id: str, *, with_workspace: bool = False) -> Project | None: ...

    def add(self, project: Project) -> None: ...

    def add_work_item(self, item: WorkItem) -> None: ...

    def flush(self) -> None: ...

    def refresh_work_item(self, item: WorkItem) -> None: ...


class EventRepository(Protocol):
    def add(self, event: ProjectEvent) -> None: ...

    def list_after(self, project_id: str, sequence: int, *, limit: int = 100) -> list[ProjectEvent]: ...


class DecisionRepository(Protocol):
    def get_by_key(self, project_id: str, key: str) -> Decision | None: ...

    def get_for_project(self, project_id: str, decision_id: str) -> Decision | None: ...

    def add(self, decision: Decision) -> None: ...

    def flush(self) -> None: ...

    def refresh(self, decision: Decision) -> None: ...


class CommandRepository(Protocol):
    def get(self, project_id: str, command_id: str) -> CommandReceipt | None: ...

    def add(
        self,
        project_id: str,
        command_id: str,
        command_type: str,
        result_type: str,
        result_id: str,
    ) -> CommandReceipt: ...

    def get_result(self, model_type: type[ModelT], result_id: str) -> ModelT | None: ...


class CreationRepository(Protocol):
    def add(self, record: CreationRecord) -> None: ...

    def flush(self) -> None: ...

    def active_requirement(self, project_id: str) -> RequirementVersion | None: ...

    def requirement_candidate(self, candidate_id: str) -> RequirementCandidate | None: ...

    def agent_run(self, run_id: str) -> AgentRun | None: ...

    def agent_manifest(self, manifest_id: str) -> AgentInputManifest | None: ...

    def pending_clarifications(self, project_id: str) -> list[ClarificationRequest]: ...

    def message(self, message_id: str) -> Message | None: ...

    def reviewable_candidates(
        self,
        project_id: str,
        *,
        exclude_id: str | None = None,
    ) -> list[RequirementCandidate]: ...

    def manifest_messages(self, project_id: str) -> list[Message]: ...

    def confirmed_bindings(self, project_id: str) -> list[AttachmentBinding]: ...

    def clarification(self, clarification_id: str) -> ClarificationRequest | None: ...

    def attachment(self, attachment_id: str) -> Attachment | None: ...

    def entity(self, entity_id: str) -> Entity | None: ...

    def entity_version(self, version_id: str) -> EntityVersion | None: ...

    def active_entity_version(self, entity_id: str) -> EntityVersion | None: ...

    def view_messages(self, project_id: str) -> list[Message]: ...

    def candidate_history(self, project_id: str) -> list[RequirementCandidate]: ...

    def agent_runs(self, project_id: str) -> list[AgentRun]: ...

    def attachments(self, project_id: str) -> list[Attachment]: ...

    def bindings(self, project_id: str) -> list[AttachmentBinding]: ...

    def active_pending_clarifications(
        self,
        project_id: str,
        requirement_version_id: str,
    ) -> list[ClarificationRequest]: ...
