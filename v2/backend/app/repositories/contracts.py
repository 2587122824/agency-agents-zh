from __future__ import annotations

from typing import Protocol

from ..db.models import CommandReceipt, Decision, Project, ProjectEvent, WorkItem


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
