# 片场 V2 数据模型设计

> 状态：设计基线
> 版本：0.2
> 更新日期：2026-07-15
> 上位文档：[V2 产品设计文档](./V2_PRODUCT_DESIGN.md)
> 配套文档：[V2 状态机与事件系统设计](./V2_STATE_MACHINE_EVENT_SYSTEM.md)

## 1. 目标

本文定义片场 V2 的权威数据实体、版本边界、引用规则和持久化约束。目标是让每个生产结果都能回答：由哪个用户决策、方案版本、生产快照、工作项尝试、供应商调用和质量结论产生。

本文不定义页面布局，也不允许通过兼容代码绕过缺失关系。

## 2. 设计原则

1. 数据库事实优先于 Agent 文本、前端状态和运行日志。
2. 用户决策、方案、实体和模板采用不可变版本；修改创建新版本。
3. 每次实际生产绑定唯一 `ProductionSnapshot`，工作项和素材不得脱离快照。
4. 所有引用使用稳定 ID 和外键，不从名称、提示词或顺序猜测。
5. 付费调用、工作尝试和成本事件一一可追溯。
6. Agent 只提交候选合同，不直接写生产权威表。
7. 业务层通过 Repository 接口访问数据，不直接散落 SQLAlchemy 查询。
8. 删除前检查引用；历史审计记录不做级联物理删除。

## 3. 总体关系

```mermaid
erDiagram
    PROJECT ||--o{ CONVERSATION : owns
    CONVERSATION ||--o{ MESSAGE : contains
    PROJECT ||--o{ REQUIREMENT_VERSION : versions
    PROJECT ||--o{ DECISION : records
    DECISION ||--o{ DECISION_IMPACT : affects
    PROJECT ||--o{ PLAN_VERSION : versions
    PLAN_VERSION ||--o{ SHOT : contains
    PLAN_VERSION ||--o{ PRODUCTION_SNAPSHOT : freezes
    DECISION ||--o{ SNAPSHOT_DECISION : selected
    PRODUCTION_SNAPSHOT ||--o{ SNAPSHOT_DECISION : binds
    PRODUCTION_SNAPSHOT ||--o{ SNAPSHOT_ENTITY_VERSION : binds
    ENTITY ||--o{ ENTITY_VERSION : versions
    ENTITY_VERSION ||--o{ SNAPSHOT_ENTITY_VERSION : selected
    PRODUCTION_SNAPSHOT ||--o{ DAG_NODE : compiles
    DAG_NODE ||--o{ DEPENDENCY_EDGE : parent
    DAG_NODE ||--o{ DEPENDENCY_EDGE : child
    DAG_NODE ||--o{ WORK_ITEM : realizes
    WORK_ITEM ||--o{ WORK_ATTEMPT : attempts
    WORK_ATTEMPT ||--o{ ASSET : produces
    ASSET ||--o{ QC_REPORT : inspected_by
    QC_REPORT ||--o{ QC_FINDING : contains
    PRODUCTION_SNAPSHOT ||--o{ TIMELINE : owns
    TIMELINE ||--o{ TIMELINE_ITEM : contains
    ASSET ||--o{ TIMELINE_ITEM : referenced_by
    PROJECT ||--o{ EVENT : emits
    WORK_ATTEMPT ||--o{ COST_EVENT : charged_by
    TEMPLATE ||--o{ TEMPLATE_VERSION : versions
    PRODUCTION_CONFIG_VERSION ||--o{ PRODUCTION_SNAPSHOT : configures
```

## 4. 通用字段规范

除纯关联表外，核心实体统一包含：

```text
id                  UUID/ULID，外部不可推断
created_at          UTC 时间
created_by          user / system / agent:<role>
updated_at          仅可变聚合使用
row_version         乐观锁版本
```

规则：

