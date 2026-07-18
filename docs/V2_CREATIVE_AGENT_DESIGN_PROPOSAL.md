# 片场 V2 创作智能体设计提案

> 状态：待用户确认，尚未实施
>
> 日期：2026-07-18
>
> 适用范围：创作中心需求对话，不包含分镜、生产路由和素材执行

## 1. 问题定义

当前创作智能体把自然对话和严格字段提取压在同一个狭窄合同中，并且输入清单只包含用户消息，不包含助手历史。直接结果是：

- 无法理解“需要”“第一个”“就按刚才那个”等依赖上一轮助手回复的表达。
- 倾向重复项目已有字段、重复自我介绍和反复要求用户补充。
- “给我选项”时缺少主动策划能力，容易先追问用户要哪类选项。
- 只有六个可写字段，平台、受众、视觉风格、情绪、内容结构等只能挤进 `creative_direction`。
- 提示词把“不能编造项目事实”和“不能提出创意建议”混为一谈。
- 模型不知道运行时模型名称、当前时间和产品能力边界，无法准确回答相关问题。

本提案不通过放宽系统权威、自动写入需求或增加隐藏兜底来解决这些问题。

## 2. 产品定位

创作智能体定义为“创作制片人”，而不是通用聊天机器人或需求登记员。

它负责：

- 理解用户当前表达与完整对话上下文。
- 直接回答创作相关问题。
- 基于已确认事实提出有区分度的创意选项。
- 把用户明确表达的事实登记为待确认更新。
- 在确实缺少关键事实时提出一个聚焦问题。
- 清楚说明建议、候选、正式需求和生产执行之间的区别。

它不负责：

- 直接修改 `RequirementVersion`、`Decision`、`PlanVersion` 或项目状态。
- 选择模型供应商、生产供应商、工作流、预算或付费路由。
- 承诺图片、视频或音频已经生成。
- 根据错误文本猜测修复动作。
- 自动重试、切换模型、替换供应商、重写失败输出或补默认值。

## 3. 三层权威模型

```text
自然对话与创意建议
        ↓
CreativeTurnProposal（不可变建议事实）
        ↓ 用户明确选择或确认
RequirementCandidate（待确认需求候选）
        ↓ 用户确认命令
RequirementVersion（正式权威需求）
```

### 3.1 对话层

模型可以自由组织语言、回答问题、比较方案和提出建议，但这些内容只作为助手消息与建议提案保存，不改变系统状态。

### 3.2 建议层

模型输出的选项、问题和字段提议保存为不可变 `CreativeTurnProposal`。它们必须显示为“智能体建议”，不能伪装成用户决定。

### 3.3 确认层

用户点击选项或明确确认后，由独立命令记录选择来源并创建 `RequirementCandidate`。候选仍需经过现有确认边界才能形成新的 `RequirementVersion`。

## 4. 对话上下文

新增 `creative-dialogue-input.v2`，每轮输入至少包含：

```json
{
  "runtime_context": {
    "assistant_name": "片场创作制片人",
    "model_display_name": "DeepSeek V4 Flash",
    "current_time": "2026-07-18T10:00:00+08:00",
    "locale": "zh-CN",
    "timezone": "Asia/Shanghai"
  },
  "project_context": {
    "project_id": "project_xxx",
    "project_stage": "collecting_requirements",
    "active_requirement": {},
    "confirmed_decisions": [],
    "confirmed_attachment_bindings": []
  },
  "conversation": {
    "session_id": "conversation_session_xxx",
    "messages": [
      {"id": "message_1", "role": "user", "content": "给我几个方向"},
      {"id": "message_2", "role": "assistant", "content": "可以从工具、路径、误区三个方向展开"},
      {"id": "message_3", "role": "user", "content": "第一个"}
    ]
  },
  "requirement_schema_version": "creative-requirement.v2"
}
```

规则：

