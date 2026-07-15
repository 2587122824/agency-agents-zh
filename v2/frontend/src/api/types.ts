export type ProjectStatus =
  | 'draft'
  | 'confirmed'
  | 'queued'
  | 'in_progress'
  | 'review_required'
  | 'blocked'
  | 'completed'

export interface ProjectCreate {
  title: string
  core_topic: string
  duration_seconds: number
  aspect_ratio: '9:16' | '16:9' | '1:1'
  audio_mode: 'off' | 'voiceover'
}

export interface Project extends ProjectCreate {
  id: string
  status: ProjectStatus
  created_at: string
  updated_at: string
}

export interface Decision {
  id: string
  project_id: string
  key: string
  label: string
  value: unknown
  status: 'pending' | 'resolved'
  source: 'user' | 'system'
  created_at: string
  resolved_at: string | null
}

export interface WorkItem {
  id: string
  project_id: string
  kind: string
  payload: Record<string, unknown>
  status: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ProjectDetail extends Project {
  decisions: Decision[]
  work_items: WorkItem[]
}

export interface Health {
  status: string
  service: string
  version: string
}
