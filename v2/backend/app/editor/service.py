from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from ..db.models import (
    Project,
    ProjectEvent,
    ProductionSnapshot,
    Timeline,
    TimelineItem,
    utc_now,
)
from ..repositories import (
    EditorRepository,
    SqlAlchemyCommandRepository,
    SqlAlchemyEditorRepository,
    SqlAlchemyEventRepository,
)
from ..orchestration.project_transitions import ProjectStateTrigger, transition_project
from ..quality.service import quality_review_view
from .contracts import (
    ApproveQualityStage,
    ConfirmTimeline,
    CreateTimelineCandidate,
    ReviseTimelineCandidate,
    ValidateTimeline,
)


class EditorConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class EditorNotFoundError(ValueError):
    pass


def _editor(session: Session) -> EditorRepository:
    return SqlAlchemyEditorRepository(session)


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
        raise EditorConflictError("COMMAND_ID_REUSED", f"命令 ID 已用于 {receipt.command_type}。")
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


def _require_timeline(session: Session, project: Project, timeline_id: str) -> Timeline:
    timeline = _editor(session).timeline(timeline_id)
    if not timeline or timeline.project_id != project.id:
        raise EditorNotFoundError("Timeline not found")
    return timeline


def _require_active_snapshot(session: Session, project: Project, snapshot_id: str) -> ProductionSnapshot:
    if not project.active_snapshot_id or project.active_snapshot_id != snapshot_id:
        raise EditorConflictError(
            "ACTIVE_SNAPSHOT_MISMATCH",
            "时间线必须精确绑定当前活动生产快照，请刷新后重试。",
        )
    snapshot = _editor(session).snapshot(snapshot_id)
    if not snapshot or snapshot.project_id != project.id:
        raise EditorNotFoundError("Production snapshot not found")
    return snapshot


def approve_quality_stage(
    session: Session,
    project: Project,
    payload: ApproveQualityStage,
) -> dict:
    receipt = _receipt(session, project.id, payload.command_id, "quality.stage.approve")
    if receipt:
        return editor_workspace(session, project)
    _require_active_snapshot(session, project, payload.expected_snapshot_id)
    if project.status != "quality_review":
        raise EditorConflictError(
            "PROJECT_NOT_IN_QUALITY_REVIEW",
            "只有处于 quality_review 的项目可以确认进入剪辑。",
        )
    quality = quality_review_view(session, project)
    if not quality["stage_ready"]:
        raise EditorConflictError(
            "QUALITY_STAGE_NOT_READY",
            "当前快照仍有未批准或缺失的必需素材，不能进入剪辑。",
        )
    transition_project(
        session,
        project,
        ProjectStateTrigger.QUALITY_STAGE_APPROVED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"snapshot_id": payload.expected_snapshot_id},
    )
    _save_receipt(session, project.id, payload.command_id, "quality.stage.approve", "project", project.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=payload.expected_snapshot_id,
        event_type="quality.stage_approved.v1",
        aggregate_type="project",
        aggregate_id=project.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="User confirmed that the approved asset set may enter editing.",
        data={"snapshot_id": payload.expected_snapshot_id, "actor_id": payload.actor_id},
    ))
    session.commit()
    return editor_workspace(session, project)


def _item_contract(item: TimelineItem) -> dict:
    return {
        "track_type": item.track_type,
        "sequence_number": item.sequence_number,
        "asset_id": item.asset_id,
        "label": item.label,
        "gap_reason": item.gap_reason,
        "source_in_ms": item.source_in_ms,
        "source_out_ms": item.source_out_ms,
        "timeline_in_ms": item.timeline_in_ms,
        "timeline_out_ms": item.timeline_out_ms,
        "transform": item.transform,
    }


def _timeline_contract(session: Session, timeline: Timeline) -> dict:
    items = _editor(session).timeline_items(timeline.id)
    return {
        "contract_version": "v2.timeline-contract.v1",
        "project_id": timeline.project_id,
        "snapshot_id": timeline.snapshot_id,
        "version_number": timeline.version_number,
        "output_spec": timeline.output_spec,
        "track_config": timeline.track_config,
        "items": [_item_contract(item) for item in items],
    }


