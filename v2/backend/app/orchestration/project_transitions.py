from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from sqlalchemy.orm import Session

from ..db.models import Project, ProjectEvent, utc_now
from ..repositories import (
    EventRepository,
    ProjectStateRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyProjectStateRepository,
)


class ProjectStateTrigger(str, Enum):
    MESSAGE_ADDED = "message_added"
    DECISION_REQUESTED = "decision_requested"
    DECISIONS_RESOLVED = "decisions_resolved"
    DECISIONS_RESOLVED_REQUIREMENTS_READY = "decisions_resolved_requirements_ready"
    REQUIREMENT_CONFIRMED = "requirement_confirmed"
    BRIEF_CANDIDATE_CREATED = "brief_candidate_created"
    BRIEF_ACCEPTED = "brief_accepted"
    BRIEF_REJECTED = "brief_rejected"
    SHOT_CANDIDATE_CREATED = "shot_candidate_created"
    SHOT_CANDIDATE_REVISED = "shot_candidate_revised"
    SHOT_PLAN_ACCEPTED = "shot_plan_accepted"
    SHOT_PLAN_REJECTED = "shot_plan_rejected"
    SNAPSHOT_PREPARED = "snapshot_prepared"
    SNAPSHOT_LOCKED = "snapshot_locked"
    SNAPSHOT_ACTIVATED = "snapshot_activated"
    PRODUCTION_SUBMITTED = "production_submitted"
    PRODUCTION_PROGRESS = "production_progress"
    PRODUCTION_SETTLED = "production_settled"
    QUALITY_RECORDED = "quality_recorded"
    QUALITY_STAGE_APPROVED = "quality_stage_approved"
    TIMELINE_CANDIDATE_CREATED = "timeline_candidate_created"
    TIMELINE_CONFIRMED = "timeline_confirmed"
    DELIVERY_VERIFIED = "delivery_verified"
    LEGACY_CONTRACT_CONFIRMED = "legacy_contract_confirmed"
    LEGACY_VALIDATION_QUEUED = "legacy_validation_queued"
    LEGACY_VALIDATION_COMPLETED = "legacy_validation_completed"


TRANSITION_RULES: Mapping[ProjectStateTrigger, Mapping[str, str]] = {
    ProjectStateTrigger.MESSAGE_ADDED: {
        "draft": "collecting_requirements",
        "collecting_requirements": "collecting_requirements",
        "decision_required": "decision_required",
    },
    ProjectStateTrigger.DECISION_REQUESTED: {
        "draft": "decision_required",
        "collecting_requirements": "decision_required",
        "decision_required": "decision_required",
    },
    ProjectStateTrigger.DECISIONS_RESOLVED: {
        "decision_required": "collecting_requirements",
        "collecting_requirements": "collecting_requirements",
    },
    ProjectStateTrigger.DECISIONS_RESOLVED_REQUIREMENTS_READY: {
        "decision_required": "planning",
        "collecting_requirements": "planning",
    },
    ProjectStateTrigger.REQUIREMENT_CONFIRMED: {
        "draft": "planning",
        "collecting_requirements": "planning",
        "decision_required": "planning",
        "planning": "planning",
        "plan_review": "planning",
        "contract_ready": "planning",
    },
    ProjectStateTrigger.BRIEF_CANDIDATE_CREATED: {
        "draft": "plan_review",
        "collecting_requirements": "plan_review",
        "planning": "plan_review",
        "plan_review": "plan_review",
    },
    ProjectStateTrigger.BRIEF_ACCEPTED: {
        "plan_review": "planning",
    },
    ProjectStateTrigger.BRIEF_REJECTED: {
        "plan_review": "collecting_requirements",
    },
    ProjectStateTrigger.SHOT_CANDIDATE_CREATED: {
        "planning": "plan_review",
    },
    ProjectStateTrigger.SHOT_CANDIDATE_REVISED: {
        "plan_review": "plan_review",
    },
    ProjectStateTrigger.SHOT_PLAN_ACCEPTED: {
        "plan_review": "contract_ready",
    },
    ProjectStateTrigger.SHOT_PLAN_REJECTED: {
        "plan_review": "planning",
    },
    ProjectStateTrigger.SNAPSHOT_PREPARED: {
        "contract_ready": "contract_ready",
    },
    ProjectStateTrigger.SNAPSHOT_LOCKED: {
        "contract_ready": "production_ready",
    },
    ProjectStateTrigger.SNAPSHOT_ACTIVATED: {
        "production_ready": "production_ready",
    },
    ProjectStateTrigger.PRODUCTION_SUBMITTED: {
        "production_ready": "producing",
    },
    ProjectStateTrigger.PRODUCTION_PROGRESS: {
        "producing": "producing",
    },
    ProjectStateTrigger.PRODUCTION_SETTLED: {
        "producing": "quality_review",
    },
    ProjectStateTrigger.QUALITY_RECORDED: {
        "producing": "quality_review",
        "quality_review": "quality_review",
        "blocked": "blocked",
    },
    ProjectStateTrigger.QUALITY_STAGE_APPROVED: {
        "quality_review": "editing",
    },
    ProjectStateTrigger.TIMELINE_CANDIDATE_CREATED: {
        "editing": "editing",
        "delivery_ready": "editing",
    },
    ProjectStateTrigger.TIMELINE_CONFIRMED: {
        "editing": "delivery_ready",
    },
    ProjectStateTrigger.DELIVERY_VERIFIED: {
        "delivery_ready": "completed",
    },
    ProjectStateTrigger.LEGACY_CONTRACT_CONFIRMED: {
        "draft": "confirmed",
        "collecting_requirements": "confirmed",
        "planning": "confirmed",
    },
    ProjectStateTrigger.LEGACY_VALIDATION_QUEUED: {
        "confirmed": "queued",
    },
    ProjectStateTrigger.LEGACY_VALIDATION_COMPLETED: {
        "queued": "review_required",
    },
}