- 业务 ID 不使用展示名称生成。
- JSON 字段必须有 Pydantic Schema 和 `schema_version`，不能存放无约束对象。
- 时间统一存 UTC，前端按用户时区显示。
- 金额使用定点小数和 ISO 4217 币种，不使用浮点数。
- 密钥、令牌和完整供应商凭据不进入项目数据库、快照或事件。

## 5. 项目与对话

### 5.1 Project

```text
id
title
state
blocked_from_state nullable
active_requirement_version_id nullable
active_plan_version_id nullable
active_snapshot_id nullable
delivery_asset_id nullable
state_reason_code nullable
created_at / updated_at / row_version
```

约束：

- `state` 只能由项目状态评估器或显式项目命令更新。
- `delivery_asset_id` 必须指向已验证、未删除的最终交付素材。
- `active_snapshot_id` 必须属于同一项目和 `active_plan_version_id`。

### 5.2 Conversation 与 Message

```text
Conversation: id, project_id, status, created_at
Message: id, conversation_id, role, content, attachment_refs, created_at
```

`Message` 是需求证据，不是生产合同。Agent 不能从未被需求版本或决策引用的历史消息补全生产字段。

### 5.3 RequirementVersion

```text
id
project_id
version_number
source_message_ids[]
structured_requirement
schema_version
status: candidate | confirmed | superseded
created_at / confirmed_at
```

唯一约束：`(project_id, version_number)`。确认后内容不可变。

## 6. 决策与影响

### 6.1 Decision

```text
id
project_id
decision_key
category: content | visual | production | audio | delivery | compliance
version_number
value_json
source: user | declared_default | template | agent_proposal
source_ref_id nullable
risk_level: low | medium | high
status: pending | confirmed | rejected | superseded
breaking_change
locked
created_at / resolved_at / resolved_by
supersedes_decision_id nullable
```

约束：

- 同一 `decision_key` 可有历史版本，但同一项目只能有一个当前确认版本。
- `declared_default` 必须关联可见的配置版本；`template` 必须关联 `TemplateVersion`。
- `high` 风险只能由用户确认。
- 已确认记录不可原位修改，修改时通过 `supersedes_decision_id` 新建记录。

### 6.2 DecisionImpact

```text
id
decision_id
target_type: entity | entity_version | shot | plan | snapshot | asset | timeline
target_id nullable
selector nullable
impact_kind: invalidates | regenerates | recomputes | informational
reason_code
estimated_work_count nullable
estimated_cost nullable
currency nullable
```

`selector` 只能用于尚未实例化的候选范围，并使用受控语法，例如 `shot.character_id=char_01`。快照创建前必须解析为精确目标；不能在执行时模糊匹配。

## 7. 方案、快照与模板

### 7.1 PlanVersion

```text
id
project_id
version_number
requirement_version_id
status: candidate | review | confirmed | superseded
creative_brief_json
contract_schema_version
confirmed_at / confirmed_by
created_at
```

确认后的 `PlanVersion` 和所属 `Shot` 不可变。修改创建下一版本。

### 7.2 ProductionSnapshot

```text
id
project_id
plan_version_id
snapshot_number
status: preparing | locked | active | superseded | archived
decision_set_hash
entity_set_hash
system_config_version_id
pricing_catalog_version_id nullable
template_version_id nullable
output_spec_json
contract_json
contract_hash
locked_at / created_at
```

快照冻结：

- 已确认决策的精确版本
- 人物、服装、场景、产品和声音的精确实体版本
- 分镜与生产合同
- 显式选择的模板、供应商槽位和输出规格版本
- 音频开关；关闭时合同中不得存在 TTS 依赖

`locked` 后禁止修改。任何变化创建新快照。旧快照结果可保留和查看，但不能推进活动快照状态。

`preparing` 快照同样不提供原地修订接口。它可以在用户确认精确影响分析后持久化合同与 DAG，但当价格目录缺失时必须保留 `cost_status=not_configured`、`estimated_cost=null` 和确定性的执行阻断；只有完成成本核算与独立费用确认后才允许进入 `locked`。未知成本不能按零成本处理。

