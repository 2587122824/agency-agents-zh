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
    WorkAttempt,
    WorkItem,
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
    measure_program_audio,
    probe_media,
    resolve_local_asset_path,
    sha256_file,
    storage_policy_for_snapshot,
)
from .contracts import AuthorizeDelivery, RegisterDeliveryOutput, VerifyDelivery
from .renderer import (
    RENDERER_CONTRACT,
    LocalRenderAudioInput,
    LocalRenderError,
    LocalRenderInput,
    LocalRenderRequest,
    LocalRenderResult,
    LocalRenderSubtitleInput,
    inspect_local_ffmpeg,
)


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


def _delivery_manifest(
    session: Session,
    project: Project,
    timeline: Timeline,
    execution: dict,
) -> dict:
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
            "asset_uri": asset.uri,
            "storage_backend": asset.storage_backend,
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
        "schema_version": "v2.delivery-request.v2",
        "project_id": project.id,
        "snapshot_id": snapshot.id,
        "timeline_id": timeline.id,
        "timeline_contract_hash": timeline.contract_hash,
        "track_config": timeline.track_config,
        "input_items": input_items,
        "output_spec": output_spec,
        "execution": execution,
    }


def _execution_contract(execution_kind: str) -> dict:
    if execution_kind == "external_upload":
        return {"kind": "external_upload"}
    readiness = inspect_local_ffmpeg()
    if not readiness.available:
        raise DeliveryConflictError(
            readiness.reason_code or "FFMPEG_UNAVAILABLE",
            readiness.reason or "本机 FFmpeg 当前不可用。",
        )
    return {
        "kind": "local_ffmpeg",
        "renderer_contract": RENDERER_CONTRACT,
        "executable_path": readiness.executable_path,
        "ffmpeg_version": readiness.version,
        "video_encoder": "libx264",
        "preset": "medium",
        "crf": 18,
    }


