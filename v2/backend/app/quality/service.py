from __future__ import annotations

import hashlib
import json
import struct
import wave
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentGatewayError
from ..core.config import CONNECTED_LOCAL_ASSET_ROOT_REF, RUNTIME_ROOT
from ..db.models import (
    AgentInputManifest,
    AgentRun,
    Asset,
    AssetRevisionRequest,
    AssetReviewDecision,
    PlanVersion,
    Project,
    ProjectEvent,
    QCFinding,
    QCReport,
    QCReportCandidate,
    StoragePolicyVersion,
    utc_now,
)
from ..repositories import (
    QualityRepository,
    SqlAlchemyCommandRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyQualityRepository,
)
from ..orchestration.project_transitions import (
    ProjectStateTrigger,
    block_project,
    transition_project,
)
from .agent_gateway import QCGateway, QCSelection
from .contracts import RegisterAttemptAsset, RetryAssetQC, ReviewAsset, RunAssetQC, VerifyAsset


RULESET_VERSION = "v2.file-contract.v1"
VISUAL_TYPES = {"image", "video"}


class QualityConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class QualityNotFoundError(ValueError):
    pass


def _quality(session: Session) -> QualityRepository:
    return SqlAlchemyQualityRepository(session)


def _event(session: Session, event: ProjectEvent) -> None:
    SqlAlchemyEventRepository(session).add(event)


def _hash_json(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _receipt(session: Session, project_id: str, command_id: str, command_type: str):
    receipt = SqlAlchemyCommandRepository(session).get(project_id, command_id)
    if not receipt:
        return None
    if receipt.command_type != command_type:
        raise QualityConflictError("COMMAND_ID_REUSED", f"Command ID is already used by {receipt.command_type}.")
    return receipt


def _save_receipt(session: Session, project_id: str, command_id: str, command_type: str, result_type: str, result_id: str):
    SqlAlchemyCommandRepository(session).add(
        project_id,
        command_id,
        command_type,
        result_type,
        result_id,
    )


def _local_asset_path(uri: str) -> Path:
    prefix = "runtime://assets/"
    if not uri.startswith(prefix):
        raise QualityConflictError("ASSET_URI_UNSUPPORTED", "Only runtime://assets/ output URIs are supported in the local V2 asset store.")
    relative = PurePosixPath(uri[len(prefix):])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise QualityConflictError("ASSET_URI_INVALID", "Asset URI is not a valid relative runtime asset path.")
    root = (RUNTIME_ROOT / "assets").resolve()
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root):
        raise QualityConflictError("ASSET_URI_OUTSIDE_RUNTIME", "Asset URI resolves outside the V2 runtime asset store.")
    return path


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _jpeg_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            raise ValueError("invalid JPEG signature")
        while True:
            byte = handle.read(1)
            if not byte:
                break
            if byte != b"\xff":
                continue
            marker = handle.read(1)
            while marker == b"\xff":
                marker = handle.read(1)
            if marker in {bytes([value]) for value in range(0xC0, 0xC4)} | {bytes([value]) for value in range(0xC5, 0xC8)} | {bytes([value]) for value in range(0xC9, 0xCC)} | {bytes([value]) for value in range(0xCD, 0xD0)}:
                length = struct.unpack(">H", handle.read(2))[0]
                payload = handle.read(length - 2)
                return struct.unpack(">H", payload[3:5])[0], struct.unpack(">H", payload[1:3])[0]
            if marker in {b"\xd8", b"\xd9"}:
                continue
            length_bytes = handle.read(2)
            if len(length_bytes) != 2:
                break
            handle.seek(struct.unpack(">H", length_bytes)[0] - 2, 1)
    raise ValueError("JPEG dimensions not found")


def _mp4_probe(path: Path) -> tuple[int | None, int | None, int | None]:
    width = height = duration_ms = None
    containers = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}
    with path.open("rb") as handle:
        def walk(start: int, end: int) -> None:
            nonlocal width, height, duration_ms
            position = start
            while position + 8 <= end:
                handle.seek(position)
                header = handle.read(8)
                if len(header) != 8:
                    return
                size, box_type = struct.unpack(">I4s", header)
                header_size = 8
                if size == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        return
                    size = struct.unpack(">Q", extended)[0]
                    header_size = 16
                elif size == 0:
                    size = end - position
                if size < header_size or position + size > end:
                    return
                payload_start = position + header_size
                payload_size = size - header_size
                if box_type in containers:
                    walk(payload_start, position + size)
                elif box_type == b"mvhd" and payload_size >= 20:
                    handle.seek(payload_start)
                    version = handle.read(1)[0]
                    handle.seek(payload_start + (20 if version == 1 else 12))
                    timescale = struct.unpack(">I", handle.read(4))[0]
                    duration = struct.unpack(">Q" if version == 1 else ">I", handle.read(8 if version == 1 else 4))[0]
                    if timescale:
                        duration_ms = round(duration * 1000 / timescale)
                elif box_type == b"tkhd" and payload_size >= 8:
                    handle.seek(position + size - 8)
                    candidate_width, candidate_height = struct.unpack(">II", handle.read(8))
                    candidate_width >>= 16
                    candidate_height >>= 16
                    if candidate_width and candidate_height and candidate_width * candidate_height > (width or 0) * (height or 0):
                        width, height = candidate_width, candidate_height
                position += size
        walk(0, path.stat().st_size)
    return width, height, duration_ms


