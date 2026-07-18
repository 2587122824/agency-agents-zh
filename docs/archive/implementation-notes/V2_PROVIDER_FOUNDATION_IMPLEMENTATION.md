# 片场 V2 Provider 基础层实现

> 实施日期：2026-07-17
>
> 当前状态说明：本文件记录 Sprint 38 的基础层边界。Sprint 39 已按用户确认注册独立 RunningHub 适配器；最新连接准备投影见 [V2 生成服务连接准备实现](./V2_PROVIDER_CONNECTION_READINESS_IMPLEMENTATION.md)。

## 1. 目标

本阶段建立 Provider 执行边界，但不接入任何真实外部生成服务。系统能够准确回答某个已发布供应商配置是否具备后端执行器和凭据条件，并在能力未接通时明确阻断。

本阶段不调用 RunningHub、CosyVoice、OSS 或其他外部网络，不产生费用，也不从 V1 导入执行代码或密钥。

## 2. 执行协议与注册表

`ProviderAdapter` 声明：

```text
adapter_kind
display_name
external
requires_credential
supported_work_kinds
execute(request)
```

Worker 只使用冻结在 `WorkAttempt.request_manifest` 中的精确 `adapter_kind`，并同时要求当前 `WorkItem.kind` 出现在适配器声明的能力集合中。注册表不根据供应商名称、工作流、提示词、错误信息或已有 V1 配置推断执行器。

当前默认注册表只有：

- `mock`：返回明确标记为模拟的非媒体结果。
- `local + assemble_timeline_contract`：组装本地时间线合同，不生成媒体。

`runninghub`、`dashscope`、`cosyvoice` 等均未注册。未知适配器或不匹配的工作类型统一产生结构化阻断 `PROVIDER_ADAPTER_NOT_CONNECTED`，不尝试其他适配器。

## 3. 凭据边界

数据库只保存 `credential_ref`。当前唯一实现的引用格式为：

```text
env://VARIABLE_NAME
```

运行时还必须在后端环境变量 `V2_CREDENTIAL_ENV_ALLOWLIST` 中显式列出该变量名。解析状态为：

- `not_configured`：配置没有凭据引用。
- `unsupported_reference`：不是受支持的精确引用格式。
- `not_authorized`：变量名未进入后端白名单。
- `missing`：已授权，但后端环境没有非空值。
- `available`：后端可以读取非空值。

引用字符串不用于猜测其他变量，变量值不写入数据库、事件、日志、API 或前端。凭据解析结果的调试表示也隐藏秘密字段；配置页面只编辑引用，不编辑真实密钥。

## 4. 只读连接状态

`GET /api/v1/system-config/provider-readiness` 读取所有已发布配置中的供应商，并返回：

- 配置和供应商显示名称。
- 适配器是否注册。
- 凭据状态。
- `connected | adapter_not_connected | credential_not_ready`。
- 是否至少有一个外部适配器满足本地执行前置条件。

该接口不发送网络请求，不验证供应商账号、余额、工作流是否在线，也不创建 Provider 任务或费用。`credential_state=available` 只证明后端能读取凭据，不能证明供应商连接成功。

API 不返回凭据引用名或凭据值。技术类型和能力声明只在设置页的折叠详情中展示。

## 5. Worker 行为

Worker 的原有 mock 与本地时间线行为迁移到注册表执行，不再由 Worker 内部硬编码分支选择。找不到精确适配器时：

1. 当前尝试进入 `blocked`。
2. 保存结构化错误码和精确执行清单证据。
3. 由已有依赖规则阻断真实下游。
4. 不创建第二次尝试，不修改路由，不替换工作流，不复用 V1。

## 6. 安全与确认边界

以下能力仍需用户单独确认后实施：

- 注册真实 RunningHub 或 CosyVoice 适配器。
- 配置新的真实后端密钥。
- 发起网络连通或账号校验。
- 发起第一笔真实生成请求。
- 对已阻断任务创建新的重试尝试。

“发布配置”“凭据可读”“适配器已注册”“网络可达”和“允许本次付费执行”是五个不同事实，不能互相替代。

## 7. 验收证据

- 注册表只解析精确的适配器与工作类型组合。
- RunningHub 未注册时始终明确阻断。
- 本地时间线适配器不能执行图片任务。
- 凭据解析要求精确格式与白名单。
- 连接状态响应不包含密钥值和环境变量名。
- 连接状态查询不进行网络探测。
- 现有 mock 和本地时间线 Worker 行为保持不变。

## 8. 未包含能力

- RunningHub、CosyVoice 或 OSS 网络客户端。
- Provider 提交、轮询、取消、对账和媒体下载。
- 外部任务 ID、权威运行时长和实际费用回写。
- 自动健康检查、自动重试、恢复、降级或路由替换。
- V1 Provider Adapter 复用或兼容桥。