def _add_items(session: Session, timeline: Timeline, items) -> None:
    repository = _editor(session)
    for item in items:
        repository.add(TimelineItem(
            timeline_id=timeline.id,
            track_type=item.track_type,
            sequence_number=item.sequence_number,
            asset_id=item.asset_id,
            label=item.label,
            gap_reason=item.gap_reason,
            source_in_ms=item.source_in_ms,
            source_out_ms=item.source_out_ms,
            timeline_in_ms=item.timeline_in_ms,
            timeline_out_ms=item.timeline_out_ms,
            transform=item.transform,
        ))


def _create_candidate(
    session: Session,
    project: Project,
    payload: CreateTimelineCandidate | ReviseTimelineCandidate,
    supersedes: Timeline | None,
    command_type: str,
) -> dict:
    repository = _editor(session)
    receipt = _receipt(session, project.id, payload.command_id, command_type)
    if receipt:
        return timeline_read(session, _require_timeline(session, project, receipt.result_id))
    snapshot = _require_active_snapshot(session, project, payload.expected_snapshot_id)
    if project.status not in {"editing", "delivery_ready"}:
        raise EditorConflictError(
            "PROJECT_NOT_EDITABLE",
            "项目必须先明确完成质量阶段，才能创建时间线候选。",
        )
    if payload.source == "editor_assistant":
        run = repository.agent_run(payload.source_agent_run_id) if payload.source_agent_run_id else None
        if not run or run.project_id != project.id or run.agent_role != "editor" or run.status != "completed":
            raise EditorConflictError(
                "EDITOR_AGENT_RUN_INVALID",
                "Editor Assistant 候选必须绑定当前项目中已完成的 editor AgentRun。",
            )
    elif payload.source_agent_run_id:
        raise EditorConflictError(
            "USER_TIMELINE_CANNOT_BIND_AGENT_RUN",
            "用户创建的时间线候选不能绑定 AgentRun。",
        )
    positions = [(item.track_type, item.sequence_number) for item in payload.items]
    if len(positions) != len(set(positions)):
        raise EditorConflictError(
            "TIMELINE_SEQUENCE_DUPLICATE",
            "同一轨道的 sequence_number 必须唯一。",
        )
    if not supersedes and repository.has_timeline(project.id):
        raise EditorConflictError(
            "TIMELINE_REVISION_REQUIRED",
            "项目已有时间线版本，必须从指定版本创建修订。",
        )
    if supersedes:
        if supersedes.snapshot_id != snapshot.id:
            raise EditorConflictError("TIMELINE_SNAPSHOT_MISMATCH", "被修订时间线不属于当前活动快照。")
        expected = getattr(payload, "expected_row_version", None)
        if supersedes.row_version != expected:
            raise EditorConflictError("TIMELINE_ROW_VERSION_MISMATCH", "时间线已变化，请刷新后重试。")
        if supersedes.status in {"exported", "superseded"}:
            raise EditorConflictError("TIMELINE_NOT_REVISABLE", "已导出或已被替代的时间线不能再修订。")
    version = repository.next_timeline_version(project.id)
    timeline = Timeline(
        project_id=project.id,
        snapshot_id=snapshot.id,
        version_number=version,
        supersedes_timeline_id=supersedes.id if supersedes else None,
        status="candidate",
        source=payload.source,
        source_agent_run_id=payload.source_agent_run_id,
        output_spec=snapshot.output_spec,
        track_config=payload.track_config.model_dump(),
        validation_report=[],
        created_by=payload.actor_id,
    )
    repository.add(timeline)
    repository.flush()
    _add_items(session, timeline, payload.items)
    if supersedes and supersedes.status in {"candidate", "review"}:
        supersedes.status = "superseded"
        supersedes.row_version += 1
    transition_project(
        session,
        project,
        ProjectStateTrigger.TIMELINE_CANDIDATE_CREATED,
        actor_type="user" if payload.source == "user" else "system",
        actor_id=payload.actor_id,
        event_data={"timeline_id": timeline.id, "snapshot_id": snapshot.id},
    )
    _save_receipt(session, project.id, payload.command_id, command_type, "timeline", timeline.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=snapshot.id,
        event_type="timeline.candidate_created.v1",
        aggregate_type="timeline",
        aggregate_id=timeline.id,
        actor_type="user" if payload.source == "user" else "system",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="An explicit timeline candidate was recorded for review.",
        data={
            "timeline_id": timeline.id,
            "version_number": timeline.version_number,
            "snapshot_id": snapshot.id,
            "source": payload.source,
            "source_agent_run_id": payload.source_agent_run_id,
            "supersedes_timeline_id": timeline.supersedes_timeline_id,
        },
    ))
    session.commit()
    return timeline_read(session, timeline)


