# 片场 V2 素材联络表实现

版本：v0.2

状态：已实现

实现目录：`v2/backend/app/contact_sheet/`、`v2/frontend/src/pages/ContactSheetPage.tsx`

## 1. 目标

素材联络表是项目级只读证据视图，用于并排核对当前活动生产快照中的素材文件、分镜合同、实际执行路由、声明依赖、实体来源和 QC 结论。它不选择素材、不改变审核状态，也不发起重试或生产调用。

## 2. 快照边界

查询只读取 `Project.active_snapshot_id` 精确指向的 `ProductionSnapshot`。项目没有活动快照、快照不存在或不属于该项目时返回显式空态，不改用编号最大、创建时间最新或任意历史快照。

```text
Project.active_snapshot_id
  -> ProductionSnapshot
  -> DAGNode / DependencyEdge
  -> WorkItem / WorkAttempt
  -> Asset / QCReport / AssetReviewDecision
  -> Shot / EntityVersion / Attachment
```

## 3. 卡片证据

每张编号素材卡返回：

- Asset 的类型、角色、状态、规格、时长、大小、哈希和可预览内容；
- DAG 节点 ID、节点键和节点类型；
- Shot 的编号、时长、类型、人物可见性、文字策略、动态要求、构图和动作；
- WorkAttempt 冻结请求清单中的供应商、适配器、工作流、任务 ID 和请求指纹；
- DependencyEdge 声明的父节点及父节点全部已登记 Asset；
- Shot 精确引用的人物、场景、服装 EntityVersion 及来源附件；
- 最新 QCReport、QCFinding、人工审核决定和下游影响。

现有执行清单没有冻结“本次实际消费的上游 asset_id”。因此联络表只能展示声明父节点及其全部登记输出，不能选中或推断其中某个 Asset 被实际使用。

## 4. 预览失败

图片、视频、音频和实体来源附件使用现有受保护内容接口。浏览器无法解码、文件缺失或内容不可访问时显示明确失败状态，仍保留 ID、哈希、路由和引用证据。页面不执行转码、修复、替换、路径搜索或重新上传。

## 5. API 与页面

```text
GET /api/v1/projects/{project_id}/contact-sheet
GET /api/v1/projects/{project_id}/assets/{asset_id}/content
GET /api/v1/projects/{project_id}/attachments/{attachment_id}/content
```

前端路由：`/projects/{project_id}/contact-sheet`。素材审核页在选中项目后提供入口。联络表支持直接访问和刷新，没有写操作控件。

## 5.1 Repository 边界

只读 `ContactSheetRepository` 负责精确读取：

- `Project.active_snapshot_id` 指向的快照记录。
- 快照内按稳定顺序读取的 DAGNode、DependencyEdge 与 Asset。
- 同项目、同方案的 Shot，以及同项目、同快照的 WorkItem/WorkAttempt。
- 按显式 ID 集合且限定项目读取的 EntityVersion、Entity 与 Attachment。

Contact Sheet 应用服务继续负责空态、卡片编号、节点内输出排序、依赖证据、实际尝试路由和实体引用组装。Repository 不采用最新快照替代活动快照，不推断 selected asset，不从节点名或错误文本猜测路由，也不搜索其他项目补齐实体或附件。

## 6. 验收

- 无活动快照时返回空态，不读取历史快照；
- 素材按 DAG 节点键、输出序号和创建时间稳定编号；
- 实际路由只来自精确 WorkAttempt，不从节点名或错误文本推断；
- 依赖展示父节点全部登记输出，不选中某个上游素材；
- 分镜与实体只展示同项目、同计划的精确引用；
- QC 报告和人工结论随素材卡展示；
- GET 前后 CommandReceipt、ProjectEvent、WorkAttempt 和 CostEvent 数量不变；
- 页面在桌面与手机宽度下没有页面级横向溢出；
- 不产生状态转移、供应商调用、费用、重试、兜底或替换。
