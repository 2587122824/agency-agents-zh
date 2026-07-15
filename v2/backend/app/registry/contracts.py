from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RegistryProject(BaseModel):
    id: str
    title: str
    status: str


class RegistryAttachment(BaseModel):
    id: str
    original_filename: str
    mime_type: str
    byte_size: int
    content_hash: str
    verification_status: str
    created_at: datetime


class RegistryBinding(BaseModel):
    id: str
    binding_type: str
    status: str
    confirmed_by: str
    confirmed_at: datetime


class RegistrySnapshotReference(BaseModel):
    snapshot_id: str
    snapshot_number: int
    snapshot_status: str
    role: str


class RegistryShotReference(BaseModel):
    plan_version_id: str
    plan_version_number: int
    shot_id: str
    shot_code: str
    role: str


class RegistryEntityVersion(BaseModel):
    id: str
    version_number: int
    attributes: dict
    status: str
    is_active: bool
    created_by: str
    created_at: datetime
    source_attachment: RegistryAttachment | None
    bindings: list[RegistryBinding]
    snapshot_references: list[RegistrySnapshotReference]
    shot_references: list[RegistryShotReference]


class RegistryEntity(BaseModel):
    id: str
    project_id: str
    project_title: str
    entity_type: str
    display_name: str
    status: str
    created_at: datetime
    active_version_id: str | None
    versions: list[RegistryEntityVersion]


class EntityRegistryView(BaseModel):
    projects: list[RegistryProject]
    counts: dict[str, int]
    entities: list[RegistryEntity]
