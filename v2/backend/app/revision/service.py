from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Asset,
    AssetRevisionRequest,
    DAGNode,
    DependencyEdge,
    PlanVersion,
    ProductionSnapshot,
    Project,
    ProjectEvent,
    Shot,
    ShotPlanCandidate,
    utc_now,
)
from ..repositories import SqlAlchemyCommandRepository, SqlAlchemyEventRepository
from .contracts import CancelAssetRevisionRequest, CreateAssetRevisionRequest


class RevisionConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RevisionNotFoundError(ValueError):
    pass


def _request_read(item: AssetRevisionRequest) -> dict:
    return {
        column.name: getattr(item, column.name)
        for column in item.__table__.columns
        if column.name != "project_id"
    }


def _next_action(item: AssetRevisionRequest) -> dict:
    if item.issue_scope == "storyboard":
        path = f"/projects/{item.project_id}/plan?revisionRequest={item.id}"
        label = "调整对应分镜"
    elif item.issue_scope == "production":
        path = f"/production?project={item.project_id}&revisionRequest={item.id}"
        label = "查看制作问题"
    else:
        path = f"/editor?project={item.project_id}&revisionRequest={item.id}"
        label = "调整剪辑取舍"
    return {"path": path, "label": label, "draft_candidate_id": item.draft_candidate_id}


def _result(item: AssetRevisionRequest) -> dict:
    return {"request": _request_read(item), "next_action": _next_action(item)}


def _shot_contract(shot: Shot) -> dict:
    excluded = {"id", "project_id", "plan_version_id", "created_at"}
    return {column.name: getattr(shot, column.name) for column in shot.__table__.columns if column.name not in excluded}


def _downstream_node_keys(session: Session, node: DAGNode) -> list[str]:
    edges = list(session.scalars(select(DependencyEdge).where(DependencyEdge.snapshot_id == node.snapshot_id)))
    children: dict[str, set[str]] = {}
    for edge in edges:
        children.setdefault(edge.parent_node_id, set()).add(edge.child_node_id)
    seen: set[str] = set()
    pending = list(children.get(node.id, set()))
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        pending.extend(children.get(node_id, set()))
    if not seen:
        return []
    return sorted(session.scalars(select(DAGNode.node_key).where(DAGNode.id.in_(seen))).all())


def _existing_receipt(
    session: Session,
    project_id: str,
    command_id: str,
    command_type: str = "asset.request_revision",
) -> AssetRevisionRequest | None:
    receipt = SqlAlchemyCommandRepository(session).get(project_id, command_id)
    if not receipt:
        return None
    if receipt.command_type != command_type:
        raise RevisionConflictError("COMMAND_ID_REUSED", f"Command ID is already used by {receipt.command_type}.")
    return session.get(AssetRevisionRequest, receipt.result_id)


