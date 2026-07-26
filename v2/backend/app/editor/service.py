from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentGatewayError
from ..db.models import (
    AgentInputManifest,
    AgentRun,
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
    GenerateEditorTimeline,
    RetryEditorTimeline,
    ReviseTimelineCandidate,
    ValidateTimeline,
)
from .agent_gateway import EditorAssistantGateway, EditorAssistantResult, EditorSelection


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


def _run_dict(run: AgentRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "agent_role": run.agent_role,
        "status": run.status,
        "input_manifest_id": run.input_manifest_id,
        "model_provider": run.model_provider,
        "model_name": run.model_name,
        "production_config_version_id": run.production_config_version_id,
        "model_config_version_id": run.model_config_version_id,
        "provider_config_version_id": run.provider_config_version_id,
        "prompt_contract_version": run.prompt_contract_version,
        "output_schema_version": run.output_schema_version,
        "provider_request_id": run.provider_request_id,
        "token_usage": run.token_usage or {},
        "error_code": run.error_code,
        "error_detail": run.error_detail,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


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
        "contract_version": "v2.timeline-contract.v2",
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
        if not run or run.project_id != project.id or run.agent_role != "editor" or run.status != "succeeded":
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


def _editor_manifest(session: Session, project: Project, snapshot: ProductionSnapshot) -> dict[str, Any]:
    repository = _editor(session)
    plan = repository.active_plan(project.id)
    if not plan or plan.id != snapshot.plan_version_id:
        raise EditorConflictError("EDITOR_ACTIVE_PLAN_MISMATCH", "当前活动方案与制作快照不一致。")
    assets = repository.available_assets(project.id, snapshot.id)
    video_assets = [asset for asset in assets if asset.asset_type == "video"]
    audio_assets = [asset for asset in assets if asset.asset_type == "audio"]
    subtitle_assets = [asset for asset in assets if asset.asset_type == "subtitle"]
    if not video_assets:
        raise EditorConflictError("EDITOR_APPROVED_VIDEO_REQUIRED", "剪辑助理至少需要一段已批准视频。")
    if project.audio_mode == "voiceover" and len(audio_assets) != 1:
        raise EditorConflictError(
            "EDITOR_APPROVED_VOICEOVER_NOT_EXACT",
            "旁白项目进入剪辑前必须精确具有一份已批准配音素材。",
        )
    if project.audio_mode == "voiceover" and len(subtitle_assets) != 1:
        raise EditorConflictError(
            "EDITOR_APPROVED_SUBTITLE_NOT_EXACT",
            "旁白项目进入剪辑前必须精确具有一份已批准字幕素材。",
        )
    nodes = {
        node.id: node for node in repository.dag_nodes_by_ids(
            [asset.dag_node_id for asset in video_assets if asset.dag_node_id]
        )
    }
    shots = {shot.id: shot for shot in repository.shots(plan.id)}
    reports_by_asset: dict[str, list[str]] = {}
    for report in repository.qc_reports([asset.id for asset in [*video_assets, *audio_assets, *subtitle_assets]]):
        reports_by_asset.setdefault(report.asset_id, []).append(report.id)
    approved_assets = []
    for asset in video_assets:
        node = nodes.get(asset.dag_node_id)
        shot = shots.get(node.shot_id) if node and node.shot_id else None
        if not node or not shot:
            raise EditorConflictError("EDITOR_ASSET_SHOT_UNRESOLVED", f"素材 {asset.id} 无法精确解析到当前分镜。")
        qc_ids = reports_by_asset.get(asset.id, [])
        if not qc_ids:
            raise EditorConflictError("EDITOR_ASSET_QC_EVIDENCE_MISSING", f"素材 {asset.id} 缺少权威 QC 报告。")
        approved_assets.append({
            "id": asset.id,
            "asset_type": asset.asset_type,
            "duration_ms": asset.duration_ms,
            "content_hash": asset.content_hash,
            "dag_node_id": asset.dag_node_id,
            "node_key": node.node_key,
            "shot_id": shot.id,
            "shot_code": shot.shot_code,
            "shot_sequence_number": shot.sequence_number,
            "shot_duration_ms": shot.duration_ms,
            "qc_report_ids": qc_ids,
        })
    approved_assets.sort(key=lambda item: (item["shot_sequence_number"], item["id"]))
    approved_audio_assets = []
    for asset in audio_assets:
        qc_ids = reports_by_asset.get(asset.id, [])
        if not qc_ids:
            raise EditorConflictError("EDITOR_AUDIO_QC_EVIDENCE_MISSING", f"配音素材 {asset.id} 缺少权威 QC 报告。")
        approved_audio_assets.append({
            "id": asset.id,
            "asset_type": asset.asset_type,
            "duration_ms": asset.duration_ms,
            "content_hash": asset.content_hash,
            "dag_node_id": asset.dag_node_id,
            "qc_report_ids": qc_ids,
        })
    approved_subtitle_assets = []
    for asset in subtitle_assets:
        qc_ids = reports_by_asset.get(asset.id, [])
        if not qc_ids:
            raise EditorConflictError("EDITOR_SUBTITLE_QC_EVIDENCE_MISSING", f"字幕素材 {asset.id} 缺少权威 QC 报告。")
        approved_subtitle_assets.append({
            "id": asset.id,
            "asset_type": asset.asset_type,
            "duration_ms": asset.duration_ms,
            "content_hash": asset.content_hash,
            "dag_node_id": asset.dag_node_id,
            "qc_report_ids": qc_ids,
        })
    return {
        "contract_version": "editor-assistant-input.v1",
        "project_id": project.id,
        "snapshot_id": snapshot.id,
        "plan_version_id": plan.id,
        "shot_plan_version": plan.contract_schema_version,
        "creative_brief_version": plan.creative_brief.get("schema_version", "creative-brief.v1"),
        "approved_asset_ids": [item["id"] for item in approved_assets],
        "qc_report_ids": sorted({report_id for item in approved_assets for report_id in item["qc_report_ids"]}),
        "approved_assets": approved_assets,
        "approved_audio_assets": approved_audio_assets,
        "approved_subtitle_assets": approved_subtitle_assets,
        "delivery_contract": {
            "duration_ms": project.duration_seconds * 1000,
            "aspect_ratio": project.aspect_ratio,
            "output_spec": snapshot.output_spec,
        },
        "audio_policy": {"mode": project.audio_mode},
        "subtitle_policy": {"enabled": bool(approved_subtitle_assets)},
        "timeline_policy_version": "timeline-policy.v1",
    }


def _validate_editor_output(output, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    target = manifest["delivery_contract"]["duration_ms"]
    if output.duration_ms != target:
        errors.append({"code": "EDITOR_DURATION_MISMATCH", "expected": target, "actual": output.duration_ms})
    assets = {item["id"]: item for item in manifest["approved_assets"]}
    expected_codes = [f"ITEM_{index:03d}" for index in range(1, len(output.video_items) + 1)]
    if [item.timeline_item_code for item in output.video_items] != expected_codes:
        errors.append({"code": "EDITOR_ITEM_CODE_SEQUENCE_INVALID"})
    cursor = 0
    seen_assets: set[str] = set()
    for index, item in enumerate(output.video_items):
        path = f"video_items.{index}"
        if item.timeline_in_ms != cursor or item.timeline_out_ms <= item.timeline_in_ms:
            errors.append({"code": "EDITOR_TIMELINE_NOT_CONTIGUOUS", "path": path, "expected_in_ms": cursor})
        cursor = item.timeline_out_ms
        if item.source_asset_id is None:
            if not item.gap_reason or item.shot_code is not None or item.qc_report_ids or item.source_in_ms is not None or item.source_out_ms is not None:
                errors.append({"code": "EDITOR_GAP_CONTRACT_INVALID", "path": path})
            continue
        asset = assets.get(item.source_asset_id)
        if asset is None:
            errors.append({"code": "EDITOR_ASSET_NOT_APPROVED", "path": path, "asset_id": item.source_asset_id})
            continue
        if item.source_asset_id in seen_assets:
            errors.append({"code": "EDITOR_ASSET_REUSE_NOT_ALLOWED", "path": path, "asset_id": item.source_asset_id})
        seen_assets.add(item.source_asset_id)
        if item.gap_reason is not None or item.shot_code != asset["shot_code"]:
            errors.append({"code": "EDITOR_ASSET_SHOT_MISMATCH", "path": path})
        if set(item.qc_report_ids) != set(asset["qc_report_ids"]) or len(item.qc_report_ids) != len(set(item.qc_report_ids)):
            errors.append({"code": "EDITOR_QC_EVIDENCE_MISMATCH", "path": path})
        if item.source_in_ms is None or item.source_out_ms is None or item.source_out_ms <= item.source_in_ms:
            errors.append({"code": "EDITOR_SOURCE_RANGE_INVALID", "path": path})
        elif item.source_out_ms > (asset["duration_ms"] or 0):
            errors.append({"code": "EDITOR_SOURCE_RANGE_EXCEEDS_ASSET", "path": path})
        elif item.source_out_ms - item.source_in_ms != item.timeline_out_ms - item.timeline_in_ms:
            errors.append({"code": "EDITOR_SPEED_CHANGE_UNDECLARED", "path": path})
    if cursor != target:
        errors.append({"code": "EDITOR_TIMELINE_DURATION_INCOMPLETE", "expected": target, "actual": cursor})
    if manifest["audio_policy"]["mode"] == "off" and output.audio_cues:
        errors.append({"code": "EDITOR_AUDIO_DISABLED"})
    if not manifest["subtitle_policy"]["enabled"] and output.subtitle_cues:
        errors.append({"code": "EDITOR_SUBTITLE_DISABLED"})
    return errors


def _execute_editor(
    session: Session,
    project: Project,
    manifest: AgentInputManifest,
    selection: EditorSelection,
    gateway: EditorAssistantGateway,
    *,
    retry_of_agent_run_id: str | None = None,
) -> dict:
    repository = _editor(session)
    run = AgentRun(
        project_id=project.id,
        agent_role="editor",
        status="running",
        input_manifest_id=manifest.id,
        model_provider=selection.model_provider,
        model_name=selection.model_name,
        production_config_version_id=selection.production_config_version_id,
        model_config_version_id=selection.model_config_version_id,
        provider_config_version_id=selection.provider_config_version_id,
        prompt_contract_version=selection.prompt_contract_version,
        output_schema_version=selection.output_schema_version,
        started_at=utc_now(),
    )
    repository.add(run)
    repository.flush()
    _event(session, ProjectEvent(
        project_id=project.id, snapshot_id=manifest.payload["snapshot_id"],
        event_type="agent.run_created.v1", aggregate_type="agent_run", aggregate_id=run.id,
        actor_type="agent", actor_id="editor-assistant", message="剪辑助理运行已开始",
        data={"agent_role": "editor", "retry_of_agent_run_id": retry_of_agent_run_id},
    ))
    try:
        result: EditorAssistantResult = gateway.invoke(selection, manifest.payload)
        errors = _validate_editor_output(result.output, manifest.payload)
        if errors:
            raise AgentGatewayError(
                "EDITOR_OUTPUT_CONTRACT_INVALID",
                "剪辑助理返回了不满足当前素材和时间线规则的候选。",
                raw_output=result.raw_output,
                diagnostics=errors,
            )
        run.status = "succeeded"
        run.raw_output = result.raw_output
        run.provider_request_id = result.provider_request_id
        run.token_usage = result.token_usage
        run.finished_at = utc_now()
        items = []
        for sequence, item in enumerate(result.output.video_items, 1):
            items.append({
                "track_type": "main_video",
                "sequence_number": sequence,
                "asset_id": item.source_asset_id,
                "label": item.shot_code or "待补素材",
                "gap_reason": item.gap_reason,
                "source_in_ms": item.source_in_ms,
                "source_out_ms": item.source_out_ms,
                "timeline_in_ms": item.timeline_in_ms,
                "timeline_out_ms": item.timeline_out_ms,
                "transform": {
                    "fit": "cover",
                    "transition_in": {"type": "cut", "duration_ms": 0},
                    "transition_out": {"type": "cut", "duration_ms": 0},
                    "editor_assistant": {
                        "timeline_item_code": item.timeline_item_code,
                        "shot_code": item.shot_code,
                        "selection_reason": item.selection_reason,
                        "qc_report_ids": item.qc_report_ids,
                    },
                },
            })
        approved_audio = manifest.payload.get("approved_audio_assets") or []
        if approved_audio:
            audio = approved_audio[0]
            audio_duration = min(
                int(audio["duration_ms"]),
                int(manifest.payload["delivery_contract"]["duration_ms"]),
            )
            items.append({
                "track_type": "audio",
                "sequence_number": 1,
                "asset_id": audio["id"],
                "label": "已批准旁白",
                "gap_reason": None,
                "source_in_ms": 0,
                "source_out_ms": audio_duration,
                "timeline_in_ms": 0,
                "timeline_out_ms": audio_duration,
                "transform": {
                    "mix": "voiceover",
                    "volume_envelope": [
                        {"time_ms": 0, "gain_db": 0.0},
                        {"time_ms": audio_duration, "gain_db": 0.0},
                    ],
                    "qc_report_ids": audio["qc_report_ids"],
                    "source": "frozen_approved_voiceover",
                },
            })
        approved_subtitles = manifest.payload.get("approved_subtitle_assets") or []
        if approved_subtitles:
            subtitle = approved_subtitles[0]
            subtitle_duration = min(
                int(subtitle["duration_ms"]),
                int(manifest.payload["delivery_contract"]["duration_ms"]),
            )
            items.append({
                "track_type": "subtitle",
                "sequence_number": 1,
                "asset_id": subtitle["id"],
                "label": "已批准字幕",
                "gap_reason": None,
                "source_in_ms": 0,
                "source_out_ms": subtitle_duration,
                "timeline_in_ms": 0,
                "timeline_out_ms": subtitle_duration,
                "transform": {
                    "render": "burn_in",
                    "qc_report_ids": subtitle["qc_report_ids"],
                    "source": "frozen_approved_subtitles",
                },
            })
        payload = CreateTimelineCandidate.model_validate({
            "command_id": f"editor-run-{run.id}",
            "actor_id": "editor-assistant",
            "expected_snapshot_id": manifest.payload["snapshot_id"],
            "source": "editor_assistant",
            "source_agent_run_id": run.id,
            "track_config": {
                "audio_enabled": bool(approved_audio),
                "subtitle_enabled": bool(approved_subtitles),
            },
            "items": items,
        })
        timeline = _create_candidate(session, project, payload, None, "timeline.candidate.create")
        run = repository.agent_run(run.id)
        run.parsed_candidate_id = timeline["id"]
        _event(session, ProjectEvent(
            project_id=project.id, snapshot_id=manifest.payload["snapshot_id"],
            event_type="editor.timeline_candidate_created.v1", aggregate_type="timeline", aggregate_id=timeline["id"],
            actor_type="agent", actor_id="editor-assistant", message="剪辑助理候选已生成，等待用户取舍",
            data={"timeline_id": timeline["id"], "agent_run_id": run.id, "has_gaps": any(item.source_asset_id is None for item in result.output.video_items)},
        ))
        session.commit()
        return timeline
    except AgentGatewayError as exc:
        run.status = "failed"
        run.error_code = exc.code
        run.error_detail = str(exc)
        run.raw_output = exc.raw_output
        run.finished_at = utc_now()
        _event(session, ProjectEvent(
            project_id=project.id, snapshot_id=manifest.payload["snapshot_id"],
            event_type="agent.run_failed.v1", aggregate_type="agent_run", aggregate_id=run.id,
            actor_type="agent", actor_id="editor-assistant", message="剪辑助理运行失败",
            data={"agent_role": "editor", "error_code": exc.code, "diagnostics": exc.diagnostics},
        ))
        session.commit()
        raise


def generate_editor_timeline(
    session: Session,
    project: Project,
    payload: GenerateEditorTimeline,
    gateway: EditorAssistantGateway,
) -> dict:
    if project.status != "editing":
        raise EditorConflictError("EDITOR_STAGE_NOT_READY", "项目必须先完成质量阶段并进入剪辑。")
    snapshot = _require_active_snapshot(session, project, payload.expected_snapshot_id)
    repository = _editor(session)
    if repository.has_timeline(project.id):
        raise EditorConflictError("TIMELINE_REVISION_REQUIRED", "项目已有时间线，请从现有版本创建人工修订。")
    selection = gateway.select(session, snapshot.production_config_version_id)
    manifest_payload = _editor_manifest(session, project, snapshot)
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=repository.active_plan(project.id).requirement_version_id,
        message_ids=[],
        decision_ids=[],
        attachment_binding_ids=[],
        system_config_version=selection.production_config_version_id,
        input_hash=_hash(manifest_payload),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    return _execute_editor(session, project, manifest, selection, gateway)


def retry_editor_timeline(
    session: Session,
    project: Project,
    payload: RetryEditorTimeline,
    gateway: EditorAssistantGateway,
) -> dict:
    if not payload.confirm_model_cost:
        raise EditorConflictError("EDITOR_RETRY_COST_CONFIRMATION_REQUIRED", "必须明确确认本次模型调用费用。")
    repository = _editor(session)
    failed = repository.agent_run(payload.failed_agent_run_id)
    if not failed or failed.project_id != project.id or failed.agent_role != "editor" or failed.status != "failed":
        raise EditorConflictError("EDITOR_FAILED_RUN_INVALID", "指定记录不是当前项目可重跑的剪辑助理失败运行。")
    latest = repository.latest_editor_run(project.id, project.active_snapshot_id or "")
    if not latest or latest.id != failed.id:
        raise EditorConflictError("EDITOR_FAILED_RUN_NOT_LATEST", "只能重跑当前快照最近一次失败的剪辑助理运行。")
    manifest = repository.input_manifest(failed.input_manifest_id)
    if not manifest or manifest.payload.get("snapshot_id") != project.active_snapshot_id:
        raise EditorConflictError("EDITOR_RETRY_MANIFEST_STALE", "原剪辑输入已不再对应当前活动快照。")
    selection = gateway.select(session, failed.production_config_version_id)
    if (
        selection.model_config_version_id != failed.model_config_version_id
        or selection.provider_config_version_id != failed.provider_config_version_id
        or selection.prompt_contract_version != failed.prompt_contract_version
        or selection.output_schema_version != failed.output_schema_version
    ):
        raise EditorConflictError("EDITOR_RETRY_CONTRACT_CHANGED", "剪辑助理配置或合同已变化，不能伪装成精确重跑。")
    return _execute_editor(session, project, manifest, selection, gateway, retry_of_agent_run_id=failed.id)


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


def _validate_item_transform(item: TimelineItem, path: str, duration_ms: int) -> list[dict]:
    errors: list[dict] = []
    transform = item.transform if isinstance(item.transform, dict) else {}
    if item.track_type == "main_video":
        for key in ("transition_in", "transition_out"):
            transition = transform.get(key)
            if transition is None:
                continue
            if not isinstance(transition, dict) or transition.get("type") not in {"cut", "fade"}:
                errors.append(_error("VIDEO_TRANSITION_INVALID", f"{path}.transform.{key}", "视频转场必须明确使用 cut 或 fade。"))
                continue
            transition_duration = transition.get("duration_ms")
            if (
                not isinstance(transition_duration, int)
                or transition_duration < 0
                or transition_duration > min(2000, duration_ms // 2)
                or (transition["type"] == "cut" and transition_duration != 0)
                or (transition["type"] == "fade" and transition_duration < 100)
            ):
                errors.append(_error(
                    "VIDEO_TRANSITION_DURATION_INVALID",
                    f"{path}.transform.{key}.duration_ms",
                    "转场时长必须与类型一致，且不能超过片段时长的一半或 2000ms。",
                    clip_duration_ms=duration_ms,
                    transition_duration_ms=transition_duration,
                ))
    if item.track_type == "audio":
        envelope = transform.get("volume_envelope")
        if envelope is not None:
            if not isinstance(envelope, list) or len(envelope) < 2 or len(envelope) > 64:
                errors.append(_error("AUDIO_VOLUME_ENVELOPE_INVALID", f"{path}.transform.volume_envelope", "音量包络必须包含 2 到 64 个关键点。"))
            else:
                previous_time = -1
                for index, point in enumerate(envelope):
                    point_path = f"{path}.transform.volume_envelope.{index}"
                    if not isinstance(point, dict):
                        errors.append(_error("AUDIO_VOLUME_ENVELOPE_POINT_INVALID", point_path, "音量关键点必须是对象。"))
                        continue
                    time_ms = point.get("time_ms")
                    gain_db = point.get("gain_db")
                    if not isinstance(time_ms, int) or time_ms < 0 or time_ms > duration_ms or time_ms <= previous_time:
                        errors.append(_error("AUDIO_VOLUME_ENVELOPE_TIME_INVALID", f"{point_path}.time_ms", "音量关键点时间必须严格递增且位于片段范围内。"))
                    else:
                        previous_time = time_ms
                    if not isinstance(gain_db, (int, float)) or gain_db < -60 or gain_db > 12:
                        errors.append(_error("AUDIO_VOLUME_ENVELOPE_GAIN_INVALID", f"{point_path}.gain_db", "音量关键点增益必须位于 -60dB 到 12dB。"))
                if (
                    isinstance(envelope[0], dict)
                    and isinstance(envelope[-1], dict)
                    and (envelope[0].get("time_ms") != 0 or envelope[-1].get("time_ms") != duration_ms)
                ):
                    errors.append(_error(
                        "AUDIO_VOLUME_ENVELOPE_BOUNDARY_INVALID",
                        f"{path}.transform.volume_envelope",
                        "音量包络必须从片段零点开始并覆盖到片段终点。",
                        clip_duration_ms=duration_ms,
                    ))
    return errors


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
        errors.extend(_validate_item_transform(item, path, timeline_duration))
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
    latest_editor_run = repository.latest_editor_run(project.id, project.active_snapshot_id) if project.active_snapshot_id else None
    if project.status == "quality_review":
        next_action = {
            "code": "APPROVE_QUALITY_STAGE" if quality["stage_ready"] else "REVIEW_REQUIRED_ASSETS",
            "label": "确认进入剪辑" if quality["stage_ready"] else "先完成必需素材审核",
        }
    elif not timelines and latest_editor_run and latest_editor_run.status == "failed":
        next_action = {"code": "RETRY_EDITOR_ASSISTANT", "label": "确认费用并重跑剪辑助理"}
    elif not timelines:
        next_action = {"code": "GENERATE_EDITOR_TIMELINE", "label": "让剪辑助理生成草案"}
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
        "latest_editor_run": _run_dict(latest_editor_run),
        "next_action": next_action,
    }
