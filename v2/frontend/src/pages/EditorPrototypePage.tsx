import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Film,
  Eye, EyeOff, Layers3, Lock, Maximize2, Music2, Pause, Play, Plus, Redo2,
  RotateCcw, Scissors, Search, Sparkles, Subtitles, Undo2, Unlock, Volume2,
  VolumeX, WandSparkles, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { Timeline, TimelineItem, TimelineItemDraft, TimelinePreview } from '../api/types'
import styles from './EditorPrototypePage.module.css'

const DEFAULT_PROJECT_ID = 'project_9cd1c4e1fe5c4c8e88466acef2913e72'
const LOCAL_DRAFT_SCHEMA = 'editor-local-draft.v1'

interface LocalEditorDraft {
  schema_version: typeof LOCAL_DRAFT_SCHEMA
  base_timeline_id: string
  base_row_version: number
  items: TimelineItem[]
  timeline_zoom: number
  saved_at: string
}

function timecode(ms: number) {
  const value = Math.max(0, Math.round(ms))
  const minutes = Math.floor(value / 60000)
  const seconds = Math.floor((value % 60000) / 1000)
  const frames = Math.floor((value % 1000) / 40)
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}:${String(frames).padStart(2, '0')}`
}

function seconds(ms: number | null | undefined) {
  return `${((ms ?? 0) / 1000).toFixed(1)}s`
}

function normalizeMainTrack(rows: TimelineItem[], durationMs: number) {
  const merged: TimelineItem[] = []
  for (const row of rows) {
    const rowDuration = Math.max(200, row.timeline_out_ms - row.timeline_in_ms)
    const previous = merged[merged.length - 1]
    if (!row.asset_id && previous && !previous.asset_id) {
      previous.timeline_out_ms += rowDuration
      continue
    }
    merged.push({ ...row, timeline_in_ms: 0, timeline_out_ms: rowDuration })
  }
  let cursor = 0
  const normalized = merged.map((row, index) => {
    const rowDuration = row.timeline_out_ms
    const next = { ...row, sequence_number: index + 1, timeline_in_ms: cursor, timeline_out_ms: cursor + rowDuration }
    cursor += rowDuration
    return next
  })
  if (cursor < durationMs) {
    const last = normalized[normalized.length - 1]
    if (last && !last.asset_id) last.timeline_out_ms = durationMs
    else normalized.push({
      id: `prototype-gap-${Date.now()}`,
      track_type: 'main_video',
      sequence_number: normalized.length + 1,
      asset_id: null,
      asset_state: null,
      asset_type: null,
      asset_duration_ms: null,
      label: '待补素材',
      gap_reason: '当前素材不足以覆盖目标时长',
      source_in_ms: null,
      source_out_ms: null,
      timeline_in_ms: cursor,
      timeline_out_ms: durationMs,
      transform: {},
    })
  }
  return normalized
}

function replaceMainTrack(items: TimelineItem[], mainRows: TimelineItem[]) {
  return [...mainRows, ...items.filter(item => item.track_type !== 'main_video')]
}

function Waveform({ projectId, assetId }: { projectId: string; assetId: string }) {
  const waveform = useQuery({
    queryKey: ['editor-prototype-waveform', projectId, assetId],
    queryFn: () => api.audioWaveform(projectId, assetId, 64),
    staleTime: Infinity,
  })
  if (!waveform.data) return <span className={styles.waveformLoading}>读取波形…</span>
  return <span className={styles.waveform} aria-label="真实音频波形">
    {waveform.data.peaks.map((peak, index) => <i key={index} style={{ height: `${Math.max(6, peak * 100)}%` }} />)}
  </span>
}

function srtTimestampMs(value: string) {
  const match = /^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$/.exec(value.trim())
  if (!match) return null
  return (((Number(match[1]) * 60 + Number(match[2])) * 60 + Number(match[3])) * 1000) + Number(match[4])
}

function activeSrtText(srt: string, sourceTimeMs: number) {
  for (const block of srt.replace(/\r\n/g, '\n').split(/\n{2,}/)) {
    const lines = block.split('\n').map(line => line.trim()).filter(Boolean)
    const timingIndex = lines.findIndex(line => line.includes('-->'))
    if (timingIndex < 0) continue
    const [startText, endText] = lines[timingIndex].split('-->').map(value => value.trim().split(/\s+/)[0])
    const start = srtTimestampMs(startText)
    const end = srtTimestampMs(endText)
    if (start != null && end != null && sourceTimeMs >= start && sourceTimeMs < end) {
      return lines.slice(timingIndex + 1).join('\n')
    }
  }
  return ''
}

function TimelineSubtitle({ projectId, item, playheadMs }: { projectId: string; item: TimelineItem; playheadMs: number }) {
  const subtitle = useQuery({
    queryKey: ['editor-prototype-subtitle', projectId, item.asset_id],
    queryFn: async () => {
      const response = await fetch(`/api/v1/projects/${projectId}/assets/${item.asset_id}/content`)
      if (!response.ok) throw new Error(`字幕读取失败（${response.status}）`)
      return response.text()
    },
    staleTime: Infinity,
  })
  const sourceTimeMs = (item.source_in_ms ?? 0) + Math.max(0, playheadMs - item.timeline_in_ms)
  const text = subtitle.data ? activeSrtText(subtitle.data, sourceTimeMs) : ''
  return text ? <div className={styles.timelineSubtitle}>{text}</div> : null
}

export function EditorPrototypePage() {
  const [params] = useSearchParams()
  const projectId = params.get('project') ?? DEFAULT_PROJECT_ID
  const queryClient = useQueryClient()
  const workspace = useQuery({
    queryKey: ['editor-prototype-workspace', projectId],
    queryFn: () => api.editorWorkspace(projectId),
  })
  const [items, setItems] = useState<TimelineItem[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [playheadMs, setPlayheadMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [assetFilter, setAssetFilter] = useState<'all' | 'video' | 'audio' | 'subtitle'>('all')
  const [notice, setNotice] = useState('原型模式：所有调整只保存在当前浏览器，不会修改真实时间线。')
  const [history, setHistory] = useState<TimelineItem[][]>([])
  const [future, setFuture] = useState<TimelineItem[][]>([])
  const [timelineZoom, setTimelineZoom] = useState(82)
  const [draggedItemId, setDraggedItemId] = useState<string | null>(null)
  const [draggedAssetId, setDraggedAssetId] = useState<string | null>(null)
  const [videoTrackLocked, setVideoTrackLocked] = useState(false)
  const [videoTrackHidden, setVideoTrackHidden] = useState(false)
  const [audioTrackMuted, setAudioTrackMuted] = useState(false)
  const [subtitleTrackHidden, setSubtitleTrackHidden] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [confirmSaveOpen, setConfirmSaveOpen] = useState(false)
  const [validationOpen, setValidationOpen] = useState(false)
  const [lastValidation, setLastValidation] = useState<Timeline | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [lastPreview, setLastPreview] = useState<TimelinePreview | null>(null)
  const [previewReviewChecks, setPreviewReviewChecks] = useState({
    visualContinuity: false,
    subjectiveSync: false,
    subtitleReadability: false,
    warnings: false,
  })
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const timelineAudioRefs = useRef<Record<string, HTMLAudioElement | null>>({})

  const sourceTimeline = workspace.data?.timelines[0] ?? null
  const localDraftKey = `agency-studio.editor-draft.${projectId}`
  useEffect(() => {
    if (!sourceTimeline) return
    let restored: LocalEditorDraft | null = null
    try {
      const raw = window.localStorage.getItem(localDraftKey)
      const parsed = raw ? JSON.parse(raw) as LocalEditorDraft : null
      if (
        parsed?.schema_version === LOCAL_DRAFT_SCHEMA
        && parsed.base_timeline_id === sourceTimeline.id
        && parsed.base_row_version === sourceTimeline.row_version
        && Array.isArray(parsed.items)
      ) restored = parsed
      else if (raw) window.localStorage.removeItem(localDraftKey)
    } catch {
      window.localStorage.removeItem(localDraftKey)
    }
    setItems(restored?.items ?? sourceTimeline.items)
    setTimelineZoom(restored?.timeline_zoom ?? sourceTimeline.track_config.pixels_per_second ?? 82)
    setDirty(Boolean(restored))
    setHistory([])
    setFuture([])
    setSelectedIndex(0)
    setPlayheadMs(0)
    setLastValidation(sourceTimeline)
    setLastPreview(null)
    setPreviewReviewChecks({
      visualContinuity: false,
      subjectiveSync: false,
      subtitleReadability: false,
      warnings: false,
    })
    setPreviewOpen(false)
    setNotice(restored
      ? `已恢复 ${new Date(restored.saved_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 的本地草稿。`
      : '当前时间线已同步；开始调整后会自动保存本地草稿。')
  }, [sourceTimeline?.id, sourceTimeline?.row_version, localDraftKey])

  const durationMs = workspace.data?.duration_ms ?? 15000
  const selectedItem = items[selectedIndex] ?? null
  const selectedAsset = workspace.data?.available_assets.find(asset => asset.id === selectedItem?.asset_id) ?? null
  const subtitlePreview = useQuery({
    queryKey: ['editor-prototype-subtitle', projectId, selectedAsset?.id],
    enabled: selectedAsset?.asset_type === 'subtitle',
    queryFn: async () => {
      const response = await fetch(`/api/v1/projects/${projectId}/assets/${selectedAsset!.id}/content`)
      if (!response.ok) throw new Error(`字幕读取失败（${response.status}）`)
      return response.text()
    },
    staleTime: Infinity,
  })
  const visibleAssets = workspace.data?.available_assets.filter(asset => assetFilter === 'all' || asset.asset_type === assetFilter) ?? []
  const mainItems = useMemo(() => items.filter(item => item.track_type === 'main_video'), [items])
  const audioItems = useMemo(() => items.filter(item => item.track_type === 'audio'), [items])
  const subtitleItems = useMemo(() => items.filter(item => item.track_type === 'subtitle'), [items])
  const activeSubtitleItem = subtitleItems.find(item => item.asset_id && playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms) ?? null
  const unresolvedCount = mainItems.filter(item => !item.asset_id).length
  const timelineWidth = Math.max(900, (durationMs / 1000) * timelineZoom)
  const validationErrors = lastValidation?.validation_report
    ?? sourceTimeline?.validation_report
    ?? []

  useEffect(() => {
    const currentIndex = items.findIndex(item => item.track_type === 'main_video' && playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms)
    if (playing && currentIndex >= 0 && currentIndex !== selectedIndex) setSelectedIndex(currentIndex)
  }, [playheadMs, items, playing, selectedIndex])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !selectedItem?.asset_id) return
    const expectedTime = (selectedItem.source_in_ms ?? 0) / 1000
    if (Math.abs(video.currentTime - expectedTime) > .05) video.currentTime = expectedTime
    if (playing) void video.play()
    else video.pause()
  }, [playing, selectedItem?.id, selectedItem?.asset_id, selectedItem?.source_in_ms])

  useEffect(() => {
    for (const item of audioItems) {
      const audio = timelineAudioRefs.current[item.id]
      if (!audio || !item.asset_id) continue
      const active = playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms
      audio.muted = audioTrackMuted
      if (!active) {
        audio.pause()
        continue
      }
      const sourceIn = item.source_in_ms ?? 0
      const sourceOut = item.source_out_ms ?? item.asset_duration_ms ?? sourceIn
      const sourceDuration = Math.max(1, sourceOut - sourceIn)
      const elapsed = playheadMs - item.timeline_in_ms
      const loop = (item.transform.playback as { mode?: string } | undefined)?.mode === 'loop'
      const expectedSeconds = (sourceIn + (loop ? elapsed % sourceDuration : elapsed)) / 1000
      if (Number.isFinite(audio.duration) && Math.abs(audio.currentTime - expectedSeconds) > .16) {
        audio.currentTime = Math.min(expectedSeconds, Math.max(0, audio.duration - .01))
      }
      if (playing) void audio.play().catch(() => setNotice('浏览器阻止了时间线声音播放，请再次点击播放。'))
      else audio.pause()
    }
  }, [audioItems, audioTrackMuted, playheadMs, playing])

  useEffect(() => {
    if (!sourceTimeline || !dirty || !items.length) return
    const draft: LocalEditorDraft = {
      schema_version: LOCAL_DRAFT_SCHEMA,
      base_timeline_id: sourceTimeline.id,
      base_row_version: sourceTimeline.row_version,
      items,
      timeline_zoom: timelineZoom,
      saved_at: new Date().toISOString(),
    }
    window.localStorage.setItem(localDraftKey, JSON.stringify(draft))
  }, [dirty, items, localDraftKey, sourceTimeline, timelineZoom])

  const draftItems = (): TimelineItemDraft[] => items.map(item => ({
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

  const saveAndValidate = useMutation({
    mutationFn: async () => {
      if (!sourceTimeline) throw new Error('当前没有可修订的时间线版本。')
      const revised = await api.reviseTimelineCandidate(projectId, sourceTimeline, {
        ...sourceTimeline.track_config,
        audio_enabled: audioItems.length > 0,
        subtitle_enabled: subtitleItems.length > 0,
        pixels_per_second: timelineZoom,
      }, draftItems())
      return api.validateTimeline(projectId, revised)
    },
    onSuccess: async timeline => {
      window.localStorage.removeItem(localDraftKey)
      setDirty(false)
      setConfirmSaveOpen(false)
      setLastValidation(timeline)
      setValidationOpen(Boolean(timeline.validation_report.length))
      await queryClient.invalidateQueries({ queryKey: ['editor-prototype-workspace', projectId] })
      await queryClient.invalidateQueries({ queryKey: ['editor-workspace', projectId] })
      setNotice(timeline.validation_report.length
        ? `时间线 v${timeline.version_number} 已保存，检查发现 ${timeline.validation_report.length} 个问题。`
        : `时间线 v${timeline.version_number} 已保存并通过确定性检查。`)
    },
  })

  const renderPreview = useMutation({
    mutationFn: async () => {
      if (!sourceTimeline?.contract_hash) throw new Error('当前时间线还没有可复验的合同哈希。')
      if (dirty) throw new Error('请先保存并检查本地草稿，再生成低清预览。')
      return api.renderTimelinePreview(projectId, sourceTimeline)
    },
    onSuccess: preview => {
      setLastPreview(preview)
      setPreviewReviewChecks({
        visualContinuity: false,
        subjectiveSync: false,
        subtitleReadability: false,
        warnings: false,
      })
      setPreviewOpen(true)
      setNotice(preview.state === 'ready'
        ? `时间线 v${preview.timeline_version_number} 的 ${preview.width}×${preview.height} 低清预览${preview.cached ? '已从缓存读取' : '已生成'}；${preview.quality_report?.status === 'blocked' ? '技术检查存在阻断' : '请继续完成人工观看复核'}。`
        : `低清预览被 ${preview.validation_report.length} 个确定性问题阻断。`)
    },
  })

  const reviewPreview = useMutation({
    mutationFn: async () => {
      if (!sourceTimeline || !lastPreview?.preview_key || !lastPreview.content_hash) {
        throw new Error('当前没有可提交人工复核的精确预览文件。')
      }
      return api.reviewTimelinePreview(
        projectId,
        sourceTimeline,
        lastPreview,
        previewReviewChecks,
      )
    },
    onSuccess: review => {
      setNotice(`低清预览人工复核已保存（${review.review_id}），可以继续确认时间线与正式交付。`)
      setPreviewOpen(false)
    },
  })

  const discardDraft = () => {
    if (!sourceTimeline) return
    window.localStorage.removeItem(localDraftKey)
    setItems(sourceTimeline.items)
    setTimelineZoom(sourceTimeline.track_config.pixels_per_second)
    setHistory([])
    setFuture([])
    setDirty(false)
    setSelectedIndex(0)
    setPlayheadMs(0)
    setNotice('已丢弃本地调整，恢复到当前时间线版本。')
  }

  const selectItem = (item: TimelineItem) => {
    const index = items.indexOf(item)
    setSelectedIndex(index)
    setPlayheadMs(item.timeline_in_ms)
    setPlaying(false)
  }

  const commitItems = (nextItems: TimelineItem[], message: string, selectedId?: string | null) => {
    setHistory(rows => [...rows.slice(-49), items])
    setFuture([])
    setItems(nextItems)
    setDirty(true)
    if (selectedId !== undefined) {
      const nextIndex = selectedId ? nextItems.findIndex(item => item.id === selectedId) : -1
      setSelectedIndex(nextIndex >= 0 ? nextIndex : 0)
    }
    setNotice(message)
  }

  const undo = () => {
    const previous = history[history.length - 1]
    if (!previous) return
    setFuture(rows => [items, ...rows].slice(0, 50))
    setHistory(rows => rows.slice(0, -1))
    setItems(previous)
    setDirty(true)
    setSelectedIndex(index => Math.min(index, Math.max(0, previous.length - 1)))
    setNotice('已撤销上一步本地剪辑操作。')
  }

  const redo = () => {
    const next = future[0]
    if (!next) return
    setHistory(rows => [...rows.slice(-49), items])
    setFuture(rows => rows.slice(1))
    setItems(next)
    setDirty(true)
    setSelectedIndex(index => Math.min(index, Math.max(0, next.length - 1)))
    setNotice('已恢复下一步本地剪辑操作。')
  }

  const shiftItem = (direction: -1 | 1) => {
    if (!selectedItem || selectedItem.track_type !== 'main_video' || videoTrackLocked) return
    const trackIndexes = items.map((item, index) => ({ item, index })).filter(row => row.item.track_type === 'main_video')
    const position = trackIndexes.findIndex(row => row.index === selectedIndex)
    const target = position + direction
    if (target < 0 || target >= trackIndexes.length) return
    const reordered = [...trackIndexes.map(row => row.item)]
    ;[reordered[position], reordered[target]] = [reordered[target], reordered[position]]
    const normalized = normalizeMainTrack(reordered, durationMs)
    commitItems(replaceMainTrack(items, normalized), '已在本地草稿中调整片段顺序。', selectedItem.id)
  }

  const reorderItem = (targetId: string) => {
    if (!draggedItemId || draggedItemId === targetId || videoTrackLocked) return
    const rows = [...mainItems]
    const from = rows.findIndex(item => item.id === draggedItemId)
    const to = rows.findIndex(item => item.id === targetId)
    if (from < 0 || to < 0) return
    const [moved] = rows.splice(from, 1)
    rows.splice(to, 0, moved)
    const normalized = normalizeMainTrack(rows, durationMs)
    commitItems(replaceMainTrack(items, normalized), `已把 ${moved.label} 拖到新的位置。`, moved.id)
    setDraggedItemId(null)
  }

  const dropAssetOnItem = (target: TimelineItem) => {
    if (!draggedAssetId || videoTrackLocked) return
    const asset = workspace.data?.available_assets.find(row => row.id === draggedAssetId)
    if (!asset || asset.asset_type !== 'video' || !asset.duration_ms) return
    const replacement: TimelineItem = {
      ...target,
      id: `prototype-${asset.id}-${Date.now()}`,
      asset_id: asset.id,
      asset_state: asset.state,
      asset_type: asset.asset_type,
      asset_duration_ms: asset.duration_ms,
      label: asset.node_key ?? asset.role,
      gap_reason: null,
      source_in_ms: 0,
      source_out_ms: asset.duration_ms,
      timeline_out_ms: target.timeline_in_ms + asset.duration_ms,
      transform: { fit: 'cover', transition_in: { type: 'cut', duration_ms: 0 }, transition_out: { type: 'cut', duration_ms: 0 } },
    }
    const rows = mainItems.map(item => item.id === target.id ? replacement : item)
    const normalized = normalizeMainTrack(rows, durationMs)
    commitItems(replaceMainTrack(items, normalized), `已把 ${replacement.label} 投放到时间线。`, replacement.id)
    setDraggedAssetId(null)
  }

  const addAssetToTrack = (trackType: TimelineItem['track_type'], assetId = draggedAssetId) => {
    if (!assetId) return
    const asset = workspace.data?.available_assets.find(row => row.id === assetId)
    const expectedType = trackType === 'main_video' ? 'video' : trackType
    if (!asset || asset.asset_type !== expectedType || !asset.duration_ms) {
      setNotice(`该素材不能加入${trackType === 'audio' ? '声音' : '字幕'}轨。`)
      return
    }
    const trackRows = items.filter(item => item.track_type === trackType)
    const cursor = trackRows.reduce((value, item) => Math.max(value, item.timeline_out_ms), 0)
    const clipDuration = Math.min(asset.duration_ms, Math.max(0, durationMs - cursor))
    if (clipDuration < 200) {
      setNotice('当前轨道已没有可用的成片时长。')
      return
    }
    const newItem: TimelineItem = {
      id: `prototype-${asset.id}-${Date.now()}`,
      track_type: trackType,
      sequence_number: trackRows.length + 1,
      asset_id: asset.id,
      asset_state: asset.state,
      asset_type: asset.asset_type,
      asset_duration_ms: asset.duration_ms,
      label: asset.node_key ?? asset.role,
      gap_reason: null,
      source_in_ms: 0,
      source_out_ms: clipDuration,
      timeline_in_ms: cursor,
      timeline_out_ms: cursor + clipDuration,
      transform: trackType === 'audio'
        ? { mix: 'voiceover', playback: { mode: 'trim' }, volume_envelope: [{ time_ms: 0, gain_db: 0 }, { time_ms: clipDuration, gain_db: 0 }] }
        : { render: 'burn_in' },
    }
    commitItems([...items, newItem], `已把 ${newItem.label} 加入${trackType === 'audio' ? '声音' : '字幕'}轨。`, newItem.id)
    if (assetId === draggedAssetId) setDraggedAssetId(null)
  }

  const updateSelectedTransform = (key: string, value: unknown) => {
    if (!selectedItem) return
    commitItems(items.map(item => item.id === selectedItem.id
      ? { ...item, transform: { ...item.transform, [key]: value } }
      : item), `已更新 ${selectedItem.label} 的声音设置。`, selectedItem.id)
  }

  const setSelectedAudioMix = (mix: 'voiceover' | 'background_music') => {
    if (!selectedItem || selectedItem.track_type !== 'audio') return
    const transform: Record<string, unknown> = { ...selectedItem.transform, mix, playback: selectedItem.transform.playback ?? { mode: 'trim' } }
    if (mix === 'background_music') {
      transform.rights = transform.rights ?? { confirmed: false, basis: 'licensed', evidence: '' }
      transform.ducking = transform.ducking ?? { enabled: false, reduction_db: -12, attack_ms: 200, release_ms: 500, regions: [] }
    } else {
      delete transform.rights
      delete transform.ducking
    }
    commitItems(items.map(item => item.id === selectedItem.id ? { ...item, transform } : item), `已设为${mix === 'background_music' ? '背景音乐' : '旁白 / 对白'}。`, selectedItem.id)
  }

  const applySelectedDucking = () => {
    if (!selectedItem || selectedItem.track_type !== 'audio') return
    const regions = audioItems
      .filter(item => item.id !== selectedItem.id && (item.transform.mix ?? 'voiceover') === 'voiceover')
      .filter(item => Math.max(item.timeline_in_ms, selectedItem.timeline_in_ms) < Math.min(item.timeline_out_ms, selectedItem.timeline_out_ms))
      .map(item => ({
        start_ms: Math.max(item.timeline_in_ms, selectedItem.timeline_in_ms) - selectedItem.timeline_in_ms,
        end_ms: Math.min(item.timeline_out_ms, selectedItem.timeline_out_ms) - selectedItem.timeline_in_ms,
      }))
    const current = (selectedItem.transform.ducking as Record<string, unknown> | undefined) ?? {}
    updateSelectedTransform('ducking', {
      enabled: true,
      reduction_db: current.reduction_db ?? -12,
      attack_ms: current.attack_ms ?? 200,
      release_ms: current.release_ms ?? 500,
      regions,
    })
    setNotice(regions.length ? `已按 ${regions.length} 个旁白区间生成 ducking。` : '当前没有与 BGM 重叠的旁白区间。')
  }

  const splitSelected = () => {
    if (!selectedItem?.asset_id || selectedItem.track_type !== 'main_video' || videoTrackLocked) return
    const splitAt = Math.round(playheadMs / 100) * 100
    if (splitAt <= selectedItem.timeline_in_ms + 200 || splitAt >= selectedItem.timeline_out_ms - 200) {
      setNotice('播放头距离片段边缘过近，至少保留 0.2 秒。')
      return
    }
    const leftDuration = splitAt - selectedItem.timeline_in_ms
    const sourceIn = selectedItem.source_in_ms ?? 0
    const left = {
      ...selectedItem,
      id: `${selectedItem.id}-left-${Date.now()}`,
      label: `${selectedItem.label} A`,
      source_out_ms: sourceIn + leftDuration,
      timeline_out_ms: splitAt,
    }
    const right = {
      ...selectedItem,
      id: `${selectedItem.id}-right-${Date.now()}`,
      label: `${selectedItem.label} B`,
      source_in_ms: sourceIn + leftDuration,
      timeline_in_ms: splitAt,
    }
    const rows = mainItems.flatMap(item => item.id === selectedItem.id ? [left, right] : [item])
    const nextItems = replaceMainTrack(items, normalizeMainTrack(rows, durationMs))
    commitItems(nextItems, `已在 ${timecode(splitAt)} 分割片段。`, right.id)
  }

  const deleteSelected = () => {
    if (!selectedItem) return
    if (selectedItem.track_type === 'main_video') {
      if (videoTrackLocked) return
      const rows = mainItems.filter(item => item.id !== selectedItem.id)
      const normalized = normalizeMainTrack(rows, durationMs)
      const nextItems = replaceMainTrack(items, normalized)
      commitItems(nextItems, `已删除 ${selectedItem.label}，后续片段已波纹前移。`, normalized[0]?.id ?? null)
    } else {
      let sequence = 0
      const nextItems = items
        .filter(item => item.id !== selectedItem.id)
        .map(item => item.track_type === selectedItem.track_type ? { ...item, sequence_number: ++sequence } : item)
      commitItems(nextItems, `已从${selectedItem.track_type === 'audio' ? '声音' : '字幕'}轨移除 ${selectedItem.label}。`, nextItems[0]?.id ?? null)
    }
    setPlayheadMs(Math.min(playheadMs, durationMs))
  }

  const beginTrim = (event: React.PointerEvent, item: TimelineItem, edge: 'start' | 'end') => {
    event.stopPropagation()
    if (!item.asset_id || videoTrackLocked) return
    const startX = event.clientX
    const original = { sourceIn: item.source_in_ms ?? 0, sourceOut: item.source_out_ms ?? item.asset_duration_ms ?? 0 }
    setHistory(rows => [...rows.slice(-49), items])
    setFuture([])
    setDirty(true)
    const onMove = (moveEvent: PointerEvent) => {
      const deltaMs = Math.round(((moveEvent.clientX - startX) / timelineZoom) * 10000) / 10
      setItems(current => {
        const currentMain = current.filter(row => row.track_type === 'main_video').map(row => {
          if (row.id !== item.id) return row
          if (edge === 'start') {
            const sourceIn = Math.max(0, Math.min(original.sourceOut - 200, original.sourceIn + deltaMs))
            return { ...row, source_in_ms: sourceIn, timeline_out_ms: row.timeline_in_ms + (original.sourceOut - sourceIn) }
          }
          const sourceOut = Math.max(original.sourceIn + 200, Math.min(item.asset_duration_ms ?? original.sourceOut, original.sourceOut + deltaMs))
          return { ...row, source_out_ms: sourceOut, timeline_out_ms: row.timeline_in_ms + (sourceOut - original.sourceIn) }
        })
        return replaceMainTrack(current, normalizeMainTrack(currentMain, durationMs))
      })
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setNotice(`已拖动${edge === 'start' ? '左' : '右'}边缘裁切片段，后续片段自动波纹对齐。`)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('input, select, textarea')) return
      if (event.code === 'Space') {
        event.preventDefault()
        setPlaying(value => !value)
      }
      if (event.key.toLowerCase() === 's') {
        event.preventDefault()
        splitSelected()
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault()
        deleteSelected()
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) redo()
        else undo()
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
        event.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  const handleTimeUpdate = () => {
    const video = videoRef.current
    if (!video || !selectedItem) return
    const sourceIn = selectedItem.source_in_ms ?? 0
    const next = selectedItem.timeline_in_ms + Math.max(0, video.currentTime * 1000 - sourceIn)
    setPlayheadMs(Math.min(next, selectedItem.timeline_out_ms))
  }

  const handleEnded = () => {
    const next = mainItems.find(item => item.timeline_in_ms >= (selectedItem?.timeline_out_ms ?? 0))
    if (!next || !next.asset_id) {
      setPlaying(false)
      setNotice(next ? '播放到缺口：需要先选择一种补齐方式。' : '时间线预览播放完成。')
      return
    }
    selectItem(next)
    setPlaying(true)
  }

  const locateValidationError = (error: Timeline['validation_report'][number]) => {
    const match = /^items\.(main_video|audio|subtitle)\.(\d+)/.exec(error.path)
    if (match) {
      const sequence = Number(match[2])
      const item = items.find(row => row.track_type === match[1] && row.sequence_number === sequence)
      if (item) {
        selectItem(item)
        setNotice(`已定位：${error.message}`)
      }
    } else {
      setNotice(error.message)
    }
    setValidationOpen(false)
  }

  if (workspace.isPending) return <main className={styles.loading}><Film /><strong>正在装载新版剪辑台原型…</strong></main>
  if (!workspace.data || workspace.error) return <main className={styles.loading}><AlertTriangle /><strong>原型无法读取当前剪辑项目</strong></main>

  return <main className={styles.prototype}>
    <header className={styles.topbar}>
      <Link to={`/editor?project=${projectId}`} title="返回现有剪辑台"><ArrowLeft /></Link>
      <div className={styles.projectTitle}>
        <span>剪辑台新版原型</span>
        <strong>{workspace.data.project_title}</strong>
      </div>
      <button className={styles.versionButton}>时间线 v{sourceTimeline?.version_number ?? '--'} <small>{dirty ? '本地草稿未提交' : '已同步'}</small></button>
      <div className={styles.topActions}>
        <button title="撤销" disabled={!history.length} onClick={undo}><Undo2 /></button>
        <button title="重做" disabled={!future.length} onClick={redo}><Redo2 /></button>
        {dirty && <button title="丢弃本地草稿" onClick={discardDraft}><RotateCcw /></button>}
        <button className={styles.primaryAction} disabled={saveAndValidate.isPending} onClick={() => {
          if (dirty) setConfirmSaveOpen(true)
          else if (validationErrors.length || unresolvedCount) setValidationOpen(true)
          else setNotice('当前版本已经通过检查，可以进入确认阶段。')
        }}>
          {dirty ? <CheckCircle2 /> : unresolvedCount || validationErrors.length ? <AlertTriangle /> : <CheckCircle2 />}
          {saveAndValidate.isPending ? '正在保存…' : dirty ? '保存并检查' : unresolvedCount || validationErrors.length ? `处理 ${Math.max(unresolvedCount, validationErrors.length)} 个问题` : '版本已通过'}
        </button>
      </div>
    </header>

    <section className={styles.statusbar} data-warning={unresolvedCount > 0 || validationErrors.length > 0 || Boolean(saveAndValidate.error) || Boolean(renderPreview.error) || Boolean(reviewPreview.error)}>
      {unresolvedCount || validationErrors.length || saveAndValidate.error || renderPreview.error || reviewPreview.error ? <AlertTriangle /> : <CheckCircle2 />}
      <span>{notice}</span>
      {saveAndValidate.error && <button onClick={() => setConfirmSaveOpen(true)}>{saveAndValidate.error instanceof Error ? saveAndValidate.error.message : '保存失败，请重试'}</button>}
      {renderPreview.error && <button onClick={() => renderPreview.mutate()}>{renderPreview.error instanceof Error ? renderPreview.error.message : '低清预览失败，请重试'}</button>}
      {reviewPreview.error && <button onClick={() => reviewPreview.mutate()}>{reviewPreview.error instanceof Error ? reviewPreview.error.message : '人工复核保存失败，请重试'}</button>}
      {validationErrors.length > 0 && <button onClick={() => setValidationOpen(true)}>查看 {validationErrors.length} 个检查问题</button>}
      <code>{workspace.data.aspect_ratio} · {seconds(durationMs)} · 预览质量</code>
    </section>

    <section className={styles.editingArea}>
      <aside className={styles.assetPanel}>
        <header><div><span>ASSETS</span><strong>素材箱</strong></div><button title="搜索素材"><Search /></button></header>
        <nav>
          {([
            ['all', '全部', Layers3],
            ['video', '视频', Film],
            ['audio', '声音', Music2],
            ['subtitle', '字幕', Subtitles],
          ] as const).map(([key, label, Icon]) => <button key={key} data-active={assetFilter === key} onClick={() => setAssetFilter(key)}><Icon />{label}</button>)}
        </nav>
        <div className={styles.assetList}>
          {visibleAssets.map(asset => <button
            key={asset.id}
            draggable={Boolean(asset.duration_ms)}
            data-selected={selectedAsset?.id === asset.id}
            onDragStart={() => {
              setDraggedAssetId(asset.id)
              setDraggedItemId(null)
              setNotice(`正在拖动 ${asset.node_key ?? asset.role}，可投放到对应轨道。`)
            }}
            onDragEnd={() => setDraggedAssetId(null)}
            onClick={() => {
              const item = items.find(row => row.asset_id === asset.id)
              if (item) selectItem(item)
              else if (asset.asset_type === 'audio' || asset.asset_type === 'subtitle') addAssetToTrack(asset.asset_type, asset.id)
              else setNotice(`${asset.node_key ?? asset.role} 尚未加入当前时间线，请拖到目标画面位置。`)
          }}>
            <i>{asset.asset_type === 'video' ? <Film /> : asset.asset_type === 'audio' ? <Music2 /> : <Subtitles />}</i>
            <span><strong>{asset.node_key ?? asset.role}</strong><small>{seconds(asset.duration_ms)} · {asset.width && asset.height ? `${asset.width}×${asset.height}` : '已批准'}</small></span>
            <Plus />
          </button>)}
        </div>
      </aside>

      <section className={styles.previewPanel}>
        <div className={styles.monitor}>
          {selectedAsset?.asset_type === 'video' && <video
            ref={videoRef}
            key={selectedAsset.id}
            src={`/api/v1/projects/${projectId}/assets/${selectedAsset.id}/content`}
            preload="metadata"
            onTimeUpdate={handleTimeUpdate}
            onEnded={handleEnded}
          />}
          {selectedAsset?.asset_type === 'audio' && <div className={styles.audioPreview}>
            <Music2 />
            <strong>{selectedItem?.label}</strong>
            <Waveform projectId={projectId} assetId={selectedAsset.id} />
            <audio controls muted={audioTrackMuted} src={`/api/v1/projects/${projectId}/assets/${selectedAsset.id}/content`} />
          </div>}
          {selectedAsset?.asset_type === 'subtitle' && <div className={styles.subtitlePreview}>
            <Subtitles />
            <strong>{selectedItem?.label}</strong>
            <pre>{subtitlePreview.isPending ? '正在读取字幕…' : subtitlePreview.error ? '字幕读取失败' : subtitlePreview.data}</pre>
          </div>}
          {audioItems.filter(item => item.asset_id).map(item => <audio
            key={`timeline-audio-${item.id}`}
            className={styles.timelineAudio}
            ref={node => { timelineAudioRefs.current[item.id] = node }}
            preload="metadata"
            src={`/api/v1/projects/${projectId}/assets/${item.asset_id}/content`}
          />)}
          {!subtitleTrackHidden && activeSubtitleItem && <TimelineSubtitle projectId={projectId} item={activeSubtitleItem} playheadMs={playheadMs} />}
          {!selectedAsset && <div className={styles.gapPreview}><AlertTriangle /><strong>缺少画面</strong><span>{selectedItem ? seconds(selectedItem.timeline_out_ms - selectedItem.timeline_in_ms) : '选择一个片段'}</span></div>}
          <div className={styles.monitorBadge}>时间线预览</div>
          <button className={styles.fullscreenButton} title="全屏"><Maximize2 /></button>
        </div>
        <div className={styles.transport}>
          <button title="跳到开头" onClick={() => setPlayheadMs(0)}><ChevronLeft /></button>
          <button title={playing ? '暂停' : '播放'} className={styles.playButton} onClick={() => setPlaying(value => !value)}>{playing ? <Pause /> : <Play />}</button>
          <button title="跳到结尾" onClick={() => setPlayheadMs(durationMs)}><ChevronRight /></button>
          <code>{timecode(playheadMs)} <span>/ {timecode(durationMs)}</span></code>
          <div className={styles.previewScrubber} role="slider" aria-label="预览播放头" aria-valuemin={0} aria-valuemax={durationMs} aria-valuenow={playheadMs} tabIndex={0} onClick={event => {
            const rect = event.currentTarget.getBoundingClientRect()
            setPlayheadMs(Math.round(((event.clientX - rect.left) / rect.width) * durationMs))
            setPlaying(false)
          }}><i style={{ width: `${Math.min(100, (playheadMs / durationMs) * 100)}%` }} /></div>
          <Volume2 />
          <button>适应</button>
        </div>
      </section>

      <aside className={styles.inspector}>
        <header><span>INSPECTOR</span><strong>{selectedItem?.asset_id ? '片段属性' : '缺口处理'}</strong></header>
        {selectedItem?.asset_id ? <>
          <section className={styles.clipIdentity}><i>{selectedItem.track_type === 'main_video' ? <Film /> : selectedItem.track_type === 'audio' ? <Music2 /> : <Subtitles />}</i><div><strong>{selectedItem.label}</strong><span>{selectedItem.track_type === 'main_video' ? `对应分镜 ${selectedItem.label.split('.')[0]}` : selectedItem.track_type === 'audio' ? '真实音频素材' : '烧录字幕素材'}</span></div></section>
          <section>
            <h3>素材范围</h3>
            <div className={styles.rangeLabels}><span>{seconds(selectedItem.source_in_ms)}</span><b>{seconds((selectedItem.source_out_ms ?? 0) - (selectedItem.source_in_ms ?? 0))}</b><span>{seconds(selectedItem.source_out_ms)}</span></div>
            <div className={styles.trimHint}>{selectedItem.track_type === 'main_video' ? '直接拖动时间线片段两侧把手裁切' : '时间范围随不可变时间线版本保存'}</div>
          </section>
          {selectedItem.track_type === 'main_video' && <section>
            <h3>片段操作</h3>
            <div className={styles.actionGrid}>
              <button onClick={splitSelected}><Scissors />播放头分割</button>
              <button onClick={() => shiftItem(-1)}><ChevronLeft />向前移动</button>
              <button onClick={() => shiftItem(1)}><ChevronRight />向后移动</button>
              <button onClick={deleteSelected}>移除片段</button>
            </div>
          </section>}
          {selectedItem.track_type === 'main_video' && <section>
            <h3>转场</h3>
            <label>入场<select defaultValue="cut"><option value="cut">直接切换</option><option value="fade">淡入</option></select></label>
            <label>出场<select defaultValue="cut"><option value="cut">直接切换</option><option value="fade">淡出</option></select></label>
          </section>}
          {selectedItem.track_type === 'audio' && <section className={styles.audioInspector}>
            <h3>声音角色与混音</h3>
            <label>用途<select value={String(selectedItem.transform.mix ?? 'voiceover')} onChange={event => setSelectedAudioMix(event.target.value as 'voiceover' | 'background_music')}><option value="voiceover">旁白 / 对白</option><option value="background_music">背景音乐 BGM</option></select></label>
            <label>片段音量<input aria-label="片段音量" type="range" min="-24" max="12" step=".5" value={Number(((selectedItem.transform.volume_envelope as Array<{ gain_db: number }> | undefined)?.[0]?.gain_db) ?? 0)} onChange={event => {
              const gain = Number(event.target.value)
              const clipDuration = selectedItem.timeline_out_ms - selectedItem.timeline_in_ms
              updateSelectedTransform('volume_envelope', [{ time_ms: 0, gain_db: gain }, { time_ms: clipDuration, gain_db: gain }])
            }} /></label>
            {selectedItem.transform.mix === 'background_music' && <>
              <div className={styles.audioFact}><strong>BGM 权利证据</strong><span>{(selectedItem.transform.rights as { confirmed?: boolean } | undefined)?.confirmed ? '将在新版本中冻结' : '保存前必须确认'}</span></div>
              <label>权利依据<select value={String(((selectedItem.transform.rights as Record<string, unknown> | undefined)?.basis) ?? 'licensed')} onChange={event => updateSelectedTransform('rights', { ...((selectedItem.transform.rights as Record<string, unknown> | undefined) ?? {}), basis: event.target.value })}><option value="owned">自有</option><option value="licensed">已许可</option><option value="royalty_free">免版税</option></select></label>
              <label>证据说明<input aria-label="BGM 权利证据" value={String(((selectedItem.transform.rights as Record<string, unknown> | undefined)?.evidence) ?? '')} onChange={event => updateSelectedTransform('rights', { ...((selectedItem.transform.rights as Record<string, unknown> | undefined) ?? {}), evidence: event.target.value })} /></label>
              <label>确认权利<input aria-label="确认 BGM 权利" type="checkbox" checked={Boolean((selectedItem.transform.rights as Record<string, unknown> | undefined)?.confirmed)} onChange={event => updateSelectedTransform('rights', { ...((selectedItem.transform.rights as Record<string, unknown> | undefined) ?? {}), confirmed: event.target.checked })} /></label>
              <button className={styles.duckingButton} onClick={applySelectedDucking}><VolumeX />按旁白区间生成 Ducking</button>
              <small>压低 {String(((selectedItem.transform.ducking as Record<string, unknown> | undefined)?.reduction_db) ?? -12)} dB · attack {String(((selectedItem.transform.ducking as Record<string, unknown> | undefined)?.attack_ms) ?? 200)}ms · release {String(((selectedItem.transform.ducking as Record<string, unknown> | undefined)?.release_ms) ?? 500)}ms</small>
            </>}
            <button className={styles.removeTrackItem} onClick={deleteSelected}>从声音轨移除</button>
          </section>}
          {selectedItem.track_type === 'subtitle' && <section className={styles.subtitleInspector}>
            <h3>字幕内容</h3>
            <pre>{subtitlePreview.isPending ? '正在读取字幕…' : subtitlePreview.error ? '字幕读取失败' : subtitlePreview.data}</pre>
            <div className={styles.trimHint}>当前版本使用烧录字幕；样式和安全区将在低清预渲染阶段检查。</div>
            <button className={styles.removeTrackItem} onClick={deleteSelected}>从字幕轨移除</button>
          </section>}
        </> : <section className={styles.gapActions}>
          <div className={styles.gapTitle}><AlertTriangle /><span><strong>缺少 {selectedItem ? seconds(selectedItem.timeline_out_ms - selectedItem.timeline_in_ms) : '0.9s'} 画面</strong><small>当前素材不足以覆盖 15 秒目标时长</small></span></div>
          <button onClick={() => setNotice('已选择缩短成片；正式版本将先显示影响预览。')}><Clock3 /><span><strong>缩短成片</strong><small>使用现有素材总时长</small></span></button>
          <button onClick={() => setNotice('将创建一个明确的补充镜头生产请求。')}><WandSparkles /><span><strong>生成补充镜头</strong><small>返回生产流程，可能产生费用</small></span></button>
          <button onClick={() => setNotice('请从素材箱选择另一个已批准视频。')}><Plus /><span><strong>选择其他素材</strong><small>只使用已批准且未归档素材</small></span></button>
        </section>}
      </aside>
    </section>

    <section className={styles.timelinePanel}>
      <header className={styles.timelineToolbar}>
        <div><strong>时间线</strong><span>{mainItems.length} 个画面片段 · {audioItems.length} 个音频 · {subtitleItems.length} 个字幕</span></div>
        <button onClick={splitSelected}><Scissors />分割</button>
        <button disabled={renderPreview.isPending} onClick={() => {
          if (dirty) setNotice('请先保存并检查本地草稿，再生成低清预览。')
          else renderPreview.mutate()
        }}><Film />{renderPreview.isPending ? '预览生成中…' : '低清预览'}</button>
        <button>磁吸 100ms</button>
        <label>缩放<input aria-label="时间线缩放" type="range" min="40" max="180" value={timelineZoom} onChange={event => { setTimelineZoom(Number(event.target.value)); setDirty(true) }} /></label>
        <code>{timecode(playheadMs)}</code>
      </header>
      <div className={styles.timelineViewport}>
        <div className={styles.timelineCanvas} style={{ width: `${84 + timelineWidth}px` }} onClick={event => {
          if ((event.target as HTMLElement).closest('button')) return
          const rect = event.currentTarget.getBoundingClientRect()
          const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - 84) / timelineWidth))
          setPlayheadMs(Math.round(ratio * durationMs))
          setPlaying(false)
        }}>
        <div className={styles.ruler}><span /><div>{[0, 3, 6, 9, 12, 15].map(value => <i key={value} style={{ left: `${(value / 15) * 100}%` }}>{`00:${String(value).padStart(2, '0')}`}</i>)}</div></div>
        <div className={styles.trackRow} data-track-hidden={videoTrackHidden}>
          <label><Film /><span>画面</span><button title={videoTrackHidden ? '显示画面轨' : '隐藏画面轨'} onClick={() => setVideoTrackHidden(value => !value)}>{videoTrackHidden ? <EyeOff /> : <Eye />}</button><button title={videoTrackLocked ? '解锁画面轨' : '锁定画面轨'} onClick={() => setVideoTrackLocked(value => !value)}>{videoTrackLocked ? <Lock /> : <Unlock />}</button></label>
          <div className={styles.trackLane} data-locked={videoTrackLocked} onDragOver={event => event.preventDefault()}>
            {mainItems.map(item => <button
              key={item.id}
              draggable={!videoTrackLocked}
              className={item.asset_id ? styles.videoClip : styles.gapClip}
              data-selected={selectedItem?.id === item.id}
              style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }}
              onDragStart={() => { setDraggedItemId(item.id); setDraggedAssetId(null) }}
              onDragEnd={() => setDraggedItemId(null)}
              onDragOver={event => event.preventDefault()}
              onDrop={event => {
                event.preventDefault()
                if (draggedAssetId) dropAssetOnItem(item)
                else reorderItem(item.id)
              }}
              onClick={() => selectItem(item)}
            >{item.asset_id && <i role="slider" aria-label={`${item.label} 左侧裁切把手`} aria-valuemin={0} aria-valuemax={item.asset_duration_ms ?? 0} aria-valuenow={item.source_in_ms ?? 0} tabIndex={0} className={styles.trimHandle} data-edge="start" onPointerDown={event => beginTrim(event, item, 'start')} />}
              {item.asset_id ? <><Film /><span><strong>{item.label}</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></> : <><AlertTriangle /><span><strong>缺少画面</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></>}
              {item.asset_id && <i role="slider" aria-label={`${item.label} 右侧裁切把手`} aria-valuemin={0} aria-valuemax={item.asset_duration_ms ?? 0} aria-valuenow={item.source_out_ms ?? 0} tabIndex={0} className={styles.trimHandle} data-edge="end" onPointerDown={event => beginTrim(event, item, 'end')} />}
            </button>)}
          </div>
        </div>
        <div className={styles.trackRow}>
          <label><Music2 /><span>声音</span><button title={audioTrackMuted ? '恢复声音轨' : '静音声音轨'} onClick={() => setAudioTrackMuted(value => !value)}>{audioTrackMuted ? <VolumeX /> : <Volume2 />}</button></label>
          <div className={styles.trackLane} data-empty={!audioItems.length} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); addAssetToTrack('audio') }}>{audioItems.length ? audioItems.map(item => <button key={item.id} data-selected={selectedItem?.id === item.id} className={styles.audioClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }} onClick={() => selectItem(item)}><Waveform projectId={projectId} assetId={item.asset_id!} /><strong>{item.label}</strong></button>) : <span>拖入已批准配音或 BGM</span>}</div>
        </div>
        <div className={styles.trackRow} data-track-hidden={subtitleTrackHidden}>
          <label><Subtitles /><span>字幕</span><button title={subtitleTrackHidden ? '显示字幕轨' : '隐藏字幕轨'} onClick={() => setSubtitleTrackHidden(value => !value)}>{subtitleTrackHidden ? <EyeOff /> : <Eye />}</button></label>
          <div className={styles.trackLane} data-empty={!subtitleItems.length} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); addAssetToTrack('subtitle') }}>{subtitleItems.length ? subtitleItems.map(item => <button key={item.id} data-selected={selectedItem?.id === item.id} className={styles.subtitleClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }} onClick={() => selectItem(item)}><Subtitles /><strong>{item.label}</strong></button>) : <span>拖入已批准字幕</span>}</div>
        </div>
        <i className={styles.playhead} style={{ left: `${84 + timelineWidth * (playheadMs / durationMs)}px` }}><b /></i>
        </div>
      </div>
      <footer><span><Sparkles />AI 初剪依据和版本证据已收进右侧抽屉</span><span>Space 播放 · S 分割 · Delete 删除 · Ctrl+Z 撤销</span></footer>
    </section>
    {confirmSaveOpen && <div className={styles.modal}><section>
      <header><CheckCircle2 /><div><span>IMMUTABLE REVISION</span><h2>保存并检查新时间线版本</h2></div><button title="关闭" onClick={() => setConfirmSaveOpen(false)}><X /></button></header>
      <p>这会基于时间线 v{sourceTimeline?.version_number} 创建不可变的新版本，然后立即执行确定性检查。不会确认交付、启动渲染或产生供应商费用。</p>
      <dl>
        <div><dt>新版本</dt><dd>v{(sourceTimeline?.version_number ?? 0) + 1}</dd></div>
        <div><dt>画面片段</dt><dd>{mainItems.length}</dd></div>
        <div><dt>显式空位</dt><dd>{unresolvedCount}</dd></div>
        <div><dt>基线版本</dt><dd>v{sourceTimeline?.version_number} · row {sourceTimeline?.row_version}</dd></div>
      </dl>
      {unresolvedCount > 0 && <div className={styles.modalWarning}><AlertTriangle /><span>允许保存含空位的候选，但检查不会通过；保存后会精确定位这些问题。</span></div>}
      <footer><button onClick={() => setConfirmSaveOpen(false)}>继续调整</button><button className={styles.confirmButton} disabled={saveAndValidate.isPending} onClick={() => saveAndValidate.mutate()}>{saveAndValidate.isPending ? '正在创建并检查…' : '创建新版本并检查'}</button></footer>
    </section></div>}
    {validationOpen && <div className={styles.modal}><section>
      <header><AlertTriangle /><div><span>VALIDATION ISSUES</span><h2>需要处理的时间线问题</h2></div><button title="关闭" onClick={() => setValidationOpen(false)}><X /></button></header>
      <p>点击问题会定位到对应片段。技术代码只用于审计，实际处理以中文说明为准。</p>
      <div className={styles.validationList}>
        {(validationErrors.length ? validationErrors : mainItems.filter(item => !item.asset_id).map(item => ({
          code: 'TIMELINE_GAP_UNRESOLVED',
          path: `items.main_video.${item.sequence_number}`,
          message: '候选保留了显式空位，必须完成素材取舍后才能确认。',
          evidence: {},
        }))).map(error => <button key={`${error.code}-${error.path}`} onClick={() => locateValidationError(error)}>
          <AlertTriangle /><span><strong>{error.message}</strong><small>{error.path}</small></span><code>{error.code}</code>
        </button>)}
      </div>
      <footer><button onClick={() => setValidationOpen(false)}>返回时间线</button></footer>
    </section></div>}
    {previewOpen && lastPreview && <div className={styles.modal}><section className={styles.previewModal}>
      <header><Film /><div><span>DRAFT PRE-RENDER</span><h2>时间线 v{lastPreview.timeline_version_number} 低清预览</h2></div><button title="关闭" onClick={() => setPreviewOpen(false)}><X /></button></header>
      {lastPreview.state === 'ready' && lastPreview.content_url ? <>
        <div className={styles.renderedPreview}><video controls src={lastPreview.content_url} /></div>
        <dl>
          <div><dt>预览规格</dt><dd>{lastPreview.width}×{lastPreview.height} · {lastPreview.fps}fps</dd></div>
          <div><dt>来源</dt><dd>{lastPreview.cached ? '确定性缓存' : '本机 FFmpeg 新生成'}</dd></div>
          <div><dt>文件大小</dt><dd>{lastPreview.byte_size ? `${(lastPreview.byte_size / 1024 / 1024).toFixed(1)} MB` : '--'}</dd></div>
          <div><dt>合同状态</dt><dd>仅预览，不是交付</dd></div>
        </dl>
        {lastPreview.quality_report && <section className={styles.previewQuality}>
          <header data-state={lastPreview.quality_report.status}>
            {lastPreview.quality_report.status === 'blocked' ? <AlertTriangle /> : <CheckCircle2 />}
            <div>
              <strong>{lastPreview.quality_report.status === 'blocked' ? '技术检查有阻断' : lastPreview.quality_report.status === 'review_required' ? '技术检查完成，等待人工复核' : '预览质量检查通过'}</strong>
              <small>自动检查不能代替观看确认；阻断项必须处理，警告与人工项需要逐项观看。</small>
            </div>
          </header>
          <div className={styles.previewQualityList}>
            {lastPreview.quality_report.checks.map(check => <article key={check.code} data-state={check.state}>
              {check.state === 'passed' ? <CheckCircle2 /> : <AlertTriangle />}
              <span><strong>{check.message}</strong><small>{check.code}</small></span>
              <b>{check.state === 'blocked' ? '阻断' : check.state === 'warning' ? '警告' : check.state === 'manual_review' ? '人工检查' : '通过'}</b>
            </article>)}
          </div>
        </section>}
        {lastPreview.quality_report && lastPreview.quality_report.status !== 'blocked' && <fieldset className={styles.previewReviewChecklist}>
          <legend>观看后逐项确认</legend>
          <label><input type="checkbox" checked={previewReviewChecks.visualContinuity} onChange={event => setPreviewReviewChecks(value => ({ ...value, visualContinuity: event.target.checked }))} /><span><strong>画面连续性</strong><small>已完整观看镜头衔接、主体一致性、动作连续性和异常闪跳。</small></span></label>
          <label><input type="checkbox" checked={previewReviewChecks.subjectiveSync} onChange={event => setPreviewReviewChecks(value => ({ ...value, subjectiveSync: event.target.checked }))} /><span><strong>主观音画同步</strong><small>已检查旁白、音乐、字幕与画面节奏是否符合预期。</small></span></label>
          {sourceTimeline?.track_config.subtitle_enabled && <label><input type="checkbox" checked={previewReviewChecks.subtitleReadability} onChange={event => setPreviewReviewChecks(value => ({ ...value, subtitleReadability: event.target.checked }))} /><span><strong>字幕可读性</strong><small>已检查文字、换行、遮挡和画面安全区。</small></span></label>}
          {lastPreview.quality_report.checks.some(check => check.state === 'warning') && <label><input type="checkbox" checked={previewReviewChecks.warnings} onChange={event => setPreviewReviewChecks(value => ({ ...value, warnings: event.target.checked }))} /><span><strong>警告项已逐项确认</strong><small>已确认检测到的黑画面或其他警告均为有意效果或可接受结果。</small></span></label>}
        </fieldset>}
      </> : <>
        <p>低清预览没有启动 FFmpeg。请先处理以下合同问题；点击条目可定位到对应片段。</p>
        <div className={styles.validationList}>
          {lastPreview.validation_report.map(error => <button key={`${error.code}-${error.path}`} onClick={() => {
            setPreviewOpen(false)
            locateValidationError(error)
          }}><AlertTriangle /><span><strong>{error.message}</strong><small>{error.path}</small></span><code>{error.code}</code></button>)}
        </div>
      </>}
      <div className={styles.modalWarning}><AlertTriangle /><span>低清预览只写入本机缓存，不确认时间线、不创建交付任务、不登记正式成片，也不产生供应商费用。</span></div>
      <footer>
        <button onClick={() => setPreviewOpen(false)}>返回时间线</button>
        {lastPreview.state === 'ready' && <button onClick={() => renderPreview.mutate()}>{renderPreview.isPending ? '检查中…' : '重新检查缓存'}</button>}
        {lastPreview.state === 'ready' && lastPreview.quality_report?.status !== 'blocked' && <button
          className={styles.confirmButton}
          disabled={
            reviewPreview.isPending
            || !previewReviewChecks.visualContinuity
            || !previewReviewChecks.subjectiveSync
            || Boolean(sourceTimeline?.track_config.subtitle_enabled && !previewReviewChecks.subtitleReadability)
            || Boolean(lastPreview.quality_report?.checks.some(check => check.state === 'warning') && !previewReviewChecks.warnings)
          }
          onClick={() => reviewPreview.mutate()}
        >{reviewPreview.isPending ? '正在保存复核…' : '保存人工复核'}</button>}
      </footer>
    </section></div>}
  </main>
}