def validate_local_render_manifest(manifest: dict) -> None:
    track_config = manifest.get("track_config") or {}
    items = manifest.get("input_items") or []
    if not items:
        raise DeliveryConflictError("LOCAL_RENDER_INPUT_EMPTY", "本机合成没有可用的视频片段。")
    video_items = [item for item in items if item.get("track_type") == "main_video"]
    audio_items = [item for item in items if item.get("track_type") == "audio"]
    subtitle_items = [item for item in items if item.get("track_type") == "subtitle"]
    if any(item.get("track_type") not in {"main_video", "audio", "subtitle"} for item in items):
        raise DeliveryConflictError("LOCAL_RENDER_TRACKS_UNSUPPORTED", "本机合成包含不支持的轨道。")
    if bool(audio_items) != bool(track_config.get("audio_enabled")):
        raise DeliveryConflictError("LOCAL_RENDER_AUDIO_CONTRACT_INVALID", "音频轨开关与冻结音频条目不一致。")
    if bool(subtitle_items) != bool(track_config.get("subtitle_enabled")) or len(subtitle_items) > 1:
        raise DeliveryConflictError("LOCAL_RENDER_SUBTITLE_CONTRACT_INVALID", "字幕轨开关必须精确对应一份冻结字幕素材。")
    mastering = track_config.get("audio_mastering")
    if bool(audio_items) and (
        not isinstance(mastering, dict)
        or not isinstance(mastering.get("loudness_target_lufs"), (int, float))
        or mastering["loudness_target_lufs"] < -24
        or mastering["loudness_target_lufs"] > -9
        or not isinstance(mastering.get("true_peak_limit_dbtp"), (int, float))
        or mastering["true_peak_limit_dbtp"] < -3
        or mastering["true_peak_limit_dbtp"] > -0.1
        or mastering.get("clipping_control") != "limiter"
    ):
        raise DeliveryConflictError("LOCAL_RENDER_MASTERING_CONTRACT_INVALID", "本机合成缺少有效的响度、true peak 和削波控制合同。")
    cursor = 0
    for item in sorted(video_items, key=lambda row: (row["timeline_in_ms"], row["sequence_number"])):
        if not item.get("asset_id") or item.get("gap_reason"):
            raise DeliveryConflictError("LOCAL_RENDER_GAP_UNSUPPORTED", "本机合成不接受时间线空位。")
        if item.get("timeline_in_ms") != cursor:
            raise DeliveryConflictError("LOCAL_RENDER_TIMELINE_NOT_CONTIGUOUS", "主视频轨必须从零开始且连续。")
        source_duration = (item.get("source_out_ms") or 0) - (item.get("source_in_ms") or 0)
        timeline_duration = item["timeline_out_ms"] - item["timeline_in_ms"]
        if source_duration != timeline_duration:
            raise DeliveryConflictError("LOCAL_RENDER_SPEED_CHANGE_UNSUPPORTED", "本机合成首期不支持变速片段。")
        if (item.get("transform") or {}).get("fit") != "cover":
            raise DeliveryConflictError("LOCAL_RENDER_TRANSFORM_UNSUPPORTED", "本机合成首期只支持 cover 画面适配。")
        for transition_key in ("transition_in", "transition_out"):
            transition = (item.get("transform") or {}).get(transition_key)
            if transition is not None and (
                not isinstance(transition, dict)
                or transition.get("type") not in {"cut", "fade"}
                or not isinstance(transition.get("duration_ms"), int)
            ):
                raise DeliveryConflictError("LOCAL_RENDER_TRANSITION_INVALID", "本机合成收到无效的冻结视频转场。")
        cursor = item["timeline_out_ms"]
    for item in sorted(audio_items, key=lambda row: (row["timeline_in_ms"], row["sequence_number"])):
        if not item.get("asset_id") or item.get("gap_reason"):
            raise DeliveryConflictError("LOCAL_RENDER_AUDIO_GAP_UNSUPPORTED", "本机合成不接受音频轨空位。")
        source_duration = (item.get("source_out_ms") or 0) - (item.get("source_in_ms") or 0)
        timeline_duration = item["timeline_out_ms"] - item["timeline_in_ms"]
        transform = item.get("transform") or {}
        playback = transform.get("playback") or {"mode": "trim"}
        mix = transform.get("mix", "voiceover")
        if source_duration != timeline_duration and not (
            mix == "background_music"
            and playback.get("mode") == "loop"
            and 0 < source_duration < timeline_duration
        ):
            raise DeliveryConflictError("LOCAL_RENDER_SPEED_CHANGE_UNSUPPORTED", "本机合成不支持音频变速。")
        envelope = transform.get("volume_envelope")
        if envelope is not None and (
            not isinstance(envelope, list)
            or len(envelope) < 2
            or any(
                not isinstance(point, dict)
                or not isinstance(point.get("time_ms"), int)
                or not isinstance(point.get("gain_db"), (int, float))
                for point in envelope
            )
        ):
            raise DeliveryConflictError("LOCAL_RENDER_VOLUME_ENVELOPE_INVALID", "本机合成收到无效的冻结音量包络。")
        if mix == "background_music":
            rights = transform.get("rights") or {}
            ducking = transform.get("ducking") or {}
            if rights.get("confirmed") is not True or rights.get("basis") not in {"owned", "licensed", "royalty_free"}:
                raise DeliveryConflictError("LOCAL_RENDER_BGM_RIGHTS_INVALID", "本机合成收到未授权的背景音乐。")
            if not isinstance(ducking.get("enabled"), bool):
                raise DeliveryConflictError("LOCAL_RENDER_BGM_DUCKING_INVALID", "本机合成收到无效的背景音乐压低合同。")
    for item in subtitle_items:
        if not item.get("asset_id") or item.get("gap_reason"):
            raise DeliveryConflictError("LOCAL_RENDER_SUBTITLE_GAP_UNSUPPORTED", "本机合成不接受字幕轨空位。")
        if item.get("timeline_in_ms") != 0 or item.get("source_in_ms") != 0:
            raise DeliveryConflictError("LOCAL_RENDER_SUBTITLE_OFFSET_UNSUPPORTED", "字幕轨必须从成片零点开始。")
        if (item.get("transform") or {}).get("render") != "burn_in":
            raise DeliveryConflictError("LOCAL_RENDER_SUBTITLE_TRANSFORM_UNSUPPORTED", "字幕轨必须明确使用 burn_in 渲染。")
    expected = manifest.get("output_spec") or {}
    if cursor != expected.get("duration_ms"):
        raise DeliveryConflictError("LOCAL_RENDER_DURATION_MISMATCH", "时间线总时长与交付规格不一致。")
    for key in ("width", "height", "fps"):
        if not isinstance(expected.get(key), (int, float)) or expected[key] <= 0:
            raise DeliveryConflictError("LOCAL_RENDER_OUTPUT_SPEC_INVALID", f"交付规格缺少有效的 {key}。")


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
    execution = _execution_contract(payload.execution_kind)
    manifest = _delivery_manifest(session, project, timeline, execution)
    if payload.execution_kind == "local_ffmpeg":
        validate_local_render_manifest(manifest)
    fingerprint = _hash(manifest)
    attempt = DeliveryAttempt(
        project_id=project.id,
        snapshot_id=timeline.snapshot_id,
        timeline_id=timeline.id,
        attempt_number=1,
        status="queued" if payload.execution_kind == "local_ffmpeg" else "authorized",
        execution_kind=payload.execution_kind,
        request_manifest=manifest,
        request_fingerprint=fingerprint,
        created_by=payload.actor_id,
    )
    repository.add(attempt)
    repository.flush()
    if payload.execution_kind == "local_ffmpeg":
        item = WorkItem(
            project_id=project.id,
            snapshot_id=None,
            dag_node_id=None,
            kind="render_delivery",
            payload={"delivery_attempt_id": attempt.id},
            status="queued",
            priority=500,
            request_fingerprint=fingerprint,
        )
        repository.add(item)
        repository.flush()
        work_attempt = WorkAttempt(
            work_item_id=item.id,
            attempt_number=1,
            trigger="user_authorized",
            provider="local_ffmpeg",
            request_fingerprint=fingerprint,
            request_manifest=manifest,
            state="created",
        )
        repository.add(work_attempt)
        repository.flush()
        item.current_attempt_id = work_attempt.id
        attempt.work_item_id = item.id
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
        message=(
            "User authorized an exact local FFmpeg render request."
            if payload.execution_kind == "local_ffmpeg"
            else "User authorized an exact external-upload delivery request."
        ),
        data={
            "delivery_attempt_id": attempt.id,
            "timeline_id": timeline.id,
            "request_fingerprint": fingerprint,
            "execution_kind": payload.execution_kind,
            "work_item_id": attempt.work_item_id,
        },
    ))
    session.commit()
    return delivery_attempt_read(session, attempt)