def _probe(path: Path, declared_type: str) -> dict:
    with path.open("rb") as handle:
        head = handle.read(32)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", head[16:24])
        return {"mime_type": "image/png", "width": width, "height": height, "duration_ms": None}
    if head.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(path)
        return {"mime_type": "image/jpeg", "width": width, "height": height, "duration_ms": None}
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        with wave.open(str(path), "rb") as source:
            duration_ms = round(source.getnframes() * 1000 / source.getframerate())
        return {"mime_type": "audio/wav", "width": None, "height": None, "duration_ms": duration_ms}
    if len(head) >= 12 and head[4:8] == b"ftyp":
        width, height, duration_ms = _mp4_probe(path)
        return {"mime_type": "video/mp4", "width": width, "height": height, "duration_ms": duration_ms}
    if declared_type in {"subtitle", "project_file"}:
        path.read_text(encoding="utf-8")
        mime = "application/x-subrip" if declared_type == "subtitle" else "application/json"
        return {"mime_type": mime, "width": None, "height": None, "duration_ms": None}
    raise QualityConflictError("ASSET_MEDIA_UNSUPPORTED", "The file signature is not supported by the V2 media probe.")


def _require_asset(session: Session, project: Project, asset_id: str) -> Asset:
    asset = _quality(session).asset(asset_id)
    if not asset or asset.project_id != project.id:
        raise QualityNotFoundError("Asset not found in project")
    return asset


def _storage_policy(session: Session, snapshot_id: str) -> StoragePolicyVersion:
    repository = _quality(session)
    snapshot = repository.snapshot(snapshot_id)
    if not snapshot:
        raise QualityConflictError("ASSET_SNAPSHOT_MISSING", "Asset snapshot no longer exists.")
    policies = repository.published_storage_policies(snapshot.production_config_version_id)
    if len(policies) != 1:
        raise QualityConflictError("STORAGE_POLICY_NOT_EXACT", "Snapshot configuration must resolve exactly one published storage policy.")
    return policies[0]


def resolve_local_asset_path(uri: str) -> Path:
    return _local_asset_path(uri)


def sha256_file(path: Path) -> tuple[str, int]:
    return _sha256_file(path)


def probe_media(path: Path, declared_type: str) -> dict:
    return _probe(path, declared_type)


def storage_policy_for_snapshot(session: Session, snapshot_id: str) -> StoragePolicyVersion:
    return _storage_policy(session, snapshot_id)


def register_attempt_asset(session: Session, project: Project, attempt_id: str, payload: RegisterAttemptAsset) -> dict:
    repository = _quality(session)
    receipt = _receipt(session, project.id, payload.command_id, "asset.register_output")
    if receipt:
        return asset_read(session, _require_asset(session, project, receipt.result_id))
    attempt = repository.work_attempt(attempt_id)
    item = repository.work_item(attempt.work_item_id) if attempt else None
    if not attempt or not item or item.project_id != project.id:
        raise QualityNotFoundError("Work attempt not found in project")
    if attempt.state != "completed" or not attempt.response_manifest:
        raise QualityConflictError("ATTEMPT_OUTPUT_NOT_COMPLETE", "Only a completed attempt with a response manifest can register assets.")
    if _hash_json(attempt.response_manifest) != payload.expected_response_manifest_hash:
        raise QualityConflictError("RESPONSE_MANIFEST_HASH_MISMATCH", "The persisted provider response manifest has changed.")
    if attempt.response_manifest.get("media_created") is not True:
        raise QualityConflictError("ATTEMPT_CREATED_NO_MEDIA", "The attempt explicitly states that no media was created.")
    outputs = attempt.response_manifest.get("outputs")
    if not isinstance(outputs, list) or payload.output_index >= len(outputs):
        raise QualityConflictError("PROVIDER_OUTPUT_INDEX_MISSING", "The exact provider output index is not present in the response manifest.")
    output = outputs[payload.output_index]
    required = {"uri", "storage_backend", "asset_type", "role", "mime_type", "content_hash"}
    if not isinstance(output, dict) or required - output.keys():
        raise QualityConflictError("PROVIDER_OUTPUT_MANIFEST_INVALID", "Provider output manifest is missing required asset fields.")
    if output["storage_backend"] != "local":
        raise QualityConflictError("STORAGE_ADAPTER_NOT_CONNECTED", "Only the local V2 asset storage adapter is connected.")
    policy = _storage_policy(session, item.snapshot_id)
    if policy.backend_kind != output["storage_backend"]:
        raise QualityConflictError("ASSET_STORAGE_POLICY_MISMATCH", "Provider output storage backend does not match the snapshot storage policy.")
    if policy.backend_kind != "local" or policy.local_root_ref != CONNECTED_LOCAL_ASSET_ROOT_REF:
        raise QualityConflictError("STORAGE_ADAPTER_NOT_CONNECTED", "The snapshot storage policy is not connected to the local V2 asset store.")
    _local_asset_path(str(output["uri"]))
    node = repository.dag_node(item.dag_node_id)
    expected_media_type = node.output_contract.get("media_type") if node else None
    mapped_type = "project_file" if expected_media_type == "timeline" else expected_media_type
    if output["asset_type"] != mapped_type:
        raise QualityConflictError("ASSET_TYPE_CONTRACT_MISMATCH", "Provider output type does not match the exact DAG output contract.")
    existing = repository.asset_for_output(attempt.id, payload.output_index)
    if existing:
        raise QualityConflictError("ATTEMPT_OUTPUT_ALREADY_REGISTERED", "This provider output index is already registered.")
    asset = Asset(
        project_id=project.id,
        snapshot_id=item.snapshot_id,
        work_attempt_id=attempt.id,
        dag_node_id=item.dag_node_id,
        output_index=payload.output_index,
        asset_type=output["asset_type"],
        role=output["role"],
        uri=output["uri"],
        storage_backend=output["storage_backend"],
        provider_output_manifest=output,
        mime_type=output["mime_type"],
        state="created",
    )
    repository.add(asset)
    repository.flush()
    _save_receipt(session, project.id, payload.command_id, "asset.register_output", "asset", asset.id)
    _event(session, ProjectEvent(project_id=project.id, snapshot_id=asset.snapshot_id, event_type="asset.created.v1", aggregate_type="asset", aggregate_id=asset.id, actor_type="user", actor_id=payload.actor_id, causation_id=payload.command_id, message="Provider output registered as an unverified asset.", data={"asset_id": asset.id, "work_attempt_id": attempt.id, "output_index": payload.output_index}))
    session.commit()
    return asset_read(session, asset)


