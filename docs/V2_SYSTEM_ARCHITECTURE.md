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
- 结构编辑通过客户端 `reconcileStructuralTransitions(previousRows, nextRows)` 维护成对转场完整性：它用稳定 TimelineItem ID 比较编辑前后的有序相邻边界，只保留仍按原顺序相邻且两侧类型、时长一致的 fade；已断开边界的左侧 `transition_out` 与右侧 `transition_in` 在同一次 `commitItems` 中归零为 `cut:0`。删除、重排、Inspector 移动和正式分镜整理复用同一重建函数；分割则显式让新内部边界为双方 `cut:0`，仅保留原片段外侧转场。该规则不新增服务端字段、迁移或旧数据兼容分支，后端现有 Timeline v4 校验继续作为最终门禁。
- `EditorDraftSession` 请求把浏览器播放头规范化为整数毫秒。同一内容指纹失败后自动保存停止，只有用户显式重试或内容变化才可再次写入；API 的结构化校验错误向用户保留字段路径和消息，不触发客户端自动重试。
- 本地兜底草稿显式升级为 `editor-local-draft.v4` 并补齐 `playhead_ms`。客户端用同一个 `editorDraftFingerprint` 处理恢复选择与 900ms 自动保存：指纹只投影基线 Timeline ID/行版本、TimelineItem 稳定合同字段、取整播放头、磁吸和缩放，递归规范化对象键，因此服务端为展示补齐的 Asset 元数据与 JSON 键序不会制造差异。基线匹配且本地/远端语义相同时直接采用远端，并同时初始化最近成功与最近尝试指纹；只有语义更新的本地 v4 草稿才触发一次 PUT。旧 schema 不读取，不保留兼容分支。
- EditorWorkspace 从活动 ProductionSnapshot 的 `plan_version_id` 读取权威 Shot 顺序，并通过 Asset → DAGNode → Shot 关系投影每个视频素材的 `shot_code / shot_sequence_number`。顺序检查只消费这些类型化字段，不解析标签或节点名；一键整理是可撤销草稿事务，未知补充素材保持槽位，声音/字幕保持原成片时点。
- 切点末帧/首帧对比使用两个独立静音视频元素分别定位 `source_out_ms - one_output_frame` 与 `source_in_ms`；元素只读、暂停、按需挂载，点击定格才调用统一时间线 seek。该预览不创建分析候选，也不声称已完成视觉连续性判断。
- `BoundaryActionComparison` 是定格区的双路动态比较器。它用 `comparisonDurationMs=min(boundaryPreviewBeforeMs, boundaryPreviewAfterMs, left_available_tail, right_available_head)` 冻结共同窗口，两路共享页面级 `boundaryPreviewRate`，不循环、不拉伸、不独立补速。`requestAnimationFrame` 分别读取两个媒体元素的真实 `currentTime`；单路到达冻结终点就立即 pause 并精确 seek，双路都完成后才结束会话。主 `playing`、`items` 或定格模式变化会清理 `boundaryActionComparisonKey`，组件卸载再次暂停两路媒体。该比较器只写 React 页面状态，不进入 EditorDraftSession、Timeline、撤销历史、迁移或 FFmpeg 合同。
- A/B 像素证据复用 `analyzeBoundaryPixels`，由 `BoundaryPixelProbe` 为每个可见方案挂载一对独立静音离屏 video。A 时点为 `max(left.source_in, left.source_out-frameStepMs)` 与 `right.source_in`；B 时点为 `max(left.source_in+leftPhaseDelta, left.source_out+leftPhaseDelta+rollDelta-frameStepMs)` 与 `right.source_in+rightPhaseDelta+rollDelta`。probe 在时点变化时先清除 readiness、结果和错误，重新 seek；`seeked` 还要核对当前媒体时钟与目标相差不超过 20ms，避免旧定位事件提前放行，双方就绪后下一 RAF 才读取 `48×48` 双区 Canvas。没有 B 时不挂载 B probe；转场-only B 时时点自然与 A 相同，UI 只解释时间呈现变化。结果与媒体都随组件卸载清理，不进入 `items` 指纹、EditorDraftSession、history、Timeline、QC、API、迁移或 FFmpeg。
- `BoundaryPixelProbe.onAnalysis` 把每个可见方案的最新完整结果稳定上报给 `BoundaryActionComparison`；任何目标时点变化先上报 `null`，成功采样才上报新结果，失败继续保持空值，避免把旧帧指标与新时点混算。父组件只在 A/B 都非空时逐项计算 `round((B-A)*10)/10`，以有符号“个百分点”投影明暗、综合色彩和逐像素差值；本次来源由互斥的转场、滚动和左右相位页面态确定性生成。转场-only 使用相同帧自然得到三项 `0.0`。差值方向只描述变化幅度，不参与 low/medium/high 等级或采用决策，也不进入草稿、history、API 或任何持久化合同。
- `BoundaryMotionProbe` 只在 A/B 主证据区为每套可见方案挂载最多六路 1px 离屏静音媒体：前镜 `boundary-2*frameStepMs / boundary-frameStepMs / boundary` 与后镜 `boundary / boundary+frameStepMs / boundary+2*frameStepMs`。存在的 `seeked` 目标都通过 20ms 时钟核对；素材边缘缺少第三个真实帧时不挂载对应媒体、不把钳制后的重复帧伪造为零，并在结果中以 `null` 投影不可用步长。动态 required-ready bitmask 完成后，下一 RAF 用 `readFramePixels` 把每帧缩放到固定 48×48；`analyzeFrameMotion` 复用于四个连续步长，其中切点内侧两步继续产出全画面总量、固定 3×3 每格 16×16 的区域变化百分比与像素差加权重心/分散度，外侧两步补成前后各两点的节奏轨迹。组件投影总量、九格、重心及四点轨迹；A/B probe 通过 `onAnalysis` 回传同一完整对象，父级以两侧 Asset ID 和六个源时点拼成 source key 拒绝旧结果，再由 `boundaryMotionDeltas` 逐项计算 B−A 的三项总量、两套九格、两侧重心差和四点轨迹差；任一方案缺失同位置步长时差值为不可比。A 使用基础时点，B 同时消费左右相位与滚动 delta；转场-only 读取与 A 相同的帧，所有可比差值确定性为零。候选列表不挂载该 probe。全部 readiness、结果与错误只属于组件生命周期，不进入 `items` 指纹、EditorDraftSession、history、Timeline、QC、API、迁移或 FFmpeg。
- `BoundaryMotionAnalysis` 同时从前后各两点轨迹确定性派生三项节奏斜率：`left = left[1]-left[0]`、`right = right[1]-right[0]`、`gap = right-left`，统一一位小数；缺少任一点时对应值为 `null`。`boundaryMotionDeltas` 对 A/B 的三项斜率再次做同位有符号差，转场-only 因源帧相同自然为零。斜率只消费同一 probe 已完成结果，不触发新 seek、解码或 Canvas 遍历，也不进入 source key 以外的新状态机、持久化合同或自动判断逻辑。
- `BoundaryMotionAnalysis` 还保留前后镜各两步的 `MotionChangeCentroid | null`，并以 `MotionCentroidPath` 派生 `x_percentage_points / y_percentage_points / distance_percent`。X/Y 为第二步重心减第一步重心，距离为 `hypot(x,y)/sqrt(2)`；任一步重心为空则整条 path 为 `null`。`boundaryMotionDeltas` 仅在 A/B 同侧 path 都存在时计算三项一位小数差，因此 transition-only 自然全零，素材边缘或无变化不会被伪造为零。该派生发生在既有四次 `analyzeFrameMotion` 完成后，不增加媒体、seek、像素遍历、source-key 分支、持久化字段或 FFmpeg 合同；语义只限于像素变化区域的重心迁移。
- `MotionCentroidContinuity` 在同一分析对象内比较前后镜 path：`x/y/distance_gap = right-left`，`angle_degrees = acos(clamp(dot(left,right)/(|left||right|),-1,1))`，统一一位小数。任一 path 为空时 continuity 为 `null`；path 都存在但任一向量长度为零时仅 angle 为 `null`。`boundaryMotionDeltas` 再对 A/B 的三项 gap 与 nullable angle 做同位差，transition-only 因当前分析完全相同自然为四项零。该层只消费已派生 path，不增加 Canvas、媒体、source-key、evidence readiness 或持久化状态。
- `MotionCentroidContinuityDiagram` 是对 `left_rhythm_centroids / right_rhythm_centroids` 的纯 SVG 投影：四个 X/Y 百分比直接作为 `viewBox="0 0 100 100"` 坐标，前后镜各一条实线，前镜第二点到后镜第一点使用虚线。组件在任一点为 `null` 时返回空，不补点、不重复最后坐标；A/B 是否更新继续完全由外层 `BoundaryMotionProbe` 的六帧 source key 与 evidence readiness 决定。SVG 不创建媒体、Canvas、异步状态、缓存或持久化数据。
- 同步动作相位试调由 `BoundaryActionComparison` 内部的 `leftPhaseDeltaMs / rightPhaseDeltaMs` 保存。每侧 delta 的下限为 `-source_in_ms`，上限为 `asset_duration_ms - source_out_ms`；`phaseView='baseline'|'tuned'` 独立决定媒体当前消费零 delta 还是已保存 delta，A/B 切换只暂停并重新定位两路媒体、归零比较进度，不重置试调值。有效源入出点、RAF 终点和共同窗口只消费当前视图的 active delta；当前视图同时进入完成提示。顺序试播使用独立的 `sequenceLeftRef / sequenceRightRef` 预载同一对素材并叠放在单一舞台，`sequenceSideRef` 作为切换过程的即时权威；前镜窗口为 `max(active_source_in, active_source_out-beforeMs)..active_source_out`，后镜窗口为 `active_source_in..min(active_source_out, active_source_in+afterMs)`。RAF 先读取前镜真实媒体时钟，到达冻结源出点后暂停前镜、切换可见层并播放后镜，到达冻结源终点后精确暂停；不循环、不补速。同步播放启动时重置顺序会话，顺序播放启动时重置同步会话；相位调整、A/B 切换和清除同时暂停并归位两套会话。试调、A/B 切换、顺序试播、清除和媒体定位不进入 `items` 指纹、草稿自动保存或撤销历史；应用按钮还要求 `viewingTunedPhase=true`，避免从 A 视图提交不可见的保留值。单侧应用回调把合法 delta 交给既有 `slipBoundaryItem`；双方组合通过 `onApplyPhasePair(leftDeltaMs, rightDeltaMs)` 交给父组件 `slipBoundaryPair`，父组件逐侧按最新素材把手重新钳制，在同一次 `items.map + commitItems` 中只修改双方 `source_in_ms / source_out_ms`。两侧片段前后邻接 boundary key 的并集统一清除页面连续性检查，一次 `commitItems` 只压入一个 history snapshot，并以 `pendingBoundaryPreviewKey` 在新投影上启动真实顺序试听。条目更新后比较媒体随 `items` 变化清理；该链路不新增 API、TimelineItem、EditorDraftSession、迁移、FFmpeg 合同或旧版兼容逻辑。
- A→B 连续对照在同一组件内使用 `phaseSequenceCompareStage='idle'|'baseline'|'tuned'` 和同步 ref 作为两阶段会话权威。启动只冻结当前内存中的 delta，先令 `phaseView=baseline`；A 的后镜到达终点后，RAF 将阶段切为 `tuned` 并令 `phaseView=tuned`，依赖当前 active delta 重新计算的定位 effect 完成归位后再播放 B。`phaseSequenceStartTokenRef` 使迟到的 `play()` 拒绝或完成回调失效；停止、手动 A/B、相位修改/清除、同步播放、普通顺序播放和条目投影都同步递增 token、暂停两路媒体并把阶段恢复为 `idle`。B 完成后只结束会话并保留 `phaseView=tuned` 与 delta。阶段、进度和媒体 ref 都只属于 React 页面状态，不进入 EditorDraftSession、history、TimelineItem、API、迁移或 FFmpeg。
- A→B 的结论门禁由组件内 `phaseDecisionReady` 管理，只在 tuned 阶段后镜到达冻结终点时置为 true；新一轮连续对照、任一试调、清除或条目源窗变化会归零，普通 A/B 查看不会伪造完成状态。结论区的“保留 A”调用纯页面态 `resetPhase`；“采用 B”先要求 `viewingTunedPhase && !editLocked`，再按试调类型确定性分发到 `onApplyRoll`、`onApplyLeftPhase`、`onApplyRightPhase` 或 `onApplyPhasePair`。因此滚动、单侧和双侧都继续复用各自既有的一次 `commitItems` 路径，组件本身不创建第二份提交、history 或新合同。父级条目更新会卸载比较媒体并清除结论，已有自动复检继续消费写入后的权威条目。
- 滚动切位试调复用父级已按 `buildRolledBoundaryItems` 语义计算的 `rollMinimumDeltaMs / rollMaximumDeltaMs`，但 delta 仅保存在 `BoundaryActionComparison.rollTrialDeltaMs`。B 视图的有效媒体窗按 `left.source_out + rollDelta` 与 `right.source_in + rollDelta` 计算，A 视图消费零 delta；两路共同窗口、同步 RAF、顺序舞台和 A→B 阶段机因此无需复制媒体实现。`hasComparisonTrial` 统一驱动 B 与结论门禁，`hasPhaseTrial` 与 `rollTrialDeltaMs !== 0` 则互相禁用调整入口，避免组合两种编辑语义。采用滚动 B 时组件只调用 `onApplyRoll(delta)`，父级继续通过 `applyBoundaryRoll → rollBoundary → buildRolledBoundaryItems → commitItems` 重新钳制最新条目、写一次 history 并登记自动复检；保留 A、切换 A/B、同步/顺序播放和清除仍不触碰 `items` 指纹或 EditorDraftSession。
- 成对转场试用也只存在于 `BoundaryActionComparison.transitionTrial`，结构为 `cut|fade + durationMs`；A 直接读取左右条目的冻结 `transition_out / transition_in`，B 才临时以试用值覆盖两侧。组件按双方片段半长与 2000ms 上限禁用非法 fade 预设，不把钳制后的另一时长伪装成用户所选时长。顺序 RAF 根据当前 A/B 分别计算前镜剩余时间与后镜已播放时间，将 opacity 投影为淡出 `remaining / duration` 和淡入 `progress / duration`；两路仍按先后显隐，舞台背景为黑色，因此保持既有“淡出淡入而非交叉叠化”的 FFmpeg 语义。转场、滚动与相位由 `hasMotionTrial / transitionTrial` 双向互斥；结论采用转场 B 时只回调父级 `setBoundaryTransition`，继续由一次 `commitItems` 写入双方、清连续性并排队自动复检。边界主控不再用选择框直接提交成对转场，Inspector 单边入口继续承担显式修复不一致参数。该页面态不进入草稿、history、API 或迁移。
- 连续性提示只消费 EditorWorkspace 已投影的 Shot `continuity_group_id / continuity_relation` 与 Asset 映射。只有正式序号相邻的边界才能套用右镜相对前镜的关系合同；跳序、拆分或补充素材边界使用通用人工检查项。勾选状态只存在于当前 React 页面会话，不进入 `EditorDraftSession` 或 Timeline 合同，正式放行仍由预览复核的独立不可变事件承担。
- 边界滚动剪辑不引入新的服务端合同：前镜源/成片出点和后镜源/成片入点以同一有符号 delta 原子写入现有 TimelineItem v4 草稿，源区间与成片区间继续等长，时间线总时长不变。客户端 `buildRolledBoundaryItems(baseItems, left, right, delta)` 用素材 duration、最短 200ms 和两侧 fade 时长统一推导可移动区间与新条目，按钮、快捷键、动作帧带和时间线拖动均不得绕过它。指针会话冻结起始 `items`，`pointermove` 只投影计算结果和播放头，`pointerup` 有实际 delta 时才手工把冻结快照压入一次 history、清空 future、重置边界连续性检查并登记待自动试听 key；零位移、回到原位和 `pointercancel` 恢复冻结快照而不写历史。完成后后端保存与不可变 Timeline 校验继续作为最终合同门禁；不新增 EditorDraftSession、迁移或 FFmpeg 分支。
- 滚动剪辑双画面监看是纯客户端临时投影。`boundaryRollMonitor` 冻结当前左右 TimelineItem、源出点前一帧、源入点、delta 和 `active` 会话标记；`BoundaryRollTrimMonitor` 用两个独立静音 video 元素 seek 对应源时点，只读显示并不进入草稿指纹。把手 hover/focus 可以以 `active=false` 预览当前切点，不影响自动保存；真实 pointer down 切换为 `active=true`，move 每次从同一冻结 `items` 更新两路时点。本地草稿 effect 与 900ms 服务端草稿 effect 都在 `active=true` 时退出，防止长拖中间态落盘；up/cancel 清理监看后才分别保存最终事务或恢复原快照。会话还在 window 级监听 `Escape` 并复用 cancel 路径。该状态不扩展 TimelineItem、EditorDraftSession、迁移或 FFmpeg 合同。
- 源窗口滑移同样复用 TimelineItem v4 与 EditorDraftSession：客户端只给单个完整画面的 `source_in_ms / source_out_ms` 加同一个有符号 delta，时间线入出点和片段时长不变，并用 `0 <= source_in_ms < source_out_ms <= asset_duration_ms` 限定区间。一次 `commitItems` 原子进入本地撤销历史和项目草稿自动保存；提交成功后登记用户所在的稳定边界 key，等待 React 投影新 `items/mainBoundaries` 后复用 `previewBoundary` 启动当前非对称窗口、当前速率的单次试听。待预览 key 不进入草稿指纹，失败门禁不登记；撤销/重做与结构重置会清理它并门禁迟到媒体回调。不新增旧数据兼容分支，也不改变后端冻结与 FFmpeg 对源区间的既有解释。
- 衔接主卡的窄栏布局不参与编辑状态。`.boundaryControl` 与直接子项显式 `min-width:0`，正式滚动剪辑和无损 A/B 的滚动切位试调都用 `minmax(0,1fr)` 与中间时间码权重列分配五列现有宽度；源窗口滑移保持同一 DOM 与按钮顺序，仅用两行 CSS Grid 把时间码和四个按钮重新定位。该布局没有 resize observer、宽度 state、备用控件或运行时分支，事件处理、aria-label、门禁、页面试调与事务函数完全复用原路径。
- 片段滑动继续只写现有 TimelineItem v4：前镜 `source_out_ms / timeline_out_ms`、目标镜 `timeline_in_ms / timeline_out_ms` 与后镜 `source_in_ms / timeline_in_ms` 同加一个 delta，目标源区间、目标时长、时间线连续性和总时长均不变。客户端以相邻素材把手、最短片段和 fade 下限计算可用区间，一次 `commitItems` 原子保存三条修改；成功后 `pendingBoundaryReview={keys, scope:'slide'}` 暂存前后两个稳定边界 key，下一次投影把仍存在的 key 转换为索引并创建同 scope 的局部 `boundaryReviewSession`。局部会话复用连续巡检逐点暂停、选择、定位和媒体时钟，但使用独立进度/完成文案、`skippedCount=0`，只播放两个受影响切点；取消、撤销、重做或结构编辑会清理待启动与活动状态。后端既有源/成片等长、连续性、素材边界和转场校验仍是不可变版本门禁，不增加新的数据结构或运行时兼容逻辑。
- 画面裁切仍由 `buildTrimmedItems` 从冻结基线计算源区间并经 `normalizeMainTrack` 波纹对齐，不新增后端字段。`queueTrimBoundaryReview` 根据原有稳定顺序投影受影响边界：左侧裁切包含 `previous→item` 与 `item→next`，右侧裁切只包含 `item→next`；所有受影响 key 先清理页面级连续性勾选，只有两侧均有 `asset_id` 的边界进入媒体复检。单个可播放 key 复用 `pendingBoundaryPreviewKey`，两个 key 写入 `pendingBoundaryReview={keys, scope:'trim'}` 并由现有局部巡检状态机依次播放。键盘提交前与 pointer down 都会清除旧待预览/循环/巡检状态；pointer up 只有实际变化才入历史并排队复检，cancel 与无移动保持零历史、零复检。该页面状态不进入 EditorDraftSession 指纹、Timeline v4、迁移或 FFmpeg 合同。
- 转场调整同样复用稳定边界 key 和 `pendingBoundaryPreviewKey`。成对衔接选择在一次 `commitItems` 中写前镜 `transition_out` 与后镜 `transition_in`，随后清除该 key 的页面连续性勾选并排队单次试听；Inspector 单边调整先按当前主画面顺序把 `transition_in` 映射到 `previous→selected`、把 `transition_out` 映射到 `selected→next`，仅当两侧 `asset_id` 完整且成片边界相等时排队。实际合同未变化时直接返回，不制造历史或试听；每次有效提交前暂停旧媒体并清理循环、连续巡检和待启动会话。首尾外侧或空位边界只保留合同修改和明确提示，不伪造媒体复检。整个调度只改变 React 页面状态，继续使用 TimelineItem v4 的 `cut/fade`、既有草稿指纹、后端校验与 FFmpeg 执行，不新增迁移或兼容分支。
- 切点预览会话在客户端分别保存 `boundaryPreviewBeforeMs / boundaryPreviewAfterMs`，每侧值域均为 `250|500|1000|2000`，窗口起点和终点分别钳制到前镜入点与后镜出点。单次预览、循环预览、全时间线连续巡检以及滚动修剪/动作帧带应用后的自动试听共用这组页面状态；提示用专用 `previewSeconds` 最多保留两位小数，避免通用一位小数格式把 0.25 秒误报为 0.3 秒。会话以页面级 `boundaryPreviewRate=0.25|0.5|1` 驱动当前主视频及所有活动时间线音频的 `playbackRate`。逐帧媒体时钟继续从真实解码 `mediaTime` 推进 Timeline 播放头，因此慢速只拉长观察时间，不改变源/成片映射；跨镜重挂载时 `onLoadedMetadata` 再次写入同一速率。到达预览终点后单次模式停止；循环模式先显式 `pause`、清除终点并置 `playing=false`，再递增冻结会话的 `iteration`，由下一次 effect 重新选择冻结前镜、定位起点、恢复终点并播放，保证停止→重启状态边界，不能在同一 render 中保持 `playing=true` 直接回绕，否则媒体回调可能逃逸到后续缺口。退出预览时统一恢复 1×。切换媒体元素导致的 `HTMLMediaElement.play()` AbortError 属于预期中断并被显式吞掉，其他播放拒绝仍转换为用户可读提示。窗口、速度和循环不进入 Timeline 合同；播放头继续使用既有 EditorDraftSession 字段。
- 全时间线连续巡检在客户端保存 `boundaryIndexes / position / skippedCount`，其中索引只投影左右 `asset_id` 均存在的 `mainBoundaries`。会话 effect 只随会话位置、稳定条目集合或窗口配置启动一次单切点播放：同一批状态同时选择前镜、写窗口起止、清除循环并开始播放，不能依赖播放过程中必然变化的 `selectedItem`，否则跨镜选择会反复把当前切点重置到起点。逐帧时钟到达窗口终点后先暂停并关闭当前媒体会话，再推进 `position`；最后清理会话并报告播放/跳过数量。所有手动导航、普通播放和结构修改入口统一清理该页面状态。该状态不进入 Timeline 或 EditorDraftSession 合同、不写人工检查和撤销栈。
- 切点巡检序列由客户端从当前 `mainItems` 的稳定相邻对即时派生，`boundaryFocusKey=left.id-right.id` 只保存当前页面焦点，不进入草稿指纹。`focusBoundaryAt` 在同一状态事务中暂停旧视频、设置切片会话门禁、结束循环/定格/叠加，选择可播放的右条目并把播放头写到左条目的精确 `timeline_out_ms`；右条目为空位时选择左条目以保留巡检控件。选择右条目可让媒体从 `source_in_ms` 展示切后第一帧，避免前镜文件实际 duration 的小数帧值通过 `timeupdate` 覆盖整数毫秒边界。上一/下一按钮与 `[` / `]` 共用该函数，首尾只在客户端禁用，不新增 API、数据表、迁移或运行时兼容分支。
- 切点叠加对齐由两个独立静音 `<video>` 读取前镜 `source_out_ms - frame_ms` 与后镜 `source_in_ms`，在两路 `seeked` 后以同一 `object-fit: contain` 画布分层显示；后镜 opacity 由页面级 0–100% 滑杆实时控制。模式、透明度和媒体就绪状态不进入 `items` 指纹、EditorDraftSession 或 Timeline 合同，锁轨门禁只阻断写操作而不阻断该只读比较；服务端、数据库与 FFmpeg 无新增分支。
- `BoundaryFrameOverlay` 在两路 `seeked` 后使用离屏 `48×48` 双区 Canvas 读取同源视频像素。`analyzeBoundaryPixels` 分别计算 Rec.709 平均亮度差、平均 RGB 欧氏距离和逐像素 RGB 平均绝对差，归一化为一位小数百分比；`8/20%` 明暗、`10/20%` 色彩和 `25/55%` 逐像素阈值只生成 low/medium/high 展示等级。源时点或 ready 状态变化先清空旧值，下一帧重新采样；Canvas 不可用或像素读取失败只投影可读错误。结果不进入 `items`、EditorDraftSession、Timeline、QC、迁移或 FFmpeg，不用于自动判定连续性。
- 单侧邻帧扫描只扩展 `BoundaryActionComparison` 的客户端页面态。`phaseCandidateScanSide` 决定当前按需挂载前镜或后镜候选；候选集合固定从 `[-2,-1,1,2] * frameStepMs` 派生，再用该侧已有 `minimumPhaseMs / maximumPhaseMs` 过滤素材把手越界值。每个 `BoundaryPhaseCandidate` 复用 `BoundaryPixelProbe` 和 A 的 `BoundaryPixelAnalysis`，由共享 `boundaryPixelDeltas` 计算三项一位小数有符号百分点；组件不排序、不生成评分或推荐。选择候选时把目标侧设为对应 delta、另一侧归零，并继续消费既有同步/顺序/A→B/采用状态机；滚动或转场试调关闭扫描以保持互斥。候选媒体只在展开侧存在，收起即卸载，不写 `items`、EditorDraftSession、history、Timeline、QC、API、迁移或 FFmpeg。
- 候选一键连续对照使用 `pendingPhaseCandidateCompare={side,deltaMs}` 作为短暂等待门禁。B 像素回调不再只保存裸分析值，而是保存 `{sourceKey,analysis}`；`sourceKey` 由当前 B 末帧/首帧源毫秒组成，只有与本次 `tunedPixelSourceKey` 完全一致的结果才可用，避免切换候选后的上一组采样触发连播。effect 同时核对单侧 delta、`phaseView=tuned`、两路 sequence metadata ready、A/B 证据和非零顺序窗口后，才调用既有 `startPhaseSequenceComparison`。`cancelPhaseSequenceComparison` 统一清除 pending，因此扫描切侧、手动调整、其他播放或条目变化都会失效等待及迟到回调。该门禁只编排 React state/media ref，不写草稿、history 或任何服务端合同。
- 动作连续帧带复用 `BoundaryFrameStill`，按 `frame_ms=round(1000/output_fps)` 为前镜投影 `source_out - 3/2/1*frame_ms`，为后镜投影 `source_in + 0/1/2*frame_ms`，并分别钳制在冻结 `source_in` 与 `source_out-frame_ms` 之间。六个媒体元素仅在用户展开该边界并选择帧带模式时按需挂载，全部静音、暂停且在 `seeked` 后才标记可用；点击帧以 `timeline_in + source_time - source_in` 反算成片位置并调用统一 `seekTimeline`。可视化设切点不新增编辑器：前镜目标 delta 为 `selected_source + frame_ms - current_source_out`，后镜目标 delta 为 `selected_source - current_source_in`；UI 先用同一 `rollMinimumDelta / rollMaximumDelta` 决定动作是否可用，点击后仍调用权威 `rollBoundary` 二次钳制并通过 `commitItems` 写入单步历史。成功后只登记稳定边界 key，等 React 提交新 `items/mainBoundaries` 后再复用 `previewBoundary` 启动当前窗口与速度的单次预览，避免用旧条目计算切点；自动预览不进入历史，且不清理 `boundaryFrameComparisonKey / boundaryFrameStripKey`。撤销、重做、项目切换、丢弃草稿、导航与结构编辑会清理待预览 key；撤销/重做还同步暂停媒体、结束预览状态并以 `advancingPlaybackRef` 门禁迟到的解码帧回调。帧带模式属于客户端边界预览状态，不进入草稿指纹、服务端合同或 FFmpeg。
- 普通滚动按钮和动作帧带统一调用 `applyBoundaryRoll`：底层 `rollBoundary` 仍是唯一修改入口并以 boolean 表示是否实际提交，成功后才登记待预览边界 key。全局键盘层以 `event.code=Comma/Period` 避免 Shift 改写字符值，普通状态传入真实 `frameStepMs`，Shift 状态传入 `1000ms`；目标边界取 `mainBoundaries[activeBoundaryIndex]`，因此与 `[ / ]` 导航、卡片焦点和当前片段推断共享同一稳定选择语义。弹窗、文本/表单/可编辑元素以及 Alt/Ctrl/Meta 组合先行退出；锁轨与素材、最短时长、fade 边界仍由 `rollBoundary` 权威门禁，失败不登记自动预览、不改 items、不创建历史。该能力不增加 API、迁移、Timeline 字段或旧版兼容分支。

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