def delivery_upload_limit(session: Session, project: Project, attempt_id: str) -> int:
    attempt = _require_attempt(session, project, attempt_id)
    if (
        project.status != "delivery_ready"
        or attempt.status != "authorized"
        or attempt.execution_kind != "external_upload"
    ):
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
    if (
        project.status != "delivery_ready"
        or attempt.status != "authorized"
        or attempt.execution_kind != "external_upload"
    ):
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
    current_manifest = _delivery_manifest(
        session,
        project,
        timeline,
        dict(attempt.request_manifest.get("execution") or {}),
    )
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
    audio_evidence = None
    track_config = attempt.request_manifest.get("track_config") or {}
    if track_config.get("audio_enabled"):
        audio_evidence = measure_program_audio(path)
        mastering = track_config.get("audio_mastering") or {}
        if audio_evidence.get("ebur128_status") != "measured":
            return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_AUDIO_QC_ANALYSIS_FAILED", audio_evidence)
        loudness_target = mastering.get("loudness_target_lufs")
        true_peak_limit = mastering.get("true_peak_limit_dbtp")
        if not isinstance(loudness_target, (int, float)) or abs(audio_evidence["integrated_loudness_lufs"] - loudness_target) > 4:
            return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_AUDIO_LOUDNESS_OUT_OF_RANGE", {
                **audio_evidence,
                "target_lufs": loudness_target,
                "tolerance_lu": 4,
            })
        if not isinstance(true_peak_limit, (int, float)) or audio_evidence["true_peak_dbtp"] > true_peak_limit + 0.2:
            return _block_delivery(session, project, attempt, asset, payload, "DELIVERY_AUDIO_TRUE_PEAK_EXCEEDED", {
                **audio_evidence,
                "limit_dbtp": true_peak_limit,
                "tolerance_db": 0.2,
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
    repository.flush()
    if audio_evidence:
        repository.add(QCFinding(
            qc_report_id=report.id,
            code="DELIVERY_AUDIO_TECHNICAL_QC_PASSED",
            severity="passed",
            evidence=audio_evidence,
            contract_field="delivery.request_manifest.track_config.audio_mastering",
            disposition="pass",
        ))
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


def prepare_local_render(
    session: Session,
    project: Project,
    work_item: WorkItem,
    work_attempt: WorkAttempt,
) -> tuple[DeliveryAttempt, LocalRenderRequest]:
    delivery_id = str((work_item.payload or {}).get("delivery_attempt_id") or "")
    attempt = _require_attempt(session, project, delivery_id)
    if (
        project.status != "delivery_ready"
        or attempt.execution_kind != "local_ffmpeg"
        or attempt.status != "queued"
        or attempt.work_item_id != work_item.id
        or work_item.kind != "render_delivery"
    ):
        raise LocalRenderError(
            "LOCAL_RENDER_AUTHORITY_INVALID",
            "本地合成交付授权、项目状态或工作项关系无效。",
        )
    if (
        work_attempt.provider != "local_ffmpeg"
        or work_attempt.request_fingerprint != attempt.request_fingerprint
        or work_attempt.request_manifest != attempt.request_manifest
    ):
        raise LocalRenderError(
            "LOCAL_RENDER_WORK_CONTRACT_MISMATCH",
            "本地合成工作尝试与交付请求不一致。",
        )
    timeline = _confirmed_timeline(session, project, attempt.timeline_id)
    current_manifest = _delivery_manifest(
        session,
        project,
        timeline,
        dict(attempt.request_manifest.get("execution") or {}),
    )
    if current_manifest != attempt.request_manifest or _hash(current_manifest) != attempt.request_fingerprint:
        raise LocalRenderError(
            "DELIVERY_INPUT_CHANGED",
            "确认时间线或输入素材事实已变化，不能执行旧交付请求。",
        )
    try:
        validate_local_render_manifest(current_manifest)
    except DeliveryConflictError as exc:
        raise LocalRenderError(exc.code, str(exc)) from exc
    execution = current_manifest["execution"]
    readiness = inspect_local_ffmpeg()
    if not readiness.available:
        raise LocalRenderError(
            readiness.reason_code or "FFMPEG_UNAVAILABLE",
            readiness.reason or "本机 FFmpeg 当前不可用。",
        )
    if (
        readiness.executable_path != execution.get("executable_path")
        or readiness.version != execution.get("ffmpeg_version")
    ):
        raise LocalRenderError(
            "FFMPEG_AUTHORITY_CHANGED",
            "当前 FFmpeg 路径或版本与授权时冻结的执行环境不一致。",
            {
                "authorized_path": execution.get("executable_path"),
                "current_path": readiness.executable_path,
                "authorized_version": execution.get("ffmpeg_version"),
                "current_version": readiness.version,
            },
        )
    render_inputs: list[LocalRenderInput] = []
    audio_inputs: list[LocalRenderAudioInput] = []
    subtitle_input: LocalRenderSubtitleInput | None = None
    repository = _delivery(session)
    for item in sorted(
        current_manifest["input_items"],
        key=lambda row: (row["timeline_in_ms"], row["sequence_number"]),
    ):
        asset = repository.asset(item["asset_id"])
        if not asset or asset.uri != item.get("asset_uri") or asset.storage_backend != "local":
            raise LocalRenderError(
                "LOCAL_RENDER_ASSET_INVALID",
                "本地合成输入素材不存在、URI 已变化或不是本地素材。",
                {"asset_id": item.get("asset_id")},
            )
        try:
            path = resolve_local_asset_path(asset.uri)
        except QualityConflictError as exc:
            raise LocalRenderError(exc.code, str(exc), {"asset_id": asset.id}) from exc
        if not path.is_file():
            raise LocalRenderError(
                "LOCAL_RENDER_INPUT_FILE_MISSING",
                "本地合成输入文件不存在。",
                {"asset_id": asset.id, "uri": asset.uri},
            )
        content_hash, _ = sha256_file(path)
        if content_hash != item["asset_content_hash"]:
            raise LocalRenderError(
                "LOCAL_RENDER_INPUT_HASH_MISMATCH",
                "本地合成输入文件哈希与授权事实不一致。",
                {
                    "asset_id": asset.id,
                    "expected_hash": item["asset_content_hash"],
                    "actual_hash": content_hash,
                },
            )
        if item["track_type"] == "main_video":
            transform = item.get("transform") or {}
            transition_in = transform.get("transition_in") or {}
            transition_out = transform.get("transition_out") or {}
            render_inputs.append(LocalRenderInput(
                path=path,
                source_in_ms=item["source_in_ms"],
                source_out_ms=item["source_out_ms"],
                transition_in_ms=transition_in.get("duration_ms", 0) if transition_in.get("type") == "fade" else 0,
                transition_out_ms=transition_out.get("duration_ms", 0) if transition_out.get("type") == "fade" else 0,
            ))
        elif item["track_type"] == "audio":
            transform = item.get("transform") or {}
            envelope = transform.get("volume_envelope") or []
            playback = transform.get("playback") or {"mode": "trim"}
            ducking = transform.get("ducking") or {}
            audio_inputs.append(LocalRenderAudioInput(
                path=path,
                source_in_ms=item["source_in_ms"],
                source_out_ms=item["source_out_ms"],
                timeline_in_ms=item["timeline_in_ms"],
                volume_envelope=tuple((point["time_ms"], float(point["gain_db"])) for point in envelope),
                loop=playback.get("mode") == "loop",
                output_duration_ms=item["timeline_out_ms"] - item["timeline_in_ms"],
                ducking_regions=tuple(
                    (region["start_ms"], region["end_ms"])
                    for region in ducking.get("regions", [])
                ) if ducking.get("enabled") else (),
                ducking_reduction_db=float(ducking.get("reduction_db", -12)),
                ducking_attack_ms=int(ducking.get("attack_ms", 200)),
                ducking_release_ms=int(ducking.get("release_ms", 500)),
            ))
        elif item["track_type"] == "subtitle":
            subtitle_input = LocalRenderSubtitleInput(path=path)
    uri = f"runtime://assets/deliveries/{project.id}/{attempt.id}.mp4"
    try:
        output_path = resolve_local_asset_path(uri)
    except QualityConflictError as exc:
        raise LocalRenderError(exc.code, str(exc)) from exc
    if output_path.exists() or repository.asset_by_uri("local", uri):
        raise LocalRenderError("DELIVERY_OUTPUT_PATH_EXISTS", "目标交付文件或 URI 已存在，不能覆盖。")
    spec = current_manifest["output_spec"]
    attempt.status = "rendering"
    attempt.render_started_at = utc_now()
    attempt.row_version += 1
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=attempt.snapshot_id,
        event_type="delivery.render_started.v1",
        aggregate_type="delivery_attempt",
        aggregate_id=attempt.id,
        actor_type="worker",
        actor_id="local_ffmpeg",
        message="Local FFmpeg render started from the frozen delivery request.",
        data={"delivery_attempt_id": attempt.id, "work_item_id": work_item.id},
    ))
    mastering = (current_manifest.get("track_config") or {}).get("audio_mastering") or {}
    return attempt, LocalRenderRequest(
        ffmpeg_path=Path(readiness.executable_path),
        inputs=tuple(render_inputs),
        output_path=output_path,
        width=int(spec["width"]),
        height=int(spec["height"]),
        fps=int(spec["fps"]),
        video_encoder=str(execution["video_encoder"]),
        preset=str(execution["preset"]),
        crf=int(execution["crf"]),
        audio_inputs=tuple(audio_inputs),
        subtitle_input=subtitle_input,
        loudness_target_lufs=float(mastering.get("loudness_target_lufs", -16)),
        true_peak_limit_dbtp=float(mastering.get("true_peak_limit_dbtp", -1)),
    )


