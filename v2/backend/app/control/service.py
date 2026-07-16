from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from ..db.models import (
    Asset,
    CostEvent,
    CreativeBriefCandidate,
    DAGNode,
    DeliveryAttempt,
    PlanVersion,
    ProductionSnapshot,
    Project,
    ProjectEvent,
    QCFinding,
    QCReport,
    ShotPlanCandidate,
    Timeline,
    WorkAttempt,
    WorkItem,
)
from ..quality.service import asset_read
from ..repositories import ControlRepository, SqlAlchemyControlRepository


STAGE_LABELS = {
    "requirements": "需求确认",
    "planning": "方案规划",
    "production_preparation": "生产准备",
    "production": "生产执行",
    "quality_review": "素材审核",
    "editing": "剪辑",
    "delivery": "最终交付",
    "completed": "已完成",
}


def _active_plan(repository: ControlRepository, project: Project) -> PlanVersion | None:
    return repository.active_plan(project.id)


def _snapshots(
    repository: ControlRepository,
    project: Project,
) -> tuple[ProductionSnapshot | None, ProductionSnapshot | None]:
    rows = repository.snapshots(project.id)
    latest = rows[0] if rows else None
    active = repository.snapshot(project.active_snapshot_id) if project.active_snapshot_id else None
    return active, latest


def _attempts_for_items(
    repository: ControlRepository,
    items: list[WorkItem],
) -> dict[str, list[WorkAttempt]]:
    if not items:
        return {}
    rows = repository.attempts_for_items([item.id for item in items])
    result: dict[str, list[WorkAttempt]] = defaultdict(list)
    for row in rows:
        result[row.work_item_id].append(row)
    return result


def _stage(
    project: Project,
    plan: PlanVersion | None,
    snapshot: ProductionSnapshot | None,
    latest_snapshot: ProductionSnapshot | None,
    timeline: Timeline | None,
    delivery: DeliveryAttempt | None,
    has_planning_candidate: bool,
) -> str:
    if project.delivery_asset_id or (delivery and delivery.status == "verified"):
        return "completed"
    if delivery or (timeline and timeline.status in {"confirmed", "exported"}) or project.status == "delivery_ready":
        return "delivery"
    if timeline or project.status == "editing":
        return "editing"
    authority = snapshot or latest_snapshot
    if project.status == "quality_review" or (authority and authority.status == "execution_completed"):
        return "quality_review"
    if authority and authority.status in {"submitted", "execution_blocked"}:
        return "production"
    if authority or plan:
        return "production_preparation"
    if has_planning_candidate:
        return "planning"
    return "requirements"


def _next_action(
    project: Project,
    stage: str,
    snapshot: ProductionSnapshot | None,
    latest_snapshot: ProductionSnapshot | None,
    timeline: Timeline | None,
    delivery: DeliveryAttempt | None,
    work_counts: Counter,
    asset_counts: Counter,
) -> dict:
    project_path = f"/projects/{project.id}"
    if stage == "completed":
        return {"code": "DOWNLOAD_DELIVERY", "label": "查看并下载最终交付", "path": "/editor"}
    if stage == "delivery":
        if not delivery:
            return {"code": "AUTHORIZE_DELIVERY", "label": "授权确认时间线交付", "path": "/editor", "confirmation_level": "high"}
        if delivery.status == "authorized":
            return {"code": "UPLOAD_DELIVERY", "label": "上传最终 MP4", "path": "/editor"}
        if delivery.status == "output_registered":
            return {"code": "VERIFY_DELIVERY", "label": "验证最终交付文件", "path": "/editor", "confirmation_level": "normal"}
        if delivery.status == "blocked":
            return {"code": "VIEW_DELIVERY_BLOCK", "label": "查看交付阻断证据", "path": "/editor"}
        return {"code": "VIEW_DELIVERY", "label": "查看最终交付", "path": "/editor"}
    if stage == "editing":
        if not timeline:
            return {"code": "CREATE_TIMELINE", "label": "创建时间线候选", "path": "/editor"}
        if timeline.status == "candidate":
            return {"code": "VALIDATE_TIMELINE", "label": "校验时间线候选", "path": "/editor"}
        if timeline.status == "review":
            return {"code": "CONFIRM_TIMELINE", "label": "确认剪辑合同", "path": "/editor", "confirmation_level": "high"}
        return {"code": "VIEW_TIMELINE", "label": "查看剪辑时间线", "path": "/editor"}
    if stage == "quality_review":
        if asset_counts["created"]:
            label = f"验证 {asset_counts['created']} 个已登记素材"
        elif asset_counts["verified"]:
            label = f"执行 {asset_counts['verified']} 个素材 QC"
        elif asset_counts["review_required"]:
            label = f"审核 {asset_counts['review_required']} 个素材"
        else:
            label = "查看素材审核与输出缺口"
        return {"code": "OPEN_QUALITY_REVIEW", "label": label, "path": "/review"}
    authority = snapshot or latest_snapshot
    if stage == "production":
        if work_counts["blocked"]:
            return {"code": "VIEW_PRODUCTION_BLOCKERS", "label": f"查看 {work_counts['blocked']} 个生产阻断", "path": "/production"}
        if work_counts["in_progress"] or work_counts["queued"]:
            return {"code": "MONITOR_PRODUCTION", "label": "查看生产执行进度", "path": "/production"}
        return {"code": "VIEW_PRODUCTION", "label": "查看生产执行", "path": "/production"}
    if stage == "production_preparation":
        if not authority:
            return {"code": "ANALYZE_PRODUCTION_IMPACT", "label": "选择配置并分析生产影响", "path": f"{project_path}/plan"}
        if authority.status == "preparing" and authority.cost_status == "estimated":
            return {"code": "CONFIRM_PRODUCTION_COST", "label": "确认预计费用并锁定快照", "path": f"{project_path}/plan", "confirmation_level": "high"}
        if authority.status == "preparing":
            return {"code": "CONFIGURE_PRICING", "label": "补齐价格目录后创建新快照", "path": f"{project_path}/plan"}
        if authority.status == "locked":
            return {"code": "ACTIVATE_SNAPSHOT", "label": "激活锁定快照", "path": f"{project_path}/plan", "confirmation_level": "high"}
        if authority.status == "active":
            return {"code": "SUBMIT_PRODUCTION", "label": "确认完整 DAG 并提交生产", "path": f"{project_path}/plan", "incurs_production_cost": True, "confirmation_level": "high"}
        return {"code": "OPEN_PRODUCTION_PREPARATION", "label": "查看生产准备", "path": f"{project_path}/plan"}
    if stage == "planning":
        return {"code": "CONTINUE_PLANNING", "label": "继续审核创意与分镜候选", "path": f"{project_path}/plan"}
    return {"code": "CONTINUE_REQUIREMENTS", "label": "继续确认创作需求", "path": project_path}


