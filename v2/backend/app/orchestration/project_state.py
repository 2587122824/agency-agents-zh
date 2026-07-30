from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping


ProjectStage = Literal[
    "requirements",
    "planning",
    "production_preparation",
    "production",
    "quality_review",
    "editing",
    "delivery",
    "completed",
]


STAGE_LABELS: Mapping[ProjectStage, str] = MappingProxyType({
    "requirements": "需求确认",
    "planning": "方案规划",
    "production_preparation": "生产准备",
    "production": "生产执行",
    "quality_review": "素材审核",
    "editing": "剪辑",
    "delivery": "最终交付",
    "completed": "已完成",
})


@dataclass(frozen=True)
class SnapshotStateFact:
    status: str
    cost_status: str | None = None


@dataclass(frozen=True)
class ProjectStateFacts:
    project_id: str
    persisted_status: str
    has_delivery_asset: bool = False
    delivery_status: str | None = None
    timeline_status: str | None = None
    active_snapshot: SnapshotStateFact | None = None
    latest_snapshot: SnapshotStateFact | None = None
    has_active_plan: bool = False
    has_planning_candidate: bool = False
    work_counts: Mapping[str, int] = field(default_factory=dict)
    asset_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectNextAction:
    code: str
    label: str
    path: str
    confirmation_level: str | None = None
    incurs_production_cost: bool = False

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "label": self.label,
            "path": self.path,
        }
        if self.confirmation_level is not None:
            result["confirmation_level"] = self.confirmation_level
        if self.incurs_production_cost:
            result["incurs_production_cost"] = True
        return result


@dataclass(frozen=True)
class ProjectStateEvaluation:
    stage: ProjectStage
    stage_label: str
    next_action: ProjectNextAction


def _authority_snapshot(facts: ProjectStateFacts) -> SnapshotStateFact | None:
    return facts.active_snapshot or facts.latest_snapshot


def evaluate_stage(facts: ProjectStateFacts) -> ProjectStage:
    if facts.timeline_status in {"candidate", "review"} or facts.persisted_status == "editing":
        return "editing"
    if facts.timeline_status == "confirmed" or facts.persisted_status == "delivery_ready":
        return "delivery"
    if facts.has_delivery_asset or facts.delivery_status == "verified":
        return "completed"
    if (
        facts.delivery_status is not None
        or facts.timeline_status == "exported"
    ):
        return "delivery"
    if facts.timeline_status is not None:
        return "editing"
    authority = _authority_snapshot(facts)
    if facts.persisted_status == "quality_review" or (
        authority is not None and authority.status == "execution_completed"
    ):
        return "quality_review"
    if facts.persisted_status == "producing" or (
        authority is not None and authority.status in {"submitted", "execution_blocked"}
    ):
        return "production"
    if (
        facts.persisted_status in {"contract_ready", "production_ready"}
        or authority is not None
        or facts.has_active_plan
    ):
        return "production_preparation"
    if facts.persisted_status in {"planning", "plan_review"} or facts.has_planning_candidate:
        return "planning"
    return "requirements"


