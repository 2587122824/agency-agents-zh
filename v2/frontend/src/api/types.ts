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

export interface RequirementVersion {
  id: string
  version_number: number
  fields: Record<string, unknown>
  field_sources: Record<string, { type?: string; reference_id?: string }>
  is_active: boolean
  created_by: string
  created_at: string
}

export interface CreationMessage {
  id: string
  role: string
  content: string
  reply_to_message_id: string | null
  created_at: string
}

export interface RequirementCandidate {
  id: string
  base_requirement_version_id: string
  agent_run_id: string
  status: string
  fields: Record<string, unknown>
  field_sources: Record<string, { type?: string; reference_id?: string }>
  change_summary: Array<{ field_key: string; before: unknown; after: unknown; source_message_id?: string; risk_level: string }>
  validation_errors: Array<{ code?: string; message?: string }>
  created_at: string
  decided_at: string | null
}

export interface AgentRun {
  id: string
  agent_role: string
  status: string
  input_manifest_id: string
  model_provider: string
  model_name: string
  prompt_contract_version: string
  output_schema_version: string
  parsed_candidate_id: string | null
  error_code: string | null
  error_detail: string | null
  started_at: string | null
  finished_at: string | null
}

export interface AttachmentBinding {
  id: string
  attachment_id: string
  binding_type: string
  entity_id: string | null
  entity_version_id: string | null
  status: string
  confirmed_by: string
  confirmed_at: string
}

export interface CreationAttachment {
  id: string
  original_filename: string
  mime_type: string
  byte_size: number
  content_hash: string
  verification_status: string
  created_at: string
  bindings: AttachmentBinding[]
}

export interface CreationCenter {
  project_id: string
  active_requirement: RequirementVersion
  messages: CreationMessage[]
  current_candidate: RequirementCandidate | null
  candidate_history: RequirementCandidate[]
  pending_clarifications: Array<{
    id: string
    base_requirement_version_id: string
    field_key: string
    reason_code: string
    question: string
    options: Array<{ value: unknown; label: string }>
    risk_level: string
    status: string
    resolution: unknown
  }>
  latest_agent_run: AgentRun | null
  agent_runs: AgentRun[]
  attachments: CreationAttachment[]
  next_action: {
    code: string
    target_ids: string[]
    label: string
    incurs_model_cost: boolean
    incurs_production_cost: boolean
  }
}

export interface CreativeBriefCandidate {
  id: string
  requirement_version_id: string
  agent_run_id: string
  status: string
  brief: Record<string, unknown>
  field_sources: Record<string, { type?: string; reference_id?: string | null }>
  validation_errors: Array<Record<string, unknown>>
  created_at: string
  decided_at: string | null
}

export interface ShotContract {
  shot_code: string
  sequence_number: number
  duration_ms: number
  shot_type: string
  scene_entity_version_id: string | null
  character_entity_version_ids: string[]
  outfit_entity_version_ids: string[]
  face_visibility: string
  text_policy: string
  motion_requirement: string
  composition: string
  action: string
}

export interface ShotPlanCandidate {
  id: string
  requirement_version_id: string
  creative_brief_candidate_id: string
  agent_run_id: string
  status: string
  shots: ShotContract[]
  validation_errors: Array<Record<string, unknown>>
  created_at: string
  decided_at: string | null
}

export interface PlanVersion {
  id: string
  version_number: number
  requirement_version_id: string
  shot_plan_candidate_id: string
  status: string
  creative_brief: Record<string, unknown>
  contract_schema_version: string
  is_active: boolean
  confirmed_at: string
  confirmed_by: string
  created_at: string
  shots: ShotContract[]
}

export interface PlanningCenter {
  project_id: string
  active_requirement: RequirementVersion
  current_brief_candidate: CreativeBriefCandidate | null
  accepted_brief_candidate: CreativeBriefCandidate | null
  current_shot_candidate: ShotPlanCandidate | null
  active_plan: PlanVersion | null
  brief_history: CreativeBriefCandidate[]
  shot_plan_history: ShotPlanCandidate[]
  plan_history: PlanVersion[]
  entity_versions: Array<{
    id: string
    entity_id: string
    entity_type: string
    display_name: string
    version_number: number
  }>
  next_action: {
    code: string
    label: string
    target_ids: string[]
    incurs_model_cost: boolean
    incurs_production_cost: boolean
  }
}

