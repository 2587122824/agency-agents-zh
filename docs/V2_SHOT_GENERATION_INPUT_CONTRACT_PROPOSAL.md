# 片场 V2 分镜生成输入合同提案

> 状态：用户已确认，合同已于 2026-07-17 实施
>
> 提案日期：2026-07-17
>
> 本文记录严格 RunningHub 适配器正确承接 `prompt / negative_prompt / reference_image` 的设计决策。实现证据见 [V2 分镜生成输入合同实现](./V2_SHOT_GENERATION_INPUT_IMPLEMENTATION.md)。

## 1. 现状审计

当前权威链为：

```text
Director 候选
  -> Shot(action, composition, entity version IDs)
  -> PlanVersion
  -> ProductionSnapshot / DAGNode.input_contract
  -> WorkAttempt.request_manifest
  -> RunningHub NodeInfoList
```

已经确认的缺口：

1. `Shot.action` 和 `Shot.composition` 是分镜语义，不是完整的供应商画面生成描述。
2. Shot 可以引用人物、服装和场景实体版本，但没有明确指定哪一个实体图片进入只有一个参考图入口的工作流。
3. `EntityVersion.source_attachment_id` 可以追溯原始附件，但生产快照只冻结实体版本 ID，没有冻结附件 ID、文件哈希、MIME 和本地 URI。
4. 当前 RunningHub 适配器只为 I2V 上传父关键帧；关键帧图片任务没有显式参考图上传合同。
5. 旧配置同时需要正向描述、负向描述、参考图和“是否有参考图”开关。把它们全部替换为 `shot.action` 或固定默认值会丢失语义并形成隐式兜底。
6. 当前已确认 `shot-plan.v1` 不包含这些字段。直接回填会改写不可变历史方案。

因此，单独增加 NodeInfoList 来源选项不足以完成正确连接。

## 2. 推荐决策

建议将 Shot 合同升级为 `shot-plan.v2`，新增三个明确字段：

```json
{
  "visual_prompt": "完整、可直接交给视觉工作流的画面生成描述",
  "negative_prompt": null,
  "primary_reference_entity_version_id": "entity_version_xxx"
}
```

字段语义：

| 字段 | 是否必填 | 权威来源 | 规则 |
|---|---:|---|---|
| `visual_prompt` | 是 | Director 候选或用户结构化修订 | 完整视觉生成描述；后端只验证和原样传递，不拼接 `action/composition` |
| `negative_prompt` | 否 | Director 候选或用户结构化修订 | 用户不需要时保持 `null`；后端不添加安全词、质量词或默认负向词 |
| `primary_reference_entity_version_id` | 否 | Director 候选或用户显式选择 | 必须属于该 Shot 已声明的人物、服装、场景或产品实体集合；不得自动取第一项 |

同时补齐当前已在实体系统中存在、但尚未进入 Shot 的：

```json
{
  "product_entity_version_ids": []
}
```

`primary_reference_entity_version_id` 可以引用：

- `scene_entity_version_id`
- `character_entity_version_ids[]`
- `outfit_entity_version_ids[]`
- `product_entity_version_ids[]`

若没有可用参考图，字段保持 `null`，不能创建占位图片或借用其他 Shot 的图片。

## 3. Agent 责任边界

Director Agent 输出完整 `shot-plan.v2` 候选。它可以提出 `visual_prompt`、`negative_prompt` 和主参考实体候选，但不能：

- 创建或修改 Entity / EntityVersion；
- 根据文件名猜实体；
- 选择未在输入清单中的附件；
- 创建 ProductionSnapshot、DAGNode 或 WorkItem；
- 把多个实体默认为列表第一项；
- 把系统提示、供应商错误或后端模板写入 Shot；
- 在用户确认后修改已接受的 PlanVersion。

用户可以在现有结构化分镜修订器中编辑这三个字段。每次编辑创建新的 `ShotPlanCandidate` 修订，不覆盖源候选。

