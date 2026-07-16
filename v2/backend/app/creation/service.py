from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..db.models import (
    AgentInputManifest,
    AgentRun,
    Attachment,
    AttachmentBinding,
    CommandReceipt,
    ClarificationRequest,
    Entity,
    EntityVersion,
    Message,
    Project,
    ProjectEvent,
    RequirementCandidate,
    RequirementVersion,
    utc_now,
)
from ..core.config import RUNTIME_ROOT
from ..repositories import (
    CreationRepository,
    SqlAlchemyCommandRepository,
    SqlAlchemyCreationRepository,
    SqlAlchemyDecisionRepository,
    SqlAlchemyEventRepository,
)
from .contracts import (
    AcceptCandidate,
    AttachmentCreate,
    BindingCreate,
    GenerateCandidate,
    MessageCreate,
    NextAction,
    RejectCandidate,
    ResolveClarification,
)
from .completeness import evaluate_requirement, validate_clarification_value


class CreationConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CreationNotFoundError(LookupError):
    pass


def _event(session: Session, project_id: str, event_type: str, message: str, data: dict | None = None) -> None:
    SqlAlchemyEventRepository(session).add(
        ProjectEvent(project_id=project_id, event_type=event_type, message=message, data=data or {})
    )


def _creation(session: Session) -> CreationRepository:
    return SqlAlchemyCreationRepository(session)


def _receipt(session: Session, project_id: str, command_id: str) -> CommandReceipt | None:
    return SqlAlchemyCommandRepository(session).get(project_id, command_id)


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


def _receipt_result(session: Session, receipt: CommandReceipt, model_type):
    result = SqlAlchemyCommandRepository(session).get_result(model_type, receipt.result_id)
    if result is None:
        raise CreationConflictError("IDEMPOTENCY_RESULT_MISSING", "幂等命令的原始结果不存在。")
    return result


def ensure_initial_requirement(session: Session, project: Project) -> RequirementVersion:
    repository = _creation(session)
    active = active_requirement(session, project.id)
    if active:
        return active
    fields = {
        "title": project.title,
        "core_topic": project.core_topic,
        "duration_seconds": project.duration_seconds,
        "aspect_ratio": project.aspect_ratio,
        "audio_mode": project.audio_mode,
    }
    sources = {key: {"type": "user", "reference_id": project.id} for key in fields}
    version = RequirementVersion(
        project_id=project.id,
        version_number=1,
        fields=fields,
        field_sources=sources,
        created_by="project.create",
    )
    repository.add(version)
    repository.flush()
    _event(session, project.id, "requirement.confirmed.v1", "初始需求版本已建立", {"requirement_version_id": version.id})
    return version


def active_requirement(session: Session, project_id: str) -> RequirementVersion | None:
    return _creation(session).active_requirement(project_id)


def consumed_message_ids(session: Session, version: RequirementVersion) -> set[str]:
    if not version.candidate_id:
        return set()
    repository = _creation(session)
    candidate = repository.requirement_candidate(version.candidate_id)
    if not candidate:
        return set()
    run = repository.agent_run(candidate.agent_run_id)
    if not run:
        return set()
    manifest = repository.agent_manifest(run.input_manifest_id)
    return set(manifest.message_ids) if manifest else set()


def sync_clarifications(
    session: Session,
    project: Project,
    base: RequirementVersion,
    *,
    fields: dict | None = None,
    field_sources: dict | None = None,
    candidate_id: str | None = None,
) -> list[ClarificationRequest]:
    repository = _creation(session)
    existing = repository.pending_clarifications(project.id)
    for clarification in existing:
        if clarification.base_requirement_version_id != base.id:
            clarification.status = "stale"
    missing = evaluate_requirement(fields or base.fields, field_sources or base.field_sources)
    pending_for_base = {
        item.field_key: item
        for item in existing
        if item.base_requirement_version_id == base.id and item.status == "pending"
    }
    for item in missing:
        if item["field_key"] in pending_for_base:
            continue
        clarification = ClarificationRequest(
            project_id=project.id,
            candidate_id=candidate_id,
            base_requirement_version_id=base.id,
            field_key=item["field_key"],
            reason_code=item["reason_code"],
            question=item["question"],
            options=item["options"],
            risk_level=item["risk_level"],
        )
        repository.add(clarification)
        repository.flush()
        pending_for_base[clarification.field_key] = clarification
        _event(session, project.id, "clarification.requested.v1", "需求字段需要用户澄清", {
            "clarification_id": clarification.id,
            "field_key": clarification.field_key,
            "risk_level": clarification.risk_level,
        })
    return [pending_for_base[item["field_key"]] for item in missing]