快照锁定时冻结 `pricing_catalog_version_id`，并逐 DAG 节点保存 `pricing_rule_id`、计价数量、计价单位、预计金额和币种。锁定命令必须回传精确合同哈希、预计总额与币种；不接受前端重新计算或模糊容差。金额确认只写 `kind=estimated, status=confirmed` 的 CostEvent，不能伪装成实际扣费。

精确冻结关系：

```text
SnapshotDecision
  snapshot_id
  decision_id
  decision_key

SnapshotEntityVersion
  snapshot_id
  entity_version_id
  role
```

两个关联表都使用复合唯一约束。`decision_set_hash` 和 `entity_set_hash` 只用于完整性校验，不能代替精确关联记录。

### 7.3 ProductionConfigVersion

```text
id
version_number
status: draft | validating | validation_failed | ready | published | retired
display_name
supersedes_version_id nullable
row_version
video_spec_json
audio_policy_json
provider_slot_bindings_json
model_registry_version
provider_registry_version
workflow_registry_version
storage_policy_version_id
quality_policy_version_id
execution_policy_version_id
pricing_catalog_version_id
config_hash
created_by
published_at nullable
created_at
```

生产配置版本不保存密钥。快照通过 `system_config_version_id` 精确绑定配置；运行时凭据只由后端密钥存储按已选供应商注入，不能改变供应商、模型或工作流选择。

`published` 版本不可修改。编辑已发布配置时复制为新 `draft`，验证和发布后形成新的 `id + version_number`。`config_hash` 用于验证组件集合未被篡改，不能代替各组件的精确外键。

#### 7.3.1 ModelConfigVersion

```text
id
config_key / version_number / display_name
agent_role: creative | director | qc | editor
provider_config_version_id / provider_model_id
input_contract_version / output_schema_version / prompt_contract_version
context_window nullable / max_output_tokens nullable
sampling_json / capability_tags_json
status: draft | published | retired
created_at / published_at
```

唯一约束：`(config_key, version_number)`。AgentRun 保存 `model_config_version_id`；仅保存供应商和模型名称不足以承担版本审计。

#### 7.3.2 ProviderConfigVersion

```text
id
provider_key / version_number / display_name
adapter_kind / region nullable / base_url
credential_ref
capabilities_json
request_timeout_seconds / poll_interval_seconds / max_concurrency
status: draft | published | retired
created_at / published_at
```

`credential_ref` 指向后端密钥存储，不使用数据库外键，不返回前端原文。供应商配置不包含备用供应商、自动降级顺序或错误改路由规则。

#### 7.3.3 WorkflowSlotVersion

```text
id
slot_key / version_number / display_name / operation_kind
provider_config_version_id
provider_workflow_id / provider_workflow_version nullable
model_config_version_id nullable
input_schema_version / output_schema_version
node_info_list_json
supported_video_spec_ids_json / capability_tags_json
validation_status / validation_report_json / tested_at nullable
status: draft | published | retired
created_at / published_at
```

唯一约束：`(slot_key, version_number)`。`node_info_list_json` 每项必须包含节点 ID、字段路径、值来源、值类型和是否必填。发布验证要求所有必填输入有且只有一个来源，输出合同可解析，引用的供应商、模型和视频规格均为已发布版本。

#### 7.3.4 Media And Runtime Policies

