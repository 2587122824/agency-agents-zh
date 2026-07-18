# 片场 V2 系统架构

> 更新日期：2026-07-18
>
> 本文是 V2 当前架构入口。详细历史表结构、状态矩阵和迭代证据保留在归档资料中。

## 1. 架构目标

片场 V2 是合同驱动、状态可审计的 AI 视频生产系统。模型负责理解与提出候选，后端负责验证、持久化、状态转移和执行，用户负责高风险确认与付费授权。

系统必须满足：

- 数据库中的不可变版本、命令回执和事件是权威事实。
- 智能体输出只能形成候选，不能直接改变正式版本或生产状态。
- 生产输入在执行前冻结为快照，Worker 不重新解释上游自然语言。
- 依赖、路由、工作流、费用和素材引用必须使用精确 ID。
- 失败明确记录，不自动重试、替换、修复、降级或猜测。

## 2. 运行分层

```text
React + TypeScript 前端
        ↓ HTTP / SSE
FastAPI 应用服务
        ↓
合同校验 + 状态转移 + Repository
        ↓
SQLAlchemy / SQLite / Alembic
        ↓
持久化 WorkItem 队列
        ↓
独立 Worker + Provider Adapter
```

### 2.1 前端

- 展示后端投影和允许执行的明确命令。
- 不从文案、错误字符串或本地动画推断项目状态。
- 不保存密钥，不直接调用生产 Provider。

### 2.2 应用服务

- 解析类型化 API 合同。
- 校验版本、项目归属、命令幂等和状态守卫。
- 在同一事务写业务事实、事件和 Outbox。
- 通过 Repository 协议访问持久层，不直接散落 ORM 查询。

### 2.3 Worker

- 只领取已持久化且依赖满足的 `WorkItem`。
- 使用冻结的 `WorkRequest`、Provider、工作流和输入资产。
- 提交成功后持久化精确 Provider 任务 ID，再轮询该任务。
- 未知提交结果进入明确阻断，不重新提交。

## 3. 核心模块

| 模块 | 责任 |
|---|---|
| Creation | 对话、附件、需求候选、Decision 和需求版本 |
| Planning | Creative Brief、分镜候选、分镜修订和方案版本 |
| Production | 快照、DAG、费用影响、执行授权和 WorkItem |
| Provider | 凭据解析、适配器注册、请求提交、轮询和输出清单 |
| Quality | 文件验证、QC 报告、人工批准与拒绝 |
| Editor | 时间线候选、修订、验证和确认 |
| Delivery | 最终文件授权、上传、验证和交付完成 |
| Control | 项目阶段、阻断、费用、事件和下一步只读投影 |
| Registry | 人物、服装、场景、产品、声音和附件版本注册表 |

## 4. 权威数据模型

### 4.1 项目与版本

```text
Project
├── RequirementVersion
├── Decision / DecisionVersion
├── PlanVersion / Shot
├── ProductionSnapshot
├── Timeline
└── DeliveryAttempt
```

- 正式版本不可原地修改；修改创建新版本或新候选。
- `Project.active_snapshot_id` 是生产与素材视图的唯一活动快照。
- 资产、执行和交付必须记录来源版本或快照。

### 4.2 智能体与候选

```text
Message
ConversationSession
AgentInputManifest
AgentRun
CreativeTurnProposal / CreativeSuggestionSelection
RequirementCandidate
CreativeBriefCandidate
ShotPlanCandidate
QCReportCandidate
TimelineCandidate
```

- 每次智能体运行绑定不可变输入清单和配置版本。
- 创作制片人只读取当前活动 ConversationSession；开启新会话不修改已确认 RequirementVersion。
- 建议与用户选择分别持久化，点击建议只能形成待确认候选。
- 模型成功只表示调用结束，不代表候选已确认。
- 助手回复、结构化候选、Provider 请求 ID 和 token 用量分别审计。

### 4.3 生产与素材

```text
ProductionSnapshot
├── DAGNode / DependencyEdge
├── WorkItem / WorkAttempt
├── CostEvent
└── Asset / QCReport / QCFinding
```

- DAG 节点和依赖是执行顺序权威，不按名称或创建时间推断。
- WorkAttempt 冻结实际 Provider、工作流、参数、输入和请求指纹。
- Asset 必须经过真实文件验证和质量审核后才能进入剪辑。

## 5. 项目状态

主要生命周期：

```text
draft
→ collecting_requirements
→ decision_required / planning
→ plan_review
→ contract_ready
→ production_ready
→ producing
→ quality_review
→ editing
→ delivery_ready
→ completed
```

异常状态使用 `blocked`，并保留 `blocked_from_status`、结构化原因、责任聚合和原始证据。归档是独立列表元数据，不等于取消、失败或删除。

项目状态只能通过权威状态转移器修改。只读阶段评估器可以计算当前页面和下一步，但不能写入状态。

## 6. 命令、事件与幂等