def add_message(session: Session, project: Project, payload: MessageCreate) -> Message:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, Message)
    if payload.reply_to_message_id:
        reply = repository.message(payload.reply_to_message_id)
        if not reply or reply.project_id != project.id:
            raise CreationNotFoundError("Reply message not found")
    message = Message(
        project_id=project.id,
        role="user",
        content=payload.content.strip(),
        reply_to_message_id=payload.reply_to_message_id,
    )
    repository.add(message)
    repository.flush()
    stale_candidates = repository.reviewable_candidates(project.id)
    for candidate in stale_candidates:
        candidate.status = "stale"
        candidate.decided_at = utc_now()
        _event(session, project.id, "candidate.stale.v1", "新消息使旧候选过期", {"candidate_id": candidate.id})
    _save_receipt(session, project.id, payload.command_id, "message.add", "message", message.id)
    _event(session, project.id, "conversation.message_added.v1", "用户需求消息已保存", {"message_id": message.id})
    session.commit()
    return message


def _manifest_payload(session: Session, project: Project, base: RequirementVersion) -> dict:
    repository = _creation(session)
    consumed = consumed_message_ids(session, base)
    messages = repository.manifest_messages(project.id)
    messages = [item for item in messages if item.id not in consumed]
    bindings = repository.confirmed_bindings(project.id)
    decisions = SqlAlchemyDecisionRepository(session).resolved_for_project(project.id)
    return {
        "active_requirement": {"id": base.id, "fields": base.fields},
        "messages": [{"id": item.id, "content": item.content, "reply_to": item.reply_to_message_id} for item in messages],
        "confirmed_attachment_bindings": [
            {"id": item.id, "type": item.binding_type, "entity_id": item.entity_id}
            for item in bindings
        ],
        "confirmed_decisions": [
            {"id": item.id, "key": item.key, "label": item.label, "value": item.value, "source": item.source}
            for item in decisions
        ],
        "system_config_version": "v2.creation.mock.v1",
    }


def generate_candidate(session: Session, project: Project, payload: GenerateCandidate) -> RequirementCandidate:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, RequirementCandidate)
    base = ensure_initial_requirement(session, project)
    if base.id != payload.expected_base_version_id:
        raise CreationConflictError("PROJECT_VERSION_CONFLICT", "活动需求版本已变化，请刷新后重新生成候选。")
    pending_clarifications = sync_clarifications(session, project, base)
    if pending_clarifications:
        session.commit()
        raise CreationConflictError("REQUIREMENT_INCOMPLETE", "请先解决阻断性的需求澄清。")
    manifest_payload = _manifest_payload(session, project, base)
    if not manifest_payload["messages"]:
        raise CreationConflictError("NO_NEW_REQUIREMENT_INPUT", "没有尚未处理的新需求消息。")
    serialized = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=base.id,
        message_ids=[item["id"] for item in manifest_payload["messages"]],
        decision_ids=[item["id"] for item in manifest_payload["confirmed_decisions"]],
        attachment_binding_ids=[item["id"] for item in manifest_payload["confirmed_attachment_bindings"]],
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    run = AgentRun(
        project_id=project.id,
        status="running",
        input_manifest_id=manifest.id,
        started_at=utc_now(),
    )
    repository.add(run)
    repository.flush()
    _event(session, project.id, "agent.run_created.v1", "Creative Mock Agent 已开始", {"agent_run_id": run.id})

    fields = dict(base.fields)
    sources = dict(base.field_sources)
    changes: list[dict] = []
    if manifest_payload["messages"]:
        latest = manifest_payload["messages"][-1]
        previous = fields.get("creative_direction")
        fields["creative_direction"] = latest["content"]
        sources["creative_direction"] = {"type": "agent_proposal", "reference_id": latest["id"]}
        changes.append({
            "field_key": "creative_direction",
            "before": previous,
            "after": latest["content"],
            "source_message_id": latest["id"],
            "risk_level": "medium",
        })
    candidate = RequirementCandidate(
        project_id=project.id,
        base_requirement_version_id=base.id,
        agent_run_id=run.id,
        fields=fields,
        field_sources=sources,
        change_summary=changes,
    )
    repository.add(candidate)
    repository.flush()
    run.status = "succeeded"
    run.raw_output = {"requirement_candidate": fields, "change_summary": changes}
    run.parsed_candidate_id = candidate.id
    run.finished_at = utc_now()
    _save_receipt(session, project.id, payload.command_id, "candidate.generate", "requirement_candidate", candidate.id)
    _event(session, project.id, "agent.run_succeeded.v1", "Creative Mock Agent 已返回候选", {"agent_run_id": run.id})
    _event(session, project.id, "candidate.generated.v1", "需求候选等待用户确认", {"candidate_id": candidate.id})
    session.commit()
    return candidate


