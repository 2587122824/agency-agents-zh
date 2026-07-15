import type { Decision, Health, Project, ProjectCreate, ProjectDetail, WorkItem } from './types'

const API_ROOT = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
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
}
