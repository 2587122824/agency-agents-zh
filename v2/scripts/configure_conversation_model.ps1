param(
  [string]$BaseUrl = "http://127.0.0.1:8766/api/v1",
  [string]$SourceConfigId = "production_config_5864d565df0148418b12409d9b991a08"
)

$ErrorActionPreference = "Stop"

function Post-Json([string]$Uri, [hashtable]$Body) {
  $Json = $Body | ConvertTo-Json -Depth 40 -Compress
  $Bytes = [Text.Encoding]::UTF8.GetBytes($Json)
  Invoke-RestMethod -Method Post -ContentType "application/json; charset=utf-8" -Uri $Uri -Body $Bytes
}

$Versions = Invoke-RestMethod "$BaseUrl/system-config/versions"
$SourceSummary = $Versions | Where-Object { $_.id -eq $SourceConfigId } | Select-Object -First 1
$ExistingPublished = $Versions |
  Where-Object { $_.config_key -eq $SourceSummary.config_key -and $_.status -eq "published" } |
  Sort-Object version_number -Descending |
  ForEach-Object { Invoke-RestMethod "$BaseUrl/system-config/versions/$($_.id)" } |
  Where-Object { $_.components | Where-Object { $_.component_type -eq "provider" -and $_.key -eq "deepseek-creative-api" } } |
  Select-Object -First 1
if ($ExistingPublished) {
  [pscustomobject]@{
    id = $ExistingPublished.id; version = $ExistingPublished.version_number; status = $ExistingPublished.status
    row_version = $ExistingPublished.row_version; component_count = $ExistingPublished.components.Count
  }
  return
}
$DraftSummary = $Versions |
  Where-Object { $_.config_key -eq $SourceSummary.config_key -and $_.status -in @("draft", "validation_failed") } |
  Sort-Object version_number -Descending |
  Select-Object -First 1
if ($DraftSummary) {
  $Version = Invoke-RestMethod "$BaseUrl/system-config/versions/$($DraftSummary.id)"
} else {
  $Version = Post-Json "$BaseUrl/system-config/versions/${SourceConfigId}:clone-draft" @{
    command_id = [guid]::NewGuid().ToString()
    actor_id = "local-user"
    display_name = "V1 导入配置 v6（创作对话模型）"
  }
}

$Groups = $Version.components | Group-Object component_type -AsHashTable -AsString
$Providers = @($Groups["provider"])
$Models = @($Groups["model"])
$Videos = @($Groups["video_spec"])
$Workflows = @($Groups["workflow_slot"])
$ProviderKeys = @{}; $Providers | ForEach-Object { $ProviderKeys[$_.id] = $_.key }
$VideoKeys = @{}; $Videos | ForEach-Object { $VideoKeys[$_.id] = $_.key }
$WorkflowKeys = @{}; $Workflows | ForEach-Object { $WorkflowKeys[$_.id] = $_.key }

$ProviderDrafts = @($Providers | ForEach-Object { @{
  provider_key = $_.key; display_name = $_.display_name
  adapter_kind = $_.details.adapter_kind; region = $_.details.region
  base_url = $_.details.base_url; credential_ref = $_.details.credential_ref
  capabilities = @($_.details.capabilities)
  request_timeout_seconds = $_.details.request_timeout_seconds
  poll_interval_seconds = $_.details.poll_interval_seconds
  max_concurrency = $_.details.max_concurrency
} })
if (-not ($ProviderDrafts | Where-Object { $_.provider_key -eq "deepseek-creative-api" })) {
  $ProviderDrafts += @{
    provider_key = "deepseek-creative-api"; display_name = "DeepSeek 创作模型"
    adapter_kind = "openai_compatible"; region = $null
    base_url = "https://api.deepseek.com/v1"; credential_ref = "env://V2_DEEPSEEK_API_KEY"
    capabilities = @("text_generation"); request_timeout_seconds = 900
    poll_interval_seconds = 5; max_concurrency = 1
  }
}

$ModelDrafts = @($Models | ForEach-Object { @{
  config_key = $_.key; display_name = $_.display_name; agent_role = $_.details.agent_role
  provider_key = "deepseek-creative-api"; provider_model_id = "deepseek-v4-flash"
  input_contract_version = "v2.creative-dialogue-input.v1"
  output_schema_version = "v2.creative-dialogue-output.v1"
  prompt_contract_version = "v2.creative-dialogue-prompt.v1"
  context_window = $_.details.context_window; max_output_tokens = 4096
  sampling = @{ temperature = 0.2 }; capability_tags = @("text_generation", "json_object")
} })

$VideoDrafts = @($Videos | ForEach-Object { @{
  spec_key = $_.key; display_name = $_.display_name; width = $_.details.width; height = $_.details.height
  aspect_ratio = $_.details.aspect_ratio; fps = $_.details.fps
  duration_min_seconds = $_.details.duration_min_seconds; duration_max_seconds = $_.details.duration_max_seconds
  frame_count_rule = $_.details.frame_count_rule; container = $_.details.container
  video_codec = $_.details.video_codec; pixel_format = $_.details.pixel_format
  bitrate_policy = $_.details.bitrate_policy; safe_crop = $_.details.safe_crop
} })