def create_timeline_candidate(
    session: Session,
    project: Project,
    payload: CreateTimelineCandidate,
) -> dict:
    return _create_candidate(session, project, payload, None, "timeline.candidate.create")


def revise_timeline_candidate(
    session: Session,
    project: Project,
    timeline_id: str,
    payload: ReviseTimelineCandidate,
) -> dict:
    timeline = _require_timeline(session, project, timeline_id)
    return _create_candidate(session, project, payload, timeline, "timeline.candidate.revise")


def _error(code: str, path: str, message: str, **evidence) -> dict:
    return {"code": code, "path": path, "message": message, "evidence": evidence}


def _validate_items(session: Session, project: Project, timeline: Timeline) -> list[dict]:
    repository = _editor(session)
    errors: list[dict] = []
    items = repository.timeline_items(timeline.id)
    track_items: dict[str, list[TimelineItem]] = {"main_video": [], "audio": [], "subtitle": []}
    expected_types = {"main_video": "video", "audio": "audio", "subtitle": "subtitle"}
    duration_ms = project.duration_seconds * 1000
    seen_positions: set[tuple[str, int]] = set()
    for item in items:
        path = f"items.{item.track_type}.{item.sequence_number}"
        position = (item.track_type, item.sequence_number)
        if position in seen_positions:
            errors.append(_error("TIMELINE_SEQUENCE_DUPLICATE", path, "同一轨道的 sequence_number 必须唯一。"))
        seen_positions.add(position)
        track_items.setdefault(item.track_type, []).append(item)
        if item.timeline_out_ms <= item.timeline_in_ms:
            errors.append(_error("TIMELINE_RANGE_INVALID", path, "时间线出点必须晚于入点。"))
        if item.timeline_out_ms > duration_ms:
            errors.append(_error(
                "TIMELINE_OUTPUT_OVERRUN",
                path,
                "片段超出项目输出时长。",
                timeline_out_ms=item.timeline_out_ms,
                output_duration_ms=duration_ms,
            ))
        if item.track_type == "audio" and not timeline.track_config.get("audio_enabled"):
            errors.append(_error("AUDIO_TRACK_DISABLED", path, "音频轨已关闭，候选中不得包含音频片段。"))
        if item.track_type == "subtitle" and not timeline.track_config.get("subtitle_enabled"):
            errors.append(_error("SUBTITLE_TRACK_DISABLED", path, "字幕轨已关闭，候选中不得包含字幕片段。"))
        if not item.asset_id:
            if not item.gap_reason:
                errors.append(_error(
                    "TIMELINE_GAP_REASON_REQUIRED",
                    path,
                    "显式空位必须记录保留原因。",
                ))
            errors.append(_error(
                "TIMELINE_GAP_UNRESOLVED",
                path,
                "候选保留了显式空位，必须完成素材取舍后才能确认。",
                gap_reason=item.gap_reason,
            ))
            continue
        asset = repository.asset(item.asset_id)
        if not asset:
            errors.append(_error("TIMELINE_ASSET_NOT_FOUND", path, "引用素材不存在。", asset_id=item.asset_id))
            continue
        if asset.project_id != project.id:
            errors.append(_error("TIMELINE_ASSET_PROJECT_MISMATCH", path, "引用素材不属于当前项目。", asset_id=asset.id))
        if asset.snapshot_id != timeline.snapshot_id:
            errors.append(_error("TIMELINE_ASSET_SNAPSHOT_MISMATCH", path, "引用素材不属于当前活动快照。", asset_id=asset.id))
        if asset.state not in {"approved", "used"}:
            errors.append(_error(
                "TIMELINE_ASSET_NOT_APPROVED",
                path,
                "时间线只能引用 approved 或 used 素材。",
                asset_id=asset.id,
                asset_state=asset.state,
            ))
        if asset.asset_type != expected_types.get(item.track_type):
            errors.append(_error(
                "TIMELINE_ASSET_TYPE_MISMATCH",
                path,
                "素材类型与轨道类型不匹配。",
                asset_id=asset.id,
                asset_type=asset.asset_type,
                expected_type=expected_types.get(item.track_type),
            ))
        if item.source_in_ms is None or item.source_out_ms is None or item.source_out_ms <= item.source_in_ms:
            errors.append(_error("SOURCE_RANGE_INVALID", path, "素材源入点和出点必须形成有效区间。"))
            continue
        if asset.asset_type in {"video", "audio"} and asset.duration_ms is None:
            errors.append(_error("ASSET_DURATION_UNKNOWN", path, "视频或音频素材缺少已验证时长。", asset_id=asset.id))
        elif asset.duration_ms is not None and item.source_out_ms > asset.duration_ms:
            errors.append(_error(
                "SOURCE_RANGE_EXCEEDS_ASSET",
                path,
                "源出点超出素材真实时长。",
                source_out_ms=item.source_out_ms,
                asset_duration_ms=asset.duration_ms,
            ))
        source_duration = item.source_out_ms - item.source_in_ms
        timeline_duration = item.timeline_out_ms - item.timeline_in_ms
        if source_duration != timeline_duration:
            errors.append(_error(
                "TIMELINE_SPEED_CHANGE_UNDECLARED",
                path,
                "首期合同不隐式变速，源区间与时间线区间时长必须一致。",
                source_duration_ms=source_duration,
                timeline_duration_ms=timeline_duration,
            ))
    for track, rows in track_items.items():
        ordered = sorted(rows, key=lambda row: (row.timeline_in_ms, row.sequence_number))
        for previous, current in zip(ordered, ordered[1:]):
            if current.timeline_in_ms < previous.timeline_out_ms:
                errors.append(_error(
                    "TIMELINE_ITEMS_OVERLAP",
                    f"tracks.{track}",
                    "同一轨道片段不得重叠。",
                    previous_sequence=previous.sequence_number,
                    current_sequence=current.sequence_number,
                ))
    video_rows = sorted(track_items["main_video"], key=lambda row: row.timeline_in_ms)
    if not video_rows:
        errors.append(_error("MAIN_VIDEO_TRACK_EMPTY", "tracks.main_video", "主视频轨不能为空。"))
    else:
        cursor = 0
        for row in video_rows:
            if row.timeline_in_ms > cursor:
                errors.append(_error(
                    "MAIN_VIDEO_TRACK_GAP",
                    "tracks.main_video",
                    "主视频轨存在未声明的时间空洞。",
                    gap_in_ms=cursor,
                    gap_out_ms=row.timeline_in_ms,
                ))
            cursor = max(cursor, row.timeline_out_ms)
        if cursor < duration_ms:
            errors.append(_error(
                "MAIN_VIDEO_DURATION_INCOMPLETE",
                "tracks.main_video",
                "主视频轨未覆盖项目完整输出时长。",
                covered_until_ms=cursor,
                output_duration_ms=duration_ms,
            ))
    if timeline.track_config.get("audio_enabled") and not track_items["audio"]:
        errors.append(_error("AUDIO_TRACK_EMPTY", "tracks.audio", "音频轨已启用但没有音频素材。"))
    if timeline.track_config.get("subtitle_enabled") and not track_items["subtitle"]:
        errors.append(_error("SUBTITLE_TRACK_EMPTY", "tracks.subtitle", "字幕轨已启用但没有字幕素材。"))
    return errors