## 4. 分镜验证

`shot-plan.v2` 的确定性验证增加：

1. `visual_prompt` 去除首尾空白后必须非空，并限制长度；不检查或改写文案内容。
2. `negative_prompt` 可以为 `null`；若提供则必须是非空字符串。
3. 人物、服装、产品 ID 数组必须唯一且属于当前项目的 `confirmed` EntityVersion。
4. `primary_reference_entity_version_id` 若存在，必须精确出现在该 Shot 的实体集合中。
5. 主参考实体必须具有 `source_attachment_id`，附件必须属于当前项目、验证通过且 MIME 为受支持图片。
6. 不按实体类型、显示名称、创建时间或数组顺序选择主参考实体。

规划阶段只验证业务合同。所选具体工作流是否要求参考图，在生产影响分析阶段验证。

## 5. 快照冻结合同

生产影响分析必须为每个关键帧 DAG 节点生成明确的参考输入：

```json
{
  "input_contract": {
    "shot": {
      "visual_prompt": "...",
      "negative_prompt": null,
      "primary_reference_entity_version_id": "entity_version_xxx"
    },
    "reference_image": {
      "role": "primary",
      "entity_version_id": "entity_version_xxx",
      "attachment_id": "attachment_xxx",
      "uri": "runtime://attachments/...",
      "mime_type": "image/png",
      "byte_size": 123456,
      "content_hash": "sha256..."
    }
  }
}
```

没有主参考图时，`reference_image` 必须为 `null`，不能省略后由 Worker 猜测。

冻结前验证：

- Attachment、EntityVersion、Shot 和 Project 的归属一致；
- 文件路径位于 V2 附件根目录；
- 文件存在，实际 MIME、字节数和 SHA-256 与数据库一致；
- 存储策略允许该 MIME 和文件大小；
- 快照合同哈希覆盖完整参考输入。

快照创建后不重新查询活动实体版本，也不跟随实体的后续版本变化。

## 6. NodeInfoList 新来源

在用户确认本提案后，RunningHub 可新增：

```text
shot.visual_prompt
shot.negative_prompt
reference_image.primary
reference_image.present
```

语义：

- `shot.visual_prompt`：直接读取冻结字段。
- `shot.negative_prompt`：字段为 `null` 且映射 `required=true` 时阻断；`required=false` 时不提交该节点输入。
- `reference_image.primary`：上传冻结的精确附件并使用供应商返回值；没有附件且 `required=true` 时阻断。
- `reference_image.present`：从冻结合同中是否存在 `reference_image` 得到布尔值，不检查文件名或提示词。

`source_image` 继续只表示 I2V 的唯一父关键帧输出，与图片工作流的 `reference_image.primary` 严格区分。

## 7. RunningHub 执行变化

关键帧图片任务的适配器流程调整为：

1. 校验 `production-work-request.v3` 完整性。
2. 若合同含 `reference_image`，再次验证本地路径、MIME、字节数和哈希。
3. 只上传该精确文件一次。
4. 逐项解析 NodeInfoList；缺少必填值立即阻断。
5. 可选值不存在时省略该映射，不写空字符串、不使用工作流默认值替代必填项。
6. 提交后沿用现有任务号持久化和轮询恢复。

不允许上传 Shot 中其他实体附件，不允许在上传失败时改用无参考图生成。

## 8. 版本与迁移

建议新增数据库迁移，但不语义回填历史数据：

- Shot 新字段允许数据库层为空，以保存历史 `shot-plan.v1`。
- 新 `shot-plan.v2` 候选和 PlanVersion 由应用合同强制验证。
- 历史 PlanVersion 保持 `shot-plan.v1` 和原哈希，不修改 Shot。
- 严格 RunningHub 图片生产只接受 `shot-plan.v2 + production-work-request.v3`。
- 历史方案若要生产，用户必须显式创建新候选版本并确认，不自动升级。

迁移只改变存储结构，不创建新候选、不发布配置、不产生费用。

