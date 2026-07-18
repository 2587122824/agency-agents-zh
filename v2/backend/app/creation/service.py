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
    ConversationSession,
    ClarificationRequest,
    CreativeSuggestionSelection,
    CreativeTurnProposal,
    Entity,
    EntityVersion,
    Message,
    Project,
    ProjectEvent,
    RequirementCandidate,
    RequirementVersion,
    new_id,
    utc_now,
)
from .agent_gateway import AgentGatewayError, CreativeAgentGateway, ProposedFieldUpdate
from ..core.config import RUNTIME_ROOT
from ..orchestration.project_transitions import ProjectStateTrigger, transition_project
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
    RetryCreativeTurn,
    SelectCreativeSuggestion,
    StartConversationSession,
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
        ProjectEvent(
            project_id=project_id,
            event_type=event_type,
            aggregate_type="project",
            aggregate_id=project_id,
            actor_type="system",
            actor_id="application",
            message=message,
            data=data or {},
        )
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


def ensure_conversation_session(
    session: Session,
    project: Project,
    *,
    started_by: str = "system",
) -> ConversationSession:
    repository = _creation(session)
    active = repository.active_conversation_session(project.id)
    if active:
        return active
    conversation = ConversationSession(project_id=project.id, started_by=started_by)
    repository.add(conversation)
    repository.flush()
    _event(session, project.id, "conversation.session_started.v1", "新的创作会话已开启", {
        "conversation_session_id": conversation.id,
    })
    return conversation


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
    conversation = ensure_conversation_session(session, project, started_by=payload.actor_id)
    if payload.reply_to_message_id:
        reply = repository.message(payload.reply_to_message_id)
        if (
            not reply
            or reply.project_id != project.id
            or reply.conversation_session_id != conversation.id
        ):
            raise CreationNotFoundError("Reply message not found")
    message = Message(
        project_id=project.id,
        conversation_session_id=conversation.id,
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
    for proposal in repository.creative_proposals(project.id):
        if proposal.status == "active":
            proposal.status = "stale"
    _save_receipt(session, project.id, payload.command_id, "message.add", "message", message.id)
    _event(session, project.id, "conversation.message_added.v1", "用户需求消息已保存", {"message_id": message.id})
    transition_project(
        session,
        project,
        ProjectStateTrigger.MESSAGE_ADDED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"message_id": message.id},
    )
    session.commit()
    return message


def start_conversation_session(
    session: Session,
    project: Project,
    payload: StartConversationSession,
) -> ConversationSession:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, ConversationSession)
    if any(item.status == "running" for item in repository.agent_runs(project.id)):
        raise CreationConflictError("AGENT_RUN_IN_PROGRESS", "创作制片人运行中，不能开启新会话。")
    current = repository.active_conversation_session(project.id)
    if current:
        current.status = "closed"
        current.ended_at = utc_now()
    for proposal in repository.creative_proposals(project.id):
        if proposal.status == "active":
            proposal.status = "stale"
    conversation = ConversationSession(project_id=project.id, started_by=payload.actor_id)
    repository.add(conversation)
    repository.flush()
    _save_receipt(
        session,
        project.id,
        payload.command_id,
        "conversation.session.start",
        "conversation_session",
        conversation.id,
    )
    _event(session, project.id, "conversation.session_started.v1", "用户已开启新的创作会话", {
        "conversation_session_id": conversation.id,
        "previous_conversation_session_id": current.id if current else None,
    })
    session.commit()
    return conversation


