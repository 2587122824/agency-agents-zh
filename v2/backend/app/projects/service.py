from __future__ import annotations

from sqlalchemy.orm import Session

from ..contracts.project import ArchiveProject, ProjectCreate, RestoreProject
from ..db.models import Project, ProjectEvent, WorkItem, utc_now
from ..creation.service import ensure_initial_requirement
from ..orchestration.project_transitions import ProjectStateTrigger, transition_project
from ..repositories import EventRepository, ProjectRepository, SqlAlchemyCommandRepository, SqlAlchemyEventRepository, SqlAlchemyProjectRepository


class ProjectConflictError(ValueError):
    def __init__(self, message: str, code: str = "PROJECT_CONFLICT"):
        super().__init__(message)
        self.code = code


def _repositories(
    session: Session,
    projects: ProjectRepository | None = None,
    events: EventRepository | None = None,
) -> tuple[ProjectRepository, EventRepository]:
    return projects or SqlAlchemyProjectRepository(session), events or SqlAlchemyEventRepository(session)


def list_projects(
    session: Session,
    *,
    include_archived: bool = False,
    projects: ProjectRepository | None = None,
) -> list[Project]:
    project_repository, _ = _repositories(session, projects=projects)
    return project_repository.list_recent(include_archived=include_archived)


def get_project(session: Session, project_id: str, projects: ProjectRepository | None = None) -> Project | None:
    project_repository, _ = _repositories(session, projects=projects)
    return project_repository.get(project_id, with_workspace=True)


def create_project(
    session: Session,
    payload: ProjectCreate,
    projects: ProjectRepository | None = None,
    events: EventRepository | None = None,
) -> Project:
    project_repository, event_repository = _repositories(session, projects, events)
    project = Project(**payload.model_dump())
    project_repository.add(project)
    project_repository.flush()
    ensure_initial_requirement(session, project)
    event_repository.add(ProjectEvent(project_id=project.id, event_type="project.created.v1", aggregate_type="project", aggregate_id=project.id, actor_type="user", actor_id="local-user", message="项目草稿已创建"))
    session.commit()
    return project_repository.get(project.id, with_workspace=True)  # type: ignore[return-value]


def _archive_replay(session: Session, project: Project, command_id: str, command_type: str) -> Project | None:
    receipt = SqlAlchemyCommandRepository(session).get(project.id, command_id)
    if receipt is None:
        return None
    if receipt.command_type != command_type or receipt.result_type != "project" or receipt.result_id != project.id:
        raise ProjectConflictError(
            "命令编号已用于其他操作，不能重复使用。",
            "COMMAND_ID_REUSED",
        )
    return project


def archive_project(
    session: Session,
    project: Project,
    payload: ArchiveProject,
    projects: ProjectRepository | None = None,
    events: EventRepository | None = None,
) -> Project:
    project_repository, event_repository = _repositories(session, projects, events)
    replay = _archive_replay(session, project, payload.command_id, "project.archive")
    if replay is not None:
        return replay
    if project.archived_at is not None:
        raise ProjectConflictError("项目已经归档。", "PROJECT_ALREADY_ARCHIVED")
    if project_repository.has_active_work(project.id):
        raise ProjectConflictError(
            "项目仍有排队或执行中的制作任务，必须先明确取消后才能归档。",
            "PROJECT_ACTIVE_WORK_EXISTS",
        )
    archived_at = utc_now()
    if not project_repository.update_archive(
        project,
        expected_row_version=payload.expected_row_version,
        archived_at=archived_at,
        archived_by=payload.actor_id,
    ):
        raise ProjectConflictError(
            "项目版本已变化，请刷新后重新确认归档。",
            "PROJECT_ROW_VERSION_CONFLICT",
        )
    SqlAlchemyCommandRepository(session).add(
        project.id,
        payload.command_id,
        "project.archive",
        "project",
        project.id,
    )
    event_repository.add(ProjectEvent(
        project_id=project.id,
        event_type="project.archived.v1",
        aggregate_type="project",
        aggregate_id=project.id,
        actor_type="user",
        actor_id=payload.actor_id,
        message="项目已从默认工作列表归档，生产与审计记录保持不变。",
        data={
            "retained_status": project.status,
            "archived_at": archived_at.isoformat(),
        },
    ))
    session.commit()
    return project_repository.get(project.id, with_workspace=True)  # type: ignore[return-value]