def accept_candidate(
    session: Session,
    project: Project,
    candidate_id: str,
    payload: AcceptCandidate,
) -> RequirementVersion:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, RequirementVersion)
    candidate = repository.requirement_candidate(candidate_id)
    if not candidate or candidate.project_id != project.id:
        raise CreationNotFoundError("Candidate not found")
    active = active_requirement(session, project.id)
    if not active or active.id != payload.expected_base_version_id or candidate.base_requirement_version_id != active.id:
        raise CreationConflictError("CANDIDATE_BASE_VERSION_STALE", "这个候选基于旧需求版本，不能确认。")
    if candidate.status != "awaiting_review":
        raise CreationConflictError("CANDIDATE_NOT_REVIEWABLE", f"候选状态为 {candidate.status}，不能确认。")
    missing = evaluate_requirement(candidate.fields, candidate.field_sources)
    if missing:
        candidate.status = "validation_failed"
        candidate.validation_errors = missing
        sync_clarifications(
            session,
            project,
            active,
            fields=candidate.fields,
            field_sources=candidate.field_sources,
            candidate_id=candidate.id,
        )
        session.commit()
        raise CreationConflictError("CANDIDATE_REQUIREMENT_INCOMPLETE", "候选缺少阻断字段，不能确认。")
    active.is_active = False
    version = RequirementVersion(
        project_id=project.id,
        version_number=active.version_number + 1,
        fields=candidate.fields,
        field_sources=candidate.field_sources,
        candidate_id=candidate.id,
        created_by=payload.actor_id,
    )
    repository.add(version)
    repository.flush()
    candidate.status = "accepted"
    candidate.decided_at = utc_now()
    for pending in repository.reviewable_candidates(project.id, exclude_id=candidate.id):
        pending.status = "stale"
        pending.decided_at = utc_now()
    _save_receipt(session, project.id, payload.command_id, "candidate.accept", "requirement_version", version.id)
    _event(session, project.id, "requirement.confirmed.v1", "需求候选已提升为正式版本", {
        "candidate_id": candidate.id, "requirement_version_id": version.id,
    })
    session.commit()
    return version


def resolve_clarification(
    session: Session,
    project: Project,
    clarification_id: str,
    payload: ResolveClarification,
) -> RequirementVersion:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, RequirementVersion)
    clarification = repository.clarification(clarification_id)
    if not clarification or clarification.project_id != project.id:
        raise CreationNotFoundError("Clarification not found")
    active = active_requirement(session, project.id)
    if (
        not active
        or active.id != payload.expected_base_version_id
        or clarification.base_requirement_version_id != active.id
    ):
        raise CreationConflictError("CLARIFICATION_BASE_VERSION_STALE", "这个澄清基于旧需求版本，不能提交。")
    if clarification.status != "pending":
        raise CreationConflictError("CLARIFICATION_NOT_PENDING", f"澄清状态为 {clarification.status}，不能提交。")
    try:
        value = validate_clarification_value(clarification.field_key, payload.value)
    except ValueError as exc:
        raise CreationConflictError(str(exc), "澄清值不符合字段合同。") from exc
    fields = dict(active.fields)
    sources = dict(active.field_sources)
    fields[clarification.field_key] = value
    sources[clarification.field_key] = {"type": "user_confirmation", "reference_id": clarification.id}
    active.is_active = False
    version = RequirementVersion(
        project_id=project.id,
        version_number=active.version_number + 1,
        fields=fields,
        field_sources=sources,
        created_by=payload.actor_id,
    )
    repository.add(version)
    repository.flush()
    clarification.status = "resolved"
    clarification.resolution = value
    clarification.resolved_at = utc_now()
    for item in repository.pending_clarifications(project.id):
        if item.id == clarification.id:
            continue
        item.status = "stale"
    for candidate in repository.reviewable_candidates(project.id):
        candidate.status = "stale"
        candidate.decided_at = utc_now()
    _save_receipt(session, project.id, payload.command_id, "clarification.resolve", "requirement_version", version.id)
    _event(session, project.id, "clarification.resolved.v1", "用户已解决需求澄清", {
        "clarification_id": clarification.id,
        "requirement_version_id": version.id,
        "field_key": clarification.field_key,
    })
    _event(session, project.id, "requirement.confirmed.v1", "澄清结果已创建新的需求版本", {
        "requirement_version_id": version.id,
    })
    session.commit()
    return version


