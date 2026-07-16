# 片场 V2 结构化分镜候选修订实现

> 实现日期：2026-07-16
>
> 范围：只修订尚未确认的 `ShotPlanCandidate`；不修改已确认 `PlanVersion`，不调用模型或生产供应商。

## 1. 产品语义

分镜候选不可原地编辑。用户提交结构化修改后，系统创建新的候选记录，并通过 `supersedes_candidate_id` 指向被替代候选。旧候选进入 `superseded`，历史内容保持不变；只有最新的 `awaiting_review` 候选可以拒绝或确认。

确认最新候选后才生成不可变 `PlanVersion` 和所属 `Shot`。已确认方案的后续变化仍必须从新的需求和规划版本开始，不能退回候选编辑器覆盖。

## 2. 数据合同

`ShotPlanCandidate` 新增：

```text
supersedes_candidate_id nullable
revision_number
source: director_agent | user_revision
agent_run_id nullable
row_version
created_by
```

Director 生成的根候选使用 `revision_number=1`、`source=director_agent` 并绑定原始 `AgentRun`。用户修订使用 `source=user_revision`、`agent_run_id=null`，通过候选版本链保留生成来源和修改来源，不把用户修改伪装成模型输出。

## 3. 修订命令

```text
POST /api/v1/projects/{project_id}/shot-plan-candidates/{candidate_id}:revise
```

命令必须携带：

- `command_id` 和 `actor_id`
- 当前活动 `expected_requirement_version_id`
- 被修订候选的 `expected_candidate_row_version`
- 一个或多个 `patches[]`

每个 patch 通过 `target_shot_code` 精确定位现有镜头，`changes` 只接受以下类型化字段：

- `shot_code`、`sequence_number`、`duration_ms`、`shot_type`
- `scene_entity_version_id`
- `character_entity_version_ids`、`outfit_entity_version_ids`
- `face_visibility`、`text_policy`、`motion_requirement`
- `composition`、`action`

请求使用 `extra=forbid`。不接受自由 JSON、供应商参数、工作流 ID、提示词覆盖或未知字段；首期也不通过该命令新增或删除镜头。

## 4. 确定性验证

服务在同一事务内完成：

1. 核对项目、活动需求版本、候选状态和 `row_version`。
2. 核对 patch 目标存在且一次命令中不重复。
3. 将 patch 应用到内存副本。
4. 验证总时长、镜头编号、连续顺序、约束枚举和精确实体版本引用。
5. 验证通过后创建新候选，并通过 Repository 条件更新原子核对状态与 `row_version` 后把旧候选标记为 `superseded`。
6. 写入命令回执和 `plan.shot_candidate_revised.v1` 事件后一次提交。

验证失败返回明确 `409`，不创建新候选，也不改变旧候选。并发请求只有一个可以完成状态切换，失败方回滚整个事务。命令回放返回第一次创建的同一候选，不重复产生版本。

## 5. 前端行为

方案页提供独立的“结构化修订”区域：

- 按镜头编辑编号、顺序、时长、类型、动作和构图。
- 通过选项控件编辑人脸、文字、动态与场景约束。
- 通过复选框绑定已确认的人物和服装实体版本。
- 只提交实际变化的镜头 patch。
- 编辑状态下禁止同时拒绝或确认候选。
- 侧栏展示候选版本、来源和状态，旧版本仍可审计。

桌面和移动端使用同一结构化合同；移动端将字段网格折叠为单列，不隐藏字段。

## 6. 传播证据

只读决策影响图新增候选之间的 `superseded_by` 边。用户修订没有虚构的 AgentRun 边；决策传播证据沿原 Director 候选和精确候选版本链继续到确认的 `PlanVersion`。

## 7. 明确禁止

本实现不包含：

- 原地修改候选或正式方案
- 自动提示词重写、字段补全或输出修复
- ID 猜测、实体名称匹配或隐式绑定
- 模型重新生成、Provider 调用或成本事件
- 自动重试、工作流替换、降级或兜底
- 项目状态机、生产快照或确认边界变化

## 8. 验收证据

- 有效 patch 创建新候选并替代旧候选。
- 幂等重放返回同一修订候选。
- 旧候选无法确认，最新候选可以生成不可变方案。
- 无效顺序、总时长或实体引用不会改变版本链。
- 过期 `row_version` 明确冲突。
- 决策影响图展示真实候选替代边。
- TypeScript 构建、后端 API/Repository 测试和 Alembic head 检查通过。
