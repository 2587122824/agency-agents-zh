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


class GenerateCandidate(CommandContext):
    expected_base_version_id: str


class AcceptCandidate(CommandContext):
    expected_base_version_id: str


class RejectCandidate(CommandContext):
    reason: str = Field(min_length=1, max_length=500)


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


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    reply_to_message_id: str | None
    created_at: datetime


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
    base_requirement_version_id: str
    agent_run_id: str
    status: str
    fields: dict[str, Any]
    field_sources: dict[str, Any]
    change_summary: list[dict[str, Any]]
    validation_errors: list[dict[str, Any]]
    created_at: datetime
    decided_at: datetime | None


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_role: str
    status: str
    input_manifest_id: str
    model_provider: str
    model_name: str
    prompt_contract_version: str
    output_schema_version: str
    parsed_candidate_id: str | None
    error_code: str | None
    error_detail: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ClarificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    field_key: str
    question: str
    risk_level: str
    status: str
    resolution: Any | None


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
    target_ids: list[str] = []
    label: str
    incurs_model_cost: bool = False
    incurs_production_cost: bool = False


class CreationCenterView(BaseModel):
    project_id: str
    active_requirement: RequirementVersionRead
    messages: list[MessageRead]
    current_candidate: CandidateRead | None
    candidate_history: list[CandidateRead]
    pending_clarifications: list[ClarificationRead]
    latest_agent_run: AgentRunRead | None
    agent_runs: list[AgentRunRead]
    attachments: list[AttachmentView]
    next_action: NextAction