```text
VideoSpecVersion
  id / spec_key / version_number / status
  width / height / aspect_ratio / fps
  duration_min_seconds / duration_max_seconds / frame_count_rule_json
  container / video_codec / pixel_format / bitrate_policy_json / safe_crop_json

AudioConfigVersion
  id / config_key / version_number / status
  supported_modes_json / tts_workflow_slot_version_id nullable
  default_voice_entity_version_id nullable
  sample_rate / channels / format
  speaking_rate_range_json / loudness_target nullable
  temporary_upload_policy_version_id nullable

StoragePolicyVersion
  id / policy_key / version_number / status
  backend_kind: local | oss
  region_ref nullable / bucket_ref nullable / credential_ref nullable
  allowed_mime_types_json / max_file_size_bytes
  public_url_policy / lifecycle_days nullable / local_root_ref nullable

QualityPolicyVersion
  id / policy_key / version_number / status
  deterministic_checks_json / review_checks_json
  motion_thresholds_json / face_visibility_rules_json / result_mapping_json

ExecutionPolicyVersion
  id / policy_key / version_number / status
  worker_concurrency / lease_seconds / submit_timeout_seconds
  poll_interval_seconds / reconciliation_window_seconds

PricingCatalogVersion
  id / catalog_key / version_number / status / currency
  effective_from / effective_to nullable
```

执行策略不包含自动付费重试次数、备用工作流或供应商降级字段。价格明细使用独立 `PricingRule`：

```text
id
pricing_catalog_version_id
provider_config_version_id
operation_kind
workflow_slot_version_id nullable
unit / unit_price / minimum_charge nullable
```

#### 7.3.5 配置引用

```text
ProductionConfigComponent
  production_config_version_id
  component_type
  component_version_id

ConfigurationReference
  production_config_version_id
  ref_type: project | plan | snapshot | work_attempt
  ref_id
  created_at
```

`ProductionConfigComponent` 使用 `(production_config_version_id, component_type, component_version_id)` 复合唯一约束。实现可以保留聚合 JSON 作为读取优化，但 JSON 不能取代精确组件引用。

配置删除前必须查询 `ConfigurationReference`。存在历史快照、工作尝试或成本记录时只允许 `retired`，禁止物理删除。

### 7.4 Template 与 TemplateVersion

```text
Template: id, key, display_name, status
TemplateVersion: id, template_id, version_number, contract_schema,
                 declared_defaults, shot_pattern, output_constraints,
                 status, created_at
```

模板必须被明确选择并写入快照。合同缺字段时禁止自动套用模板。

## 8. 类型化实体

### 8.1 Entity 与 EntityVersion

```text
Entity: id, project_id nullable, entity_type, display_name, status
EntityVersion: id, entity_id, version_number, detail_schema_version,
               detail_json, reference_asset_ids[], status, created_at
SnapshotEntityVersion: snapshot_id, entity_version_id, role
```

`entity_type` 为 `character`、`outfit`、`scene`、`product` 或 `voice`。不同类型详情由判别联合 Schema 验证。

### 8.2 类型详情

```text
CharacterDetail
  identity_reference_asset_ids[]
  immutable_identity_traits
  aliases[]

OutfitDetail
  character_entity_id
  description
  reference_asset_ids[]
  continuity_label

SceneDetail
  master_asset_id
  environment_constraints
  continuity_label

VoiceDetail
  provider
  provider_voice_id
  clone_metadata_without_secret
  sample_asset_id
  model_family
```

同一实体的更新创建新 `EntityVersion`。服装与人物通过 ID 关联，不从描述判断所属关系。

### 8.3 实体注册表只读投影

`EntityRegistryView` 不新增持久化表，从以下记录构建：

```text
Entity -> EntityVersion -> Attachment
                      -> AttachmentBinding
                      -> SnapshotEntityVersion -> ProductionSnapshot
                      -> Shot -> PlanVersion
```

每个版本同时返回来源附件、确认绑定、快照引用和分镜角色引用。活动版本由 `EntityVersion.is_active` 明确标记，不按最大版本号猜测。查询不会改变 `Entity.status`、`EntityVersion.is_active` 或任何绑定关系。

来源附件内容接口只允许读取同项目、状态为 `verified` 且解析后路径仍位于 `RUNTIME_ROOT` 内的文件。该读取不提升附件状态，也不把浏览器可解码性当作新的数据库事实；完整媒体合同校验若要增强，必须由独立验证命令和版本化规则实现。

