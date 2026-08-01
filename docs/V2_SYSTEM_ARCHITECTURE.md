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
- `/editor` 直接装载权威剪辑工作台；未携带项目参数时从项目投影选择首个具备剪辑资格的项目，顶部切换只修改 URL 项目上下文并重新读取对应投影。项目上下文变化时先清空内存编辑状态，待新投影按基线恢复，禁止跨项目残留。无 Timeline 时才进入 `/editor/setup` 创建或选择时间线；该准备页不是“旧剪辑页 → 新版原型”的必经跳板。
- 剪辑预览调度与 Timeline 合同分层：当前可见视频按解码帧驱动播放头，隐藏预载元素只为下一条已批准视频预热网络和解码状态；切换门禁隔离旧元素迟到事件。预载和逐帧回调均是瞬时 UI 状态，不进入 `EditorDraftSession`、Timeline 哈希、低清预览或 Delivery Manifest。

### 2.2 应用服务

- 解析类型化 API 合同。
- 校验版本、项目归属、命令幂等和状态守卫。
- 在同一事务写业务事实、事件和 Outbox。
- 通过 Repository 协议访问持久层，不直接散落 ORM 查询。

### 2.3 Worker

- 只领取已持久化且依赖满足的 `WorkItem`。
- 外部任务领取与冻结供应商 `max_concurrency` 校验在同一数据库原子更新中完成；同 `provider_key` 活跃容量已满时保持排队。
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

