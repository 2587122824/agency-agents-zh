from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..core.config import CONNECTED_LOCAL_ASSET_ROOT_REF
from ..db.models import (
    Asset,
    DeliveryAttempt,
    Project,
    ProjectEvent,
    QCFinding,
    QCReport,
    Timeline,
    utc_now,
)
from ..repositories import (
    DeliveryRepository,
    SqlAlchemyCommandRepository,
    SqlAlchemyDeliveryRepository,
    SqlAlchemyEventRepository,
)
from ..orchestration.project_transitions import (
    ProjectStateTrigger,
    block_project,
    transition_project,
)
from ..quality.service import (
    QualityConflictError,
    asset_read,
    probe_media,
    resolve_local_asset_path,
    sha256_file,
    storage_policy_for_snapshot,
)
from .contracts import AuthorizeDelivery, RegisterDeliveryOutput, VerifyDelivery


DELIVERY_RULESET_VERSION = "v2.delivery-file-contract.v1"


class DeliveryConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DeliveryNotFoundError(ValueError):
    pass


def _delivery(session: Session) -> DeliveryRepository:
    return SqlAlchemyDeliveryRepository(session)


def _event(session: Session, event: ProjectEvent) -> None:
    SqlAlchemyEventRepository(session).add(event)


def _hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _receipt(session: Session, project_id: str, command_id: str, command_type: str):
    receipt = SqlAlchemyCommandRepository(session).get(project_id, command_id)
    if not receipt:
        return None
    if receipt.command_type != command_type:
        raise DeliveryConflictError("COMMAND_ID_REUSED", f"命令 ID 已用于 {receipt.command_type}。")
    return receipt


def _save_receipt(
    session: Session,
    project_id: str,
    command_id: str,
    command_type: str,
    result_type: str,
    result_id: str,
) -> None:
    SqlAlchemyCommandRepository(session).add(
        project_id,
        command_id,
        command_type,
        result_type,
        result_id,
    )


def _require_attempt(session: Session, project: Project, attempt_id: str) -> DeliveryAttempt:
    attempt = _delivery(session).attempt(attempt_id)
    if not attempt or attempt.project_id != project.id:
        raise DeliveryNotFoundError("Delivery attempt not found")
    return attempt


def _confirmed_timeline(session: Session, project: Project, timeline_id: str | None = None) -> Timeline:
    rows = _delivery(session).confirmed_timelines(
        project.id,
        project.active_snapshot_id,
        timeline_id=timeline_id,
    )
    if len(rows) != 1:
        raise DeliveryConflictError(
            "CONFIRMED_TIMELINE_NOT_EXACT",
            "当前活动快照必须精确对应一个 confirmed 时间线。",
        )
    return rows[0]


def _delivery_manifest(session: Session, project: Project, timeline: Timeline) -> dict:
    repository = _delivery(session)
    snapshot = repository.snapshot(timeline.snapshot_id)
    if not snapshot or snapshot.project_id != project.id or project.active_snapshot_id != snapshot.id:
        raise DeliveryConflictError("DELIVERY_SNAPSHOT_MISMATCH", "交付时间线不属于当前活动快照。")
    items = repository.timeline_items(timeline.id)
    asset_ids = [item.asset_id for item in items if item.asset_id]
    assets = {
        asset.id: asset
        for asset in repository.assets_by_ids(asset_ids)
    } if asset_ids else {}
    input_items = []
    for item in items:
        if not item.asset_id:
            if not item.gap_reason:
                raise DeliveryConflictError("DELIVERY_TIMELINE_ASSET_MISSING", "确认时间线包含未解释的空素材引用。")
            input_items.append({
                "timeline_item_id": item.id,
                "track_type": item.track_type,
                "sequence_number": item.sequence_number,
                "asset_id": None,
                "asset_content_hash": None,
                "gap_reason": item.gap_reason,
                "source_in_ms": None,
                "source_out_ms": None,
                "timeline_in_ms": item.timeline_in_ms,
                "timeline_out_ms": item.timeline_out_ms,
                "transform": item.transform,
            })
            continue
        if item.asset_id not in assets:
            raise DeliveryConflictError("DELIVERY_TIMELINE_ASSET_MISSING", "确认时间线包含缺失素材引用。")
        asset = assets[item.asset_id]
        if asset.project_id != project.id or asset.snapshot_id != snapshot.id or asset.state != "used" or not asset.content_hash:
            raise DeliveryConflictError(
                "DELIVERY_INPUT_ASSET_INVALID",
                "交付输入必须是当前项目、当前快照中具有内容哈希的 used 素材。",
            )
        input_items.append({
            "timeline_item_id": item.id,
            "track_type": item.track_type,
            "sequence_number": item.sequence_number,
            "asset_id": asset.id,
            "asset_content_hash": asset.content_hash,
            "gap_reason": None,
            "source_in_ms": item.source_in_ms,
            "source_out_ms": item.source_out_ms,
            "timeline_in_ms": item.timeline_in_ms,
            "timeline_out_ms": item.timeline_out_ms,
            "transform": item.transform,
        })
    output_spec = dict(snapshot.output_spec)
    output_spec["duration_ms"] = project.duration_seconds * 1000
    if output_spec.get("container") != "mp4":
        raise DeliveryConflictError("DELIVERY_CONTAINER_UNSUPPORTED", "当前仅连接 MP4 最终交付验证器。")
    return {
        "schema_version": "v2.delivery-request.v1",
        "project_id": project.id,
        "snapshot_id": snapshot.id,
        "timeline_id": timeline.id,
        "timeline_contract_hash": timeline.contract_hash,
        "input_items": input_items,
        "output_spec": output_spec,
    }


