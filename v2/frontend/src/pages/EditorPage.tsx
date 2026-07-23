import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, ChevronDown, ChevronUp, Clock3, Download, FileVideo2, Film, ListVideo, Music2, Plus, RefreshCw, Save, Scissors, ShieldCheck, Sparkles, Subtitles, Trash2, Upload, Video, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { DeliveryAttempt, EditorAsset, Timeline, TimelineItemDraft } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { projectStatusLabel } from '../presentation/projectFacts'
import styles from './EditorPage.module.css'

type TrackType = TimelineItemDraft['track_type']
type DraftMode = 'view' | 'new' | 'revision'

interface EditorEvidence {
  timeline_item_code: string | null
  shot_code: string | null
  selection_reason: string | null
  qc_report_ids: string[]
}

const trackMeta: Record<TrackType, { label: string; icon: typeof Video }> = {
  main_video: { label: '主画面', icon: Video },
  audio: { label: '音频', icon: Music2 },
  subtitle: { label: '字幕', icon: Subtitles },
}

const timelineStatusLabels: Record<Timeline['status'], string> = {
  candidate: '待校验',
  review: '待确认',
  confirmed: '已确认',
  exported: '已交付',
  superseded: '历史版本',
}

const timelineSourceLabels: Record<Timeline['source'], string> = {
  user: '人工创建',
  editor_assistant: '剪辑助理',
}

const assetTypeLabels: Record<EditorAsset['asset_type'], string> = {
  video: '视频',
  audio: '音频',
  subtitle: '字幕',
}

const assetStateLabels: Record<EditorAsset['state'], string> = {
  approved: '已批准',
  used: '已使用',
}

const timeFieldLabels: Record<'source_in_ms' | 'source_out_ms' | 'timeline_in_ms' | 'timeline_out_ms', string> = {
  source_in_ms: '素材起点',
  source_out_ms: '素材终点',
  timeline_in_ms: '成片起点',
  timeline_out_ms: '成片终点',
}

function seconds(ms: number | null) {
  return ms == null ? '--' : `${(ms / 1000).toFixed(ms % 1000 ? 1 : 0)}s`
}

function timecode(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000))
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function assetTrack(asset: EditorAsset): TrackType {
  return asset.asset_type === 'video' ? 'main_video' : asset.asset_type
}

function normalizeSequences(items: TimelineItemDraft[]) {
  const counters: Record<TrackType, number> = { main_video: 0, audio: 0, subtitle: 0 }
  return items.map(item => ({ ...item, sequence_number: ++counters[item.track_type] }))
}

function timelineDraftItems(timeline: Timeline): TimelineItemDraft[] {
  return timeline.items.map(item => ({
    track_type: item.track_type,
    sequence_number: item.sequence_number,
    asset_id: item.asset_id,
    label: item.label,
    gap_reason: item.gap_reason,
    source_in_ms: item.source_in_ms,
    source_out_ms: item.source_out_ms,
    timeline_in_ms: item.timeline_in_ms,
    timeline_out_ms: item.timeline_out_ms,
    transform: item.transform,
  }))
}

function editorEvidence(transform: Record<string, unknown>): EditorEvidence | null {
  const value = transform.editor_assistant
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  return {
    timeline_item_code: typeof record.timeline_item_code === 'string' ? record.timeline_item_code : null,
    shot_code: typeof record.shot_code === 'string' ? record.shot_code : null,
    selection_reason: typeof record.selection_reason === 'string' ? record.selection_reason : null,
    qc_report_ids: Array.isArray(record.qc_report_ids) ? record.qc_report_ids.filter((item): item is string => typeof item === 'string') : [],
  }
}

