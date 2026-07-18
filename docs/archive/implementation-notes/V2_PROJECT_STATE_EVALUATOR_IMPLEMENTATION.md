# 片场 V2 项目状态评估器实现

> 实现版本：Sprint 30
>
> 状态：只读评估器已实现；权威状态转移器见独立实现文档
>
> 实现目录：`v2/backend/app/orchestration/project_state.py`

## 1. 目标

项目状态评估器把项目控制台原有的阶段优先级与唯一下一步规则，从数据库投影服务中抽成一个不依赖 ORM、Session、Repository 或网络的纯函数模块。

本次解决的是“相同持久事实必须得到相同展示阶段与下一步”，不是“自动修改 Project.status”。

## 2. 输入合同

`ProjectStateFacts` 只接受调用方已经精确读取的持久事实：

- Project ID 与当前 `persisted_status`。
- 是否存在最终交付 Asset，以及最新 DeliveryAttempt 状态。
- 最新 Timeline 状态。
- 精确活动快照与最新快照的状态、费用状态。
- 是否存在活动 PlanVersion 与规划候选。
- 当前权威快照内 WorkItem 和 Asset 的状态计数。

评估器不读取数据库，不消费事件，不解析错误文本，不检查员工输出，也不从标题、提示词、文件名或状态文案推断事实。

活动快照存在时永远优先于最新历史快照。只有没有活动快照时，控制台才使用最新快照作为准备阶段的可见事实；评估器不会把历史快照设为活动快照。

## 3. 输出合同

`ProjectStateEvaluation` 返回：

```text
stage
stage_label
next_action
```

`next_action` 包含明确的 `code`、标签与页面路径；只有动作本身需要时才包含：

- `confirmation_level`
- `incurs_production_cost`

它是页面导航建议，不是命令。调用方不能因为读取到下一步就执行、排队、重试、扣费或写事件。

## 4. 阶段优先级

评估器按已经成立的后续事实从后向前判断：

```text
completed
delivery
editing
quality_review
production
production_preparation
planning
requirements
```

关键规则：

1. 最终交付 Asset 或已验证 DeliveryAttempt 才能投影为 `completed`；单独的 `Project.status=completed` 不替代交付证据。
2. DeliveryAttempt、已确认/导出的 Timeline 或 `delivery_ready` 投影为交付阶段。
3. Timeline 或 `editing` 状态投影为剪辑阶段。
4. 活动权威快照的 `execution_completed` 投影为质量阶段。
5. 权威快照的 `submitted/execution_blocked` 投影为生产阶段。
6. 权威快照或活动方案存在时投影为生产准备。
7. 只有规划候选时投影为规划，否则为需求阶段。

历史 blocked 工作项不会遮盖已验证交付；最新历史快照也不会覆盖一个精确活动快照。

## 5. 下一步规则

下一步只依据结构化事实：

- 生产阻断数量来自 `WorkItem.status=blocked` 计数。
- 素材动作按 `created -> verified -> review_required` 的确定顺序展示。
- 快照准备动作区分未分析、费用未配置、费用待确认、已锁定和已激活。
- 提交生产明确标记高风险确认与可能产生生产费用。
- 时间线和交付动作只根据各自持久状态选择。

评估器不根据错误码特殊字样切换动作，不生成“继续执行”之类没有对应命令的按钮，也不猜测缺失步骤。

## 6. 控制台接入

`ControlRepository` 和控制台服务仍负责精确收集项目、方案、快照、工作项、素材、时间线与交付记录。服务将这些记录转换为 `ProjectStateFacts`，再调用 `evaluate_project_state`。

现有 API 保持兼容：

```text
GET /api/v1/project-controls
GET /api/v1/projects/{project_id}/control-center
```

响应继续同时显示 `persisted_status` 与 `evaluated_stage`。评估结果不写回数据库。

## 7. 明确未实现

本次没有实现：

- Project 权威状态转移器。
- `blocked_from_state`、解除阻断或取消命令。
- 根据评估结果自动更新 Project.status。
- 自动创建 WorkItem、WorkAttempt、Asset、Timeline 或 DeliveryAttempt。
- Provider、模型、OSS、FFmpeg 或费用调用。
- 自动重试、失效、路由替换、工作流替换、提示词重写或输出修复。
- 事件信封、Outbox 或异步发布器。

Sprint 31 已经在单独确认后实现权威状态转移器；评估器仍保持只读且不会调用转移器。详见 [V2 权威项目状态转移器实现](./V2_PROJECT_STATE_TRANSITION_IMPLEMENTATION.md)。

## 8. 验证

专用测试覆盖：

- 八个展示阶段和后续事实优先级。
- 活动快照覆盖最新历史快照。
- 生产准备各费用/快照状态的下一步。
- 生产阻断、素材 QC、时间线和交付的下一步。
- 高风险确认与生产费用标记。
- 重复输入得到相同输出，输入计数字典不被修改。
- 现有项目控制台与最终交付集成测试保持通过。

完整后端测试、Python 编译、前端生产构建、运行时健康检查和数据库迁移状态应在每次提交前继续验证。
