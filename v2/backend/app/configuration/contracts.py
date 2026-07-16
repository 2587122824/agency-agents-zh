from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


Key = str


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigurationCommand(StrictContract):
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=48)


class VersionedConfigurationCommand(ConfigurationCommand):
    expected_row_version: int = Field(ge=1)


class NodeBinding(StrictContract):
    node_id: str = Field(min_length=1, max_length=120)
    field_path: str = Field(min_length=1, max_length=240)
    value_source: str = Field(min_length=1, max_length=160)
    value_type: Literal["string", "integer", "number", "boolean", "image", "audio", "json"]
    required: bool = True


class ProviderDraft(StrictContract):
    provider_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    adapter_kind: str = Field(min_length=1, max_length=80)
    region: str | None = Field(default=None, max_length=120)
    base_url: HttpUrl
    credential_ref: str | None = Field(default=None, max_length=160)
    capabilities: list[str] = Field(min_length=1)
    request_timeout_seconds: int = Field(ge=1, le=3600)
    poll_interval_seconds: int = Field(ge=1, le=300)
    max_concurrency: int = Field(ge=1, le=128)

    @model_validator(mode="after")
    def base_url_does_not_embed_credentials(self):
        if self.base_url.username or self.base_url.password or self.base_url.query or self.base_url.fragment:
            raise ValueError("base_url must not contain credentials, query parameters, or fragments")
        return self


class ModelDraft(StrictContract):
    config_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    agent_role: Literal["creative", "director", "qc", "editor"]
    provider_key: Key
    provider_model_id: str = Field(min_length=1, max_length=200)
    input_contract_version: str = Field(min_length=1, max_length=80)
    output_schema_version: str = Field(min_length=1, max_length=80)
    prompt_contract_version: str = Field(min_length=1, max_length=80)
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    sampling: dict = Field(default_factory=dict)
    capability_tags: list[str] = Field(default_factory=list)


class VideoSpecDraft(StrictContract):
    spec_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    fps: int = Field(ge=1, le=120)
    duration_min_seconds: int = Field(ge=1, le=3600)
    duration_max_seconds: int = Field(ge=1, le=3600)
    frame_count_rule: dict
    container: str = Field(min_length=1, max_length=24)
    video_codec: str = Field(min_length=1, max_length=40)
    pixel_format: str = Field(min_length=1, max_length=40)
    bitrate_policy: dict = Field(default_factory=dict)
    safe_crop: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def duration_range_is_valid(self):
        if self.duration_max_seconds < self.duration_min_seconds:
            raise ValueError("duration_max_seconds must be greater than or equal to duration_min_seconds")
        return self


class WorkflowSlotDraft(StrictContract):
    slot_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    operation_kind: str = Field(min_length=1, max_length=80)
    provider_key: Key
    provider_workflow_id: str = Field(min_length=1, max_length=200)
    provider_workflow_version: str | None = Field(default=None, max_length=120)
    model_config_key: Key | None = None
    input_schema_version: str = Field(min_length=1, max_length=80)
    output_schema_version: str = Field(min_length=1, max_length=80)
    node_info_list: list[NodeBinding] = Field(min_length=1)
    supported_video_spec_keys: list[Key] = Field(default_factory=list)
    capability_tags: list[str] = Field(default_factory=list)


class AudioConfigDraft(StrictContract):
    config_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    supported_modes: list[Literal["off", "voiceover"]] = Field(min_length=1)
    tts_workflow_slot_key: Key | None = None
    default_voice_entity_version_id: str | None = None
    sample_rate: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2]
    format: str = Field(min_length=1, max_length=24)
    speaking_rate_min: float = Field(gt=0, le=4)
    speaking_rate_max: float = Field(gt=0, le=4)
    loudness_target: float | None = Field(default=None, ge=-70, le=0)
    temporary_upload_policy_version_id: str | None = None

    @model_validator(mode="after")
    def speaking_rate_range_is_valid(self):
        if self.speaking_rate_max < self.speaking_rate_min:
            raise ValueError("speaking_rate_max must be greater than or equal to speaking_rate_min")
        return self


class StoragePolicyDraft(StrictContract):
    policy_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    backend_kind: Literal["local", "oss"]
    region_ref: str | None = Field(default=None, max_length=160)
    bucket_ref: str | None = Field(default=None, max_length=160)
    credential_ref: str | None = Field(default=None, max_length=160)
    allowed_mime_types: list[str] = Field(min_length=1)
    max_file_size_bytes: int = Field(gt=0, le=10_737_418_240)
    public_url_policy: Literal["none", "signed", "public", "temporary_public"]
    lifecycle_days: int | None = Field(default=None, ge=1, le=3650)
    local_root_ref: str | None = Field(default=None, max_length=160)


class PricingRuleDraft(StrictContract):
    workflow_slot_key: Key
    unit: Literal["call", "output_second", "runtime_second"]
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    minimum_charge: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    estimated_runtime_seconds: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)

    @model_validator(mode="after")
    def runtime_estimate_matches_unit(self):
        if self.unit == "runtime_second" and self.estimated_runtime_seconds is None:
            raise ValueError("estimated_runtime_seconds is required for runtime_second pricing")
        if self.unit != "runtime_second" and self.estimated_runtime_seconds is not None:
            raise ValueError("estimated_runtime_seconds is only valid for runtime_second pricing")
        return self


class PricingCatalogDraft(StrictContract):
    catalog_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    currency: str = Field(pattern=r"^[A-Z]{3,12}$")
    confirmation_threshold: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    rules: list[PricingRuleDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def effective_range_is_valid(self):
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be later than effective_from")
        return self


class ConfigurationDraftBody(StrictContract):
    config_key: Key = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$")
    display_name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    providers: list[ProviderDraft] = Field(min_length=1)
    models: list[ModelDraft] = Field(default_factory=list)
    workflow_slots: list[WorkflowSlotDraft] = Field(min_length=1)
    video_specs: list[VideoSpecDraft] = Field(min_length=1)
    audio: AudioConfigDraft
    storage: StoragePolicyDraft
    pricing: PricingCatalogDraft | None = None


class CreateConfiguration(ConfigurationCommand):
    configuration: ConfigurationDraftBody


class ReviseConfiguration(VersionedConfigurationCommand):
    configuration: ConfigurationDraftBody


class ValidateConfiguration(VersionedConfigurationCommand):
    pass


class PublishConfiguration(VersionedConfigurationCommand):
    confirm_high_risk_changes: bool


class RetireConfiguration(VersionedConfigurationCommand):
    confirm_reference_impact: bool


class CloneConfiguration(ConfigurationCommand):
    display_name: str | None = Field(default=None, max_length=160)


class ComponentSummary(BaseModel):
    id: str
    component_type: str
    key: str
    version_number: int
    display_name: str
    status: str
    details: dict


class ConfigurationVersionRead(BaseModel):
    id: str
    config_key: str
    version_number: int
    display_name: str
    description: str | None
    status: str
    supersedes_version_id: str | None
    row_version: int
    config_hash: str | None
    validation_report: list[dict]
    created_by: str
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    components: list[ComponentSummary]
    references: list[dict]


class ConfigurationVersionSummary(BaseModel):
    id: str
    config_key: str
    version_number: int
    display_name: str
    description: str | None
    status: str
    row_version: int
    config_hash: str | None
    component_count: int
    validation_error_count: int
    published_at: datetime | None
    updated_at: datetime


class ConfigurationDiffRead(BaseModel):
    version_id: str
    base_version_id: str
    changed_components: list[dict]
    high_risk_changes: list[str]
    incurs_production_cost: bool = False
