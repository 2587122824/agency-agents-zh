from __future__ import annotations

from collections import Counter, defaultdict, deque

from sqlalchemy.orm import Session

from ..db.models import Project, utc_now
from ..repositories import ImpactRepository, SqlAlchemyImpactRepository


def _node_id(record_type: str, record_id: str) -> str:
    return f"{record_type}:{record_id}"


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

    briefs = repository.creative_briefs(project.id)
    for item in briefs:
        add_node("creative_brief", item.id, "Creative Brief", item.status, "candidate")
        add_edge("agent_run", item.agent_run_id, "creative_brief", item.id, "produced")
        add_edge("requirement_version", item.requirement_version_id, "creative_brief", item.id, "planned_from")

    shot_plans = repository.shot_plans(project.id)
    for item in shot_plans:
        add_node("shot_plan", item.id, "分镜候选", item.status, "candidate")
        add_edge("agent_run", item.agent_run_id, "shot_plan", item.id, "produced")
        add_edge("creative_brief", item.creative_brief_candidate_id, "shot_plan", item.id, "directed_from")
        add_edge("requirement_version", item.requirement_version_id, "shot_plan", item.id, "planned_from")

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
        add_node("dag_node", item.id, item.node_key, item.kind, "recorded", kind=item.kind)
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