### 剪辑台 A/B 证据会话门禁

同步动作的 A/B 播放状态机以页面内 `sourceKey` 约束异步媒体与 Canvas 结果：像素 key 包含两侧 Asset ID 和切点帧时点，动作 key 包含两侧 Asset ID 与最多六个真实连续帧时点，顺序媒体 key 包含 Asset ID 与当前播放窗口起止点。探针回调只把分析登记到创建回调时冻结的 key；父组件只消费与当前 key 相等的 evidence。Asset 或时点变化会先把派生结果视为未就绪，即使旧 React state 尚未被异步清理也不能通过门禁。

`comparisonEvidenceReady` 要求当前试调存在且 A/B 像素、A/B 动作四套 evidence 同时匹配；手动与候选自动 A→B 都还要求两路顺序媒体已经 seek 到当前 source key。A/B 阶段切换导致窗口改变时，播放 effect 等待新一轮 `seeked`，不会沿用上一视图的 loaded 状态。决策不再是独立布尔值，而记录包含条目、素材、基础源窗、相位、滚动和转场参数的试调 key；只有该 key 仍等于当前试调且四套证据仍有效时才渲染结论。证据失效会停止活动对照，所有调整与清除路径清空结论和候选等待。该实现不进入 Timeline、EditorDraftSession、API、数据库迁移或 FFmpeg。

