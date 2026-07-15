from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..contracts.project import ProjectCreate
from ..db.models import Project, ProjectEvent, WorkItem


class ProjectConflictError(ValueError):
    pass


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.updated_at.desc())))


def get_project(session: Session, project_id: str) -> Project | None:
    statement = (
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.decisions), selectinload(Project.work_items))
    )
    return session.scalar(statement)


def create_project(session: Session, payload: ProjectCreate) -> Project:
    project = Project(**payload.model_dump())
    session.add(project)
    session.flush()
    session.add(ProjectEvent(project_id=project.id, event_type="project.created", message="项目草稿已创建"))
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def confirm_project(session: Session, project: Project) -> Project:
    pending = [decision for decision in project.decisions if decision.status == "pending"]
    if pending:
        keys = ", ".join(decision.key for decision in pending)
        raise ProjectConflictError(f"以下决策尚未确认：{keys}")
    if project.status != "draft":
        raise ProjectConflictError(f"只有 draft 项目可以确认，当前状态：{project.status}")
    project.status = "confirmed"
    session.add(ProjectEvent(project_id=project.id, event_type="project.confirmed", message="生产合同已由用户确认"))
    session.commit()
    return get_project(session, project.id)  # type: ignore[return-value]


def queue_contract_validation(session: Session, project: Project) -> WorkItem:
    if project.status != "confirmed":
        raise ProjectConflictError("项目必须先明确确认，才能加入验证队列")
    item = WorkItem(project_id=project.id, kind="contract_validation", payload={"project_id": project.id})
    project.status = "queued"
    session.add(item)
    session.flush()
    session.add(
        ProjectEvent(
            project_id=project.id,
            event_type="work.queued",
            message="合同验证已加入队列",
            data={"work_item_id": item.id, "kind": item.kind},
        )
    )
    session.commit()
    session.refresh(item)
    return item
