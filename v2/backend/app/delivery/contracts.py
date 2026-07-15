from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..quality.contracts import AssetRead


class DeliveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=48)


class AuthorizeDelivery(DeliveryCommand):
    timeline_id: str
    expected_timeline_contract_hash: str = Field(min_length=64, max_length=64)
    execution_kind: Literal["external_upload"]
    confirm_delivery_authorization: bool


class RegisterDeliveryOutput(DeliveryCommand):
    expected_request_fingerprint: str = Field(min_length=64, max_length=64)
    expected_row_version: int = Field(ge=1)
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=120)
    content_hash: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(gt=0)


class VerifyDelivery(DeliveryCommand):
    expected_row_version: int = Field(ge=1)
    expected_asset_row_version: int = Field(ge=1)


class DeliveryAttemptRead(BaseModel):
    id: str
    project_id: str
    snapshot_id: str
    timeline_id: str
    attempt_number: int
    status: str
    execution_kind: str
    request_manifest: dict
    request_fingerprint: str
    final_asset_id: str | None
    final_asset: AssetRead | None
    error_code: str | None
    error_detail: dict | None
    row_version: int
    created_by: str
    created_at: datetime
    output_registered_at: datetime | None
    verified_at: datetime | None


class DeliveryWorkspaceView(BaseModel):
    project_id: str
    project_title: str
    project_status: str
    active_snapshot_id: str | None
    delivery_asset_id: str | None
    confirmed_timeline: dict | None
    attempts: list[DeliveryAttemptRead]
    next_action: dict
