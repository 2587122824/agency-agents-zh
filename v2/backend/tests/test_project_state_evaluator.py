from __future__ import annotations

from copy import deepcopy

import pytest

from v2.backend.app.orchestration.project_state import (
    ProjectStateFacts,
    SnapshotStateFact,
    evaluate_project_state,
)


def facts(**overrides: object) -> ProjectStateFacts:
    values: dict[str, object] = {
        "project_id": "project_test",
        "persisted_status": "draft",
    }
    values.update(overrides)
    return ProjectStateFacts(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("state_facts", "expected_stage"),
    [
        (facts(has_delivery_asset=True, delivery_status="blocked"), "completed"),
        (facts(delivery_status="verified"), "completed"),
        (facts(delivery_status="authorized"), "delivery"),
        (facts(timeline_status="confirmed"), "delivery"),
        (facts(persisted_status="delivery_ready"), "delivery"),
        (facts(timeline_status="candidate"), "editing"),
        (facts(persisted_status="editing"), "editing"),
        (facts(persisted_status="quality_review"), "quality_review"),
        (facts(active_snapshot=SnapshotStateFact("execution_completed")), "quality_review"),
        (facts(active_snapshot=SnapshotStateFact("submitted")), "production"),
        (facts(latest_snapshot=SnapshotStateFact("execution_blocked")), "production"),
        (
            facts(
                active_snapshot=SnapshotStateFact("preparing"),
                latest_snapshot=SnapshotStateFact("submitted"),
            ),
            "production_preparation",
        ),
        (facts(active_snapshot=SnapshotStateFact("locked")), "production_preparation"),
        (facts(persisted_status="production_ready"), "production_preparation"),
        (facts(persisted_status="contract_ready"), "production_preparation"),
        (facts(has_active_plan=True), "production_preparation"),
        (facts(persisted_status="producing"), "production"),
        (facts(persisted_status="plan_review"), "planning"),
        (facts(persisted_status="planning"), "planning"),
        (facts(has_planning_candidate=True), "planning"),
        (facts(), "requirements"),
    ],
)
def test_project_stage_is_deterministic_and_uses_active_authority(
    state_facts: ProjectStateFacts,
    expected_stage: str,
) -> None:
    assert evaluate_project_state(state_facts).stage == expected_stage


@pytest.mark.parametrize(
    ("state_facts", "expected_code", "expected_label"),
    [
        (facts(), "CONTINUE_REQUIREMENTS", "继续确认创作需求"),
        (facts(has_planning_candidate=True), "CONTINUE_PLANNING", "继续审核创意与分镜候选"),
        (facts(has_active_plan=True), "ANALYZE_PRODUCTION_IMPACT", "选择配置并分析生产影响"),
        (
            facts(active_snapshot=SnapshotStateFact("preparing", "estimated")),
            "CONFIRM_PRODUCTION_COST",
            "确认预计费用并锁定快照",
        ),
        (
            facts(active_snapshot=SnapshotStateFact("preparing", "not_configured")),
            "CONFIGURE_PRICING",
            "补齐价格目录后创建新快照",
        ),
        (facts(active_snapshot=SnapshotStateFact("locked")), "ACTIVATE_AND_SUBMIT_PRODUCTION", "确认并开始制作"),
        (facts(active_snapshot=SnapshotStateFact("active")), "SUBMIT_PRODUCTION", "确认完整 DAG 并提交生产"),
        (
            facts(active_snapshot=SnapshotStateFact("submitted"), work_counts={"blocked": 2}),
            "VIEW_PRODUCTION_BLOCKERS",
            "查看 2 个生产阻断",
        ),
        (
            facts(active_snapshot=SnapshotStateFact("submitted"), work_counts={"queued": 1}),
            "MONITOR_PRODUCTION",
            "查看生产执行进度",
        ),
        (
            facts(persisted_status="quality_review", asset_counts={"created": 2, "verified": 3}),
            "OPEN_QUALITY_REVIEW",
            "验证 2 个已登记素材",
        ),
        (
            facts(persisted_status="quality_review", asset_counts={"verified": 3}),
            "OPEN_QUALITY_REVIEW",
            "执行 3 个素材 QC",
        ),
        (
            facts(persisted_status="quality_review", asset_counts={"review_required": 4}),
            "OPEN_QUALITY_REVIEW",
            "审核 4 个素材",
        ),
        (facts(persisted_status="editing"), "CREATE_TIMELINE", "创建时间线候选"),
        (facts(timeline_status="candidate"), "VALIDATE_TIMELINE", "校验时间线候选"),
        (facts(timeline_status="review"), "CONFIRM_TIMELINE", "确认剪辑合同"),
        (facts(timeline_status="confirmed"), "AUTHORIZE_DELIVERY", "授权确认时间线交付"),
        (facts(delivery_status="authorized"), "UPLOAD_DELIVERY", "上传最终 MP4"),
        (facts(delivery_status="output_registered"), "VERIFY_DELIVERY", "验证最终交付文件"),
        (facts(delivery_status="blocked"), "VIEW_DELIVERY_BLOCK", "查看交付阻断证据"),
        (facts(has_delivery_asset=True), "DOWNLOAD_DELIVERY", "查看并下载最终交付"),
    ],
)
def test_project_next_action_is_derived_from_explicit_facts(
    state_facts: ProjectStateFacts,
    expected_code: str,
    expected_label: str,
) -> None:
    action = evaluate_project_state(state_facts).next_action
    assert action.code == expected_code
    assert action.label == expected_label


def test_project_state_evaluation_is_repeatable_and_does_not_mutate_inputs() -> None:
    work_counts = {"blocked": 1, "queued": 3}
    asset_counts = {"created": 2}
    state_facts = facts(
        persisted_status="blocked",
        active_snapshot=SnapshotStateFact("execution_blocked", "confirmed"),
        work_counts=work_counts,
        asset_counts=asset_counts,
    )
    before_work = deepcopy(work_counts)
    before_assets = deepcopy(asset_counts)

    first = evaluate_project_state(state_facts)
    second = evaluate_project_state(state_facts)

    assert first == second
    assert first.stage == "production"
    assert first.next_action.as_dict() == {
        "code": "VIEW_PRODUCTION_BLOCKERS",
        "label": "查看 1 个生产阻断",
        "path": "/production?project=project_test",
    }
    assert work_counts == before_work
    assert asset_counts == before_assets


def test_high_risk_and_cost_metadata_remain_explicit() -> None:
    locked_submission = evaluate_project_state(
        facts(active_snapshot=SnapshotStateFact("locked", "confirmed"))
    ).next_action.as_dict()
    submission = evaluate_project_state(
        facts(active_snapshot=SnapshotStateFact("active", "confirmed"))
    ).next_action.as_dict()
    verification = evaluate_project_state(
        facts(delivery_status="output_registered")
    ).next_action.as_dict()

    assert locked_submission["confirmation_level"] == "high"
    assert locked_submission["incurs_production_cost"] is True
    assert submission["confirmation_level"] == "high"
    assert submission["incurs_production_cost"] is True
    assert verification["confirmation_level"] == "normal"
    assert "incurs_production_cost" not in verification
