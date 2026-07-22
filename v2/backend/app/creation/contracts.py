from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CommandContext(BaseModel):
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=48)


class MessageCreate(CommandContext):
    content: str = Field(min_length=1, max_length=10000)
    reply_to_message_id: str | None = None


class StartConversationSession(CommandContext):
    pass


class GenerateCandidate(CommandContext):
    expected_base_version_id: str


class InitializeCreativeConversation(CommandContext):
    expected_base_version_id: str


class RetryCreativeTurn(CommandContext):
    expected_base_version_id: str
    failed_agent_run_id: str = Field(min_length=1, max_length=48)
    confirm_model_cost: bool


class SelectCreativeSuggestion(CommandContext):
    expected_base_version_id: str
    suggestion_set_id: str = Field(min_length=1, max_length=48)
    option_id: str = Field(min_length=1, max_length=48)
    confirm_model_cost: bool


class AcceptCandidate(CommandContext):
    expected_base_version_id: str


class RejectCandidate(CommandContext):
    reason: str = Field(min_length=1, max_length=500)


class ResolveClarification(CommandContext):
    expected_base_version_id: str
    value: Any


class AttachmentCreate(CommandContext):
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(pattern=r"^(image|audio|video)/[a-zA-Z0-9.+-]+$")
    byte_size: int = Field(gt=0, le=524_288_000)
    content_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    message_id: str | None = None


class BindingCreate(CommandContext):
    binding_type: Literal[
        "identity_reference", "outfit_reference", "scene_reference",
        "product_reference", "voice_sample", "inspiration_only",
    ]
    entity_id: str | None = Field(default=None, max_length=80)
    entity_version_id: str | None = Field(default=None, max_length=80)
    create_new_entity: bool = False
    entity_display_name: str | None = Field(default=None, min_length=1, max_length=120)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_session_id: str
    role: str
    content: str
    reply_to_message_id: str | None
    agent_run_id: str | None
    created_at: datetime


class ConversationSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    status: str
    started_by: str
    started_at: datetime
    ended_at: datetime | None


class RequirementVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    version_number: int
    fields: dict[str, Any]
    field_sources: dict[str, Any]
    is_active: bool
    created_by: str
    created_at: datetime


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_session_id: str
    base_requirement_version_id: str
    supersedes_candidate_id: str | None
    agent_run_id: str
    status: str
    fields: dict[str, Any]
    field_sources: dict[str, Any]
    change_summary: list[dict[str, Any]]
    validation_errors: list[dict[str, Any]]
    created_at: datetime
    decided_at: datetime | None


class AgentInputManifestAuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    base_requirement_version_id: str
    message_ids: list[str]
    decision_ids: list[str]
    attachment_binding_ids: list[str]
    system_config_version: str
    input_hash: str
    created_at: datetime


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_role: str
    status: str
    input_manifest_id: str
    model_provider: str
    model_name: str
    production_config_version_id: str | None
    model_config_version_id: str | None
    provider_config_version_id: str | None
    prompt_contract_version: str
    output_schema_version: str
    parsed_candidate_id: str | None
    parsed_proposal_id: str | None
    error_code: str | None
    error_detail: str | None
    provider_request_id: str | None
    token_usage: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    input_manifest: AgentInputManifestAuditRead | None


class ClarificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    base_requirement_version_id: str
    field_key: str
    reason_code: str
    question: str
    options: list[dict[str, Any]]
    risk_level: str
    status: str
    resolution: Any | None


class CreativeSuggestionSelectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    proposal_id: str
    suggestion_set_id: str
    option_id: str
    candidate_id: str | None
    selected_by: str
    selected_at: datetime


class CreativeTurnProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    base_requirement_version_id: str
    agent_run_id: str
    assistant_message_id: str
    status: str
    suggestion_sets: list[dict[str, Any]]
    explicit_updates: list[dict[str, Any]]
    creative_diagnosis: dict[str, Any] | None
    clarifying_question: dict[str, Any] | None
    prompt_contract_version: str
    output_schema_version: str
    created_at: datetime
    selections: list[CreativeSuggestionSelectionRead] = Field(default_factory=list)


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    original_filename: str
    mime_type: str
    byte_size: int
    content_hash: str
    verification_status: str
    created_at: datetime


class BindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    attachment_id: str
    binding_type: str
    entity_id: str | None
    entity_version_id: str | None
    status: str
    confirmed_by: str
    confirmed_at: datetime


class AttachmentView(AttachmentRead):
    bindings: list[BindingRead]


class NextAction(BaseModel):
    code: str
    target_ids: list[str] = Field(default_factory=list)
    label: str
    incurs_model_cost: bool = False
    incurs_production_cost: bool = False


class CreationCenterView(BaseModel):
    project_id: str
    conversation_session_id: str
    initialization_status: Literal["not_started", "running", "succeeded", "failed"]
    active_requirement: RequirementVersionRead
    messages: list[MessageRead]
    current_candidate: CandidateRead | None
    candidate_history: list[CandidateRead]
    pending_clarifications: list[ClarificationRead]
    active_creative_proposal: CreativeTurnProposalRead | None
    latest_agent_run: AgentRunRead | None
    agent_runs: list[AgentRunRead]
    attachments: list[AttachmentView]
    next_action: NextAction
