# 片场 V2 对话式创作实现

## 目标

创作台不再把“对话”实现成只有用户消息的输入收集箱。用户消息保存成功后，前端只发起一次明确的需求整理命令；后端使用当前唯一可解析的已发布 `creative` 模型版本，生成助手回复和结构化需求候选。

## 权威链路

1. `Message(role=user)` 独立提交并进入项目事件流。
2. 需求整理命令冻结活动 `RequirementVersion`、尚未消费的用户消息、已确认 Decision、附件绑定及精确系统配置版本。
3. `AgentInputManifest` 与 `AgentRun(running)` 先提交，再进行一次模型请求。
4. 模型必须返回严格 JSON：`assistant_reply + field_updates[]`。
5. 后端只接受声明字段、输入清单中的精确消息 ID 和合法类型；不截取代码块、不修复 JSON、不补默认值。
6. 成功后创建 `Message(role=assistant)` 并记录模型/供应商配置版本、供应商请求 ID 和 token 用量；存在字段更新时，候选进入 `awaiting_review`，纯问候等无更新结果记为 `no_change`，不要求用户审核空候选。
7. 失败将 `AgentRun` 标为 `failed` 并展示结构化错误；同一批消息不得再次调用，新增用户消息后才允许新一轮。

## 配置与执行边界

- 仅支持显式 `adapter_kind=openai_compatible` 且声明 `text_generation` 的供应商。
- 同一时刻必须只能解析出一个已发布的 `creative` 模型系列；多模型系列明确阻断，不猜选。
- 凭据只从 `env://NAME` 后端引用读取，名称必须进入 `V2_CREDENTIAL_ENV_ALLOWLIST`。
- 真实对话调用要求 `V2_AGENT_MODEL_EXECUTION_ENABLED=true`，与生产素材执行授权分离。
- 对话模型可能产生模型费用，但不创建生产快照、WorkItem、图片、视频或音频费用。

## 明确不做

- 不自动重试失败的模型调用。
- 不切换模型、供应商或 Prompt 合同。
- 不把助手建议直接写成已确认 Decision 或 RequirementVersion。
- 不解析自然语言错误来改变项目状态。
- 不复用 V1 运行时代码或在请求时读取 V1 配置。

## 验收

- 发送后显示运行中助手气泡，成功后显示持久化助手回复。
- 普通问候允许返回空字段更新，但必须有助手回复。
- 非 JSON、代码块 JSON、未知字段、重复字段、无效消息 ID 或非法值均失败且只调用一次。
- 页面明确显示失败且说明没有自动重试和模型切换。
- 刷新后助手回复、运行状态、配置版本和 token 用量仍可审计。
