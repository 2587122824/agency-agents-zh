# 片场 V2 权威项目状态转移器实现

> 实现版本：Sprint 31
>
> 状态：当前业务命令面已接入；恢复、取消和重新开版命令未开放
>
> 实现目录：`v2/backend/app/orchestration/project_transitions.py`

## 1. 目标

权威项目状态转移器负责 V2 当前业务命令面的全部 `Project.status` 写入。应用服务和 Worker 只能提交类型化触发器与已经验证的业务事实，不能直接赋值项目状态。

本次同时实现：

- `status + row_version` 原子条件更新。
- 状态触发器、操作者和变更时间持久化。
- 首次阻断来源、原因码、责任聚合和允许命令持久化。
- 状态变化与 ProjectEvent 在调用方同一事务提交。
- 旧运行库基于精确方案、活动快照、时间线和交付记录的一次性迁移。

本次不实现 Outbox、自动状态推进、自动解除阻断、重试或任何供应商执行。

## 2. 持久化合同

Project 新增：

```text
row_version
state_changed_at
state_actor_type
state_changed_by
state_trigger
state_reason_code nullable
blocked_from_state nullable
blocked_responsible_aggregate_type nullable
blocked_responsible_aggregate_id nullable
blocked_allowed_commands[]
blocked_at nullable
```

迁移版本为 `20260716_13`，并由 `20260716_14` 补齐旧规划候选的状态权威。现有 blocked 项目保留 blocked 并写入明确迁移原因；迁移不自动解除阻断。规划回填只读取 `accepted/awaiting_review` 候选与活动方案，不按名称或文本推断。

## 3. 原子 Repository

`ProjectStateRepository.transition_state` 使用以下条件更新：

```text
project_id
AND expected_status
AND expected_row_version
```

成功时同时写目标状态、`row_version + 1`、触发器、操作者和阻断字段。条件不匹配时返回冲突，转移服务抛出 `PROJECT_STATE_VERSION_CONFLICT`，不会覆盖并发命令结果。

Repository 不 commit、不创建事件、不判断业务触发器是否合法。调用方应用服务仍拥有事务。

## 4. 显式触发器

当前正式链路包含：

```text
message_added
decision_requested
decisions_resolved
decisions_resolved_requirements_ready
requirement_confirmed
brief_candidate_created
brief_accepted / brief_rejected
shot_candidate_created / shot_candidate_revised
shot_plan_accepted / shot_plan_rejected
snapshot_prepared / snapshot_locked / snapshot_activated
production_submitted / production_progress / production_settled
quality_recorded / quality_stage_approved
timeline_candidate_created / timeline_confirmed
delivery_verified
```

每个触发器有明确的来源状态与目标状态表。没有表项时返回 `PROJECT_STATE_TRANSITION_NOT_ALLOWED`，不猜测目标状态。

需求字段在项目创建时已经完整的项目，可以由“生成 Creative Brief”命令从 `draft/collecting_requirements` 进入 `plan_review`；该命令仍先执行需求完整性和 pending Decision 守卫。最后一个 Decision 解决后，决策服务显式判断活动需求是否完整，再选择 `planning` 或 `collecting_requirements` 触发器，转移器本身不读取需求内容。

## 5. 旧本地合同验证隔离

首期脚手架保留的 `/confirm -> /queue -> local contract_validation` 不属于正式生产 DAG。它使用独立触发器：

```text
legacy_contract_confirmed
legacy_validation_queued
legacy_validation_completed
```

这些状态仍为 `confirmed/queued/review_required`，只服务已有本地合同测试，不参与正式生产路由、供应商调用或费用。新业务代码不得复用这些触发器。

## 6. 阻断合同

`block_project` 必须收到：

- `reason_code`
- `responsible_aggregate_type`
- `responsible_aggregate_id`
- actor 类型与 ID
- 当前真正存在的 `allowed_commands[]`

首次阻断执行原子状态转移并产生 `project.blocked.v1`。Worker 优先冻结真实失败 WorkItem 与 WorkAttempt 错误码；质量和交付冻结 Asset 或 DeliveryAttempt。

项目已经 blocked 时，新阻断只追加 `project.block_diagnostic.v1`，不覆盖首次：

- `blocked_from_state`
- `state_reason_code`
- 责任聚合
- `blocked_at`

`completed/cancelled` 不能进入 blocked。当前没有合法解除阻断命令，因此 `blocked_allowed_commands` 可以为空，系统不会显示并不存在的“继续”操作。

## 7. 事务与事件

实际状态变化产生 `project.state_changed.v1`；阻断产生 `project.blocked.v1`。事件包含：

```text
from_state
to_state
trigger
actor_type / actor_id
row_version
关联业务记录 ID
```

转移器不 commit。业务记录、命令回执、领域事件、项目状态和项目状态事件由现有应用服务一次 commit；任一步骤异常时整体 rollback。

相同状态的合法触发器是无状态变化操作，不增加 `row_version`，也不额外制造 `project.state_changed.v1`；对应业务命令仍保存自己的领域事件。

## 8. 控制台

项目控制台新增只读状态证据：

- 状态行版本。
- 状态触发器、操作者与时间。
- blocked 原状态、原因码和责任聚合。
- 当前明确允许的命令集合。

控制台仍同时展示 `persisted_status` 与纯函数 `evaluated_stage`。页面不能写回状态，也没有新增解除阻断或重试按钮。

## 9. 明确未实现

当前没有实现：

- `ResolveBlock`
- `CancelProject`
- `StartNewPlanVersion`
- `ConfirmSelectedRetry`
- 事件信封、Outbox 和异步发布器
- Provider、OSS、FFmpeg 或模型调用
- 自动重试、自动失效、自动恢复、路由/工作流替换

以上命令会改变恢复、费用或执行语义，必须分别设计和确认，不能由状态转移器根据只读评估结果自动创建。

## 10. 验证

自动化测试覆盖：

- 从需求到最终交付的完整显式状态链。
- 合法同状态触发器不增加版本或状态事件。
- 非法跳转不写状态、不写事件。
- stale `row_version` 无法覆盖已提交转移。
- pending Decision 阻止方案生成，解决后才可继续。
- 首次阻断字段完整，后续诊断不覆盖首次来源。
- 终态项目不能被迟到失败改为 blocked。
- 未连接 Provider 冻结精确 WorkItem 与真实错误码，且不创建第二尝试。
- 控制台返回状态版本、触发器和结构化阻断责任。
- 空数据库完整升级到 `20260716_14 (head)`。

源码审计要求 `Project.status =` 不得出现在应用服务或 Worker；唯一状态写入位于 SQLAlchemy ProjectStateRepository 的条件更新中。