## 9. 分镜、DAG 与依赖

### 9.1 Shot

```text
id
plan_version_id
shot_code
sequence_number
scene_entity_version_id nullable
character_entity_version_ids[]
outfit_entity_version_ids[]
duration_ms
shot_type
face_visibility: required | optional | not_visible
text_policy: required | allowed | forbidden
motion_requirement: static_allowed | moderate | significant
composition / action
output_spec_id
```

唯一约束：`(plan_version_id, shot_code)` 和 `(plan_version_id, sequence_number)`。

### 9.2 DAGNode

```text
id
snapshot_id
node_key
kind
shot_id nullable
input_contract
output_contract
provider_slot_id nullable
estimated_cost nullable
currency nullable
```

唯一约束：`(snapshot_id, node_key)`。节点只引用同一快照冻结的资源。

### 9.3 DependencyEdge

```text
id
snapshot_id
parent_node_id
child_node_id
dependency_type: required | optional | informational
input_slot nullable
```

语义：

- `required`：父节点未完成且输出未验证时，子节点不能领取。
- `optional`：父节点缺失不会阻断，但系统不得自动寻找替代输入。
- `informational`：仅用于追踪影响，不参与可执行性判断。

不定义含糊的 `replaceable`。若确需替换，必须记录替换目标、用户决策并创建新快照或显式的新工作项合同。

编译约束：DAG 必须无环；普通 I2V 节点只能有一个图片输入；所有边端点必须属于同一快照。

## 10. 工作项与幂等

### 10.1 WorkItem

```text
id
project_id
snapshot_id
dag_node_id
kind
state
priority
request_fingerprint
current_attempt_id nullable
available_at
created_at / updated_at / row_version
```

唯一约束：`(snapshot_id, dag_node_id)`；若产品允许用户明确重做，则重做表现为同一工作项的新 `WorkAttempt`，而不是复制含糊节点。

### 10.2 WorkAttempt

```text
id
work_item_id
attempt_number
trigger: initial_user_confirmed | user_retry
provider
provider_task_id nullable
request_fingerprint
request_manifest
response_manifest nullable
state
execution_lock_owner nullable
execution_lock_expires_at nullable
submitted_at / started_at / finished_at
error_code / error_detail nullable
```

约束：

- `(work_item_id, attempt_number)` 唯一。
- `(provider, provider_task_id)` 在非空时唯一。
- `request_fingerprint` 由规范化输入合同、快照、供应商槽位和配置版本计算。
- Worker 通过有期限执行租约领取，不使用永久布尔锁。
- 超时租约只允许重新对账，不代表允许再次提交付费请求。
- 自动尝试次数固定为 1；新尝试必须由用户命令创建。

## 11. 素材与质量

### 11.1 Asset

```text
id
project_id
snapshot_id
work_attempt_id nullable
asset_type: image | video | audio | subtitle | project_file | final_delivery
role
uri
storage_backend
content_hash
mime_type
byte_size
width / height / duration_ms nullable
state: created | verified | review_required | approved | used | archived | deleted
verified_at / approved_at / deleted_at nullable
```

状态变化见状态机文档。`uri` 不等于文件存在，必须完成文件探测和哈希验证后进入 `verified`。

删除规则：

- 检查 DAG 输入、实体参考、QC、时间线和最终交付引用。
- 被活动快照或时间线引用时阻止删除，并列出引用。
- 审计要求保留时使用 `archived`；物理删除后保留墓碑和哈希，不复用 ID。

### 11.2 QCReport 与 QCFinding

```text
QCReport: id, asset_id, snapshot_id, ruleset_version, status,
          analyzer, created_at, reviewed_at, reviewed_by
QCFinding: id, qc_report_id, code, severity, evidence_json,
           contract_field, disposition
```

