export type ProjectStatus =
  | 'draft'
  | 'confirmed'
  | 'queued'
  | 'in_progress'
  | 'review_required'
  | 'blocked'
  | 'completed'
  | 'contract_ready'
  | 'production_ready'
  | 'producing'
  | 'quality_review'
  | 'editing'
  | 'delivery_ready'

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

export interface ProductionImpactAnalysis {
  id: string
  project_id: string
  plan_version_id: string
  production_config_version_id: string
  pricing_catalog_version_id: string | null
  status: string
  selection: Record<string, string | null>
  manifest: {
    audio_mode: string
    output_spec: Record<string, unknown>
    entity_version_ids: string[]
    shots: ShotContract[]
    dag: {
      nodes: Array<{ node_key: string; kind: string; workflow_slot_version_id: string | null; input_contract: Record<string, unknown> }>
      edges: Array<{ parent_node_key: string; child_node_key: string; dependency_type: string; input_slot: string | null }>
    }
  }
  analysis_hash: string
  validation_errors: Array<{ code: string; path?: string; message?: string }>
  execution_blockers: Array<{ code: string; message: string }>
  estimated_call_count: number
  cost_status: string
  estimated_cost: number | null
  currency: string | null
  created_by: string
  created_at: string
}

export interface ProductionSnapshot {
  id: string
  project_id: string
  plan_version_id: string
  production_config_version_id: string
  pricing_catalog_version_id: string | null
  impact_analysis_id: string
  snapshot_number: number
  status: string
  audio_mode: string
  output_spec: Record<string, unknown>
  selection: Record<string, string | null>
  contract: Record<string, unknown>
  contract_hash: string
  estimated_call_count: number
  cost_status: string
  estimated_cost: number | null
  currency: string | null
  execution_blockers: Array<{ code: string; message: string }>
  created_by: string
  created_at: string
  locked_at: string | null
  activated_at: string | null
  entity_versions: Array<{ entity_version_id: string; role: string }>
  nodes: Array<{ id: string; node_key: string; kind: string; shot_id: string | null; workflow_slot_version_id: string | null; pricing_rule_id: string | null; pricing_quantity: number | null; pricing_unit: string | null; estimated_cost: number | null; currency: string | null; input_contract: Record<string, unknown>; output_contract: Record<string, unknown> }>
  edges: Array<{ id: string; parent_node_id: string; child_node_id: string; dependency_type: string; input_slot: string | null }>
}

export interface WorkAttempt {
  id: string
  work_item_id: string
  attempt_number: number
  trigger: string
  provider: string
  provider_task_id: string | null
  request_fingerprint: string
  request_manifest: Record<string, unknown>
  response_manifest: Record<string, unknown> | null
  state: string
  execution_lock_owner: string | null
  execution_lock_expires_at: string | null
  submitted_at: string | null
  started_at: string | null
  finished_at: string | null
  error_code: string | null
  error_detail: string | null
  created_at: string
}

export interface ExecutionWorkItem {
  id: string
  project_id: string
  snapshot_id: string
  dag_node_id: string
  node_key: string
  kind: string
  status: string
  error: string | null
  priority: number
  request_fingerprint: string
  current_attempt_id: string
  available_at: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  attempts: WorkAttempt[]
}

export interface ProductionExecution {
  project_id: string
  project_status: string
  active_snapshot_id: string | null
  snapshot: ProductionSnapshot | null
  work_items: ExecutionWorkItem[]
  blockers: Array<{ work_item_id: string; node_key: string; error: string }>
}

export interface QCFinding {
  id: string
  code: string
  severity: string
  evidence: Record<string, unknown>
  contract_field: string | null
  disposition: string
  created_at: string
}

export interface QCReport {
  id: string
  report_number: number
  ruleset_version: string
  status: string
  analyzer: string
  created_at: string
  reviewed_at: string | null
  reviewed_by: string | null
  findings: QCFinding[]
}

