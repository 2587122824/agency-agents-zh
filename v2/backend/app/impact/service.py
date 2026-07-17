from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..creation.service import _event, _receipt, _save_receipt
from ..db.models import (
    DecisionChangeImpactAnalysis,
    DecisionChangeImpactTarget,
    Project,
    utc_now,
)
from ..repositories import ImpactRepository, SqlAlchemyImpactRepository
from .contracts import AnalyzeDecisionChangeImpact


class ImpactConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ImpactNotFoundError(LookupError):
    pass


def _node_id(record_type: str, record_id: str) -> str:
    return f"{record_type}:{record_id}"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _referenced_entity_versions(payload: dict) -> set[str]:
    values: set[str] = set()
    if payload.get("scene_entity_version_id"):
        values.add(str(payload["scene_entity_version_id"]))
    for key in (
        "character_entity_version_ids",
        "outfit_entity_version_ids",
        "product_entity_version_ids",
        "character_refs",
        "outfit_refs",
        "scene_refs",
        "product_refs",
        "voice_refs",
    ):
        values.update(str(value) for value in (payload.get(key) or []) if value)
    return values


def decision_impact_graph_view(
    session: Session,
    project: Project,
    repository: ImpactRepository | None = None,
) -> dict:
    repository = repository or SqlAlchemyImpactRepository(session)
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str, str]] = set()

    def add_node(
        record_type: str,
        record_id: str,
        label: str,
        status: str,
        authority: str = "recorded",
        **details,
    ) -> str:
        node_id = _node_id(record_type, record_id)
        nodes[node_id] = {
            "node_id": node_id,
            "record_type": record_type,
            "record_id": record_id,
            "label": label,
            "status": status,
            "authority": authority,
            "details": details,
        }
        return node_id

    def add_edge(source_type: str, source_id: str, target_type: str, target_id: str, relation: str) -> None:
        source = _node_id(source_type, source_id)
        target = _node_id(target_type, target_id)
        if source in nodes and target in nodes:
            edges.add((source, target, relation))

    decisions = repository.decisions(project.id)
    decision_ids = {item.id for item in decisions}
    for item in decisions:
        add_node("decision", item.id, item.label, item.status, "current", key=item.key, source=item.source)

    manifests = repository.manifests(project.id)
    for item in manifests:
        add_node(
            "manifest",
            item.id,
            f"输入清单 {item.id[-8:]}",
            "frozen",
            "evidence",
            input_hash=item.input_hash,
            system_config_version=item.system_config_version,
        )
    for item in manifests:
        for decision_id in item.decision_ids or []:
            if decision_id in decision_ids:
                add_edge("decision", decision_id, "manifest", item.id, "consumed_by")

    runs = repository.agent_runs(project.id)
    for item in runs:
        add_node(
            "agent_run",
            item.id,
            f"{item.agent_role} Agent",
            item.status,
            "evidence",
            model_provider=item.model_provider,
            model_name=item.model_name,
        )
        add_edge("manifest", item.input_manifest_id, "agent_run", item.id, "executed_as")

    requirement_candidates = repository.requirement_candidates(project.id)
    for item in requirement_candidates:
        add_node("requirement_candidate", item.id, "需求候选", item.status, "candidate")
        add_edge("agent_run", item.agent_run_id, "requirement_candidate", item.id, "produced")

    requirements = repository.requirement_versions(project.id)
    for item in requirements:
        add_node(
            "requirement_version",
            item.id,
            f"需求 v{item.version_number}",
            "active" if item.is_active else "historical",
            "active" if item.is_active else "historical",
            version_number=item.version_number,
        )
        if item.candidate_id:
            add_edge("requirement_candidate", item.candidate_id, "requirement_version", item.id, "accepted_as")

    entities = repository.entities(project.id)
    for item in entities:
        add_node("entity", item.id, item.display_name, item.status, "recorded", entity_type=item.entity_type)

    entity_versions = repository.entity_versions(project.id)
    entity_version_ids = {item.id for item in entity_versions}
    for item in entity_versions:
        add_node(
            "entity_version",
            item.id,
            f"实体版本 v{item.version_number}",
            item.status,
            "active" if item.is_active else "historical",
            entity_id=item.entity_id,
            version_number=item.version_number,
        )
        add_edge("entity_version", item.id, "entity", item.entity_id, "version_of")

    briefs = repository.creative_briefs(project.id)
    for item in briefs:
        add_node("creative_brief", item.id, "Creative Brief", item.status, "candidate")
        add_edge("agent_run", item.agent_run_id, "creative_brief", item.id, "produced")
        add_edge("requirement_version", item.requirement_version_id, "creative_brief", item.id, "planned_from")
        for version_id in sorted(_referenced_entity_versions(item.brief) & entity_version_ids):
            add_edge("creative_brief", item.id, "entity_version", version_id, "references")

    shot_plans = repository.shot_plans(project.id)
    for item in shot_plans:
        add_node("shot_plan", item.id, "分镜候选", item.status, "candidate")
        if item.agent_run_id:
            add_edge("agent_run", item.agent_run_id, "shot_plan", item.id, "produced")
        if item.supersedes_candidate_id:
            add_edge("shot_plan", item.supersedes_candidate_id, "shot_plan", item.id, "superseded_by")
        add_edge("creative_brief", item.creative_brief_candidate_id, "shot_plan", item.id, "directed_from")
        add_edge("requirement_version", item.requirement_version_id, "shot_plan", item.id, "planned_from")
        referenced_versions: set[str] = set()
        for shot_contract in item.shots or []:
            referenced_versions.update(_referenced_entity_versions(shot_contract))
        for version_id in sorted(referenced_versions & entity_version_ids):
            add_edge("shot_plan", item.id, "entity_version", version_id, "references")

    plans = repository.plans(project.id)
    for item in plans:
        add_node(
            "plan",
            item.id,
            f"方案 v{item.version_number}",
            item.status,
            "active" if item.is_active else "historical",
            version_number=item.version_number,
        )
        add_edge("shot_plan", item.shot_plan_candidate_id, "plan", item.id, "accepted_as")
        add_edge("requirement_version", item.requirement_version_id, "plan", item.id, "governs")

    shots = repository.shots(project.id)
    for item in shots:
        add_node(
            "shot",
            item.id,
            item.shot_code,
            "contract",
            "recorded",
            sequence_number=item.sequence_number,
            shot_type=item.shot_type,
        )
        add_edge("plan", item.plan_version_id, "shot", item.id, "contains")
        for version_id in sorted(_referenced_entity_versions({
            "scene_entity_version_id": item.scene_entity_version_id,
            "character_entity_version_ids": item.character_entity_version_ids,
            "outfit_entity_version_ids": item.outfit_entity_version_ids,
            "product_entity_version_ids": item.product_entity_version_ids,
        }) & entity_version_ids):
            add_edge("shot", item.id, "entity_version", version_id, "references")

    snapshots = repository.snapshots(project.id)
    snapshot_ids = {item.id for item in snapshots}
    for item in snapshots:
        add_node(
            "snapshot",
            item.id,
            f"快照 #{item.snapshot_number}",
            item.status,
            "active" if item.id == project.active_snapshot_id else "historical",
            snapshot_number=item.snapshot_number,
            contract_hash=item.contract_hash,
        )
        add_edge("plan", item.plan_version_id, "snapshot", item.id, "frozen_into")

    dag_nodes = repository.dag_nodes(snapshot_ids)
    for item in dag_nodes:
        add_node(
            "dag_node",
            item.id,
            item.node_key,
            item.kind,
            "recorded",
            kind=item.kind,
            snapshot_id=item.snapshot_id,
            shot_id=item.shot_id,
            workflow_slot_version_id=item.workflow_slot_version_id,
            estimated_cost=item.estimated_cost,
            currency=item.currency,
        )
        add_edge("snapshot", item.snapshot_id, "dag_node", item.id, "compiled_to")
        if item.shot_id:
            add_edge("shot", item.shot_id, "dag_node", item.id, "realized_by")

    work_items = repository.work_items(project.id)
    for item in work_items:
        add_node("work_item", item.id, item.kind, item.status, "recorded", kind=item.kind)
        if item.dag_node_id:
            add_edge("dag_node", item.dag_node_id, "work_item", item.id, "scheduled_as")
        elif item.snapshot_id:
            add_edge("snapshot", item.snapshot_id, "work_item", item.id, "scheduled_as")

    assets = repository.assets(project.id)
    for item in assets:
        add_node(
            "asset",
            item.id,
            f"{item.asset_type} / {item.role}",
            item.state,
            "recorded",
            asset_type=item.asset_type,
            role=item.role,
        )
        if item.dag_node_id:
            add_edge("dag_node", item.dag_node_id, "asset", item.id, "produced")
        elif item.snapshot_id:
            add_edge("snapshot", item.snapshot_id, "asset", item.id, "registered_in")

    timelines = repository.timelines(project.id)
    timeline_ids = {item.id for item in timelines}
    for item in timelines:
        add_node(
            "timeline",
            item.id,
            f"时间线 v{item.version_number}",
            item.status,
            "active" if item.status in {"confirmed", "exported"} else "candidate",
            version_number=item.version_number,
        )
        add_edge("snapshot", item.snapshot_id, "timeline", item.id, "edited_as")

    timeline_items = repository.timeline_items(timeline_ids)
    for item in timeline_items:
        add_node(
            "timeline_item",
            item.id,
            item.label,
            item.track_type,
            "recorded",
            track_type=item.track_type,
            sequence_number=item.sequence_number,
        )
        add_edge("timeline", item.timeline_id, "timeline_item", item.id, "contains")
        if item.asset_id:
            add_edge("asset", item.asset_id, "timeline_item", item.id, "used_by")

    adjacency: dict[str, set[str]] = defaultdict(set)
    for source, target, _ in edges:
        adjacency[source].add(target)

    summaries = []
    reachable_union: set[str] = set()
    for decision in decisions:
        start = _node_id("decision", decision.id)
        visited = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for target in adjacency.get(current, set()):
                if target not in visited:
                    visited.add(target)
                    queue.append(target)
        reachable_union.update(visited)
        downstream = sorted(visited - {start})
        counts = Counter(nodes[item]["record_type"] for item in downstream)
        direct_manifests = sorted(
            nodes[target]["record_id"]
            for target in adjacency.get(start, set())
            if nodes[target]["record_type"] == "manifest"
        )
        summaries.append({
            "decision_id": decision.id,
            "key": decision.key,
            "label": decision.label,
            "current_value": decision.value,
            "status": decision.status,
            "observation_status": "observed" if direct_manifests else "not_observed",
            "direct_manifest_ids": direct_manifests,
            "downstream_node_ids": downstream,
            "downstream_counts": dict(sorted(counts.items())),
            "active_downstream_count": sum(nodes[item]["authority"] == "active" for item in downstream),
        })

    filtered_nodes = [nodes[item] for item in sorted(reachable_union)]
    filtered_edges = [
        {"source_node_id": source, "target_node_id": target, "relation": relation}
        for source, target, relation in sorted(edges)
        if source in reachable_union and target in reachable_union
    ]
    return {
        "project_id": project.id,
        "project_title": project.title,
        "generated_at": utc_now(),
        "scope": "observed_lineage",
        "decisions": summaries,
        "nodes": filtered_nodes,
        "edges": filtered_edges,
        "boundary": "只展示被 AgentInputManifest 精确冻结并沿持久化外键传播的已观测证据；未观测不等于无影响，系统不会按决策名称推断、自动失效、重做或重试。",
    }


