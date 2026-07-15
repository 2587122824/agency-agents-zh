import type { AttachmentBinding, CreationAttachment, CreationCenter, CreationMessage, CreativeBriefCandidate, Decision, Health, PlanningCenter, PlanVersion, ProductionImpactAnalysis, ProductionPreparation, ProductionSnapshot, Project, ProjectCreate, ProjectDetail, RequirementCandidate, RequirementVersion, ShotPlanCandidate, SystemConfigurationDiff, SystemConfigurationDraft, SystemConfigurationSummary, SystemConfigurationVersion, WorkItem } from './types'

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
  projects: () => request<Project[]>('/projects'),
  project: (id: string) => request<ProjectDetail>(`/projects/${id}`),
  createProject: (payload: ProjectCreate) =>
    request<ProjectDetail>('/projects', { method: 'POST', body: JSON.stringify(payload) }),
  addDecision: (projectId: string, payload: { key: string; label: string; value?: unknown; status: 'pending' | 'resolved' }) =>
    request<Decision>(`/projects/${projectId}/decisions`, { method: 'POST', body: JSON.stringify(payload) }),
  resolveDecision: (projectId: string, decisionId: string, value: unknown) =>
    request<Decision>(`/projects/${projectId}/decisions/${decisionId}/resolve`, { method: 'POST', body: JSON.stringify({ value }) }),
  confirmProject: (projectId: string) => request<ProjectDetail>(`/projects/${projectId}/confirm`, { method: 'POST' }),
  queueValidation: (projectId: string) =>
    request<WorkItem>(`/projects/${projectId}/queue`, { method: 'POST', body: JSON.stringify({ kind: 'contract_validation' }) }),
  creationCenter: (projectId: string) => request<CreationCenter>(`/projects/${projectId}/creation-center`),
  addMessage: (projectId: string, content: string) => request<CreationMessage>(`/projects/${projectId}/messages`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', content }),
  }),
  generateRequirementCandidate: (projectId: string, baseVersionId: string) => request<RequirementCandidate>(`/projects/${projectId}/requirement-candidates:generate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_base_version_id: baseVersionId }),
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
  decideCreativeBrief: (projectId: string, candidateId: string, requirementVersionId: string, accept: boolean) => request<CreativeBriefCandidate>(`/projects/${projectId}/creative-brief-candidates/${candidateId}:${accept ? 'accept' : 'reject'}`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, reason: accept ? undefined : '用户拒绝当前创意方案' }),
  }),
  generateShotPlan: (projectId: string, requirementVersionId: string, briefCandidateId: string) => request<ShotPlanCandidate>(`/projects/${projectId}/shot-plan-candidates:generate`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, creative_brief_candidate_id: briefCandidateId }),
  }),
  decideShotPlan: (projectId: string, candidateId: string, requirementVersionId: string, accept: boolean) => request<PlanVersion | ShotPlanCandidate>(`/projects/${projectId}/shot-plan-candidates/${candidateId}:${accept ? 'accept' : 'reject'}`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', expected_requirement_version_id: requirementVersionId, reason: accept ? undefined : '用户拒绝当前分镜方案' }),
  }),
  productionPreparation: (projectId: string) => request<ProductionPreparation>(`/projects/${projectId}/production-preparation`),
  analyzeProductionImpact: (projectId: string, payload: {
    plan_version_id: string
    production_config_version_id: string
    video_spec_version_id: string
    keyframe_workflow_slot_version_id: string
    video_workflow_slot_version_id: string
    tts_workflow_slot_version_id?: string | null
  }) => request<ProductionImpactAnalysis>(`/projects/${projectId}/production-impact-analyses`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', ...payload }),
  }),
  createProductionSnapshot: (projectId: string, impact: ProductionImpactAnalysis) => request<ProductionSnapshot>(`/projects/${projectId}/production-snapshots`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', impact_analysis_id: impact.id, analysis_hash: impact.analysis_hash, confirm_contract_scope: true }),
  }),
  systemConfigurations: () => request<SystemConfigurationSummary[]>('/system-config/versions'),
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