export interface ProductionAsset {
  id: string
  project_id: string
  snapshot_id: string
  work_attempt_id: string | null
  dag_node_id: string | null
  node_key: string | null
  output_index: number
  asset_type: string
  role: string
  uri: string
  storage_backend: string
  content_hash: string | null
  mime_type: string | null
  byte_size: number | null
  width: number | null
  height: number | null
  duration_ms: number | null
  state: string
  row_version: number
  created_at: string
  verified_at: string | null
  approved_at: string | null
  archived_at: string | null
  latest_qc_report: QCReport | null
  review_decisions: Array<{ id: string; decision: string; rationale: string; actor_id: string; created_at: string }>
  affected_downstream_node_keys: string[]
}

export interface QualityReview {
  project_id: string
  project_status: string
  active_snapshot_id: string | null
  assets: ProductionAsset[]
  output_gaps: Array<{ code: string; dag_node_id: string; node_key: string; work_item_id: string | null; message: string }>
  counts: Record<string, number>
  stage_ready: boolean
  next_action: { code: string; label: string }
}

export interface ProductionPreparation {
  project_id: string
  active_plan_id: string | null
  audio_mode: string
  published_configurations: Array<{
    id: string
    config_key: string
    version_number: number
    display_name: string
    video_specs: Array<{ id: string; key: string; display_name: string; aspect_ratio: string; width: number; height: number; fps: number }>
    workflow_slots: Array<{ id: string; key: string; display_name: string; operation_kind: string; supported_video_spec_ids: string[] }>
    pricing_catalogs: Array<{ id: string; key: string; display_name: string; currency: string; confirmation_threshold: number; effective_from: string | null; effective_to: string | null }>
  }>
  analyses: ProductionImpactAnalysis[]
  snapshots: ProductionSnapshot[]
  next_action: { code: string; label: string; incurs_production_cost: boolean }
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

export interface PricingCatalogDraft {
  catalog_key: string
  display_name: string
  currency: string
  confirmation_threshold: number
  effective_from?: string | null
  effective_to?: string | null
  rules: Array<{
    workflow_slot_key: string
    unit: 'call' | 'output_second'
    unit_price: number
    minimum_charge?: number | null
  }>
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
  pricing?: PricingCatalogDraft | null
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

export interface EditorAsset {
  id: string
  snapshot_id: string
  dag_node_id: string | null
  node_key: string | null
  asset_type: 'video' | 'audio' | 'subtitle'
  role: string
  duration_ms: number | null
  width: number | null
  height: number | null
  state: 'approved' | 'used'
  content_hash: string | null
}

export interface TimelineItem {
  id: string
  track_type: 'main_video' | 'audio' | 'subtitle'
  sequence_number: number
  asset_id: string | null
  asset_state: string | null
  asset_type: string | null
  asset_duration_ms: number | null
  label: string
  gap_reason: string | null
  source_in_ms: number | null
  source_out_ms: number | null
  timeline_in_ms: number
  timeline_out_ms: number
  transform: Record<string, unknown>
}

export interface Timeline {
  id: string
  project_id: string
  snapshot_id: string
  version_number: number
  supersedes_timeline_id: string | null
  status: 'candidate' | 'review' | 'confirmed' | 'exported' | 'superseded'
  source: 'user' | 'editor_assistant'
  source_agent_run_id: string | null
  output_spec: Record<string, unknown>
  track_config: { audio_enabled: boolean; subtitle_enabled: boolean }
  validation_report: Array<{ code: string; path: string; message: string; evidence: Record<string, unknown> }>
  contract_hash: string | null
  row_version: number
  created_by: string
  created_at: string
  validated_at: string | null
  confirmed_at: string | null
  items: TimelineItem[]
}

export interface TimelineItemDraft {
  track_type: 'main_video' | 'audio' | 'subtitle'
  sequence_number: number
  asset_id: string | null
  label: string
  gap_reason?: string | null
  source_in_ms: number | null
  source_out_ms: number | null
  timeline_in_ms: number
  timeline_out_ms: number
  transform: Record<string, unknown>
}

export interface EditorWorkspace {
  project_id: string
  project_title: string
  project_status: string
  active_snapshot_id: string | null
  duration_ms: number
  aspect_ratio: string
  audio_mode: string
  quality_stage_ready: boolean
  quality_output_gaps: Array<{ code: string; node_key: string; message: string }>
  available_assets: EditorAsset[]
  timelines: Timeline[]
  next_action: { code: string; label: string }
}