def _manifest_payload(
    session: Session,
    project: Project,
    base: RequirementVersion,
    conversation_session: ConversationSession,
) -> dict:
    repository = _creation(session)
    messages = repository.manifest_messages(project.id, conversation_session.id)
    message_ids = {item.id for item in messages}
    proposal_selections = repository.suggestion_selections(project.id)
    selections_by_proposal: dict[str, list[dict]] = {}
    for selection in proposal_selections:
        selections_by_proposal.setdefault(selection.proposal_id, []).append({
            "id": selection.id,
            "suggestion_set_id": selection.suggestion_set_id,
            "option_id": selection.option_id,
        })
    proposals = [
        proposal
        for proposal in sorted(repository.creative_proposals(project.id), key=lambda item: (item.created_at, item.id))
        if proposal.assistant_message_id in message_ids
        and proposal.base_requirement_version_id == base.id
        and proposal.suggestion_sets
    ]
    proposal_history = []
    for proposal in proposals:
        selection_summaries = []
        for selection in selections_by_proposal.get(proposal.id, []):
            suggestion_set = next(
                (item for item in proposal.suggestion_sets if item.get("id") == selection["suggestion_set_id"]),
                None,
            )
            option = next(
                (
                    item
                    for item in (suggestion_set or {}).get("options", [])
                    if item.get("id") == selection["option_id"]
                ),
                None,
            )
            if suggestion_set and option:
                selection_summaries.append({
                    "suggestion_set_title": suggestion_set.get("title"),
                    "option_label": option.get("label"),
                    "option_summary": option.get("summary"),
                })
        proposal_history.append({
            "assistant_message_id": proposal.assistant_message_id,
            "suggestion_sets": [
                {
                    "category": suggestion_set.get("category"),
                    "title": suggestion_set.get("title"),
                    "options": [
                        {
                            "label": option.get("label"),
                            "summary": option.get("summary"),
                            "recommended": bool(option.get("recommended")),
                        }
                        for option in suggestion_set.get("options", [])
                    ],
                }
                for suggestion_set in proposal.suggestion_sets
            ],
            "selections": selection_summaries,
        })
    latest_reply_to = messages[-1].reply_to_message_id if messages and messages[-1].role == "user" else None
    scoped_proposal = next(
        (proposal for proposal in proposals if proposal.assistant_message_id == latest_reply_to),
        None,
    )
    selection_scope = None
    if scoped_proposal:
        selected_set_ids = {
            item["suggestion_set_id"] for item in selections_by_proposal.get(scoped_proposal.id, [])
        }
        selectable_sets = [
            item for item in scoped_proposal.suggestion_sets if item.get("id") not in selected_set_ids
        ]
        if selectable_sets:
            selection_scope = {
                "proposal_id": scoped_proposal.id,
                "assistant_message_id": scoped_proposal.assistant_message_id,
                "suggestion_sets": selectable_sets,
            }
    bindings = repository.confirmed_bindings(project.id)
    decisions = SqlAlchemyDecisionRepository(session).resolved_for_project(project.id)
    attachment_bindings = []
    for binding in bindings:
        attachment = repository.attachment(binding.attachment_id)
        attachment_bindings.append({
            "id": binding.id,
            "type": binding.binding_type,
            "entity_id": binding.entity_id,
            "attachment": None if not attachment else {
                "id": attachment.id,
                "original_filename": attachment.original_filename,
                "mime_type": attachment.mime_type,
                "byte_size": attachment.byte_size,
                "verification_status": attachment.verification_status,
                "content_access": "metadata_only",
            },
        })
    return {
        "runtime_context": {
            "assistant_name": "片场创作制片人",
            "current_time": utc_now().isoformat(),
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
        },
        "project_context": {
            "project_id": project.id,
            "project_stage": project.status,
            "active_requirement": {"id": base.id, "fields": base.fields},
            "confirmed_attachment_bindings": attachment_bindings,
            "confirmed_decisions": [
                {"id": item.id, "key": item.key, "label": item.label, "value": item.value, "source": item.source}
                for item in decisions
            ],
        },
        "conversation": {
            "session_id": conversation_session.id,
            "messages": [
                {
                    "id": item.id,
                    "role": item.role,
                    "content": item.content,
                    "reply_to": item.reply_to_message_id,
                }
                for item in messages
            ],
            "proposal_history": proposal_history,
            "selection_scope": selection_scope,
        },
        "system_config_version": "v2.creation.mock.v1",
        "requirement_schema_version": "creative-requirement.v2",
    }


def _validated_update_value(field_key: str, value):
    if field_key in {
        "title", "core_topic", "content_goal", "platform", "target_audience",
        "visual_style", "tone", "content_structure", "call_to_action", "creative_direction",
    }:
        if not isinstance(value, str) or not value.strip():
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_VALUE_INVALID", f"字段 {field_key} 必须是非空文本。")
        return value.strip()
    if field_key == "creative_constraints":
        if not isinstance(value, list) or len(value) > 20 or any(not isinstance(item, str) or not item.strip() for item in value):
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_VALUE_INVALID", "创作限制必须是不超过 20 项的非空文本列表。")
        return [item.strip() for item in value]
    if field_key == "duration_seconds":
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 3600:
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_VALUE_INVALID", "目标时长必须是 5 到 3600 秒的整数。")
        return value
    if field_key == "aspect_ratio":
        if value not in {"9:16", "16:9", "1:1"}:
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_VALUE_INVALID", "画幅必须是 9:16、16:9 或 1:1。")
        return value
    if field_key == "audio_mode":
        if value not in {"off", "voiceover"}:
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_VALUE_INVALID", "音频模式必须是 off 或 voiceover。")
        return value
    raise AgentGatewayError("AGENT_MODEL_OUTPUT_FIELD_UNSUPPORTED", f"模型返回了不支持的字段 {field_key}。")