def verify_asset(session: Session, project: Project, asset_id: str, payload: VerifyAsset) -> dict:
    receipt = _receipt(session, project.id, payload.command_id, "asset.verify")
    if receipt:
        return asset_read(session, _require_asset(session, project, receipt.result_id))
    asset = _require_asset(session, project, asset_id)
    if asset.state != "created":
        raise QualityConflictError("ASSET_NOT_CREATED", f"Asset state {asset.state} cannot be verified.")
    if asset.row_version != payload.expected_row_version:
        raise QualityConflictError("ASSET_ROW_VERSION_MISMATCH", "Asset changed; refresh before verification.")
    path = _local_asset_path(asset.uri)
    if not path.is_file():
        return _record_file_block(session, project, asset, payload, "ASSET_FILE_MISSING", {"uri": asset.uri})
    content_hash, byte_size = _sha256_file(path)
    expected_hash = str(asset.provider_output_manifest.get("content_hash", ""))
    if content_hash != expected_hash:
        return _record_file_block(session, project, asset, payload, "ASSET_CONTENT_HASH_MISMATCH", {"actual": content_hash, "expected": expected_hash})
    try:
        media = _probe(path, asset.asset_type)
    except (OSError, UnicodeError, ValueError, wave.Error) as exc:
        return _record_file_block(session, project, asset, payload, "ASSET_FILE_INVALID", {"probe_error": str(exc)})
    if media["mime_type"] != asset.provider_output_manifest.get("mime_type"):
        return _record_file_block(session, project, asset, payload, "ASSET_MIME_TYPE_MISMATCH", {"actual": media["mime_type"], "expected": asset.provider_output_manifest.get("mime_type")})
    policy = _storage_policy(session, asset.snapshot_id)
    if byte_size > policy.max_file_size_bytes:
        return _record_file_block(session, project, asset, payload, "ASSET_FILE_TOO_LARGE", {"actual_bytes": byte_size, "maximum_bytes": policy.max_file_size_bytes})
    if media["mime_type"] not in policy.allowed_mime_types:
        return _record_file_block(session, project, asset, payload, "ASSET_MIME_TYPE_NOT_ALLOWED", {"actual": media["mime_type"], "allowed": policy.allowed_mime_types})
    asset.content_hash = content_hash
    asset.byte_size = byte_size
    asset.mime_type = media["mime_type"]
    asset.width = media["width"]
    asset.height = media["height"]
    asset.duration_ms = media["duration_ms"]
    asset.state = "verified"
    asset.verified_at = utc_now()
    asset.row_version += 1
    _save_receipt(session, project.id, payload.command_id, "asset.verify", "asset", asset.id)
    _event(session, ProjectEvent(project_id=project.id, snapshot_id=asset.snapshot_id, event_type="asset.verified.v1", aggregate_type="asset", aggregate_id=asset.id, actor_type="system", actor_id=payload.actor_id, causation_id=payload.command_id, message="Asset file and content hash verified.", data={"asset_id": asset.id, "content_hash": asset.content_hash, "byte_size": asset.byte_size}))
    session.commit()
    return asset_read(session, asset)


def _record_file_block(
    session: Session,
    project: Project,
    asset: Asset,
    payload: VerifyAsset,
    code: str,
    evidence: dict,
) -> dict:
    repository = _quality(session)
    now = utc_now()
    report = QCReport(
        project_id=project.id,
        snapshot_id=asset.snapshot_id,
        asset_id=asset.id,
        report_number=1,
        ruleset_version=RULESET_VERSION,
        status="blocked",
        analyzer="deterministic-file-verifier",
    )
    repository.add(report)
    repository.flush()
    repository.add(QCFinding(
        qc_report_id=report.id,
        code=code,
        severity="blocked",
        evidence=evidence,
        contract_field="provider_output_manifest",
        disposition="block",
    ))
    asset.state = "archived"
    asset.archived_at = now
    asset.row_version += 1
    item = repository.work_item_for_node(asset.snapshot_id, asset.dag_node_id)
    if item:
        item.status = "blocked"
        item.error = f"{code}: registered output failed file verification"
        item.row_version += 1
    block_project(
        session,
        project,
        reason_code=code,
        responsible_aggregate_type="asset",
        responsible_aggregate_id=asset.id,
        actor_type="system",
        actor_id=payload.actor_id,
        event_data={"qc_report_id": report.id},
    )
    _save_receipt(session, project.id, payload.command_id, "asset.verify", "asset", asset.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=asset.snapshot_id,
        event_type="quality.blocked.v1",
        aggregate_type="asset",
        aggregate_id=asset.id,
        actor_type="system",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Registered asset failed deterministic file verification.",
        data={"asset_id": asset.id, "qc_report_id": report.id, "code": code, "evidence": evidence},
    ))
    session.commit()
    return asset_read(session, asset)


