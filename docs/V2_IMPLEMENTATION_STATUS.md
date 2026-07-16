# 片场 V2 实现状态

> 更新日期：2026-07-16
>
> 本文只记录可以由代码、迁移、测试或可运行页面证明的状态。设计目标不等于已实现。

## 状态定义

- `已完成`：已有持久化合同、API/服务、测试，并在需要时已有可运行页面。
- `部分完成`：已有一部分能力，但尚未满足设计文档中的完整边界。
- `未开始`：没有找到对应实现，或当前明确保留为后续范围。
- `需确认`：实现会改变付费调用、重试、兜底、路由、确认边界或项目状态语义，必须先由用户确认。

## 基础架构

| 能力 | 状态 | 权威证据与缺口 |
|---|---|---|
| React + TypeScript + Vite | 已完成 | `v2/frontend`，生产构建持续通过 |
| FastAPI + Pydantic | 已完成 | `v2/backend/app/main.py`、`api/router.py` |
| SQLAlchemy + SQLite + Alembic | 已完成 | `db/`、`migrations/versions/20260716_12_*` |
| 数据库队列与独立 Worker | 已完成 | `WorkItem`、`WorkAttempt`、`workers/worker.py` |
| SSE 事件流与游标恢复 | 已完成 | `ProjectEvent`、`events/service.py`、`Last-Event-ID` |
| 项目状态机与统一评估器 | 部分完成 | `orchestration/project_state.py` 已提供无 ORM 的纯事实评估器并由控制台统一调用；状态写入仍分布在应用服务中，尚无权威转移器、blocked 恢复合同或迁移 |
| Repository 接口 | 已完成 | Project/Event/Decision/Command/Creation/Planning/Production/Quality/Editor/Delivery/Work/Configuration/Registry/Control/ContactSheet/Impact 均有协议、SQLAlchemy 实现和合同测试；当前应用服务、Worker 与业务投影已无直接 ORM 查询 |
| 事件信封与 Outbox | 部分完成 | 已有持久事件、项目序列和 SSE；尚无完整事件信封与 Outbox 发布边界 |

## 创作、合同与编排

| 能力 | 状态 | 权威证据与缺口 |
|---|---|---|
| 对话、需求版本、方案版本 | 已完成 | `Message`、`RequirementVersion`、`PlanVersion` 与创作/规划 API |
| 类型化实体与不可变版本 | 已完成 | `Entity`、`EntityVersion`、实体资产库页面 |
| 方案候选与显式确认 | 已完成 | Creative/Director AgentRun、候选接受命令、方案页 |
| 结构化分镜编辑 | 已完成 | 类型化逐镜头 patch、候选替代链、行版本冲突、方案页编辑器与版本历史；不开放自由 JSON 或提示词覆盖 |
| 决策影响分析 | 已完成 | 已观测传播图、持久化变更提案报告、精确目标、活动快照工作量与冻结价格汇总；按确认边界不提供应用变更、失效、重做或重试命令 |
| 生产快照、DAG 与依赖验证 | 已完成 | `ProductionSnapshot`、`DAGNode`、`DependencyEdge` 与确定性编译测试 |
| 工作流槽位注册表 | 已完成 | 版本化系统配置、WorkflowSlot、NodeInfoList 验证和设置页 |
| 调用与费用估算 | 已完成 | `ProductionImpactAnalysis`、版本化价格目录与精确金额确认 |
| 阶段确认门禁 | 已完成 | 需求、方案、快照、费用、执行、质量、时间线、交付均使用显式命令 |

## 执行、素材与交付

| 能力 | 状态 | 权威证据与缺口 |
|---|---|---|
| Worker 幂等、指纹与执行租约 | 已完成 | WorkItem/WorkAttempt 唯一约束、租约、依赖阻断测试 |
| 素材生命周期与确定性 QC | 已完成 | Asset/QCReport/QCFinding、文件探测、哈希与引用检查 |
| 人工审核 | 已完成 | 明确批准/拒绝命令、审核依据和审核页面 |
| 成本账本 | 已完成 | `CostEvent`、估算与实际费用分离 |
| 时间线合同与确认 | 已完成 | Timeline/TimelineItem、验证、版本修订、确认和剪辑页 |
| 最终交付验证 | 已完成 | DeliveryAttempt、外部上传、真实 MP4 验证与完成条件 |
| RunningHub/CosyVoice Provider | 未开始，需确认 | 真实调用会产生外部副作用和费用；当前只允许 mock 或明确阻断 |
| OSS 临时音频上传 | 未开始，需确认 | 涉及真实存储凭据、生命周期和外部网络调用 |
| FFmpeg 本地合成 | 未开始，需确认 | 会新增交付执行方式和失败/恢复语义 |
| 精确依赖重试 | 未开始，需确认 | 当前没有第二次尝试；重试范围、费用和确认合同尚未冻结 |

## 当前安全开发顺序

1. 只读项目状态评估器已经收敛；下一步需单独评审权威项目状态转移器与 Outbox，二者都不得在重构中顺带引入。
2. 若要把决策影响报告转成实际变更，必须先确认 Decision 版本链、选择范围、费用和状态转移语义。
3. Provider、OSS、FFmpeg 与重试保持冻结，直到用户明确授权对应边界。
