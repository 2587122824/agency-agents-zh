export interface FactPresentation {
  title: string
  description: string
}

const projectStatusLabels: Record<string, string> = {
  draft: '项目草稿',
  collecting_requirements: '正在整理需求',
  decision_required: '等待确认关键选择',
  planning: '正在制定创作方案',
  plan_review: '等待审核创作方案',
  contract_ready: '创作方案已确认',
  production_ready: '等待开始制作',
  producing: '正在制作素材',
  quality_review: '等待审核素材',
  editing: '正在剪辑',
  delivery_ready: '等待生成最终文件',
  blocked: '制作已暂停',
  completed: '项目已完成',
  cancelled: '项目已取消',
  confirmed: '已确认',
  queued: '等待处理',
  in_progress: '正在处理',
  review_required: '等待人工审核',
}

const snapshotStatusLabels: Record<string, string> = {
  preparing: '等待核对制作方案',
  locked: '制作方案已锁定',
  active: '制作方案已启用',
  submitted: '制作任务已提交',
  execution_completed: '制作执行已完成',
  execution_blocked: '制作执行已阻断',
}

const workStatusLabels: Record<string, string> = {
  queued: '等待制作',
  in_progress: '正在制作',
  completed: '已完成',
  review_required: '等待审核',
  blocked: '已阻断',
  cancelled: '已取消',
  skipped: '已跳过',
}

const attemptStateLabels: Record<string, string> = {
  created: '已创建',
  claimed: '准备执行',
  submitting: '正在提交',
  submitted: '已提交',
  polling: '生成中',
  succeeded: '已完成',
  failed: '执行失败',
  blocked: '已阻断',
  reconciliation_required: '等待人工核对',
}

const stateTriggerLabels: Record<string, string> = {
  project_created: '创建项目',
  legacy_contract_confirmed: '确认旧版项目合同',
  legacy_validation_completed: '完成旧版合同检查',
  brief_candidate_created: '创建创意方案候选',
  explicit_submission: '用户确认开始制作',
  block_project: '记录项目阻断',
  migration_backfill: '历史数据登记',
  migration_authority_backfill: '历史权威状态登记',
  migration_planning_authority_backfill: '历史方案状态登记',
}

const actorTypeLabels: Record<string, string> = {
  system: '系统',
  user: '用户',
  worker: '制作服务',
  agent: '智能体',
}

const aggregateTypeLabels: Record<string, string> = {
  project: '项目',
  production_snapshot: '制作方案',
  work_item: '制作步骤',
  work_attempt: '执行记录',
  asset: '素材',
  qc_report: '质量检查',
  timeline: '剪辑方案',
  delivery_attempt: '交付记录',
}

function exactLabel(value: string | null | undefined, labels: Record<string, string>, fallback: string) {
  return value ? labels[value] ?? fallback : '--'
}

export const projectStatusLabel = (value: string | null | undefined) => exactLabel(value, projectStatusLabels, '项目状态待确认')
export const snapshotStatusLabel = (value: string | null | undefined) => exactLabel(value, snapshotStatusLabels, '制作方案状态待确认')
export const workStatusLabel = (value: string | null | undefined) => exactLabel(value, workStatusLabels, '制作步骤状态待确认')
export const attemptStateLabel = (value: string | null | undefined) => exactLabel(value, attemptStateLabels, '执行状态待确认')
export const stateTriggerLabel = (value: string | null | undefined) => exactLabel(value, stateTriggerLabels, '状态来源待确认')
export const actorTypeLabel = (value: string | null | undefined) => exactLabel(value, actorTypeLabels, '操作来源待确认')
export const aggregateTypeLabel = (value: string | null | undefined) => exactLabel(value, aggregateTypeLabels, '责任对象待确认')