def restore_project(
    session: Session,
    project: Project,
    payload: RestoreProject,
    projects: ProjectRepository | None = None,
    events: EventRepository | None = None,
) -> Project:
    project_repository, event_repository = _repositories(session, projects, events)
    replay = _archive_replay(session, project, payload.command_id, "project.restore")
    if replay is not None:
        return replay
    if project.archived_at is None:
        raise ProjectConflictError("项目未归档，无需恢复。", "PROJECT_NOT_ARCHIVED")
    previous_archived_at = project.archived_at
    if not project_repository.update_archive(
        project,
        expected_row_version=payload.expected_row_version,
        archived_at=None,
        archived_by=None,
    ):
        raise ProjectConflictError(
            "项目版本已变化，请刷新后重新恢复。",
            "PROJECT_ROW_VERSION_CONFLICT",
        )
    SqlAlchemyCommandRepository(session).add(
        project.id,
        payload.command_id,
        "project.restore",
        "project",
        project.id,
    )
    event_repository.add(ProjectEvent(
        project_id=project.id,
        event_type="project.restored.v1",
        aggregate_type="project",
        aggregate_id=project.id,
        actor_type="user",
        actor_id=payload.actor_id,
        message="项目已恢复到默认工作列表，原生产状态保持不变。",
        data={
            "retained_status": project.status,
            "previous_archived_at": previous_archived_at.isoformat(),
        },
    ))
    session.commit()
    return project_repository.get(project.id, with_workspace=True)  # type: ignore[return-value]


def confirm_project(
    session: Session,
    project: Project,
    projects: ProjectRepository | None = None,
    events: EventRepository | None = None,
) -> Project:
    project_repository, event_repository = _repositories(session, projects, events)
    pending = [decision for decision in project.decisions if decision.status == "pending"]
    if pending:
        keys = ", ".join(decision.key for decision in pending)
        raise ProjectConflictError(f"以下决策尚未确认：{keys}")
    if project.status not in {"draft", "collecting_requirements", "planning"}:
        raise ProjectConflictError(f"旧版合同确认不允许项目从 {project.status} 转移")
    transition_project(
        session,
        project,
        ProjectStateTrigger.LEGACY_CONTRACT_CONFIRMED,
        actor_type="user",
        actor_id="local-user",
    )
    event_repository.add(ProjectEvent(project_id=project.id, event_type="project.confirmed.v1", aggregate_type="project", aggregate_id=project.id, actor_type="user", actor_id="local-user", message="生产合同已由用户确认"))
    session.commit()
    return project_repository.get(project.id, with_workspace=True)  # type: ignore[return-value]


def queue_contract_validation(
    session: Session,
    project: Project,
    projects: ProjectRepository | None = None,
    events: EventRepository | None = None,
) -> WorkItem:
    project_repository, event_repository = _repositories(session, projects, events)
    if project.status != "confirmed":
        raise ProjectConflictError("项目必须先明确确认，才能加入验证队列")
    item = WorkItem(project_id=project.id, kind="contract_validation", payload={"project_id": project.id})
    transition_project(
        session,
        project,
        ProjectStateTrigger.LEGACY_VALIDATION_QUEUED,
        actor_type="user",
        actor_id="local-user",
    )
    project_repository.add_work_item(item)
    project_repository.flush()
    event_repository.add(
        ProjectEvent(
            project_id=project.id,
            event_type="work.queued.v1",
            aggregate_type="work_item",
            aggregate_id=item.id,
            actor_type="user",
            actor_id="local-user",
            message="合同验证已加入队列",
            data={"work_item_id": item.id, "kind": item.kind},
        )
    )
    session.commit()
    project_repository.refresh_work_item(item)
    return item
