# 片场 V2 当前交接

> 更新日期：2026-07-18
>
> 文档入口见 [README](./README.md)。本文只保留恢复开发所需的当前事实，不再累积完整 Sprint 日志。

## 1. 当前目标

V2 正在独立于 V1 建设合同驱动、状态可审计的 AI 视频生产系统。五个智能体与确定性生产编译器的设计已经确认，运行代码尚未按完整合同实施：

- 角色从“需求登记员”调整为“创作制片人”。
- 输入包含当前会话的用户与助手消息，解决上下文指代丢失。
- 自然回复、创意建议和用户明确字段更新分离。
- 建议不自动成为系统事实，正式需求仍需用户确认。
- 内容策划、分镜导演、质量审核和剪辑助理均已定义版本化输入、候选输出、证据与验收边界。
- 确定性生产编译器不属于智能体，不调用模型或猜测生产路由。
- 权威合同位于 [创作中心设计](./V2_CREATION_CENTER_DESIGN.md#9-智能体编制与合同)。

当前只完成设计确认；实施时仍需逐阶段开发和验收，不能把文档能力展示成已经可用。

## 2. 运行状态

| 项目 | 当前值 |
|---|---|
| 分支 | `main` |
| V2 地址 | `http://127.0.0.1:8766/` |
| 健康检查 | `GET /api/v1/health` |
| 数据库迁移 | `20260717_19 (head)` |
| 已发布配置 | `production_config_5d3a7e46a72d4704bd8ded3d76dc2ab3`，版本 7 |
| 创作模型 | `DeepSeek V4 Flash`，OpenAI-compatible Provider |
| 外部生产执行 | `V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED` 未设置，保持关闭 |
| 创作模型执行 | 独立授权 `V2_AGENT_MODEL_EXECUTION_ENABLED=true` |

启动或重启：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\v2\start_v2.ps1 -Port 8766 -NoBrowser
```

启动脚本会运行 Alembic、重启 API 与 Worker，并把日志写入：

```text
v2/runtime/api.out.log
v2/runtime/api.err.log
v2/runtime/worker.out.log
v2/runtime/worker.err.log
```

## 3. 已实现能力

- React + TypeScript + Vite 管理台，FastAPI 后端，SQLAlchemy/SQLite/Alembic。
- Repository 边界、项目状态转移器、持久化事件、SSE 和 Transactional Outbox。
- 对话、助手回复、需求候选、需求版本、Decision、附件绑定和智能体运行审计。
- Creative Brief、`shot-plan.v2`、结构化分镜修订、人物/服装/场景/产品和主参考图合同。
- 系统配置版本、Provider/模型/工作流/视频/音频/存储/价格组件。
- ProductionSnapshot、DAG、费用影响、锁定、激活、提交和持久化 WorkItem。
- RunningHub 图片与首帧视频 Adapter 的严格合同和假传输测试。
- Asset 文件验证、QC、人工审核、素材联络表、时间线和最终交付合同。
- 项目控制台、制作队列、事件与费用审计账本、项目归档与恢复。
- 创作对话区固定高度、内部滚动、Enter 发送和助手回复持久化。

更精确的完成度见 [实现状态](./V2_IMPLEMENTATION_STATUS.md)。

## 4. 当前创作智能体事实

当前运行代码仍使用 `creative-dialogue-input.v1 / output.v1 / prompt.v1`：

- 每条成功保存的用户消息触发一次明确模型调用。
- 严格返回 `assistant_reply + field_updates[]`。
- 当前输入只包含用户消息，不包含历史助手回复，因此上下文指代能力不足。
- 当前只支持标题、主题、时长、画幅、音频模式和创作方向六个字段。
- 模型调用失败不自动重试，同一批消息不重复调用。
- 助手消息关联精确 AgentRun，Provider 请求 ID 和 token 用量可审计。

真实验收项目 `project_362dcdee277d4af2af8d3dd9667bbefd` 已证明调用和持久化链路可用，但也暴露了重复追问与助手历史缺失的问题。

## 5. 必须保持的边界

- 不得自行增加兜底、自动重试、模型切换、Provider 替换、工作流替换、输出修复或降级。
- 任何未来兜底逻辑必须先获得用户确认。
- 模型与智能体输出只能形成候选，不能直接修改正式版本、项目状态或生产路由。
- 不从自然语言、错误文本、名称或创建顺序猜测 ID、状态、依赖或路由。
- 工作流、Provider、模型、媒体规格和价格只使用显式已发布配置。
- 音频关闭时不创建 TTS 依赖。
- 普通首帧视频只能消费一张明确父图片，多图输入必须明确失败或使用已配置多帧工作流。
- QC 失败不自动发起付费重试，用户选择后才能设计重试命令。
- 凭据只保存在后端环境变量，不写入 Git、数据库或 API 响应。
- V1 与 V2 运行代码保持隔离，不复用 V1 运行时逻辑作为隐藏兼容层。
- `my_workspace/my_asset_library/library.json` 是用户本地改动，除非用户明确要求，否则不得暂存或提交。

## 6. 仍待确认的能力

- 外部 RunningHub 真实生产执行授权和联网验收。
- V2 CosyVoice、声音复刻和临时 OSS 上传。
- FFmpeg 本地合成执行方式。
- 用户确认的精确依赖重试、阻断恢复、取消项目和重新开版命令。
- 多用户部署、PostgreSQL、外部消息队列和正式权限模型。

这些能力不能因“页面上需要下一步”而自动实现或启用。

## 7. 最近变更

| 日期 | 变更 |
|---|---|
| 2026-07-18 | 五个智能体、确定性生产编译器、交接矩阵与验收合同完成设计确认，尚未完整实施 |
| 2026-07-18 | 创作智能体 V2 设计提案完成，尚未实施 |
| 2026-07-17 | 创作对话面板固定高度并内部滚动 |
| 2026-07-17 | 真实 DeepSeek 对话调用、助手消息持久化与 AgentRun 审计完成 |
| 2026-07-17 | 项目归档恢复、中文事实展示、事件与费用账本完成 |
| 2026-07-17 | `shot-plan.v2` 主参考图和视觉输入合同完成 |
| 2026-07-17 | RunningHub Adapter 与严格 Provider 准备检查完成，真实生产仍关闭 |

历史逐 Sprint 证据位于 [实现记录归档](./archive/implementation-notes/) 和 Git 历史。

## 8. 验证基线

最近完整基线：

- 后端测试：`130 passed`
- Python compileall：通过
- Vite production build：通过
- Alembic runtime/head：`20260717_19`
- 真实 DeepSeek 单次调用：通过
- 桌面与 390px 浏览器验收：通过，无横向溢出和控制台错误
- API 与 Worker 重启健康检查：通过

每次功能调整后仍需按风险重新运行相关测试、提交并推送代码、重启 `8766`，并更新对应活跃文档。

## 9. 下一步

按已经确认的 [智能体编制与合同](./V2_CREATION_CENTER_DESIGN.md#9-智能体编制与合同) 分阶段实施，建议顺序：

1. 完整用户/助手会话上下文与明确超限阻断。
2. 创意建议、用户显式更新和正式需求事实分离。
3. 建议选项 UI 与版本化需求字段目录。
4. 内容策划与分镜导演真实合同和固定验收集。
5. 质量审核智能体与剪辑助理候选合同；保持人工审核和确认边界。