邻帧候选的已实测动作记忆使用 `EditorPrototypePage` 持有的 `Record<reviewSessionKey, BoundaryCandidateReviewSession>`；每个 session 内保存 `Record<exactCandidateSourceKey, BoundaryMotionAnalysis>`，只在当前试调为当前已挂载范围内合法的单侧 `±1..±4` 帧、没有滚动或转场试调，且当前 B 动作探针返回有效分析时登记。review session key 包含 project、双方 item/Asset、双方基础源入出点、frame step 与 fps；exact key 继续包含双方 item/Asset、双方基础源入出点、侧别、delta 与派生的六个动作源时点。`BoundaryActionComparison` 通过 props 精确读取当前 session，并用稳定父级回调按 session/source key 更新；观察模式切换可以卸载全部隐藏媒体而不丢失审核记忆，边界合同变化则自然读取新的空 session。该缓存不触发候选批量动作解码、不进入 query cache、localStorage、草稿指纹、后端合同或迁移，也不参与排序和决策。

边界观察模式工具栏直接对当前 `BoundaryCandidateReviewSession` 做只读聚合：`comparisonOutcomes` 分别统计 `completed / shortlisted / kept_baseline`，`measuredMotionEvidence` 中没有对应 outcome 的 key 统计为“已实测未对照”。任一计数非零才渲染摘要；非同步动作模式且仍有 `completed / shortlisted / measured-only` 时渲染“继续审核”，点击只把 overlay/strip key 清空并把 action comparison key 设为当前 boundary。该路径不触碰组件内部扫描侧、phase delta、pending、媒体或保存函数；session key 变化后聚合自然归零。所有计数均为 render-time 派生，不增加 React state、effect、缓存表或后端合同。