def _costs(repository: ControlRepository, project: Project) -> list[dict]:
    totals: dict[str, dict[str, float | int]] = defaultdict(lambda: {
        "estimated_confirmed": 0.0,
        "charged_confirmed": 0.0,
        "adjusted_confirmed": 0.0,
        "refunded_confirmed": 0.0,
        "pending_event_count": 0,
    })
    for event in repository.cost_events(project.id):
        row = totals[event.currency]
        if event.status != "confirmed":
            row["pending_event_count"] += 1
            continue
        key = f"{event.kind}_confirmed"
        if key in row:
            row[key] = round(float(row[key]) + event.amount, 6)
    return [{"currency": currency, **values} for currency, values in sorted(totals.items())]


def _blockers(
    repository: ControlRepository,
    session: Session,
    project: Project,
    snapshot: ProductionSnapshot | None,
    items: list[WorkItem],
    attempts: dict[str, list[WorkAttempt]],
    assets: list[Asset],
    delivery: DeliveryAttempt | None,
) -> list[dict]:
    blockers: list[dict] = []
    nodes = {
        node.id: node.node_key
        for node in repository.dag_nodes(snapshot.id)
    } if snapshot else {}
    if snapshot:
        for index, item in enumerate(snapshot.execution_blockers or []):
            blockers.append({
                "source_type": "snapshot",
                "source_id": snapshot.id,
                "code": str(item.get("code", "SNAPSHOT_BLOCKED")),
                "message": str(item.get("message", item.get("code", "Snapshot execution blocker"))),
                "evidence": {"index": index, **item},
                "affected_node_keys": [],
            })
    for item in items:
        if item.status != "blocked":
            continue
        attempt = attempts.get(item.id, [])[-1] if attempts.get(item.id) else None
        blockers.append({
            "source_type": "work_item",
            "source_id": item.id,
            "code": attempt.error_code if attempt and attempt.error_code else "WORK_ITEM_BLOCKED",
            "message": attempt.error_detail if attempt and attempt.error_detail else item.error or "Work item blocked",
            "evidence": {"attempt_id": attempt.id if attempt else None, "dag_node_id": item.dag_node_id},
            "affected_node_keys": [nodes[item.dag_node_id]] if item.dag_node_id in nodes else [],
        })
    for asset in assets:
        report = repository.latest_blocked_report(asset.id)
        if not report:
            continue
        findings = repository.findings(report.id)
        asset_row = asset_read(session, asset)
        for finding in findings:
            blockers.append({
                "source_type": "asset_qc",
                "source_id": asset.id,
                "code": finding.code,
                "message": finding.code,
                "evidence": finding.evidence,
                "affected_node_keys": asset_row["affected_downstream_node_keys"],
            })
    if delivery and delivery.status == "blocked":
        blockers.append({
            "source_type": "delivery",
            "source_id": delivery.id,
            "code": delivery.error_code or "DELIVERY_BLOCKED",
            "message": delivery.error_code or "Delivery blocked",
            "evidence": delivery.error_detail or {},
            "affected_node_keys": [],
        })
    return blockers