def _field_risk(field_key: str) -> str:
    if field_key == "audio_mode":
        return "high"
    if field_key in {"duration_seconds", "aspect_ratio", "platform", "target_audience", "visual_style"}:
        return "medium"
    return "low"


def _validate_update_sources(update, valid_user_message_ids: set[str]) -> None:
    if not set(update.source_message_ids).issubset(valid_user_message_ids):
        raise AgentGatewayError("AGENT_MODEL_OUTPUT_SOURCE_INVALID", "模型返回了非用户消息或输入清单之外的消息引用。")


def _apply_updates(
    base_fields: dict,
    base_sources: dict,
    updates,
    *,
    valid_user_message_ids: set[str],
    source_type: str,
    source_reference_id: str | None = None,
) -> tuple[dict, dict, list[dict]]:
    fields = dict(base_fields)
    sources = dict(base_sources)
    changes: list[dict] = []
    seen_fields: set[str] = set()
    for update in updates:
        if update.field_key in seen_fields:
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_FIELD_DUPLICATE", f"模型重复更新字段 {update.field_key}。")
        _validate_update_sources(update, valid_user_message_ids)
        seen_fields.add(update.field_key)
        value = _validated_update_value(update.field_key, update.value)
        previous = fields.get(update.field_key)
        fields[update.field_key] = value
        sources[update.field_key] = {
            "type": source_type,
            "reference_id": source_reference_id or update.source_message_ids[-1],
        }
        changes.append({
            "field_key": update.field_key,
            "before": previous,
            "after": value,
            "source_message_ids": update.source_message_ids,
            "risk_level": _field_risk(update.field_key),
        })
    return fields, sources, changes


def _resolve_proposal_selections(
    repository: CreationRepository,
    project: Project,
    base: RequirementVersion,
    selections,
    *,
    conversation_message_ids: set[str],
    current_message_id: str,
    current_reply_to_message_id: str | None,
) -> list[dict]:
    resolved: list[dict] = []
    selected_sets: set[tuple[str, str]] = set()
    selected_fields: set[str] = set()
    for selection in selections:
        if selection.source_message_ids != [current_message_id]:
            raise AgentGatewayError(
                "AGENT_MODEL_SELECTION_SOURCE_INVALID",
                "自然语言选项选择只能引用当前用户消息。",
            )
        key = (selection.proposal_id, selection.suggestion_set_id)
        if key in selected_sets:
            raise AgentGatewayError("AGENT_MODEL_SELECTION_DUPLICATE", "模型重复选择了同一个建议组。")
        selected_sets.add(key)
        proposal = repository.creative_proposal(selection.proposal_id)
        if (
            not proposal
            or proposal.project_id != project.id
            or proposal.base_requirement_version_id != base.id
            or proposal.assistant_message_id not in conversation_message_ids
        ):
            raise AgentGatewayError("AGENT_MODEL_SELECTION_PROPOSAL_INVALID", "模型引用的历史创作提案不在当前输入清单中。")
        if not current_reply_to_message_id or proposal.assistant_message_id != current_reply_to_message_id:
            raise AgentGatewayError(
                "AGENT_MODEL_SELECTION_REPLY_SCOPE_INVALID",
                "模型引用的创作提案不属于当前用户消息精确回复的助手消息。",
            )
        suggestion_set = next(
            (item for item in proposal.suggestion_sets if item.get("id") == selection.suggestion_set_id),
            None,
        )
        if suggestion_set is None:
            raise AgentGatewayError("AGENT_MODEL_SELECTION_SET_INVALID", "模型引用的建议组不存在。")
        option = next(
            (item for item in suggestion_set.get("options", []) if item.get("id") == selection.option_id),
            None,
        )
        if option is None:
            raise AgentGatewayError("AGENT_MODEL_SELECTION_OPTION_INVALID", "模型引用的建议选项不存在。")
        if repository.suggestion_selection(proposal.id, selection.suggestion_set_id):
            raise AgentGatewayError("AGENT_MODEL_SELECTION_ALREADY_RECORDED", "该建议组已经有精确选择记录。")
        updates: list[ProposedFieldUpdate] = []
        for frozen in option.get("proposed_updates", []):
            update = ProposedFieldUpdate(
                field_key=frozen.get("field_key"),
                value=frozen.get("value"),
                source_message_ids=[current_message_id],
            )
            _validated_update_value(update.field_key, update.value)
            if update.field_key in selected_fields:
                raise AgentGatewayError("AGENT_MODEL_SELECTION_FIELD_DUPLICATE", "多个选择重复更新同一需求字段。")
            selected_fields.add(update.field_key)
            updates.append(update)
        if not updates:
            raise AgentGatewayError("AGENT_MODEL_SELECTION_OPTION_EMPTY", "模型引用的建议选项没有冻结字段更新。")
        resolved.append({
            "proposal": proposal,
            "suggestion_set_id": selection.suggestion_set_id,
            "option_id": selection.option_id,
            "updates": updates,
        })
    return resolved