const blockerPresentations: Record<string, FactPresentation> = {
  PROVIDER_ADAPTER_NOT_CONNECTED: { title: '生成服务尚未接通', description: '对应制作步骤没有可用的执行组件，系统已停止继续执行。' },
  EXTERNAL_PROVIDER_EXECUTION_DISABLED: { title: '外部生成尚未授权', description: '外部服务执行开关未启用，本次没有发送真实生成请求。' },
  DEPENDENCY_BLOCKED: { title: '前置制作步骤被阻断', description: '当前步骤依赖的上游素材没有完成，因此不能继续。' },
  SNAPSHOT_EXECUTION_BLOCKED: { title: '制作方案存在执行阻断', description: '当前制作方案没有通过全部执行条件检查。' },
  PROVIDER_SUBMISSION_RECONCILIATION_REQUIRED: { title: '供应商提交结果需要人工核对', description: '系统无法确认外部任务是否已创建，不会自动再次提交。' },
  PROVIDER_TASK_FAILED: { title: '外部生成任务失败', description: '供应商已返回失败结果，系统没有自动重试或更换工作流。' },
  ASSET_QC_BLOCKED: { title: '素材未通过确定性检查', description: '文件或合同事实不符合当前制作方案，素材已停止流转。' },
  MEDIA_DIMENSIONS_INVALID: { title: '素材尺寸不符合要求', description: '素材宽高与制作方案冻结的画面规格不一致。' },
  MEDIA_DURATION_INVALID: { title: '素材时长不符合要求', description: '素材时长与对应镜头合同不一致。' },
  DELIVERY_BLOCKED: { title: '最终交付文件未通过检查', description: '交付文件或交付合同存在确定性问题。' },
}