$WorkflowDrafts = @($Workflows | ForEach-Object {
  $Workflow = $_
  $Bindings = @($Workflow.details.node_info_list | ForEach-Object {
    $Source = $_.value_source
    $ValueType = $_.value_type
    switch ($Source) {
      "{{prompt}}" { $Source = "shot.visual_prompt" }
      "{{negative_prompt}}" { $Source = "shot.negative_prompt" }
      "{{reference_image}}" { $Source = if ($Workflow.details.operation_kind -eq "video_generation") { "source_image" } else { "reference_image.primary" } }
      "{{has_reference_image}}" { $Source = "reference_image.present"; $ValueType = "boolean" }
      "{{long_side}}" { $Source = "video_spec.long_side" }
      "{{frame_count}}" { $Source = "video_spec.frame_count" }
      "{{fps}}" { $Source = "video_spec.fps" }
      "{{seed}}" { $Source = "seed" }
      "false" { $Source = "literal:false" }
      "video/ltx2.3-i2v-first-frame" { $Source = 'literal:"video/ltx2.3-i2v-first-frame"' }
      "all_in_one_image" { $Source = 'literal:"all_in_one_image"' }
      "1080" { $Source = "literal:1080" }
      "1920" { $Source = "literal:1920" }
    }
    if ($Source -eq "reference_image.present") { $ValueType = "boolean" }
    @{ node_id = $_.node_id; field_path = $_.field_path; value_source = $Source; value_type = $ValueType; required = $_.required }
  })
  @{
    slot_key = $Workflow.key; display_name = $Workflow.display_name
    operation_kind = $Workflow.details.operation_kind
    provider_key = $ProviderKeys[[string]$Workflow.details.provider_config_version_id]
    provider_workflow_id = $Workflow.details.provider_workflow_id
    provider_workflow_version = $Workflow.details.provider_workflow_version
    model_config_key = $null; input_schema_version = $Workflow.details.input_schema_version
    output_schema_version = $Workflow.details.output_schema_version; node_info_list = $Bindings
    supported_video_spec_keys = @($Workflow.details.supported_video_spec_ids | ForEach-Object { $VideoKeys[[string]$_] })
    capability_tags = @($Workflow.details.capability_tags)
  }
})

$Audio = @($Groups["audio"])[0]
$Storage = @($Groups["storage"])[0]
$Pricing = @($Groups["pricing_catalog"])[0]
$TtsWorkflowKey = $null
if ($Audio.details.tts_workflow_slot_version_id) {
  $TtsWorkflowKey = $WorkflowKeys[[string]$Audio.details.tts_workflow_slot_version_id]
}
$AudioDraft = @{
  config_key = $Audio.key; display_name = $Audio.display_name; supported_modes = @($Audio.details.supported_modes)
  tts_workflow_slot_key = $TtsWorkflowKey
  default_voice_entity_version_id = $Audio.details.default_voice_entity_version_id
  sample_rate = $Audio.details.sample_rate; channels = $Audio.details.channels; format = $Audio.details.format
  speaking_rate_min = $Audio.details.speaking_rate_range.min; speaking_rate_max = $Audio.details.speaking_rate_range.max
  loudness_target = $Audio.details.loudness_target
  temporary_upload_policy_version_id = $Audio.details.temporary_upload_policy_version_id
}
$StorageDraft = @{
  policy_key = $Storage.key; display_name = $Storage.display_name; backend_kind = $Storage.details.backend_kind
  region_ref = $Storage.details.region_ref; bucket_ref = $Storage.details.bucket_ref; credential_ref = $Storage.details.credential_ref
  allowed_mime_types = @($Storage.details.allowed_mime_types); max_file_size_bytes = $Storage.details.max_file_size_bytes
  public_url_policy = $Storage.details.public_url_policy; lifecycle_days = $Storage.details.lifecycle_days
  local_root_ref = $Storage.details.local_root_ref
}
$PricingDraft = @{
  catalog_key = $Pricing.key; display_name = $Pricing.display_name; currency = $Pricing.details.currency
  confirmation_threshold = $Pricing.details.confirmation_threshold
  effective_from = $Pricing.details.effective_from; effective_to = $Pricing.details.effective_to
  rules = @($Pricing.details.rules | ForEach-Object { @{
    workflow_slot_key = $WorkflowKeys[[string]$_.workflow_slot_version_id]
    unit = $_.unit; unit_price = $_.unit_price; minimum_charge = $_.minimum_charge
    estimated_runtime_seconds = $_.estimated_runtime_seconds
  } })
}

$Draft = @{
  config_key = $Version.config_key; display_name = "V1 导入配置 v6（创作对话模型）"
  description = "新增独立 DeepSeek 创作对话供应商；生产工作流 ID 与媒体规格保持不变，节点来源更新为 V2 严格合同。"
  providers = $ProviderDrafts; models = $ModelDrafts; workflow_slots = $WorkflowDrafts
  video_specs = $VideoDrafts; audio = $AudioDraft; storage = $StorageDraft; pricing = $PricingDraft
}

$Revised = Post-Json "$BaseUrl/system-config/versions/$($Version.id):revise" @{
  command_id = [guid]::NewGuid().ToString(); actor_id = "local-user"
  expected_row_version = $Version.row_version; configuration = $Draft
}
$Validated = Post-Json "$BaseUrl/system-config/versions/$($Version.id):validate" @{
  command_id = [guid]::NewGuid().ToString(); actor_id = "local-user"; expected_row_version = $Revised.row_version
}
if ($Validated.status -ne "ready") {
  $Validated.validation_report | ConvertTo-Json -Depth 10
  throw "Configuration validation failed."
}
$Published = Post-Json "$BaseUrl/system-config/versions/$($Version.id):publish" @{
  command_id = [guid]::NewGuid().ToString(); actor_id = "local-user"
  expected_row_version = $Validated.row_version; confirm_high_risk_changes = $true
}
[pscustomobject]@{
  id = $Published.id; version = $Published.version_number; status = $Published.status
  row_version = $Published.row_version; component_count = $Published.components.Count
}