def register_local_render_output(
    session: Session,
    project: Project,
    delivery_attempt: DeliveryAttempt,
    work_attempt: WorkAttempt,
    request: LocalRenderRequest,
    result: LocalRenderResult,
) -> Asset:
    if delivery_attempt.status != "rendering" or not request.output_path.is_file():
        raise LocalRenderError(
            "LOCAL_RENDER_OUTPUT_NOT_READY",
            "本地合成没有处于可登记输出的状态。",
        )
    content_hash, byte_size = sha256_file(request.output_path)
    uri = f"runtime://assets/deliveries/{project.id}/{delivery_attempt.id}.mp4"
    repository = _delivery(session)
    if repository.asset_by_uri("local", uri):
        raise LocalRenderError("DELIVERY_ASSET_ALREADY_REGISTERED", "目标交付 URI 已登记。")
    now = utc_now()
    asset = Asset(
        project_id=project.id,
        snapshot_id=delivery_attempt.snapshot_id,
        work_attempt_id=work_attempt.id,
        dag_node_id=None,
        output_index=0,
        asset_type="final_delivery",
        role="final_delivery",
        uri=uri,
        storage_backend="local",
        provider_output_manifest={
            "schema_version": "v2.delivery-output.v1",
            "source": "local_ffmpeg",
            "mime_type": "video/mp4",
            "content_hash": content_hash,
            "byte_size": byte_size,
            "request_fingerprint": delivery_attempt.request_fingerprint,
            "renderer_contract": RENDERER_CONTRACT,
            "command": list(result.command),
            "stdout_tail": result.stdout_tail,
            "stderr_tail": result.stderr_tail,
        },
        mime_type="video/mp4",
        state="created",
    )
    repository.add(asset)
    repository.flush()
    delivery_attempt.final_asset_id = asset.id
    delivery_attempt.status = "output_registered"
    delivery_attempt.render_finished_at = now
    delivery_attempt.output_registered_at = now
    delivery_attempt.row_version += 1
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=delivery_attempt.snapshot_id,
        event_type="asset.created.v1",
        aggregate_type="asset",
        aggregate_id=asset.id,
        actor_type="worker",
        actor_id="local_ffmpeg",
        message="Local FFmpeg output registered as an unverified final delivery asset.",
        data={
            "delivery_attempt_id": delivery_attempt.id,
            "work_item_id": delivery_attempt.work_item_id,
            "asset_id": asset.id,
            "request_fingerprint": delivery_attempt.request_fingerprint,
        },
    ))
    return asset


