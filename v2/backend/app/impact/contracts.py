from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..creation.contracts import CommandContext


class ImpactGraphNode(BaseModel):
    node_id: str
    record_type: str
    record_id: str
    label: str
    status: str
    authority: str
    details: dict


class ImpactGraphEdge(BaseModel):
    source_node_id: str
    target_node_id: str
    relation: str


class DecisionImpactSummary(BaseModel):
    decision_id: str
    key: str
    label: str
    current_value: Any
    status: str
    observation_status: str
    direct_manifest_ids: list[str]
    downstream_node_ids: list[str]
    downstream_counts: dict[str, int]
    active_downstream_count: int


class DecisionImpactGraphView(BaseModel):
    project_id: str
    project_title: str
    generated_at: datetime
    scope: str
    decisions: list[DecisionImpactSummary]
    nodes: list[ImpactGraphNode]
    edges: list[ImpactGraphEdge]
    boundary: str


class AnalyzeDecisionChangeImpact(CommandContext):
    model_config = ConfigDict(extra="forbid")
    proposed_value: Any


class DecisionChangeImpactTargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    record_type: str
    record_id: str
    label: str
    record_status: str
    authority: str
    impact_kind: str
    reason_code: str
    included_in_estimate: bool
    estimated_work_units: int
    estimated_cost: float | None
    currency: str | None
    evidence: dict


class DecisionChangeImpactAnalysisRead(BaseModel):
    id: str
    project_id: str
    decision_id: str
    status: str
    scope: str
    current_value: Any
    proposed_value: Any
    observed_manifest_ids: list[str]
    target_counts: dict[str, int]
    estimated_work_count: int
    cost_status: str
    estimated_cost: float | None
    currency: str | None
    analysis_hash: str
    active_snapshot_id: str | None
    created_by: str
    created_at: datetime
    targets: list[DecisionChangeImpactTargetRead]


class DecisionChangeImpactWorkspace(BaseModel):
    project_id: str
    analyses: list[DecisionChangeImpactAnalysisRead] = Field(default_factory=list)
    boundary: str
