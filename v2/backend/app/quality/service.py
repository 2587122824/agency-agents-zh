from __future__ import annotations

import hashlib
import json
import struct
import wave
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from ..core.config import RUNTIME_ROOT
from ..db.models import (
    Asset,
    AssetReviewDecision,
    Project,
    ProjectEvent,
    QCFinding,
    QCReport,
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
from .contracts import RegisterAttemptAsset, ReviewAsset, RunAssetQC, VerifyAsset


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
    if policy.backend_kind != "local" or policy.local_root_ref != "v2.runtime.assets":
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


def run_asset_qc(session: Session, project: Project, asset_id: str, payload: RunAssetQC) -> dict:
    repository = _quality(session)
    receipt = _receipt(session, project.id, payload.command_id, "quality.run")
    if receipt:
        report = repository.qc_report(receipt.result_id)
        if not report:
            raise QualityConflictError("COMMAND_RESULT_MISSING", "QC command result no longer exists.")
        return qc_report_read(session, report)
    asset = _require_asset(session, project, asset_id)
    if asset.state != "verified":
        raise QualityConflictError("ASSET_NOT_VERIFIED", "Only a verified asset can enter QC.")
    if asset.row_version != payload.expected_row_version:
        raise QualityConflictError("ASSET_ROW_VERSION_MISMATCH", "Asset changed; refresh before QC.")
    node = repository.dag_node(asset.dag_node_id) if asset.dag_node_id else None
    if not node or node.snapshot_id != asset.snapshot_id:
        raise QualityConflictError("ASSET_DAG_REFERENCE_MISSING", "Asset DAG node reference is invalid.")
    findings: list[dict] = []
    if asset.asset_type in VISUAL_TYPES:
        snapshot_spec = node.output_contract
        video_spec_id = snapshot_spec.get("video_spec_version_id")
        snapshot = repository.snapshot(asset.snapshot_id)
        expected_width = snapshot.output_spec.get("width") if snapshot and video_spec_id else None
        expected_height = snapshot.output_spec.get("height") if snapshot and video_spec_id else None
        if expected_width and expected_height and (asset.width != expected_width or asset.height != expected_height):
            _add_finding(findings, "MEDIA_DIMENSIONS_INVALID", "blocked", {"actual": [asset.width, asset.height], "expected": [expected_width, expected_height]}, "output_spec.width_height", "block")
    if asset.asset_type == "video":
        expected_duration = node.input_contract.get("duration_ms")
        if expected_duration is not None and (asset.duration_ms is None or abs(asset.duration_ms - expected_duration) > 100):
            _add_finding(findings, "MEDIA_DURATION_INVALID", "blocked", {"actual_ms": asset.duration_ms, "expected_ms": expected_duration, "tolerance_ms": 100}, "input_contract.duration_ms", "block")
    if not any(finding["severity"] == "blocked" for finding in findings):
        shot = node.input_contract.get("shot", {})
        if asset.asset_type in VISUAL_TYPES:
            _add_finding(findings, "VISUAL_CONTENT_REVIEW_REQUIRED", "review_required", {
                "face_visibility": shot.get("face_visibility"),
                "text_policy": shot.get("text_policy"),
                "motion_requirement": shot.get("motion_requirement"),
                "automated_visual_analyzer_connected": False,
            }, "input_contract.shot", "manual_review")
        elif asset.asset_type == "audio":
            _add_finding(findings, "AUDIO_CONTENT_REVIEW_REQUIRED", "review_required", {"automated_audio_analyzer_connected": False}, None, "manual_review")
    if any(finding["severity"] == "blocked" for finding in findings):
        status = "blocked"
    elif any(finding["severity"] == "review_required" for finding in findings):
        status = "review_required"
    else:
        status = "passed"
    number = repository.next_report_number(asset.id)
    report = QCReport(project_id=project.id, snapshot_id=asset.snapshot_id, asset_id=asset.id, report_number=number, ruleset_version=RULESET_VERSION, status=status, analyzer="deterministic-file-contract")
    repository.add(report)
    repository.flush()
    for finding in findings:
        repository.add(QCFinding(qc_report_id=report.id, **finding))
    now = utc_now()
    if status == "blocked":
        asset.state = "archived"
        asset.archived_at = now
        item = repository.work_item_for_node(asset.snapshot_id, asset.dag_node_id)
        if item:
            item.status = "blocked"
            item.error = "ASSET_QC_BLOCKED: deterministic file contract failed"
            item.row_version += 1
        block_project(
            session,
            project,
            reason_code="ASSET_QC_BLOCKED",
            responsible_aggregate_type="asset",
            responsible_aggregate_id=asset.id,
            actor_type="system",
            actor_id=payload.actor_id,
            event_data={"qc_report_id": report.id},
        )
    elif status == "review_required":
        asset.state = "review_required"
        transition_project(
            session,
            project,
            ProjectStateTrigger.QUALITY_RECORDED,
            actor_type="system",
            actor_id=payload.actor_id,
            event_data={"asset_id": asset.id, "qc_report_id": report.id},
        )
    else:
        asset.state = "approved"
        asset.approved_at = now
        transition_project(
            session,
            project,
            ProjectStateTrigger.QUALITY_RECORDED,
            actor_type="system",
            actor_id=payload.actor_id,
            event_data={"asset_id": asset.id, "qc_report_id": report.id},
        )
    asset.row_version += 1
    _save_receipt(session, project.id, payload.command_id, "quality.run", "qc_report", report.id)
    event_type = "quality.review_required.v1" if status == "review_required" else "quality.blocked.v1" if status == "blocked" else "asset.approved.v1"
    _event(session, ProjectEvent(project_id=project.id, snapshot_id=asset.snapshot_id, event_type=event_type, aggregate_type="qc_report", aggregate_id=report.id, actor_type="system", actor_id=payload.actor_id, causation_id=payload.command_id, message="Asset quality contract evaluated.", data={"asset_id": asset.id, "qc_report_id": report.id, "status": status}))
    session.commit()
    return qc_report_read(session, report)


def review_asset(session: Session, project: Project, asset_id: str, payload: ReviewAsset, decision: str) -> dict:
    repository = _quality(session)
    command_type = f"asset.review.{decision}"
    receipt = _receipt(session, project.id, payload.command_id, command_type)
    if receipt:
        return asset_read(session, _require_asset(session, project, receipt.result_id))
    asset = _require_asset(session, project, asset_id)
    report = repository.qc_report(payload.qc_report_id)
    if not report or report.asset_id != asset.id or report.project_id != project.id:
        raise QualityNotFoundError("QC report not found for asset")
    if asset.state != "review_required" or report.status != "review_required":
        raise QualityConflictError("ASSET_NOT_AWAITING_REVIEW", "Only an asset with a review_required QC report can be reviewed.")
    if asset.row_version != payload.expected_row_version:
        raise QualityConflictError("ASSET_ROW_VERSION_MISMATCH", "Asset changed; refresh before review.")
    if repository.has_review_decision(report.id):
        raise QualityConflictError("QC_REPORT_ALREADY_REVIEWED", "This QC report already has a human decision.")
    now = utc_now()
    repository.add(AssetReviewDecision(project_id=project.id, asset_id=asset.id, qc_report_id=report.id, decision=decision, rationale=payload.rationale, actor_id=payload.actor_id))
    report.reviewed_at = now
    report.reviewed_by = payload.actor_id
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
    _event(session, ProjectEvent(project_id=project.id, snapshot_id=asset.snapshot_id, event_type=event_type, aggregate_type="qc_report", aggregate_id=report.id, actor_type="user", actor_id=payload.actor_id, causation_id=payload.command_id, message="Human asset review decision recorded.", data={"asset_id": asset.id, "qc_report_id": report.id, "decision": decision, "rationale": payload.rationale}))
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
    decisions = repository.review_decisions(asset.id)
    result = {column.name: getattr(asset, column.name) for column in asset.__table__.columns if column.name not in {"provider_output_manifest", "deleted_at"}}
    result["node_key"] = node.node_key if node else None
    result["latest_qc_report"] = qc_report_read(session, report) if report else None
    result["review_decisions"] = [{column.name: getattr(decision, column.name) for column in decision.__table__.columns if column.name not in {"project_id", "asset_id", "qc_report_id"}} for decision in decisions]
    result["affected_downstream_node_keys"] = _downstream_keys(session, asset)
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
        next_action = {"code": "VERIFY_ASSETS", "label": f"Verify {counts['created']} registered assets"}
    elif counts["verified"]:
        next_action = {"code": "RUN_QC", "label": f"Run QC for {counts['verified']} verified assets"}
    elif counts["review_required"]:
        next_action = {"code": "REVIEW_ASSETS", "label": f"Review {counts['review_required']} assets"}
    elif output_gaps:
        next_action = {"code": "WAIT_FOR_OUTPUT_REGISTRATION", "label": "Resolve missing production outputs"}
    elif stage_ready:
        next_action = {"code": "START_EDITING", "label": "All required assets are approved"}
    else:
        next_action = {"code": "NO_ACTIVE_SNAPSHOT", "label": "No active production snapshot"}
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