- 每个写命令带 `command_id`、操作者和需要时的 `expected_row_version`。
- 同一作用域重复命令返回第一次持久结果，不重复执行。
- 每个业务事件使用版本化完整信封和项目内递增序号。
- 事件与 Outbox 在业务事务内同时写入。
- Outbox 发布失败保持待发布，不自动重试。
- SSE 使用项目内序号恢复，不根据 UI 本地状态补事件。

## 7. 创作与确认边界

创作流程：

```text
用户消息与附件
→ AgentInputManifest
→ 智能体候选
→ 用户审核或澄清
→ RequirementVersion
→ Brief / ShotPlan 候选
→ 用户确认
→ PlanVersion
```

自然语言不能直接创建 WorkItem。创作制片人、内容策划、分镜导演、质量审核智能体和剪辑助理的合同已经确认，详见 [创作中心设计](./V2_CREATION_CENTER_DESIGN.md#9-智能体编制与合同)；运行代码是否完成以 [实现状态](./V2_IMPLEMENTATION_STATUS.md) 为准。

创作制片人和内容策划使用不同的配置角色与输入边界：`creative` 可以读取当前活动会话，`planner` 只能读取已确认版本化事实。两者都使用显式已发布模型配置和不可变 `AgentInputManifest`，但不能互相替代或在缺少配置时复用对方模型。内容策划输出在持久化前执行 Schema 与跨字段合同验证，成功后仍只是人工审核候选。

## 8. 生产合同与 DAG

确认方案后，系统根据精确已发布配置生成生产影响分析。用户确认配置、工作量和费用后创建并锁定 `ProductionSnapshot`，再由单独命令激活和提交。

生产编译器是确定性应用服务，不是 Agent。它不能调用模型、读取对话补字段、解释 Agent 文案或选择替代工作流；所有输入必须来自已确认版本与已发布配置。

普通首帧视频必须恰好消费一个父图片输出。多个图片输入必须由分镜或明确多帧工作流表达，不能静默取第一张。音频关闭时，快照和 DAG 不创建 TTS 依赖。

## 9. Provider 边界

- Provider 由已发布配置中的 `adapter_kind + work_kind` 精确解析。
- 凭据只支持后端 `env://NAME` 引用，变量名还必须进入白名单。
- API、事件、数据库投影和前端不返回密钥值。
- RunningHub 只接受严格 NodeInfoList 与声明的结构化来源。
- Provider 或配置不兼容时明确阻断，不切换供应商或工作流。
- 外部生产执行由独立环境授权控制，默认关闭。

## 10. 质量、剪辑与交付

- 文件缺失、损坏、哈希或 MIME 不符属于确定性阻断。
- 视觉内容、身份、文字和动态问题可以进入 `review_required`。
- QC 不通过不会自动发起付费重试。
- 时间线只能引用活动快照内已批准素材和精确素材 ID。
- 最终交付必须验证 MP4 文件、尺寸、时长、哈希、时间线和快照一致性。
- 内容质量模型只产生 `QCReportCandidate`；确定性文件阻断和人工批准仍由质量服务负责。
- 剪辑模型只产生 `TimelineCandidate`；素材缺口必须显式列出，不能自动复用、补帧或插入替代素材。

## 11. 当前技术栈

| 层 | 技术 |
|---|---|
| 前端 | React、TypeScript、Vite、React Query、Zustand |
| API | FastAPI、Pydantic |
| 数据 | SQLAlchemy、SQLite、Alembic |
| 任务 | 数据库队列、独立 Python Worker |
| 实时 | 持久化事件、SSE、Transactional Outbox |
| Provider | 版本化配置、Adapter、后端环境凭据 |

SQLite 目前适用于本地单用户阶段。Repository 已隔离持久层，后续多用户或高并发阶段可以评审 PostgreSQL 与外部队列，但当前不做隐藏替换。

## 12. 安全与禁止行为

- 不记录、提交或返回密钥原文。
- 不执行未确认的付费调用。
- 不从提示词或错误文本决定生产路由。
- 不自动补字段、改 ID、替换模型、替换工作流或寻找替代素材。
- 不把模型输出直接视为用户确认。
- 新增任何兜底、重试、恢复或降级前必须先获得用户确认。

## 13. 详细归档

以下资料保留更细的历史设计和实现证据：

- [数据模型详细设计](./archive/reference/V2_DATA_MODEL_DESIGN.md)
- [状态机与事件详细设计](./archive/reference/V2_STATE_MACHINE_EVENT_SYSTEM.md)
- [旧生产架构说明](./archive/reference/PRODUCTION_ARCHITECTURE.md)
- [历史实现记录](./archive/implementation-notes/)
- [历史与待确认提案](./archive/proposals/)

归档资料出现冲突时，以本文、[产品设计](./V2_PRODUCT_DESIGN.md)、[实现状态](./V2_IMPLEMENTATION_STATUS.md) 和当前代码为准。
