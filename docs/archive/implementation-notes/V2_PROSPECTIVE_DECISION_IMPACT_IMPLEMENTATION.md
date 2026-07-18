# 片场 V2 前瞻决策变更影响分析实现

> 实现日期：2026-07-16
>
> 范围：为已解决 Decision 的提议值生成不可变分析报告；不应用变更，不创建重做、重试或生产任务。

## 1. 三类影响概念

V2 明确区分：

1. `DecisionImpactGraph`：历史已观测传播证据，回答决策实际进入过哪些合同记录。
2. `DecisionChangeImpactAnalysis`：对一个提议值生成候选影响范围、潜在工作量和费用证据。
3. `ProductionImpactAnalysis`：为一个已确认方案和明确生产配置编译新快照合同与预计费用。

三者不能互相代替。决策变更分析不是生产合同，也不具有失效、重做或放行权限。

## 2. 持久化模型

`DecisionChangeImpactAnalysis` 冻结：

```text
project_id / decision_id
current_value / proposed_value
status: completed | insufficient_evidence
scope: observed_lineage_with_active_cost
observed_manifest_ids[]
target_counts{}
estimated_work_count
cost_status / estimated_cost / currency
active_snapshot_id nullable
analysis_hash
created_by / created_at
```

`DecisionChangeImpactTarget` 对每个精确目标保存：

```text
record_type / record_id / label
record_status / authority
impact_kind: review_candidate
reason_code: OBSERVED_DECISION_LINEAGE
included_in_estimate
estimated_work_units
estimated_cost / currency nullable
evidence{}
```

Target 使用持久化 ID，不保存名称选择器、通配符或自然语言匹配结果。

## 3. 精确范围

分析沿现有已观测图的精确关系读取：

```text
Decision -> AgentInputManifest -> AgentRun -> Candidate
-> Requirement/Plan/Shot -> EntityVersion/Entity
-> Snapshot -> DAGNode -> WorkItem/Asset -> Timeline
```

实体关系只来自 Creative Brief、ShotPlanCandidate 和 Shot 中已有的实体版本 ID。系统不按决策键、标签、值、人物名或提示词推断实体影响。

未被任何 Manifest 冻结的决策仍可保存报告，但状态为 `insufficient_evidence`，目标为空。未观测不能被解释为无影响。

## 4. 工作量与费用

费用估算仅使用：

- `Project.active_snapshot_id` 指向的当前活动快照。
- 该快照内可达且绑定工作流槽位的 DAGNode。
- DAGNode 已冻结的 `estimated_cost` 和 `currency`。

规则：

- 每个可计价 DAGNode 计一个潜在工作单元。
- 所有节点有同一币种价格时为 `estimated` 并精确求和到六位小数。
- 任一节点缺价时为 `not_configured`，总价保持 `null`。
- 多币种时为 `mixed_currency`，不进行换算。
- 没有活动可计价 DAGNode 时为 `not_applicable`，不把未知成本写成零。

分析不会写 `CostEvent`。显示的金额只是当前冻结 DAG 按原价格重新执行时的候选成本证据，不是报价、确认金额或扣费记录。

## 5. API 与幂等

```text
POST /api/v1/projects/{project_id}/decisions/{decision_id}/change-impact-analyses
GET  /api/v1/projects/{project_id}/decision-change-impact-analyses
```

POST 必须携带 `command_id`、`actor_id` 和 `proposed_value`。同一命令重放返回同一报告；命令 ID 用于其他操作时明确冲突。提议值与当前值规范化后相同则返回 `DECISION_VALUE_UNCHANGED`。

报告、目标、命令回执和 `decision.change_impact_analyzed.v1` 事件在同一事务提交。

## 6. 页面

决策影响页新增独立分析区域：

- 展示当前决策值。
- 按当前值类型提供字符串、数字、布尔或 JSON 提议输入。
- 展示最新报告的目标数、潜在工作单元和活动快照费用证据。
- 按实体、镜头、快照、DAG、素材和时间线分组展示精确目标。
- 显示历史报告数量和不可变分析哈希。

页面没有“应用变更”“使素材失效”“重做”“重试”或“替换路由”按钮。

## 7. 明确禁止

本实现不执行：

- 修改或创建 Decision 版本
- 修改项目状态、活动方案或活动快照
- 标记实体、素材、时间线或工作项失效
- 创建 WorkItem、WorkAttempt、CostEvent 或供应商调用
- 自动重做、重试、降级、路由替换或工作流替换
- 提示词重写、ID 猜测、名称匹配或影响范围补全

未来若要应用变更，必须另行设计命令、版本语义、费用确认和用户选择范围，并再次取得确认。

## 8. 验收证据

- 已观测决策报告保存精确实体、镜头、快照、DAG、素材和时间线目标。
- 未观测决策保存 `insufficient_evidence` 空报告。
- 活动快照六个可计价节点产生六个工作单元，费用等于已冻结节点金额之和。
- 缺价、多币种和无活动工作分别明确分类，不进行猜测或汇率换算。
- 幂等重放不产生第二份报告或事件。
- 分析前后 Decision、Project、CostEvent 和 WorkItem 不变。
- Repository 项目隔离、API、迁移、前端构建和桌面/手机页面检查通过。
