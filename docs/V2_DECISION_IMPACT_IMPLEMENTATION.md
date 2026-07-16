# 片场 V2 决策影响证据图实现

版本：v0.1

状态：已实现已观测链路；前瞻影响估算未实现

实现目录：`v2/backend/app/impact/`、`v2/frontend/src/pages/DecisionImpactPage.tsx`

## 1. 目标

决策影响证据图回答“某个决策实际进入过哪些 Agent 输入，并沿持久化合同传播到了哪些记录”。首期只展示已观测事实，不声明未观测决策没有影响，也不执行失效、重做、重试或费用确认。

## 2. 输入冻结修复

`AgentInputManifest.decision_ids` 是决策传播的唯一直接起点。创建和规划 Agent 现在只读取同项目、`status=resolved` 的 Decision，并同时冻结：

```text
decision_ids[]
payload.confirmed_decisions[]:
  id, key, label, value, source
```

Pending Decision 不进入清单。历史清单的 `decision_ids=[]` 保持原样，不补写、不按键名建立关系。决策值进入规范化清单后参与 `input_hash`，但不改变候选确认边界。

## 3. 精确传播链

```mermaid
flowchart LR
    D["Decision"] --> M["AgentInputManifest"]
    M --> R["AgentRun"]
    R --> C["Candidate"]
    C --> V["Requirement / Plan Version"]
    V --> S["Shot / Snapshot"]
    S --> G["DAGNode / WorkItem / Asset"]
    S --> T["Timeline / TimelineItem"]
```

每条边只来自 JSON 中的精确 ID 或数据库外键。服务不会使用 Decision.key、label、value、错误文本、节点名称或自然语言内容推断影响。

## 4. 查询合同

```text
GET /api/v1/projects/{project_id}/decision-impact-graph
```

响应包含：

- `scope=observed_lineage`。
- 每个决策的 `observed / not_observed` 结论。
- 直接消费该决策的清单 ID。
- 下游节点 ID、按记录类型计数和活动记录计数。
- 去重后的节点与有向关系。
- 明确的证据边界说明。

查询不创建 CommandReceipt、ProjectEvent、WorkItem、CostEvent 或任何影响记录。

## 5. Repository 边界

只读 `ImpactRepository` 按项目读取 Decision、Manifest、AgentRun、候选、需求版本、方案、分镜、快照、DAG、工作项、素材和时间线。应用服务负责节点建模、精确边连接、可达性计算和摘要，不把图计算规则下沉到 SQL 查询。

## 6. 页面

前端路由：`/projects/{project_id}/decision-impact`。

页面左侧列出决策及已观测状态，右侧按记录类型展示选中决策的传播证据、关系名称、状态与活动权威标记。页面只有刷新和返回控制台，不提供失效、重做、重试或生产按钮。

## 7. 未实现边界

- 未持久化设计文档中的 `DecisionImpact` 前瞻分析记录。
- 未估算尚未发生的工作项、供应商调用次数或费用。
- 未实现已确认 Decision 的新版本、supersedes 链或变更确认命令。
- 未自动标记任何方案、快照、素材或时间线失效。

这些能力会改变决策版本、项目状态、付费范围或重做边界，实施前必须单独确认。

## 8. 验收

- 已解决决策精确写入创建与规划清单；Pending Decision 不写入。
- 同一决策可沿三个清单传播到需求版本、方案和分镜。
- 未消费决策返回 `not_observed` 且无下游节点。
- 跨项目记录不会进入图。
- 查询前后项目事件数量不变。
- 页面在桌面和手机宽度下无横向溢出或内容重叠。
- 不产生供应商调用、费用、重试、兜底、路由替换或状态转移。