当 `boundaryFrameComparisonKey` 不等于当前 boundary、但同一聚合结果的 follow-up 为真时，边界主卡渲染收起态 reminder。其继续按钮一次设置 frame comparison key 和 action comparison key 为当前 boundary，并把 overlay/strip key 清空；React 随后挂载新的 `BoundaryActionComparison`，所有内部 state 仍使用默认空值，因此不会把 reminder 点击解释为选择候选或恢复播放。follow-up 只由 completed、shortlisted、measured-only 构成，kept-only 不渲染；reminder 不增加独立状态或第二套计数。

全时间线待办队列是 `mainBoundaries × boundaryCandidateReviewSessions` 的同步只读投影。页面为每个当前双方素材完整的 boundary 重新计算稳定 session key，只读取 exact-key 命中的 session，并统计 `completed / shortlisted / measured-only`；因此已删除边界、源窗或素材变化后的旧 map 项即使仍留在页面内存，也没有进入全局计数的路径。队列按 `mainBoundaries` 原顺序保存 boundary index；“下一个”先取活动 index 之后的首项，没有则循环到首项。待办定位使用独立的只读 focus 路径：暂停主媒体、门禁迟到回调、停止旧预览/巡检，更新 selected index 与 boundary focus，再设置 frame/action comparison key；它不调用会推进播放头的普通 `focusBoundaryAt`。组件重新挂载时扫描侧、B、pending 与播放为空，`playheadMs` 及草稿指纹保持不变。该投影不新增 state、effect、持久化、API、草稿字段或排序逻辑。