Planning 的单镜头编辑与多镜头人物绑定共用 `ShotContractPatch`，批量只是前端生成多条精确 `target_shot_code` Patch 的交互方式，不新增批量写库捷径。完整候选仍由 `validate_shots` 一次验证人物实体类型、主参考声明、人脸主体归属及身份一致性与参考图要求；`PlanningRepository` 必须同时提供 EntityVersion 与其 Entity 的明确查询，不能绕过实体类型校验或从附件名称推断人物。
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
├── AssetRevisionRequest
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
- RequirementCandidate 通过 `conversation_session_id` 和 `supersedes_candidate_id` 形成不可变草稿修订链；下一轮以最新草稿而非活动正式版本为基线。
- 建议与用户选择分别持久化，点击建议只形成下一版草稿修订，不触发项目状态迁移。
- 首次引导由持久化 system 初始化消息和 `turn_intent=initial_guidance` 标识，一次执行；失败保留证据且不自动重试。
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
- 制作快照合同去重只约束未结束生命周期：同项目、同 `contract_hash` 且状态不是 `superseded` 时拒绝创建；用户显式结束旧生产后，相同合同可创建新的快照 ID 和递增编号，旧任务与事件保持只读且不被复用。
- Provider 并发容量由 `in_progress WorkItem + current WorkAttempt.provider` 计算；领取条件原子限制活跃数小于冻结 `max_concurrency`，避免单 Worker 连续提交或多 Worker 竞争突破上限。
- Provider 返回成功结果后，Adapter 先按冻结输出合同选出目标媒体，明确的辅助文本、日志或其他非目标媒体只进入审计清单，不登记为 Asset。Worker 在提交完成态前原子登记并确定性验证全部目标输出；任一目标输出文件或清单无效时整项阻断，不留下部分登记。成功 Asset 为 `verified`，内容审核与批准仍由质量阶段负责。
- 单工作项精确重跑只创建新的 WorkAttempt，必须复用原请求清单与指纹并单独记账；旧尝试不可修改，结果未知的供应商提交不可重跑。
- Asset 必须经过真实文件验证和质量审核后才能进入剪辑。
- 质量审核的确定性检查、智能体候选与人工决定分别持久化。`QCReportCandidate` 不能改变素材批准状态；人工决定落账时才形成权威 `QCReport`。没有可用多模态模型时，`verified` 素材可以直接人工审核并形成 `human-review.v1` 报告，不创建伪造的智能体候选。当前多模态合同只授权图片理解，视频与音频保持显式人工审核。
- 批准撤销是独立幂等命令，不删除原报告或决定。只有活动快照内尚未放行下游、未被时间线引用的 `approved` 素材可以恢复为 `verified`；图片阶段放行和剪辑引用均读取结构化持久化事实，不从状态文案推断。
- `AssetRevisionRequest` 冻结成品反馈的素材、快照、方案、镜头和下游证据，并分别持久化用户选择的 `issue_scope`、结构化 `issue_code` 与可选 `rationale`。合同按范围白名单校验原因，只有 `other` 要求非空说明；服务不得从说明文本推断或修正范围和原因。分镜草稿、生产重做和时间线修订分别由各自服务处理，不共享隐式重试逻辑。分镜回改只接受活动方案的精确 `DAGNode.shot_id`，历史方案不做运行时映射；开放请求可由用户显式取消，候选链和请求转为 `cancelled`，证据继续保留。迁移 `20260724_33` 将已有开发记录显式标记为 `other` 并保留原说明，不增加运行时旧合同兼容分支。
- 应用启动不执行 `metadata.create_all`；数据库结构只由 Alembic 迁移推进。测试 fixture 可以显式创建一次性测试库，但不能影响运行库的迁移版本。

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
→ editing（用户基于已导出版本继续剪辑）
```

`completed` 表示已经存在通过验证的成片，不表示项目永久只读。用户从最新 `exported` Timeline 创建新候选时，旧 Timeline、DeliveryAttempt 和成片 Asset 保持不可变，项目回到 `editing`；日常调整写入独立可变 `EditorDraftSession`，不改变项目阶段。异常状态使用 `blocked`，并保留 `blocked_from_status`、结构化原因、责任聚合和原始证据。归档是独立列表元数据，不等于取消、失败或删除。

创作会话中的消息、建议选择和草稿修订均保持在 `collecting_requirements`；只有用户最终确认最新草稿并生成新的 `RequirementVersion` 时，`REQUIREMENT_CONFIRMED` 才允许项目进入 `planning`。项目已进入策划后，普通 `MESSAGE_ADDED` 不允许隐式回退。

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

自然语言不能直接创建 WorkItem。创作制片人、内容策划、分镜导演、制作规划、质量审核智能体和剪辑助理的合同已经确认，详见 [创作中心设计](./V2_CREATION_CENTER_DESIGN.md#9-智能体编制与合同)；运行代码是否完成以 [实现状态](./V2_IMPLEMENTATION_STATUS.md) 为准。

创作制片人和内容策划使用不同的配置角色与输入边界：`creative` 可以读取当前活动会话，`planner` 只能读取已确认版本化事实。两者都使用显式已发布模型配置和不可变 `AgentInputManifest`，但不能互相替代或在缺少配置时复用对方模型。内容策划输出在持久化前执行 Schema 与跨字段合同验证，成功后仍只是人工审核候选。

## 8. 生产合同与 DAG

确认方案后，系统根据精确已发布配置生成生产影响分析。用户确认配置、工作量和费用后创建并锁定 `ProductionSnapshot`，再由单独命令激活和提交。

制作规划智能体位于确认方案与影响分析之间，只在用户选定制作配置和画面规格后生成 `ProductionPlanCandidate`。用户可以逐镜头修改并确认候选；其输出不能直接进入 Worker。影响分析仍只读取用户最终提交的精确 `ShotWorkflowAssignment`，并重新执行全部确定性能力校验。

生产编译器是确定性应用服务，不是 Agent。它不能调用模型、读取对话补字段、解释 Agent 文案或选择替代工作流；所有输入必须来自已确认版本与已发布配置。

普通首帧视频必须恰好消费一个父图片输出。多个图片输入必须由分镜或明确多帧工作流表达，不能静默取第一张。音频关闭时，快照和 DAG 不创建 TTS 依赖。

## 9. Provider 边界

- Provider 由已发布配置中的 `adapter_kind + work_kind` 精确解析。
- 本地单用户阶段，Provider 的 API Key 使用已发布供应商版本中的普通 `api_key` 字段；配置详情 API 原样返回并供设置页回填，不使用环境变量回退。
- API Key 不进入 Git、错误消息或供应商失败证据；当前数据库明文边界不适用于多用户或公网部署。
- RunningHub V2 的 JSON 请求和媒体上传统一使用 `Authorization: Bearer <API_KEY>`；创建任务正文不包含 `apiKey`，避免把 V1 鉴权格式带入 V2 接口。
- RunningHub 只接受严格 NodeInfoList 与声明的结构化来源。
- RunningHub 成功响应按 `output_contract.media_type` 选择目标输出；`outputType` 或后缀已明确为其他媒体类型的结果写入 `ignored_outputs` 且不下载登记。目标结果下载后以字节签名确定真实 MIME，并核对供应商声明、URL 后缀、非通用响应 MIME 与冻结允许列表；任一矛盾都以带证据的确定性错误阻断。
- `max_concurrency` 是 Worker 的提交前调度约束，不是供应商拒绝后的重试次数；容量不足时任务保留 `queued`，不会调用 RunningHub。
- Provider 能力标签必须能指向该 Provider 官方 API 的明确请求字段、响应字段或独立端点；模型名称、宣传能力或“OpenAI-compatible”本身不能证明支持图片、音频或视频输入。
- Provider 或配置不兼容时明确阻断，不切换供应商或工作流。
- 外部生产执行由独立环境授权控制，默认关闭。

## 10. 质量、剪辑与交付

- 文件缺失、损坏、哈希或 MIME 不符属于确定性阻断。
- 视觉内容、身份、文字和动态问题可以进入 `review_required`。
- QC 不通过不会自动发起付费重试。
- 时间线只能引用活动快照内已批准素材和精确素材 ID。
- 最终交付必须验证 MP4 文件、尺寸、时长、哈希、时间线和快照一致性。
- 内容质量模型只产生 `QCReportCandidate`；确定性文件阻断和人工批准仍由质量服务负责。
- 质量审核投影只把已执行终态且没有媒体的节点列为输出缺口；`queued`、`waiting_phase` 和 `running` 不表示输出缺失。
- 剪辑模型只产生 `TimelineCandidate`；素材缺口必须显式列出，不能自动复用、补帧或插入替代素材。
- 编辑器边界预览是只读播放会话：从相邻主画面切点前后各 1 秒映射到已有媒体时钟，跨片段继续逐帧推进并在冻结结束点自动暂停，不写 `EditorDraftSession`。
- 成对柔和过渡仍是两个相邻 TimelineItem 上的 `transition_out / transition_in` 冻结值，由一次前端事务和一个撤销步骤修改；后端校验与 FFmpeg 继续只认识既有 `cut/fade`，不增加改变时长的隐式叠化合同。
- `EditorDraftSession` 请求把浏览器播放头规范化为整数毫秒。同一内容指纹失败后自动保存停止，只有用户显式重试或内容变化才可再次写入；API 的结构化校验错误向用户保留字段路径和消息，不触发客户端自动重试。
- EditorWorkspace 从活动 ProductionSnapshot 的 `plan_version_id` 读取权威 Shot 顺序，并通过 Asset → DAGNode → Shot 关系投影每个视频素材的 `shot_code / shot_sequence_number`。顺序检查只消费这些类型化字段，不解析标签或节点名；一键整理是可撤销草稿事务，未知补充素材保持槽位，声音/字幕保持原成片时点。
- 切点末帧/首帧对比使用两个独立静音视频元素分别定位 `source_out_ms - one_output_frame` 与 `source_in_ms`；元素只读、暂停、按需挂载，点击定格才调用统一时间线 seek。该预览不创建分析候选，也不声称已完成视觉连续性判断。
- 连续性提示只消费 EditorWorkspace 已投影的 Shot `continuity_group_id / continuity_relation` 与 Asset 映射。只有正式序号相邻的边界才能套用右镜相对前镜的关系合同；跳序、拆分或补充素材边界使用通用人工检查项。勾选状态只存在于当前 React 页面会话，不进入 `EditorDraftSession` 或 Timeline 合同，正式放行仍由预览复核的独立不可变事件承担。
- 边界滚动剪辑不引入新的服务端合同：前镜源/成片出点和后镜源/成片入点以同一有符号 delta 原子写入现有 TimelineItem v4 草稿，源区间与成片区间继续等长，时间线总时长不变。客户端用素材 duration、最短 200ms 和两侧 fade 时长推导可移动区间；一次 `commitItems` 形成一个撤销步骤，后端保存与不可变 Timeline 校验继续作为最终合同门禁。
- 切点预览会话在客户端保存窗口起点、终点、前镜条目和循环标记。逐帧媒体时钟到达预览终点时，单次模式停止；循环模式切回前镜并保持播放状态，继续复用下一片段预载与跨条目门禁。切换媒体元素导致的 `HTMLMediaElement.play()` AbortError 属于预期中断并被显式吞掉，其他播放拒绝仍转换为用户可读提示。窗口/循环不进入 Timeline 合同；播放头继续使用既有 EditorDraftSession 字段。

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