def _add_finding(findings: list[dict], code: str, severity: str, evidence: dict, contract_field: str | None, disposition: str):
    findings.append({"code": code, "severity": severity, "evidence": evidence, "contract_field": contract_field, "disposition": disposition})


def _deterministic_contract_findings(session: Session, asset: Asset, node) -> list[dict]:
    repository = _quality(session)
    findings: list[dict] = []
    if asset.asset_type in VISUAL_TYPES:
        video_spec_id = node.output_contract.get("video_spec_version_id")
        snapshot = repository.snapshot(asset.snapshot_id)
        expected_width = snapshot.output_spec.get("width") if snapshot and video_spec_id else None
        expected_height = snapshot.output_spec.get("height") if snapshot and video_spec_id else None
        if expected_width and expected_height and (asset.width != expected_width or asset.height != expected_height):
            _add_finding(findings, "MEDIA_DIMENSIONS_INVALID", "blocked", {"actual": [asset.width, asset.height], "expected": [expected_width, expected_height]}, "output_spec.width_height", "block")
    if asset.asset_type == "video":
        expected_duration = node.input_contract.get("duration_ms")
        if expected_duration is not None and (asset.duration_ms is None or abs(asset.duration_ms - expected_duration) > 100):
            _add_finding(findings, "MEDIA_DURATION_INVALID", "blocked", {"actual_ms": asset.duration_ms, "expected_ms": expected_duration, "tolerance_ms": 100}, "input_contract.duration_ms", "block")
    return findings


def _record_contract_block(session: Session, project: Project, asset: Asset, payload: RunAssetQC, findings: list[dict]) -> QCReport:
    repository = _quality(session)
    report = QCReport(
        project_id=project.id, snapshot_id=asset.snapshot_id, asset_id=asset.id,
        report_number=repository.next_report_number(asset.id), ruleset_version=RULESET_VERSION,
        status="blocked", analyzer="deterministic-file-contract",
    )
    repository.add(report)
    repository.flush()
    for finding in findings:
        repository.add(QCFinding(qc_report_id=report.id, **finding))
    asset.state = "archived"
    asset.archived_at = utc_now()
    asset.row_version += 1
    item = repository.work_item_for_node(asset.snapshot_id, asset.dag_node_id)
    if item:
        item.status = "blocked"
        item.error = "ASSET_QC_BLOCKED: deterministic file contract failed"
        item.row_version += 1
    block_project(
        session, project, reason_code="ASSET_QC_BLOCKED", responsible_aggregate_type="asset",
        responsible_aggregate_id=asset.id, actor_type="system", actor_id=payload.actor_id,
        event_data={"qc_report_id": report.id},
    )
    _save_receipt(session, project.id, payload.command_id, "quality.run", "qc_report", report.id)
    _event(session, ProjectEvent(
        project_id=project.id, snapshot_id=asset.snapshot_id, event_type="quality.blocked.v1",
        aggregate_type="qc_report", aggregate_id=report.id, actor_type="system", actor_id=payload.actor_id,
        causation_id=payload.command_id, message="Asset failed deterministic quality contract checks.",
        data={"asset_id": asset.id, "qc_report_id": report.id, "status": "blocked"},
    ))
    session.commit()
    return report


def _record_manual_content_review(session: Session, project: Project, asset: Asset, payload: RunAssetQC) -> QCReport:
    repository = _quality(session)
    code = "VIDEO_CONTENT_REVIEW_REQUIRED" if asset.asset_type == "video" else "AUDIO_CONTENT_REVIEW_REQUIRED"
    report = QCReport(
        project_id=project.id, snapshot_id=asset.snapshot_id, asset_id=asset.id,
        report_number=repository.next_report_number(asset.id), ruleset_version="qc-policy.v1",
        status="review_required", analyzer="human-review-required",
    )
    repository.add(report)
    repository.flush()
    repository.add(QCFinding(
        qc_report_id=report.id, code=code, severity="review_required",
        evidence={"asset_id": asset.id, "reason": "configured_qc_contract_does_not_claim_this_media_capability"},
        contract_field="input_contract.shot", disposition="manual_review",
    ))
    asset.state = "review_required"
    asset.row_version += 1
    transition_project(
        session, project, ProjectStateTrigger.QUALITY_RECORDED, actor_type="system", actor_id="quality-contract",
        event_data={"asset_id": asset.id, "qc_report_id": report.id},
    )
    _save_receipt(session, project.id, payload.command_id, "quality.run", "qc_report", report.id)
    _event(session, ProjectEvent(
        project_id=project.id, snapshot_id=asset.snapshot_id, event_type="quality.review_required.v1",
        aggregate_type="qc_report", aggregate_id=report.id, actor_type="system", actor_id="quality-contract",
        causation_id=payload.command_id, message="Media requires explicit human content review.",
        data={"asset_id": asset.id, "qc_report_id": report.id, "analyzer": "human-review-required"},
    ))
    session.commit()
    return report


