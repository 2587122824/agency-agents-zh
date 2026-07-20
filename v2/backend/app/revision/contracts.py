from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..creation.contracts import CommandContext


class CreateAssetRevisionRequest(CommandContext):
    model_config = ConfigDict(extra="forbid")
    expected_asset_row_version: int = Field(ge=1)
    issue_scope: Literal["storyboard", "production", "editing"]
    rationale: str = Field(min_length=1, max_length=2000)


class CancelAssetRevisionRequest(CommandContext):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)


class AssetRevisionRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    asset_id: str
    snapshot_id: str
    plan_version_id: str
    shot_id: str | None
    shot_code: str | None
    issue_scope: str
    rationale: str
    status: str
    source_asset_state: str
    source_asset_row_version: int
    affected_downstream_node_keys: list[str]
    draft_candidate_id: str | None
    resulting_candidate_id: str | None
    resulting_plan_version_id: str | None
    created_by: str
    created_at: datetime
    resolved_at: datetime | None


class RevisionNextAction(BaseModel):
    path: str
    label: str
    draft_candidate_id: str | None = None


class RevisionRequestResult(BaseModel):
    request: AssetRevisionRequestRead
    next_action: RevisionNextAction
