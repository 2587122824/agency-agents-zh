import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, BadgeCheck, Boxes, Check, ChevronDown, ChevronRight, CircleDollarSign, Copy, Database, FileCheck2, History, KeyRound, LockKeyhole, PlugZap, Plus, RefreshCw, Save, Server, Settings2, ShieldCheck, Trash2, Unplug, Workflow, X } from 'lucide-react'
import { FormEvent, useEffect, useMemo, useState } from 'react'

import { api } from '../api/client'
import type { ConfigurationComponent, ModelConfigDraft, NodeBindingDraft, ProviderConfigDraft, ProviderReadinessItem, SystemConfigurationDraft, SystemConfigurationVersion, VideoSpecDraft, WorkflowSlotDraft } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './SettingsPage.module.css'

const generatedKey = (prefix: string) => `${prefix}_${crypto.randomUUID()}`
const providerDraft = (): ProviderConfigDraft => ({ provider_key: generatedKey('provider'), display_name: '', adapter_kind: '', base_url: '', capabilities: [''], request_timeout_seconds: 60, poll_interval_seconds: 5, max_concurrency: 1 })
const modelDraft = (): ModelConfigDraft => ({ config_key: generatedKey('model'), display_name: '', agent_role: 'creative', provider_key: '', provider_model_id: '', input_contract_version: '', output_schema_version: '', prompt_contract_version: '', sampling: {}, capability_tags: [] })
const workflowDraft = (): WorkflowSlotDraft => ({ slot_key: generatedKey('workflow'), display_name: '', operation_kind: '', provider_key: '', provider_workflow_id: '', input_schema_version: '', output_schema_version: '', node_info_list: [{ node_id: '', field_path: '', value_source: '', value_type: 'string', required: true }], supported_video_spec_keys: [], capability_tags: [] })
const videoDraft = (): VideoSpecDraft => ({ spec_key: generatedKey('video'), display_name: '', width: 480, height: 848, aspect_ratio: '9:16', fps: 24, duration_min_seconds: 1, duration_max_seconds: 30, frame_count_rule: { type: 'duration_times_fps' }, container: 'mp4', video_codec: 'h264', pixel_format: 'yuv420p', bitrate_policy: {}, safe_crop: {} })
const emptyDraft = (): SystemConfigurationDraft => ({
  config_key: generatedKey('config'), display_name: '', description: '', providers: [providerDraft()], models: [], workflow_slots: [workflowDraft()], video_specs: [videoDraft()],
  audio: { config_key: generatedKey('audio'), display_name: '', supported_modes: ['off'], sample_rate: 48000, channels: 2, format: 'wav', speaking_rate_min: 0.8, speaking_rate_max: 1.2 },
  storage: { policy_key: generatedKey('storage'), display_name: '', backend_kind: 'local', allowed_mime_types: ['image/png', 'video/mp4', 'audio/wav'], max_file_size_bytes: 524288000, public_url_policy: 'none', local_root_ref: '' },
})

const componentLabels: Record<string, string> = { provider: '服务供应商', model: '模型', workflow_slot: '工作流槽位', video_spec: '视频规格', audio: '音频策略', storage: '存储策略', pricing_catalog: '价格目录' }
const statusLabels: Record<string, string> = { draft: '草稿', validating: '校验中', validation_failed: '校验失败', ready: '可发布', published: '已发布', retired: '已停用' }
const readinessLabels = { connected: '执行能力已就绪', adapter_not_connected: '执行组件尚未接通', configuration_not_ready: '生成配置需要更新', execution_disabled: '真实执行授权未开启', credential_not_ready: '后端密钥尚未就绪' } as const
const credentialLabels = { not_configured: '未登记后端凭据引用', unsupported_reference: '凭据引用格式暂不支持', not_authorized: '后端尚未授权读取该凭据', missing: '后端环境中没有对应凭据', available: '后端凭据已就绪' } as const
const nextActionLabels = { connect_adapter: '先接入对应的执行组件', revise_configuration: '复制此版本并更新工作流输入', configure_credential: '在后端配置密钥并加入读取许可', enable_execution: '确认真实测试前再开启执行授权', ready: '可以用于新生产任务' } as const
const valueTypeLabels: Record<NodeBindingDraft['value_type'], string> = { string: '文本', integer: '整数', number: '数字', boolean: '开关', image: '图片', audio: '音频', json: '结构化数据' }
const valueSourceOptions = [
  { value: 'shot.visual_prompt', label: '画面生成描述' },
  { value: 'shot.negative_prompt', label: '避免内容' },
  { value: 'shot.action', label: '镜头动作' },
  { value: 'shot.composition', label: '镜头构图' },
  { value: 'shot.duration_ms', label: '镜头时长' },
  { value: 'shot.face_visibility', label: '人物露脸要求' },
  { value: 'shot.subject_motion', label: '主体运动幅度' },
  { value: 'shot.text_policy', label: '画面文字策略' },
  { value: 'duration_ms', label: '生成时长' },
  { value: 'video_spec.width', label: '画面宽度' },
  { value: 'video_spec.height', label: '画面高度' },
  { value: 'video_spec.fps', label: '帧率' },
  { value: 'video_spec.long_side', label: '画面长边' },
  { value: 'video_spec.frame_count', label: '总帧数' },
  { value: 'seed', label: '随机种子' },
] as const

function sourceOptions(operationKind: string) {
  if (operationKind === 'video_generation') return [{ value: 'source_image', label: '上一步生成的关键帧' }, ...valueSourceOptions]
  if (operationKind === 'image_generation') return [
    { value: 'reference_image.primary', label: '分镜主参考图' },
    { value: 'reference_image.present', label: '是否选择了主参考图' },
    ...valueSourceOptions,
  ]
  return valueSourceOptions
}

function literalDisplayValue(binding: NodeBindingDraft) {
  const raw = binding.value_source.slice('literal:'.length)
  try {
    const parsed = JSON.parse(raw)
    return typeof parsed === 'string' ? parsed : JSON.stringify(parsed)
  } catch {
    return raw
  }
}

function literalSource(value: string, valueType: NodeBindingDraft['value_type']) {
  if (['string', 'image', 'audio'].includes(valueType)) return `literal:${JSON.stringify(value)}`
  if (valueType === 'boolean') return `literal:${value === 'true' ? 'true' : 'false'}`
  return `literal:${value}`
}

