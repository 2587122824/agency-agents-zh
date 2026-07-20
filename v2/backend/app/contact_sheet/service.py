from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from ..db.models import (
    Asset,
    Attachment,
    DAGNode,
    DependencyEdge,
    Entity,
    EntityVersion,
    Project,
    ProductionSnapshot,
    Shot,
    WorkAttempt,
    WorkItem,
    utc_now,
)
from ..quality.service import asset_read, quality_review_view
from ..repositories import SqlAlchemyContactSheetRepository


def _entity_reference_rows(
    shot: Shot | None,
    entities: dict[str, Entity],
    versions: dict[str, EntityVersion],
    attachments: dict[str, Attachment],
) -> list[dict]:
    if not shot:
        return []
    references: list[tuple[str, str]] = []
    if shot.scene_entity_version_id:
        references.append(("scene", shot.scene_entity_version_id))
    references.extend(("character", item) for item in shot.character_entity_version_ids or [])
    references.extend(("outfit", item) for item in shot.outfit_entity_version_ids or [])
    references.extend(("product", item) for item in shot.product_entity_version_ids or [])

    rows = []
    for role, version_id in references:
        version = versions.get(version_id)
        entity = entities.get(version.entity_id) if version else None
        if not version or not entity:
            continue
        attachment = attachments.get(version.source_attachment_id) if version.source_attachment_id else None
        rows.append({
            "role": role,
            "is_primary": version.id == shot.primary_reference_entity_version_id,
            "entity_id": entity.id,
            "entity_name": entity.display_name,
            "entity_type": entity.entity_type,
            "entity_version_id": version.id,
            "version_number": version.version_number,
            "source_attachment_id": attachment.id if attachment else None,
            "source_filename": attachment.original_filename if attachment else None,
            "source_mime_type": attachment.mime_type if attachment else None,
        })
    return rows


