from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..contracts.project import DecisionCreate
from ..db.models import Decision, Project, ProjectEvent, utc_now


class DecisionConflictError(ValueError):
    pass


def add_decision(session: Session, project: Project, payload: DecisionCreate) -> Decision:
    existing = session.scalar(
        select(Decision).where(Decision.project_id == project.id, Decision.key == payload.key)
    )
    if existing:
        raise DecisionConflictError(f"决策键已存在：{payload.key}")
    if project.status != "draft":
        raise DecisionConflictError("只有 draft 项目可以增加决策")
    decision = Decision(project_id=project.id, source="user", **payload.model_dump())
    if decision.status == "resolved":
        decision.resolved_at = utc_now()
    session.add(decision)
    session.flush()
    session.add(
        ProjectEvent(
            project_id=project.id,
            event_type="decision.created",
            message=f"已登记决策：{decision.label}",
            data={"decision_id": decision.id, "key": decision.key, "status": decision.status},
        )
    )
    session.commit()
    session.refresh(decision)
    return decision


def resolve_decision(session: Session, project: Project, decision_id: str, value: object) -> Decision:
    decision = session.scalar(
        select(Decision).where(Decision.id == decision_id, Decision.project_id == project.id)
    )
    if not decision:
        raise LookupError(decision_id)
    if project.status != "draft":
        raise DecisionConflictError("项目确认后不能覆盖决策")
    if decision.status == "resolved":
        raise DecisionConflictError("该决策已经确认，决策账本不允许覆盖历史值")
    decision.value = value
    decision.status = "resolved"
    decision.resolved_at = utc_now()
    session.add(
        ProjectEvent(
            project_id=project.id,
            event_type="decision.resolved",
            message=f"已确认决策：{decision.label}",
            data={"decision_id": decision.id, "key": decision.key},
        )
    )
    session.commit()
    session.refresh(decision)
    return decision