function NodeBindingEditor({ binding, operationKind, onChange, onRemove, removable }: { binding: NodeBindingDraft; operationKind: string; onChange: (binding: NodeBindingDraft) => void; onRemove: () => void; removable: boolean }) {
  const options = sourceOptions(operationKind)
  const supported = options.some(option => option.value === binding.value_source)
  const isLiteral = binding.value_source.startsWith('literal:')
  const sourceMode = isLiteral ? '__literal__' : binding.value_source
  const fixedValue = isLiteral ? literalDisplayValue(binding) : ''
  const updateType = (valueType: NodeBindingDraft['value_type']) => onChange({ ...binding, value_type: valueType, value_source: isLiteral ? literalSource(fixedValue, valueType) : binding.value_source })
  return <div className={styles.bindingRow}>
    <label><span>节点 ID</span><input required placeholder="例如 2483" value={binding.node_id} onChange={event => onChange({ ...binding, node_id: event.target.value })} /></label>
    <label><span>输入字段</span><input required placeholder="例如 text" value={binding.field_path} onChange={event => onChange({ ...binding, field_path: event.target.value })} /></label>
    <label className={styles.sourceField}><span>输入内容</span><select aria-label="输入内容来源" value={sourceMode} onChange={event => onChange({ ...binding, value_source: event.target.value === '__literal__' ? literalSource('', binding.value_type) : event.target.value })}><option value="">请选择</option>{!supported && !isLiteral && binding.value_source && <option value={binding.value_source}>旧格式（需要更换）</option>}{options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}<option value="__literal__">固定值</option></select>{isLiteral && (binding.value_type === 'boolean' ? <select aria-label="固定开关值" value={fixedValue || 'false'} onChange={event => onChange({ ...binding, value_source: literalSource(event.target.value, binding.value_type) })}><option value="true">开启</option><option value="false">关闭</option></select> : <input aria-label="固定值" required placeholder={binding.value_type === 'json' ? '填写 JSON' : '填写固定值'} value={fixedValue} onChange={event => onChange({ ...binding, value_source: literalSource(event.target.value, binding.value_type) })} />)}</label>
    <label><span>内容类型</span><select aria-label="内容类型" value={binding.value_type} onChange={event => updateType(event.target.value as NodeBindingDraft['value_type'])}>{Object.entries(valueTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label className={styles.requiredField}><input type="checkbox" checked={binding.required} onChange={event => onChange({ ...binding, required: event.target.checked })} /><span>必填</span></label>
    {removable && <button type="button" className="iconButton" title="删除节点映射" onClick={onRemove}><Trash2 size={13} /></button>}
  </div>
}

function ReadinessChecks({ provider }: { provider: ProviderReadinessItem }) {
  const checks = [
    { label: '执行组件', ready: provider.adapter_registered },
    { label: '生成配置', ready: provider.configuration_ready },
    { label: '后端密钥', ready: !provider.credential_required || provider.credential_state === 'available' },
    { label: '执行授权', ready: !provider.external || provider.execution_enabled === true },
  ]
  return <div className={styles.readinessChecks}>{checks.map(item => <span key={item.label} data-ready={item.ready}>{item.ready ? <Check size={12} /> : <X size={12} />}{item.label}</span>)}</div>
}

function groupComponents(components: ConfigurationComponent[]) {
  return components.reduce<Record<string, ConfigurationComponent[]>>((result, item) => {
    ;(result[item.component_type] ??= []).push(item)
    return result
  }, {})
}

function draftFromVersion(version: SystemConfigurationVersion): SystemConfigurationDraft {
  const groups = groupComponents(version.components)
  const providers = groups.provider ?? []
  const models = groups.model ?? []
  const videos = groups.video_spec ?? []
  const workflows = groups.workflow_slot ?? []
  const providerKeys = new Map(providers.map(item => [item.id, item.key]))
  const modelKeys = new Map(models.map(item => [item.id, item.key]))
  const videoKeys = new Map(videos.map(item => [item.id, item.key]))
  const workflowKeys = new Map(workflows.map(item => [item.id, item.key]))
  const audio = (groups.audio ?? [])[0]
  const storage = (groups.storage ?? [])[0]
  const pricing = (groups.pricing_catalog ?? [])[0]
  return {
    config_key: version.config_key,
    display_name: version.display_name,
    description: version.description,
    providers: providers.map(item => ({ provider_key: item.key, display_name: item.display_name, ...(item.details as unknown as Omit<ProviderConfigDraft, 'provider_key' | 'display_name'>) })),
    models: models.map(item => ({
      config_key: item.key, display_name: item.display_name,
      agent_role: item.details.agent_role as ModelConfigDraft['agent_role'],
      provider_key: providerKeys.get(String(item.details.provider_config_version_id)) ?? '',
      provider_model_id: String(item.details.provider_model_id ?? ''), input_contract_version: String(item.details.input_contract_version ?? ''),
      output_schema_version: String(item.details.output_schema_version ?? ''), prompt_contract_version: String(item.details.prompt_contract_version ?? ''),
      context_window: item.details.context_window as number | null, max_output_tokens: item.details.max_output_tokens as number | null,
      sampling: (item.details.sampling ?? {}) as Record<string, unknown>, capability_tags: (item.details.capability_tags ?? []) as string[],
    })),
    workflow_slots: workflows.map(item => ({
      slot_key: item.key, display_name: item.display_name, operation_kind: String(item.details.operation_kind ?? ''),
      provider_key: providerKeys.get(String(item.details.provider_config_version_id)) ?? '', provider_workflow_id: String(item.details.provider_workflow_id ?? ''),
      provider_workflow_version: item.details.provider_workflow_version as string | null, model_config_key: modelKeys.get(String(item.details.model_config_version_id)) ?? null,
      input_schema_version: String(item.details.input_schema_version ?? ''), output_schema_version: String(item.details.output_schema_version ?? ''),
      node_info_list: item.details.node_info_list as WorkflowSlotDraft['node_info_list'],
      supported_video_spec_keys: ((item.details.supported_video_spec_ids ?? []) as string[]).map(id => videoKeys.get(id) ?? ''),
      capability_tags: (item.details.capability_tags ?? []) as string[],
    })),
    video_specs: videos.map(item => ({ spec_key: item.key, display_name: item.display_name, ...(item.details as unknown as Omit<VideoSpecDraft, 'spec_key' | 'display_name'>) })),
    audio: {
      config_key: audio.key, display_name: audio.display_name, supported_modes: audio.details.supported_modes as Array<'off' | 'voiceover'>,
      tts_workflow_slot_key: workflowKeys.get(String(audio.details.tts_workflow_slot_version_id)) ?? null,
      default_voice_entity_version_id: audio.details.default_voice_entity_version_id as string | null, sample_rate: Number(audio.details.sample_rate),
      channels: Number(audio.details.channels) as 1 | 2, format: String(audio.details.format),
      speaking_rate_min: Number((audio.details.speaking_rate_range as { min: number }).min), speaking_rate_max: Number((audio.details.speaking_rate_range as { max: number }).max),
      loudness_target: audio.details.loudness_target as number | null, temporary_upload_policy_version_id: audio.details.temporary_upload_policy_version_id as string | null,
    },
    storage: { policy_key: storage.key, display_name: storage.display_name, ...(storage.details as unknown as Omit<SystemConfigurationDraft['storage'], 'policy_key' | 'display_name'>) },
    pricing: pricing ? {
      catalog_key: pricing.key,
      display_name: pricing.display_name,
      currency: String(pricing.details.currency),
      confirmation_threshold: Number(pricing.details.confirmation_threshold),
      effective_from: pricing.details.effective_from as string | null,
      effective_to: pricing.details.effective_to as string | null,
      rules: (pricing.details.rules as Array<Record<string, unknown>>).map(rule => ({
        workflow_slot_key: workflowKeys.get(String(rule.workflow_slot_version_id)) ?? '',
        unit: rule.unit as 'call' | 'output_second' | 'runtime_second',
        unit_price: Number(rule.unit_price),
        minimum_charge: rule.minimum_charge === null ? null : Number(rule.minimum_charge),
        estimated_runtime_seconds: rule.estimated_runtime_seconds == null ? null : Number(rule.estimated_runtime_seconds),
      })),
    } : null,
  }
}

function ComponentList({ components }: { components: ConfigurationComponent[] }) {
  const groups = groupComponents(components)
  return <div className={styles.componentGroups}>{Object.entries(groups).map(([type, items]) => <section key={type}><header><span>{componentLabels[type] ?? type}</span><b>{items?.length ?? 0}</b></header>{items?.map(item => <article key={item.id}><div><strong>{item.display_name}</strong><small>{item.key} · v{item.version_number}</small></div><em data-status={item.status}>{statusLabels[item.status] ?? item.status}</em></article>)}</section>)}</div>
}

export function SettingsPage() {
  const client = useQueryClient()
  const versions = useQuery({ queryKey: ['system-configurations'], queryFn: api.systemConfigurations })
  const readiness = useQuery({ queryKey: ['provider-readiness'], queryFn: api.providerReadiness })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<SystemConfigurationDraft>(emptyDraft)
  const [confirmPublish, setConfirmPublish] = useState(false)
  const [confirmRetire, setConfirmRetire] = useState(false)
  const currentVersion = useMemo(() => versions.data?.find(item => item.status === 'published') ?? versions.data?.[0] ?? null, [versions.data])
  const activeDraft = useMemo(() => {
    if (!currentVersion) return null
    return versions.data?.find(item => item.config_key === currentVersion.config_key && item.version_number > currentVersion.version_number && !['published', 'retired'].includes(item.status)) ?? null
  }, [currentVersion, versions.data])
  const historyVersions = useMemo(() => versions.data?.filter(item => item.id !== currentVersion?.id && item.id !== activeDraft?.id) ?? [], [activeDraft?.id, currentVersion?.id, versions.data])
  const currentReadiness = useMemo(() => readiness.data?.providers.filter(item => item.configuration_version_id === currentVersion?.id) ?? [], [currentVersion?.id, readiness.data?.providers])
  const selected = useQuery({ queryKey: ['system-configuration', selectedId], queryFn: () => api.systemConfiguration(selectedId!), enabled: Boolean(selectedId) })
  const diff = useQuery({
    queryKey: ['system-configuration-diff', selected.data?.id, selected.data?.supersedes_version_id],
    queryFn: () => api.systemConfigurationDiff(selected.data!.id, selected.data!.supersedes_version_id!),
    enabled: Boolean(selected.data?.supersedes_version_id),
  })
  useEffect(() => { if (!selectedId && currentVersion) setSelectedId(currentVersion.id) }, [currentVersion, selectedId])
  const refresh = async (version?: SystemConfigurationVersion) => { await client.invalidateQueries({ queryKey: ['system-configurations'] }); if (version) { setSelectedId(version.id); client.setQueryData(['system-configuration', version.id], version) } else await client.invalidateQueries({ queryKey: ['system-configuration', selectedId] }) }
  const create = useMutation({ mutationFn: () => api.createSystemConfiguration(draft), onSuccess: async data => { setEditing(false); await refresh(data) } })
  const revise = useMutation({ mutationFn: () => api.reviseSystemConfiguration(selected.data!.id, selected.data!.row_version, draft), onSuccess: async data => { setEditing(false); await refresh(data) } })
  const validate = useMutation({ mutationFn: () => api.validateSystemConfiguration(selected.data!.id, selected.data!.row_version), onSuccess: refresh })
  const publish = useMutation({ mutationFn: () => api.publishSystemConfiguration(selected.data!.id, selected.data!.row_version), onSuccess: async data => { setConfirmPublish(false); await refresh(data) } })
  const retire = useMutation({ mutationFn: () => api.retireSystemConfiguration(selected.data!.id, selected.data!.row_version), onSuccess: async data => { setConfirmRetire(false); await refresh(data) } })
  const prepareEdit = useMutation({
    mutationFn: async () => {
      if (activeDraft) return api.systemConfiguration(activeDraft.id)
      if (!currentVersion) throw new Error('当前没有可编辑的生产配置。')
      return api.cloneSystemConfiguration(currentVersion.id, currentVersion.display_name)
    },
    onSuccess: async data => {
      client.setQueryData(['system-configuration', data.id], data)
      setSelectedId(data.id)
      setDraft(draftFromVersion(data))
      setEditing(true)
      await client.invalidateQueries({ queryKey: ['system-configurations'] })
    },
  })
  const mutationError = create.error || revise.error || validate.error || publish.error || retire.error || prepareEdit.error
  const busy = create.isPending || revise.isPending || validate.isPending || publish.isPending || retire.isPending || prepareEdit.isPending
  const componentCount = useMemo(() => selected.data?.components.length ?? 0, [selected.data])

  function beginCreate() { setDraft(emptyDraft()); setSelectedId(null); setEditing(true) }
  function beginEdit() { if (selected.data) { setDraft(draftFromVersion(selected.data)); setEditing(true) } }
  function updateList<T>(key: 'providers' | 'models' | 'workflow_slots' | 'video_specs', index: number, value: T) { setDraft(current => ({ ...current, [key]: current[key].map((item, itemIndex) => itemIndex === index ? value : item) })) }
  function removeList(key: 'providers' | 'models' | 'workflow_slots' | 'video_specs', index: number) { setDraft(current => ({ ...current, [key]: current[key].filter((_, itemIndex) => itemIndex !== index) })) }
  function submit(event: FormEvent) { event.preventDefault(); selected.data ? revise.mutate() : create.mutate() }

  return <><PageHeader eyebrow="SYSTEM AUTHORITY" title="系统配置" description="日常只维护当前生产配置；历史版本由系统保留，用于项目追溯。" actions={<><button className="secondaryButton" onClick={() => client.invalidateQueries()}><RefreshCw size={14} />刷新</button>{currentVersion ? <button className="primaryButton" disabled={busy} onClick={() => prepareEdit.mutate()}><Settings2 size={15} />{activeDraft ? '继续编辑' : '编辑配置'}</button> : <button className="primaryButton" onClick={beginCreate}><Plus size={15} />创建生产配置</button>}</>} />
    <section className={styles.connectionPanel}>
      <header><div>{readiness.data?.external_execution_enabled ? <PlugZap /> : <Unplug />}<span><strong>生成服务连接准备</strong><small>按执行组件、生成配置、后端密钥和执行授权逐项检查；不会联网或产生费用。</small></span></div><em data-ready={readiness.data?.external_execution_enabled}>{readiness.data?.external_execution_enabled ? '可以执行' : '尚未准备完成'}</em></header>
      <div className={styles.connectionList}>
        {currentReadiness.map(provider => <article key={`${provider.configuration_version_id}:${provider.provider_version_id}`} data-status={provider.status}>
          <span className={styles.connectionIcon}>{provider.status === 'connected' ? <BadgeCheck /> : <AlertTriangle />}</span>
          <div><strong>{provider.provider_display_name}</strong><small>{provider.configuration_display_name} · v{provider.configuration_version_number}</small></div>
          <span><b>{readinessLabels[provider.status]}</b><small>{nextActionLabels[provider.next_action]}</small></span>
          <ReadinessChecks provider={provider} />
          <button type="button" className="secondaryButton" onClick={() => { setEditing(false); setSelectedId(provider.configuration_version_id) }}><FileCheck2 size={13} />查看配置</button>
          <details><summary>技术详情</summary><code>{provider.adapter_kind} · {provider.capabilities.join(', ') || 'NO_CAPABILITY'} · credential={credentialLabels[provider.credential_state]} · contract_issues={provider.configuration_issue_codes.join(', ') || 'none'}</code></details>
        </article>)}
        {readiness.isPending && <p>正在读取后端连接状态…</p>}
        {!readiness.isPending && !currentReadiness.length && <p>当前生产配置还没有可检查的服务供应商。</p>}
        {readiness.error && <p>连接状态读取失败：{readiness.error.message}</p>}
      </div>
    </section>
    <div className={styles.page}>
      <aside className={styles.versionList}>
        <header><div><span>PRODUCTION CONFIG</span><h2>当前配置</h2></div>{currentVersion && <b>v{currentVersion.version_number}</b>}</header>
        {currentVersion && <button data-selected={currentVersion.id === selectedId} onClick={() => { setEditing(false); setSelectedId(currentVersion.id) }}><i data-status={currentVersion.status}></i><div><strong>{currentVersion.display_name}</strong><small>当前生产版本 · v{currentVersion.version_number}</small></div><em>{statusLabels[currentVersion.status] ?? currentVersion.status}</em><ChevronRight size={14} /></button>}
        {activeDraft && <section className={styles.draftNotice}><div><i data-status={activeDraft.status}></i><span><strong>有未发布修改</strong><small>草稿 v{activeDraft.version_number} · {statusLabels[activeDraft.status] ?? activeDraft.status}</small></span></div><button type="button" className="secondaryButton" onClick={() => prepareEdit.mutate()} disabled={busy}>继续编辑</button></section>}
        {!versions.isPending && !currentVersion && <div className={styles.emptyList}><Settings2 size={22} /><strong>暂无系统配置</strong><span>创建配置不会调用供应商。</span></div>}
        {historyVersions.length > 0 && <details className={styles.historyList}><summary><History size={14} /><span>历史版本</span><b>{historyVersions.length}</b><ChevronDown size={14} /></summary><div>{historyVersions.map(item => <button key={item.id} data-selected={item.id === selectedId} onClick={() => { setEditing(false); setSelectedId(item.id) }}><i data-status={item.status}></i><span><strong>{item.display_name}</strong><small>v{item.version_number} · {statusLabels[item.status] ?? item.status}</small></span><ChevronRight size={13} /></button>)}</div></details>}
      </aside>

      <main className={styles.workspace}>{editing ? <form className={styles.editor} onSubmit={submit}>
        <div className={styles.editorHead}><div><span>{selected.data ? `REVISE v${selected.data.version_number}` : 'NEW DRAFT'}</span><h2>{selected.data ? '修订配置草稿' : '创建配置草稿'}</h2><p>技术标识由系统生成并在修订中保持不变。凭据只填写后端引用 ID，不填写密钥原文。</p></div><button type="button" className="iconButton" title="关闭编辑" onClick={() => setEditing(false)}><X size={16} /></button></div>
        <section className={styles.formSection}><header><Database /><div><strong>配置身份</strong><span>内部系列标识由系统生成</span></div></header><div className={styles.formGrid}><label>显示名称<input required value={draft.display_name} onChange={event => setDraft({ ...draft, display_name: event.target.value })} /></label><label className={styles.wide}>说明<input value={draft.description ?? ''} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label></div></section>

        <section className={styles.formSection}><header><Server /><div><strong>服务供应商</strong><span>能力声明必须覆盖槽位操作类型</span></div><button type="button" className="secondaryButton" onClick={() => setDraft({ ...draft, providers: [...draft.providers, providerDraft()] })}><Plus size={13} />供应商</button></header>{draft.providers.map((item, index) => <div className={styles.repeatBlock} key={item.provider_key}><div className={styles.repeatTitle}><b>服务供应商 {index + 1}</b>{draft.providers.length > 1 && <button type="button" className="iconButton" title="删除供应商" onClick={() => removeList('providers', index)}><Trash2 size={14} /></button>}</div><div className={styles.formGrid}><label>显示名称<input required value={item.display_name} onChange={event => updateList('providers', index, { ...item, display_name: event.target.value })} /></label><label>适配器类型<input required value={item.adapter_kind} onChange={event => updateList('providers', index, { ...item, adapter_kind: event.target.value })} placeholder="runninghub / dashscope / local" /></label><label>API 地址<input required type="url" value={item.base_url} onChange={event => updateList('providers', index, { ...item, base_url: event.target.value })} /></label><label>区域<input value={item.region ?? ''} onChange={event => updateList('providers', index, { ...item, region: event.target.value || null })} /></label><label>后端凭据引用<input value={item.credential_ref ?? ''} onChange={event => updateList('providers', index, { ...item, credential_ref: event.target.value || null })} placeholder="env://VARIABLE_NAME" /></label><label className={styles.wide}>能力（逗号分隔）<input required value={item.capabilities.join(', ')} onChange={event => updateList('providers', index, { ...item, capabilities: event.target.value.split(',').map(value => value.trim()).filter(Boolean) })} /></label><label>请求超时（秒）<input required type="number" min="1" value={item.request_timeout_seconds} onChange={event => updateList('providers', index, { ...item, request_timeout_seconds: Number(event.target.value) })} /></label><label>轮询间隔（秒）<input required type="number" min="1" value={item.poll_interval_seconds} onChange={event => updateList('providers', index, { ...item, poll_interval_seconds: Number(event.target.value) })} /></label><label>最大并发<input required type="number" min="1" value={item.max_concurrency} onChange={event => updateList('providers', index, { ...item, max_concurrency: Number(event.target.value) })} /></label></div></div>)}</section>

        <section className={styles.formSection}>
          <header><KeyRound /><div><strong>模型注册表</strong><span>可选；智能体运行后续按精确版本引用</span></div><button type="button" className="secondaryButton" onClick={() => setDraft({ ...draft, models: [...draft.models, modelDraft()] })}><Plus size={13} />模型</button></header>
          {draft.models.length === 0 && <div className={styles.inlineEmpty}>本配置暂未登记模型，不会自动选择任何模型。</div>}
          {draft.models.map((item, index) => <div className={styles.repeatBlock} key={item.config_key}>
            <div className={styles.repeatTitle}><b>模型 {index + 1}</b><button type="button" className="iconButton" title="删除模型" onClick={() => removeList('models', index)}><Trash2 size={14} /></button></div>
            <div className={styles.formGrid}>
              <label>智能体分工<select value={item.agent_role} onChange={event => updateList('models', index, { ...item, agent_role: event.target.value as ModelConfigDraft['agent_role'] })}><option value="creative">创作制片人</option><option value="planner">内容策划</option><option value="director">分镜导演</option><option value="production_planner">制作规划</option><option value="qc">质量审核</option><option value="editor">剪辑助理</option></select></label>
              <label>显示名称<input required value={item.display_name} onChange={event => updateList('models', index, { ...item, display_name: event.target.value })} /></label>
              <label>所属服务供应商<select required value={item.provider_key} onChange={event => updateList('models', index, { ...item, provider_key: event.target.value })}><option value="">请选择</option>{draft.providers.map((provider, providerIndex) => <option key={provider.provider_key} value={provider.provider_key}>{provider.display_name || `服务供应商 ${providerIndex + 1}`}</option>)}</select></label>
              <label>供应商模型 ID<input required value={item.provider_model_id} onChange={event => updateList('models', index, { ...item, provider_model_id: event.target.value })} /></label>
              <label>输入合同版本<input required value={item.input_contract_version} onChange={event => updateList('models', index, { ...item, input_contract_version: event.target.value })} /></label>
              <label>输出 Schema 版本<input required value={item.output_schema_version} onChange={event => updateList('models', index, { ...item, output_schema_version: event.target.value })} /></label>
              <label>Prompt 合同版本<input required value={item.prompt_contract_version} onChange={event => updateList('models', index, { ...item, prompt_contract_version: event.target.value })} /></label>
            </div>
          </div>)}
        </section>

        <section className={styles.formSection}><header><Boxes /><div><strong>视频规格</strong><span>工作流必须精确声明支持的规格</span></div><button type="button" className="secondaryButton" onClick={() => setDraft({ ...draft, video_specs: [...draft.video_specs, videoDraft()] })}><Plus size={13} />规格</button></header>{draft.video_specs.map((item, index) => <div className={styles.repeatBlock} key={item.spec_key}><div className={styles.repeatTitle}><b>视频规格 {index + 1}</b>{draft.video_specs.length > 1 && <button type="button" className="iconButton" title="删除规格" onClick={() => removeList('video_specs', index)}><Trash2 size={14} /></button>}</div><div className={styles.formGrid}><label>显示名称<input required value={item.display_name} onChange={event => updateList('video_specs', index, { ...item, display_name: event.target.value })} /></label><label>画幅<select value={item.aspect_ratio} onChange={event => updateList('video_specs', index, { ...item, aspect_ratio: event.target.value as VideoSpecDraft['aspect_ratio'] })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label><label>宽度<input required type="number" min="64" value={item.width} onChange={event => updateList('video_specs', index, { ...item, width: Number(event.target.value) })} /></label><label>高度<input required type="number" min="64" value={item.height} onChange={event => updateList('video_specs', index, { ...item, height: Number(event.target.value) })} /></label><label>FPS<input required type="number" min="1" value={item.fps} onChange={event => updateList('video_specs', index, { ...item, fps: Number(event.target.value) })} /></label><label>最短时长<input required type="number" min="1" value={item.duration_min_seconds} onChange={event => updateList('video_specs', index, { ...item, duration_min_seconds: Number(event.target.value) })} /></label><label>最长时长<input required type="number" min="1" value={item.duration_max_seconds} onChange={event => updateList('video_specs', index, { ...item, duration_max_seconds: Number(event.target.value) })} /></label><label>容器<input required value={item.container} onChange={event => updateList('video_specs', index, { ...item, container: event.target.value })} /></label><label>编码器<input required value={item.video_codec} onChange={event => updateList('video_specs', index, { ...item, video_codec: event.target.value })} /></label><label>像素格式<input required value={item.pixel_format} onChange={event => updateList('video_specs', index, { ...item, pixel_format: event.target.value })} /></label></div></div>)}</section>

        <section className={styles.formSection}>
          <header><Workflow /><div><strong>工作流槽位</strong><span>每个工作流精确绑定 RunningHub 节点输入，不接受旧占位符</span></div><button type="button" className="secondaryButton" onClick={() => setDraft({ ...draft, workflow_slots: [...draft.workflow_slots, workflowDraft()] })}><Plus size={13} />槽位</button></header>
          {draft.workflow_slots.map((item, index) => <div className={styles.repeatBlock} key={item.slot_key}>
            <div className={styles.repeatTitle}><b>工作流槽位 {index + 1}</b>{draft.workflow_slots.length > 1 && <button type="button" className="iconButton" title="删除槽位" onClick={() => removeList('workflow_slots', index)}><Trash2 size={14} /></button>}</div>
            <div className={styles.formGrid}>
              <label>显示名称<input required value={item.display_name} onChange={event => updateList('workflow_slots', index, { ...item, display_name: event.target.value })} /></label>
              <label>生成类型<select required value={item.operation_kind} onChange={event => updateList('workflow_slots', index, { ...item, operation_kind: event.target.value })}><option value="">请选择</option><option value="image_generation">生成图片</option><option value="video_generation">首帧生成视频</option><option value="tts">文字生成语音</option>{!['', 'image_generation', 'video_generation', 'tts'].includes(item.operation_kind) && <option value={item.operation_kind}>其他：{item.operation_kind}</option>}</select></label>
              <label>所属服务供应商<select required value={item.provider_key} onChange={event => updateList('workflow_slots', index, { ...item, provider_key: event.target.value })}><option value="">请选择</option>{draft.providers.map((provider, providerIndex) => <option key={provider.provider_key} value={provider.provider_key}>{provider.display_name || `服务供应商 ${providerIndex + 1}`}</option>)}</select></label>
              <label>工作流 ID<input required value={item.provider_workflow_id} onChange={event => updateList('workflow_slots', index, { ...item, provider_workflow_id: event.target.value })} /></label>
              <label>工作流版本<input value={item.provider_workflow_version ?? ''} onChange={event => updateList('workflow_slots', index, { ...item, provider_workflow_version: event.target.value || null })} /></label>
              <label>模型配置<select value={item.model_config_key ?? ''} onChange={event => updateList('workflow_slots', index, { ...item, model_config_key: event.target.value || null })}><option value="">不绑定模型</option>{draft.models.map((model, modelIndex) => <option key={model.config_key} value={model.config_key}>{model.display_name || `模型 ${modelIndex + 1}`}</option>)}</select></label>
              <label>输入合同版本<input required value={item.input_schema_version} onChange={event => updateList('workflow_slots', index, { ...item, input_schema_version: event.target.value })} /></label>
              <label>输出合同版本<input required value={item.output_schema_version} onChange={event => updateList('workflow_slots', index, { ...item, output_schema_version: event.target.value })} /></label>
              <fieldset className={`${styles.wide} ${styles.choiceField}`}><legend>支持的视频规格</legend><div className={styles.choiceList}>{draft.video_specs.map((video, videoIndex) => <label key={video.spec_key}><input type="checkbox" checked={item.supported_video_spec_keys.includes(video.spec_key)} onChange={event => updateList('workflow_slots', index, { ...item, supported_video_spec_keys: event.target.checked ? [...item.supported_video_spec_keys, video.spec_key] : item.supported_video_spec_keys.filter(key => key !== video.spec_key) })} /><span>{video.display_name || `视频规格 ${videoIndex + 1}`}</span><small>{video.width}×{video.height} · {video.fps}fps</small></label>)}</div></fieldset>
            </div>
            <div className={styles.nodeBindings}>
              <div className={styles.bindingHeader}><strong>工作流输入映射</strong><span>节点 ID 和输入字段来自 RunningHub 工作流；输入内容从已确认的镜头合同读取。</span></div>
              {item.node_info_list.map((binding, bindingIndex) => <NodeBindingEditor
                key={bindingIndex}
                binding={binding}
                operationKind={item.operation_kind}
                removable={item.node_info_list.length > 1}
                onChange={nextBinding => updateList('workflow_slots', index, { ...item, node_info_list: item.node_info_list.map((row, rowIndex) => rowIndex === bindingIndex ? nextBinding : row) })}
                onRemove={() => updateList('workflow_slots', index, { ...item, node_info_list: item.node_info_list.filter((_, rowIndex) => rowIndex !== bindingIndex) })}
              />)}
              <button type="button" className="secondaryButton" onClick={() => updateList('workflow_slots', index, { ...item, node_info_list: [...item.node_info_list, { node_id: '', field_path: '', value_source: '', value_type: 'string', required: true }] })}><Plus size={13} />添加输入</button>
            </div>
          </div>)}
        </section>

        <section className={styles.formSection}><header><ShieldCheck /><div><strong>音频与存储策略</strong><span>音频关闭时不能绑定 TTS；OSS 只记录凭据引用</span></div></header><div className={styles.policyColumns}><div><h3>音频配置</h3><div className={styles.formGrid}><label>显示名称<input required value={draft.audio.display_name} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, display_name: event.target.value } })} /></label><label>支持模式<select multiple value={draft.audio.supported_modes} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, supported_modes: Array.from(event.target.selectedOptions).map(option => option.value) as Array<'off' | 'voiceover'> } })}><option value="off">关闭</option><option value="voiceover">旁白</option></select></label><label>TTS 槽位<select value={draft.audio.tts_workflow_slot_key ?? ''} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, tts_workflow_slot_key: event.target.value || null } })}><option value="">不绑定</option>{draft.workflow_slots.filter(workflow => workflow.operation_kind === 'tts').map((workflow, workflowIndex) => <option key={workflow.slot_key} value={workflow.slot_key}>{workflow.display_name || `TTS 工作流 ${workflowIndex + 1}`}</option>)}</select></label><label>采样率<input required type="number" value={draft.audio.sample_rate} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, sample_rate: Number(event.target.value) } })} /></label><label>声道<select value={draft.audio.channels} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, channels: Number(event.target.value) as 1 | 2 } })}><option value="1">单声道</option><option value="2">双声道</option></select></label><label>格式<input required value={draft.audio.format} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, format: event.target.value } })} /></label><label>语速下限<input required type="number" step="0.1" value={draft.audio.speaking_rate_min} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, speaking_rate_min: Number(event.target.value) } })} /></label><label>语速上限<input required type="number" step="0.1" value={draft.audio.speaking_rate_max} onChange={event => setDraft({ ...draft, audio: { ...draft.audio, speaking_rate_max: Number(event.target.value) } })} /></label></div></div><div><h3>存储策略</h3><div className={styles.formGrid}><label>显示名称<input required value={draft.storage.display_name} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, display_name: event.target.value } })} /></label><label>后端<select value={draft.storage.backend_kind} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, backend_kind: event.target.value as 'local' | 'oss' } })}><option value="local">本地</option><option value="oss">阿里云 OSS</option></select></label><label>公网 URL 策略<select value={draft.storage.public_url_policy} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, public_url_policy: event.target.value as typeof draft.storage.public_url_policy } })}><option value="none">不提供</option><option value="signed">签名 URL</option><option value="public">公共 URL</option><option value="temporary_public">临时公共 URL</option></select></label><label className={styles.wide}>允许 MIME（逗号分隔）<input required value={draft.storage.allowed_mime_types.join(', ')} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, allowed_mime_types: event.target.value.split(',').map(value => value.trim()).filter(Boolean) } })} /></label><label>文件上限（字节）<input required type="number" value={draft.storage.max_file_size_bytes} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, max_file_size_bytes: Number(event.target.value) } })} /></label>{draft.storage.backend_kind === 'local' ? <label>本地目录引用<input required value={draft.storage.local_root_ref ?? ''} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, local_root_ref: event.target.value || null } })} /></label> : <><label>区域引用<input required value={draft.storage.region_ref ?? ''} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, region_ref: event.target.value || null } })} /></label><label>Bucket 引用<input required value={draft.storage.bucket_ref ?? ''} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, bucket_ref: event.target.value || null } })} /></label><label>凭据引用<input required value={draft.storage.credential_ref ?? ''} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, credential_ref: event.target.value || null } })} /></label><label>生命周期（天）<input required type="number" min="1" value={draft.storage.lifecycle_days ?? ''} onChange={event => setDraft({ ...draft, storage: { ...draft.storage, lifecycle_days: Number(event.target.value) || null } })} /></label></>}</div></div></div></section>
        <section className={styles.formSection}>
          <header><CircleDollarSign /><div><strong>价格目录</strong><span>配置和发布时可选；付费生产执行前必须完成明确估价</span></div>{draft.pricing ? <button type="button" className="secondaryButton" onClick={() => setDraft({ ...draft, pricing: null })}><Trash2 size={13} />移除目录</button> : <button type="button" className="secondaryButton" onClick={() => setDraft({ ...draft, pricing: { catalog_key: generatedKey('pricing'), display_name: '', currency: 'CNY', confirmation_threshold: 0, rules: [{ workflow_slot_key: '', unit: 'call', unit_price: 0, minimum_charge: null, estimated_runtime_seconds: null }] } })}><Plus size={13} />添加目录</button>}</header>
          {draft.pricing ? <div className={styles.repeatBlock}>
            <div className={styles.formGrid}><label>显示名称<input required value={draft.pricing.display_name} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, display_name: event.target.value } })} /></label><label>币种<input required pattern="[A-Z]{3,12}" value={draft.pricing.currency} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, currency: event.target.value.toUpperCase() } })} /></label><label>费用确认阈值<input required type="number" min="0" step="0.000001" value={draft.pricing.confirmation_threshold} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, confirmation_threshold: Number(event.target.value) } })} /></label><label>生效时间（ISO，可空）<input value={draft.pricing.effective_from ?? ''} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, effective_from: event.target.value || null } })} placeholder="2026-07-15T00:00:00+08:00" /></label><label>失效时间（ISO，可空）<input value={draft.pricing.effective_to ?? ''} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, effective_to: event.target.value || null } })} /></label></div>
            <div className={styles.nodeBindings}><strong>精确工作流计价规则</strong>{draft.pricing.rules.map((rule, index) => <div key={index}>
              <select required aria-label="计价工作流槽位" value={rule.workflow_slot_key} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, rules: draft.pricing!.rules.map((item, itemIndex) => itemIndex === index ? { ...item, workflow_slot_key: event.target.value } : item) } })}><option value="">请选择工作流槽位</option>{draft.workflow_slots.map((workflow, workflowIndex) => <option key={workflow.slot_key} value={workflow.slot_key}>{workflow.display_name || `工作流槽位 ${workflowIndex + 1}`}</option>)}</select>
              <select aria-label="计价单位" value={rule.unit} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, rules: draft.pricing!.rules.map((item, itemIndex) => itemIndex === index ? { ...item, unit: event.target.value as typeof rule.unit, estimated_runtime_seconds: event.target.value === 'runtime_second' ? item.estimated_runtime_seconds : null } : item) } })}><option value="call">按调用</option><option value="output_second">按输出秒数</option><option value="runtime_second">按云端运行秒数</option></select>
              {rule.unit === 'runtime_second' && <input required aria-label="预计单次运行秒数" type="number" min="0.001" step="0.001" value={rule.estimated_runtime_seconds ?? ''} placeholder="预计单次运行秒数" onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, rules: draft.pricing!.rules.map((item, itemIndex) => itemIndex === index ? { ...item, estimated_runtime_seconds: event.target.value === '' ? null : Number(event.target.value) } : item) } })} />}
              <input required aria-label="单价" type="number" min="0" step="0.000001" value={rule.unit_price} onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, rules: draft.pricing!.rules.map((item, itemIndex) => itemIndex === index ? { ...item, unit_price: Number(event.target.value) } : item) } })} />
              <input aria-label="最低收费" type="number" min="0" step="0.000001" value={rule.minimum_charge ?? ''} placeholder="最低收费（可空）" onChange={event => setDraft({ ...draft, pricing: { ...draft.pricing!, rules: draft.pricing!.rules.map((item, itemIndex) => itemIndex === index ? { ...item, minimum_charge: event.target.value === '' ? null : Number(event.target.value) } : item) } })} />
              {draft.pricing!.rules.length > 1 && <button type="button" className="iconButton" title="删除价格规则" onClick={() => setDraft({ ...draft, pricing: { ...draft.pricing!, rules: draft.pricing!.rules.filter((_, itemIndex) => itemIndex !== index) } })}><Trash2 size={13} /></button>}
            </div>)}<button type="button" className="secondaryButton" onClick={() => setDraft({ ...draft, pricing: { ...draft.pricing!, rules: [...draft.pricing!.rules, { workflow_slot_key: '', unit: 'call', unit_price: 0, minimum_charge: null, estimated_runtime_seconds: null }] } })}><Plus size={13} />价格规则</button></div>
          </div> : <div className={styles.inlineEmpty}>当前配置不包含价格目录；仍可创建、校验、发布和生成 preparing 快照，但不能授权未知成本的付费执行。</div>}
        </section>
        <div className={styles.formActions}>{mutationError && <span>{mutationError.message}</span>}<button type="button" className="secondaryButton" onClick={() => setEditing(false)}>取消</button><button className="primaryButton" disabled={busy}><Save size={14} />{selected.data ? '保存修订草稿' : '创建草稿'}</button></div>
      </form> : selected.data ? <div className={styles.detail}>
        <section className={styles.detailHead}><div><span>CONFIGURATION AUTHORITY</span><h2>{selected.data.display_name}</h2><p>{selected.data.description || '未填写说明'}</p></div><em data-status={selected.data.status}>{statusLabels[selected.data.status] ?? selected.data.status}</em></section>
        <div className={styles.factStrip}><div><span>配置版本</span><strong>{selected.data.config_key} · v{selected.data.version_number}</strong></div><div><span>行版本</span><strong>{selected.data.row_version}</strong></div><div><span>组件</span><strong>{componentCount}</strong></div><div><span>引用</span><strong>{selected.data.references.length}</strong></div></div>
        <section className={styles.boundary}><LockKeyhole size={18} /><div><strong>{selected.data.status === 'published' ? '当前配置已发布' : '发布不等于生产'}</strong><p>{selected.data.status === 'published' ? '点击“编辑配置”即可修改；系统会在后台保留当前版本并创建修订草稿。' : '校验和发布不会创建项目快照、工作项或供应商调用。'}</p></div></section>
        {selected.data.validation_report.length > 0 && <section className={styles.validation}><header><AlertTriangle size={17} /><div><strong>确定性校验未通过</strong><span>{selected.data.validation_report.length} 项错误，系统不会补值或替换路由。</span></div></header>{selected.data.validation_report.map((item, index) => <article key={index}><b>{item.code ?? 'VALIDATION_ERROR'}</b><span>{item.path ?? item.slot_key ?? 'configuration'}</span><p>{item.message ?? (item.missing ? `缺少：${item.missing.join(', ')}` : '请检查该字段的精确引用。')}</p></article>)}</section>}
        {selected.data.config_hash && <section className={styles.hashBox}><FileCheck2 size={17} /><div><strong>配置哈希</strong><code>{selected.data.config_hash}</code></div></section>}
        {selected.data.supersedes_version_id && <section className={styles.diffBox}><header><Copy size={16} /><div><strong>相对上一版本的差异</strong><span>基线：{selected.data.supersedes_version_id}</span></div><b>{diff.data?.changed_components.length ?? '…'}</b></header>{diff.data?.changed_components.map(item => <article key={`${item.component_type}:${item.key}`}><span>{componentLabels[item.component_type] ?? item.component_type}</span><strong>{item.key}</strong><em>高风险版本变化</em></article>)}<footer>发布差异本身不产生费用；项目采用新版本时仍需单独创建快照并确认影响。</footer></section>}
        <ComponentList components={selected.data.components} />
        <section className={styles.referenceBox}><header><Database size={16} /><div><strong>引用关系</strong><span>历史引用阻止物理删除，但不阻止审计。</span></div><b>{selected.data.references.length}</b></header>{selected.data.references.length ? selected.data.references.map(item => <article key={`${item.ref_type}:${item.ref_id}`}><span>{item.ref_type}</span><strong>{item.ref_id}</strong></article>) : <p>当前没有项目、快照或工作尝试引用该配置版本。</p>}</section>
        <section className={styles.actions}><div><strong>下一步</strong><span>{selected.data.status === 'draft' || selected.data.status === 'validation_failed' ? '修订或执行确定性校验' : selected.data.status === 'ready' ? '强确认后发布为当前生产配置' : selected.data.id === currentVersion?.id ? '可供新生产快照显式选择' : '历史版本仅供追溯，不影响当前配置'}</span></div>{['draft', 'validation_failed', 'ready'].includes(selected.data.status) && <button className="secondaryButton" onClick={beginEdit} disabled={busy}><Settings2 size={14} />编辑草稿</button>}{['draft', 'validation_failed'].includes(selected.data.status) && <button className="primaryButton" onClick={() => validate.mutate()} disabled={busy}><Check size={14} />执行校验</button>}{selected.data.status === 'ready' && <button className="primaryButton" onClick={() => setConfirmPublish(true)} disabled={busy}><BadgeCheck size={14} />发布配置</button>}{selected.data.id === currentVersion?.id && selected.data.status === 'published' && <button className="secondaryButton" onClick={() => prepareEdit.mutate()} disabled={busy}><Settings2 size={14} />编辑配置</button>}{selected.data.id === currentVersion?.id && selected.data.status === 'published' && <button className="secondaryButton" onClick={() => setConfirmRetire(true)} disabled={busy}><Trash2 size={14} />停用</button>}</section>
        {mutationError && <div className={styles.mutationError}>{mutationError.message}</div>}
      </div> : selectedId && selected.isPending ? <div className={styles.loading}>正在读取配置版本…</div> : <div className={styles.workspaceEmpty}><Settings2 size={28} /><strong>等待创建第一个配置草稿</strong><span>草稿不会成为生产权威；必须先通过校验并由你明确发布。</span><button className="primaryButton" onClick={beginCreate}><Plus size={14} />新建配置草稿</button></div>}</main>
    </div>
    {confirmPublish && selected.data && <div className={styles.modalBackdrop}><section className={styles.confirmDialog}><header><BadgeCheck size={20} /><div><span>HIGH RISK CONFIRMATION</span><h2>发布系统配置 v{selected.data.version_number}</h2></div></header><p>本版本包含供应商、工作流 ID、NodeInfoList 和媒体规格。发布后不可修改，但不会创建快照、启动 Worker 或产生费用。</p><ul><li>{selected.data.components.length} 个精确组件版本将同时发布</li><li>配置哈希：{selected.data.config_hash?.slice(0, 16)}…</li><li>现有项目和快照不会自动采用该版本</li></ul><div><button className="secondaryButton" onClick={() => setConfirmPublish(false)}>取消</button><button className="primaryButton" disabled={publish.isPending} onClick={() => publish.mutate()}><BadgeCheck size={14} />确认发布</button></div></section></div>}
    {confirmRetire && selected.data && <div className={styles.modalBackdrop}><section className={styles.confirmDialog}><header><AlertTriangle size={20} /><div><span>REFERENCE IMPACT</span><h2>停用配置 v{selected.data.version_number}</h2></div></header><p>停用只阻止新快照选择，历史引用和审计链保持有效。目前共有 {selected.data.references.length} 个引用。</p><div><button className="secondaryButton" onClick={() => setConfirmRetire(false)}>取消</button><button className="primaryButton" disabled={retire.isPending} onClick={() => retire.mutate()}>确认停用</button></div></section></div>}
  </>
}
