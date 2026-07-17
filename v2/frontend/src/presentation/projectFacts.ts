export interface FactPresentation {
  title: string
  description: string
}

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
  'requirement.confirmed.v1': { title: '需求版本已确认', description: '需求候选已成为正式且可追溯的需求版本。' },
  'conversation.message_added.v1': { title: '创作需求已保存', description: '用户新增的对话内容已写入项目。' },
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