def authorize_delivery(session: Session, project: Project, payload: AuthorizeDelivery) -> dict:
    repository = _delivery(session)
    receipt = _receipt(session, project.id, payload.command_id, "delivery.authorize")
    if receipt:
        return delivery_attempt_read(session, _require_attempt(session, project, receipt.result_id))
    if project.status != "delivery_ready" or project.delivery_asset_id:
        raise DeliveryConflictError(
            "PROJECT_NOT_DELIVERY_READY",
            "项目必须具有未交付的 confirmed 时间线才能授权交付。",
        )
    timeline = _confirmed_timeline(session, project, payload.timeline_id)
    if timeline.contract_hash != payload.expected_timeline_contract_hash:
        raise DeliveryConflictError("TIMELINE_CONTRACT_HASH_MISMATCH", "时间线合同哈希不匹配，请刷新后重试。")
    if not payload.confirm_delivery_authorization:
        raise DeliveryConflictError("DELIVERY_AUTHORIZATION_REQUIRED", "必须明确确认本次交付范围。")
    if repository.has_attempt_for_timeline(timeline.id):
        raise DeliveryConflictError(
            "DELIVERY_ATTEMPT_EXISTS",
            "当前时间线已有交付尝试；重试语义尚未确认，不能创建第二次尝试。",
        )
    manifest = _delivery_manifest(session, project, timeline)
    fingerprint = _hash(manifest)
    attempt = DeliveryAttempt(
        project_id=project.id,
        snapshot_id=timeline.snapshot_id,
        timeline_id=timeline.id,
        attempt_number=1,
        status="authorized",
        execution_kind=payload.execution_kind,
        request_manifest=manifest,
        request_fingerprint=fingerprint,
        created_by=payload.actor_id,
    )
    repository.add(attempt)
    repository.flush()
    _save_receipt(session, project.id, payload.command_id, "delivery.authorize", "delivery_attempt", attempt.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=timeline.snapshot_id,
        event_type="delivery.authorized.v1",
        aggregate_type="delivery_attempt",
        aggregate_id=attempt.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="User authorized an exact delivery request without starting a renderer.",
        data={
            "delivery_attempt_id": attempt.id,
            "timeline_id": timeline.id,
            "request_fingerprint": fingerprint,
            "execution_kind": payload.execution_kind,
        },
    ))
    session.commit()
    return delivery_attempt_read(session, attempt)


def delivery_upload_limit(session: Session, project: Project, attempt_id: str) -> int:
    attempt = _require_attempt(session, project, attempt_id)
    if project.status != "delivery_ready" or attempt.status != "authorized":
        raise DeliveryConflictError("DELIVERY_NOT_AWAITING_OUTPUT", "交付尝试当前不接受文件上传。")
    try:
        policy = storage_policy_for_snapshot(session, attempt.snapshot_id)
    except QualityConflictError as exc:
        raise DeliveryConflictError(exc.code, str(exc)) from exc
    if policy.backend_kind != "local" or policy.local_root_ref != CONNECTED_LOCAL_ASSET_ROOT_REF:
        raise DeliveryConflictError("STORAGE_ADAPTER_NOT_CONNECTED", "当前快照没有连接本地最终交付存储。")
    return policy.max_file_size_bytes