export interface NodeBindingDraft {
  node_id: string
  field_path: string
  value_source: string
  value_type: 'string' | 'integer' | 'number' | 'boolean' | 'image' | 'audio' | 'json'
  required: boolean
}

export interface ProviderConfigDraft {
  provider_key: string
  display_name: string
  adapter_kind: string
  region?: string | null
  base_url: string
  credential_ref?: string | null
  capabilities: string[]
  request_timeout_seconds: number
  poll_interval_seconds: number
  max_concurrency: number
}

export interface ModelConfigDraft {
  config_key: string
  display_name: string
  agent_role: 'creative' | 'director' | 'qc' | 'editor'
  provider_key: string
  provider_model_id: string
  input_contract_version: string
  output_schema_version: string
  prompt_contract_version: string
  context_window?: number | null
  max_output_tokens?: number | null
  sampling: Record<string, unknown>
  capability_tags: string[]
}

export interface WorkflowSlotDraft {
  slot_key: string
  display_name: string
  operation_kind: string
  provider_key: string
  provider_workflow_id: string
  provider_workflow_version?: string | null
  model_config_key?: string | null
  input_schema_version: string
  output_schema_version: string
  node_info_list: NodeBindingDraft[]
  supported_video_spec_keys: string[]
  capability_tags: string[]
}

export interface VideoSpecDraft {
  spec_key: string
  display_name: string
  width: number
  height: number
  aspect_ratio: '9:16' | '16:9' | '1:1'
  fps: number
  duration_min_seconds: number
  duration_max_seconds: number
  frame_count_rule: Record<string, unknown>
  container: string
  video_codec: string
  pixel_format: string
  bitrate_policy: Record<string, unknown>
  safe_crop: Record<string, unknown>
}

export interface AudioConfigDraft {
  config_key: string
  display_name: string
  supported_modes: Array<'off' | 'voiceover'>
  tts_workflow_slot_key?: string | null
  default_voice_entity_version_id?: string | null
  sample_rate: number
  channels: 1 | 2
  format: string
  speaking_rate_min: number
  speaking_rate_max: number
  loudness_target?: number | null
  temporary_upload_policy_version_id?: string | null
}

export interface StoragePolicyDraft {
  policy_key: string
  display_name: string
  backend_kind: 'local' | 'oss'
  region_ref?: string | null
  bucket_ref?: string | null
  credential_ref?: string | null
  allowed_mime_types: string[]
  max_file_size_bytes: number
  public_url_policy: 'none' | 'signed' | 'public' | 'temporary_public'
  lifecycle_days?: number | null
  local_root_ref?: string | null
}

export interface SystemConfigurationDraft {
  config_key: string
  display_name: string
  description?: string | null
  providers: ProviderConfigDraft[]
  models: ModelConfigDraft[]
  workflow_slots: WorkflowSlotDraft[]
  video_specs: VideoSpecDraft[]
  audio: AudioConfigDraft
  storage: StoragePolicyDraft
}

export interface ConfigurationComponent {
  id: string
  component_type: string
  key: string
  version_number: number
  display_name: string
  status: string
  details: Record<string, unknown>
}

export interface SystemConfigurationSummary {
  id: string
  config_key: string
  version_number: number
  display_name: string
  description: string | null
  status: string
  row_version: number
  config_hash: string | null
  component_count: number
  validation_error_count: number
  published_at: string | null
  updated_at: string
}

export interface SystemConfigurationVersion extends Omit<SystemConfigurationSummary, 'component_count' | 'validation_error_count'> {
  supersedes_version_id: string | null
  validation_report: Array<{ code?: string; path?: string; message?: string; slot_key?: string; missing?: string[] }>
  created_by: string
  created_at: string
  components: ConfigurationComponent[]
  references: Array<{ ref_type: string; ref_id: string; created_at: string }>
}

export interface SystemConfigurationDiff {
  version_id: string
  base_version_id: string
  changed_components: Array<{ component_type: string; key: string; before: ConfigurationComponent | null; after: ConfigurationComponent | null }>
  high_risk_changes: string[]
  incurs_production_cost: boolean
}