浏览器验收以普通切点导航完成后的 `row_version=236 / playhead_ms=5118 / PUT=1` 为前置快照；点击待办并等待 1.4 秒自动保存窗口后，三项值及 `updated_at` 完全不变。同步动作 region 为 1、扫描侧 pressed 为 0、B disabled、13 个媒体全部暂停，证明只读 focus 没有借由既有 autosave effect 产生隐式写入。

人工连续性全局进度同样从当前 `mainBoundaries` 同步派生，只纳入双方 Asset 都存在的边界。每个边界重新解析双方正式分镜序号；正式相邻时按右镜 `continuity_relation` 读取 `CONTINUITY_CHECKS`，否则读取 `GENERAL_CONTINUITY_CHECKS`。完成数只计算仍存在于当前 checks 数组的 ID，旧关系下的页面勾选不能抬高新关系进度。未完成队列保持 boundary index 顺序并循环选择下一个；定位复用 `focusBoundaryForReviewAt(..., 'frames')`，而候选待办复用同一函数的 `action` 模式。两者共享媒体停止和迟到回调门禁，但 frames 模式显式卸载同步动作媒体，且都不修改 `playheadMs` 或 autosave 指纹。

只读 focus 改变选中片段时，主监看会因 React key 变化重新挂载 video；其 metadata 定位可能派发 `timeupdate`。`preservePlayheadOnReviewFocusRef` 只为这一次 selected item 变化保留 `advancingPlaybackRef=true`，使新媒体定位回调不能把源时点反写为播放头；标记随后一次性消费。用户主动播放会显式解除门禁，之后正常选择其他片段也会由既有 selected-item effect 恢复常规回调，不能把只读门禁扩散为永久忽略媒体时钟。