def validate_timeline(
    session: Session,
    project: Project,
    timeline_id: str,
    payload: ValidateTimeline,
) -> dict:
    receipt = _receipt(session, project.id, payload.command_id, "timeline.validate")
    if receipt:
        return timeline_read(session, _require_timeline(session, project, receipt.result_id))
    timeline = _require_timeline(session, project, timeline_id)
    _require_active_snapshot(session, project, timeline.snapshot_id)
    if timeline.status != "candidate":
        raise EditorConflictError("TIMELINE_NOT_CANDIDATE", "只有 candidate 时间线可以执行校验。")
    if timeline.row_version != payload.expected_row_version:
        raise EditorConflictError("TIMELINE_ROW_VERSION_MISMATCH", "时间线已变化，请刷新后重试。")
    errors = _validate_items(session, project, timeline)
    now = utc_now()
    timeline.validation_report = errors
    timeline.validated_at = now
    timeline.contract_hash = _hash(_timeline_contract(session, timeline))
    timeline.status = "candidate" if errors else "review"
    timeline.row_version += 1
    _save_receipt(session, project.id, payload.command_id, "timeline.validate", "timeline", timeline.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=timeline.snapshot_id,
        event_type="timeline.validation_failed.v1" if errors else "timeline.validated.v1",
        aggregate_type="timeline",
        aggregate_id=timeline.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Timeline contract validation completed.",
        data={"timeline_id": timeline.id, "error_count": len(errors), "contract_hash": timeline.contract_hash},
    ))
    session.commit()
    return timeline_read(session, timeline)


