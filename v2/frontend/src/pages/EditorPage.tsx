import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, ChevronDown, ChevronUp, Clock3, FileVideo2, Film, ListVideo, Music2, Plus, RefreshCw, Save, Scissors, Subtitles, Trash2, Video, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { api } from '../api/client'
import type { EditorAsset, Timeline, TimelineItemDraft } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './EditorPage.module.css'

type TrackType = TimelineItemDraft['track_type']

const trackMeta: Record<TrackType, { label: string; icon: typeof Video }> = {
  main_video: { label: '主画面', icon: Video },
  audio: { label: '音频', icon: Music2 },
  subtitle: { label: '字幕', icon: Subtitles },
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

export function EditorPage() {
  const client = useQueryClient()
  const [projectId, setProjectId] = useState('')
  const [selectedTimelineId, setSelectedTimelineId] = useState('')
  const [draftItems, setDraftItems] = useState<TimelineItemDraft[]>([])
  const [revisionBase, setRevisionBase] = useState<Timeline | null>(null)
  const [selectedDraftIndex, setSelectedDraftIndex] = useState<number | null>(null)
  const [selectedAssetId, setSelectedAssetId] = useState('')
  const [audioEnabled, setAudioEnabled] = useState(false)
  const [subtitleEnabled, setSubtitleEnabled] = useState(false)
  const [gapSeconds, setGapSeconds] = useState(2)
  const [confirming, setConfirming] = useState<Timeline | null>(null)
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects, refetchInterval: 5000 })
  const workspace = useQuery({ queryKey: ['editor-workspace', projectId], queryFn: () => api.editorWorkspace(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['editor-workspace', projectId] })
    await client.invalidateQueries({ queryKey: ['projects'] })
  }
  const stage = useMutation({ mutationFn: () => api.approveQualityStage(projectId, workspace.data!.active_snapshot_id!), onSuccess: refresh })
  const save = useMutation({
    mutationFn: () => revisionBase
      ? api.reviseTimelineCandidate(projectId, revisionBase, { audio_enabled: audioEnabled, subtitle_enabled: subtitleEnabled }, draftItems)
      : api.createTimelineCandidate(projectId, workspace.data!.active_snapshot_id!, 'user', { audio_enabled: audioEnabled, subtitle_enabled: subtitleEnabled }, draftItems),
    onSuccess: async timeline => {
      setSelectedTimelineId(timeline.id)
      setRevisionBase(null)
      await refresh()
    },
  })
  const validate = useMutation({ mutationFn: (timeline: Timeline) => api.validateTimeline(projectId, timeline), onSuccess: async timeline => { setSelectedTimelineId(timeline.id); await refresh() } })
  const confirm = useMutation({ mutationFn: (timeline: Timeline) => api.confirmTimeline(projectId, timeline), onSuccess: async timeline => { setConfirming(null); setSelectedTimelineId(timeline.id); await refresh() } })
  const editorProjects = projects.data?.filter(project => ['quality_review', 'editing', 'delivery_ready'].includes(project.status)) ?? []
  const selectedTimeline = workspace.data?.timelines.find(row => row.id === selectedTimelineId) ?? workspace.data?.timelines[0] ?? null
  const selectedAsset = workspace.data?.available_assets.find(asset => asset.id === selectedAssetId) ?? null
  const error = workspace.error || stage.error || save.error || validate.error || confirm.error

  useEffect(() => {
    setSelectedTimelineId('')
    setDraftItems([])
    setRevisionBase(null)
    setSelectedDraftIndex(null)
    setSelectedAssetId('')
  }, [projectId])

  const timelineDuration = useMemo(() => draftItems.reduce((maximum, item) => Math.max(maximum, item.timeline_out_ms), 0), [draftItems])
  const beginNew = () => {
    setDraftItems([])
    setRevisionBase(null)
    setSelectedDraftIndex(null)
    setAudioEnabled(workspace.data?.audio_mode !== 'off')
    setSubtitleEnabled(false)
  }
  const beginRevision = (timeline: Timeline) => {
    setDraftItems(timeline.items.map(item => ({
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
    })))
    setAudioEnabled(timeline.track_config.audio_enabled)
    setSubtitleEnabled(timeline.track_config.subtitle_enabled)
    setRevisionBase(timeline)
    setSelectedDraftIndex(null)
  }
  const addAsset = (asset: EditorAsset) => {
    if (!asset.duration_ms || asset.duration_ms <= 0) return
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
        {editorProjects.map(project => <button key={project.id} data-selected={projectId === project.id} onClick={() => setProjectId(project.id)}><i>{project.status === 'delivery_ready' ? <Check /> : <Clock3 />}</i><span><strong>{project.title}</strong><small>{project.status}</small></span></button>)}
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

          {workspace.data.project_status === 'quality_review' && <div className={styles.gate} data-ready={workspace.data.quality_stage_ready}>
            <div>{workspace.data.quality_stage_ready ? <Check /> : <AlertTriangle />}<span><strong>{workspace.data.quality_stage_ready ? '必需素材已批准' : '质量阶段尚未完成'}</strong><small>{workspace.data.quality_stage_ready ? '当前批准素材集可以进入剪辑' : `${workspace.data.quality_output_gaps.length} 个必需输出仍未满足`}</small></span></div>
            <button className="primaryButton" disabled={!workspace.data.quality_stage_ready || stage.isPending} onClick={() => stage.mutate()}>确认进入剪辑</button>
          </div>}

          {['editing', 'delivery_ready'].includes(workspace.data.project_status) && <>
            <div className={styles.editorTop}>
              <section className={styles.monitor}>
                <div className={styles.monitorFrame}>{selectedAsset?.asset_type === 'video' ? <video key={selectedAsset.id} src={`/api/v1/projects/${projectId}/assets/${selectedAsset.id}/content`} controls preload="metadata" /> : <Film />}</div>
                <footer><span>{selectedAsset?.node_key ?? (selectedTimeline ? `时间线 v${selectedTimeline.version_number}` : '未选择预览素材')}</span><code>{timecode(timelineDuration)} / {timecode(workspace.data.duration_ms)}</code></footer>
              </section>
              <aside className={styles.assetBin}>
                <header><div><span>APPROVED ASSETS</span><h3>素材箱</h3></div><b>{workspace.data.available_assets.length}</b></header>
                <div>{workspace.data.available_assets.map(asset => {
                  const disabled = !asset.duration_ms || (asset.asset_type === 'audio' && (!audioEnabled || workspace.data.audio_mode === 'off')) || (asset.asset_type === 'subtitle' && !subtitleEnabled)
                  return <article key={asset.id} data-selected={selectedAssetId === asset.id} onClick={() => setSelectedAssetId(asset.id)}><i><FileVideo2 /></i><span><strong>{asset.node_key ?? asset.role}</strong><small>{asset.asset_type} · {seconds(asset.duration_ms)} · {asset.state}</small></span><button title="加入时间线" disabled={disabled} onClick={event => { event.stopPropagation(); addAsset(asset) }}><Plus /></button></article>
                })}</div>
              </aside>
            </div>

            <section className={styles.builder}>
              <header>
                <div><span>TIMELINE CANDIDATE</span><h3>{revisionBase ? `修订 v${revisionBase.version_number}` : '新时间线候选'}</h3></div>
                <div className={styles.trackToggles}><label><input type="checkbox" checked={audioEnabled} disabled={workspace.data.audio_mode === 'off'} onChange={event => setAudioEnabled(event.target.checked)} />音频</label><label><input type="checkbox" checked={subtitleEnabled} onChange={event => setSubtitleEnabled(event.target.checked)} />字幕</label></div>
                <button className="secondaryButton" disabled={workspace.data.timelines.length > 0} title={workspace.data.timelines.length > 0 ? '已有版本时请从目标版本创建修订' : '新建首个候选'} onClick={beginNew}><Plus size={14} />新建</button>
                <button className="primaryButton" disabled={!draftItems.length || save.isPending} onClick={() => save.mutate()}><Save size={14} />{revisionBase ? '保存新版本' : '保存候选'}</button>
              </header>
              <div className={styles.timelineTools}><Scissors /><span>显式空位</span><input type="number" min="0.1" step="0.1" value={gapSeconds} onChange={event => setGapSeconds(Number(event.target.value))} /><small>秒</small><button onClick={addGap}><Plus />添加</button><code>{timecode(timelineDuration)} / {timecode(workspace.data.duration_ms)}</code></div>
              <div className={styles.ruler}>{[0, .2, .4, .6, .8, 1].map(mark => <span key={mark}>{timecode(workspace.data!.duration_ms * mark)}</span>)}</div>
              <div className={styles.tracks}>{(['main_video', 'audio', 'subtitle'] as TrackType[]).map(track => {
                const Icon = trackMeta[track].icon
                const rows = draftItems.map((item, index) => ({ item, index })).filter(row => row.item.track_type === track)
                const disabled = (track === 'audio' && !audioEnabled) || (track === 'subtitle' && !subtitleEnabled)
                return <div className={styles.track} key={track} data-disabled={disabled}><header><Icon /><b>{trackMeta[track].label}</b></header><div>{rows.length ? rows.map(({ item, index }) => <button key={`${track}-${item.sequence_number}-${index}`} className={item.asset_id ? styles.clip : styles.gapClip} data-selected={selectedDraftIndex === index} style={{ flexGrow: Math.max(1, item.timeline_out_ms - item.timeline_in_ms) }} onClick={() => { setSelectedDraftIndex(index); if (item.asset_id) setSelectedAssetId(item.asset_id) }}><strong>{item.label}</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></button>) : <span>{disabled ? '轨道已关闭' : '暂无片段'}</span>}</div></div>
              })}</div>
              {selectedDraftIndex != null && draftItems[selectedDraftIndex] && <div className={styles.inspector}>
                <div><span>CLIP INSPECTOR</span><strong>{draftItems[selectedDraftIndex].label}</strong></div>
                {(['source_in_ms', 'source_out_ms', 'timeline_in_ms', 'timeline_out_ms'] as const).map(field => <label key={field}>{field.replace('_ms', '')}<input type="number" step="0.1" value={(draftItems[selectedDraftIndex][field] ?? 0) / 1000} onChange={event => updateItem(selectedDraftIndex, field, Math.round(Number(event.target.value) * 1000))} /></label>)}
                <button title="上移" onClick={() => moveItem(selectedDraftIndex, -1)}><ChevronUp /></button><button title="下移" onClick={() => moveItem(selectedDraftIndex, 1)}><ChevronDown /></button><button title="删除片段" onClick={() => removeItem(selectedDraftIndex)}><Trash2 /></button>
              </div>}
            </section>

            <section className={styles.versions}>
              <header><div><span>VERSION HISTORY</span><h3>时间线版本</h3></div></header>
              {!workspace.data.timelines.length && <p>尚未创建时间线候选</p>}
              {workspace.data.timelines.map(timeline => <article key={timeline.id} data-selected={selectedTimeline?.id === timeline.id} onClick={() => setSelectedTimelineId(timeline.id)}>
                <div><strong>v{timeline.version_number}</strong><em>{timeline.status}</em><span>{timeline.source}</span></div><small>{timeline.items.length} 个片段 · {timeline.contract_hash?.slice(0, 12) ?? '未校验'}</small>
                <footer>{timeline.status === 'candidate' && <button className="secondaryButton" disabled={validate.isPending} onClick={event => { event.stopPropagation(); validate.mutate(timeline) }}>校验</button>}{timeline.status === 'review' && <button className="primaryButton" onClick={event => { event.stopPropagation(); setConfirming(timeline) }}><Check size={14} />确认合同</button>}{!['exported', 'superseded'].includes(timeline.status) && <button className="secondaryButton" onClick={event => { event.stopPropagation(); beginRevision(timeline) }}>创建修订</button>}</footer>
                {timeline.validation_report.length > 0 && <div className={styles.validation}>{timeline.validation_report.map((row, index) => <p key={`${row.code}-${index}`}><AlertTriangle /><span><b>{row.code}</b>{row.message}</span></p>)}</div>}
              </article>)}
            </section>
          </>}
        </>}
        {error && <div className={styles.error}>{error.message}</div>}
      </section>
    </main>
    {confirming && <div className={styles.modal}><section><header><Check /><div><span>TIMELINE AUTHORITY</span><h2>确认剪辑合同 v{confirming.version_number}</h2></div></header><dl><div><dt>快照</dt><dd>{confirming.snapshot_id}</dd></div><div><dt>合同哈希</dt><dd><code>{confirming.contract_hash}</code></dd></div><div><dt>片段</dt><dd>{confirming.items.length}</dd></div></dl><p>确认后该版本不可修改，引用素材进入 used，项目进入 delivery_ready。本操作不会导出、调用供应商或产生费用。</p><footer><button className="secondaryButton" onClick={() => setConfirming(null)}><X size={14} />取消</button><button className="primaryButton" disabled={confirm.isPending} onClick={() => confirm.mutate(confirming)}><Check size={14} />确认当前范围</button></footer></section></div>}
  </>
}
