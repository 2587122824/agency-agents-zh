import type { AttachmentBinding, CreationAttachment, CreationCenter, CreationMessage, CreativeBriefCandidate, Decision, DecisionChangeImpactAnalysis, DecisionChangeImpactWorkspace, DecisionImpactGraph, DeliveryAttempt, DeliveryWorkspace, EditorWorkspace, EntityRegistry, Health, MaterialContactSheet, PlanningCenter, PlanVersion, ProductionAsset, ProductionExecution, ProductionImpactAnalysis, ProductionPreparation, ProductionSnapshot, Project, ProjectAuditLedger, ProjectControl, ProjectControlSummary, ProjectCreate, ProjectDetail, ProviderReadiness, QCReport, QualityReview, RequirementCandidate, RequirementVersion, ShotContract, ShotPlanCandidate, SystemConfigurationDiff, SystemConfigurationDraft, SystemConfigurationSummary, SystemConfigurationVersion, Timeline, TimelineItemDraft, WorkItem } from './types'

const API_ROOT = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: { ...(!isFormData ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new Error(payload?.detail || `请求失败：${response.status}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/health'),
  projects: (includeArchived = false) => request<Project[]>(`/projects?include_archived=${includeArchived}`),
  projectControls: (includeArchived = false) => request<ProjectControlSummary[]>(`/project-controls?include_archived=${includeArchived}`),
  projectControl: (id: string) => request<ProjectControl>(`/projects/${id}/control-center`),
  projectAuditLedger: (id: string, beforeSequence: number | null = null) => request<ProjectAuditLedger>(`/projects/${id}/audit-ledger?limit=50${beforeSequence === null ? '' : `&before_sequence=${beforeSequence}`}`),
  contactSheet: (id: string) => request<MaterialContactSheet>(`/projects/${id}/contact-sheet`),
  decisionImpactGraph: (id: string) => request<DecisionImpactGraph>(`/projects/${id}/decision-impact-graph`),
  decisionChangeImpacts: (id: string) => request<DecisionChangeImpactWorkspace>(`/projects/${id}/decision-change-impact-analyses`),
  analyzeDecisionChange: (projectId: string, decisionId: string, proposedValue: unknown) => request<DecisionChangeImpactAnalysis>(`/projects/${projectId}/decisions/${decisionId}/change-impact-analyses`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', proposed_value: proposedValue }),
  }),
  entityRegistry: () => request<EntityRegistry>('/entity-registry'),
  project: (id: string) => request<ProjectDetail>(`/projects/${id}`),
  createProject: (payload: ProjectCreate) =>
    request<ProjectDetail>('/projects', { method: 'POST', body: JSON.stringify(payload) }),
  archiveProject: (projectId: string, rowVersion: number) => request<ProjectDetail>(`/projects/${projectId}:archive`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: rowVersion, confirm_archive: true }),
  }),
  restoreProject: (projectId: string, rowVersion: number) => request<ProjectDetail>(`/projects/${projectId}:restore`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: rowVersion }),
  }),
  addDecision: (projectId: string, payload: { key: string; label: string; value?: unknown; status: 'pending' | 'resolved' }) =>
    request<Decision>(`/projects/${projectId}/decisions`, { method: 'POST', body: JSON.stringify(payload) }),
  resolveDecision: (projectId: string, decisionId: string, value: unknown) =>
    request<Decision>(`/projects/${projectId}/decisions/${decisionId}/resolve`, { method: 'POST', body: JSON.stringify({ value }) }),
  confirmProject: (projectId: string) => request<ProjectDetail>(`/projects/${projectId}/confirm`, { method: 'POST' }),
  queueValidation: (projectId: string) =>
    request<WorkItem>(`/projects/${projectId}/queue`, { method: 'POST', body: JSON.stringify({ kind: 'contract_validation' }) }),
  creationCenter: (projectId: string) => request<CreationCenter>(`/projects/${projectId}/creation-center`),
  addMessage: (projectId: string, content: string, replyToMessageId: string | null = null) => request<CreationMessage>(`/projects/${projectId}/messages`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', content, reply_to_message_id: replyToMessageId }),
  }),
  startConversationSession: (projectId: string) => request<{ id: string; status: string }>(`/projects/${projectId}/conversation-sessions`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user' }),
  }),
  initializeCreativeConversation: (projectId: string, baseVersionId: string) => request<RequirementCandidate>(`/projects/${projectId}/creative-conversation:initialize`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_base_version_id: baseVersionId }),
  }),
  generateRequirementCandidate: (projectId: string, baseVersionId: string) => request<RequirementCandidate>(`/projects/${projectId}/requirement-candidates:generate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_base_version_id: baseVersionId }),
  }),
  retryCreativeTurn: (projectId: string, runId: string, baseVersionId: string) => request<RequirementCandidate>(`/projects/${projectId}/creative-agent-runs/${runId}:retry`, {
    method: 'POST', body: JSON.stringify({
      command_id: crypto.randomUUID(), actor_id: 'local-user', expected_base_version_id: baseVersionId,
      failed_agent_run_id: runId, confirm_model_cost: true,
    }),
  }),
  selectCreativeSuggestion: (projectId: string, proposalId: string, baseVersionId: string, suggestionSetId: string, optionId: string) => request<RequirementCandidate>(`/projects/${projectId}/creative-proposals/${proposalId}:select`, {
    method: 'POST', body: JSON.stringify({
      command_id: crypto.randomUUID(), actor_id: 'local-user', expected_base_version_id: baseVersionId,
      suggestion_set_id: suggestionSetId, option_id: optionId,
    }),
  }),
  acceptRequirementCandidate: (projectId: string, candidateId: string, baseVersionId: string) => request<RequirementVersion>(`/projects/${projectId}/requirement-candidates/${candidateId}:accept`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_base_version_id: baseVersionId }),
  }),
  rejectRequirementCandidate: (projectId: string, candidateId: string, reason: string) => request<RequirementCandidate>(`/projects/${projectId}/requirement-candidates/${candidateId}:reject`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', reason }),
  }),
  resolveClarification: (projectId: string, clarificationId: string, baseVersionId: string, value: unknown) => request<RequirementVersion>(`/projects/${projectId}/clarifications/${clarificationId}:resolve`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_base_version_id: baseVersionId, value }),
  }),
  registerAttachment: (projectId: string, file: File) => {
    const form = new FormData()
    form.set('command_id', crypto.randomUUID())
    form.set('actor_id', 'local-user')
    form.set('file', file)
    return request<CreationAttachment>(`/projects/${projectId}/attachments`, { method: 'POST', body: form })
  },
  bindAttachment: (projectId: string, attachmentId: string, bindingType: 'identity_reference' | 'voice_sample' | 'inspiration_only', entityId?: string) => request<AttachmentBinding>(`/projects/${projectId}/attachments/${attachmentId}/bindings`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', binding_type: bindingType, entity_id: entityId }),
  }),
  planningCenter: (projectId: string) => request<PlanningCenter>(`/projects/${projectId}/planning-center`),
  generateCreativeBrief: (projectId: string, requirementVersionId: string) => request<CreativeBriefCandidate>(`/projects/${projectId}/creative-brief-candidates:generate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId }),
  }),
  retryCreativeBrief: (projectId: string, runId: string, requirementVersionId: string) => request<CreativeBriefCandidate>(`/projects/${projectId}/content-planner-runs/${runId}:retry`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, failed_agent_run_id: runId, confirm_model_cost: true }),
  }),
  decideCreativeBrief: (projectId: string, candidateId: string, requirementVersionId: string, accept: boolean) => request<CreativeBriefCandidate>(`/projects/${projectId}/creative-brief-candidates/${candidateId}:${accept ? 'accept' : 'reject'}`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, reason: accept ? undefined : '用户拒绝当前创意方案' }),
  }),
  generateShotPlan: (projectId: string, requirementVersionId: string, briefCandidateId: string) => request<ShotPlanCandidate>(`/projects/${projectId}/shot-plan-candidates:generate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, creative_brief_candidate_id: briefCandidateId }),
  }),
  retryShotPlan: (projectId: string, runId: string, requirementVersionId: string) => request<ShotPlanCandidate>(`/projects/${projectId}/director-runs/${runId}:retry`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, failed_agent_run_id: runId, confirm_model_cost: true }),
  }),
  reviseShotPlan: (projectId: string, candidateId: string, requirementVersionId: string, rowVersion: number, patches: Array<{ target_shot_code: string; changes: Partial<ShotContract> }>) => request<ShotPlanCandidate>(`/projects/${projectId}/shot-plan-candidates/${candidateId}:revise`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, expected_candidate_row_version: rowVersion, patches }),
  }),
  decideShotPlan: (projectId: string, candidateId: string, requirementVersionId: string, rowVersion: number, accept: boolean) => request<PlanVersion | ShotPlanCandidate>(`/projects/${projectId}/shot-plan-candidates/${candidateId}:${accept ? 'accept' : 'reject'}`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, expected_candidate_row_version: rowVersion, reason: accept ? undefined : '用户拒绝当前分镜方案' }),
  }),
  productionPreparation: (projectId: string) => request<ProductionPreparation>(`/projects/${projectId}/production-preparation`),
  analyzeProductionImpact: (projectId: string, payload: {
    plan_version_id: string
    production_config_version_id: string
    video_spec_version_id: string
    keyframe_workflow_slot_version_id: string
    video_workflow_slot_version_id: string
    tts_workflow_slot_version_id?: string | null
    pricing_catalog_version_id?: string | null
  }) => request<ProductionImpactAnalysis>(`/projects/${projectId}/production-impact-analyses`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', ...payload }),
  }),
  createProductionSnapshot: (projectId: string, impact: ProductionImpactAnalysis) => request<ProductionSnapshot>(`/projects/${projectId}/production-snapshots`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', impact_analysis_id: impact.id, analysis_hash: impact.analysis_hash, confirm_contract_scope: true }),
  }),
  lockProductionSnapshot: (projectId: string, snapshot: ProductionSnapshot) => request<ProductionSnapshot>(`/projects/${projectId}/production-snapshots/${snapshot.id}:lock`, {
    method: 'POST', body: JSON.stringify({
      command_id: crypto.randomUUID(), actor_id: 'local-user', expected_contract_hash: snapshot.contract_hash,
      expected_estimated_cost: snapshot.estimated_cost, expected_currency: snapshot.currency, confirm_high_risk_cost: true,
    }),
  }),
  activateProductionSnapshot: (projectId: string, snapshot: ProductionSnapshot) => request<ProductionSnapshot>(`/projects/${projectId}/production-snapshots/${snapshot.id}:activate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_contract_hash: snapshot.contract_hash }),
  }),
  submitProduction: (projectId: string, snapshot: ProductionSnapshot) => request<ProductionExecution>(`/projects/${projectId}/production-snapshots/${snapshot.id}:submit`, {
    method: 'POST', body: JSON.stringify({
      command_id: crypto.randomUUID(), actor_id: 'local-user', expected_contract_hash: snapshot.contract_hash,
      expected_estimated_cost: snapshot.estimated_cost, expected_currency: snapshot.currency,
      expected_dag_node_ids: snapshot.nodes.map(node => node.id), confirm_high_risk_submission: true,
    }),
  }),
  productionExecution: (projectId: string) => request<ProductionExecution>(`/projects/${projectId}/production-execution`),
  qualityReview: (projectId: string) => request<QualityReview>(`/projects/${projectId}/quality-review`),
  verifyAsset: (projectId: string, asset: ProductionAsset) => request<ProductionAsset>(`/projects/${projectId}/assets/${asset.id}:verify`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: asset.row_version }),
  }),
  runAssetQC: (projectId: string, asset: ProductionAsset) => request<QCReport>(`/projects/${projectId}/assets/${asset.id}:run-qc`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: asset.row_version }),
  }),
  reviewAsset: (projectId: string, asset: ProductionAsset, decision: 'approve' | 'reject', rationale: string) => request<ProductionAsset>(`/projects/${projectId}/assets/${asset.id}:${decision}`, {
    method: 'POST', body: JSON.stringify({
      command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: asset.row_version,
      qc_report_id: asset.latest_qc_report?.id, rationale,
    }),
  }),
  editorWorkspace: (projectId: string) => request<EditorWorkspace>(`/projects/${projectId}/editor-workspace`),
  approveQualityStage: (projectId: string, snapshotId: string) => request<EditorWorkspace>(`/projects/${projectId}/quality-stage:approve`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_snapshot_id: snapshotId }),
  }),
  createTimelineCandidate: (projectId: string, snapshotId: string, source: 'user' | 'editor_assistant', trackConfig: { audio_enabled: boolean; subtitle_enabled: boolean }, items: TimelineItemDraft[]) => request<Timeline>(`/projects/${projectId}/timeline-candidates`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_snapshot_id: snapshotId, source, track_config: trackConfig, items }),
  }),
  reviseTimelineCandidate: (projectId: string, timeline: Timeline, trackConfig: { audio_enabled: boolean; subtitle_enabled: boolean }, items: TimelineItemDraft[]) => request<Timeline>(`/projects/${projectId}/timelines/${timeline.id}:revise`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_snapshot_id: timeline.snapshot_id, expected_row_version: timeline.row_version, source: 'user', track_config: trackConfig, items }),
  }),
  validateTimeline: (projectId: string, timeline: Timeline) => request<Timeline>(`/projects/${projectId}/timelines/${timeline.id}:validate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: timeline.row_version }),
  }),
  confirmTimeline: (projectId: string, timeline: Timeline) => request<Timeline>(`/projects/${projectId}/timelines/${timeline.id}:confirm`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: timeline.row_version, expected_contract_hash: timeline.contract_hash, confirm_delivery_scope: true }),
  }),
  deliveryWorkspace: (projectId: string) => request<DeliveryWorkspace>(`/projects/${projectId}/delivery-workspace`),
  authorizeDelivery: (projectId: string, workspace: DeliveryWorkspace) => request<DeliveryAttempt>(`/projects/${projectId}/deliveries:authorize`, {
    method: 'POST', body: JSON.stringify({
      command_id: crypto.randomUUID(), actor_id: 'local-user', timeline_id: workspace.confirmed_timeline!.id,
      expected_timeline_contract_hash: workspace.confirmed_timeline!.contract_hash,
      execution_kind: 'external_upload', confirm_delivery_authorization: true,
    }),
  }),
  uploadDelivery: (projectId: string, attempt: DeliveryAttempt, file: File) => {
    const form = new FormData()
    form.set('command_id', crypto.randomUUID())
    form.set('actor_id', 'local-user')
    form.set('expected_request_fingerprint', attempt.request_fingerprint)
    form.set('expected_row_version', String(attempt.row_version))
    form.set('file', file)
    return request<DeliveryAttempt>(`/projects/${projectId}/delivery-attempts/${attempt.id}/output`, { method: 'POST', body: form })
  },
  verifyDelivery: (projectId: string, attempt: DeliveryAttempt) => request<DeliveryAttempt>(`/projects/${projectId}/delivery-attempts/${attempt.id}:verify`, {
    method: 'POST', body: JSON.stringify({
      command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: attempt.row_version,
      expected_asset_row_version: attempt.final_asset!.row_version,
    }),
  }),
  systemConfigurations: () => request<SystemConfigurationSummary[]>('/system-config/versions'),
  providerReadiness: () => request<ProviderReadiness>('/system-config/provider-readiness'),
  systemConfiguration: (id: string) => request<SystemConfigurationVersion>(`/system-config/versions/${id}`),
  createSystemConfiguration: (configuration: SystemConfigurationDraft) => request<SystemConfigurationVersion>('/system-config/versions', {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', configuration }),
  }),
  reviseSystemConfiguration: (id: string, rowVersion: number, configuration: SystemConfigurationDraft) => request<SystemConfigurationVersion>(`/system-config/versions/${id}:revise`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: rowVersion, configuration }),
  }),
  validateSystemConfiguration: (id: string, rowVersion: number) => request<SystemConfigurationVersion>(`/system-config/versions/${id}:validate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: rowVersion }),
  }),
  publishSystemConfiguration: (id: string, rowVersion: number) => request<SystemConfigurationVersion>(`/system-config/versions/${id}:publish`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: rowVersion, confirm_high_risk_changes: true }),
  }),
  retireSystemConfiguration: (id: string, rowVersion: number) => request<SystemConfigurationVersion>(`/system-config/versions/${id}:retire`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_row_version: rowVersion, confirm_reference_impact: true }),
  }),
  cloneSystemConfiguration: (id: string, displayName: string) => request<SystemConfigurationVersion>(`/system-config/versions/${id}:clone-draft`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', display_name: displayName }),
  }),
  systemConfigurationDiff: (id: string, baseVersionId: string) => request<SystemConfigurationDiff>(`/system-config/versions/${id}/diff?base_version_id=${encodeURIComponent(baseVersionId)}`),
}