- 用户和助手消息按持久化顺序完整进入当前会话上下文。
- 消息必须保留真实 `role`、ID 和回复关系，不把助手文本伪装成系统事实。
- 不做隐藏语义摘要、相关性筛选或静默截断。
- 配置明确声明最大消息数与最大序列化字节数；超限返回 `CONVERSATION_CONTEXT_LIMIT_EXCEEDED`。
- 超限后只能由用户显式开启新会话；活动需求和已确认决策继续作为结构化事实传入。
- 运行时上下文只包含可公开事实，不返回密钥、凭据引用、内部配置 ID 或隐藏提示词。

## 5. 输出合同

新增 `creative-dialogue-output.v2`：

```json
{
  "assistant_reply": "可以做成三个方向，我更推荐第一个……",
  "suggestion_sets": [
    {
      "category": "content_direction",
      "title": "内容方向",
      "options": [
        {
          "label": "AI 学习路径",
          "summary": "按入门、练习、复盘形成清晰路径",
          "proposed_updates": [
            {
              "field_key": "content_structure",
              "value": "learning_path",
              "source_message_ids": ["message_1"]
            }
          ]
        }
      ]
    }
  ],
  "explicit_updates": [
    {
      "field_key": "visual_style",
      "value": "live_action",
      "source_message_ids": ["message_3"]
    }
  ],
  "clarifying_question": null
}
```

### 5.1 `assistant_reply`

- 必须先直接回应用户当前问题，再决定是否引导回创作任务。
- 不重复自我介绍，不机械复述全部项目字段。
- 用户要求选项时直接给出 2–4 个有差异的选项；只有无法形成有效选项时才澄清。
- 用户说“真人风格”等明确值时，说明自己的理解并登记显式更新，不反问已经明确的内容。

### 5.2 `suggestion_sets`

- 表示模型提出的创意建议，不等于用户事实。
- 每组 2–4 个选项，必须说明区别，不能只是同义改写。
- 后端在持久化时生成稳定的集合 ID 和选项 ID，模型不生成系统主键。
- 点击选项是独立用户命令，记录 exact proposal/option ID。

### 5.3 `explicit_updates`

- 只包含用户在本轮或可追溯上下文中明确表达的值。
- `source_message_ids` 必须引用用户消息，不能引用助手建议作为用户事实。
- 后端按版本化字段目录校验类型、枚举和值域。
- 风险等级和确认要求由后端字段目录决定，不接受模型返回的 `risk_level`。

### 5.4 `clarifying_question`

- 每轮最多一个主问题。
- 已经能够直接回答或给选项时不得用问题代替答案。
- 问题可以带 2–4 个选项，但选择仍需要显式用户命令。

## 6. 需求字段目录

