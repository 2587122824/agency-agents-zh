from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..creation.contracts import CommandContext


IssueScope = Literal["storyboard", "production", "editing"]
IssueCode = Literal[
    "content_mismatch",
    "action_mismatch",
    "composition_mismatch",
    "character_setup_mismatch",
    "identity_inconsistent",
    "visual_artifact",
    "composition_deviation",
    "text_error",
    "low_clarity",
    "style_mismatch",
    "exclude_asset",
    "shorten_clip",
    "reorder_clip",
    "replace_clip",
    "other",
]

ISSUE_CODES_BY_SCOPE: dict[str, set[str]] = {
    "storyboard": {
        "content_mismatch",
        "action_mismatch",
        "composition_mismatch",
        "character_setup_mismatch",
        "other",
    },
    "production": {
        "identity_inconsistent",
        "visual_artifact",
        "composition_deviation",
        "text_error",
        "low_clarity",
        "style_mismatch",
        "other",
    },
    "editing": {
        "exclude_asset",
        "shorten_clip",
        "reorder_clip",
        "replace_clip",
        "other",
    },
}


class CreateAssetRevisionRequest(CommandContext):
    model_config = ConfigDict(extra="forbid")
    expected_asset_row_version: int = Field(ge=1)
    issue_scope: IssueScope
    issue_code: IssueCode
    rationale: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_issue(self) -> "CreateAssetRevisionRequest":
        if self.issue_code not in ISSUE_CODES_BY_SCOPE[self.issue_scope]:
            raise ValueError(f"issue_code '{self.issue_code}' is not valid for issue_scope '{self.issue_scope}'")
        if self.issue_code == "other" and not self.rationale.strip():
            raise ValueError("rationale is required when issue_code is 'other'")
        return self


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
    issue_code: str
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
