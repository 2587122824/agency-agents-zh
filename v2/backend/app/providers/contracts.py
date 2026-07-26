from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

class ProviderReadinessItem(BaseModel):
    configuration_version_id: str
    configuration_display_name: str
    configuration_version_number: int
    provider_version_id: str
    provider_display_name: str
    adapter_kind: str
    capabilities: list[str]
    adapter_registered: bool
    external: bool | None
    execution_enabled: bool | None
    credential_required: bool | None
    api_key_state: Literal["not_required", "missing", "configured"]
    configuration_ready: bool
    configuration_issue_count: int
    configuration_issue_codes: list[str]
    status: Literal[
        "connected",
        "adapter_not_connected",
        "configuration_not_ready",
        "execution_disabled",
        "credential_not_ready",
    ]
    next_action: Literal[
        "connect_adapter",
        "revise_configuration",
        "configure_credential",
        "enable_execution",
        "ready",
    ]


class ProviderReadinessView(BaseModel):
    network_probe_performed: bool
    external_execution_enabled: bool
    providers: list[ProviderReadinessItem]


class CosyVoicePaidValidationCommand(BaseModel):
    command_id: str = Field(min_length=1, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=48)
    configuration_id: str
    expected_config_hash: str = Field(min_length=64, max_length=64)
    validation_text: str = Field(min_length=1, max_length=200)
    expected_validation_text_sha256: str = Field(min_length=64, max_length=64)
    confirm_paid_call: Literal[True]


class CosyVoiceValidationRunView(BaseModel):
    id: str
    production_config_version_id: str
    provider_config_version_id: str
    workflow_slot_version_id: str
    status: Literal["passed", "blocked"]
    network_probe_performed: bool
    validation_text_sha256: str
    validation_text_character_count: int
    request_id: str | None
    usage: dict
    output: dict
    error_code: str | None
    error_detail: str | None
    created_by: str
    created_at: str


class CosyVoiceValidationWorkspaceView(BaseModel):
    preflight: dict
    validation_runs: list[CosyVoiceValidationRunView]