`QCReport.status` 为 `passed`、`review_required` 或 `blocked`。检测证据不能直接宣称人物身份；人工结论必须记录审核人和时间。

## 12. 时间线与交付

```text
Timeline
  id, project_id, snapshot_id, version_number
  status: candidate | review | confirmed | exported | superseded
  output_spec_json, created_at, confirmed_at

TimelineItem
  id, timeline_id, track_type, sequence_number
  asset_id, source_in_ms, source_out_ms
  timeline_in_ms, timeline_out_ms, transform_json
```

约束：

- 时间线只能引用同一项目中 `approved` 或 `used` 的素材。
- 源区间不能超过素材真实时长。
- 确认后的时间线不可变；修改创建新版本。
- 导出成功不等于项目完成，最终交付素材还必须存在并验证。
- `source=editor_assistant` 时必须绑定同项目已完成的 editor AgentRun；不能只用来源字符串冒充员工输出。
- 首个时间线使用创建命令；已有版本后必须填写 `supersedes_timeline_id` 并通过修订命令创建下一版本。
- `asset_id=null` 只表示用户明确保留的候选空位，不能通过时间线确认守卫。

## 13. 事件与成本

### 13.1 Event

`Event` 使用配套状态机文档定义的统一信封，核心索引为：

```text
(project_id, sequence) unique
(aggregate_type, aggregate_id, sequence)
(correlation_id)
(occurred_at)
```

事件仅记录事实和必要快照，不保存密钥、大段二进制或完整供应商凭据。

### 13.2 CostEvent

```text
id
project_id
snapshot_id
work_attempt_id
provider
provider_operation
kind: estimated | charged | adjusted | refunded
amount
currency
provider_reference nullable
status: pending | confirmed | disputed
occurred_at
```

同一供应商账单事件通过 `provider_reference + kind` 去重。预计费用与实际扣费分开记录，退款不覆盖原扣费。

## 14. Repository 边界

当前实施状态和逐步迁移边界见 [V2 Repository 边界实现](./V2_REPOSITORY_IMPLEMENTATION.md)。Project、Event、Decision、项目级 Command Receipt、Creation、Planning、Production、Quality、Editor、Delivery、Work、Configuration、Registry 和 Control 已有协议、SQLAlchemy 实现与合同测试；素材联络表只读投影仍未完成，因此完整 Repository 条目不得标记完成。

建议接口：

```text
ProjectRepository
ConversationRepository
CreationRepository
DecisionRepository
DeliveryRepository
PlanRepository
PlanningRepository
ProductionRepository
SnapshotRepository
EntityRepository
DagRepository
EditorRepository
WorkRepository
AssetRepository
QualityRepository
TimelineRepository
EventRepository
CostRepository
ConfigurationRepository
```

规则：

- API、状态评估器、DAG 编译器和 Worker 只依赖接口。
- Repository 负责事务内约束和持久化，不包含产品决策。
- 跨聚合操作由应用服务开启一个事务，并在同一事务写 Outbox 事件。
- 测试提供内存或 SQLite 实现，但必须运行同一组 Repository 合同测试。

## 15. SQLite 与 PostgreSQL 边界

SQLite 适用于本地单用户首期，但必须限制：

- 保持短事务，供应商网络调用不占用数据库事务。
- 开启外键、WAL 和合理 busy timeout。
- 领取工作项使用条件更新和 `row_version`，不假设行级锁。
- 单独建立事件序列分配策略，避免依赖数据库自增值作为业务顺序。
- JSON 内容由应用 Schema 验证，不依赖 SQLite 特有 JSON 查询完成核心约束。

迁移 PostgreSQL 时可替换 Repository 和锁实现，业务合同、状态机、ID、事件信封及迁移历史保持不变。

## 16. 索引与迁移

首期必要索引：