def _qc_manifest(session: Session, project: Project, asset: Asset, node, selection: QCSelection) -> AgentInputManifest:
    repository = _quality(session)
    snapshot = repository.snapshot(asset.snapshot_id)
    plan = session.get(PlanVersion, snapshot.plan_version_id) if snapshot else None
    if not snapshot or not plan:
        raise QualityConflictError("QC_PLAN_REFERENCE_MISSING", "素材绑定的生产快照或方案版本不存在。")
    if selection.production_config_version_id != snapshot.production_config_version_id:
        raise QualityConflictError("QC_MODEL_CONFIG_SNAPSHOT_MISMATCH", "质量审核模型不属于该素材冻结的生产配置版本。")
    shot = dict(node.input_contract.get("shot") or {})
    reference = node.input_contract.get("reference_image")
    reference_ids = []
    if isinstance(reference, dict) and isinstance(reference.get("attachment_id"), str):
        reference_ids.append(reference["attachment_id"])
        prefix = "runtime://attachments/"
        uri = str(reference.get("uri") or "")
        if not uri.startswith(prefix):
            raise QualityConflictError("QC_REFERENCE_URI_INVALID", "镜头主参考图片地址不属于附件存储。")
        relative = PurePosixPath(uri[len(prefix):])
        root = RUNTIME_ROOT.resolve()
        reference_path = root.joinpath(*relative.parts).resolve()
        if relative.is_absolute() or ".." in relative.parts or not reference_path.is_relative_to(root) or not reference_path.is_file():
            raise QualityConflictError("QC_REFERENCE_FILE_MISSING", "镜头主参考图片文件不存在或路径无效。")
        reference_hash, _ = _sha256_file(reference_path)
        if reference_hash != reference.get("content_hash"):
            raise QualityConflictError("QC_REFERENCE_HASH_MISMATCH", "镜头主参考图片内容与冻结哈希不一致。")
    reference_catalog = [
        f"dag_node.{node.id}.input_contract.shot.{field}"
        for field in ("face_visibility", "face_subject_entity_version_ids", "text_policy", "required_on_screen_text", "subject_motion", "composition", "action", "visual_prompt")
        if field in shot
    ]
    payload = {
        "contract_version": "qc-agent-input.v1",
        "project_id": project.id,
        "asset": {
            "id": asset.id, "content_hash": asset.content_hash, "asset_type": asset.asset_type,
            "mime_type": asset.mime_type, "width": asset.width, "height": asset.height, "duration_ms": asset.duration_ms,
        },
        "media_probe_id": f"media-probe:{asset.content_hash}",
        "snapshot_id": asset.snapshot_id,
        "dag_node_id": node.id,
        "shot_code": shot.get("shot_code"),
        "shot_contract": shot,
        "entity_reference_asset_ids": reference_ids,
        "entity_reference_images": [reference] if reference_ids else [],
        "deterministic_checks": [{"id": f"file-contract:{asset.id}", "status": "passed", "ruleset_version": RULESET_VERSION}],
        "qc_policy_version": "qc-policy.v1",
        "contract_reference_catalog": reference_catalog,
        "system_config_version": selection.production_config_version_id,
    }
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=plan.requirement_version_id,
        message_ids=[], decision_ids=[], attachment_binding_ids=reference_ids,
        system_config_version=selection.production_config_version_id,
        input_hash=_hash_json(payload), payload=payload,
    )
    repository.add(manifest)
    repository.flush()
    return manifest


def _agent_run_read(session: Session, run: AgentRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id, "agent_role": run.agent_role, "status": run.status,
        "model_provider": run.model_provider, "model_name": run.model_name,
        "prompt_contract_version": run.prompt_contract_version, "output_schema_version": run.output_schema_version,
        "error_code": run.error_code, "error_detail": run.error_detail,
        "provider_request_id": run.provider_request_id, "token_usage": run.token_usage or {},
        "started_at": run.started_at, "finished_at": run.finished_at,
    }


def qc_candidate_read(candidate: QCReportCandidate) -> dict:
    return {
        "id": candidate.id, "asset_id": candidate.asset_id, "agent_run_id": candidate.agent_run_id,
        "status": candidate.status, "overall_recommendation": candidate.overall_recommendation,
        "findings": candidate.findings, "analyzer_version": candidate.analyzer_version,
        "created_at": candidate.created_at, "decided_at": candidate.decided_at,
    }