def _project_control(session: Session, project: Project, include_detail: bool) -> dict:
    repository = SqlAlchemyControlRepository(session)
    plan = _active_plan(repository, project)
    active_snapshot, latest_snapshot = _snapshots(repository, project)
    authority_snapshot = active_snapshot or latest_snapshot
    snapshot_id = authority_snapshot.id if authority_snapshot else None
    items = repository.work_items(project.id, snapshot_id)
    attempts = _attempts_for_items(repository, items)
    assets = repository.assets(project.id, snapshot_id)
    timeline = repository.latest_timeline(project.id)
    delivery = repository.latest_delivery(project.id)
    has_planning_candidate = repository.has_planning_candidate(project.id)
    work_counts = Counter(item.status for item in items)
    asset_counts = Counter(asset.state for asset in assets)
    blockers = _blockers(repository, session, project, authority_snapshot, items, attempts, assets, delivery)
    stage = _stage(project, plan, active_snapshot, latest_snapshot, timeline, delivery, has_planning_candidate)
    events = repository.events(project.id)
    next_action = _next_action(project, stage, active_snapshot, latest_snapshot, timeline, delivery, work_counts, asset_counts)
    result = {
        "project_id": project.id,
        "title": project.title,
        "core_topic": project.core_topic,
        "duration_seconds": project.duration_seconds,
        "aspect_ratio": project.aspect_ratio,
        "audio_mode": project.audio_mode,
        "persisted_status": project.status,
        "evaluated_stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "active_plan_version": plan.version_number if plan else None,
        "active_snapshot_number": authority_snapshot.snapshot_number if authority_snapshot else None,
        "active_snapshot_status": authority_snapshot.status if authority_snapshot else None,
        "work_counts": dict(work_counts),
        "asset_counts": dict(asset_counts),
        "blocker_count": len(blockers),
        "latest_event_at": events[0].created_at if events else None,
        "updated_at": project.updated_at,
        "next_action": next_action,
    }
    if not include_detail:
        return result
    node_keys = {
        node.id: node.node_key
        for node in repository.dag_nodes(authority_snapshot.id)
    } if authority_snapshot else {}
    routes = []
    for item in items:
        for attempt in attempts.get(item.id, []):
            manifest = attempt.request_manifest or {}
            routes.append({
                "work_item_id": item.id,
                "work_item_status": item.status,
                "node_key": node_keys.get(item.dag_node_id),
                "attempt_id": attempt.id,
                "attempt_number": attempt.attempt_number,
                "attempt_state": attempt.state,
                "provider": attempt.provider,
                "adapter_kind": manifest.get("adapter_kind"),
                "provider_workflow_id": manifest.get("provider_workflow_id"),
                "provider_task_id": attempt.provider_task_id,
                "request_fingerprint": attempt.request_fingerprint,
                "error_code": attempt.error_code,
            })
    result.update({
        "active_plan": None if not plan else {
            "id": plan.id,
            "version_number": plan.version_number,
            "status": plan.status,
            "requirement_version_id": plan.requirement_version_id,
            "contract_schema_version": plan.contract_schema_version,
            "confirmed_at": plan.confirmed_at,
        },
        "active_snapshot": None if not authority_snapshot else {
            "id": authority_snapshot.id,
            "snapshot_number": authority_snapshot.snapshot_number,
            "status": authority_snapshot.status,
            "contract_hash": authority_snapshot.contract_hash,
            "cost_status": authority_snapshot.cost_status,
            "estimated_cost": authority_snapshot.estimated_cost,
            "currency": authority_snapshot.currency,
            "estimated_call_count": authority_snapshot.estimated_call_count,
        },
        "delivery": None if not delivery else {
            "id": delivery.id,
            "status": delivery.status,
            "timeline_id": delivery.timeline_id,
            "request_fingerprint": delivery.request_fingerprint,
            "final_asset_id": delivery.final_asset_id,
            "error_code": delivery.error_code,
        },
        "costs": _costs(repository, project),
        "blockers": blockers,
        "routes": routes,
        "recent_events": [{column.name: getattr(event, column.name) for column in event.__table__.columns if column.name != "project_id"} for event in events],
    })
    return result


def project_controls(session: Session) -> list[dict]:
    projects = SqlAlchemyControlRepository(session).projects()
    return [_project_control(session, project, False) for project in projects]


def project_control_view(session: Session, project: Project) -> dict:
    return _project_control(session, project, True)