## 9. 前端体验

分镜表新增：

- “画面生成描述”正文；
- “避免内容”可空项；
- “主参考图”显示实体名称和缩略图；
- 未选择时明确显示“无主参考图”。

结构化修订器：

- 画面生成描述使用多行输入；
- 避免内容可清空为“未设置”；
- 主参考图只列出当前 Shot 已声明且具有已验证图片附件的实体版本；
- 不显示任意附件路径、Provider 参数或自由 JSON。

生产影响报告在锁定前显示每个镜头实际使用的主参考图、文件哈希摘要和工作流是否要求参考图。

## 10. 状态与确认边界

本提案不新增自动状态转移。流程保持：

```text
Director 候选
  -> 用户审核/修订
  -> 确认 PlanVersion
  -> 生产影响分析
  -> 用户确认快照范围
  -> 用户确认费用
  -> 用户激活
  -> 用户提交生产
```

下列情况在影响分析或快照创建前明确阻断：

- 工作流要求正向/负向描述，但 Shot 缺少对应字段；
- 工作流要求主参考图，但 Shot 未选择；
- 主参考实体不属于 Shot；
- 实体没有已验证图片附件；
- 冻结文件事实与数据库不一致；
- 旧 Plan/WorkRequest 合同版本用于严格适配器。

阻断不创建 WorkItem、不调用 Provider、不自动修订分镜或配置。

## 11. 验收测试

- Director 候选缺少 `visual_prompt` 时不能进入审核。
- 用户修订生成新候选，旧候选和已确认方案不变。
- 主参考实体不在 Shot 实体集合中时失败。
- 主参考实体没有图片附件或附件归属错误时失败。
- 多个人物存在但未明确主参考时，不选择第一项。
- 工作流要求参考图而 Shot 没有时，影响分析失败且零网络调用。
- 可选负向描述为 `null` 时省略可选映射；必填映射则失败。
- 参考附件在快照冻结后被修改时，Worker 在上传前以哈希不一致阻断。
- 图片工作流只上传冻结的主参考附件；I2V 仍只上传唯一父关键帧。
- 历史 `shot-plan.v1` 不被回填或自动升级。
- 失败不触发提示词拼接、参考图替换、无参考降级、重试或费用事件。

## 12. 实施影响范围

确认后预计涉及：

- Alembic migration 与 `Shot` 模型；
- Planning Pydantic 合同、Mock Director、验证、修订和 PlanVersion；
- PlanningRepository / ProductionRepository 的精确附件读取；
- 生产影响分析、快照合同、DAG 和 WorkAttempt `v3` 清单；
- RunningHub 合同校验、图片上传和可选映射；
- 方案页、生产影响页、素材联络表及 TypeScript 类型；
- 数据模型、产品设计、状态/事件和实现状态文档；
- 迁移、合同、Repository、Worker、Provider 与桌面/移动端测试。

## 13. 明确不包含

- 自动生成或拼接正向/负向提示词；
- 从 `action/composition` 回退生成 `visual_prompt`；
- 按数组第一项、实体类型或文件名猜主参考图；
- 多参考图融合、工作流自动选择或工作流替换；
- 对历史 PlanVersion 语义回填；
- 网络测试、真实 RunningHub 任务、自动重试或降级。

## 14. 待确认项

建议一次确认以下完整组合：

1. `visual_prompt` 必填，由 Director 输出且用户可修订。
2. `negative_prompt` 可空，不由系统补默认内容。
3. `primary_reference_entity_version_id` 可空，但只能由 Director 候选或用户显式选择。
4. 增加 `product_entity_version_ids`，使产品参考进入同一权威 Shot 合同。
5. 快照冻结主参考附件的 ID、URI、MIME、大小和哈希。
6. 旧 `shot-plan.v1` 不回填，需显式新建并确认 v2 方案。
7. 缺少工作流必需输入时阻断，不降级为无参考图或空提示词。