def generate_candidate(
    session: Session,
    project: Project,
    payload: GenerateCandidate,
    gateway: CreativeAgentGateway,
    *,
    retry_failed_agent_run_id: str | None = None,
) -> RequirementCandidate:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, RequirementCandidate)
    if any(item.status == "running" for item in repository.agent_runs(project.id)):
        raise CreationConflictError("AGENT_RUN_IN_PROGRESS", "当前已有一轮创作智能体正在运行。")
    base = ensure_initial_requirement(session, project)
    if base.id != payload.expected_base_version_id:
        raise CreationConflictError("PROJECT_VERSION_CONFLICT", "活动需求版本已变化，请刷新后重新生成候选。")
    pending_clarifications = sync_clarifications(session, project, base)
    if pending_clarifications:
        session.commit()
        raise CreationConflictError("REQUIREMENT_INCOMPLETE", "请先解决阻断性的需求澄清。")
    conversation_session = ensure_conversation_session(session, project, started_by=payload.actor_id)
    manifest_payload = _manifest_payload(session, project, base, conversation_session)
    conversation_messages = manifest_payload["conversation"]["messages"]
    user_messages = [item for item in conversation_messages if item["role"] == "user"]
    if not user_messages:
        raise CreationConflictError("NO_NEW_REQUIREMENT_INPUT", "没有尚未处理的新需求消息。")
    if len(conversation_messages) > 100:
        raise CreationConflictError("CONVERSATION_CONTEXT_LIMIT_EXCEEDED", "当前会话超过 100 条消息，请先开启新的创作会话。")
    attempted_message_ids: set[str] = set()
    for previous_run in repository.agent_runs(project.id):
        previous_manifest = repository.agent_manifest(previous_run.input_manifest_id)
        if previous_manifest:
            attempted_message_ids.update(previous_manifest.message_ids or [])
    current_message_id = user_messages[-1]["id"]
    if current_message_id in consumed_message_ids(session, base):
        raise CreationConflictError("NO_NEW_REQUIREMENT_INPUT", "没有尚未处理的新需求消息。")
    if retry_failed_agent_run_id is not None:
        failed_run = repository.agent_run(retry_failed_agent_run_id)
        failed_manifest = (
            repository.agent_manifest(failed_run.input_manifest_id)
            if failed_run and failed_run.project_id == project.id else None
        )
        current_message_ids = [item["id"] for item in conversation_messages]
        if (
            not failed_run
            or failed_run.agent_role != "creative"
            or failed_run.status != "failed"
            or not failed_manifest
            or failed_manifest.base_requirement_version_id != base.id
            or failed_manifest.message_ids != current_message_ids
        ):
            raise CreationConflictError(
                "FAILED_AGENT_RUN_NOT_RETRYABLE",
                "只能重跑当前需求版本和当前会话中最近一次失败的创作智能体运行。",
            )
        if repository.reviewable_candidates(project.id):
            raise CreationConflictError("CANDIDATE_ALREADY_EXISTS", "当前已有待审核需求候选，不能重跑失败轮次。")
        manifest_payload["runtime_context"]["retry_of_agent_run_id"] = failed_run.id
    elif current_message_id in attempted_message_ids:
        raise CreationConflictError("AGENT_RUN_ALREADY_ATTEMPTED", "当前消息已经运行过创作智能体；失败后不会自动或重复调用模型。")
    selection = gateway.select(session)
    manifest_payload["system_config_version"] = selection.production_config_version_id
    manifest_payload["contract_versions"] = {
        "input": selection.input_contract_version,
        "output": selection.output_schema_version,
        "prompt": selection.prompt_contract_version,
    }
    manifest_payload["runtime_context"]["model_display_name"] = selection.model_name
    context_bytes = len(json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if context_bytes > 120_000:
        raise CreationConflictError("CONVERSATION_CONTEXT_LIMIT_EXCEEDED", "当前会话上下文超过 120000 字节，请先开启新的创作会话。")
    serialized = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=base.id,
        message_ids=[item["id"] for item in conversation_messages],
        decision_ids=[item["id"] for item in manifest_payload["project_context"]["confirmed_decisions"]],
        attachment_binding_ids=[item["id"] for item in manifest_payload["project_context"]["confirmed_attachment_bindings"]],
        system_config_version=selection.production_config_version_id,
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    run = AgentRun(
        project_id=project.id,
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
    _event(session, project.id, "agent.run_created.v1", "创作智能体已开始处理本轮消息", {
        "agent_run_id": run.id,
        "model_config_version_id": selection.model_config_version_id,
        "provider_config_version_id": selection.provider_config_version_id,
    })
    session.commit()

    try:
        result = gateway.invoke(selection, manifest_payload)
        valid_user_message_ids = {item["id"] for item in user_messages}
        resolved_selections = _resolve_proposal_selections(
            repository,
            project,
            base,
            result.output.proposal_selections,
            conversation_message_ids={item["id"] for item in conversation_messages},
            current_message_id=current_message_id,
            current_reply_to_message_id=user_messages[-1].get("reply_to"),
        )
        selection_updates = [
            update
            for resolved in resolved_selections
            for update in resolved["updates"]
        ]
        selected_field_keys = {item.field_key for item in selection_updates}
        explicit_field_keys = {item.field_key for item in result.output.explicit_updates}
        if selected_field_keys & explicit_field_keys:
            raise AgentGatewayError(
                "AGENT_MODEL_SELECTION_UPDATE_CONFLICT",
                "自然语言选项选择与用户明确更新重复修改同一字段。",
            )
        fields, sources, selection_changes = _apply_updates(
            base.fields,
            base.field_sources,
            selection_updates,
            valid_user_message_ids=valid_user_message_ids,
            source_type="user_selection",
            source_reference_id=current_message_id,
        )
        fields, sources, explicit_changes = _apply_updates(
            fields,
            sources,
            result.output.explicit_updates,
            valid_user_message_ids=valid_user_message_ids,
            source_type="agent_proposal",
        )
        changes = selection_changes + explicit_changes
        suggestion_sets: list[dict] = []
        for suggestion_set in result.output.suggestion_sets:
            normalized_set = {
                "id": new_id("sgset"),
                "category": suggestion_set.category,
                "title": suggestion_set.title,
                "options": [],
            }
            for index, option in enumerate(suggestion_set.options):
                option_fields: set[str] = set()
                for update in option.proposed_updates:
                    _validate_update_sources(update, valid_user_message_ids)
                    _validated_update_value(update.field_key, update.value)
                    if update.field_key in option_fields:
                        raise AgentGatewayError("AGENT_MODEL_OUTPUT_FIELD_DUPLICATE", f"建议选项重复更新字段 {update.field_key}。")
                    option_fields.add(update.field_key)
                normalized_set["options"].append({
                    "id": new_id("sgopt"),
                    "label": option.label,
                    "summary": option.summary,
                    "recommended": index == 0,
                    "proposed_updates": [item.model_dump(mode="json") for item in option.proposed_updates],
                })
            suggestion_sets.append(normalized_set)
    except AgentGatewayError as exc:
        failed_run = repository.agent_run(run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_code = exc.code
            failed_run.error_detail = (
                f"{exc} {json.dumps(exc.diagnostics, ensure_ascii=False, separators=(',', ':'))}"
                if exc.diagnostics else str(exc)
            )
            failed_run.raw_output = exc.raw_output
            failed_run.finished_at = utc_now()
            _event(session, project.id, "agent.run_failed.v1", "创作智能体本轮运行失败", {
                "agent_run_id": failed_run.id,
                "error_code": exc.code,
            })
            session.commit()
        raise
    candidate = RequirementCandidate(
        project_id=project.id,
        base_requirement_version_id=base.id,
        agent_run_id=run.id,
        fields=fields,
        field_sources=sources,
        change_summary=changes,
        status="awaiting_review" if changes else "no_change",
        decided_at=None if changes else utc_now(),
    )
    repository.add(candidate)
    repository.flush()
    for resolved in resolved_selections:
        selection_record = CreativeSuggestionSelection(
            project_id=project.id,
            proposal_id=resolved["proposal"].id,
            suggestion_set_id=resolved["suggestion_set_id"],
            option_id=resolved["option_id"],
            candidate_id=candidate.id,
            selected_by=current_message_id,
        )
        repository.add(selection_record)
        repository.flush()
        _event(session, project.id, "creative.suggestion_selected.v1", "用户通过对话选择了创作建议", {
            "proposal_id": resolved["proposal"].id,
            "suggestion_set_id": resolved["suggestion_set_id"],
            "option_id": resolved["option_id"],
            "candidate_id": candidate.id,
            "source_message_id": current_message_id,
        })
    latest_message_id = current_message_id
    assistant_message = Message(
        project_id=project.id,
        conversation_session_id=conversation_session.id,
        role="assistant",
        content=result.output.assistant_reply,
        reply_to_message_id=latest_message_id,
        agent_run_id=run.id,
    )
    repository.add(assistant_message)
    repository.flush()
    proposal = CreativeTurnProposal(
        project_id=project.id,
        base_requirement_version_id=base.id,
        agent_run_id=run.id,
        assistant_message_id=assistant_message.id,
        suggestion_sets=suggestion_sets,
        explicit_updates=[item.model_dump(mode="json") for item in result.output.explicit_updates],
        clarifying_question=(
            {"prompt": result.output.clarifying_question}
            if result.output.clarifying_question else None
        ),
        prompt_contract_version=selection.prompt_contract_version,
        output_schema_version=selection.output_schema_version,
    )
    repository.add(proposal)
    repository.flush()
    run.status = "succeeded"
    run.raw_output = result.raw_output
    run.parsed_candidate_id = candidate.id
    run.parsed_proposal_id = proposal.id
    run.provider_request_id = result.provider_request_id
    run.token_usage = result.token_usage
    run.finished_at = utc_now()
    _save_receipt(session, project.id, payload.command_id, "candidate.generate", "requirement_candidate", candidate.id)
    _event(session, project.id, "conversation.assistant_replied.v1", "创作智能体已回复本轮消息", {
        "agent_run_id": run.id,
        "message_id": assistant_message.id,
        "creative_proposal_id": proposal.id,
    })
    _event(session, project.id, "agent.run_succeeded.v1", "创作智能体已返回严格候选", {"agent_run_id": run.id})
    if changes:
        _event(session, project.id, "candidate.generated.v1", "需求候选等待用户确认", {"candidate_id": candidate.id})
    else:
        _event(session, project.id, "candidate.no_change.v1", "本轮回复没有提出结构化字段变更", {"candidate_id": candidate.id})
    session.commit()
    return candidate


def retry_creative_turn(
    session: Session,
    project: Project,
    payload: RetryCreativeTurn,
    gateway: CreativeAgentGateway,
) -> RequirementCandidate:
    if not payload.confirm_model_cost:
        raise CreationConflictError("MODEL_COST_CONFIRMATION_REQUIRED", "请明确确认本次重跑会再次调用当前创作模型。")
    return generate_candidate(
        session,
        project,
        GenerateCandidate(
            command_id=payload.command_id,
            actor_id=payload.actor_id,
            expected_base_version_id=payload.expected_base_version_id,
        ),
        gateway,
        retry_failed_agent_run_id=payload.failed_agent_run_id,
    )


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
    for proposal in repository.creative_proposals(project.id):
        if proposal.status == "active" and proposal.base_requirement_version_id == active.id:
            proposal.status = "stale"
    _save_receipt(session, project.id, payload.command_id, "candidate.accept", "requirement_version", version.id)
    _event(session, project.id, "requirement.confirmed.v1", "需求候选已提升为正式版本", {
        "candidate_id": candidate.id, "requirement_version_id": version.id,
    })
    if not any(item.status == "pending" for item in project.decisions):
        transition_project(
            session,
            project,
            ProjectStateTrigger.REQUIREMENT_CONFIRMED,
            actor_type="user",
            actor_id=payload.actor_id,
            event_data={"requirement_version_id": version.id},
        )
    session.commit()
    return version


def select_creative_suggestion(
    session: Session,
    project: Project,
    proposal_id: str,
    payload: SelectCreativeSuggestion,
) -> RequirementCandidate:
    repository = _creation(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, RequirementCandidate)
    proposal = repository.creative_proposal(proposal_id)
    if not proposal or proposal.project_id != project.id:
        raise CreationNotFoundError("Creative proposal not found")
    active = active_requirement(session, project.id)
    if (
        not active
        or active.id != payload.expected_base_version_id
        or proposal.base_requirement_version_id != active.id
    ):
        raise CreationConflictError("CREATIVE_PROPOSAL_BASE_VERSION_STALE", "这组选项基于旧需求版本，不能选择。")
    if proposal.status != "active":
        raise CreationConflictError("CREATIVE_PROPOSAL_NOT_ACTIVE", "这组选项已经过期。")
    if repository.suggestion_selection(proposal.id, payload.suggestion_set_id):
        raise CreationConflictError("CREATIVE_SUGGESTION_ALREADY_SELECTED", "这组建议已经选择过。")
    suggestion_set = next(
        (item for item in proposal.suggestion_sets if item.get("id") == payload.suggestion_set_id),
        None,
    )
    if suggestion_set is None:
        raise CreationConflictError("CREATIVE_SUGGESTION_SET_NOT_FOUND", "建议组不存在。")
    option = next(
        (item for item in suggestion_set.get("options", []) if item.get("id") == payload.option_id),
        None,
    )
    if option is None:
        raise CreationConflictError("CREATIVE_SUGGESTION_OPTION_NOT_FOUND", "建议选项不存在。")
    run = repository.agent_run(proposal.agent_run_id)
    manifest = repository.agent_manifest(run.input_manifest_id) if run else None
    if not manifest:
        raise CreationConflictError("CREATIVE_PROPOSAL_MANIFEST_MISSING", "建议的输入清单不存在。")
    conversation = manifest.payload.get("conversation", {}).get("messages", [])
    valid_user_message_ids = {item["id"] for item in conversation if item.get("role") == "user"}
    updates = [
        ProposedFieldUpdate.model_validate(item)
        for item in [*proposal.explicit_updates, *option.get("proposed_updates", [])]
    ]
    selection = CreativeSuggestionSelection(
        project_id=project.id,
        proposal_id=proposal.id,
        suggestion_set_id=payload.suggestion_set_id,
        option_id=payload.option_id,
        selected_by=payload.actor_id,
    )
    repository.add(selection)
    repository.flush()
    fields, sources, changes = _apply_updates(
        active.fields,
        active.field_sources,
        updates,
        valid_user_message_ids=valid_user_message_ids,
        source_type="user_selection",
        source_reference_id=selection.id,
    )
    for candidate in repository.reviewable_candidates(project.id):
        candidate.status = "stale"
        candidate.decided_at = utc_now()
    candidate = RequirementCandidate(
        project_id=project.id,
        base_requirement_version_id=active.id,
        agent_run_id=proposal.agent_run_id,
        fields=fields,
        field_sources=sources,
        change_summary=changes,
        status="awaiting_review",
    )
    repository.add(candidate)
    repository.flush()
    selection.candidate_id = candidate.id
    _save_receipt(
        session,
        project.id,
        payload.command_id,
        "creative_suggestion.select",
        "requirement_candidate",
        candidate.id,
    )
    _event(session, project.id, "creative.suggestion_selected.v1", "用户已选择创作建议，需求候选等待确认", {
        "creative_proposal_id": proposal.id,
        "suggestion_set_id": payload.suggestion_set_id,
        "option_id": payload.option_id,
        "candidate_id": candidate.id,
    })
    session.commit()
    return candidate


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
    for proposal in repository.creative_proposals(project.id):
        if proposal.status == "active" and proposal.base_requirement_version_id == active.id:
            proposal.status = "stale"
    _save_receipt(session, project.id, payload.command_id, "clarification.resolve", "requirement_version", version.id)
    _event(session, project.id, "clarification.resolved.v1", "用户已解决需求澄清", {
        "clarification_id": clarification.id,
        "requirement_version_id": version.id,
        "field_key": clarification.field_key,
    })
    _event(session, project.id, "requirement.confirmed.v1", "澄清结果已创建新的需求版本", {
        "requirement_version_id": version.id,
    })
    if not evaluate_requirement(fields, sources) and not any(
        item.status == "pending" for item in project.decisions
    ):
        transition_project(
            session,
            project,
            ProjectStateTrigger.REQUIREMENT_CONFIRMED,
            actor_type="user",
            actor_id=payload.actor_id,
            event_data={"requirement_version_id": version.id},
        )
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
    conversation = ensure_conversation_session(session, project)
    sync_clarifications(session, project, active)
    session.commit()
    messages = repository.view_messages(project.id, conversation.id)
    candidates = repository.candidate_history(project.id)
    runs = repository.agent_runs(project.id)
    run_views = []
    for run in runs:
        manifest = repository.agent_manifest(run.input_manifest_id)
        run_views.append({
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
            "parsed_candidate_id": run.parsed_candidate_id,
            "parsed_proposal_id": run.parsed_proposal_id,
            "error_code": run.error_code,
            "error_detail": run.error_detail,
            "provider_request_id": run.provider_request_id,
            "token_usage": run.token_usage or {},
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "input_manifest": manifest,
        })
    attachments = repository.attachments(project.id)
    binding_rows = repository.bindings(project.id)
    bindings_by_attachment: dict[str, list[AttachmentBinding]] = {}
    for binding in binding_rows:
        bindings_by_attachment.setdefault(binding.attachment_id, []).append(binding)
    clarifications = repository.active_pending_clarifications(project.id, active.id)
    active_proposal = repository.active_creative_proposal(project.id)
    proposal_selections = repository.suggestion_selections(project.id)
    current = next((item for item in candidates if item.status == "awaiting_review"), None)
    confirmed_attachment_ids = {
        binding.attachment_id for binding in binding_rows if binding.status == "confirmed"
    }
    unbound = [item for item in attachments if item.id not in confirmed_attachment_ids]
    consumed = consumed_message_ids(session, active)
    unconsumed_messages = [item for item in messages if item.role == "user" and item.id not in consumed]
    attempted_message_ids: set[str] = set()
    for run in runs:
        manifest = repository.agent_manifest(run.input_manifest_id)
        if manifest:
            attempted_message_ids.update(manifest.message_ids or [])
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
    elif unconsumed_messages and not {item.id for item in unconsumed_messages}.issubset(attempted_message_ids):
        next_action = NextAction(code="GENERATE_REQUIREMENT_CANDIDATE", label="生成需求候选", incurs_model_cost=True)
    elif unconsumed_messages:
        latest_attempt = next((item for item in runs if item.status in {"succeeded", "failed"}), None)
        if latest_attempt and latest_attempt.status == "succeeded":
            next_action = NextAction(code="CONTINUE_REQUIREMENT_CONVERSATION", label="继续补充创作需求", incurs_model_cost=True)
        else:
            next_action = NextAction(
                code="RETRY_FAILED_CREATIVE_TURN",
                target_ids=[latest_attempt.id] if latest_attempt else [],
                label="确认后重跑失败轮次",
                incurs_model_cost=True,
            )
    elif messages:
        next_action = NextAction(code="REQUIREMENT_READY_FOR_PLANNING", label="进入创意方案规划")
    else:
        next_action = NextAction(code="ADD_REQUIREMENT_MESSAGE", label="补充创作需求", incurs_model_cost=True)
    return {
        "project_id": project.id,
        "conversation_session_id": conversation.id,
        "active_requirement": active,
        "messages": messages,
        "current_candidate": current,
        "candidate_history": candidates,
        "pending_clarifications": clarifications,
        "active_creative_proposal": (
            {
                "id": active_proposal.id,
                "base_requirement_version_id": active_proposal.base_requirement_version_id,
                "agent_run_id": active_proposal.agent_run_id,
                "assistant_message_id": active_proposal.assistant_message_id,
                "status": active_proposal.status,
                "suggestion_sets": active_proposal.suggestion_sets,
                "explicit_updates": active_proposal.explicit_updates,
                "clarifying_question": active_proposal.clarifying_question,
                "prompt_contract_version": active_proposal.prompt_contract_version,
                "output_schema_version": active_proposal.output_schema_version,
                "created_at": active_proposal.created_at,
                "selections": [
                    item for item in proposal_selections if item.proposal_id == active_proposal.id
                ],
            }
            if active_proposal else None
        ),
        "latest_agent_run": run_views[0] if run_views else None,
        "agent_runs": run_views,
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
