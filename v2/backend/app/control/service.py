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
from ..orchestration.project_state import (
    ProjectStateFacts,
    SnapshotStateFact,
    evaluate_project_state,
)
from ..quality.service import asset_read
from ..repositories import ControlRepository, SqlAlchemyControlRepository


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


def _event_read(event: ProjectEvent) -> dict:
    return {
        "sequence": event.project_sequence,
        "event_id": event.event_id,
        "snapshot_id": event.snapshot_id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "causation_id": event.causation_id,
        "correlation_id": event.correlation_id,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "schema_version": event.schema_version,
        "message": event.message,
        "data": event.data,
        "created_at": event.created_at,
    }


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
    evaluation = evaluate_project_state(ProjectStateFacts(
        project_id=project.id,
        persisted_status=project.status,
        has_delivery_asset=project.delivery_asset_id is not None,
        delivery_status=delivery.status if delivery else None,
        timeline_status=timeline.status if timeline else None,
        active_snapshot=None if not active_snapshot else SnapshotStateFact(
            status=active_snapshot.status,
            cost_status=active_snapshot.cost_status,
        ),
        latest_snapshot=None if not latest_snapshot else SnapshotStateFact(
            status=latest_snapshot.status,
            cost_status=latest_snapshot.cost_status,
        ),
        has_active_plan=plan is not None,
        has_planning_candidate=has_planning_candidate,
        work_counts=work_counts,
        asset_counts=asset_counts,
    ))
    stage = evaluation.stage
    events = repository.events(project.id)
    result = {
        "project_id": project.id,
        "title": project.title,
        "core_topic": project.core_topic,
        "duration_seconds": project.duration_seconds,
        "aspect_ratio": project.aspect_ratio,
        "audio_mode": project.audio_mode,
        "persisted_status": project.status,
        "state_row_version": project.row_version,
        "state_changed_at": project.state_changed_at,
        "state_actor_type": project.state_actor_type,
        "state_changed_by": project.state_changed_by,
        "state_trigger": project.state_trigger,
        "state_reason_code": project.state_reason_code,
        "blocked_from_state": project.blocked_from_state,
        "blocked_responsible_aggregate_type": project.blocked_responsible_aggregate_type,
        "blocked_responsible_aggregate_id": project.blocked_responsible_aggregate_id,
        "blocked_allowed_commands": project.blocked_allowed_commands,
        "blocked_at": project.blocked_at,
        "evaluated_stage": stage,
        "stage_label": evaluation.stage_label,
        "active_plan_version": plan.version_number if plan else None,
        "active_snapshot_number": authority_snapshot.snapshot_number if authority_snapshot else None,
        "active_snapshot_status": authority_snapshot.status if authority_snapshot else None,
        "work_counts": dict(work_counts),
        "asset_counts": dict(asset_counts),
        "blocker_count": len(blockers),
        "latest_event_at": events[0].created_at if events else None,
        "updated_at": project.updated_at,
        "next_action": evaluation.next_action.as_dict(),
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
        "recent_events": [_event_read(event) for event in events],
    })
    return result


def project_controls(session: Session) -> list[dict]:
    projects = SqlAlchemyControlRepository(session).projects()
    return [_project_control(session, project, False) for project in projects]


def project_control_view(session: Session, project: Project) -> dict:
    return _project_control(session, project, True)


def project_audit_ledger(
    session: Session,
    project: Project,
    *,
    before_sequence: int | None,
    limit: int,
) -> dict:
    repository = SqlAlchemyControlRepository(session)
    event_rows = repository.events(
        project.id,
        limit=limit + 1,
        before_sequence=before_sequence,
    )
    has_more = len(event_rows) > limit
    page = event_rows[:limit]
    cost_rows = repository.cost_events(project.id)
    return {
        "project_id": project.id,
        "project_title": project.title,
        "event_limit": limit,
        "before_sequence": before_sequence,
        "has_more_events": has_more,
        "next_before_sequence": page[-1].project_sequence if has_more and page else None,
        "events": [_event_read(event) for event in page],
        "cost_summaries": _costs(repository, project),
        "cost_events": [{
            "id": event.id,
            "snapshot_id": event.snapshot_id,
            "work_attempt_id": event.work_attempt_id,
            "provider": event.provider,
            "provider_operation": event.provider_operation,
            "kind": event.kind,
            "amount": event.amount,
            "currency": event.currency,
            "provider_reference": event.provider_reference,
            "status": event.status,
            "occurred_at": event.occurred_at,
        } for event in cost_rows],
    }
