export type ProjectStatus =
  | 'draft'
  | 'collecting_requirements'
  | 'decision_required'
  | 'planning'
  | 'plan_review'
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
  | 'cancelled'

export interface ProjectCreate {
  title: string
  core_topic: string
  duration_seconds: number
  aspect_ratio: '9:16' | '16:9' | '1:1'
  audio_mode: 'off' | 'voiceover'
}

export interface Project extends ProjectCreate {
  id: string
  row_version: number
  state_changed_at: string
  state_actor_type: string
  state_changed_by: string
  state_trigger: string
  state_reason_code: string | null
  blocked_from_state: string | null
  blocked_responsible_aggregate_type: string | null
  blocked_responsible_aggregate_id: string | null
  blocked_allowed_commands: string[]
  blocked_at: string | null
  archived_at: string | null
  archived_by: string | null
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

export interface DecisionImpactNode {
  node_id: string
  record_type: string
  record_id: string
  label: string
  status: string
  authority: string
  details: Record<string, unknown>
}

export interface DecisionImpactEdge {
  source_node_id: string
  target_node_id: string
  relation: string
}

export interface DecisionImpactSummary {
  decision_id: string
  key: string
  label: string
  current_value: unknown
  status: string
  observation_status: 'observed' | 'not_observed'
  direct_manifest_ids: string[]
  downstream_node_ids: string[]
  downstream_counts: Record<string, number>
  active_downstream_count: number
}

export interface DecisionImpactGraph {
  project_id: string
  project_title: string
  generated_at: string
  scope: 'observed_lineage'
  decisions: DecisionImpactSummary[]
  nodes: DecisionImpactNode[]
  edges: DecisionImpactEdge[]
  boundary: string
}

export interface DecisionChangeImpactTarget {
  id: string
  record_type: string
  record_id: string
  label: string
  record_status: string
  authority: string
  impact_kind: 'review_candidate'
  reason_code: 'OBSERVED_DECISION_LINEAGE'
  included_in_estimate: boolean
  estimated_work_units: number
  estimated_cost: number | null
  currency: string | null
  evidence: Record<string, unknown>
}

export interface DecisionChangeImpactAnalysis {
  id: string
  project_id: string
  decision_id: string
  status: 'completed' | 'insufficient_evidence'
  scope: 'observed_lineage_with_active_cost'
  current_value: unknown
  proposed_value: unknown
  observed_manifest_ids: string[]
  target_counts: Record<string, number>
  estimated_work_count: number
  cost_status: 'estimated' | 'not_applicable' | 'not_configured' | 'mixed_currency'
  estimated_cost: number | null
  currency: string | null
  analysis_hash: string
  active_snapshot_id: string | null
  created_by: string
  created_at: string
  targets: DecisionChangeImpactTarget[]
}

export interface DecisionChangeImpactWorkspace {
  project_id: string
  analyses: DecisionChangeImpactAnalysis[]
  boundary: string
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
  agent_run_id: string | null
  created_at: string
}

export interface RequirementCandidate {
  id: string
  conversation_session_id: string
  base_requirement_version_id: string
  supersedes_candidate_id: string | null
  agent_run_id: string
  status: string
  fields: Record<string, unknown>
  field_sources: Record<string, { type?: string; reference_id?: string }>
  change_summary: Array<{ field_key: string; before: unknown; after: unknown; source_message_id?: string; source_message_ids?: string[]; risk_level: string }>
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
  production_config_version_id: string | null
  model_config_version_id: string | null
  provider_config_version_id: string | null
  prompt_contract_version: string
  output_schema_version: string
  parsed_candidate_id: string | null
  parsed_proposal_id: string | null
  error_code: string | null
  error_detail: string | null
  provider_request_id: string | null
  token_usage: Record<string, unknown>
  started_at: string | null
  finished_at: string | null
  input_manifest: {
    id: string
    base_requirement_version_id: string
    message_ids: string[]
    decision_ids: string[]
    attachment_binding_ids: string[]
    system_config_version: string
    input_hash: string
    created_at: string
  } | null
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

export interface CreativeSuggestionSelection {
  id: string
  proposal_id: string
  suggestion_set_id: string
  option_id: string
  candidate_id: string | null
  selected_by: string
  selected_at: string
}

export interface CreativeTurnProposal {
  id: string
  base_requirement_version_id: string
  agent_run_id: string
  assistant_message_id: string
  status: string
  suggestion_sets: Array<{
    id: string
    category: string
    title: string
    field_key: string
    options: Array<{
      id: string
      label: string
      summary: string
      recommended: boolean
      proposed_updates: Array<{ field_key: string; value: unknown; source_message_ids: string[] }>
    }>
  }>
  explicit_updates: Array<{ field_key: string; value: unknown; source_message_ids: string[] }>
  creative_diagnosis: {
    project_type: 'personal_record' | 'promotion' | 'knowledge' | 'narrative' | 'brand_story' | 'emotional_expression' | 'other'
    stage: 'exploring' | 'shaping' | 'refining' | 'ready_to_confirm'
    summary: string
    established_fields: string[]
    open_gaps: Array<{ field_key: string; reason: string }>
    focus_field: string | null
    focus_reason: string | null
    source_message_ids: string[]
  } | null
  clarifying_question: { prompt: string } | null
  prompt_contract_version: string
  output_schema_version: string
  created_at: string
  selections: CreativeSuggestionSelection[]
}

export interface CreationCenter {
  project_id: string
  conversation_session_id: string
  initialization_status: 'not_started' | 'running' | 'succeeded' | 'failed'
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
  active_creative_proposal: CreativeTurnProposal | null
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

export interface CreativeBrief {
  title: string
  content_promise: string
  audience_takeaway: string
  hook: { kind: string; content: string }
  narrative_beats: Array<{ beat_code: string; purpose: string; summary: string; target_duration_ms: number }>
  script_segments: Array<{ segment_code: string; beat_code: string; kind: string; spoken_text?: string; on_screen_text?: string }>
  tone: string
  pacing: string
  platform_adaptation: string | null
  entity_version_ids: string[]
  constraints_carried_forward: string[]
  creative_additions: CreativeAddition[]
  facts_requiring_confirmation: BriefPendingFact[]
  open_questions: BriefOpenQuestion[]
  duration_seconds: number
  aspect_ratio: string
  audio_mode: 'off' | 'voiceover'
}

export interface CreativeAddition {
  addition_code: string
  category: 'narrative_structure' | 'hook' | 'expression' | 'example' | 'visual_strategy' | 'call_to_action'
  content: string
  purpose: string
  basis_refs: Array<{
    type: 'requirement_field' | 'decision' | 'entity_version'
    reference_id: string
  }>
}

export interface BriefPendingFact {
  fact_code: string
  statement: string
  reason: string
  resolution_question_code: string
}

export interface BriefOpenQuestion {
  question_code: string
  prompt: string
  reason: string
  options: Array<{
    option_code: string
    label: string
    description: string
    answer: string
  }>
}

export interface CreativeBriefCandidate {
  id: string
  requirement_version_id: string
  agent_run_id: string
  supersedes_candidate_id: string | null
  revision_number: number
  source: 'planner_agent' | 'planner_revision'
  status: string
  brief: CreativeBrief
  field_sources: Record<string, { type?: string; reference_id?: string | null }>
  validation_errors: Array<Record<string, unknown>>
  created_by: string
  created_at: string
  decided_at: string | null
}

export interface ShotContract {
  shot_code: string
  sequence_number: number
  duration_ms: number
  narrative_beat_code: string | null
  brief_segment_codes: string[]
  continuity_group_id: string | null
  continuity_relation: 'same_moment' | 'time_jump' | 'location_change' | 'outfit_change'
  action_count: 1
  shot_purpose: 'establish' | 'develop' | 'demonstrate' | 'contrast' | 'transition' | 'resolve'
  framing: 'extreme_close_up' | 'close_up' | 'medium' | 'full' | 'wide'
  camera_angle: 'eye_level' | 'high' | 'low' | 'top_down' | 'over_shoulder'
  camera_motion: 'locked' | 'pan' | 'tilt' | 'dolly' | 'tracking' | 'handheld'
  subject_motion: 'none' | 'subtle' | 'moderate' | 'significant'
  scene_entity_version_id: string | null
  character_entity_version_ids: string[]
  outfit_entity_version_ids: string[]
  product_entity_version_ids: string[]
  primary_reference_entity_version_id: string | null
  face_visibility: string
  face_subject_entity_version_ids: string[]
  text_policy: string
  required_on_screen_text: string | null
  audio_requirement: 'off' | 'lip_motion_only' | 'configured'
  composition: string
  action: string
  visual_prompt: string | null
  guide_frame_prompts: { start: string; middle: string; end: string } | null
  negative_prompt: string | null
  new_information: string
  generation_requirements: {
    reference_image_required: boolean
    multi_frame_required: boolean
    identity_consistency_required: boolean
    precise_text_required: boolean
  }
}

export interface ShotPlanCandidate {
  id: string
  requirement_version_id: string
  creative_brief_candidate_id: string
  agent_run_id: string | null
  supersedes_candidate_id: string | null
  revision_number: number
  source: 'director_agent' | 'director_revision' | 'user_revision' | 'asset_feedback_draft' | 'manual_revision_draft'
  status: string
  shots: ShotContract[]
  validation_errors: Array<Record<string, unknown>>
  row_version: number
  created_by: string
  created_at: string
  decided_at: string | null
}

export interface PlanVersion {
  id: string
  version_number: number
  requirement_version_id: string
  shot_plan_candidate_id: string
  status: string
  creative_brief: CreativeBrief
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
  revision_draft: ShotPlanCandidate | null
  revision_context: AssetRevisionRequest | null
  active_plan: PlanVersion | null
  brief_history: CreativeBriefCandidate[]
  shot_plan_history: ShotPlanCandidate[]
  plan_history: PlanVersion[]
  latest_planner_run: AgentRun | null
  latest_director_run: AgentRun | null
  entity_versions: Array<{
    id: string
    entity_id: string
    entity_type: string
    display_name: string
    version_number: number
    source_attachment_id: string | null
    source_mime_type: string | null
    source_attachment_verified: boolean
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
  snapshot_contract_hash: string
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
  image_phase_required: boolean
  image_phase_approval_manifest: Record<string, unknown> | null
  image_phase_approved_at: string | null
  image_phase_approved_by: string | null
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
  row_version: number
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
  blockers: Array<{ work_item_id: string; node_key: string; error_code: string | null; error: string | null }>
  phases: Array<{
    phase: 'images' | 'videos_and_later'
    status: string
    total_count: number
    completed_count: number
    approved_count: number
    expected_node_ids: string[]
    approved_asset_ids: string[]
  }>
}

export interface BlockedProductionClosed {
  project_id: string
  project_status: string
  closed_snapshot_id: string
  closed_snapshot_status: string
  cancelled_work_item_ids: string[]
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

export interface QCReportCandidateFinding {
  finding_code: string
  category: string
  severity: string
  confidence: number
  summary: string
  evidence: Array<Record<string, unknown>>
  contract_refs: string[]
  suggested_review_action: string
}

export interface QCReportCandidate {
  id: string
  asset_id: string
  agent_run_id: string
  status: string
  overall_recommendation: string
  findings: QCReportCandidateFinding[]
  analyzer_version: string
  created_at: string
  decided_at: string | null
}

export interface QCAgentRun {
  id: string
  agent_role: 'qc'
  status: string
  model_provider: string
  model_name: string
  prompt_contract_version: string
  output_schema_version: string
  error_code: string | null
  error_detail: string | null
  provider_request_id: string | null
  token_usage: Record<string, unknown>
  started_at: string | null
  finished_at: string | null
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
  latest_qc_candidate: QCReportCandidate | null
  latest_qc_agent_run: QCAgentRun | null
  review_decisions: Array<{ id: string; decision: string; rationale: string; actor_id: string; created_at: string }>
  affected_downstream_node_keys: string[]
  revision_requests: AssetRevisionRequest[]
  review_context: {
    node_kind: string | null
    shot: Record<string, unknown>
    output_contract: Record<string, unknown>
  }
  approval_revocation: {
    allowed: boolean
    blocker_code: string | null
    message: string
  }
}

export interface AssetRevisionRequest {
  id: string
  asset_id: string
  snapshot_id: string
  plan_version_id: string
  shot_id: string | null
  shot_code: string | null
  issue_scope: 'storyboard' | 'production' | 'editing'
  issue_code: 'content_mismatch' | 'action_mismatch' | 'composition_mismatch' | 'character_setup_mismatch' | 'identity_inconsistent' | 'visual_artifact' | 'composition_deviation' | 'text_error' | 'low_clarity' | 'style_mismatch' | 'exclude_asset' | 'shorten_clip' | 'reorder_clip' | 'replace_clip' | 'other'
  rationale: string
  status: string
  source_asset_state: string
  source_asset_row_version: number
  affected_downstream_node_keys: string[]
  draft_candidate_id: string | null
  resulting_candidate_id: string | null
  resulting_plan_version_id: string | null
  created_by: string
  created_at: string
  resolved_at: string | null
}

export interface AssetRevisionResult {
  request: AssetRevisionRequest
  next_action: { path: string; label: string; draft_candidate_id: string | null }
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

export interface ContactSheetEntityReference {
  role: 'character' | 'scene' | 'outfit' | 'product'
  is_primary: boolean
  entity_id: string
  entity_name: string
  entity_type: string
  entity_version_id: string
  version_number: number
  source_attachment_id: string | null
  source_filename: string | null
  source_mime_type: string | null
}

export interface ContactSheetEntry {
  number: number
  node_id: string | null
  node_key: string | null
  node_kind: string | null
  asset: ProductionAsset
  shot: {
    id: string
    shot_code: string
    sequence_number: number
    duration_ms: number
    shot_purpose: string
    face_visibility: string
    text_policy: string
    subject_motion: string
    composition: string
    action: string
    visual_prompt: string | null
    negative_prompt: string | null
    primary_reference_entity_version_id: string | null
  } | null
  route: {
    work_item_id: string
    work_item_status: string
    attempt_id: string
    attempt_number: number
    attempt_state: string
    provider: string
    adapter_kind: string | null
    provider_workflow_id: string | null
    provider_task_id: string | null
    request_fingerprint: string
  } | null
  dependencies: Array<{
    edge_id: string
    dependency_type: string
    input_slot: string | null
    parent_node_id: string
    parent_node_key: string
    registered_assets: Array<{ id: string; asset_type: string; role: string; state: string; content_hash: string | null }>
  }>
  entity_references: ContactSheetEntityReference[]
  frozen_reference_image: {
    role: 'primary'
    entity_version_id: string
    attachment_id: string
    uri: string
    mime_type: string
    byte_size: number
    content_hash: string
  } | null
}

export interface MaterialContactSheet {
  project_id: string
  project_title: string
  project_status: string
  generated_at: string
  snapshot: { id: string; snapshot_number: number; status: string; contract_hash: string; plan_version_id: string } | null
  entries: ContactSheetEntry[]
  output_gaps: Array<{ code: string; dag_node_id: string; node_key: string; work_item_id: string | null; message: string }>
  counts: Record<string, number>
  boundary: string
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
    workflow_slots: Array<{ id: string; key: string; display_name: string; operation_kind: string; supported_video_spec_ids: string[]; capability_tags: string[] }>
    audio_config: {
      id: string
      voice_presets: Array<{ key: string; display_name: string; provider_voice_id: string; description: string; preview_text: string }>
      default_voice_key: string | null
      speaking_rate_range: { min: number; max: number }
      speaking_rate_default: number
      volume_range: { min: number; max: number }
      volume_default: number
      duration_tolerance_ms: number
      loudness_target_lufs: number | null
      sample_rate: number
      channels: number
      format: string
    } | null
    pricing_catalogs: Array<{ id: string; key: string; display_name: string; currency: string; confirmation_threshold: number; effective_from: string | null; effective_to: string | null }>
  }>
  analyses: ProductionImpactAnalysis[]
  current_snapshot: ProductionSnapshot | null
  snapshots: ProductionSnapshot[]
  production_plan_candidates: ProductionPlanCandidate[]
  latest_production_planner_run: AgentRun | null
  next_action: { code: string; label: string; incurs_production_cost: boolean }
}

export interface ProductionPlanCandidate {
  id: string
  project_id: string
  plan_version_id: string
  production_config_version_id: string
  video_spec_version_id: string
  agent_run_id: string
  status: 'awaiting_review' | 'accepted' | 'rejected'
  proposed_assignments: Array<{
    shot_code: string
    keyframe_workflow_slot_version_id: string | null
    video_workflow_slot_version_id: string
    required_input_sources: string[]
    reason: string
  }>
  confirmed_assignments: Array<{
    shot_code: string
    keyframe_workflow_slot_version_id: string | null
    video_workflow_slot_version_id: string
  }> | null
  validation_errors: Array<{ code: string; path?: string; shot_code?: string }>
  row_version: number
  created_by: string
  created_at: string
  decided_at: string | null
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
  api_key?: string | null
  capabilities: string[]
  request_timeout_seconds: number
  poll_interval_seconds: number
  max_concurrency: number
}

export interface ModelConfigDraft {
  config_key: string
  display_name: string
  agent_role: 'creative' | 'planner' | 'director' | 'production_planner' | 'qc' | 'editor'
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
  voice_presets: Array<{
    key: string
    display_name: string
    provider_voice_id: string
    description: string
    preview_text: string
  }>
  default_voice_key?: string | null
  sample_rate: number
  channels: 1 | 2
  format: string
  speaking_rate_min: number
  speaking_rate_max: number
  speaking_rate_default: number
  volume_min: number
  volume_max: number
  volume_default: number
  duration_tolerance_ms: number
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
    unit: 'call' | 'output_second' | 'runtime_second'
    unit_price: number
    minimum_charge?: number | null
    estimated_runtime_seconds?: number | null
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

export interface ProviderReadinessItem {
  configuration_version_id: string
  configuration_display_name: string
  configuration_version_number: number
  provider_version_id: string
  provider_display_name: string
  adapter_kind: string
  capabilities: string[]
  adapter_registered: boolean
  external: boolean | null
  execution_enabled: boolean | null
  credential_required: boolean | null
  api_key_state: 'not_required' | 'missing' | 'configured'
  configuration_ready: boolean
  configuration_issue_count: number
  configuration_issue_codes: string[]
  status: 'connected' | 'adapter_not_connected' | 'configuration_not_ready' | 'execution_disabled' | 'credential_not_ready'
  next_action: 'connect_adapter' | 'revise_configuration' | 'configure_credential' | 'enable_execution' | 'ready'
}

export interface ProviderReadiness {
  network_probe_performed: boolean
  external_execution_enabled: boolean
  providers: ProviderReadinessItem[]
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
  track_config: TimelineTrackConfig
  validation_report: Array<{ code: string; path: string; message: string; evidence: Record<string, unknown> }>
  contract_hash: string | null
  row_version: number
  created_by: string
  created_at: string
  validated_at: string | null
  confirmed_at: string | null
  items: TimelineItem[]
}

export interface TimelineTrackConfig {
  audio_enabled: boolean
  subtitle_enabled: boolean
  pixels_per_second: number
  snap_interval_ms: number
  audio_mastering: {
    loudness_target_lufs: number
    true_peak_limit_dbtp: number
    clipping_control: 'limiter'
  }
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

export interface AudioWaveform {
  schema_version: 'audio-waveform-cache.v1'
  asset_id: string
  content_hash: string
  sample_rate: number
  channels: number
  duration_ms: number
  bin_count: number
  peaks: number[]
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
  latest_editor_run: AgentRun | null
  next_action: { code: string; label: string }
}

export interface DeliveryAttempt {
  id: string
  project_id: string
  snapshot_id: string
  timeline_id: string
  attempt_number: number
  status: 'authorized' | 'queued' | 'rendering' | 'output_registered' | 'verified' | 'blocked'
  execution_kind: 'external_upload' | 'local_ffmpeg'
  request_manifest: Record<string, unknown>
  request_fingerprint: string
  work_item_id: string | null
  final_asset_id: string | null
  final_asset: ProductionAsset | null
  error_code: string | null
  error_detail: Record<string, unknown> | null
  row_version: number
  created_by: string
  created_at: string
  render_started_at: string | null
  render_finished_at: string | null
  output_registered_at: string | null
  verified_at: string | null
}

export interface DeliveryWorkspace {
  project_id: string
  project_title: string
  project_status: ProjectStatus
  active_snapshot_id: string | null
  delivery_asset_id: string | null
  confirmed_timeline: {
    id: string
    version_number: number
    status: 'confirmed' | 'exported'
    contract_hash: string
    output_spec: Record<string, unknown>
    confirmed_at: string
  } | null
  delivery_methods: Array<{
    kind: 'external_upload' | 'local_ffmpeg'
    label: string
    available: boolean
    reason_code: string | null
    reason: string | null
    renderer_version: string | null
  }>
  attempts: DeliveryAttempt[]
  next_action: { code: string; label: string }
}

export interface ControlNextAction {
  code: string
  label: string
  path: string
  incurs_production_cost: boolean
  confirmation_level: 'none' | 'normal' | 'high'
}

export interface ProjectControlSummary {
  project_id: string
  title: string
  core_topic: string
  duration_seconds: number
  aspect_ratio: string
  audio_mode: string
  persisted_status: ProjectStatus
  state_row_version: number
  state_changed_at: string
  state_actor_type: string
  state_changed_by: string
  state_trigger: string
  state_reason_code: string | null
  blocked_from_state: string | null
  blocked_responsible_aggregate_type: string | null
  blocked_responsible_aggregate_id: string | null
  blocked_allowed_commands: string[]
  blocked_at: string | null
  archived_at: string | null
  archived_by: string | null
  evaluated_stage: 'requirements' | 'planning' | 'production_preparation' | 'production' | 'quality_review' | 'editing' | 'delivery' | 'completed'
  stage_label: string
  active_plan_version: number | null
  active_snapshot_number: number | null
  active_snapshot_status: string | null
  work_counts: Record<string, number>
  asset_counts: Record<string, number>
  blocker_count: number
  latest_event_at: string | null
  updated_at: string
  next_action: ControlNextAction
}

export interface ProjectControl extends ProjectControlSummary {
  active_plan: { id: string; version_number: number; status: string; requirement_version_id: string; contract_schema_version: string; confirmed_at: string } | null
  production_basis: {
    requirement: { id: string; version_number: number; fields: Record<string, unknown>; created_at: string }
    creative_brief: CreativeBrief
    plan: { id: string; version_number: number; contract_schema_version: string; shot_count: number; confirmed_at: string; confirmed_by: string }
  } | null
  active_snapshot: { id: string; snapshot_number: number; status: string; contract_hash: string; cost_status: string; estimated_cost: number | null; currency: string | null; estimated_call_count: number } | null
  delivery: { id: string; status: string; timeline_id: string; request_fingerprint: string; final_asset_id: string | null; error_code: string | null } | null
  costs: Array<{ currency: string; estimated_confirmed: number; charged_confirmed: number; adjusted_confirmed: number; refunded_confirmed: number; pending_event_count: number }>
  blockers: Array<{ source_type: string; source_id: string; code: string; message: string; evidence: Record<string, unknown>; affected_node_keys: string[] }>
  routes: Array<{ work_item_id: string; work_item_status: string; node_key: string | null; attempt_id: string; attempt_number: number; attempt_state: string; provider: string; adapter_kind: string | null; provider_workflow_id: string | null; provider_task_id: string | null; request_fingerprint: string; error_code: string | null }>
  recent_events: AuditEvent[]
}

export interface AuditEvent {
  sequence: number
  event_id: string
  snapshot_id: string | null
  event_type: string
  aggregate_type: string
  aggregate_id: string
  causation_id: string | null
  correlation_id: string
  actor_type: string
  actor_id: string
  schema_version: number
  message: string
  data: Record<string, unknown>
  created_at: string
}

export interface AuditCostEvent {
  id: string
  snapshot_id: string
  work_attempt_id: string | null
  provider: string
  provider_operation: string
  kind: 'estimated' | 'charged' | 'adjusted' | 'refunded'
  amount: number
  currency: string
  provider_reference: string | null
  status: 'pending' | 'confirmed' | 'disputed'
  occurred_at: string
}

export interface ProjectAuditLedger {
  project_id: string
  project_title: string
  event_limit: number
  before_sequence: number | null
  has_more_events: boolean
  next_before_sequence: number | null
  events: AuditEvent[]
  cost_summaries: ProjectControl['costs']
  cost_events: AuditCostEvent[]
}

export interface RegistryAttachment {
  id: string
  original_filename: string
  mime_type: string
  byte_size: number
  content_hash: string
  verification_status: string
  created_at: string
}

export interface RegistryEntityVersion {
  id: string
  version_number: number
  attributes: Record<string, unknown>
  status: string
  is_active: boolean
  created_by: string
  created_at: string
  source_attachment: RegistryAttachment | null
  bindings: Array<{ id: string; binding_type: string; status: string; confirmed_by: string; confirmed_at: string }>
  snapshot_references: Array<{ snapshot_id: string; snapshot_number: number; snapshot_status: string; role: string }>
  shot_references: Array<{ plan_version_id: string; plan_version_number: number; shot_id: string; shot_code: string; role: string }>
}

export interface RegistryEntity {
  id: string
  project_id: string
  project_title: string
  entity_type: 'character' | 'outfit' | 'scene' | 'product' | 'voice'
  display_name: string
  status: string
  created_at: string
  active_version_id: string | null
  versions: RegistryEntityVersion[]
}

export interface EntityRegistry {
  projects: Array<{ id: string; title: string; status: string }>
  counts: Record<string, number>
  entities: RegistryEntity[]
}