def create_asset_revision_request(
    session: Session,
    project: Project,
    asset_id: str,
    payload: CreateAssetRevisionRequest,
) -> dict:
    replay = _existing_receipt(session, project.id, payload.command_id)
    if replay:
        return _result(replay)
    asset = session.get(Asset, asset_id)
    if not asset or asset.project_id != project.id:
        raise RevisionNotFoundError("Asset not found")
    if asset.row_version != payload.expected_asset_row_version:
        raise RevisionConflictError("ASSET_ROW_VERSION_MISMATCH", "素材已变化，请刷新后再登记调整意见。")
    snapshot = session.get(ProductionSnapshot, asset.snapshot_id)
    if not snapshot or snapshot.project_id != project.id:
        raise RevisionConflictError("ASSET_SNAPSHOT_MISSING", "素材没有可追溯的制作快照。")
    plan = session.get(PlanVersion, snapshot.plan_version_id)
    if not plan or plan.project_id != project.id:
        raise RevisionConflictError("ASSET_PLAN_MISSING", "素材没有可追溯的创作方案。")
    node = session.get(DAGNode, asset.dag_node_id) if asset.dag_node_id else None
    shot = session.get(Shot, node.shot_id) if node and node.shot_id else None
    if shot and shot.plan_version_id != plan.id:
        raise RevisionConflictError("ASSET_SHOT_PLAN_MISMATCH", "素材绑定的分镜不属于其制作方案。")
    if payload.issue_scope == "storyboard" and not shot:
        raise RevisionConflictError("STORYBOARD_REVISION_SHOT_REQUIRED", "该素材没有绑定明确分镜，不能返回分镜修改。")
    if payload.issue_scope == "storyboard" and not plan.is_active:
        raise RevisionConflictError("STORYBOARD_REVISION_PLAN_NOT_ACTIVE", "该素材属于历史创作方案，不能直接覆盖当前分镜方案。")
    if payload.issue_scope == "storyboard":
        open_request = session.scalar(select(AssetRevisionRequest).where(
            AssetRevisionRequest.project_id == project.id,
            AssetRevisionRequest.issue_scope == "storyboard",
            AssetRevisionRequest.status.in_(("draft_created", "candidate_created")),
        ))
        if open_request:
            raise RevisionConflictError(
                "STORYBOARD_REVISION_ALREADY_OPEN",
                f"项目已有未完成的分镜调整请求 {open_request.id}，请先处理该请求。",
            )
    request = AssetRevisionRequest(
        project_id=project.id,
        asset_id=asset.id,
        snapshot_id=snapshot.id,
        plan_version_id=plan.id,
        shot_id=shot.id if shot else None,
        shot_code=shot.shot_code if shot else None,
        issue_scope=payload.issue_scope,
        issue_code=payload.issue_code,
        rationale=payload.rationale,
        status="recorded",
        source_asset_state=asset.state,
        source_asset_row_version=asset.row_version,
        affected_downstream_node_keys=_downstream_node_keys(session, node) if node else [],
        created_by=payload.actor_id,
    )
    session.add(request)
    session.flush()
    if payload.issue_scope == "storyboard":
        source_candidate = session.get(ShotPlanCandidate, plan.shot_plan_candidate_id)
        if not source_candidate:
            raise RevisionConflictError("SOURCE_SHOT_PLAN_MISSING", "创作方案缺少来源分镜候选。")
        shots = list(session.scalars(
            select(Shot).where(Shot.plan_version_id == plan.id).order_by(Shot.sequence_number)
        ))
        if not shots:
            raise RevisionConflictError("SOURCE_SHOTS_MISSING", "创作方案没有可修订的分镜。")
        draft = ShotPlanCandidate(
            project_id=project.id,
            requirement_version_id=plan.requirement_version_id,
            creative_brief_candidate_id=source_candidate.creative_brief_candidate_id,
            agent_run_id=None,
            supersedes_candidate_id=source_candidate.id,
            revision_number=source_candidate.revision_number + 1,
            source="asset_feedback_draft",
            status="revision_draft",
            shots=[_shot_contract(item) for item in shots],
            validation_errors=[],
            created_by=payload.actor_id,
        )
        session.add(draft)
        session.flush()
        request.status = "draft_created"
        request.draft_candidate_id = draft.id
    SqlAlchemyCommandRepository(session).add(
        project.id, payload.command_id, "asset.request_revision", "asset_revision_request", request.id
    )
    SqlAlchemyEventRepository(session).add(ProjectEvent(
        project_id=project.id,
        snapshot_id=snapshot.id,
        event_type="asset.revision_requested.v1",
        aggregate_type="asset_revision_request",
        aggregate_id=request.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="User classified an asset issue and requested an explicit revision path.",
        data={
            "asset_id": asset.id,
            "issue_scope": payload.issue_scope,
            "issue_code": payload.issue_code,
            "shot_id": request.shot_id,
            "shot_code": request.shot_code,
            "draft_candidate_id": request.draft_candidate_id,
            "affected_downstream_node_keys": request.affected_downstream_node_keys,
        },
    ))
    session.commit()
    return _result(request)


def get_asset_revision_request(session: Session, project: Project, request_id: str) -> dict:
    item = session.get(AssetRevisionRequest, request_id)
    if not item or item.project_id != project.id:
        raise RevisionNotFoundError("Asset revision request not found")
    return _request_read(item)


def cancel_asset_revision_request(
    session: Session,
    project: Project,
    request_id: str,
    payload: CancelAssetRevisionRequest,
) -> dict:
    replay = _existing_receipt(session, project.id, payload.command_id, "asset_revision.cancel")
    if replay:
        return _request_read(replay)
    item = session.get(AssetRevisionRequest, request_id)
    if not item or item.project_id != project.id:
        raise RevisionNotFoundError("Asset revision request not found")
    if item.status not in {"draft_created", "candidate_created"}:
        raise RevisionConflictError("ASSET_REVISION_NOT_CANCELLABLE", f"调整请求状态为 {item.status}，不能取消。")
    candidate_ids = [candidate_id for candidate_id in (item.draft_candidate_id, item.resulting_candidate_id) if candidate_id]
    now = utc_now()
    for candidate_id in candidate_ids:
        candidate = session.get(ShotPlanCandidate, candidate_id)
        if candidate and candidate.status in {"revision_draft", "awaiting_review", "rejected"}:
            candidate.status = "cancelled"
            candidate.row_version += 1
            candidate.decided_at = now
    if item.resolved_at is None:
        item.resolved_at = now
    item.status = "cancelled"
    SqlAlchemyCommandRepository(session).add(
        project.id, payload.command_id, "asset_revision.cancel", "asset_revision_request", item.id
    )
    SqlAlchemyEventRepository(session).add(ProjectEvent(
        project_id=project.id,
        snapshot_id=item.snapshot_id,
        event_type="asset.revision_cancelled.v1",
        aggregate_type="asset_revision_request",
        aggregate_id=item.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="User cancelled an open asset revision request.",
        data={"reason": payload.reason, "draft_candidate_id": item.draft_candidate_id, "resulting_candidate_id": item.resulting_candidate_id},
    ))
    session.commit()
    return _request_read(item)