def confirm_timeline(
    session: Session,
    project: Project,
    timeline_id: str,
    payload: ConfirmTimeline,
) -> dict:
    repository = _editor(session)
    receipt = _receipt(session, project.id, payload.command_id, "timeline.confirm")
    if receipt:
        return timeline_read(session, _require_timeline(session, project, receipt.result_id))
    timeline = _require_timeline(session, project, timeline_id)
    _require_active_snapshot(session, project, timeline.snapshot_id)
    if timeline.status != "review" or timeline.validation_report:
        raise EditorConflictError("TIMELINE_NOT_READY_FOR_CONFIRMATION", "时间线必须先通过确定性校验。")
    if timeline.row_version != payload.expected_row_version:
        raise EditorConflictError("TIMELINE_ROW_VERSION_MISMATCH", "时间线已变化，请刷新后重试。")
    current_hash = _hash(_timeline_contract(session, timeline))
    if timeline.contract_hash != payload.expected_contract_hash or current_hash != payload.expected_contract_hash:
        raise EditorConflictError("TIMELINE_CONTRACT_HASH_MISMATCH", "时间线合同已变化，请重新校验。")
    if not payload.confirm_delivery_scope:
        raise EditorConflictError("TIMELINE_CONFIRMATION_REQUIRED", "必须明确确认当前剪辑范围。")
    now = utc_now()
    previous = repository.confirmed_timelines(project.id, exclude_id=timeline.id)
    for row in previous:
        row.status = "superseded"
        row.row_version += 1
    asset_ids = repository.timeline_asset_ids(timeline.id)
    for asset in repository.assets_by_ids(asset_ids):
        if asset.state == "approved":
            asset.state = "used"
            asset.row_version += 1
    timeline.status = "confirmed"
    timeline.confirmed_at = now
    timeline.row_version += 1
    transition_project(
        session,
        project,
        ProjectStateTrigger.TIMELINE_CONFIRMED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"timeline_id": timeline.id},
    )
    _save_receipt(session, project.id, payload.command_id, "timeline.confirm", "timeline", timeline.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=timeline.snapshot_id,
        event_type="timeline.confirmed.v1",
        aggregate_type="timeline",
        aggregate_id=timeline.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="User confirmed the exact timeline contract.",
        data={
            "timeline_id": timeline.id,
            "version_number": timeline.version_number,
            "contract_hash": timeline.contract_hash,
            "actor_id": payload.actor_id,
        },
    ))
    session.commit()
    return timeline_read(session, timeline)


