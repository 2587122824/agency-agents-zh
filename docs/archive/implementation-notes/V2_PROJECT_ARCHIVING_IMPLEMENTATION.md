# 片场 V2 项目归档与恢复实现

> 实现版本：Sprint 47
>
> 状态：已实现

## 1. 产品边界

项目归档用于清理默认工作列表，不是项目取消，也不是物理删除。归档后保留项目状态、需求、方案、生产快照、工作项、素材、费用、事件和所有引用关系；恢复后项目以归档前的原制作状态重新出现在默认列表。

V2 当前不提供生产项目物理删除。归档命令不会删除文件、级联数据库记录、取消供应商任务、重试工作项、替换路由或改变 `Project.status`。

## 2. 数据与并发

`Project` 增加：

```text
archived_at nullable
archived_by nullable
```

归档和恢复都要求 `expected_row_version`。成功后只更新归档字段、`updated_at` 和递增的 `row_version`；项目状态机字段保持不变。条件更新同时检查当前归档状态，避免重复归档、重复恢复和并发覆盖。

## 3. 命令与门禁

```text
POST /api/v1/projects/{project_id}:archive
POST /api/v1/projects/{project_id}:restore
```

归档要求 `confirm_archive=true`。项目存在 `queued` 或 `in_progress` 工作项时返回 `PROJECT_ACTIVE_WORK_EXISTS`，不会替用户取消任务。版本冲突返回 `PROJECT_ROW_VERSION_CONFLICT`。

两个命令都写入项目级 `CommandReceipt`。相同命令编号只执行一次；命令编号被其他操作使用时返回 `COMMAND_ID_REUSED`。

## 4. 查询与界面

`GET /projects` 和 `GET /project-controls` 默认排除归档项目，只有显式传入 `include_archived=true` 才返回全部项目。

项目总控台提供“已归档”视图。未归档项目显示归档按钮，归档项目显示恢复按钮；归档前展示确认对话框。存在活动工作项时按钮禁用，后端仍重复执行同一门禁。归档项目控制台不展示阶段继续按钮，但费用和事件审计仍可读取。

## 5. 审计

归档和恢复分别产生：

```text
project.archived.v1
project.restored.v1
```

事件保存操作者、保留的原项目状态和归档时间证据，并与项目归档字段和命令回执在同一事务提交。事件中文展示只按精确事件类型查表，不解析消息文本。

## 6. 明确不包含

- 生产项目物理删除
- 自动取消或供应商取消
- 自动恢复、重试或重新提交
- 项目状态替换为 `cancelled`
- 文件、成本或事件清理
- 归档项目的定时删除

## 7. 验收

- 归档后默认项目与控制台列表均不再返回项目。
- `include_archived=true` 仍返回完整项目事实。
- 项目状态不变，行版本递增，归档事件和命令回执只创建一次。
- 恢复后项目重新进入默认列表，原状态不变。
- 排队或执行中的工作项明确阻止归档。
- 旧版本请求不能覆盖较新的项目记录。
