from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
