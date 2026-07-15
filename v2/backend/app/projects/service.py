from __future__ import annotations

from sqlalchemy.orm import Session

from ..contracts.project import ProjectCreate
from ..db.models import Project, ProjectEvent, WorkItem
from ..creation.service import ensure_initial_requirement
from ..repositories import EventRepository, ProjectRepository, SqlAlchemyEventRepository, SqlAlchemyProjectRepository


class ProjectConflictError(ValueError):
    pass


def _repositories(
    session: Session,
    projects: ProjectRepository | None = None,
    events: EventRepository | None = None,
) -> tuple[ProjectRepository, EventRepository]:
    return projects or SqlAlchemyProjectRepository(session), events or SqlAlchemyEventRepository(session)


def list_projects(session: Session, projects: ProjectRepository | None = None) -> list[Project]:
    project_repository, _ = _repositories(session, projects=projects)
    return project_repository.list_recent()


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
    event_repository.add(ProjectEvent(project_id=project.id, event_type="project.created", message="项目草稿已创建"))
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
    if project.status != "draft":
        raise ProjectConflictError(f"只有 draft 项目可以确认，当前状态：{project.status}")
    project.status = "confirmed"
    event_repository.add(ProjectEvent(project_id=project.id, event_type="project.confirmed", message="生产合同已由用户确认"))
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
    project.status = "queued"
    project_repository.add_work_item(item)
    project_repository.flush()
    event_repository.add(
        ProjectEvent(
            project_id=project.id,
            event_type="work.queued",
            message="合同验证已加入队列",
            data={"work_item_id": item.id, "kind": item.kind},
        )
    )
    session.commit()
    project_repository.refresh_work_item(item)
    return item