修正后从 `playhead_ms=0` 重新加载真实页面并点击连续性待办，等待 1.4 秒后草稿的 row version、播放头与 updated_at 三项完全不变，重启后的 API 日志仍无 editor-draft PUT。DOM 同时证明 frames 已展开、并排模式 pressed、同步动作未挂载、所有媒体暂停；三项 checkbox 全部切换为 true 后只更新页面进度，后端草稿仍不变化。

定位到中间片段时，`clipSlide` 的五列控制使用 `minmax(0, fixed)` 与弹性时间码列共同收缩；容器、直接子项和标题子项显式允许收缩，时间码继续在格内 ellipsis。该布局只修正 Inspector 内部几何，不改变片段滑动事务、按钮门禁、自动复检或草稿合同。

候选卡的相对 A 影响不保存第二份派生状态：渲染时仅当 `baselineMotionAnalysis` 与 exact-key 命中的 `measuredMotionAnalysis` 同时存在，才复用 `boundaryMotionDeltas` 计算一位小数的幅度和接续几何差。夹角只在 A/B 两个原始夹角均可用时相减；轨迹缺失则整组接续影响不可比。该投影不增加 effect、媒体、Canvas 遍历、缓存 key、持久化或决策逻辑。

候选人工结果记忆使用同一父级 review session 内的 `Record<exactCandidateSourceKey, 'completed' | 'kept_baseline' | 'shortlisted'>`。A→B 状态机只有在 tuned 阶段播放到右侧冻结终点、当前四套 source-key 证据仍有效时，才把当前已挂载的合法单侧 `±1..±4` 帧候选登记为 `completed`；当前 exact trial 的结论门禁仍有效时，用户点击“保留 A”更新为 `kept_baseline`，点击“暂存 B 待复看”更新为 `shortlisted`，两者随后都复用 reset 清除试调且零草稿写入。短名单入口只在 `activePhaseCandidateSourceKey` 存在时渲染，滚动、转场和双侧相位没有写入路径。再次完整播放同一候选会用 `completed` 覆盖旧结果；停止、异常、只播放 A 或证据失效也没有写入路径。组件卸载时只清理播放、pending、B、扫描侧、结论门禁与媒体，不清理父级 session；基础边界依赖变化通过新的 review session key 隔离旧结果。该记录不复用 `phaseDecisionSourceKey` 充当持久事实，也不进入 Timeline、EditorDraftSession、history、API、localStorage、排序或采用逻辑。