def _execute_qc_agent(
    session: Session, project: Project, asset: Asset, manifest: AgentInputManifest,
    payload: RunAssetQC | RetryAssetQC, gateway: QCGateway, selection: QCSelection,
    *, retry_of_agent_run_id: str | None = None,
) -> QCReportCandidate:
    repository = _quality(session)
    run = AgentRun(
        project_id=project.id, agent_role="qc", status="running", input_manifest_id=manifest.id,
        model_provider=selection.model_provider, model_name=selection.model_name,
        production_config_version_id=selection.production_config_version_id,
        model_config_version_id=selection.model_config_version_id,
        provider_config_version_id=selection.provider_config_version_id,
        prompt_contract_version=selection.prompt_contract_version,
        output_schema_version=selection.output_schema_version, started_at=utc_now(),
    )
    repository.add(run)
    repository.flush()
    _event(session, ProjectEvent(
        project_id=project.id, snapshot_id=asset.snapshot_id, event_type="agent.run_created.v1",
        aggregate_type="agent_run", aggregate_id=run.id, actor_type="user", actor_id=payload.actor_id,
        causation_id=payload.command_id, message="Quality review agent started.",
        data={"asset_id": asset.id, "agent_role": "qc", "retry_of_agent_run_id": retry_of_agent_run_id},
    ))
    session.commit()
    try:
        result = gateway.invoke(selection, manifest.payload, _local_asset_path(asset.uri))
    except AgentGatewayError as exc:
        failed = session.get(AgentRun, run.id)
        if failed:
            failed.status = "failed"
            failed.error_code = exc.code
            failed.error_detail = f"{exc} {json.dumps(exc.diagnostics, ensure_ascii=False, separators=(',', ':'), default=str)}" if exc.diagnostics else str(exc)
            failed.raw_output = exc.raw_output
            failed.finished_at = utc_now()
            _event(session, ProjectEvent(
                project_id=project.id, snapshot_id=asset.snapshot_id, event_type="agent.run_failed.v1",
                aggregate_type="agent_run", aggregate_id=failed.id, actor_type="system", actor_id="qc-agent",
                causation_id=payload.command_id, message="Quality review agent failed.",
                data={"asset_id": asset.id, "error_code": exc.code, "retry_of_agent_run_id": retry_of_agent_run_id},
            ))
            session.commit()
        raise
    candidate = QCReportCandidate(
        project_id=project.id, snapshot_id=asset.snapshot_id, asset_id=asset.id, agent_run_id=run.id,
        status="awaiting_review", overall_recommendation=result.output.overall_recommendation,
        findings=[item.model_dump(mode="json") for item in result.output.findings],
        analyzer_version=result.output.analyzer_version,
    )
    repository.add(candidate)
    repository.flush()
    run.status = "succeeded"
    run.parsed_candidate_id = candidate.id
    run.raw_output = result.raw_output
    run.provider_request_id = result.provider_request_id
    run.token_usage = result.token_usage
    run.finished_at = utc_now()
    asset.state = "review_required"
    asset.row_version += 1
    transition_project(
        session, project, ProjectStateTrigger.QUALITY_RECORDED, actor_type="system", actor_id="qc-agent",
        event_data={"asset_id": asset.id, "qc_report_candidate_id": candidate.id},
    )
    command_type = "quality.retry" if retry_of_agent_run_id else "quality.run"
    _save_receipt(session, project.id, payload.command_id, command_type, "qc_report_candidate", candidate.id)
    _event(session, ProjectEvent(
        project_id=project.id, snapshot_id=asset.snapshot_id, event_type="quality.candidate_created.v1",
        aggregate_type="qc_report_candidate", aggregate_id=candidate.id, actor_type="system", actor_id="qc-agent",
        causation_id=payload.command_id, message="Quality review candidate created for human review.",
        data={"asset_id": asset.id, "agent_run_id": run.id, "retry_of_agent_run_id": retry_of_agent_run_id},
    ))
    session.commit()
    return candidate


def run_asset_qc(session: Session, project: Project, asset_id: str, payload: RunAssetQC, gateway: QCGateway) -> dict:
    repository = _quality(session)
    receipt = _receipt(session, project.id, payload.command_id, "quality.run")
    if receipt:
        candidate = repository.qc_candidate(receipt.result_id)
        if candidate:
            return qc_candidate_read(candidate)
        report = repository.qc_report(receipt.result_id)
        if report:
            return qc_report_read(session, report)
        raise QualityConflictError("COMMAND_RESULT_MISSING", "质量审核命令结果已不存在。")
    asset = _require_asset(session, project, asset_id)
    if asset.state != "verified":
        raise QualityConflictError("ASSET_NOT_VERIFIED", "只有已验证素材可以进入质量审核。")
    if asset.row_version != payload.expected_row_version:
        raise QualityConflictError("ASSET_ROW_VERSION_MISMATCH", "素材已变化，请刷新后再审核。")
    node = repository.dag_node(asset.dag_node_id) if asset.dag_node_id else None
    if not node or node.snapshot_id != asset.snapshot_id:
        raise QualityConflictError("ASSET_DAG_REFERENCE_MISSING", "素材绑定的制作步骤不存在。")
    findings = _deterministic_contract_findings(session, asset, node)
    if findings:
        return qc_report_read(session, _record_contract_block(session, project, asset, payload, findings))
    if asset.asset_type != "image":
        return qc_report_read(session, _record_manual_content_review(session, project, asset, payload))
    latest_candidate = repository.latest_qc_candidate(asset.id)
    if latest_candidate and latest_candidate.status == "awaiting_review":
        raise QualityConflictError("QC_CANDIDATE_ALREADY_EXISTS", "该素材已有待确认的质量审核结果。")
    latest_run = repository.latest_qc_agent_run(asset.id)
    if latest_run and latest_run.status == "failed":
        raise QualityConflictError("QC_FAILED_RUN_REQUIRES_RETRY", "上次质量审核失败，请从失败记录精确重跑。")
    selection = gateway.select(session)
    manifest = _qc_manifest(session, project, asset, node, selection)
    return qc_candidate_read(_execute_qc_agent(session, project, asset, manifest, payload, gateway, selection))


def retry_failed_asset_qc(session: Session, project: Project, asset_id: str, payload: RetryAssetQC, gateway: QCGateway) -> dict:
    repository = _quality(session)
    receipt = _receipt(session, project.id, payload.command_id, "quality.retry")
    if receipt:
        candidate = repository.qc_candidate(receipt.result_id)
        if not candidate:
            raise QualityConflictError("COMMAND_RESULT_MISSING", "质量审核重跑结果已不存在。")
        return qc_candidate_read(candidate)
    if not payload.confirm_model_cost:
        raise QualityConflictError("MODEL_COST_CONFIRMATION_REQUIRED", "请明确确认本次重跑会再次调用当前质量审核模型。")
    asset = _require_asset(session, project, asset_id)
    if payload.expected_asset_id != asset.id or asset.state != "verified":
        raise QualityConflictError("QC_RETRY_ASSET_CHANGED", "失败运行绑定的素材已变化，不能重跑。")
    if asset.row_version != payload.expected_row_version:
        raise QualityConflictError("ASSET_ROW_VERSION_MISMATCH", "素材已变化，请刷新后再重跑。")
    failed = session.get(AgentRun, payload.failed_agent_run_id)
    latest = repository.latest_qc_agent_run(asset.id)
    if not failed or failed.id != (latest.id if latest else None) or failed.status != "failed" or failed.agent_role != "qc":
        raise QualityConflictError("QC_FAILED_RUN_NOT_LATEST", "只能重跑该素材最近一次失败的质量审核。")
    manifest = session.get(AgentInputManifest, failed.input_manifest_id)
    if not manifest or manifest.payload.get("asset", {}).get("id") != asset.id:
        raise QualityConflictError("QC_MANIFEST_MISSING", "失败运行的冻结输入不存在或已不匹配。")
    selection = gateway.select(session)
    expected = (failed.production_config_version_id, failed.model_config_version_id, failed.provider_config_version_id, failed.prompt_contract_version, failed.output_schema_version)
    actual = (selection.production_config_version_id, selection.model_config_version_id, selection.provider_config_version_id, selection.prompt_contract_version, selection.output_schema_version)
    if actual != expected:
        raise QualityConflictError("QC_RETRY_CONFIG_CHANGED", "当前质量审核配置与失败运行不一致，不能静默更换模型或合同。")
    return qc_candidate_read(_execute_qc_agent(session, project, asset, manifest, payload, gateway, selection, retry_of_agent_run_id=failed.id))