```text
project(state, updated_at)
decision(project_id, decision_key, status)
production_snapshot(project_id, status)
work_item(state, available_at, priority)
work_item(snapshot_id, dag_node_id)
work_attempt(provider, provider_task_id)
asset(project_id, snapshot_id, state)
qc_report(asset_id, status)
event(project_id, sequence)
cost_event(project_id, snapshot_id, occurred_at)
production_config_version(status, version_number)
model_config_version(config_key, version_number)
provider_config_version(provider_key, version_number)
workflow_slot_version(slot_key, version_number)
configuration_reference(production_config_version_id, ref_type)
```

所有 Schema 变化通过 Alembic：先增加可兼容字段和回填脚本，再启用非空/唯一约束。禁止在应用启动时临时修改表结构或用默认对象掩盖迁移失败。

## 17. 完整数据链示例

```text
project_01
  requirement_v1
  decision_identity_v1 + decision_audio_off_v1
  plan_v1
    SH-001 ... SH-005
  snapshot_001 (locked, active)
    character_main@v1
    outfit_training@v1
    scene_gym@v1
    dag_image_SH001 -> dag_video_SH001 -> dag_timeline
    work_image_SH001
      attempt_1
        asset_keyframe_SH001
        qc_report_01: approved
    work_video_SH001
      attempt_1
        cost_estimated_01
        cost_charged_01
        asset_video_SH001
        qc_report_02: review_required
    timeline_v1
      item_01 -> asset_video_SH001
```

音频决策为关闭，因此快照、DAG 和工作项中都不存在 TTS。若用户修改人物身份，则创建 `decision_identity_v2`、`plan_v2` 和 `snapshot_002`；`snapshot_001` 的素材保留可审计，但不能推进活动项目状态。

## 18. 数据模型验收

- 任一素材可追溯到快照、工作尝试和输入合同。
- 任一付费调用可追溯到用户确认和成本事件。
- 方案或实体修改不会覆盖历史版本。
- 不存在跨快照 DAG 边或靠名称解析的引用。
- 普通 I2V 多图片输入在编译期失败。
- 音频关闭的快照不存在 TTS 节点。
- 删除被引用素材时明确阻止并列出引用。
- Worker 崩溃恢复不会仅因租约过期而重复付费提交。
- SQLite 和 PostgreSQL Repository 通过同一合同测试。

## 19. 执行授权落地约束

- `Project.active_snapshot_id` 指向项目唯一活动快照。
- `WorkItem(snapshot_id, dag_node_id)` 唯一，保证一个快照节点只编译一次。
- `WorkItem.request_fingerprint` 对不可变执行清单做规范化 SHA-256；清单包含快照、合同哈希、节点、输入输出合同、工作流、供应商和适配器版本引用。
- 每个首次提交的 WorkItem 只创建 `attempt_number=1`、`trigger=explicit_submission` 的 WorkAttempt。
- Worker 通过 `execution_lock_owner` 与 `execution_lock_expires_at` 记录租约；租约不是自动重试授权。
- Mock 完成记录 `media_created=false` 和空 `provider_task_id`，不得创建 Asset 或 charged CostEvent。
- 本地时间线节点只记录所消费的父 WorkItem ID，不生成最终视频文件。

## 20. 素材与审核落地约束

`Asset` 额外保存：

```text
dag_node_id
output_index
provider_output_manifest
row_version
archived_at
```

- `(work_attempt_id, output_index)` 唯一，阻止同一供应商输出重复登记。
- `(storage_backend, uri)` 唯一，阻止同一存储对象被伪装成多个素材。
- `provider_output_manifest` 保存该输出在供应商响应中的原始结构；文件探测结果写入独立的哈希、MIME、尺寸和时长字段。
- `created` Asset 的 `content_hash` 为空；只有后端重新读取文件并匹配供应商声明哈希后才写入。

新增 `AssetReviewDecision`：

```text
id, project_id, asset_id, qc_report_id
decision: approved | rejected
rationale, actor_id, created_at
```

