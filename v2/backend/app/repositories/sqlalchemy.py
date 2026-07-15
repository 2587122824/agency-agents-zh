from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db.models import Project, ProjectEvent, WorkItem


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
