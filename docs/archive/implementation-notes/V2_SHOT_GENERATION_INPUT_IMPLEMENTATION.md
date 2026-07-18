# 片场 V2 分镜生成输入合同实现

> 实施日期：2026-07-17
>
> 对应确认提案：[V2 分镜生成输入合同提案](../proposals/V2_SHOT_GENERATION_INPUT_CONTRACT_PROPOSAL.md)

## 1. 已实现范围

- 新生成的分镜候选与确认方案使用 `shot-plan.v2`。
- 每个新镜头必须具有 `visual_prompt`；`negative_prompt` 保持可空且无后端默认值。
- Shot 支持产品实体列表和显式 `primary_reference_entity_version_id`。
- 主参考只能来自镜头已声明、同项目、具有已验证图片附件的实体版本。
- 生产输入冻结附件 ID、URI、MIME、字节数和 SHA-256。
- 关键帧快照升级为 `production-snapshot.v2`，外部工作请求升级为 `production-work-request.v3`。
- RunningHub 在上传前校验冻结文件并只上传该文件一次。
- 设置页、方案编辑器、生产影响页和素材联络表展示对应合同事实。

## 2. 权威链

```text
分镜导演智能体候选 / 用户结构化修订
  -> shot-plan.v2 验证
  -> 用户确认 PlanVersion
  -> 生产影响分析
  -> production-snapshot.v2 冻结文件事实
  -> production-work-request.v3
  -> RunningHub 精确 NodeInfoList
```

后端不从 `action`、`composition`、文件名、实体顺序或历史配置拼装缺失输入。主参考未选择时是明确的 `null`，不是“待 Worker 决定”。

## 3. 验证层次

规划验证负责字段类型、长度、实体归属、唯一性、主参考成员关系及已验证图片附件。生产影响分析负责计划版本、工作流映射和工作流必填语义。快照创建负责实际文件路径、存储策略、MIME、大小和哈希。生产提交与 Provider 上传前再次核对冻结事实。

任一层失败都返回结构化错误并停止真实下游，不修改上游合同。可选输入缺失时按 NodeInfoList 合同省略该映射；必填输入缺失时阻断。

## 4. 历史兼容

迁移 `20260717_17` 将新 Shot 列设为可空，只保证旧数据仍可读取。历史 `shot-plan.v1` 不升级、不回填、不重新计算哈希。严格生产遇到 v1 时返回 `SHOT_PLAN_SCHEMA_UNSUPPORTED`，用户必须显式创建并确认新候选。

## 5. 明确未包含

- 未发布或改写任何现有系统配置。
- 未启用 `V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED`。
- 未进行真实 RunningHub 网络请求或产生费用。
- 未增加提示词拼接、默认负面词、首项参考选择、多图融合、工作流替换、自动重试、无参考图降级或历史语义修复。

## 6. 验证证据

- 后端完整测试：`124 passed`。
- Python `compileall` 通过。
- 前端 TypeScript 与 Vite 生产构建通过。
- 运行库与全新数据库均升级到 Alembic `20260717_17 (head)`。
- 桌面和 390px 配置编辑器无横向溢出，新增输入来源使用普通中文，浏览器控制台无错误。
- Provider 测试使用假传输验证精确参考图上传、可选输入省略和文件篡改联网前阻断。
