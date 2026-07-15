from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from ..quality.contracts import AssetRead


class ContactSheetSnapshot(BaseModel):
    id: str
    snapshot_number: int
    status: str
    contract_hash: str
    plan_version_id: str


class ContactSheetShot(BaseModel):
    id: str
    shot_code: str
    sequence_number: int
    duration_ms: int
    shot_type: str
    face_visibility: str
    text_policy: str
    motion_requirement: str
    composition: str
    action: str


class ContactSheetRoute(BaseModel):
    work_item_id: str
    work_item_status: str
    attempt_id: str
    attempt_number: int
    attempt_state: str
    provider: str
    adapter_kind: str | None
    provider_workflow_id: str | None
    provider_task_id: str | None
    request_fingerprint: str


class ContactSheetDependencyAsset(BaseModel):
    id: str
    asset_type: str
    role: str
    state: str
    content_hash: str | None


class ContactSheetDependency(BaseModel):
    edge_id: str
    dependency_type: str
    input_slot: str | None
    parent_node_id: str
    parent_node_key: str
    registered_assets: list[ContactSheetDependencyAsset]


class ContactSheetEntityReference(BaseModel):
    role: str
    entity_id: str
    entity_name: str
    entity_type: str
    entity_version_id: str
    version_number: int
    source_attachment_id: str | None
    source_filename: str | None
    source_mime_type: str | None


class ContactSheetEntry(BaseModel):
    number: int
    node_id: str | None
    node_key: str | None
    node_kind: str | None
    asset: AssetRead
    shot: ContactSheetShot | None
    route: ContactSheetRoute | None
    dependencies: list[ContactSheetDependency]
    entity_references: list[ContactSheetEntityReference]


class MaterialContactSheetView(BaseModel):
    project_id: str
    project_title: str
    project_status: str
    generated_at: datetime
    snapshot: ContactSheetSnapshot | None
    entries: list[ContactSheetEntry]
    output_gaps: list[dict]
    counts: dict[str, int]
    boundary: str