def review_asset(session: Session, project: Project, asset_id: str, payload: ReviewAsset, decision: str) -> dict:
    repository = _quality(session)
    command_type = f"asset.review.{decision}"
    receipt = _receipt(session, project.id, payload.command_id, command_type)
    if receipt:
        return asset_read(session, _require_asset(session, project, receipt.result_id))
    asset = _require_asset(session, project, asset_id)
    candidate = repository.qc_candidate(payload.qc_report_candidate_id) if payload.qc_report_candidate_id else None
    source_report = repository.qc_report(payload.qc_report_id) if payload.qc_report_id else None
    if candidate and (candidate.asset_id != asset.id or candidate.project_id != project.id):
        raise QualityNotFoundError("Quality review candidate not found for asset")
    if source_report and (source_report.asset_id != asset.id or source_report.project_id != project.id):
        raise QualityNotFoundError("QC report not found for asset")
    if not candidate and not source_report:
        raise QualityNotFoundError("Quality review source not found for asset")
    if asset.state != "review_required" or (candidate and candidate.status != "awaiting_review") or (source_report and source_report.status != "review_required"):
        raise QualityConflictError("ASSET_NOT_AWAITING_REVIEW", "该素材当前没有可确认的质量审核结果。")
    if asset.row_version != payload.expected_row_version:
        raise QualityConflictError("ASSET_ROW_VERSION_MISMATCH", "素材已变化，请刷新后再审核。")
    now = utc_now()
    if candidate:
        report = QCReport(
            project_id=project.id, snapshot_id=asset.snapshot_id, asset_id=asset.id,
            report_number=repository.next_report_number(asset.id), ruleset_version="qc-policy.v1",
            status="review_required", analyzer=candidate.analyzer_version,
        )
        repository.add(report)
        repository.flush()
        for finding in candidate.findings:
            repository.add(QCFinding(
                qc_report_id=report.id, code=finding["finding_code"], severity=finding["severity"],
                evidence={"confidence": finding["confidence"], "summary": finding["summary"], "items": finding["evidence"], "contract_refs": finding["contract_refs"], "suggested_review_action": finding["suggested_review_action"]},
                contract_field=finding["contract_refs"][0], disposition="manual_review",
            ))
    else:
        report = source_report
        if repository.has_review_decision(report.id):
            raise QualityConflictError("QC_REPORT_ALREADY_REVIEWED", "该人工审核项已经处理。")
    repository.add(AssetReviewDecision(project_id=project.id, asset_id=asset.id, qc_report_id=report.id, decision=decision, rationale=payload.rationale, actor_id=payload.actor_id))
    report.reviewed_at = now
    report.reviewed_by = payload.actor_id
    if candidate:
        candidate.status = "reviewed"
        candidate.decided_at = now
    if decision == "approved":
        asset.state = "approved"
        asset.approved_at = now
        event_type = "asset.approved.v1"
    else:
        asset.state = "archived"
        asset.archived_at = now
        event_type = "asset.rejected.v1"
    asset.row_version += 1
    transition_project(
        session,
        project,
        ProjectStateTrigger.QUALITY_RECORDED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"asset_id": asset.id, "qc_report_id": report.id},
    )
    _save_receipt(session, project.id, payload.command_id, command_type, "asset", asset.id)
    _event(session, ProjectEvent(project_id=project.id, snapshot_id=asset.snapshot_id, event_type=event_type, aggregate_type="qc_report", aggregate_id=report.id, actor_type="user", actor_id=payload.actor_id, causation_id=payload.command_id, message="Human asset review decision recorded.", data={"asset_id": asset.id, "qc_report_id": report.id, "qc_report_candidate_id": candidate.id if candidate else None, "decision": decision, "rationale": payload.rationale}))
    session.commit()
    return asset_read(session, asset)


def qc_report_read(session: Session, report: QCReport) -> dict:
    findings = _quality(session).findings(report.id)
    result = {column.name: getattr(report, column.name) for column in report.__table__.columns if column.name not in {"asset_id", "project_id", "snapshot_id"}}
    result["findings"] = [{column.name: getattr(finding, column.name) for column in finding.__table__.columns if column.name not in {"qc_report_id"}} for finding in findings]
    return result


def _downstream_keys(session: Session, asset: Asset) -> list[str]:
    if not asset.dag_node_id:
        return []
    repository = _quality(session)
    edges = repository.dependency_edges(asset.snapshot_id)
    children: dict[str, set[str]] = {}
    for edge in edges:
        children.setdefault(edge.parent_node_id, set()).add(edge.child_node_id)
    seen: set[str] = set()
    pending = list(children.get(asset.dag_node_id, set()))
    while pending:
        node_id = pending.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        pending.extend(children.get(node_id, set()))
    nodes = {node.id: node.node_key for node in repository.dag_nodes_by_ids(seen)}
    return sorted(nodes.values())