`creative-requirement.v2` 建议包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` | text | 项目名称 |
| `core_topic` | text | 核心主题 |
| `content_goal` | enum/text | 传播、教学、转化、叙事等目标 |
| `platform` | enum | 抖音、小红书、视频号、B 站等 |
| `target_audience` | text | 目标受众 |
| `duration_seconds` | integer | 目标时长 |
| `aspect_ratio` | enum | 9:16、16:9、1:1 |
| `audio_mode` | enum | off、voiceover |
| `visual_style` | enum/text | 真人实拍、动画、产品展示等 |
| `tone` | enum/text | 专业、轻松、紧张、温暖等 |
| `content_structure` | enum/text | 路径、清单、问题解决、故事等 |
| `call_to_action` | text/null | 结尾行动号召 |
| `creative_constraints` | list[text] | 用户明确声明的创作限制 |

字段目录必须由后端版本化定义类型、枚举、风险和确认等级。模型不能创建未知字段，也不能把生产工作流参数写入需求字段。

## 7. 提示词结构

提示词改为仓库内版本化合同 `creative-dialogue-prompt.v2`，不继续作为难以审计的散落字符串。

提示词分四段：

1. 角色：创作制片人，先理解和共创。
2. 行为：直接回答、主动给选项、避免重复、理解上下文指代。
3. 权威边界：建议不是事实，模型不能确认、路由或生产。
4. 输出合同：只返回严格 JSON，不修复、不包代码块。

“不得编造”改为更精确的规则：

- 不得编造用户、项目、附件、费用和生产状态事实。
- 允许明确标记为“建议”的创意构思、比较和方案选项。
- 建议不能进入 `explicit_updates`，除非存在精确用户消息来源。

## 8. 交互设计

创作中心对话气泡下方支持结构化建议卡：

- 选项使用单选卡或按钮，不把技术字段展示给普通用户。
- 点击后显示将影响的需求项，再提交一次明确确认。
- 纯建议不让右侧正式需求面板出现“已修改”状态。
- `explicit_updates` 进入现有候选审核区，并显示用户原话来源。
- 元问题如“你是什么模型”直接从 `runtime_context` 回答，不产生候选。
- 非创作闲聊可以简短回应，但不登记需求字段，也不伪装具备外部工具能力。

## 9. 数据与审计

建议新增：

```text
ConversationSession
CreativeTurnProposal
CreativeSuggestionSelection
```

`CreativeTurnProposal` 绑定：

- `project_id`
- `conversation_session_id`
- `agent_run_id`
- `input_manifest_id`
- `assistant_message_id`
- `prompt_contract_version`
- `output_schema_version`
- 完整验证后的建议/更新/问题载荷

选择命令绑定精确提案、选项和用户，不从按钮文案或助手文本反推选择。

## 10. 调用与失败策略

- 每条成功持久化的用户消息最多触发一次当前已发布创作模型调用。
- 第一阶段继续采用单次模型调用，同时返回自然回复与结构化提案，避免双调用成本。
- 失败后持久化结构化错误，不自动重试。
- 不切换模型、供应商、Prompt 合同或输出合同。
- 不修复 JSON、不截取代码块、不补字段、不降低到旧合同。
- 模型选择歧义、上下文超限或输出无效时明确失败。

未来只有在同一验收集证明“回复质量”和“字段提取准确率”无法同时达标时，才单独评审拆分对话模型与提取模型；不得静默增加第二次调用。

## 11. 质量评估

建立固定对话验收集，不先凭主观感受更换模型：

| 场景 | 期望行为 |
|---|---|
| “给我选项” | 直接提供 2–4 个与当前主题相关的方向 |
| “第一个” | 依据上一轮助手选项理解指代，并请求明确确认 |
| “抖音” | 登记 `platform=douyin` 待确认，不反问“哪一方面” |
| “真人风格” | 登记视觉风格待确认，并简短说明理解 |
| “你是什么模型” | 回答运行时模型显示名称，不编造 |
| “现在几点” | 使用传入时区和时间回答，不改变需求 |
| “我还没吃饭” | 简短自然回应，不产生需求更新 |
| 长对话超限 | 明确阻断，不隐藏摘要或静默丢弃历史 |
| 非法 JSON | 本轮失败且只调用一次 |
| 建议选项 | 未经点击和确认不改变正式需求 |

指标至少包括：上下文指代理解、直接回答率、重复提问率、建议差异度、显式字段提取准确率、错误写入率和每轮 token/费用。

## 12. 实施顺序

### 阶段 A：上下文正确性

- 引入会话边界。
- 同时传入用户与助手消息。
- 增加公开运行时上下文。
- 增加确定性上下文上限和明确阻断。

### 阶段 B：建议与事实分离

- 实现 `CreativeTurnProposal` 和 output v2。
- 后端生成建议 ID，校验用户来源。
- 风险和确认等级移回后端字段目录。

### 阶段 C：创作交互

- 增加建议卡、选择确认和来源展示。
- 扩展 `creative-requirement.v2` 字段。
- 保持正式需求版本和项目状态机边界不变。

### 阶段 D：模型评测

- 使用固定验收集评测当前模型。
- 在同一输入、Prompt 和输出合同下比较候选模型。
- 模型切换只能通过显式配置发布，不做运行时自动选择。

## 13. 本提案的默认建议

- 角色名称使用“创作制片人”。
- 第一阶段保持每轮一次模型调用。
- 完整传递当前会话的用户和助手消息。
- 建议永远不自动成为系统事实。
- 风险等级由后端字段目录决定。
- 不加入自动摘要、自动重试、模型切换或任何隐藏兜底。
- 先修正上下文和合同，再决定是否更换模型。

以上内容在用户确认前仅为设计提案，不修改当前运行行为。
