# 片场 V2 费用与事件审计账本实现

> 实现日期：2026-07-17

## 1. 范围

项目控制台新增独立只读“费用与事件”页面，用于核对完整项目事件信封、分币种费用汇总和逐笔费用事实。它不提供状态修复、事件补发、费用调整、退款、重试或供应商操作。

## 2. 事件分页合同

接口：

```text
GET /api/v1/projects/{project_id}/audit-ledger
    ?limit=1..100
    &before_sequence={project_sequence}
```

- 首次按 `project_sequence DESC` 返回最新事件。
- 下一页只查询 `project_sequence < before_sequence`。
- 服务读取 `limit + 1` 条判断 `has_more_events`，但只返回 `limit` 条。
- `next_before_sequence` 使用当前页最后一条项目序号。
- 不使用全局自增主键、时间戳或前端位置作为游标。

每条事件返回完整信封：稳定事件 ID、快照、类型、聚合对象、原因/关联 ID、操作者、Schema 版本、原始消息、结构化数据和时间。

## 3. 费用合同

账本返回两层事实：

- `cost_summaries`：按原币种和 `kind/status` 汇总已确认金额。
- `cost_events`：全部原始 CostEvent，按发生时间和 ID 确定性倒序。

逐节点预计费用保持逐笔记录。系统不把多条记录合成伪造事件，不跨币种换算，不把 `pending/disputed` 计入确认金额，也不从 Provider 文本估算费用。

## 4. 中文展示边界

当前阻断、最近事件和完整账本共用集中展示词典：

- 已知 `error_code` 显示中文问题名称和说明。
- 已知 `event_type` 显示中文事件名称和说明。
- 未知类型显示中性中文提示，不解析原始英文 message 猜测语义。
- 原始代码、类型、消息、ID 和结构化证据保留在默认收起的技术详情。

这只是展示翻译，不改变后端 code、事件合同、阻断分类、状态机或下一步。

## 5. 只读保证

- 查询通过 ControlRepository 读取既有 ProjectEvent 与 CostEvent。
- 查询不提交事务，不创建 CommandReceipt、ProjectEvent、CostEvent、WorkItem 或 WorkAttempt。
- 读取更早事件必须由用户点击，不自动轮询历史页。
- 页面没有补发、重试、退款、调整、恢复或取消按钮。

## 6. 验收

- 相邻事件页序号严格递减且事件 ID 不重复。
- 查询前后 ProjectEvent 与 CostEvent 数量完全一致。
- 控制台最近事件返回完整信封，不再被响应模型丢弃。
- 逐笔预计费用合计与对应币种汇总一致。
- 当前阻断和事件主要界面为中文，英文原始值只在技术详情。
- 桌面和 390px 窄屏无页面级横向溢出，展开长 ID 与 JSON 证据仍可阅读。
- 项目页头存在多个操作时在窄屏自动换行，不以扩展页面宽度容纳按钮。