def asset_read(session: Session, asset: Asset) -> dict:
    repository = _quality(session)
    node = repository.dag_node(asset.dag_node_id) if asset.dag_node_id else None
    report = repository.latest_qc_report(asset.id)
    candidate = repository.latest_qc_candidate(asset.id)
    agent_run = repository.latest_qc_agent_run(asset.id)
    decisions = repository.review_decisions(asset.id)
    result = {column.name: getattr(asset, column.name) for column in asset.__table__.columns if column.name not in {"provider_output_manifest", "deleted_at"}}
    result["node_key"] = node.node_key if node else None
    result["latest_qc_report"] = qc_report_read(session, report) if report else None
    result["latest_qc_candidate"] = qc_candidate_read(candidate) if candidate else None
    result["latest_qc_agent_run"] = _agent_run_read(session, agent_run)
    result["review_decisions"] = [{column.name: getattr(decision, column.name) for column in decision.__table__.columns if column.name not in {"project_id", "asset_id", "qc_report_id"}} for decision in decisions]
    result["affected_downstream_node_keys"] = _downstream_keys(session, asset)
    revision_requests = list(session.scalars(
        select(AssetRevisionRequest)
        .where(AssetRevisionRequest.asset_id == asset.id)
        .order_by(AssetRevisionRequest.created_at.desc())
    ))
    result["revision_requests"] = [
        {column.name: getattr(item, column.name) for column in item.__table__.columns if column.name != "project_id"}
        for item in revision_requests
    ]
    return result


def quality_review_view(session: Session, project: Project) -> dict:
    repository = _quality(session)
    assets = repository.project_assets(project.id)
    asset_rows = [asset_read(session, asset) for asset in assets]
    nodes = []
    if project.active_snapshot_id:
        nodes = repository.snapshot_nodes(project.active_snapshot_id)
    assets_by_node: dict[str, list[Asset]] = {}
    for asset in assets:
        if asset.dag_node_id and asset.state != "deleted":
            assets_by_node.setdefault(asset.dag_node_id, []).append(asset)
    output_gaps = []
    required_node_ids: set[str] = set()
    for node in nodes:
        media_type = node.output_contract.get("media_type")
        if media_type not in {"image", "video", "audio"}:
            continue
        required_node_ids.add(node.id)
        node_assets = assets_by_node.get(node.id, [])
        if any(asset.state in {"approved", "used"} for asset in node_assets):
            continue
        if node_assets and any(asset.state in {"created", "verified", "review_required"} for asset in node_assets):
            continue
        item = repository.work_item_for_node(project.active_snapshot_id, node.id)
        attempt = repository.work_attempt(item.current_attempt_id) if item and item.current_attempt_id else None
        simulated = bool(attempt and attempt.response_manifest and attempt.response_manifest.get("media_created") is False)
        rejected = bool(node_assets)
        output_gaps.append({
            "code": "OUTPUT_NOT_APPROVED" if rejected else "SIMULATION_CREATED_NO_MEDIA" if simulated else "OUTPUT_NOT_REGISTERED",
            "dag_node_id": node.id,
            "node_key": node.node_key,
            "work_item_id": item.id if item else None,
            "message": "Registered outputs for this node are archived or rejected." if rejected else "Mock execution intentionally created no media." if simulated else "Required DAG output has no registered asset.",
        })
    counts = {state: sum(1 for asset in assets if asset.state == state) for state in ("created", "verified", "review_required", "approved", "archived")}
    stage_ready = bool(required_node_ids) and not output_gaps and all(
        any(asset.state in {"approved", "used"} for asset in assets_by_node.get(node_id, []))
        for node_id in required_node_ids
    )
    if counts["created"]:
        next_action = {"code": "VERIFY_ASSETS", "label": f"验证 {counts['created']} 个新素材"}
    elif counts["verified"]:
        next_action = {"code": "RUN_QC", "label": f"审核 {counts['verified']} 个已验证素材"}
    elif counts["review_required"]:
        next_action = {"code": "REVIEW_ASSETS", "label": f"确认 {counts['review_required']} 个审核结果"}
    elif output_gaps:
        next_action = {"code": "WAIT_FOR_OUTPUT_REGISTRATION", "label": "处理尚未登记的生产结果"}
    elif stage_ready:
        next_action = {"code": "START_EDITING", "label": "所需素材均已批准，可以进入剪辑"}
    else:
        next_action = {"code": "NO_ACTIVE_SNAPSHOT", "label": "当前没有活动生产快照"}
    return {
        "project_id": project.id,
        "project_status": project.status,
        "active_snapshot_id": project.active_snapshot_id,
        "assets": asset_rows,
        "output_gaps": output_gaps,
        "counts": counts,
        "stage_ready": stage_ready,
        "next_action": next_action,
    }


def asset_content_path(session: Session, project: Project, asset_id: str) -> tuple[Path, str]:
    asset = _require_asset(session, project, asset_id)
    if asset.state in {"created", "deleted"} or not asset.content_hash:
        raise QualityConflictError("ASSET_NOT_AVAILABLE", "Asset content is not verified and available.")
    path = _local_asset_path(asset.uri)
    if not path.is_file():
        raise QualityConflictError("ASSET_FILE_MISSING", "Verified asset file is missing from storage.")
    return path, asset.mime_type or "application/octet-stream"