export function EditorPage() {
  const [searchParams] = useSearchParams()
  const client = useQueryClient()
  const [projectId, setProjectId] = useState(() => searchParams.get('project') ?? '')
  const revisionRequestId = searchParams.get('revisionRequest') ?? ''
  const [selectedTimelineId, setSelectedTimelineId] = useState('')
  const [draftItems, setDraftItems] = useState<TimelineItemDraft[]>([])
  const [draftMode, setDraftMode] = useState<DraftMode>('view')
  const [revisionBase, setRevisionBase] = useState<Timeline | null>(null)
  const [selectedDraftIndex, setSelectedDraftIndex] = useState<number | null>(null)
  const [selectedAssetId, setSelectedAssetId] = useState('')
  const [audioEnabled, setAudioEnabled] = useState(false)
  const [subtitleEnabled, setSubtitleEnabled] = useState(false)
  const [gapSeconds, setGapSeconds] = useState(2)
  const [confirming, setConfirming] = useState<Timeline | null>(null)
  const [authorizingDelivery, setAuthorizingDelivery] = useState(false)
  const [deliveryMethod, setDeliveryMethod] = useState<DeliveryAttempt['execution_kind'] | null>(null)
  const [deliveryFile, setDeliveryFile] = useState<File | null>(null)
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.projects(), refetchInterval: 5000 })
  const workspace = useQuery({ queryKey: ['editor-workspace', projectId], queryFn: () => api.editorWorkspace(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const delivery = useQuery({ queryKey: ['delivery-workspace', projectId], queryFn: () => api.deliveryWorkspace(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const revision = useQuery({ queryKey: ['asset-revision-request', projectId, revisionRequestId], queryFn: () => api.assetRevisionRequest(projectId, revisionRequestId), enabled: Boolean(projectId && revisionRequestId) })
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['editor-workspace', projectId] })
    await client.invalidateQueries({ queryKey: ['delivery-workspace', projectId] })
    await client.invalidateQueries({ queryKey: ['projects'] })
  }
  const stage = useMutation({ mutationFn: () => api.approveQualityStage(projectId, workspace.data!.active_snapshot_id!), onSuccess: refresh })
  const generateAssistant = useMutation({
    mutationFn: () => api.generateEditorTimeline(projectId, workspace.data!.active_snapshot_id!),
    onSuccess: async timeline => { setDraftMode('view'); setSelectedTimelineId(timeline.id); await refresh() },
  })
  const retryAssistant = useMutation({
    mutationFn: () => api.retryEditorTimeline(projectId, workspace.data!.latest_editor_run!.id),
    onSuccess: async timeline => { setDraftMode('view'); setSelectedTimelineId(timeline.id); await refresh() },
  })
  const save = useMutation({
    mutationFn: () => revisionBase
      ? api.reviseTimelineCandidate(projectId, revisionBase, { audio_enabled: audioEnabled, subtitle_enabled: subtitleEnabled }, draftItems)
      : api.createTimelineCandidate(projectId, workspace.data!.active_snapshot_id!, 'user', { audio_enabled: audioEnabled, subtitle_enabled: subtitleEnabled }, draftItems),
    onSuccess: async timeline => {
      setSelectedTimelineId(timeline.id)
      setDraftMode('view')
      setRevisionBase(null)
      await refresh()
    },
  })
  const validate = useMutation({ mutationFn: (timeline: Timeline) => api.validateTimeline(projectId, timeline), onSuccess: async timeline => { setSelectedTimelineId(timeline.id); await refresh() } })
  const confirm = useMutation({ mutationFn: (timeline: Timeline) => api.confirmTimeline(projectId, timeline), onSuccess: async timeline => { setConfirming(null); setSelectedTimelineId(timeline.id); await refresh() } })
  const authorize = useMutation({ mutationFn: () => api.authorizeDelivery(projectId, delivery.data!, deliveryMethod!), onSuccess: async () => { setAuthorizingDelivery(false); setDeliveryMethod(null); await refresh() } })
  const upload = useMutation({ mutationFn: () => api.uploadDelivery(projectId, delivery.data!.attempts[0], deliveryFile!), onSuccess: async () => { setDeliveryFile(null); await refresh() } })
  const verify = useMutation({ mutationFn: () => api.verifyDelivery(projectId, delivery.data!.attempts[0]), onSuccess: refresh })
  const editorProjects = projects.data?.filter(project => ['quality_review', 'editing', 'delivery_ready', 'blocked', 'completed'].includes(project.status)) ?? []
  const selectedTimeline = workspace.data?.timelines.find(row => row.id === selectedTimelineId) ?? workspace.data?.timelines[0] ?? null
  const selectedAsset = workspace.data?.available_assets.find(asset => asset.id === selectedAssetId) ?? null
  const deliveryAttempt = delivery.data?.attempts[0] ?? null
  const error = workspace.error || delivery.error || stage.error || generateAssistant.error || retryAssistant.error || save.error || validate.error || confirm.error || authorize.error || upload.error || verify.error

  useEffect(() => {
    setSelectedTimelineId('')
    setDraftItems([])
    setDraftMode('view')
    setRevisionBase(null)
    setSelectedDraftIndex(null)
    setSelectedAssetId('')
    setAuthorizingDelivery(false)
    setDeliveryMethod(null)
    setDeliveryFile(null)
  }, [projectId])

  useEffect(() => {
    if (draftMode !== 'view' || !selectedTimeline) return
    const items = timelineDraftItems(selectedTimeline)
    setDraftItems(items)
    setAudioEnabled(selectedTimeline.track_config.audio_enabled)
    setSubtitleEnabled(selectedTimeline.track_config.subtitle_enabled)
    setRevisionBase(null)
    setSelectedDraftIndex(items.length ? 0 : null)
    setSelectedAssetId(items.find(item => item.asset_id)?.asset_id ?? '')
  }, [draftMode, selectedTimeline?.id, selectedTimeline?.row_version])

  const timelineDuration = useMemo(() => draftItems.reduce((maximum, item) => Math.max(maximum, item.timeline_out_ms), 0), [draftItems])
  const selectedDraftItem = selectedDraftIndex == null ? null : draftItems[selectedDraftIndex] ?? null
  const selectedEvidence = selectedDraftItem ? editorEvidence(selectedDraftItem.transform) : null
  const beginNew = () => {
    setDraftMode('new')
    setDraftItems([])
    setRevisionBase(null)
    setSelectedDraftIndex(null)
    setAudioEnabled(workspace.data?.audio_mode !== 'off')
    setSubtitleEnabled(false)
  }
  const beginRevision = (timeline: Timeline) => {
    setDraftMode('revision')
    setSelectedTimelineId(timeline.id)
    setDraftItems(timelineDraftItems(timeline))
    setAudioEnabled(timeline.track_config.audio_enabled)
    setSubtitleEnabled(timeline.track_config.subtitle_enabled)
    setRevisionBase(timeline)
    setSelectedDraftIndex(null)
  }
  const addAsset = (asset: EditorAsset) => {
    if (draftMode === 'view' || !asset.duration_ms || asset.duration_ms <= 0) return
    const duration = asset.duration_ms
    const track = assetTrack(asset)
    const trackRows = draftItems.filter(item => item.track_type === track)
    const cursor = trackRows.reduce((maximum, item) => Math.max(maximum, item.timeline_out_ms), 0)
    setDraftItems(items => [...items, {
      track_type: track,
      sequence_number: trackRows.length + 1,
      asset_id: asset.id,
      label: asset.node_key ?? asset.role,
      source_in_ms: 0,
      source_out_ms: duration,
      timeline_in_ms: cursor,
      timeline_out_ms: cursor + duration,
      transform: track === 'main_video' ? { fit: 'cover' } : {},
    }])
    setSelectedAssetId(asset.id)
  }
  const addGap = () => {
    if (draftMode === 'view') return
    const duration = Math.round(gapSeconds * 1000)
    if (duration <= 0) return
    const rows = draftItems.filter(item => item.track_type === 'main_video')
    const cursor = rows.reduce((maximum, item) => Math.max(maximum, item.timeline_out_ms), 0)
    setDraftItems(items => [...items, {
      track_type: 'main_video', sequence_number: rows.length + 1, asset_id: null, label: '待取舍', gap_reason: '用户保留的剪辑空位',
      source_in_ms: null, source_out_ms: null, timeline_in_ms: cursor, timeline_out_ms: cursor + duration, transform: {},
    }])
  }
  const removeItem = (index: number) => {
    setDraftItems(items => normalizeSequences(items.filter((_, itemIndex) => itemIndex !== index)))
    setSelectedDraftIndex(null)
  }
  const moveItem = (index: number, direction: -1 | 1) => {
    setDraftItems(items => {
      const current = items[index]
      const sameTrack = items.map((item, itemIndex) => ({ item, itemIndex })).filter(row => row.item.track_type === current.track_type)
      const position = sameTrack.findIndex(row => row.itemIndex === index)
      const target = sameTrack[position + direction]
      if (!target) return items
      const next = [...items]
      ;[next[index], next[target.itemIndex]] = [next[target.itemIndex], next[index]]
      return normalizeSequences(next)
    })
  }
  const updateItem = (index: number, field: keyof TimelineItemDraft, value: string | number | null) => {
    setDraftItems(items => items.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: value } : item))
  }

  return <>
    <PageHeader eyebrow="EDITOR" title="剪辑台" description="素材取舍、时间区间和交付范围以版本化时间线合同记录。" actions={<button className="secondaryButton" onClick={() => refresh()}><RefreshCw size={14} />刷新</button>} />
    <main className={styles.layout}>
      <aside className={styles.projects}>
        <header><span>EDITABLE PROJECTS</span><h2>剪辑项目</h2><b>{editorProjects.length}</b></header>
        {editorProjects.map(project => <button key={project.id} data-selected={projectId === project.id} onClick={() => setProjectId(project.id)}><i>{project.status === 'delivery_ready' ? <Check /> : <Clock3 />}</i><span><strong>{project.title}</strong><small>{projectStatusLabel(project.status)}</small></span></button>)}
        {!editorProjects.length && <p>暂无进入审核或剪辑阶段的项目</p>}
      </aside>

      <section className={styles.workspace}>
        {!projectId && <div className={styles.empty}><ListVideo /><strong>选择一个项目</strong><span>时间线候选和素材引用将显示在这里</span></div>}
        {workspace.isPending && projectId && <div className={styles.empty}>正在读取剪辑合同...</div>}
        {workspace.data && <>
          <header className={styles.summary}>
            <div><span>NEXT ACTION</span><h2>{workspace.data.next_action.label}</h2></div>
            <dl><div><dt>输出</dt><dd>{seconds(workspace.data.duration_ms)} · {workspace.data.aspect_ratio}</dd></div><div><dt>素材</dt><dd>{workspace.data.available_assets.length}</dd></div><div><dt>版本</dt><dd>{workspace.data.timelines.length}</dd></div></dl>
          </header>
          {revision.data && <div className={styles.revisionContext}><AlertTriangle size={17} /><div><strong>已登记剪辑取舍问题</strong><span>{revision.data.rationale}</span><small>{revision.data.shot_code ? `对应分镜 ${revision.data.shot_code} · ` : ''}原素材仍保留，请在时间线候选中明确调整。</small></div></div>}

          {workspace.data.project_status === 'quality_review' && <div className={styles.gate} data-ready={workspace.data.quality_stage_ready}>
            <div>{workspace.data.quality_stage_ready ? <Check /> : <AlertTriangle />}<span><strong>{workspace.data.quality_stage_ready ? '必需素材已批准' : '质量阶段尚未完成'}</strong><small>{workspace.data.quality_stage_ready ? '当前批准素材集可以进入剪辑' : `${workspace.data.quality_output_gaps.length} 个必需输出仍未满足`}</small></span></div>
            <button className="primaryButton" disabled={!workspace.data.quality_stage_ready || stage.isPending} onClick={() => stage.mutate()}>确认进入剪辑</button>
          </div>}

          {['editing', 'delivery_ready'].includes(workspace.data.project_status) && <>
            {workspace.data.project_status === 'editing' && !workspace.data.timelines.length && <section className={styles.assistantStart}>
              <Sparkles />
              <div><strong>让剪辑助理先整理一版草案</strong><span>它只使用当前已批准素材，并保留分镜和 QC 依据；生成后仍由你修改、校验和确认。</span></div>
              {workspace.data.next_action.code === 'RETRY_EDITOR_ASSISTANT' && workspace.data.latest_editor_run
                ? <button className="primaryButton" disabled={retryAssistant.isPending} onClick={() => retryAssistant.mutate()}>{retryAssistant.isPending ? '正在重跑…' : '确认模型费用并重跑'}</button>
                : <button className="primaryButton" disabled={generateAssistant.isPending} onClick={() => generateAssistant.mutate()}>{generateAssistant.isPending ? '正在生成…' : '生成剪辑草案'}</button>}
            </section>}
            {workspace.data.latest_editor_run?.status === 'failed' && <div className={styles.editorAgentError}><AlertTriangle /><div><strong>剪辑助理没有生成有效草案</strong><span>{workspace.data.latest_editor_run.error_detail ?? '模型输出没有通过严格合同检查。'} 系统没有自动重试。</span></div></div>}
            <div className={styles.editorTop}>
              <section className={styles.monitor}>
                <div className={styles.monitorFrame}>{selectedAsset?.asset_type === 'video' ? <video key={selectedAsset.id} src={`/api/v1/projects/${projectId}/assets/${selectedAsset.id}/content`} controls preload="metadata" /> : <Film />}</div>
                <footer><span>{selectedAsset?.node_key ?? (selectedTimeline ? `时间线 v${selectedTimeline.version_number}` : '未选择预览素材')}</span><code>{timecode(timelineDuration)} / {timecode(workspace.data.duration_ms)}</code></footer>
              </section>
              <aside className={styles.assetBin}>
                <header><div><span>APPROVED ASSETS</span><h3>素材箱</h3></div><b>{workspace.data.available_assets.length}</b></header>
                <div>{workspace.data.available_assets.map(asset => {
                  const disabled = !asset.duration_ms || (asset.asset_type === 'audio' && (!audioEnabled || workspace.data.audio_mode === 'off')) || (asset.asset_type === 'subtitle' && !subtitleEnabled)
                  return <article key={asset.id} data-selected={selectedAssetId === asset.id} onClick={() => setSelectedAssetId(asset.id)}><i><FileVideo2 /></i><span><strong>{asset.node_key ?? asset.role}</strong><small>{assetTypeLabels[asset.asset_type]} · {seconds(asset.duration_ms)} · {assetStateLabels[asset.state]}</small></span><button title={draftMode === 'view' ? '创建修订后才能调整素材' : '加入时间线'} disabled={disabled || draftMode === 'view'} onClick={event => { event.stopPropagation(); addAsset(asset) }}><Plus /></button></article>
                })}</div>
              </aside>
            </div>

            <section className={styles.builder}>
              <header>
                <div><span>{draftMode === 'view' ? 'TIMELINE REVIEW' : 'TIMELINE CANDIDATE'}</span><h3>{draftMode === 'view' ? (selectedTimeline ? `审核时间线 v${selectedTimeline.version_number}` : '尚无剪辑草案') : revisionBase ? `修订 v${revisionBase.version_number}` : '新时间线候选'}</h3></div>
                <div className={styles.trackToggles}><label><input type="checkbox" checked={audioEnabled} disabled={draftMode === 'view' || workspace.data.audio_mode === 'off'} onChange={event => setAudioEnabled(event.target.checked)} />音频</label><label><input type="checkbox" checked={subtitleEnabled} disabled={draftMode === 'view'} onChange={event => setSubtitleEnabled(event.target.checked)} />字幕</label></div>
                <button className="secondaryButton" disabled={workspace.data.timelines.length > 0} title={workspace.data.timelines.length > 0 ? '已有版本时请从目标版本创建修订' : '新建首个候选'} onClick={beginNew}><Plus size={14} />新建</button>
                {draftMode !== 'view' && <button className="primaryButton" disabled={!draftItems.length || save.isPending} onClick={() => save.mutate()}><Save size={14} />{revisionBase ? '保存新版本' : '保存候选'}</button>}
              </header>
              {draftMode === 'view'
                ? <div className={styles.reviewNotice}><ShieldCheck /><span>当前是只读审核。需要调整素材、顺序或时间时，请从下方版本创建修订。</span><code>{timecode(timelineDuration)} / {timecode(workspace.data.duration_ms)}</code></div>
                : <div className={styles.timelineTools}><Scissors /><span>显式空位</span><input type="number" min="0.1" step="0.1" value={gapSeconds} onChange={event => setGapSeconds(Number(event.target.value))} /><small>秒</small><button onClick={addGap}><Plus />添加</button><code>{timecode(timelineDuration)} / {timecode(workspace.data.duration_ms)}</code></div>}
              <div className={styles.ruler}>{[0, .2, .4, .6, .8, 1].map(mark => <span key={mark}>{timecode(workspace.data!.duration_ms * mark)}</span>)}</div>
              <div className={styles.tracks}>{(['main_video', 'audio', 'subtitle'] as TrackType[]).map(track => {
                const Icon = trackMeta[track].icon
                const rows = draftItems.map((item, index) => ({ item, index })).filter(row => row.item.track_type === track)
                const disabled = (track === 'audio' && !audioEnabled) || (track === 'subtitle' && !subtitleEnabled)
                return <div className={styles.track} key={track} data-disabled={disabled}><header><Icon /><b>{trackMeta[track].label}</b></header><div>{rows.length ? rows.map(({ item, index }) => <button key={`${track}-${item.sequence_number}-${index}`} className={item.asset_id ? styles.clip : styles.gapClip} data-selected={selectedDraftIndex === index} style={{ flexGrow: Math.max(1, item.timeline_out_ms - item.timeline_in_ms) }} onClick={() => { setSelectedDraftIndex(index); if (item.asset_id) setSelectedAssetId(item.asset_id) }}><strong>{item.label}</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></button>) : <span>{disabled ? '轨道已关闭' : '暂无片段'}</span>}</div></div>
              })}</div>
              {selectedDraftIndex != null && selectedDraftItem && <>
                <div className={styles.inspector}>
                  <div><span>片段检查</span><strong>{selectedDraftItem.label}</strong></div>
                  {(['source_in_ms', 'source_out_ms', 'timeline_in_ms', 'timeline_out_ms'] as const).map(field => <label key={field}>{timeFieldLabels[field]}<input disabled={draftMode === 'view'} type="number" step="0.1" value={(selectedDraftItem[field] ?? 0) / 1000} onChange={event => updateItem(selectedDraftIndex, field, Math.round(Number(event.target.value) * 1000))} /></label>)}
                  {draftMode !== 'view' && <><button title="上移" onClick={() => moveItem(selectedDraftIndex, -1)}><ChevronUp /></button><button title="下移" onClick={() => moveItem(selectedDraftIndex, 1)}><ChevronDown /></button><button title="删除片段" onClick={() => removeItem(selectedDraftIndex)}><Trash2 /></button></>}
                </div>
                {(selectedEvidence || selectedDraftItem.gap_reason) && <div className={styles.editorEvidence} data-gap={!selectedDraftItem.asset_id}>
                  <div><Sparkles /><span><strong>{selectedDraftItem.asset_id ? '剪辑助理选用依据' : '素材空位说明'}</strong><small>{selectedEvidence?.shot_code ? `对应分镜 ${selectedEvidence.shot_code}` : '该段没有绑定素材'}</small></span></div>
                  <p>{selectedDraftItem.gap_reason ?? selectedEvidence?.selection_reason ?? '未记录选择理由'}</p>
                  {selectedEvidence && selectedDraftItem.asset_id && <details><summary>查看审核证据</summary><dl><div><dt>片段编号</dt><dd>{selectedEvidence.timeline_item_code ?? '未记录'}</dd></div><div><dt>QC 报告</dt><dd>{selectedEvidence.qc_report_ids.length ? selectedEvidence.qc_report_ids.join('、') : '未记录'}</dd></div></dl></details>}
                </div>}
              </>}
            </section>

            <section className={styles.versions}>
              <header><div><span>VERSION HISTORY</span><h3>时间线版本</h3></div></header>
              {!workspace.data.timelines.length && <p>尚未创建时间线候选</p>}
              {workspace.data.timelines.map(timeline => <article key={timeline.id} data-selected={selectedTimeline?.id === timeline.id} onClick={() => { setDraftMode('view'); setSelectedTimelineId(timeline.id) }}>
                <div><strong>v{timeline.version_number}</strong><em>{timelineStatusLabels[timeline.status]}</em><span>{timelineSourceLabels[timeline.source]}</span></div><small>{timeline.items.length} 个片段 · {timeline.contract_hash?.slice(0, 12) ?? '尚未校验'}</small>
                <footer>{timeline.status === 'candidate' && <button className="secondaryButton" disabled={validate.isPending} onClick={event => { event.stopPropagation(); validate.mutate(timeline) }}>校验</button>}{timeline.status === 'review' && <button className="primaryButton" onClick={event => { event.stopPropagation(); setConfirming(timeline) }}><Check size={14} />确认合同</button>}{!['exported', 'superseded'].includes(timeline.status) && <button className="secondaryButton" onClick={event => { event.stopPropagation(); beginRevision(timeline) }}>创建修订</button>}</footer>
                {timeline.validation_report.length > 0 && <div className={styles.validation}>{timeline.validation_report.map((row, index) => <p key={`${row.code}-${index}`}><AlertTriangle /><span><b>{row.code}</b>{row.message}</span></p>)}</div>}
              </article>)}
            </section>
          </>}

          {delivery.data && ['delivery_ready', 'blocked', 'completed'].includes(delivery.data.project_status) && <section className={styles.delivery} data-status={delivery.data.project_status}>
            <header><div><span>FINAL DELIVERY</span><h3>最终交付</h3></div><em>{delivery.data.next_action.label}</em></header>
            {delivery.data.confirmed_timeline && <dl>
              <div><dt>时间线</dt><dd>v{delivery.data.confirmed_timeline.version_number} · {delivery.data.confirmed_timeline.status}</dd></div>
              <div><dt>合同哈希</dt><dd><code>{delivery.data.confirmed_timeline.contract_hash}</code></dd></div>
              {deliveryAttempt && <div><dt>请求指纹</dt><dd><code>{deliveryAttempt.request_fingerprint}</code></dd></div>}
            </dl>}
            {!deliveryAttempt && delivery.data.confirmed_timeline && <div className={styles.deliveryAction}><ShieldCheck /><span><strong>等待交付授权</strong><small>当前确认时间线尚未创建交付尝试</small></span><button className="primaryButton" onClick={() => { setDeliveryMethod(null); setAuthorizingDelivery(true) }}>授权交付</button></div>}
            {deliveryAttempt?.status === 'queued' && <div className={styles.deliveryAction}><Clock3 /><span><strong>等待本机生成</strong><small>已进入本地交付队列，系统只执行本次已授权请求</small></span></div>}
            {deliveryAttempt?.status === 'rendering' && <div className={styles.deliveryAction}><RefreshCw className={styles.spinning} /><span><strong>正在生成最终视频</strong><small>FFmpeg 正在按冻结的时间线和编码参数合成 MP4</small></span></div>}
            {deliveryAttempt?.status === 'authorized' && <div className={styles.deliveryAction}><Upload /><span><strong>上传最终 MP4</strong><small>{deliveryFile?.name ?? '尚未选择文件'}</small></span><label className="secondaryButton"><Upload size={14} />选择文件<input type="file" accept="video/mp4,.mp4" onChange={event => setDeliveryFile(event.target.files?.[0] ?? null)} /></label><button className="primaryButton" disabled={!deliveryFile || upload.isPending} onClick={() => upload.mutate()}>上传并登记</button></div>}
            {deliveryAttempt?.status === 'output_registered' && <div className={styles.deliveryAction}><ShieldCheck /><span><strong>{deliveryAttempt.final_asset?.role}</strong><small>{deliveryAttempt.final_asset?.byte_size?.toLocaleString()} bytes · unverified</small></span><button className="primaryButton" disabled={verify.isPending} onClick={() => verify.mutate()}>验证交付文件</button></div>}
            {deliveryAttempt?.status === 'blocked' && <div className={styles.deliveryBlocked}><AlertTriangle /><div><strong>{deliveryAttempt.error_code}</strong><pre>{JSON.stringify(deliveryAttempt.error_detail, null, 2)}</pre></div></div>}
            {deliveryAttempt?.status === 'verified' && deliveryAttempt.final_asset && <div className={styles.deliveryAction}><Check /><span><strong>交付文件已验证</strong><small>{deliveryAttempt.final_asset.width}×{deliveryAttempt.final_asset.height} · {seconds(deliveryAttempt.final_asset.duration_ms)}</small></span><a className="primaryButton" href={`/api/v1/projects/${projectId}/assets/${deliveryAttempt.final_asset.id}/content`}><Download size={14} />下载 MP4</a></div>}
          </section>}
        </>}
        {error && <div className={styles.error}>{error.message}</div>}
      </section>
    </main>
    {confirming && <div className={styles.modal}><section><header><Check /><div><span>TIMELINE AUTHORITY</span><h2>确认剪辑合同 v{confirming.version_number}</h2></div></header><dl><div><dt>快照</dt><dd>{confirming.snapshot_id}</dd></div><div><dt>合同哈希</dt><dd><code>{confirming.contract_hash}</code></dd></div><div><dt>片段</dt><dd>{confirming.items.length}</dd></div></dl><p>确认后该版本不可修改，引用素材进入 used，项目进入 delivery_ready。本操作不会导出、调用供应商或产生费用。</p><footer><button className="secondaryButton" onClick={() => setConfirming(null)}><X size={14} />取消</button><button className="primaryButton" disabled={confirm.isPending} onClick={() => confirm.mutate(confirming)}><Check size={14} />确认当前范围</button></footer></section></div>}
    {authorizingDelivery && delivery.data?.confirmed_timeline && <div className={styles.modal}><section><header><ShieldCheck /><div><span>DELIVERY AUTHORITY</span><h2>授权最终交付</h2></div></header><dl><div><dt>时间线</dt><dd>v{delivery.data.confirmed_timeline.version_number}</dd></div><div><dt>合同哈希</dt><dd><code>{delivery.data.confirmed_timeline.contract_hash}</code></dd></div></dl><div className={styles.deliveryMethods}>{delivery.data.delivery_methods.map(method => <label key={method.kind} data-selected={deliveryMethod === method.kind} data-disabled={!method.available}><input type="radio" name="delivery-method" checked={deliveryMethod === method.kind} disabled={!method.available} onChange={() => setDeliveryMethod(method.kind)} />{method.kind === 'local_ffmpeg' ? <Film /> : <Upload />}<span><strong>{method.label}</strong><small>{method.available ? (method.renderer_version ?? '等待你上传已生成文件') : method.reason}</small></span></label>)}</div><p>{deliveryMethod === 'local_ffmpeg' ? '确认后会创建一次本地合成任务。失败只保留证据，不会自动重试，也不会切换为上传方式。' : deliveryMethod === 'external_upload' ? '确认后只冻结交付请求，等待你上传已经生成的 MP4，不会启动本地渲染器。' : '请选择本次交付方式。每条确认时间线当前只允许一次交付尝试。'}</p><footer><button className="secondaryButton" onClick={() => { setAuthorizingDelivery(false); setDeliveryMethod(null) }}><X size={14} />取消</button><button className="primaryButton" disabled={!deliveryMethod || authorize.isPending} onClick={() => authorize.mutate()}><ShieldCheck size={14} />确认授权</button></footer></section></div>}
  </>
}