def timeline_read(session: Session, timeline: Timeline) -> dict:
    repository = _editor(session)
    items = repository.timeline_items(timeline.id)
    assets = {
        asset.id: asset
        for asset in repository.assets_by_ids([item.asset_id for item in items if item.asset_id])
    } if any(item.asset_id for item in items) else {}
    result = {column.name: getattr(timeline, column.name) for column in timeline.__table__.columns}
    result["items"] = []
    for item in items:
        asset = assets.get(item.asset_id)
        row = {column.name: getattr(item, column.name) for column in item.__table__.columns if column.name not in {"timeline_id", "created_at"}}
        row["asset_state"] = asset.state if asset else None
        row["asset_type"] = asset.asset_type if asset else None
        row["asset_duration_ms"] = asset.duration_ms if asset else None
        result["items"].append(row)
    return result


def editor_workspace(session: Session, project: Project) -> dict:
    repository = _editor(session)
    quality = quality_review_view(session, project)
    assets = []
    if project.active_snapshot_id:
        rows = repository.available_assets(project.id, project.active_snapshot_id)
        node_ids = [row.dag_node_id for row in rows if row.dag_node_id]
        nodes = {
            node.id: node
            for node in repository.dag_nodes_by_ids(node_ids)
        } if node_ids else {}
        assets = [{
            "id": row.id,
            "snapshot_id": row.snapshot_id,
            "dag_node_id": row.dag_node_id,
            "node_key": nodes[row.dag_node_id].node_key if row.dag_node_id in nodes else None,
            "asset_type": row.asset_type,
            "role": row.role,
            "duration_ms": row.duration_ms,
            "width": row.width,
            "height": row.height,
            "state": row.state,
            "content_hash": row.content_hash,
        } for row in rows]
    timelines = repository.timeline_history(project.id)
    if project.status == "quality_review":
        next_action = {
            "code": "APPROVE_QUALITY_STAGE" if quality["stage_ready"] else "REVIEW_REQUIRED_ASSETS",
            "label": "确认进入剪辑" if quality["stage_ready"] else "先完成必需素材审核",
        }
    elif not timelines:
        next_action = {"code": "CREATE_TIMELINE_CANDIDATE", "label": "选择素材并创建剪辑候选"}
    elif timelines[0].status == "candidate":
        next_action = {"code": "VALIDATE_TIMELINE", "label": "校验最新时间线候选"}
    elif timelines[0].status == "review":
        next_action = {"code": "CONFIRM_TIMELINE", "label": "确认剪辑合同"}
    elif timelines[0].status == "confirmed":
        next_action = {"code": "PREPARE_DELIVERY", "label": "剪辑合同已确认，等待交付实现"}
    else:
        next_action = {"code": "REVISE_TIMELINE", "label": "创建新的时间线修订"}
    return {
        "project_id": project.id,
        "project_title": project.title,
        "project_status": project.status,
        "active_snapshot_id": project.active_snapshot_id,
        "duration_ms": project.duration_seconds * 1000,
        "aspect_ratio": project.aspect_ratio,
        "audio_mode": project.audio_mode,
        "quality_stage_ready": quality["stage_ready"],
        "quality_output_gaps": quality["output_gaps"],
        "available_assets": assets,
        "timelines": [timeline_read(session, row) for row in timelines],
        "next_action": next_action,
    }