_ANALYSIS_TARGET_TYPES = {
    "entity",
    "entity_version",
    "requirement_candidate",
    "requirement_version",
    "creative_brief",
    "shot_plan",
    "plan",
    "shot",
    "snapshot",
    "dag_node",
    "work_item",
    "asset",
    "timeline",
    "timeline_item",
}


def _analysis_dict(repository: ImpactRepository, row: DecisionChangeImpactAnalysis) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "decision_id": row.decision_id,
        "status": row.status,
        "scope": row.scope,
        "current_value": row.current_value,
        "proposed_value": row.proposed_value,
        "observed_manifest_ids": row.observed_manifest_ids,
        "target_counts": row.target_counts,
        "estimated_work_count": row.estimated_work_count,
        "cost_status": row.cost_status,
        "estimated_cost": row.estimated_cost,
        "currency": row.currency,
        "analysis_hash": row.analysis_hash,
        "active_snapshot_id": row.active_snapshot_id,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "targets": repository.change_analysis_targets(row.id),
    }


def analyze_decision_change(
    session: Session,
    project: Project,
    decision_id: str,
    payload: AnalyzeDecisionChangeImpact,
    repository: ImpactRepository | None = None,
) -> dict:
    repository = repository or SqlAlchemyImpactRepository(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        if receipt.command_type != "decision.change_impact.analyze":
            raise ImpactConflictError("COMMAND_ID_REUSED", "命令 ID 已用于其他操作。")
        analysis = repository.change_analysis(project.id, receipt.result_id)
        if not analysis:
            raise ImpactConflictError("COMMAND_RESULT_MISSING", "影响分析命令的原始结果不存在。")
        if analysis.decision_id != decision_id or _canonical(analysis.proposed_value) != _canonical(payload.proposed_value):
            raise ImpactConflictError("COMMAND_REPLAY_MISMATCH", "命令重放的决策或提议值与原始请求不一致。")
        return _analysis_dict(repository, analysis)

    decision = repository.decision(project.id, decision_id)
    if not decision:
        raise ImpactNotFoundError("Decision not found in project")
    if decision.status != "resolved":
        raise ImpactConflictError("DECISION_NOT_RESOLVED", "只有已解决决策可以创建变更影响分析。")
    if _canonical(decision.value) == _canonical(payload.proposed_value):
        raise ImpactConflictError("DECISION_VALUE_UNCHANGED", "提议值与当前决策值相同，不需要影响分析。")

    graph = decision_impact_graph_view(session, project, repository)
    summary = next(item for item in graph["decisions"] if item["decision_id"] == decision.id)
    nodes = {item["node_id"]: item for item in graph["nodes"]}
    target_nodes = [
        nodes[node_id]
        for node_id in summary["downstream_node_ids"]
        if node_id in nodes and nodes[node_id]["record_type"] in _ANALYSIS_TARGET_TYPES
    ]
    active_payable_nodes = [
        item for item in target_nodes
        if item["record_type"] == "dag_node"
        and item["details"].get("snapshot_id") == project.active_snapshot_id
        and item["details"].get("workflow_slot_version_id")
    ]
    estimated_work_count = len(active_payable_nodes)
    missing_cost = any(
        item["details"].get("estimated_cost") is None or not item["details"].get("currency")
        for item in active_payable_nodes
    )
    currencies = {str(item["details"].get("currency")) for item in active_payable_nodes if item["details"].get("currency")}
    if not active_payable_nodes:
        cost_status, estimated_cost, currency = "not_applicable", None, None
    elif missing_cost:
        cost_status, estimated_cost, currency = "not_configured", None, None
    elif len(currencies) != 1:
        cost_status, estimated_cost, currency = "mixed_currency", None, None
    else:
        currency = next(iter(currencies))
        estimated_cost = float(_money(sum(
            (Decimal(str(item["details"]["estimated_cost"])) for item in active_payable_nodes),
            Decimal("0"),
        )))
        cost_status = "estimated"

    target_counts = dict(sorted(Counter(item["record_type"] for item in target_nodes).items()))
    target_payloads = []
    active_payable_ids = {item["node_id"] for item in active_payable_nodes}
    for item in sorted(target_nodes, key=lambda value: value["node_id"]):
        included = item["node_id"] in active_payable_ids
        target_payloads.append({
            "record_type": item["record_type"],
            "record_id": item["record_id"],
            "label": item["label"],
            "record_status": item["status"],
            "authority": item["authority"],
            "impact_kind": "review_candidate",
            "reason_code": "OBSERVED_DECISION_LINEAGE",
            "included_in_estimate": included,
            "estimated_work_units": 1 if included else 0,
            "estimated_cost": item["details"].get("estimated_cost") if included else None,
            "currency": item["details"].get("currency") if included else None,
            "evidence": {"node_id": item["node_id"], "details": item["details"]},
        })
    frozen = {
        "project_id": project.id,
        "decision_id": decision.id,
        "current_value": decision.value,
        "proposed_value": payload.proposed_value,
        "scope": "observed_lineage_with_active_cost",
        "observed_manifest_ids": summary["direct_manifest_ids"],
        "targets": target_payloads,
        "estimated_work_count": estimated_work_count,
        "cost_status": cost_status,
        "estimated_cost": estimated_cost,
        "currency": currency,
        "active_snapshot_id": project.active_snapshot_id,
    }
    analysis = DecisionChangeImpactAnalysis(
        project_id=project.id,
        decision_id=decision.id,
        status="completed" if summary["observation_status"] == "observed" else "insufficient_evidence",
        scope="observed_lineage_with_active_cost",
        current_value=decision.value,
        proposed_value=payload.proposed_value,
        observed_manifest_ids=summary["direct_manifest_ids"],
        target_counts=target_counts,
        estimated_work_count=estimated_work_count,
        cost_status=cost_status,
        estimated_cost=estimated_cost,
        currency=currency,
        analysis_hash=hashlib.sha256(_canonical(frozen).encode("utf-8")).hexdigest(),
        active_snapshot_id=project.active_snapshot_id,
        created_by=payload.actor_id,
    )
    repository.add(analysis)
    repository.flush()
    for item in target_payloads:
        repository.add(DecisionChangeImpactTarget(analysis_id=analysis.id, **item))
    _save_receipt(
        session,
        project.id,
        payload.command_id,
        "decision.change_impact.analyze",
        "decision_change_impact_analysis",
        analysis.id,
    )
    _event(session, project.id, "decision.change_impact_analyzed.v1", "决策变更影响分析已保存", {
        "analysis_id": analysis.id,
        "decision_id": decision.id,
        "status": analysis.status,
        "target_counts": target_counts,
        "estimated_work_count": estimated_work_count,
        "cost_status": cost_status,
    })
    session.commit()
    return _analysis_dict(repository, analysis)


def decision_change_impact_workspace(
    session: Session,
    project: Project,
    repository: ImpactRepository | None = None,
) -> dict:
    repository = repository or SqlAlchemyImpactRepository(session)
    return {
        "project_id": project.id,
        "analyses": [_analysis_dict(repository, item) for item in repository.change_analysis_history(project.id)],
        "boundary": "分析报告只冻结当前已观测下游与活动快照价格证据，不修改决策、项目状态、素材、DAG、路由或费用账本，也不会创建重做或重试任务。",
    }