def register_delivery_output(
    session: Session,
    project: Project,
    attempt_id: str,
    payload: RegisterDeliveryOutput,
    temporary_path: Path,
) -> dict:
    repository = _delivery(session)
    receipt = _receipt(session, project.id, payload.command_id, "delivery.output.register")
    if receipt:
        temporary_path.unlink(missing_ok=True)
        return delivery_attempt_read(session, _require_attempt(session, project, receipt.result_id))
    attempt = _require_attempt(session, project, attempt_id)
    if project.status != "delivery_ready" or attempt.status != "authorized":
        raise DeliveryConflictError("DELIVERY_NOT_AWAITING_OUTPUT", "交付尝试当前不接受文件上传。")
    if attempt.row_version != payload.expected_row_version:
        raise DeliveryConflictError("DELIVERY_ROW_VERSION_MISMATCH", "交付尝试已变化，请刷新后重试。")
    if attempt.request_fingerprint != payload.expected_request_fingerprint:
        raise DeliveryConflictError("DELIVERY_REQUEST_FINGERPRINT_MISMATCH", "交付请求指纹不匹配。")
    if payload.mime_type != "video/mp4":
        raise DeliveryConflictError("DELIVERY_MIME_TYPE_UNSUPPORTED", "最终交付当前只接受 video/mp4。")
    try:
        policy = storage_policy_for_snapshot(session, attempt.snapshot_id)
    except QualityConflictError as exc:
        raise DeliveryConflictError(exc.code, str(exc)) from exc
    if payload.byte_size > policy.max_file_size_bytes or payload.mime_type not in policy.allowed_mime_types:
        raise DeliveryConflictError("DELIVERY_STORAGE_POLICY_VIOLATION", "交付文件不符合当前快照存储策略。")
    uri = f"runtime://assets/deliveries/{project.id}/{attempt.id}.mp4"
    try:
        final_path = resolve_local_asset_path(uri)
    except QualityConflictError as exc:
        raise DeliveryConflictError(exc.code, str(exc)) from exc
    if final_path.exists():
        raise DeliveryConflictError("DELIVERY_OUTPUT_PATH_EXISTS", "目标交付文件已存在，不能覆盖。")
    if repository.asset_by_uri("local", uri):
        raise DeliveryConflictError("DELIVERY_ASSET_ALREADY_REGISTERED", "目标交付 URI 已登记。")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path.replace(final_path)
    now = utc_now()
    asset = Asset(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        work_attempt_id=None,
        dag_node_id=None,
        output_index=0,
        asset_type="final_delivery",
        role="final_delivery",
        uri=uri,
        storage_backend="local",
        provider_output_manifest={
            "schema_version": "v2.delivery-output.v1",
            "source": "user_upload",
            "original_filename": payload.original_filename,
            "mime_type": payload.mime_type,
            "content_hash": payload.content_hash,
            "byte_size": payload.byte_size,
            "request_fingerprint": attempt.request_fingerprint,
        },
        mime_type=payload.mime_type,
        state="created",
    )
    repository.add(asset)
    repository.flush()
    attempt.final_asset_id = asset.id
    attempt.status = "output_registered"
    attempt.output_registered_at = now
    attempt.row_version += 1
    _save_receipt(session, project.id, payload.command_id, "delivery.output.register", "delivery_attempt", attempt.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        event_type="asset.created.v1",
        aggregate_type="asset",
        aggregate_id=asset.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Uploaded final delivery file registered as an unverified asset.",
        data={"delivery_attempt_id": attempt.id, "asset_id": asset.id, "request_fingerprint": attempt.request_fingerprint},
    ))
    try:
        session.commit()
    except Exception:
        final_path.unlink(missing_ok=True)
        raise
    return delivery_attempt_read(session, attempt)


