from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentGatewayError
from ..core.config import RUNTIME_ROOT
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
from ..delivery.renderer import (
    LocalFFmpegRenderer,
    LocalRenderAudioInput,
    LocalRenderError,
    LocalRenderInput,
    LocalRenderRequest,
    LocalRenderSubtitleInput,
    inspect_local_ffmpeg,
)
from ..delivery.service import DeliveryConflictError, validate_local_render_manifest
from ..repositories import (
    EditorRepository,
    SqlAlchemyCommandRepository,
    SqlAlchemyEditorRepository,
    SqlAlchemyEventRepository,
)
from ..orchestration.project_transitions import ProjectStateTrigger, transition_project
from ..quality.service import (
    QualityConflictError,
    measure_program_audio,
    probe_media,
    quality_review_view,
    resolve_local_asset_path,
    sha256_file,
)
from .contracts import (
    ApproveQualityStage,
    ConfirmTimeline,
    CreateTimelineCandidate,
    GenerateEditorTimeline,
    RetryEditorTimeline,
    RenderTimelinePreview,
    ReviewTimelinePreview,
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
        "contract_version": "v2.timeline-contract.v3",
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
    from ..production_profiles import profile_manifest

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
        "contract_version": "editor-assistant-input.v2",
        "project_id": project.id,
        "production_profile": profile_manifest(session, project.id),
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
                    "playback": {"mode": "trim"},
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
        mix = transform.get("mix", "voiceover")
        if mix not in {"voiceover", "background_music"}:
            errors.append(_error("AUDIO_MIX_ROLE_INVALID", f"{path}.transform.mix", "音频片段必须明确属于 voiceover 或 background_music。"))
        playback = transform.get("playback", {"mode": "trim"})
        if (
            not isinstance(playback, dict)
            or playback.get("mode") not in {"trim", "loop"}
            or set(playback) != {"mode"}
        ):
            errors.append(_error("AUDIO_PLAYBACK_INVALID", f"{path}.transform.playback", "音频播放方式必须明确使用 trim 或 loop。"))
        if mix == "background_music":
            rights = transform.get("rights")
            if (
                not isinstance(rights, dict)
                or rights.get("confirmed") is not True
                or rights.get("basis") not in {"owned", "licensed", "royalty_free"}
                or not isinstance(rights.get("evidence"), str)
                or not rights["evidence"].strip()
                or len(rights["evidence"]) > 500
            ):
                errors.append(_error(
                    "BGM_RIGHTS_AUTHORIZATION_REQUIRED",
                    f"{path}.transform.rights",
                    "背景音乐必须冻结用户确认的权利依据和证据。",
                ))
            ducking = transform.get("ducking")
            if not isinstance(ducking, dict) or not isinstance(ducking.get("enabled"), bool):
                errors.append(_error("BGM_DUCKING_INVALID", f"{path}.transform.ducking", "背景音乐必须明确是否按旁白区间自动压低。"))
            elif ducking["enabled"]:
                if (
                    not isinstance(ducking.get("reduction_db"), (int, float))
                    or ducking["reduction_db"] < -24
                    or ducking["reduction_db"] > -3
                    or not isinstance(ducking.get("attack_ms"), int)
                    or ducking["attack_ms"] < 0
                    or ducking["attack_ms"] > 1000
                    or not isinstance(ducking.get("release_ms"), int)
                    or ducking["release_ms"] < 0
                    or ducking["release_ms"] > 2000
                    or not isinstance(ducking.get("regions"), list)
                ):
                    errors.append(_error("BGM_DUCKING_PARAMETERS_INVALID", f"{path}.transform.ducking", "自动压低参数或冻结旁白区间无效。"))
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
    seen_main_video_assets: dict[str, int] = {}
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
        if item.track_type == "main_video":
            previous_sequence = seen_main_video_assets.get(asset.id)
            if previous_sequence is not None:
                errors.append(_error(
                    "TIMELINE_VIDEO_ASSET_REUSE_NOT_ALLOWED",
                    path,
                    "主画面素材不能重复引用来填补时长缺口；请选择未使用素材或返回生产流程补充镜头。",
                    asset_id=asset.id,
                    previous_sequence_number=previous_sequence,
                ))
            else:
                seen_main_video_assets[asset.id] = item.sequence_number
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
        playback_mode = ((item.transform or {}).get("playback") or {}).get("mode", "trim")
        if source_duration != timeline_duration and not (
            item.track_type == "audio"
            and playback_mode == "loop"
            and source_duration < timeline_duration
        ):
            errors.append(_error(
                "TIMELINE_SPEED_CHANGE_UNDECLARED",
                path,
                "除显式循环的背景音乐外，源区间与时间线区间时长必须一致。",
                source_duration_ms=source_duration,
                timeline_duration_ms=timeline_duration,
            ))
    for track, rows in track_items.items():
        ordered = sorted(rows, key=lambda row: (row.timeline_in_ms, row.sequence_number))
        for previous, current in zip(ordered, ordered[1:]):
            if current.timeline_in_ms < previous.timeline_out_ms:
                if track == "audio":
                    previous_mix = (previous.transform or {}).get("mix", "voiceover")
                    current_mix = (current.transform or {}).get("mix", "voiceover")
                    if {previous_mix, current_mix} == {"voiceover", "background_music"}:
                        continue
                errors.append(_error(
                    "TIMELINE_ITEMS_OVERLAP",
                    f"tracks.{track}",
                    "同一轨道片段不得重叠。",
                    previous_sequence=previous.sequence_number,
                    current_sequence=current.sequence_number,
                ))
    audio_rows = track_items["audio"]
    voiceover_rows = [row for row in audio_rows if (row.transform or {}).get("mix", "voiceover") == "voiceover"]
    for row in [item for item in audio_rows if (item.transform or {}).get("mix") == "background_music"]:
        ducking = (row.transform or {}).get("ducking") or {}
        if not ducking.get("enabled"):
            continue
        expected_regions = [
            {
                "start_ms": max(voice.timeline_in_ms, row.timeline_in_ms) - row.timeline_in_ms,
                "end_ms": min(voice.timeline_out_ms, row.timeline_out_ms) - row.timeline_in_ms,
            }
            for voice in voiceover_rows
            if max(voice.timeline_in_ms, row.timeline_in_ms) < min(voice.timeline_out_ms, row.timeline_out_ms)
        ]
        if ducking.get("regions") != expected_regions:
            errors.append(_error(
                "BGM_DUCKING_REGIONS_STALE",
                f"items.audio.{row.sequence_number}.transform.ducking.regions",
                "背景音乐冻结的压低区间必须与当前旁白片段精确一致。",
                expected_regions=expected_regions,
                actual_regions=ducking.get("regions"),
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


def _preview_dimensions(timeline: Timeline, project: Project) -> tuple[int, int, int]:
    output_spec = timeline.output_spec if isinstance(timeline.output_spec, dict) else {}
    ratio_parts = str(project.aspect_ratio).split(":")
    if len(ratio_parts) == 2 and all(part.isdigit() and int(part) > 0 for part in ratio_parts):
        source_width, source_height = int(ratio_parts[0]), int(ratio_parts[1])
    else:
        source_width = int(output_spec.get("width") or 16)
        source_height = int(output_spec.get("height") or 9)
    scale = 640 / max(source_width, source_height)
    width = max(2, round(source_width * scale / 2) * 2)
    height = max(2, round(source_height * scale / 2) * 2)
    fps = min(24, max(12, int(output_spec.get("fps") or 24)))
    return width, height, fps


def _preview_key(timeline: Timeline, quality_profile: str, ffmpeg_version: str) -> str:
    return _hash({
        "schema_version": "editor-preview.v1",
        "timeline_id": timeline.id,
        "timeline_contract_hash": timeline.contract_hash,
        "quality_profile": quality_profile,
        "ffmpeg_version": ffmpeg_version,
    })


def _preview_path(project: Project, preview_key: str):
    return RUNTIME_ROOT / "cache" / "editor-previews" / project.id / f"{preview_key}.mp4"


def _preview_response(
    timeline: Timeline,
    project: Project,
    payload: RenderTimelinePreview,
    *,
    validation_report: list[dict] | None = None,
    preview_key: str | None = None,
    cached: bool = False,
    content_hash: str | None = None,
    byte_size: int | None = None,
    quality_report: dict | None = None,
) -> dict:
    width, height, fps = _preview_dimensions(timeline, project)
    ready = preview_key is not None and not validation_report
    return {
        "schema_version": "editor-preview.v1",
        "state": "ready" if ready else "blocked",
        "timeline_id": timeline.id,
        "timeline_version_number": timeline.version_number,
        "timeline_contract_hash": timeline.contract_hash,
        "quality_profile": payload.quality_profile,
        "width": width,
        "height": height,
        "fps": fps,
        "duration_ms": project.duration_seconds * 1000,
        "cached": cached,
        "preview_key": preview_key,
        "content_url": (
            f"/api/v1/projects/{project.id}/timelines/{timeline.id}/previews/{preview_key}/content"
            if ready else None
        ),
        "content_hash": content_hash,
        "byte_size": byte_size,
        "validation_report": validation_report or [],
        "quality_report": quality_report,
    }


def _preview_output_validation(path: Path, width: int, height: int, duration_ms: int) -> dict | None:
    try:
        media_probe = probe_media(path, "video")
    except (QualityConflictError, OSError, ValueError) as exc:
        return _error(
            getattr(exc, "code", "PREVIEW_OUTPUT_PROBE_FAILED"),
            "preview.output",
            f"低清预览文件探测失败：{exc}",
        )
    if (
        media_probe.get("mime_type") != "video/mp4"
        or media_probe.get("width") != width
        or media_probe.get("height") != height
        or not isinstance(media_probe.get("duration_ms"), int)
        or abs(media_probe["duration_ms"] - duration_ms) > 250
    ):
        return _error(
            "PREVIEW_OUTPUT_CONTRACT_INVALID",
            "preview.output",
            "低清预览输出的格式、尺寸或时长与冻结预览合同不一致。",
            expected_width=width,
            expected_height=height,
            expected_duration_ms=duration_ms,
            actual=media_probe,
        )
    return None


def _preview_black_segments(path: Path, ffmpeg_path: Path) -> tuple[list[dict], dict | None]:
    try:
        result = subprocess.run(
            [
                str(ffmpeg_path),
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-vf",
                "blackdetect=d=0.5:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "NUL",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], {"error": str(exc)}
    if result.returncode != 0:
        return [], {"return_code": result.returncode}
    segments = []
    for start, end, duration in re.findall(
        r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)\s+black_duration:(\d+(?:\.\d+)?)",
        result.stderr,
    ):
        segments.append({
            "start_ms": round(float(start) * 1000),
            "end_ms": round(float(end) * 1000),
            "duration_ms": round(float(duration) * 1000),
        })
    return segments, None


def _preview_audio_duration(path: Path, ffmpeg_path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                str(ffmpeg_path),
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-progress",
                "pipe:1",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "NUL",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "analysis_failed", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "analysis_failed", "return_code": result.returncode}
    timestamps = re.findall(r"^out_time=(\d+):(\d+):(\d+(?:\.\d+)?)$", result.stdout, re.MULTILINE)
    if not timestamps:
        return {"status": "analysis_failed", "return_code": result.returncode}
    hours, minutes, seconds = timestamps[-1]
    duration_ms = round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)
    return {"status": "measured", "duration_ms": duration_ms}


def _preview_quality_report(
    path: Path,
    timeline: Timeline,
    ffmpeg_path: Path,
    duration_ms: int,
) -> dict:
    checks: list[dict] = [{
        "code": "PREVIEW_OUTPUT_TECHNICAL_CONTRACT_PASSED",
        "state": "passed",
        "message": "低清预览的 MP4 格式、画幅和时长符合冻结合同。",
        "evidence": {"duration_ms": duration_ms},
    }]
    black_segments, black_error = _preview_black_segments(path, ffmpeg_path)
    if black_error:
        checks.append({
            "code": "PREVIEW_BLACK_FRAME_ANALYSIS_FAILED",
            "state": "blocked",
            "message": "黑帧分析未完成，不能把该预览视为技术检查通过。",
            "evidence": black_error,
        })
    elif black_segments:
        checks.append({
            "code": "PREVIEW_BLACK_SEGMENTS_DETECTED",
            "state": "warning",
            "message": f"检测到 {len(black_segments)} 段连续黑画面，请确认是否为有意转场。",
            "evidence": {"segments": black_segments, "minimum_duration_ms": 500, "pixel_threshold": 0.10},
        })
    else:
        checks.append({
            "code": "PREVIEW_BLACK_FRAME_CHECK_PASSED",
            "state": "passed",
            "message": "未检测到持续 0.5 秒以上的黑画面。",
            "evidence": {"minimum_duration_ms": 500, "pixel_threshold": 0.10},
        })

    track_config = timeline.track_config if isinstance(timeline.track_config, dict) else {}
    if track_config.get("audio_enabled"):
        audio_evidence = measure_program_audio(path, ffmpeg_path)
        mastering = track_config.get("audio_mastering") or {}
        target_lufs = float(mastering.get("loudness_target_lufs", -16))
        peak_limit = float(mastering.get("true_peak_limit_dbtp", -1))
        if audio_evidence.get("ebur128_status") != "measured":
            checks.append({
                "code": "PREVIEW_AUDIO_ANALYSIS_FAILED",
                "state": "blocked",
                "message": "预览已启用声音，但响度与峰值分析没有得到有效实测结果。",
                "evidence": audio_evidence,
            })
        else:
            actual_lufs = float(audio_evidence["integrated_loudness_lufs"])
            actual_peak = float(audio_evidence["true_peak_dbtp"])
            if abs(actual_lufs - target_lufs) > 4:
                checks.append({
                    "code": "PREVIEW_AUDIO_LOUDNESS_OUT_OF_RANGE",
                    "state": "blocked",
                    "message": "预览综合响度超出目标容差。",
                    "evidence": {**audio_evidence, "target_lufs": target_lufs, "tolerance_lu": 4},
                })
            else:
                checks.append({
                    "code": "PREVIEW_AUDIO_LOUDNESS_PASSED",
                    "state": "passed",
                    "message": "预览综合响度在冻结目标容差内。",
                    "evidence": {**audio_evidence, "target_lufs": target_lufs, "tolerance_lu": 4},
                })
            if actual_peak > peak_limit + 0.2:
                checks.append({
                    "code": "PREVIEW_AUDIO_TRUE_PEAK_EXCEEDED",
                    "state": "blocked",
                    "message": "预览真峰值超过冻结上限。",
                    "evidence": {**audio_evidence, "limit_dbtp": peak_limit, "tolerance_db": 0.2},
                })
            else:
                checks.append({
                    "code": "PREVIEW_AUDIO_TRUE_PEAK_PASSED",
                    "state": "passed",
                    "message": "预览真峰值未超过冻结上限。",
                    "evidence": {**audio_evidence, "limit_dbtp": peak_limit, "tolerance_db": 0.2},
                })
        audio_duration = _preview_audio_duration(path, ffmpeg_path)
        if (
            audio_duration.get("status") != "measured"
            or abs(int(audio_duration.get("duration_ms", -1)) - duration_ms) > 250
        ):
            checks.append({
                "code": "PREVIEW_AUDIO_DURATION_MISMATCH",
                "state": "blocked",
                "message": "预览音轨未覆盖完整成片时长，或音轨时长无法实测。",
                "evidence": {**audio_duration, "expected_duration_ms": duration_ms, "tolerance_ms": 250},
            })
        else:
            checks.append({
                "code": "PREVIEW_AUDIO_DURATION_PASSED",
                "state": "passed",
                "message": "预览音轨与成片时长一致。",
                "evidence": {**audio_duration, "expected_duration_ms": duration_ms, "tolerance_ms": 250},
            })
    else:
        checks.append({
            "code": "PREVIEW_AUDIO_NOT_ENABLED",
            "state": "passed",
            "message": "当前时间线未启用音轨，本项不适用。",
            "evidence": {"audio_enabled": False},
        })

    if track_config.get("subtitle_enabled"):
        checks.append({
            "code": "PREVIEW_SUBTITLE_VISUAL_REVIEW_REQUIRED",
            "state": "manual_review",
            "message": "字幕已按合同烧录；仍需人工检查文字、换行、遮挡和安全区。",
            "evidence": {"subtitle_enabled": True, "render_mode": "burn_in"},
        })
    else:
        checks.append({
            "code": "PREVIEW_SUBTITLE_NOT_ENABLED",
            "state": "passed",
            "message": "当前时间线未启用字幕，本项不适用。",
            "evidence": {"subtitle_enabled": False},
        })
    checks.extend([
        {
            "code": "PREVIEW_VISUAL_CONTINUITY_REVIEW_REQUIRED",
            "state": "manual_review",
            "message": "请人工观看并检查镜头衔接、主体一致性、动作连续性和异常闪跳。",
            "evidence": {},
        },
        {
            "code": "PREVIEW_SUBJECTIVE_SYNC_REVIEW_REQUIRED",
            "state": "manual_review",
            "message": "请人工检查旁白、音乐、字幕与画面节奏的主观同步效果。",
            "evidence": {},
        },
    ])
    states = {check["state"] for check in checks}
    status = "blocked" if "blocked" in states else "review_required" if states & {"warning", "manual_review"} else "passed"
    return {
        "schema_version": "editor-preview-qc.v1",
        "status": status,
        "checks": checks,
    }


def render_timeline_preview(
    session: Session,
    project: Project,
    timeline_id: str,
    payload: RenderTimelinePreview,
) -> dict:
    timeline = _require_timeline(session, project, timeline_id)
    if timeline.row_version != payload.expected_row_version:
        raise EditorConflictError("TIMELINE_ROW_VERSION_MISMATCH", "时间线已变化，请刷新后重试。")
    if not timeline.contract_hash or timeline.contract_hash != payload.expected_contract_hash:
        raise EditorConflictError("TIMELINE_CONTRACT_HASH_MISMATCH", "时间线合同哈希已变化，请刷新后重试。")
    errors = _validate_items(session, project, timeline)
    if errors:
        return _preview_response(timeline, project, payload, validation_report=errors)
    repository = _editor(session)
    input_items: list[dict] = []
    for item in repository.timeline_items(timeline.id):
        asset = repository.asset(item.asset_id) if item.asset_id else None
        input_items.append({
            "track_type": item.track_type,
            "sequence_number": item.sequence_number,
            "asset_id": item.asset_id,
            "gap_reason": item.gap_reason,
            "source_in_ms": item.source_in_ms,
            "source_out_ms": item.source_out_ms,
            "timeline_in_ms": item.timeline_in_ms,
            "timeline_out_ms": item.timeline_out_ms,
            "transform": item.transform,
            "asset_uri": asset.uri if asset else None,
            "asset_content_hash": asset.content_hash if asset else None,
        })
    original_spec = timeline.output_spec if isinstance(timeline.output_spec, dict) else {}
    manifest = {
        "track_config": timeline.track_config,
        "input_items": input_items,
        "output_spec": {
            "width": original_spec.get("width"),
            "height": original_spec.get("height"),
            "fps": original_spec.get("fps"),
            "duration_ms": project.duration_seconds * 1000,
        },
    }
    try:
        validate_local_render_manifest(manifest)
    except DeliveryConflictError as exc:
        return _preview_response(timeline, project, payload, validation_report=[_error(exc.code, "preview.render_contract", str(exc))])
    readiness = inspect_local_ffmpeg()
    if not readiness.available or not readiness.executable_path or not readiness.version:
        return _preview_response(timeline, project, payload, validation_report=[_error(
            readiness.reason_code or "FFMPEG_UNAVAILABLE",
            "preview.ffmpeg",
            readiness.reason or "本机 FFmpeg 当前不可用。",
        )])
    render_inputs: list[LocalRenderInput] = []
    audio_inputs: list[LocalRenderAudioInput] = []
    subtitle_input: LocalRenderSubtitleInput | None = None
    for item in sorted(input_items, key=lambda row: (row["track_type"], row["timeline_in_ms"], row["sequence_number"])):
        asset = repository.asset(item["asset_id"])
        if not asset or asset.storage_backend != "local":
            return _preview_response(timeline, project, payload, validation_report=[_error(
                "PREVIEW_ASSET_NOT_LOCAL",
                f"items.{item['track_type']}.{item['sequence_number']}",
                "低清预览只接受已验证的本地素材。",
                asset_id=item["asset_id"],
            )])
        try:
            path = resolve_local_asset_path(asset.uri)
        except QualityConflictError as exc:
            return _preview_response(timeline, project, payload, validation_report=[_error(
                exc.code,
                f"items.{item['track_type']}.{item['sequence_number']}",
                str(exc),
                asset_id=asset.id,
            )])
        if not path.is_file():
            return _preview_response(timeline, project, payload, validation_report=[_error(
                "PREVIEW_INPUT_FILE_MISSING",
                f"items.{item['track_type']}.{item['sequence_number']}",
                "低清预览输入文件不存在。",
                asset_id=asset.id,
            )])
        actual_hash, _ = sha256_file(path)
        if actual_hash != asset.content_hash:
            return _preview_response(timeline, project, payload, validation_report=[_error(
                "PREVIEW_INPUT_HASH_MISMATCH",
                f"items.{item['track_type']}.{item['sequence_number']}",
                "低清预览输入文件哈希与时间线素材事实不一致。",
                asset_id=asset.id,
            )])
        transform = item["transform"] or {}
        if item["track_type"] == "main_video":
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
    preview_key = _preview_key(timeline, payload.quality_profile, readiness.version)
    output_path = _preview_path(project, preview_key)
    width, height, fps = _preview_dimensions(timeline, project)
    duration_ms = project.duration_seconds * 1000
    if output_path.is_file():
        output_error = _preview_output_validation(output_path, width, height, duration_ms)
        if output_error:
            return _preview_response(timeline, project, payload, validation_report=[output_error])
        content_hash, byte_size = sha256_file(output_path)
        quality_report = _preview_quality_report(
            output_path,
            timeline,
            Path(readiness.executable_path),
            duration_ms,
        )
        return _preview_response(
            timeline,
            project,
            payload,
            preview_key=preview_key,
            cached=True,
            content_hash=content_hash,
            byte_size=byte_size,
            quality_report=quality_report,
        )
    mastering = (timeline.track_config or {}).get("audio_mastering") or {}
    try:
        LocalFFmpegRenderer().render(LocalRenderRequest(
            ffmpeg_path=Path(readiness.executable_path),
            inputs=tuple(render_inputs),
            output_path=output_path,
            width=width,
            height=height,
            fps=fps,
            video_encoder="libx264",
            preset="veryfast",
            crf=30,
            audio_inputs=tuple(audio_inputs),
            subtitle_input=subtitle_input,
            loudness_target_lufs=float(mastering.get("loudness_target_lufs", -16)),
            true_peak_limit_dbtp=float(mastering.get("true_peak_limit_dbtp", -1)),
        ))
    except LocalRenderError as exc:
        return _preview_response(timeline, project, payload, validation_report=[_error(
            exc.code,
            "preview.ffmpeg",
            exc.detail,
            **exc.evidence,
        )])
    content_hash, byte_size = sha256_file(output_path)
    output_error = _preview_output_validation(output_path, width, height, duration_ms)
    if output_error:
        return _preview_response(timeline, project, payload, validation_report=[output_error])
    quality_report = _preview_quality_report(
        output_path,
        timeline,
        Path(readiness.executable_path),
        duration_ms,
    )
    return _preview_response(
        timeline,
        project,
        payload,
        preview_key=preview_key,
        cached=False,
        content_hash=content_hash,
        byte_size=byte_size,
        quality_report=quality_report,
    )


def timeline_preview_content_path(
    session: Session,
    project: Project,
    timeline_id: str,
    preview_key: str,
):
    timeline = _require_timeline(session, project, timeline_id)
    readiness = inspect_local_ffmpeg()
    if (
        len(preview_key) != 64
        or any(character not in "0123456789abcdef" for character in preview_key)
        or not readiness.available
        or not readiness.version
        or preview_key != _preview_key(timeline, "draft_360p", readiness.version)
    ):
        raise EditorNotFoundError("低清预览不存在或已因时间线/执行环境变化而失效。")
    path = _preview_path(project, preview_key)
    if not path.is_file():
        raise EditorNotFoundError("低清预览文件不存在。")
    width, height, _ = _preview_dimensions(timeline, project)
    if _preview_output_validation(path, width, height, project.duration_seconds * 1000):
        raise EditorNotFoundError("低清预览文件不再满足冻结预览合同。")
    return path


def _preview_review_read(event: ProjectEvent) -> dict:
    data = event.data if isinstance(event.data, dict) else {}
    return {
        "schema_version": "editor-preview-review.v1",
        "review_id": event.event_id,
        "timeline_id": event.aggregate_id,
        "timeline_contract_hash": data["timeline_contract_hash"],
        "preview_key": data["preview_key"],
        "preview_content_hash": data["preview_content_hash"],
        "quality_status": data["quality_status"],
        "quality_check_codes": data["quality_check_codes"],
        "reviewed_by": event.actor_id,
        "reviewed_at": event.created_at,
    }


def _matching_preview_review(session: Session, timeline: Timeline) -> dict | None:
    if not timeline.contract_hash:
        return None
    events = session.scalars(
        select(ProjectEvent)
        .where(
            ProjectEvent.project_id == timeline.project_id,
            ProjectEvent.event_type == "timeline.preview_reviewed.v1",
            ProjectEvent.aggregate_type == "timeline",
            ProjectEvent.aggregate_id == timeline.id,
        )
        .order_by(ProjectEvent.project_sequence.desc())
    )
    for event in events:
        data = event.data if isinstance(event.data, dict) else {}
        if data.get("timeline_contract_hash") == timeline.contract_hash:
            return _preview_review_read(event)
    return None


def review_timeline_preview(
    session: Session,
    project: Project,
    timeline_id: str,
    payload: ReviewTimelinePreview,
) -> dict:
    receipt = _receipt(session, project.id, payload.command_id, "timeline.review_preview")
    if receipt:
        replay = session.scalar(select(ProjectEvent).where(
            ProjectEvent.project_id == project.id,
            ProjectEvent.event_id == receipt.result_id,
        ))
        if not replay:
            raise EditorConflictError(
                "PREVIEW_REVIEW_RECEIPT_INVALID",
                "预览复核幂等回执指向的审计事件不存在。",
            )
        return _preview_review_read(replay)
    timeline = _require_timeline(session, project, timeline_id)
    _require_active_snapshot(session, project, timeline.snapshot_id)
    if timeline.status not in {"review", "confirmed"} or timeline.validation_report:
        raise EditorConflictError(
            "TIMELINE_PREVIEW_REVIEW_NOT_READY",
            "时间线必须已通过确定性校验且尚未交付，才能提交预览人工复核。",
        )
    if timeline.row_version != payload.expected_row_version:
        raise EditorConflictError("TIMELINE_ROW_VERSION_MISMATCH", "时间线已变化，请刷新后重新预览。")
    current_hash = _hash(_timeline_contract(session, timeline))
    if timeline.contract_hash != payload.expected_contract_hash or current_hash != payload.expected_contract_hash:
        raise EditorConflictError("TIMELINE_CONTRACT_HASH_MISMATCH", "时间线合同已变化，请重新生成预览。")
    path = timeline_preview_content_path(session, project, timeline.id, payload.preview_key)
    content_hash, _ = sha256_file(path)
    if content_hash != payload.expected_preview_content_hash:
        raise EditorConflictError(
            "PREVIEW_CONTENT_HASH_MISMATCH",
            "低清预览文件已变化，请重新生成并完成观看检查。",
        )
    readiness = inspect_local_ffmpeg()
    if not readiness.available or not readiness.executable_path:
        raise EditorConflictError(
            readiness.reason_code or "FFMPEG_UNAVAILABLE",
            readiness.reason or "本机 FFmpeg 当前不可用。",
        )
    quality_report = _preview_quality_report(
        path,
        timeline,
        Path(readiness.executable_path),
        project.duration_seconds * 1000,
    )
    if quality_report["status"] == "blocked":
        blocked_codes = [
            check["code"] for check in quality_report["checks"] if check["state"] == "blocked"
        ]
        raise EditorConflictError(
            "PREVIEW_TECHNICAL_QC_BLOCKED",
            f"低清预览仍有技术阻断，不能完成人工复核：{', '.join(blocked_codes)}",
        )
    if not payload.confirm_visual_continuity_reviewed:
        raise EditorConflictError(
            "PREVIEW_VISUAL_CONTINUITY_REVIEW_REQUIRED",
            "必须实际观看并确认镜头衔接、主体和动作连续性。",
        )
    if not payload.confirm_subjective_sync_reviewed:
        raise EditorConflictError(
            "PREVIEW_SUBJECTIVE_SYNC_REVIEW_REQUIRED",
            "必须实际观看并确认声音、字幕与画面节奏的主观同步。",
        )
    track_config = timeline.track_config if isinstance(timeline.track_config, dict) else {}
    if track_config.get("subtitle_enabled") and not payload.confirm_subtitle_readability_reviewed:
        raise EditorConflictError(
            "PREVIEW_SUBTITLE_REVIEW_REQUIRED",
            "启用字幕的时间线必须人工确认文字、换行、遮挡和安全区。",
        )
    has_warning = any(check["state"] == "warning" for check in quality_report["checks"])
    if has_warning and not payload.confirm_warnings_reviewed:
        raise EditorConflictError(
            "PREVIEW_WARNING_REVIEW_REQUIRED",
            "预览存在警告项，必须逐项观看并确认后才能提交复核。",
        )
    event = ProjectEvent(
        project_id=project.id,
        snapshot_id=timeline.snapshot_id,
        event_type="timeline.preview_reviewed.v1",
        aggregate_type="timeline",
        aggregate_id=timeline.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="User reviewed the exact low-resolution timeline preview.",
        data={
            "timeline_contract_hash": timeline.contract_hash,
            "preview_key": payload.preview_key,
            "preview_content_hash": content_hash,
            "quality_status": quality_report["status"],
            "quality_check_codes": [check["code"] for check in quality_report["checks"]],
            "confirmed_manual_checks": {
                "visual_continuity": payload.confirm_visual_continuity_reviewed,
                "subjective_sync": payload.confirm_subjective_sync_reviewed,
                "subtitle_readability": payload.confirm_subtitle_readability_reviewed,
                "warnings": payload.confirm_warnings_reviewed,
            },
        },
    )
    _event(session, event)
    _save_receipt(
        session,
        project.id,
        payload.command_id,
        "timeline.review_preview",
        "timeline_preview_review",
        event.event_id,
    )
    session.commit()
    return _preview_review_read(event)


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
    result["preview_review"] = _matching_preview_review(session, timeline)
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