候选审核进度是 `nearbyPhaseCandidates` 与 `candidateComparisonOutcomes` 的同步派生投影：reviewed 统计存在任一 outcome 的 exact key，kept、undecided、shortlisted 分别统计 `kept_baseline / completed / shortlisted`。`nextUnreviewed / nextUndecided / nextShortlisted` 都使用固定候选数组的第一个匹配 key，统一目标严格按未看、待决定、待复看的顺序取第一个。按钮据此显示“对照下一个未看候选”“复看下一个待决定”或“复看下一个待复看”，并直接调用既有 `selectPhaseCandidate(side, delta, true)`，因此继续受证据、媒体和迟到回调门禁约束；`hasComparisonTrial` 为真时禁用，不能越过当前活动 B。该投影保证组件卸载后仍保留的 `completed` 不会被 reviewed 计数吞掉并形成无统一恢复入口；重新对照会再次经过当前 source-key 证据和明确结论。派生目标与计数不增加 state、effect、媒体、缓存键、排序或持久化。

候选范围扩展只增加一个组件内布尔页面态。默认偏移数组为 `[-2,-1,1,2]`；用户显式扩展后切换为 `[-4,-3,-2,-1,1,2,3,4]`，再统一经过既有素材把手过滤。扩展入口只在过滤后确有 `abs(offset)>2` 的候选且 `hasComparisonTrial=false` 时可用；切侧、收起或基础边界依赖变化会复位范围，不清除仍有效的 exact-key 缓存。额外候选各自复用现有 `BoundaryPixelProbe`，动作证据仍只在候选成为当前 B 后由主探针产生，因此没有后台动作扫描、指标排序、推荐、自动播放、草稿字段、API 或迁移。

候选快速导航不引入 React state。父组件对每个当前合法候选同步调用现有 exact-key 派生：当前单侧 delta 优先投影为 `当前 B`，其后依次读取 `kept_baseline / shortlisted / completed`，没有人工结果但存在动作记忆时为 `已实测`，否则为 `未看`。稳定 DOM ID 由双方 item ID、扫描侧和整数帧偏移组成；点击只执行 `focus({preventScroll:true}) + scrollIntoView({block:'nearest'})`。候选卡通过 `tabIndex=-1` 接受程序化焦点，导航按钮用 `aria-controls` 绑定目标。CSS sticky 只以 Inspector 为滚动容器、以候选扫描 section 为约束范围固定导航，候选卡的 scroll margin 为工具条预留定位空间；没有额外滚动监听、observer 或布局 state。该路径不调用 `selectPhaseCandidate`、播放状态机、证据回调、保存或排序逻辑。