const eventPresentations: Record<string, FactPresentation> = {
  'project.created': { title: '项目草稿已创建', description: '系统保存了新的项目草稿。' },
  'project.created.v1': { title: '项目草稿已创建', description: '系统保存了新的项目草稿。' },
  'project.confirmed': { title: '项目合同已确认', description: '用户确认了当前项目合同。' },
  'project.confirmed.v1': { title: '项目合同已确认', description: '用户确认了当前项目合同。' },
  'project.state_changed.v1': { title: '项目状态已更新', description: '显式状态触发器完成了一次权威状态变更。' },
  'project.blocked.v1': { title: '项目已进入阻断状态', description: '系统记录了首次阻断原因和责任对象。' },
  'project.block_diagnostic.v1': { title: '项目新增阻断证据', description: '系统追加了诊断证据，没有覆盖首次阻断来源。' },
  'project.completed.v1': { title: '项目已完成', description: '最终交付通过全部完成条件检查。' },
  'project.archived.v1': { title: '项目已归档', description: '项目已从默认工作列表隐藏，原制作、费用和审计记录均保留。' },
  'project.restored.v1': { title: '项目已恢复', description: '项目已重新回到默认工作列表，原制作状态保持不变。' },
  'requirement.confirmed.v1': { title: '需求版本已确认', description: '需求候选已成为正式且可追溯的需求版本。' },
  'conversation.message_added.v1': { title: '创作需求已保存', description: '用户新增的对话内容已写入项目。' },
  'conversation.assistant_replied.v1': { title: '创作智能体已回复', description: '本轮助手回复已经保存并关联到精确运行记录。' },
  'candidate.no_change.v1': { title: '本轮没有需求变更', description: '智能体已回复，但没有提出需要审核的结构化字段修改。' },
  'clarification.requested.v1': { title: '需求需要补充确认', description: '系统登记了一项必须由用户回答的需求问题。' },
  'clarification.resolved.v1': { title: '需求补充已确认', description: '用户回答已形成新的需求事实。' },
  'candidate.generated.v1': { title: '需求候选已生成', description: '智能体候选正在等待用户审核。' },
  'candidate.stale.v1': { title: '旧需求候选已过期', description: '新的用户输入使旧候选不再可确认。' },
  'candidate.rejected.v1': { title: '需求候选已拒绝', description: '用户拒绝了当前需求候选。' },
  'agent.run_created.v1': { title: '智能体运行已开始', description: '系统创建并启动了一次有明确输入清单的智能体运行。' },
  'agent.run_succeeded.v1': { title: '智能体运行已完成', description: '智能体已返回候选，候选仍需独立审核。' },
  'attachment.verified.v1': { title: '附件已登记', description: '附件元数据已验证，具体用途仍需明确绑定。' },
  'attachment.binding_confirmed.v1': { title: '附件用途已确认', description: '用户确认了附件在项目中的用途。' },
  'entity.version_confirmed.v1': { title: '实体版本已创建', description: '已确认的附件绑定形成了可追溯实体版本。' },
  'decision.created': { title: '项目决策已登记', description: '系统保存了一项项目决策。' },
  'decision.created.v1': { title: '项目决策已登记', description: '系统保存了一项项目决策。' },
  'decision.resolved.v1': { title: '项目决策已确认', description: '用户为待处理决策提供了明确答案。' },
  'decision.change_impact_analyzed.v1': { title: '决策变更影响已分析', description: '系统保存了只读影响范围和费用估算证据。' },
  'plan.brief_candidate_created.v1': { title: '创意方案候选已生成', description: '创意策划候选正在等待用户审核。' },
  'plan.brief_candidate_accepted.v1': { title: '创意方案候选已接受', description: '用户确认了创意方案候选。' },
  'plan.brief_candidate_rejected.v1': { title: '创意方案候选已拒绝', description: '用户拒绝了创意方案候选。' },
  'plan.shot_candidate_created.v1': { title: '分镜候选已生成', description: '分镜导演候选正在等待用户审核。' },
  'plan.shot_candidate_validation_failed.v1': { title: '分镜候选校验未通过', description: '候选合同存在明确问题，尚不能确认。' },
  'plan.shot_candidate_revised.v1': { title: '分镜候选已修订', description: '用户创建了新的分镜候选版本。' },
  'plan.shot_candidate_rejected.v1': { title: '分镜候选已拒绝', description: '用户拒绝了当前分镜候选。' },
  'plan.confirmed.v1': { title: '制作方案已确认', description: '分镜候选已成为不可变方案版本。' },
  'production.impact_evaluated.v1': { title: '制作影响已分析', description: '系统已计算制作步骤、配置兼容性和预计费用。' },
  'production.snapshot_prepared.v1': { title: '制作方案已创建', description: '不可变制作方案已保存，等待费用核对。' },
  'production.snapshot_locked.v1': { title: '制作方案与预计费用已锁定', description: '用户确认了合同哈希和预计费用。' },
  'production.snapshot_activated.v1': { title: '制作方案已启用', description: '当前制作方案已成为项目执行权威，尚未自动创建任务。' },
  'production.submitted.v1': { title: '制作任务已提交', description: '用户明确确认后，系统创建了当前方案的制作任务。' },
  'production.work_finished.v1': { title: '制作步骤已结束', description: '一个制作步骤进入完成或阻断等终止状态。' },
  'work.queued': { title: '合同检查已排队', description: '系统创建了本地合同检查任务。' },
  'work.queued.v1': { title: '合同检查已排队', description: '系统创建了本地合同检查任务。' },
  'contract.validated': { title: '制作合同检查完成', description: '本地结构化合同已完成检查。' },
  'contract.validated.v1': { title: '制作合同检查完成', description: '本地结构化合同已完成检查。' },
  'asset.created.v1': { title: '素材文件已登记', description: '文件已登记为待验证素材，尚未获得可用结论。' },
  'asset.verified.v1': { title: '素材文件已验证', description: '文件签名、内容哈希和媒体事实已通过检查。' },
  'quality.blocked.v1': { title: '素材质量检查被阻断', description: '素材存在确定性合同问题，不能继续流转。' },
  'quality.review_required.v1': { title: '素材等待人工审核', description: '自动分析能力不足以给出最终结论，需要用户判断。' },
  'asset.approved.v1': { title: '素材已通过审核', description: '素材已进入可用于剪辑的状态。' },
  'asset.rejected.v1': { title: '素材已被拒绝', description: '用户拒绝了该素材，原始审核证据仍保留。' },
  'quality.stage_approved.v1': { title: '素材审核阶段已确认', description: '用户确认当前素材范围可以进入剪辑。' },
  'timeline.candidate_created.v1': { title: '剪辑草案已创建', description: '新的时间线候选正在等待校验和确认。' },
  'timeline.validation_failed.v1': { title: '剪辑草案校验未通过', description: '时间线合同存在明确问题，尚不能确认。' },
  'timeline.validated.v1': { title: '剪辑草案已通过校验', description: '时间线合同已完成确定性检查。' },
  'timeline.confirmed.v1': { title: '剪辑方案已确认', description: '用户确认了精确时间线合同。' },
  'delivery.authorized.v1': { title: '最终交付已授权', description: '用户确认了精确交付请求，但系统没有自动启动渲染。' },
  'delivery.blocked.v1': { title: '最终交付检查被阻断', description: '最终文件没有通过确定性交付检查。' },
  'delivery.verified.v1': { title: '最终交付已验证', description: '最终文件通过了格式、尺寸、时长和内容检查。' },
}

export function blockerPresentation(code: string): FactPresentation {
  return blockerPresentations[code] ?? {
    title: '需要处理的项目问题',
    description: '系统保存了结构化阻断证据，请在技术详情中查看原始代码和记录。',
  }
}

export function eventPresentation(eventType: string): FactPresentation {
  return eventPresentations[eventType] ?? {
    title: '项目新增一条记录',
    description: '该事件尚未配置中文名称，原始类型和内容保留在技术详情中。',
  }
}
