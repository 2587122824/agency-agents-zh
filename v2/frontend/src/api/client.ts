import type { AttachmentBinding, CreationAttachment, CreationCenter, CreationMessage, Decision, Health, Project, ProjectCreate, ProjectDetail, RequirementCandidate, RequirementVersion, WorkItem } from './types'

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
  registerAttachment: (projectId: string, file: File) => {
    const form = new FormData()
    form.set('command_id', crypto.randomUUID())
    form.set('actor_id', 'local-user')
    form.set('file', file)
    return request<CreationAttachment>(`/projects/${projectId}/attachments`, { method: 'POST', body: form })
  },
  bindAttachment: (projectId: string, attachmentId: string, bindingType: 'identity_reference' | 'voice_sample' | 'inspiration_only', entityId?: string) => request<AttachmentBinding>(`/projects/${projectId}/attachments/${attachmentId}/bindings`, {
    method: 'POST', body: JSON.stringify({ command_id: crypto.randomUUID(), actor_id: 'local-user', binding_type: bindingType, entity_id: entityId, entity_version_id: entityId ? `${entityId}_v1` : undefined }),
  }),
}