def _block_delivery(
    session: Session,
    project: Project,
    attempt: DeliveryAttempt,
    asset: Asset,
    payload: VerifyDelivery,
    code: str,
    evidence: dict,
) -> dict:
    repository = _delivery(session)
    now = utc_now()
    number = repository.next_report_number(asset.id)
    report = QCReport(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        asset_id=asset.id,
        report_number=number,
        ruleset_version=DELIVERY_RULESET_VERSION,
        status="blocked",
        analyzer="deterministic-delivery-verifier",
    )
    repository.add(report)
    repository.flush()
    repository.add(QCFinding(
        qc_report_id=report.id,
        code=code,
        severity="blocked",
        evidence=evidence,
        contract_field="delivery.request_manifest.output_spec",
        disposition="block",
    ))
    asset.state = "archived"
    asset.archived_at = now
    asset.row_version += 1
    attempt.status = "blocked"
    attempt.error_code = code
    attempt.error_detail = evidence
    attempt.row_version += 1
    block_project(
        session,
        project,
        reason_code=code,
        responsible_aggregate_type="delivery_attempt",
        responsible_aggregate_id=attempt.id,
        actor_type="system",
        actor_id=payload.actor_id,
        event_data={"asset_id": asset.id, "qc_report_id": report.id},
    )
    _save_receipt(session, project.id, payload.command_id, "delivery.verify", "delivery_attempt", attempt.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        event_type="delivery.blocked.v1",
        aggregate_type="delivery_attempt",
        aggregate_id=attempt.id,
        actor_type="system",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Final delivery file failed deterministic verification.",
        data={"delivery_attempt_id": attempt.id, "asset_id": asset.id, "qc_report_id": report.id, "code": code, "evidence": evidence},
    ))
    session.commit()
    return delivery_attempt_read(session, attempt)


def verify_delivery(
    session: Session,
    project: Project,
    attempt_id: str,
    payload: VerifyDelivery,
) -> dict:
    repository = _delivery(session)
    receipt = _receipt(session, project.id, payload.command_id, "delivery.verify")
    if receipt:
        return delivery_attempt_read(session, _require_attempt(session, project, receipt.result_id))
    attempt = _require_attempt(session, project, attempt_id)
    if project.status != "delivery_ready" or attempt.status != "output_registered" or not attempt.final_asset_id:
        raise DeliveryConflictError("DELIVERY_OUTPUT_NOT_REGISTERED", "交付尝试没有待验证的最终文件。")
    if attempt.row_version != payload.expected_row_version:
        raise DeliveryConflictError("DELIVERY_ROW_VERSION_MISMATCH", "交付尝试已变化，请刷新后重试。")
    asset = repository.asset(attempt.final_asset_id)
    if not asset or asset.project_id != project.id or asset.state != "created":
        raise DeliveryConflictError("DELIVERY_ASSET_NOT_CREATED", "最终交付素材不存在或状态无效。")
    if asset.row_version != payload.expected_asset_row_version:
        raise DeliveryConflictError("ASSET_ROW_VERSION_MISMATCH", "最终交付素材已变化，请刷新后重试。")
    timeline = _confirmed_timeline(session, project, attempt.timeline_id)
    current_manifest = _delivery_manifest(session, project, timeline)
    if _hash(current_manifest) != attempt.request_fingerprint or current_manifest != attempt.request_manifest:
        raise DeliveryConflictError("DELIVERY_INPUT_CHANGED", "确认时间线或输入素材事实已变化，不能验证旧输出。")
    try:
        path = resolve_local_asset_path(asset.uri)
    except QualityConflictError as exc:
        return _block_delivery(session, project, attempt, asset, payload, exc.code, {"detail": str(exc)})
    if not path.is_file():
        return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_FILE_MISSING", {"uri": asset.uri})
    content_hash, byte_size = sha256_file(path)
    declared = asset.provider_output_manifest
    if content_hash != declared.get("content_hash") or byte_size != declared.get("byte_size"):
        return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_FILE_FACT_MISMATCH", {
            "actual_hash": content_hash,
            "expected_hash": declared.get("content_hash"),
            "actual_bytes": byte_size,
            "expected_bytes": declared.get("byte_size"),
        })
    try:
        media = probe_media(path, "final_delivery")
    except (OSError, ValueError, UnicodeError, QualityConflictError) as exc:
        return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_FILE_INVALID", {"probe_error": str(exc)})
    expected = attempt.request_manifest["output_spec"]
    if media["mime_type"] != "video/mp4":
        return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_MIME_TYPE_INVALID", {"actual": media["mime_type"], "expected": "video/mp4"})
    if media["width"] != expected.get("width") or media["height"] != expected.get("height"):
        return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_DIMENSIONS_INVALID", {
            "actual": [media["width"], media["height"]],
            "expected": [expected.get("width"), expected.get("height")],
        })
    expected_duration = expected.get("duration_ms")
    if media["duration_ms"] is None or abs(media["duration_ms"] - expected_duration) > 100:
        return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_DURATION_INVALID", {
            "actual_ms": media["duration_ms"],
            "expected_ms": expected_duration,
            "tolerance_ms": 100,
        })
    try:
        policy = storage_policy_for_snapshot(session, attempt.snapshot_id)
    except QualityConflictError as exc:
        return _block_delivery(session, project, attempt, asset, payload, exc.code, {"detail": str(exc)})
    if byte_size > policy.max_file_size_bytes or media["mime_type"] not in policy.allowed_mime_types:
        return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_STORAGE_POLICY_VIOLATION", {
            "byte_size": byte_size,
            "maximum_bytes": policy.max_file_size_bytes,
            "mime_type": media["mime_type"],
            "allowed_mime_types": policy.allowed_mime_types,
        })
    now = utc_now()
    report = QCReport(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        asset_id=asset.id,
        report_number=1,
        ruleset_version=DELIVERY_RULESET_VERSION,
        status="passed",
        analyzer="deterministic-delivery-verifier",
    )
    repository.add(report)
    asset.content_hash = content_hash
    asset.byte_size = byte_size
    asset.mime_type = media["mime_type"]
    asset.width = media["width"]
    asset.height = media["height"]
    asset.duration_ms = media["duration_ms"]
    asset.state = "verified"
    asset.verified_at = now
    asset.row_version += 1
    attempt.status = "verified"
    attempt.verified_at = now
    attempt.row_version += 1
    timeline.status = "exported"
    timeline.row_version += 1
    project.delivery_asset_id = asset.id
    transition_project(
        session,
        project,
        ProjectStateTrigger.DELIVERY_VERIFIED,
        actor_type="system",
        actor_id=payload.actor_id,
        event_data={"delivery_attempt_id": attempt.id, "delivery_asset_id": asset.id},
    )
    _save_receipt(session, project.id, payload.command_id, "delivery.verify", "delivery_attempt", attempt.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        event_type="delivery.verified.v1",
        aggregate_type="delivery_attempt",
        aggregate_id=attempt.id,
        actor_type="system",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Final delivery file passed deterministic verification.",
        data={"delivery_attempt_id": attempt.id, "asset_id": asset.id, "content_hash": content_hash},
    ))
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        event_type="project.completed.v1",
        aggregate_type="project",
        aggregate_id=project.id,
        actor_type="system",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Project completed after the final delivery asset passed all completion guards.",
        data={"delivery_attempt_id": attempt.id, "delivery_asset_id": asset.id, "timeline_id": timeline.id},
    ))
    session.commit()
    return delivery_attempt_read(session, attempt)