def evaluate_next_action(facts: ProjectStateFacts, stage: ProjectStage) -> ProjectNextAction:
    project_path = f"/projects/{facts.project_id}"
    if stage == "completed":
        return ProjectNextAction("DOWNLOAD_DELIVERY", "查看并下载最终交付", "/editor")
    if stage == "delivery":
        if facts.delivery_status is None:
            return ProjectNextAction(
                "AUTHORIZE_DELIVERY",
                "授权确认时间线交付",
                "/editor",
                confirmation_level="high",
            )
        if facts.delivery_status == "authorized":
            return ProjectNextAction("UPLOAD_DELIVERY", "上传最终 MP4", "/editor")
        if facts.delivery_status == "output_registered":
            return ProjectNextAction(
                "VERIFY_DELIVERY",
                "验证最终交付文件",
                "/editor",
                confirmation_level="normal",
            )
        if facts.delivery_status == "blocked":
            return ProjectNextAction("VIEW_DELIVERY_BLOCK", "查看交付阻断证据", "/editor")
        return ProjectNextAction("VIEW_DELIVERY", "查看最终交付", "/editor")
    if stage == "editing":
        if facts.timeline_status is None:
            return ProjectNextAction("CREATE_TIMELINE", "创建时间线候选", "/editor")
        if facts.timeline_status == "candidate":
            return ProjectNextAction("VALIDATE_TIMELINE", "校验时间线候选", "/editor")
        if facts.timeline_status == "review":
            return ProjectNextAction(
                "CONFIRM_TIMELINE",
                "确认剪辑合同",
                "/editor",
                confirmation_level="high",
            )
        return ProjectNextAction("VIEW_TIMELINE", "查看剪辑时间线", "/editor")
    if stage == "quality_review":
        if facts.asset_counts.get("created", 0):
            label = f"验证 {facts.asset_counts['created']} 个已登记素材"
        elif facts.asset_counts.get("verified", 0):
            label = f"执行 {facts.asset_counts['verified']} 个素材 QC"
        elif facts.asset_counts.get("review_required", 0):
            label = f"审核 {facts.asset_counts['review_required']} 个素材"
        else:
            label = "查看素材审核与输出缺口"
        return ProjectNextAction("OPEN_QUALITY_REVIEW", label, "/review")
    authority = _authority_snapshot(facts)
    if stage == "production":
        production_path = f"/production?project={facts.project_id}"
        if facts.work_counts.get("blocked", 0):
            return ProjectNextAction(
                "VIEW_PRODUCTION_BLOCKERS",
                f"查看 {facts.work_counts['blocked']} 个生产阻断",
                production_path,
            )
        if facts.work_counts.get("in_progress", 0) or facts.work_counts.get("queued", 0):
            return ProjectNextAction("MONITOR_PRODUCTION", "查看生产执行进度", production_path)
        return ProjectNextAction("VIEW_PRODUCTION", "查看生产执行", production_path)
    if stage == "production_preparation":
        if authority is None:
            return ProjectNextAction(
                "ANALYZE_PRODUCTION_IMPACT",
                "选择配置并分析生产影响",
                f"{project_path}/plan",
            )
        if authority.status == "preparing" and authority.cost_status == "estimated":
            return ProjectNextAction(
                "CONFIRM_PRODUCTION_COST",
                "确认预计费用并锁定快照",
                f"{project_path}/plan",
                confirmation_level="high",
            )
        if authority.status == "preparing":
            return ProjectNextAction(
                "CONFIGURE_PRICING",
                "补齐价格目录后创建新快照",
                f"{project_path}/plan",
            )
        if authority.status == "locked":
            return ProjectNextAction(
                "ACTIVATE_AND_SUBMIT_PRODUCTION",
                "确认并开始制作",
                f"{project_path}/plan",
                confirmation_level="high",
                incurs_production_cost=True,
            )
        if authority.status == "active":
            return ProjectNextAction(
                "SUBMIT_PRODUCTION",
                "确认完整 DAG 并提交生产",
                f"{project_path}/plan",
                confirmation_level="high",
                incurs_production_cost=True,
            )
        return ProjectNextAction(
            "OPEN_PRODUCTION_PREPARATION",
            "查看生产准备",
            f"{project_path}/plan",
        )
    if stage == "planning":
        return ProjectNextAction(
            "CONTINUE_PLANNING",
            "继续审核创意与分镜候选",
            f"{project_path}/plan",
        )
    return ProjectNextAction("CONTINUE_REQUIREMENTS", "继续确认创作需求", project_path)


def evaluate_project_state(facts: ProjectStateFacts) -> ProjectStateEvaluation:
    stage = evaluate_stage(facts)
    return ProjectStateEvaluation(
        stage=stage,
        stage_label=STAGE_LABELS[stage],
        next_action=evaluate_next_action(facts, stage),
    )