def block_local_render(
    session: Session,
    project: Project,
    delivery_attempt: DeliveryAttempt,
    error: LocalRenderError,
    *,
    actor_id: str,
) -> None:
    now = utc_now()
    delivery_attempt.status = "blocked"
    delivery_attempt.error_code = error.code
    delivery_attempt.error_detail = {
        "detail": error.detail,
        **error.evidence,
    }
    delivery_attempt.render_finished_at = now
    delivery_attempt.row_version += 1
    block_project(
        session,
        project,
        reason_code=error.code,
        responsible_aggregate_type="delivery_attempt",
        responsible_aggregate_id=delivery_attempt.id,
        actor_type="worker",
        actor_id=actor_id,
        event_data={
            "delivery_attempt_id": delivery_attempt.id,
            "work_item_id": delivery_attempt.work_item_id,
            "evidence": delivery_attempt.error_detail,
        },
    )
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=delivery_attempt.snapshot_id,
        event_type="delivery.render_blocked.v1",
        aggregate_type="delivery_attempt",
        aggregate_id=delivery_attempt.id,
        actor_type="worker",
        actor_id=actor_id,
        message="Local FFmpeg render stopped with preserved failure evidence.",
        data={
            "delivery_attempt_id": delivery_attempt.id,
            "work_item_id": delivery_attempt.work_item_id,
            "error_code": error.code,
            "error_detail": delivery_attempt.error_detail,
        },
    ))


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
    readiness = inspect_local_ffmpeg()
    delivery_methods = [
        {
            "kind": "local_ffmpeg",
            "label": "在本机生成 MP4",
            "available": readiness.available,
            "reason_code": readiness.reason_code,
            "reason": readiness.reason,
            "renderer_version": readiness.version,
        },
        {
            "kind": "external_upload",
            "label": "上传已经生成的 MP4",
            "available": True,
            "reason_code": None,
            "reason": None,
            "renderer_version": None,
        },
    ]
    if project.status == "completed" and project.delivery_asset_id:
        next_action = {"code": "DELIVERY_COMPLETE", "label": "最终交付文件已验证"}
    elif attempts and attempts[0].status == "blocked":
        next_action = {"code": "DELIVERY_BLOCKED", "label": "查看交付阻断证据"}
    elif attempts and attempts[0].status == "output_registered":
        next_action = {"code": "VERIFY_DELIVERY", "label": "验证最终交付文件"}
    elif attempts and attempts[0].status == "rendering":
        next_action = {"code": "RENDER_DELIVERY", "label": "正在本机生成最终视频"}
    elif attempts and attempts[0].status == "queued":
        next_action = {"code": "QUEUE_DELIVERY", "label": "等待本机生成最终视频"}
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
        "delivery_methods": delivery_methods,
        "attempts": [delivery_attempt_read(session, attempt) for attempt in attempts],
        "next_action": next_action,
    }
