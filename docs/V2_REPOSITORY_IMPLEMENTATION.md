# 片场 V2 Repository 边界实现

> 实现版本：Sprint 14-27
>
> 目标：把持久化查询和 ORM 构造逐步移出应用服务，同时保持现有产品合同、事务边界和状态语义不变。

## 1. 当前实现范围

Repository 协议位于 `v2/backend/app/repositories/contracts.py`，SQLAlchemy 实现位于 `v2/backend/app/repositories/sqlalchemy.py`。

| Repository | 已覆盖行为 | 当前调用方 |
|---|---|---|
| `ProjectRepository` | 最近项目排序、按 ID 读取、工作区关系加载、项目和 WorkItem 持久化 | 项目应用服务 |
| `EventRepository` | 追加项目事件、按项目和序号游标读取 | 项目、决策应用服务与 SSE |
| `DecisionRepository` | 按项目/键查重、按项目/ID 读取、追加和刷新 | 决策账本服务 |
| `CommandRepository` | 按 `(project_id, command_id)` 精确读取、追加不可变回执 | 创作、规划、生产、质量、剪辑、交付服务 |
| `CreationRepository` | 对话、需求版本、需求候选、Agent 清单/运行、澄清、附件/绑定及创作时实体版本查询 | 创作中心服务 |
| `PlanningRepository` | Creative Brief、Shot Plan、PlanVersion、Shot、已确认实体引用和规划历史 | 规划中心服务 |
| `ProductionRepository` | 影响分析、快照、DAG、价格组件、费用记录、WorkItem/Attempt 编译和生产准备/执行投影 | 生产服务 |
| `QualityRepository` | Asset 登记、存储策略、QCReport/QCFinding、人工审核、DAG 下游影响和质量工作区投影 | 质量服务 |
| `EditorRepository` | Timeline/TimelineItem 版本链、素材验证读取、确认版本替代和剪辑素材箱投影 | 剪辑服务 |
| `DeliveryRepository` | 确认时间线、冻结输入素材、DeliveryAttempt、最终 Asset URI、交付 QC 和工作区投影 | 交付服务 |
| `WorkRepository` | 可领取候选、必需父依赖、快照工作状态、WorkAttempt、原子 claim 和执行权威读取 | Worker |
| `ConfigurationRepository` | 配置命令回执、语义版本号、组件和价格规则、引用、版本历史及草稿组件隔离删除 | 系统配置服务 |
| `RegistryRepository` | 全局实体/版本、来源附件、确认绑定、方案/分镜引用、快照引用及附件精确读取 | 实体资产库只读投影 |
| `ControlRepository` | 活动方案、权威快照、执行/素材/QC、费用、时间线、交付、事件和项目顺序 | 项目控制台只读投影 |
| `ContactSheetRepository` | 活动快照、DAG/依赖、素材、实际尝试路由、分镜与实体来源证据 | 素材联络表只读投影 |
| `ImpactRepository` | 决策、输入清单、Agent 运行、候选、版本、方案、生产与时间线传播记录 | 决策影响证据图 |

系统配置继续使用独立的全局 `ConfigurationCommandReceipt`，不属于项目级 `CommandRepository`，Repository 迁移没有合并或改变两者的幂等作用域。

## 2. 事务边界

- Repository 不调用 `commit` 或 `rollback`。
- 应用服务仍负责一个命令的完整事务，并在同一事务内写业务记录、命令回执和已有项目事件。
- `flush` 和 `refresh` 只在应用服务需要数据库生成 ID 或重新读取字段时显式调用。
- 当前没有引入 Unit of Work、Outbox 或后台事件发布器。

## 3. 幂等语义

`CommandRepository` 只保存和读取事实，不判断命令是否允许重放：

```text
(project_id, command_id)
  -> command_type
  -> result_type
  -> result_id
```

命令 ID 重用、结果类型验证和结果缺失错误仍由原应用服务处理。迁移没有统一错误文案，也没有扩大或缩小任何命令的可重放范围。

## 4. 决策账本语义

`DecisionRepository` 不决定项目是否可修改，也不覆盖历史值。应用服务继续负责：

- 只有 `draft` 项目可以增加或确认决策。
- 同一项目内决策键重复时明确失败。
- 已确认决策不能覆盖。
- 决策创建和确认事件与决策记录在同一事务提交。

## 5. 当前完成边界

当前 V2 应用服务、Worker 与业务只读投影均通过 typed Repository 接口访问数据库。直接 `select/get/execute` 仅存在于 `repositories/sqlalchemy.py` 的 SQLAlchemy 实现中。

这一完成状态只证明当前持久化访问边界已隔离，不表示已实现 Unit of Work、Outbox、PostgreSQL 适配、统一项目状态转移器或未来尚未设计的聚合。新增业务聚合时仍必须先定义 Repository 合同和排序/隔离测试，不得把 ORM 查询重新散落到应用服务。

## 6. 验证

Repository 合同测试覆盖：

- 项目更新时间排序和关系加载。
- 事件按项目隔离、游标递增和数量限制。
- 决策键及决策 ID 的项目隔离。
- 相同 command ID 在不同项目中的隔离，以及回执字段原样持久化。
- 创作聚合的活动版本、状态过滤、历史排序、项目隔离和精确 ID 读取。
- 规划聚合的候选状态过滤、历史倒序、方案版本递增、镜头顺序和活动实体引用。
- 生产聚合的快照编号、DAG/依赖顺序、项目历史、WorkItem/Attempt 顺序和精确组件读取。
- 质量聚合的输出索引、素材历史、QC 编号、finding/审核顺序、DAG 下游和项目隔离。
- 剪辑聚合的时间线版本、轨道条目顺序、确认版本、素材箱过滤和 DAG 节点映射。
- 交付聚合的确认范围、输入条目、尝试唯一性、最终 URI、QC 编号和工作区顺序。
- Worker 的候选排序、可用时间、必需父依赖、快照状态集合和乐观锁原子 claim。
- 配置聚合的全局回执、语义版本号、组件/价格排序、引用与版本历史，以及草稿组件删除的版本隔离。
- 实体资产库的全局稳定排序、精确附件读取、实体版本、方案/分镜和生产快照引用。
- 项目控制台的活动/最新权威读取、跨快照过滤、执行尝试、阻塞 QC、费用、时间线、交付和最近事件。
- 素材联络表的活动快照、节点/依赖/素材排序、实际尝试、项目/方案分镜与实体来源隔离。
- 决策影响图的项目隔离、清单冻结、候选/版本/生产/时间线精确传播读取和空集合行为。
- 现有 API 全量测试继续验证六个业务阶段的幂等重放和命令冲突行为。

本实现不包含数据库迁移、Provider 调用、重试、兜底、路由替换、提示词改写、状态转移或费用事件。