def material_contact_sheet_view(session: Session, project: Project) -> dict:
    repository = SqlAlchemyContactSheetRepository(session)
    snapshot = repository.snapshot(project.active_snapshot_id) if project.active_snapshot_id else None
    if not snapshot or snapshot.project_id != project.id:
        return {
            "project_id": project.id,
            "project_title": project.title,
            "project_status": project.status,
            "generated_at": utc_now(),
            "snapshot": None,
            "entries": [],
            "output_gaps": [],
            "counts": {},
            "boundary": "\u6ca1\u6709\u6d3b\u52a8\u751f\u4ea7\u5feb\u7167\uff1b\u8054\u7edc\u8868\u4e0d\u4f1a\u6539\u7528\u6700\u65b0\u6216\u5386\u53f2\u5feb\u7167\u3002",
        }

    nodes = repository.nodes(snapshot.id)
    node_map = {item.id: item for item in nodes}
    shots = {
        item.id: item for item in repository.shots(project.id, snapshot.plan_version_id)
    }
    assets = repository.assets(project.id, snapshot.id)
    assets_by_node: dict[str, list[Asset]] = defaultdict(list)
    for asset in assets:
        if asset.dag_node_id:
            assets_by_node[asset.dag_node_id].append(asset)

    edges_by_child: dict[str, list[DependencyEdge]] = defaultdict(list)
    for edge in repository.edges(snapshot.id):
        edges_by_child[edge.child_node_id].append(edge)

    items = {
        item.id: item for item in repository.work_items(project.id, snapshot.id)
    }
    attempts = {
        item.id: item for item in repository.attempts_for_items(set(items))
    } if items else {}

    version_ids: set[str] = set()
    for shot in shots.values():
        if shot.scene_entity_version_id:
            version_ids.add(shot.scene_entity_version_id)
        version_ids.update(shot.character_entity_version_ids or [])
        version_ids.update(shot.outfit_entity_version_ids or [])
        version_ids.update(shot.product_entity_version_ids or [])
    versions = {
        item.id: item for item in repository.entity_versions(project.id, version_ids)
    } if version_ids else {}
    entity_ids = {item.entity_id for item in versions.values()}
    entities = {
        item.id: item for item in repository.entities(project.id, entity_ids)
    } if entity_ids else {}
    attachment_ids = {item.source_attachment_id for item in versions.values() if item.source_attachment_id}
    attachments = {
        item.id: item for item in repository.attachments(project.id, attachment_ids)
    } if attachment_ids else {}

    ordered_assets: list[Asset] = []
    for node in nodes:
        ordered_assets.extend(sorted(
            assets_by_node.get(node.id, []),
            key=lambda item: (item.output_index, item.created_at, item.id),
        ))
    ordered_assets.extend(item for item in assets if not item.dag_node_id or item.dag_node_id not in node_map)

    entries = []
    for number, asset in enumerate(ordered_assets, start=1):
        node = node_map.get(asset.dag_node_id)
        shot = shots.get(node.shot_id) if node and node.shot_id else None
        attempt = attempts.get(asset.work_attempt_id) if asset.work_attempt_id else None
        work_item = items.get(attempt.work_item_id) if attempt else None
        manifest = attempt.request_manifest if attempt else {}
        route = None if not attempt or not work_item else {
            "work_item_id": work_item.id,
            "work_item_status": work_item.status,
            "attempt_id": attempt.id,
            "attempt_number": attempt.attempt_number,
            "attempt_state": attempt.state,
            "provider": attempt.provider,
            "adapter_kind": manifest.get("adapter_kind"),
            "provider_workflow_id": manifest.get("provider_workflow_id"),
            "provider_task_id": attempt.provider_task_id,
            "request_fingerprint": attempt.request_fingerprint,
        }
        dependencies = []
        for edge in edges_by_child.get(node.id if node else "", []):
            parent = node_map.get(edge.parent_node_id)
            if not parent:
                continue
            dependencies.append({
                "edge_id": edge.id,
                "dependency_type": edge.dependency_type,
                "input_slot": edge.input_slot,
                "parent_node_id": parent.id,
                "parent_node_key": parent.node_key,
                "registered_assets": [{
                    "id": item.id,
                    "asset_type": item.asset_type,
                    "role": item.role,
                    "state": item.state,
                    "content_hash": item.content_hash,
                } for item in assets_by_node.get(parent.id, [])],
            })
        entries.append({
            "number": number,
            "node_id": node.id if node else None,
            "node_key": node.node_key if node else None,
            "node_kind": node.kind if node else None,
            "asset": asset_read(session, asset),
            "shot": None if not shot else {
                "id": shot.id,
                "shot_code": shot.shot_code,
                "sequence_number": shot.sequence_number,
                "duration_ms": shot.duration_ms,
                "shot_purpose": shot.shot_purpose,
                "face_visibility": shot.face_visibility,
                "text_policy": shot.text_policy,
                "subject_motion": shot.subject_motion,
                "composition": shot.composition,
                "action": shot.action,
                "visual_prompt": shot.visual_prompt,
                "negative_prompt": shot.negative_prompt,
                "primary_reference_entity_version_id": shot.primary_reference_entity_version_id,
            },
            "route": route,
            "dependencies": dependencies,
            "entity_references": _entity_reference_rows(shot, entities, versions, attachments),
            "frozen_reference_image": node.input_contract.get("reference_image") if node else None,
        })

    quality = quality_review_view(session, project)
    counts = Counter(item.state for item in assets)
    return {
        "project_id": project.id,
        "project_title": project.title,
        "project_status": project.status,
        "generated_at": utc_now(),
        "snapshot": {
            "id": snapshot.id,
            "snapshot_number": snapshot.snapshot_number,
            "status": snapshot.status,
            "contract_hash": snapshot.contract_hash,
            "plan_version_id": snapshot.plan_version_id,
        },
        "entries": entries,
        "output_gaps": quality["output_gaps"],
        "counts": dict(sorted(counts.items())),
        "boundary": "\u53ea\u8bfb\u6295\u5f71\uff1b\u4f9d\u8d56\u5361\u5c55\u793a\u58f0\u660e\u8282\u70b9\u53ca\u5176\u5168\u90e8\u767b\u8bb0\u8f93\u51fa\uff0c\u4e0d\u63a8\u65ad\u6267\u884c\u65f6\u91c7\u7528\u4e86\u54ea\u4e2a Asset\u3002",
    }