def reject_candidate(
    session: Session,
    project: Project,
    candidate_id: str,
    payload: RejectCandidate,
) -> RequirementCandidate:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, RequirementCandidate)
    candidate = repository.requirement_candidate(candidate_id)
    if not candidate or candidate.project_id != project.id:
        raise CreationNotFoundError("Candidate not found")
    if candidate.status != "awaiting_review":
        raise CreationConflictError("CANDIDATE_NOT_REVIEWABLE", f"候选状态为 {candidate.status}，不能拒绝。")
    candidate.status = "rejected"
    candidate.decided_at = utc_now()
    candidate.validation_errors = [{"code": "USER_REJECTED", "message": payload.reason}]
    _save_receipt(session, project.id, payload.command_id, "candidate.reject", "requirement_candidate", candidate.id)
    _event(session, project.id, "candidate.rejected.v1", "用户已拒绝需求候选", {"candidate_id": candidate.id})
    session.commit()
    return candidate


def register_attachment(
    session: Session,
    project: Project,
    payload: AttachmentCreate,
    file_bytes: bytes,
) -> Attachment:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, Attachment)
    if payload.message_id:
        message = repository.message(payload.message_id)
        if not message or message.project_id != project.id:
            raise CreationNotFoundError("Message not found")
    detected_type = detect_media_type(file_bytes)
    if detected_type is None or detected_type != payload.mime_type:
        raise CreationConflictError("ATTACHMENT_TYPE_MISMATCH", "附件内容与声明的媒体类型不一致。")
    actual_hash = hashlib.sha256(file_bytes).hexdigest()
    if actual_hash != payload.content_hash.lower():
        raise CreationConflictError("ATTACHMENT_HASH_MISMATCH", "附件内容哈希校验失败。")
    attachment = Attachment(
        project_id=project.id,
        message_id=payload.message_id,
        original_filename=payload.original_filename,
        mime_type=payload.mime_type,
        byte_size=payload.byte_size,
        content_hash=payload.content_hash.lower(),
        storage_path="pending",
        verification_status="verified",
    )
    repository.add(attachment)
    repository.flush()
    suffix = Path(payload.original_filename).suffix.lower()[:12]
    target_dir = RUNTIME_ROOT / "uploads" / project.id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{attachment.id}{suffix}"
    target.write_bytes(file_bytes)
    attachment.storage_path = str(target.relative_to(RUNTIME_ROOT))
    _save_receipt(session, project.id, payload.command_id, "attachment.register", "attachment", attachment.id)
    _event(session, project.id, "attachment.verified.v1", "附件元数据已验证登记，尚未绑定", {"attachment_id": attachment.id})
    session.commit()
    return attachment


def detect_media_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        return "audio/wav"
    if content.startswith(b"ID3") or (len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) > 12 and content[4:8] == b"ftyp":
        return "video/mp4"
    return None


