from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy.orm import Session

from ..core.config import RUNTIME_ROOT
from ..db.models import (
    Attachment,
    AttachmentBinding,
    Entity,
    EntityVersion,
    PlanVersion,
    ProductionSnapshot,
    Project,
    Shot,
    SnapshotEntityVersion,
)
from ..repositories import SqlAlchemyRegistryRepository


class RegistryNotFoundError(LookupError):
    pass


class RegistryConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def entity_registry_view(session: Session) -> dict:
    repository = SqlAlchemyRegistryRepository(session)
    projects = repository.projects()
    project_map = {item.id: item for item in projects}
    entities = repository.entities()
    versions = repository.entity_versions()
    version_ids = {item.id for item in versions}

    attachment_ids = {item.source_attachment_id for item in versions if item.source_attachment_id}
    attachments = {
        item.id: item for item in repository.attachments_by_ids(attachment_ids)
    } if attachment_ids else {}
    bindings_by_version: dict[str, list[AttachmentBinding]] = defaultdict(list)
    if version_ids:
        for item in repository.bindings_by_entity_version_ids(version_ids):
            bindings_by_version[item.entity_version_id].append(item)

    snapshots = {
        item.id: item for item in repository.snapshots()
    }
    snapshot_refs: dict[str, list[dict]] = defaultdict(list)
    if version_ids:
        for item in repository.snapshot_entity_versions(version_ids):
            snapshot = snapshots.get(item.snapshot_id)
            if snapshot:
                snapshot_refs[item.entity_version_id].append({
                    "snapshot_id": snapshot.id,
                    "snapshot_number": snapshot.snapshot_number,
                    "snapshot_status": snapshot.status,
                    "role": item.role,
                })

    plans = {item.id: item for item in repository.plans()}
    shot_refs: dict[str, list[dict]] = defaultdict(list)
    if version_ids:
        for shot in repository.shots():
            plan = plans.get(shot.plan_version_id)
            if not plan:
                continue
            refs: list[tuple[str, str]] = []
            if shot.scene_entity_version_id:
                refs.append((shot.scene_entity_version_id, "scene"))
            refs.extend((item, "character") for item in shot.character_entity_version_ids or [])
            refs.extend((item, "outfit") for item in shot.outfit_entity_version_ids or [])
            refs.extend((item, "product") for item in shot.product_entity_version_ids or [])
            for entity_version_id, role in refs:
                if entity_version_id not in version_ids:
                    continue
                shot_refs[entity_version_id].append({
                    "plan_version_id": plan.id,
                    "plan_version_number": plan.version_number,
                    "shot_id": shot.id,
                    "shot_code": shot.shot_code,
                    "role": role,
                })

    versions_by_entity: dict[str, list[dict]] = defaultdict(list)
    for version in versions:
        attachment = attachments.get(version.source_attachment_id)
        versions_by_entity[version.entity_id].append({
            "id": version.id,
            "version_number": version.version_number,
            "attributes": version.attributes,
            "status": version.status,
            "is_active": version.is_active,
            "created_by": version.created_by,
            "created_at": version.created_at,
            "source_attachment": None if not attachment else {
                "id": attachment.id,
                "original_filename": attachment.original_filename,
                "mime_type": attachment.mime_type,
                "byte_size": attachment.byte_size,
                "content_hash": attachment.content_hash,
                "verification_status": attachment.verification_status,
                "created_at": attachment.created_at,
            },
            "bindings": [{
                "id": item.id,
                "binding_type": item.binding_type,
                "status": item.status,
                "confirmed_by": item.confirmed_by,
                "confirmed_at": item.confirmed_at,
            } for item in bindings_by_version.get(version.id, [])],
            "snapshot_references": snapshot_refs.get(version.id, []),
            "shot_references": shot_refs.get(version.id, []),
        })

    rows = []
    for entity in entities:
        project = project_map.get(entity.project_id)
        entity_versions = versions_by_entity.get(entity.id, [])
        rows.append({
            "id": entity.id,
            "project_id": entity.project_id,
            "project_title": project.title if project else entity.project_id,
            "entity_type": entity.entity_type,
            "display_name": entity.display_name,
            "status": entity.status,
            "created_at": entity.created_at,
            "active_version_id": next((item["id"] for item in entity_versions if item["is_active"]), None),
            "versions": entity_versions,
        })
    counts = Counter(item.entity_type for item in entities)
    return {
        "projects": [{"id": item.id, "title": item.title, "status": item.status} for item in projects],
        "counts": {kind: counts.get(kind, 0) for kind in ("character", "outfit", "scene", "product", "voice")},
        "entities": rows,
    }


def attachment_content_path(session: Session, project: Project, attachment_id: str) -> tuple[Path, str]:
    attachment = SqlAlchemyRegistryRepository(session).attachment(attachment_id)
    if not attachment or attachment.project_id != project.id:
        raise RegistryNotFoundError("Attachment not found")
    if attachment.verification_status != "verified":
        raise RegistryConflictError("ATTACHMENT_NOT_AVAILABLE", "附件尚未通过验证，不能读取内容。")
    root = RUNTIME_ROOT.resolve()
    path = (root / attachment.storage_path).resolve()
    if not path.is_relative_to(root):
        raise RegistryConflictError("ATTACHMENT_PATH_INVALID", "附件存储路径不在 V2 运行目录内。")
    if not path.is_file():
        raise RegistryConflictError("ATTACHMENT_FILE_MISSING", "已登记的附件文件不存在。")
    return path, attachment.mime_type