TERMINAL_STATES = {"completed", "cancelled"}


class ProjectStateConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProjectTransitionResult:
    project_id: str
    from_state: str
    to_state: str
    trigger: str
    changed: bool
    row_version: int


def _repositories(
    session: Session,
    states: ProjectStateRepository | None,
    events: EventRepository | None,
) -> tuple[ProjectStateRepository, EventRepository]:
    return states or SqlAlchemyProjectStateRepository(session), events or SqlAlchemyEventRepository(session)


def _result(project: Project, from_state: str, trigger: str, changed: bool) -> ProjectTransitionResult:
    return ProjectTransitionResult(
        project_id=project.id,
        from_state=from_state,
        to_state=project.status,
        trigger=trigger,
        changed=changed,
        row_version=project.row_version,
    )


def transition_project(
    session: Session,
    project: Project,
    trigger: ProjectStateTrigger,
    *,
    actor_type: str,
    actor_id: str,
    event_data: dict | None = None,
    states: ProjectStateRepository | None = None,
    events: EventRepository | None = None,
) -> ProjectTransitionResult:
    rules = TRANSITION_RULES[trigger]
    from_state = project.status
    target = rules.get(from_state)
    if target is None:
        raise ProjectStateConflictError(
            "PROJECT_STATE_TRANSITION_NOT_ALLOWED",
            f"触发器 {trigger.value} 不允许项目从 {from_state} 转移。",
        )
    if target == from_state:
        return _result(project, from_state, trigger.value, False)
    state_repository, event_repository = _repositories(session, states, events)
    changed_at = utc_now()
    if not state_repository.transition_state(
        project,
        expected_status=from_state,
        expected_row_version=project.row_version,
        target_status=target,
        changed_at=changed_at,
        actor_type=actor_type,
        actor_id=actor_id,
        trigger=trigger.value,
        reason_code=None,
        blocked_from_state=None,
        responsible_aggregate_type=None,
        responsible_aggregate_id=None,
        allowed_commands=[],
        blocked_at=None,
    ):
        raise ProjectStateConflictError(
            "PROJECT_STATE_VERSION_CONFLICT",
            "项目状态已被其他命令修改，请刷新后重试。",
        )
    event_repository.add(ProjectEvent(
        project_id=project.id,
        event_type="project.state_changed.v1",
        aggregate_type="project",
        aggregate_id=project.id,
        actor_type=actor_type,
        actor_id=actor_id,
        message=f"Project state changed from {from_state} to {target}.",
        data={
            "from_state": from_state,
            "to_state": target,
            "trigger": trigger.value,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "row_version": project.row_version,
            **(event_data or {}),
        },
    ))
    return _result(project, from_state, trigger.value, True)


def block_project(
    session: Session,
    project: Project,
    *,
    reason_code: str,
    responsible_aggregate_type: str,
    responsible_aggregate_id: str,
    actor_type: str,
    actor_id: str,
    allowed_commands: tuple[str, ...] = (),
    event_data: dict | None = None,
    states: ProjectStateRepository | None = None,
    events: EventRepository | None = None,
) -> ProjectTransitionResult:
    if not reason_code or not responsible_aggregate_type or not responsible_aggregate_id:
        raise ProjectStateConflictError(
            "PROJECT_BLOCK_EVIDENCE_REQUIRED",
            "项目阻断必须包含原因码和责任聚合。",
        )
    state_repository, event_repository = _repositories(session, states, events)
    from_state = project.status
    evidence = {
        "reason_code": reason_code,
        "responsible_aggregate_type": responsible_aggregate_type,
        "responsible_aggregate_id": responsible_aggregate_id,
        "allowed_commands": list(allowed_commands),
        "actor_type": actor_type,
        "actor_id": actor_id,
        **(event_data or {}),
    }
    if from_state == "blocked":
        event_repository.add(ProjectEvent(
            project_id=project.id,
            event_type="project.block_diagnostic.v1",
            aggregate_type="project",
            aggregate_id=project.id,
            actor_type=actor_type,
            actor_id=actor_id,
            message="Additional project block evidence was recorded without replacing the original block.",
            data=evidence,
        ))
        return _result(project, from_state, "block_project", False)
    if from_state in TERMINAL_STATES:
        raise ProjectStateConflictError(
            "PROJECT_TERMINAL_STATE",
            f"终态项目 {from_state} 不能进入 blocked。",
        )
    changed_at = utc_now()
    if not state_repository.transition_state(
        project,
        expected_status=from_state,
        expected_row_version=project.row_version,
        target_status="blocked",
        changed_at=changed_at,
        actor_type=actor_type,
        actor_id=actor_id,
        trigger="block_project",
        reason_code=reason_code,
        blocked_from_state=from_state,
        responsible_aggregate_type=responsible_aggregate_type,
        responsible_aggregate_id=responsible_aggregate_id,
        allowed_commands=list(allowed_commands),
        blocked_at=changed_at,
    ):
        raise ProjectStateConflictError(
            "PROJECT_STATE_VERSION_CONFLICT",
            "项目状态已被其他命令修改，请刷新后重试。",
        )
    event_repository.add(ProjectEvent(
        project_id=project.id,
        event_type="project.blocked.v1",
        aggregate_type="project",
        aggregate_id=project.id,
        actor_type=actor_type,
        actor_id=actor_id,
        message=f"Project was blocked from {from_state} by {reason_code}.",
        data={
            "from_state": from_state,
            "to_state": "blocked",
            "trigger": "block_project",
            "row_version": project.row_version,
            **evidence,
        },
    ))
    return _result(project, from_state, "block_project", True)