def bind_attachment(
    session: Session,
    project: Project,
    attachment_id: str,
    payload: BindingCreate,
) -> AttachmentBinding:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, AttachmentBinding)
    attachment = repository.attachment(attachment_id)
    if not attachment or attachment.project_id != project.id:
        raise CreationNotFoundError("Attachment not found")
    if attachment.verification_status != "verified":
        raise CreationConflictError("ATTACHMENT_NOT_VERIFIED", "附件尚未通过验证，不能绑定。")
    if payload.binding_type != "inspiration_only" and not payload.entity_id:
        raise CreationConflictError("ENTITY_ID_REQUIRED", "该绑定类型必须明确选择实体 ID。")
    entity_version_id = None
    if payload.binding_type != "inspiration_only":
        entity_type = {
            "identity_reference": "character",
            "outfit_reference": "outfit",
            "scene_reference": "scene",
            "product_reference": "product",
            "voice_sample": "voice",
        }[payload.binding_type]
        entity = repository.entity(payload.entity_id)
        if entity and (entity.project_id != project.id or entity.entity_type != entity_type):
            raise CreationConflictError("ENTITY_TYPE_CONFLICT", "实体 ID 已存在，但项目或实体类型不匹配。")
        if not entity:
            entity = Entity(
                id=payload.entity_id,
                project_id=project.id,
                entity_type=entity_type,
                display_name=payload.entity_id,
            )
            repository.add(entity)
            repository.flush()
        if payload.entity_version_id:
            selected_version = repository.entity_version(payload.entity_version_id)
            if (
                not selected_version
                or selected_version.project_id != project.id
                or selected_version.entity_id != entity.id
                or selected_version.status != "confirmed"
            ):
                raise CreationConflictError("ENTITY_VERSION_NOT_FOUND", "明确选择的实体版本不存在或不可使用。")
            entity_version_id = selected_version.id
        else:
            active_version = repository.active_entity_version(entity.id)
            next_version = (active_version.version_number + 1) if active_version else 1
            if active_version:
                active_version.is_active = False
            selected_version = EntityVersion(
                project_id=project.id,
                entity_id=entity.id,
                version_number=next_version,
                attributes={"binding_type": payload.binding_type},
                source_attachment_id=attachment.id,
                created_by=payload.actor_id,
            )
            repository.add(selected_version)
            repository.flush()
            entity_version_id = selected_version.id
            _event(session, project.id, "entity.version_confirmed.v1", "附件绑定已创建实体版本", {
                "entity_id": entity.id,
                "entity_version_id": selected_version.id,
                "entity_type": entity.entity_type,
            })
    binding = AttachmentBinding(
        project_id=project.id,
        attachment_id=attachment.id,
        binding_type=payload.binding_type,
        entity_id=payload.entity_id,
        entity_version_id=entity_version_id,
        confirmed_by=payload.actor_id,
    )
    repository.add(binding)
    repository.flush()
    _save_receipt(session, project.id, payload.command_id, "attachment.bind", "attachment_binding", binding.id)
    _event(session, project.id, "attachment.binding_confirmed.v1", "用户已确认附件绑定", {
        "attachment_id": attachment.id, "binding_id": binding.id, "binding_type": binding.binding_type,
    })
    session.commit()
    return binding


def creation_center_view(session: Session, project: Project) -> dict:
    repository = _creation(session)
    active = ensure_initial_requirement(session, project)
    sync_clarifications(session, project, active)
    session.commit()
    messages = repository.view_messages(project.id)
    candidates = repository.candidate_history(project.id)
    runs = repository.agent_runs(project.id)
    attachments = repository.attachments(project.id)
    binding_rows = repository.bindings(project.id)
    bindings_by_attachment: dict[str, list[AttachmentBinding]] = {}
    for binding in binding_rows:
        bindings_by_attachment.setdefault(binding.attachment_id, []).append(binding)
    clarifications = repository.active_pending_clarifications(project.id, active.id)
    current = next((item for item in candidates if item.status == "awaiting_review"), None)
    confirmed_attachment_ids = {
        binding.attachment_id for binding in binding_rows if binding.status == "confirmed"
    }
    unbound = [item for item in attachments if item.id not in confirmed_attachment_ids]
    consumed = consumed_message_ids(session, active)
    unconsumed_messages = [item for item in messages if item.id not in consumed]
    if clarifications:
        next_action = NextAction(
            code="RESOLVE_REQUIRED_CLARIFICATIONS",
            target_ids=[item.id for item in clarifications],
            label="解决阻断性需求",
        )
    elif current:
        next_action = NextAction(code="REVIEW_REQUIREMENT_CANDIDATE", target_ids=[current.id], label="审核需求候选")
    elif unbound:
        next_action = NextAction(code="CLASSIFY_ATTACHMENT", target_ids=[item.id for item in unbound], label="确认附件用途")
    elif unconsumed_messages:
        next_action = NextAction(code="GENERATE_REQUIREMENT_CANDIDATE", label="生成需求候选")
    elif messages:
        next_action = NextAction(code="REQUIREMENT_READY_FOR_PLANNING", label="进入创意方案规划")
    else:
        next_action = NextAction(code="ADD_REQUIREMENT_MESSAGE", label="补充创作需求")
    return {
        "project_id": project.id,
        "active_requirement": active,
        "messages": messages,
        "current_candidate": current,
        "candidate_history": candidates,
        "pending_clarifications": clarifications,
        "latest_agent_run": runs[0] if runs else None,
        "agent_runs": runs,
        "attachments": [
            {
                "id": item.id,
                "original_filename": item.original_filename,
                "mime_type": item.mime_type,
                "byte_size": item.byte_size,
                "content_hash": item.content_hash,
                "verification_status": item.verification_status,
                "created_at": item.created_at,
                "bindings": bindings_by_attachment.get(item.id, []),
            }
            for item in attachments
        ],
        "next_action": next_action,
    }