def delivery_attempt_read(session: Session, attempt: DeliveryAttempt) -> dict:
    result = {column.name: getattr(attempt, column.name) for column in attempt.__table__.columns}
    asset = _delivery(session).asset(attempt.final_asset_id) if attempt.final_asset_id else None
    result["final_asset"] = asset_read(session, asset) if asset else None
    return result


def delivery_workspace(session: Session, project: Project) -> dict:
    repository = _delivery(session)
    timelines = repository.delivery_timelines(project.id, project.active_snapshot_id) if project.active_snapshot_id else []
    timeline = timelines[0] if timelines else None
    attempts = repository.project_attempts(project.id)
    if project.status == "completed" and project.delivery_asset_id:
        next_action = {"code": "DELIVERY_COMPLETE", "label": "最终交付文件已验证"}
    elif attempts and attempts[0].status == "blocked":
        next_action = {"code": "DELIVERY_BLOCKED", "label": "查看交付阻断证据"}
    elif attempts and attempts[0].status == "output_registered":
        next_action = {"code": "VERIFY_DELIVERY", "label": "验证最终交付文件"}
    elif attempts and attempts[0].status == "authorized":
        next_action = {"code": "UPLOAD_DELIVERY", "label": "上传外部渲染的 MP4"}
    elif project.status == "delivery_ready" and timeline:
        next_action = {"code": "AUTHORIZE_DELIVERY", "label": "授权当前确认时间线交付"}
    else:
        next_action = {"code": "CONFIRM_TIMELINE", "label": "先确认当前活动快照的时间线"}
    return {
        "project_id": project.id,
        "project_title": project.title,
        "project_status": project.status,
        "active_snapshot_id": project.active_snapshot_id,
        "delivery_asset_id": project.delivery_asset_id,
        "confirmed_timeline": None if not timeline else {
            "id": timeline.id,
            "version_number": timeline.version_number,
            "status": timeline.status,
            "contract_hash": timeline.contract_hash,
            "output_spec": timeline.output_spec,
            "confirmed_at": timeline.confirmed_at,
        },
        "attempts": [delivery_attempt_read(session, attempt) for attempt in attempts],
        "next_action": next_action,
    }
