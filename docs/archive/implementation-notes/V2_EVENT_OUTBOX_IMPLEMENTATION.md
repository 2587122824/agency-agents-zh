# 片场 V2 事件信封与 Outbox 实现

> 实施日期：2026-07-17

## 1. 目标

本阶段把已有 `ProjectEvent + SSE` 补成可审计的统一事件边界：业务事实、项目内顺序和待发布记录在同一数据库事务中写入。它不连接消息中间件，不调用供应商，也不提供自动重试。

## 2. 持久化合同

`ProjectEvent` 保留数据库全局自增主键，仅用于内部存储；对外权威身份和游标改为：

- `event_id`：不可变事件 ID。
- `project_sequence`：项目内严格递增序号，唯一约束为 `(project_id, project_sequence)`。
- `aggregate_type / aggregate_id`：事件所属聚合，由事件创建方明确填写。
- `snapshot_id`：生产事件的精确快照，可为空。
- `causation_id / correlation_id`：命令因果和流程关联；没有跨事件关联时 `correlation_id=event_id`。
- `actor_type / actor_id`：用户、Worker 或系统来源。
- `schema_version`：信封版本，当前为 `1`。
- `data`：事件类型自己的对象载荷，不允许非对象值。

事件类型必须符合 `<domain>.<fact>.v<major>`。新写入不再接受未版本化名称。

## 3. 项目内序号

`Project.event_sequence` 是独立分配器。`SqlAlchemyEventRepository.add()` 在当前事务内原子递增该值，并把返回值写入事件。SSE 的 `id`、`Last-Event-ID` 和 Repository 游标全部使用 `project_sequence`，不再把数据库全局主键当成项目业务顺序。

## 4. Transactional Outbox

每次 `EventRepository.add()` 同时创建唯一 `OutboxMessage`：

```text
event_id unique
project_id
topic = project.events
status = pending | published
available_at
published_at
```

业务事务回滚时，事件和 Outbox 一起回滚。迁移前的历史事件被回填为 `published`，避免上线后把旧事实误当作新通知再次发布。

`publish_pending_outbox(session, sink)` 只发布调用方明确请求的一批：

- Sink 成功后才把对应记录标记为 `published`。
- Sink 抛错时错误直接返回，记录保持 `pending`。
- 函数内部不等待、不循环、不创建重试计划，也不修改业务聚合。
- 远端已收到但本地尚未标记时可能重复投递，消费者必须按 `event_id` 幂等。

## 5. SSE 信封

SSE 与 Outbox Sink 共享 `event_envelope()`，包括事件 ID、类型、聚合、项目、快照、项目序号、因果、关联、Actor、发生时间、Schema 版本、载荷和人类可读消息。心跳不进入领域事件表。

当前不删除历史事件，因此不存在早于保留窗口的游标；未来引入事件保留策略时再实现 `resync_required`，不得提前伪造该状态。

## 6. 迁移

Alembic `20260717_16`：

- 为 Project 增加事件序号分配器。
- 为历史 ProjectEvent 回填稳定事件 ID、项目内序号和信封字段。
- 建立事件唯一约束和查询索引。
- 创建 Outbox 表，并把历史事件登记为已发布。

## 7. 测试边界

测试覆盖：

- 不同项目各自从序号 1 开始，同项目连续递增。
- 事件和 Outbox 同时创建且一一对应。
- 信封字段与 SSE/Outbox 发布内容一致。
- 未版本化事件名被拒绝。
- Sink 失败后记录保持待发布，且没有自动第二次调用。
- 成功发布后同一批再次执行不会重复发送。
- 旧数据库迁移后包含新字段与 Outbox 表。

## 8. 未包含能力

- RabbitMQ、Kafka、Redis Streams 或其他外部消息服务。
- 后台定时发布器和自动重试策略。
- Provider、OSS、FFmpeg 调用。
- 工作项重试、解除阻断或状态恢复命令。
- 事件保留、归档和游标过期策略。