QCReport 保留检测器原始 `passed/review_required/blocked` 结论；人工审核不会覆盖检测结论，而是追加 ReviewDecision 并改变 Asset 生命周期状态。这样可以区分“检测器要求复核”和“用户最终批准”。

当前本地媒体探测支持 PNG、JPEG、WAV、MP4、UTF-8 SRT 和 JSON 项目文件。MP4 从容器 box 读取画面尺寸和时长，不接受调用方提交的替代元数据。未支持签名直接阻断，不按扩展名猜测格式。

## 21. 最终交付数据合同

`Project.delivery_asset_id` 只在最终文件通过确定性验证后写入，指向唯一的 `final_delivery` Asset。

`DeliveryAttempt`：

```text
id, project_id, snapshot_id, timeline_id
attempt_number
status: authorized | output_registered | verified | blocked
execution_kind: external_upload
request_manifest, request_fingerprint
final_asset_id nullable
error_code nullable, error_detail nullable
row_version, created_by, created_at
output_registered_at nullable, verified_at nullable
```

约束：

- `(timeline_id, attempt_number)` 唯一；首期每条确认时间线只允许 `attempt_number=1`。
- `final_asset_id` 唯一，避免一个最终文件被多个交付尝试声明。
- 请求清单冻结时间线合同哈希、逐项素材 ID 与内容哈希、剪辑区间、变换和输出规格。
- `request_fingerprint` 是规范化请求清单的 SHA-256，不包含上传文件路径或可变时间戳。
- 上传文件以 `work_attempt_id=null`、`dag_node_id=null` 的独立 Asset 登记，不能伪装成供应商 WorkAttempt 输出。
- 失败的 DeliveryAttempt 保留错误证据，不删除、不覆盖、不自动创建下一尝试。

完成守卫要求 DeliveryAttempt、Asset、Timeline、Project 和真实文件事实在同一事务结论中一致。仅有员工输出、上传成功响应或前端状态均不能建立 `completed` 权威。

## 22. 项目控制台只读投影

项目控制台不新增持久化表。`ProjectControlView` 在查询时从现有权威记录构建：

```text
Project
PlanVersion(active)
ProductionSnapshot(active or latest)
WorkItem -> WorkAttempt
Asset -> QCReport -> QCFinding
Timeline(latest)
DeliveryAttempt(latest)
CostEvent
ProjectEvent
```

投影同时返回 `persisted_status` 和 `evaluated_stage`，后者不是 Project 字段，也不能作为写命令输入。`evaluated_stage` 只用于把页面定位到需求、规划、生产准备、生产、质量审核、剪辑、交付或完成阶段。

执行路由来自不可变的 `WorkAttempt.request_manifest`，供应商任务 ID 来自 WorkAttempt；控制台不从 DAG 名称、员工输出或错误文本推断路由。阻断来源使用 `source_type + source_id + code + evidence`，费用按 CostEvent 的原始币种和 kind/status 聚合。查询不创建事件、不提交事务、不修复状态。

## 23. 素材联络表只读投影

素材联络表不新增持久化实体。`MaterialContactSheetView` 从项目当前活动快照的既有权威记录构建：

```text
Project.active_snapshot_id -> ProductionSnapshot
ProductionSnapshot -> DAGNode -> DependencyEdge
DAGNode -> WorkItem -> WorkAttempt
DAGNode -> Asset -> QCReport -> QCFinding
Shot -> EntityVersion -> Entity / Attachment
```

所有 Shot、EntityVersion、Entity 和 Attachment 查询同时校验 `project_id`；素材同时校验 `project_id + snapshot_id`。路由字段只读取 Asset 绑定的 WorkAttempt 及其冻结请求清单。

`DependencyEdge` 只证明节点级依赖，不能证明执行时采用了父节点的哪个 Asset。投影返回父节点全部已登记输出，不生成推断的 `selected_asset_id`。查询不新增 CommandReceipt、ProjectEvent、WorkAttempt、CostEvent 或其他记录。
