import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Film,
  Download, Eye, EyeOff, Layers3, Lock, Maximize2, Music2, Pause, Play, Plus, Redo2,
  RefreshCw, Minus,
  RotateCcw, Scissors, Search, ShieldCheck, Sparkles, Subtitles, Undo2, Unlock,
  Upload, Volume2, VolumeX, WandSparkles, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
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

function timecode(ms: number, fps = 24) {
  const value = Math.max(0, Math.round(ms))
  const safeFps = Math.max(1, Math.round(fps))
  const totalFrames = Math.floor((value * safeFps) / 1000)
  const totalSeconds = Math.floor(totalFrames / safeFps)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const frames = totalFrames % safeFps
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}:${String(frames).padStart(2, '0')}`
}

function seconds(ms: number | null | undefined) {
  return `${((ms ?? 0) / 1000).toFixed(1)}s`
}

function rulerLabel(ms: number) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
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
  const deliveryWorkspace = useQuery({
    queryKey: ['editor-prototype-delivery', projectId],
    queryFn: () => api.deliveryWorkspace(projectId),
    refetchInterval: query => {
      const status = query.state.data?.attempts[0]?.status
      return status === 'queued' || status === 'rendering' ? 3000 : false
    },
  })
  const [items, setItems] = useState<TimelineItem[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [playheadMs, setPlayheadMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [monitorScale, setMonitorScale] = useState<'fit' | 'actual'>('fit')
  const [monitorFullscreen, setMonitorFullscreen] = useState(false)
  const [assetFilter, setAssetFilter] = useState<'all' | 'video' | 'audio' | 'subtitle'>('all')
  const [assetSearchOpen, setAssetSearchOpen] = useState(false)
  const [assetSearchQuery, setAssetSearchQuery] = useState('')
  const [gapAssetSelection, setGapAssetSelection] = useState(false)
  const [notice, setNotice] = useState('原型模式：所有调整只保存在当前浏览器，不会修改真实时间线。')
  const [history, setHistory] = useState<TimelineItem[][]>([])
  const [future, setFuture] = useState<TimelineItem[][]>([])
  const [timelineZoom, setTimelineZoom] = useState(82)
  const [snapEnabled, setSnapEnabled] = useState(true)
  const [draggedItemId, setDraggedItemId] = useState<string | null>(null)
  const [draggedAssetId, setDraggedAssetId] = useState<string | null>(null)
  const [videoTrackLocked, setVideoTrackLocked] = useState(false)
  const [videoTrackHidden, setVideoTrackHidden] = useState(false)
  const [audioTrackMuted, setAudioTrackMuted] = useState(false)
  const [subtitleTrackHidden, setSubtitleTrackHidden] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [versionOpen, setVersionOpen] = useState(false)
  const [confirmSaveOpen, setConfirmSaveOpen] = useState(false)
  const [validationOpen, setValidationOpen] = useState(false)
  const [lastValidation, setLastValidation] = useState<Timeline | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [lastPreview, setLastPreview] = useState<TimelinePreview | null>(null)
  const [previewCompareMode, setPreviewCompareMode] = useState<'result' | 'compare'>('result')
  const [previewCompareMs, setPreviewCompareMs] = useState(0)
  const [previewReviewSaved, setPreviewReviewSaved] = useState(false)
  const [previewReviewChecks, setPreviewReviewChecks] = useState({
    visualContinuity: false,
    subjectiveSync: false,
    subtitleReadability: false,
    warnings: false,
  })
  const [deliveryAuthorizeOpen, setDeliveryAuthorizeOpen] = useState(false)
  const [deliveryStatusOpen, setDeliveryStatusOpen] = useState(false)
  const [deliveryMethod, setDeliveryMethod] = useState<'external_upload' | 'local_ffmpeg' | null>(null)
  const [deliveryFile, setDeliveryFile] = useState<File | null>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const monitorRef = useRef<HTMLDivElement | null>(null)
  const timelineViewportRef = useRef<HTMLDivElement | null>(null)
  const renderedPreviewRef = useRef<HTMLVideoElement | null>(null)
  const sourceCompareRef = useRef<HTMLVideoElement | null>(null)
  const advancingPlaybackRef = useRef(false)
  const timelineScrubbingRef = useRef(false)
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
    setSnapEnabled(true)
    setDirty(Boolean(restored))
    setHistory([])
    setFuture([])
    setSelectedIndex(0)
    setPlayheadMs(0)
    setPlaying(false)
    setLastValidation(sourceTimeline)
    setLastPreview(null)
    setPreviewCompareMode('result')
    setPreviewCompareMs(0)
    setPreviewReviewSaved(Boolean(sourceTimeline.preview_review))
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
  const outputFps = Math.max(1, Number(sourceTimeline?.output_spec.fps) || 24)
  const snapIntervalMs = sourceTimeline?.track_config.snap_interval_ms ?? 100
  const snapMs = (value: number) => snapEnabled
    ? Math.round(value / snapIntervalMs) * snapIntervalMs
    : Math.round(value)
  const selectedItem = items[selectedIndex] ?? null
  const selectedAsset = workspace.data?.available_assets.find(asset => asset.id === selectedItem?.asset_id) ?? null
  const selectedClipDurationMs = selectedItem
    ? selectedItem.timeline_out_ms - selectedItem.timeline_in_ms
    : 0
  const selectedTransitionLimitMs = Math.min(2000, Math.floor(selectedClipDurationMs / 2))
  const selectedPreviewOpacity = (() => {
    if (!selectedItem || selectedItem.track_type !== 'main_video') return 1
    const offsetMs = Math.max(0, Math.min(selectedClipDurationMs, playheadMs - selectedItem.timeline_in_ms))
    const transitionIn = selectedItem.transform.transition_in as { type?: string; duration_ms?: number } | undefined
    const transitionOut = selectedItem.transform.transition_out as { type?: string; duration_ms?: number } | undefined
    const fadeInOpacity = transitionIn?.type === 'fade' && transitionIn.duration_ms
      ? Math.min(1, offsetMs / transitionIn.duration_ms)
      : 1
    const fadeOutOpacity = transitionOut?.type === 'fade' && transitionOut.duration_ms
      ? Math.min(1, Math.max(0, selectedClipDurationMs - offsetMs) / transitionOut.duration_ms)
      : 1
    return Math.min(fadeInOpacity, fadeOutOpacity)
  })()
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
  const mainItems = useMemo(() => items.filter(item => item.track_type === 'main_video'), [items])
  const audioItems = useMemo(() => items.filter(item => item.track_type === 'audio'), [items])
  const subtitleItems = useMemo(() => items.filter(item => item.track_type === 'subtitle'), [items])
  const usedMainVideoAssetIds = useMemo(
    () => new Set(mainItems.flatMap(item => item.asset_id ? [item.asset_id] : [])),
    [mainItems],
  )
  const normalizedAssetSearch = assetSearchQuery.trim().toLocaleLowerCase('zh-CN')
  const visibleAssets = workspace.data?.available_assets.filter(asset => (
    (assetFilter === 'all' || asset.asset_type === assetFilter)
    && (!gapAssetSelection || (asset.asset_type === 'video' && !usedMainVideoAssetIds.has(asset.id)))
    && (!normalizedAssetSearch || [asset.node_key, asset.role, asset.asset_type]
      .filter(Boolean)
      .some(value => String(value).toLocaleLowerCase('zh-CN').includes(normalizedAssetSearch)))
  )) ?? []
  const activeSubtitleItem = subtitleItems.find(item => item.asset_id && playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms) ?? null
  const unresolvedCount = mainItems.filter(item => !item.asset_id).length
  const deliveryAttempt = deliveryWorkspace.data?.attempts[0] ?? null
  const comparisonItem = mainItems.find(item => (
    item.asset_id
    && previewCompareMs >= item.timeline_in_ms
    && previewCompareMs < item.timeline_out_ms
  )) ?? null
  const timelineWidth = Math.max(900, (durationMs / 1000) * timelineZoom)
  const rulerTicks = useMemo(() => {
    const stepSeconds = [1, 2, 3, 5, 10, 15, 30, 60].find(step => step * timelineZoom >= 90) ?? 60
    const stepMs = stepSeconds * 1000
    const ticks = Array.from({ length: Math.floor(durationMs / stepMs) + 1 }, (_, index) => index * stepMs)
    if (ticks[ticks.length - 1] !== durationMs) ticks.push(durationMs)
    return ticks
  }, [durationMs, timelineZoom])
  const validationErrors = lastValidation?.validation_report
    ?? sourceTimeline?.validation_report
    ?? []

  useEffect(() => {
    const currentIndex = items.findIndex(item => item.track_type === 'main_video' && playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms)
    if (playing && currentIndex >= 0 && currentIndex !== selectedIndex) setSelectedIndex(currentIndex)
  }, [playheadMs, items, playing, selectedIndex])

  useEffect(() => {
    advancingPlaybackRef.current = false
  }, [selectedItem?.id])

  useEffect(() => {
    const syncFullscreen = () => setMonitorFullscreen(document.fullscreenElement === monitorRef.current)
    document.addEventListener('fullscreenchange', syncFullscreen)
    return () => document.removeEventListener('fullscreenchange', syncFullscreen)
  }, [])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !selectedItem?.asset_id) return
    const expectedTime = (selectedItem.source_in_ms ?? 0) / 1000
    if (Math.abs(video.currentTime - expectedTime) > .05) video.currentTime = expectedTime
    if (playing) void video.play()
    else video.pause()
  }, [playing, selectedItem?.id, selectedItem?.asset_id, selectedItem?.source_in_ms])

  useEffect(() => {
    const video = videoRef.current
    if (playing || !video || selectedItem?.track_type !== 'main_video' || !selectedItem.asset_id) return
    const sourceIn = selectedItem.source_in_ms ?? 0
    const sourceOut = selectedItem.source_out_ms ?? selectedItem.asset_duration_ms ?? sourceIn
    const timelineOffset = Math.max(0, Math.min(
      selectedItem.timeline_out_ms - selectedItem.timeline_in_ms,
      playheadMs - selectedItem.timeline_in_ms,
    ))
    const expectedTime = Math.min(sourceOut, sourceIn + timelineOffset) / 1000
    if (Math.abs(video.currentTime - expectedTime) > .05) video.currentTime = expectedTime
  }, [
    playheadMs,
    playing,
    selectedItem?.id,
    selectedItem?.track_type,
    selectedItem?.asset_id,
    selectedItem?.source_in_ms,
    selectedItem?.source_out_ms,
    selectedItem?.asset_duration_ms,
    selectedItem?.timeline_in_ms,
    selectedItem?.timeline_out_ms,
  ])

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
    const source = sourceCompareRef.current
    if (!source || !comparisonItem?.asset_id || previewCompareMode !== 'compare') return
    const sourceTime = (
      (comparisonItem.source_in_ms ?? 0)
      + Math.max(0, previewCompareMs - comparisonItem.timeline_in_ms)
    ) / 1000
    if (Math.abs(source.currentTime - sourceTime) > .08) source.currentTime = sourceTime
    if (renderedPreviewRef.current && !renderedPreviewRef.current.paused) {
      void source.play().catch(() => undefined)
    } else {
      source.pause()
    }
  }, [comparisonItem?.id, comparisonItem?.asset_id, previewCompareMode, previewCompareMs])

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
      setPreviewCompareMode('result')
      setPreviewCompareMs(0)
      setPreviewReviewSaved(Boolean(
        sourceTimeline?.preview_review
        && sourceTimeline.preview_review.preview_key === preview.preview_key
        && sourceTimeline.preview_review.preview_content_hash === preview.content_hash
      ))
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
      setPreviewReviewSaved(true)
      void queryClient.invalidateQueries({ queryKey: ['editor-prototype-delivery', projectId] })
    },
  })

  const confirmTimeline = useMutation({
    mutationFn: async () => {
      if (!sourceTimeline || sourceTimeline.status !== 'review') {
        throw new Error('当前时间线不是待确认状态。')
      }
      if (!previewReviewSaved) throw new Error('请先保存本次低清预览人工复核。')
      return api.confirmTimeline(projectId, sourceTimeline)
    },
    onSuccess: async timeline => {
      setNotice(`时间线 v${timeline.version_number} 已确认；预览复核保持绑定，可以选择正式交付方式。`)
      setPreviewOpen(false)
      setDeliveryMethod(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-workspace', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['editor-workspace', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-delivery', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['delivery-workspace', projectId] }),
      ])
      await deliveryWorkspace.refetch()
      setDeliveryAuthorizeOpen(true)
    },
  })

  const authorizeDelivery = useMutation({
    mutationFn: async () => {
      if (!deliveryMethod) throw new Error('请选择正式交付方式。')
      const current = await api.deliveryWorkspace(projectId)
      if (!current.confirmed_timeline || !current.preview_review) {
        throw new Error('正式交付仍缺少已确认时间线或精确预览复核。')
      }
      return api.authorizeDelivery(projectId, current, deliveryMethod)
    },
    onSuccess: async attempt => {
      setDeliveryAuthorizeOpen(false)
      setDeliveryStatusOpen(true)
      setDeliveryMethod(null)
      setNotice(attempt.execution_kind === 'local_ffmpeg'
        ? '正式交付已授权，本机 FFmpeg 任务已进入队列。'
        : '正式交付已授权，请继续上传已经生成的 MP4。')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-delivery', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['delivery-workspace', projectId] }),
      ])
    },
  })

  const uploadDelivery = useMutation({
    mutationFn: async () => {
      if (!deliveryAttempt || deliveryAttempt.status !== 'authorized' || !deliveryFile) {
        throw new Error('当前没有等待上传的交付尝试，或尚未选择 MP4。')
      }
      return api.uploadDelivery(projectId, deliveryAttempt, deliveryFile)
    },
    onSuccess: async () => {
      setDeliveryFile(null)
      setNotice('最终 MP4 已上传并登记；请继续执行交付文件验证。')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-delivery', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['delivery-workspace', projectId] }),
      ])
    },
  })

  const verifyDelivery = useMutation({
    mutationFn: async () => {
      if (!deliveryAttempt || deliveryAttempt.status !== 'output_registered' || !deliveryAttempt.final_asset) {
        throw new Error('当前没有可以验证的已登记交付文件。')
      }
      return api.verifyDelivery(projectId, deliveryAttempt)
    },
    onSuccess: async attempt => {
      setNotice(`交付文件已验证（${attempt.final_asset?.width}×${attempt.final_asset?.height}），可以下载最终 MP4。`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-delivery', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['delivery-workspace', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-workspace', projectId] }),
      ])
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
    setPlaying(false)
    setNotice('已丢弃本地调整，恢复到当前时间线版本。')
  }

  const selectItem = (item: TimelineItem) => {
    const index = items.indexOf(item)
    setSelectedIndex(index)
    setPlayheadMs(item.timeline_in_ms)
    setPlaying(false)
  }

  const seekTimeline = (positionMs: number) => {
    const position = Math.max(0, Math.min(durationMs, Math.round(positionMs)))
    const target = mainItems.find(item => (
      position >= item.timeline_in_ms
      && (position < item.timeline_out_ms || (position === durationMs && item.timeline_out_ms === durationMs))
    ))
    if (target) {
      const index = items.findIndex(item => item.id === target.id)
      if (index >= 0) setSelectedIndex(index)
    }
    setPlayheadMs(position)
    setPlaying(false)
  }

  const beginScrub = (
    event: ReactPointerEvent<HTMLElement>,
    contentOffsetPx = 0,
    timelineScrub = false,
  ) => {
    event.currentTarget.focus()
    event.preventDefault()
    event.stopPropagation()
    const rect = event.currentTarget.getBoundingClientRect()
    const contentWidth = Math.max(1, rect.width - contentOffsetPx)
    timelineScrubbingRef.current = timelineScrub
    const update = (clientX: number) => {
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left - contentOffsetPx) / contentWidth))
      seekTimeline(ratio * durationMs)
    }
    const onMove = (moveEvent: PointerEvent) => update(moveEvent.clientX)
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      timelineScrubbingRef.current = false
    }
    update(event.clientX)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  const handleSeekKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    const frameMs = 1000 / outputFps
    const stepMs = event.shiftKey ? 1000 : frameMs
    let target: number | null = null
    if (event.key === 'ArrowLeft') target = playheadMs - stepMs
    if (event.key === 'ArrowRight') target = playheadMs + stepMs
    if (event.key === 'PageUp') target = playheadMs - 5000
    if (event.key === 'PageDown') target = playheadMs + 5000
    if (event.key === 'Home') target = 0
    if (event.key === 'End') target = durationMs
    if (target === null) return
    event.preventDefault()
    event.stopPropagation()
    seekTimeline(target)
  }

  const changeTimelineZoom = (value: number) => {
    setTimelineZoom(Math.max(40, Math.min(180, Math.round(value))))
    setDirty(true)
  }

  const togglePlayback = () => {
    if (playing) {
      setPlaying(false)
      return
    }
    let target = mainItems.find(item => playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms) ?? null
    if (!target && playheadMs >= durationMs) {
      target = mainItems[0] ?? null
      if (target) selectItem(target)
    }
    if (!target?.asset_id) {
      if (target) selectItem(target)
      setNotice(target ? '当前播放头位于显式空位，请先选择补齐方式。' : '当前时间线没有可以播放的画面。')
      return
    }
    if (selectedItem?.id !== target.id) selectItem(target)
    advancingPlaybackRef.current = false
    setPlaying(true)
  }

  const toggleMonitorFullscreen = async () => {
    const monitor = monitorRef.current
    if (!monitor) return
    try {
      if (document.fullscreenElement === monitor) await document.exitFullscreen()
      else await monitor.requestFullscreen()
      const active = document.fullscreenElement === monitor
      setMonitorFullscreen(active)
      if (!active) setNotice('当前浏览器没有进入监看全屏；仍可使用适应窗口或 100% 像素模式。')
    } catch {
      setMonitorFullscreen(false)
      setNotice('浏览器没有允许监看窗口进入全屏，请检查当前窗口权限。')
    }
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

  const blockMainTrackEdit = (item: TimelineItem | null = selectedItem) => {
    if (!videoTrackLocked || item?.track_type !== 'main_video') return false
    setNotice('画面轨已锁定，当前操作没有修改时间线；请先解锁画面轨。')
    return true
  }

  const toggleVideoTrackLock = () => {
    const nextLocked = !videoTrackLocked
    setVideoTrackLocked(nextLocked)
    setNotice(nextLocked
      ? '画面轨已锁定：仍可选择和预览，但不能裁切、分割、移动、删除、替换或修改转场。'
      : '画面轨已解锁，可以继续调整画面片段。')
  }

  const toggleVideoTrackVisibility = () => {
    const nextHidden = !videoTrackHidden
    setVideoTrackHidden(nextHidden)
    setNotice(nextHidden
      ? '画面轨已在监看中隐藏；视频仍推进时间码，只影响当前剪辑预览。'
      : '画面轨已恢复监看显示。')
  }

  const toggleAudioTrackMute = () => {
    const nextMuted = !audioTrackMuted
    setAudioTrackMuted(nextMuted)
    setNotice(nextMuted
      ? '声音轨已在监看中静音；混音合同和最终成片不会被改写。'
      : '声音轨已恢复监听。')
  }

  const toggleSubtitleTrackVisibility = () => {
    const nextHidden = !subtitleTrackHidden
    setSubtitleTrackHidden(nextHidden)
    setNotice(nextHidden
      ? '字幕轨已在监看中隐藏；烧录字幕合同不会被改写。'
      : '字幕轨已恢复监看显示。')
  }

  const shiftItem = (direction: -1 | 1) => {
    if (!selectedItem || selectedItem.track_type !== 'main_video' || blockMainTrackEdit()) return
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
    if (!draggedItemId || draggedItemId === targetId) return
    const targetItem = mainItems.find(item => item.id === targetId) ?? null
    if (blockMainTrackEdit(targetItem)) {
      setDraggedItemId(null)
      return
    }
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

  const dropAssetOnItem = (target: TimelineItem, explicitAssetId?: string) => {
    const sourceAssetId = explicitAssetId ?? draggedAssetId
    if (!sourceAssetId) return
    if (blockMainTrackEdit(target)) {
      setDraggedAssetId(null)
      return
    }
    const asset = workspace.data?.available_assets.find(row => row.id === sourceAssetId)
    if (!asset || asset.asset_type !== 'video' || !asset.duration_ms) return
    if (mainItems.some(item => item.id !== target.id && item.asset_id === asset.id)) {
      setNotice(`${asset.node_key ?? asset.role} 已用于当前主画面，不能重复引用来填补缺口。`)
      setDraggedAssetId(null)
      return
    }
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
    setGapAssetSelection(false)
  }

  const startGapAssetSelection = () => {
    if (blockMainTrackEdit()) return
    setAssetFilter('video')
    setGapAssetSelection(true)
    const available = workspace.data?.available_assets.filter(asset => (
      asset.asset_type === 'video' && !usedMainVideoAssetIds.has(asset.id)
    )) ?? []
    setNotice(available.length
      ? `素材箱已只显示 ${available.length} 个未用于主画面的已批准视频；点击即可填入当前缺口。`
      : '当前没有未使用的已批准视频。请返回生产流程生成补充镜头，或先分析缩短目标时长的影响。')
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
    if (!selectedItem || blockMainTrackEdit()) return
    commitItems(items.map(item => item.id === selectedItem.id
      ? { ...item, transform: { ...item.transform, [key]: value } }
      : item), `已更新 ${selectedItem.label} 的片段设置。`, selectedItem.id)
  }

  const setSelectedTransition = (
    key: 'transition_in' | 'transition_out',
    type: 'cut' | 'fade',
    durationMs?: number,
  ) => {
    if (!selectedItem || selectedItem.track_type !== 'main_video') return
    const maximum = Math.min(2000, Math.floor((selectedItem.timeline_out_ms - selectedItem.timeline_in_ms) / 2))
    const requestedDuration = durationMs != null && durationMs >= 100
      ? durationMs
      : Math.min(300, maximum)
    const nextDuration = type === 'cut'
      ? 0
      : Math.max(100, Math.min(maximum, requestedDuration))
    updateSelectedTransform(key, { type, duration_ms: nextDuration })
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
    if (!selectedItem?.asset_id || selectedItem.track_type !== 'main_video' || blockMainTrackEdit()) return
    const splitAt = snapMs(playheadMs)
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
    commitItems(nextItems, `已在 ${timecode(splitAt, outputFps)} 分割片段。`, right.id)
  }

  const deleteSelected = () => {
    if (!selectedItem) return
    if (selectedItem.track_type === 'main_video') {
      if (blockMainTrackEdit()) return
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
    if (!item.asset_id || blockMainTrackEdit(item)) return
    const startX = event.clientX
    const original = { sourceIn: item.source_in_ms ?? 0, sourceOut: item.source_out_ms ?? item.asset_duration_ms ?? 0 }
    setHistory(rows => [...rows.slice(-49), items])
    setFuture([])
    setDirty(true)
    const onMove = (moveEvent: PointerEvent) => {
      const rawDeltaMs = ((moveEvent.clientX - startX) / timelineZoom) * 1000
      const deltaMs = snapEnabled
        ? Math.round(rawDeltaMs / snapIntervalMs) * snapIntervalMs
        : Math.round(rawDeltaMs)
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
        togglePlayback()
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

  useEffect(() => {
    const viewport = timelineViewportRef.current
    if (!viewport || timelineScrubbingRef.current) return
    const playheadX = 84 + timelineWidth * (playheadMs / durationMs)
    const visibleStart = viewport.scrollLeft + 24
    const visibleEnd = viewport.scrollLeft + viewport.clientWidth - 24
    if (playheadX >= visibleStart && playheadX <= visibleEnd) return
    viewport.scrollLeft = Math.max(0, playheadX - viewport.clientWidth / 2)
  }, [durationMs, playheadMs, timelineWidth])

  const advancePlayback = () => {
    if (advancingPlaybackRef.current) return
    advancingPlaybackRef.current = true
    videoRef.current?.pause()
    const position = mainItems.findIndex(item => item.id === selectedItem?.id)
    const next = position >= 0 ? mainItems[position + 1] : null
    if (!next) {
      setPlayheadMs(durationMs)
      setPlaying(false)
      advancingPlaybackRef.current = false
      setNotice('时间线预览播放完成。')
      return
    }
    selectItem(next)
    if (!next.asset_id) {
      setPlaying(false)
      setNotice('播放到缺口：需要先选择一种补齐方式。')
      return
    }
    setPlaying(true)
  }

  const handleTimeUpdate = () => {
    const video = videoRef.current
    if (!video || !selectedItem) return
    const sourceIn = selectedItem.source_in_ms ?? 0
    const sourceOut = selectedItem.source_out_ms ?? selectedItem.asset_duration_ms ?? 0
    if (playing && sourceOut > sourceIn && video.currentTime * 1000 >= sourceOut - (500 / outputFps)) {
      setPlayheadMs(selectedItem.timeline_out_ms)
      advancePlayback()
      return
    }
    const next = selectedItem.timeline_in_ms + Math.max(0, video.currentTime * 1000 - sourceIn)
    setPlayheadMs(Math.min(next, selectedItem.timeline_out_ms))
  }

  const handleEnded = (event: React.SyntheticEvent<HTMLVideoElement>) => {
    if (videoRef.current !== event.currentTarget || !selectedItem?.asset_id) return
    const sourceOut = selectedItem.source_out_ms ?? selectedItem.asset_duration_ms ?? 0
    if (event.currentTarget.currentTime * 1000 + (1000 / outputFps) < sourceOut) return
    advancePlayback()
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
      <button className={styles.versionButton} onClick={() => setVersionOpen(true)}>时间线 v{sourceTimeline?.version_number ?? '--'} <small>{dirty ? '本地草稿未提交' : '已同步'} · 查看版本证据</small></button>
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

    <section className={styles.statusbar} data-warning={unresolvedCount > 0 || validationErrors.length > 0 || Boolean(saveAndValidate.error) || Boolean(renderPreview.error) || Boolean(reviewPreview.error) || Boolean(confirmTimeline.error) || Boolean(authorizeDelivery.error) || Boolean(uploadDelivery.error) || Boolean(verifyDelivery.error)}>
      {unresolvedCount || validationErrors.length || saveAndValidate.error || renderPreview.error || reviewPreview.error || confirmTimeline.error || authorizeDelivery.error || uploadDelivery.error || verifyDelivery.error ? <AlertTriangle /> : <CheckCircle2 />}
      <span>{notice}</span>
      {saveAndValidate.error && <button onClick={() => setConfirmSaveOpen(true)}>{saveAndValidate.error instanceof Error ? saveAndValidate.error.message : '保存失败，请重试'}</button>}
      {renderPreview.error && <button onClick={() => renderPreview.mutate()}>{renderPreview.error instanceof Error ? renderPreview.error.message : '低清预览失败，请重试'}</button>}
      {reviewPreview.error && <button onClick={() => reviewPreview.mutate()}>{reviewPreview.error instanceof Error ? reviewPreview.error.message : '人工复核保存失败，请重试'}</button>}
      {confirmTimeline.error && <button onClick={() => confirmTimeline.mutate()}>{confirmTimeline.error instanceof Error ? confirmTimeline.error.message : '时间线确认失败，请重试'}</button>}
      {authorizeDelivery.error && <button onClick={() => setDeliveryAuthorizeOpen(true)}>{authorizeDelivery.error instanceof Error ? authorizeDelivery.error.message : '交付授权失败，请重试'}</button>}
      {uploadDelivery.error && <button onClick={() => setDeliveryStatusOpen(true)}>{uploadDelivery.error instanceof Error ? uploadDelivery.error.message : '交付文件上传失败'}</button>}
      {verifyDelivery.error && <button onClick={() => setDeliveryStatusOpen(true)}>{verifyDelivery.error instanceof Error ? verifyDelivery.error.message : '交付文件验证失败'}</button>}
      {validationErrors.length > 0 && <button onClick={() => setValidationOpen(true)}>查看 {validationErrors.length} 个检查问题</button>}
      <code>{workspace.data.aspect_ratio} · {outputFps}fps · {seconds(durationMs)} · 预览质量</code>
    </section>

    <section className={styles.editingArea}>
      <aside className={styles.assetPanel} data-gap-selection={gapAssetSelection} data-search={assetSearchOpen}>
        <header><div><span>ASSETS</span><strong>素材箱</strong></div><button title={assetSearchOpen ? '关闭素材搜索' : '搜索素材'} onClick={() => {
          setAssetSearchOpen(value => {
            if (value) setAssetSearchQuery('')
            return !value
          })
        }}><Search /></button></header>
        {assetSearchOpen && <div className={styles.assetSearch}><Search /><input autoFocus aria-label="搜索素材" placeholder="名称、角色或类型" value={assetSearchQuery} onChange={event => setAssetSearchQuery(event.target.value)} onKeyDown={event => {
          if (event.key === 'Escape') {
            setAssetSearchQuery('')
            setAssetSearchOpen(false)
          }
        }} />{assetSearchQuery && <button title="清空搜索" onClick={() => setAssetSearchQuery('')}><X /></button>}</div>}
        {gapAssetSelection && <div className={styles.assetSelectionBanner}><strong>缺口素材选择</strong><span>仅显示未用于主画面的已批准视频</span><button onClick={() => setGapAssetSelection(false)}>退出</button></div>}
        <nav>
          {([
            ['all', '全部', Layers3],
            ['video', '视频', Film],
            ['audio', '声音', Music2],
            ['subtitle', '字幕', Subtitles],
          ] as const).map(([key, label, Icon]) => <button key={key} data-active={assetFilter === key} onClick={() => setAssetFilter(key)}><Icon />{label}</button>)}
        </nav>
        <div className={styles.assetList}>
          {gapAssetSelection && visibleAssets.length === 0 && <div className={styles.assetEmpty}><AlertTriangle /><strong>{normalizedAssetSearch ? '没有匹配的未使用视频' : '没有可用的新视频'}</strong><span>{normalizedAssetSearch ? '可清空搜索查看全部未使用候选。' : '重复使用现有镜头会破坏连续性证据，系统不会放行。'}</span></div>}
          {!gapAssetSelection && normalizedAssetSearch && visibleAssets.length === 0 && <div className={styles.assetEmpty}><Search /><strong>没有匹配素材</strong><span>可更换名称、角色或类型关键词。</span></div>}
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
              if (gapAssetSelection && asset.asset_type === 'video' && selectedItem && !selectedItem.asset_id) {
                dropAssetOnItem(selectedItem, asset.id)
                return
              }
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
        <div className={styles.monitor} ref={monitorRef} data-scale={monitorScale} data-video-hidden={videoTrackHidden}>
          {selectedAsset?.asset_type === 'video' && <video
            ref={videoRef}
            key={selectedItem?.id}
            src={`/api/v1/projects/${projectId}/assets/${selectedAsset.id}/content`}
            style={{
              opacity: selectedPreviewOpacity,
              width: monitorScale === 'actual' && selectedAsset.width ? `${selectedAsset.width}px` : undefined,
              height: monitorScale === 'actual' && selectedAsset.height ? `${selectedAsset.height}px` : undefined,
            }}
            preload="metadata"
            muted
            playsInline
            onLoadedMetadata={event => {
              const sourceIn = selectedItem?.source_in_ms ?? 0
              const sourceOut = selectedItem?.source_out_ms ?? selectedItem?.asset_duration_ms ?? sourceIn
              const timelineOffset = selectedItem
                ? Math.max(0, Math.min(
                  selectedItem.timeline_out_ms - selectedItem.timeline_in_ms,
                  playheadMs - selectedItem.timeline_in_ms,
                ))
                : 0
              event.currentTarget.currentTime = Math.min(sourceOut, sourceIn + timelineOffset) / 1000
              if (playing) void event.currentTarget.play().catch(() => setNotice('浏览器阻止了时间线视频播放，请再次点击播放。'))
            }}
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
          {videoTrackHidden && selectedAsset?.asset_type === 'video' && <div className={styles.monitorTrackHidden}><EyeOff /><strong>画面轨已隐藏</strong><span>视频仍在后台推进，仅影响剪辑预览</span></div>}
          <div className={styles.monitorBadge}>时间线预览</div>
          {(videoTrackHidden || audioTrackMuted || subtitleTrackHidden) && <div className={styles.monitorTrackFlags}>
            {videoTrackHidden && <span><EyeOff />画面隐藏</span>}
            {audioTrackMuted && <span><VolumeX />声音静音</span>}
            {subtitleTrackHidden && <span><EyeOff />字幕隐藏</span>}
          </div>}
          <button className={styles.fullscreenButton} title={monitorFullscreen ? '退出全屏' : '全屏'} onClick={() => void toggleMonitorFullscreen()}><Maximize2 /></button>
        </div>
        <div className={styles.transport}>
          <button title="跳到开头" onClick={() => seekTimeline(0)}><ChevronLeft /></button>
          <button title={playing ? '暂停' : '播放'} className={styles.playButton} onClick={togglePlayback}>{playing ? <Pause /> : <Play />}</button>
          <button title="跳到结尾" onClick={() => seekTimeline(durationMs)}><ChevronRight /></button>
          <code>{timecode(playheadMs, outputFps)} <span>/ {timecode(durationMs, outputFps)}</span></code>
          <div
            className={styles.previewScrubber}
            role="slider"
            aria-label="预览播放头"
            aria-valuemin={0}
            aria-valuemax={durationMs}
            aria-valuenow={playheadMs}
            aria-valuetext={timecode(playheadMs, outputFps)}
            tabIndex={0}
            onPointerDown={event => beginScrub(event)}
            onKeyDown={handleSeekKeyDown}
          ><i style={{ width: `${Math.min(100, (playheadMs / durationMs) * 100)}%` }} /></div>
          <Volume2 />
          <button disabled={selectedAsset?.asset_type !== 'video'} onClick={() => setMonitorScale(value => value === 'fit' ? 'actual' : 'fit')}>{monitorScale === 'fit' ? '适应' : '100%'}</button>
        </div>
      </section>

      <aside className={styles.inspector}>
        <header><span>INSPECTOR</span><strong>{selectedItem?.asset_id ? '片段属性' : '缺口处理'}</strong></header>
        {selectedItem?.asset_id ? <>
          <section className={styles.clipIdentity}><i>{selectedItem.track_type === 'main_video' ? <Film /> : selectedItem.track_type === 'audio' ? <Music2 /> : <Subtitles />}</i><div><strong>{selectedItem.label}</strong><span>{selectedItem.track_type === 'main_video' ? `对应分镜 ${selectedItem.label.split('.')[0]}` : selectedItem.track_type === 'audio' ? '真实音频素材' : '烧录字幕素材'}</span></div></section>
          <section>
            <h3>素材范围</h3>
            <div className={styles.rangeLabels}><span>{seconds(selectedItem.source_in_ms)}</span><b>{seconds((selectedItem.source_out_ms ?? 0) - (selectedItem.source_in_ms ?? 0))}</b><span>{seconds(selectedItem.source_out_ms)}</span></div>
            <div className={styles.trimHint}>{selectedItem.track_type === 'main_video'
              ? videoTrackLocked ? '画面轨已锁定；解锁后才能拖动两侧把手裁切' : '直接拖动时间线片段两侧把手裁切'
              : '时间范围随不可变时间线版本保存'}</div>
          </section>
          {selectedItem.track_type === 'main_video' && <section>
            <h3>片段操作</h3>
            <div className={styles.actionGrid}>
              <button disabled={videoTrackLocked} onClick={splitSelected}><Scissors />播放头分割</button>
              <button disabled={videoTrackLocked} onClick={() => shiftItem(-1)}><ChevronLeft />向前移动</button>
              <button disabled={videoTrackLocked} onClick={() => shiftItem(1)}><ChevronRight />向后移动</button>
              <button disabled={videoTrackLocked} onClick={deleteSelected}>移除片段</button>
            </div>
            {videoTrackLocked && <div className={styles.trimHint}>画面轨锁定期间只允许选择、寻帧和预览，不写入本地草稿。</div>}
          </section>}
          {selectedItem.track_type === 'main_video' && <section>
            <h3>转场</h3>
            {(['transition_in', 'transition_out'] as const).map(key => {
              const transition = selectedItem.transform[key] as { type?: 'cut' | 'fade'; duration_ms?: number } | undefined
              const type = transition?.type ?? 'cut'
              return <div className={styles.transitionControl} key={key}>
                <label>{key === 'transition_in' ? '入场' : '出场'}<select disabled={videoTrackLocked} value={type} onChange={event => setSelectedTransition(key, event.target.value as 'cut' | 'fade', transition?.duration_ms)}><option value="cut">直接切换</option><option value="fade">{key === 'transition_in' ? '淡入' : '淡出'}</option></select></label>
                <label>时长<input aria-label={`${key === 'transition_in' ? '入场' : '出场'}转场时长`} disabled={videoTrackLocked || type !== 'fade'} type="number" min="0.1" max={selectedTransitionLimitMs / 1000} step="0.1" value={type === 'fade' ? (transition?.duration_ms ?? 300) / 1000 : 0} onChange={event => setSelectedTransition(key, 'fade', Math.round(Number(event.target.value) * 1000))} /><small>秒</small></label>
              </div>
            })}
            <div className={styles.trimHint}>淡入淡出会写入时间线合同，并在低清预览与最终 FFmpeg 成片中执行。</div>
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
          <Link to={`/projects/${projectId}/decision-impact`}><Clock3 /><span><strong>分析缩短成片</strong><small>先评估修改 15 秒目标的下游影响</small></span></Link>
          <Link to={`/production?project=${projectId}`}><WandSparkles /><span><strong>前往生成补充镜头</strong><small>在生产流程登记新镜头，授权后才可能产生费用</small></span></Link>
          <button disabled={videoTrackLocked} onClick={startGapAssetSelection}><Plus /><span><strong>{videoTrackLocked ? '先解锁画面轨' : '选择其他素材'}</strong><small>{videoTrackLocked ? '锁定期间不会替换缺口' : '只列出未用于主画面的已批准视频'}</small></span></button>
        </section>}
      </aside>
    </section>

    <section className={styles.timelinePanel}>
      <header className={styles.timelineToolbar}>
        <div><strong>时间线</strong><span>{mainItems.length} 个画面片段 · {audioItems.length} 个音频 · {subtitleItems.length} 个字幕</span></div>
        <button disabled={videoTrackLocked || selectedItem?.track_type !== 'main_video' || !selectedItem.asset_id} onClick={splitSelected}><Scissors />分割</button>
        <button disabled={renderPreview.isPending} onClick={() => {
          if (dirty) setNotice('请先保存并检查本地草稿，再生成低清预览。')
          else renderPreview.mutate()
        }}><Film />{renderPreview.isPending ? '预览生成中…' : '低清预览'}</button>
        {(deliveryWorkspace.data?.confirmed_timeline || deliveryAttempt) && <button onClick={() => setDeliveryStatusOpen(true)}>
          {deliveryAttempt?.status === 'queued' || deliveryAttempt?.status === 'rendering' ? <RefreshCw /> : deliveryAttempt?.status === 'verified' ? <CheckCircle2 /> : <ShieldCheck />}
          {deliveryAttempt?.status === 'verified' ? '成片交付' : deliveryAttempt ? '交付状态' : '授权交付'}
        </button>}
        <button data-active={snapEnabled} onClick={() => setSnapEnabled(value => !value)}>磁吸 {snapEnabled ? `${snapIntervalMs}ms` : '关闭'}</button>
        <button title="缩小时间线" disabled={timelineZoom <= 40} onClick={() => changeTimelineZoom(timelineZoom - 20)}><Minus /></button>
        <label>缩放<input aria-label="时间线缩放" type="range" min="40" max="180" value={timelineZoom} onChange={event => changeTimelineZoom(Number(event.target.value))} /></label>
        <button title="放大时间线" disabled={timelineZoom >= 180} onClick={() => changeTimelineZoom(timelineZoom + 20)}><Plus /></button>
        <code>{timecode(playheadMs, outputFps)}</code>
      </header>
      <div className={styles.timelineViewport} ref={timelineViewportRef}>
        <div className={styles.timelineCanvas} style={{ width: `${84 + timelineWidth}px` }} onClick={event => {
          if ((event.target as HTMLElement).closest('button')) return
          const rect = event.currentTarget.getBoundingClientRect()
          const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - 84) / timelineWidth))
          seekTimeline(ratio * durationMs)
        }}>
        <div className={styles.ruler}><span /><div
          role="slider"
          aria-label="时间尺播放头"
          aria-valuemin={0}
          aria-valuemax={durationMs}
          aria-valuenow={playheadMs}
          aria-valuetext={timecode(playheadMs, outputFps)}
          tabIndex={0}
          onPointerDown={event => beginScrub(event, 0, true)}
          onKeyDown={handleSeekKeyDown}
        >{rulerTicks.map(value => <i
          key={value}
          style={{
            left: `${(value / durationMs) * 100}%`,
            transform: value === 0 ? 'none' : value === durationMs ? 'translateX(-100%)' : undefined,
          }}
        >{rulerLabel(value)}</i>)}</div></div>
        <div className={styles.trackRow} data-track-hidden={videoTrackHidden}>
          <label><Film /><span>画面</span><button title={videoTrackHidden ? '显示画面轨' : '隐藏画面轨'} aria-pressed={videoTrackHidden} onClick={toggleVideoTrackVisibility}>{videoTrackHidden ? <EyeOff /> : <Eye />}</button><button title={videoTrackLocked ? '解锁画面轨' : '锁定画面轨'} aria-pressed={videoTrackLocked} onClick={toggleVideoTrackLock}>{videoTrackLocked ? <Lock /> : <Unlock />}</button></label>
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
            >{item.asset_id && <i role="slider" aria-label={`${item.label} 左侧裁切把手`} aria-disabled={videoTrackLocked} aria-valuemin={0} aria-valuemax={item.asset_duration_ms ?? 0} aria-valuenow={item.source_in_ms ?? 0} tabIndex={videoTrackLocked ? -1 : 0} className={styles.trimHandle} data-edge="start" onPointerDown={event => beginTrim(event, item, 'start')} />}
              {item.asset_id ? <><Film /><span><strong>{item.label}</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></> : <><AlertTriangle /><span><strong>缺少画面</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></>}
              {item.asset_id && <i role="slider" aria-label={`${item.label} 右侧裁切把手`} aria-disabled={videoTrackLocked} aria-valuemin={0} aria-valuemax={item.asset_duration_ms ?? 0} aria-valuenow={item.source_out_ms ?? 0} tabIndex={videoTrackLocked ? -1 : 0} className={styles.trimHandle} data-edge="end" onPointerDown={event => beginTrim(event, item, 'end')} />}
            </button>)}
          </div>
        </div>
        <div className={styles.trackRow} data-track-muted={audioTrackMuted}>
          <label><Music2 /><span>声音</span><button title={audioTrackMuted ? '恢复声音轨' : '静音声音轨'} aria-pressed={audioTrackMuted} onClick={toggleAudioTrackMute}>{audioTrackMuted ? <VolumeX /> : <Volume2 />}</button></label>
          <div className={styles.trackLane} data-empty={!audioItems.length} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); addAssetToTrack('audio') }}>{audioItems.length ? audioItems.map(item => <button key={item.id} data-selected={selectedItem?.id === item.id} className={styles.audioClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }} onClick={() => selectItem(item)}><Waveform projectId={projectId} assetId={item.asset_id!} /><strong>{item.label}</strong></button>) : <span>拖入已批准配音或 BGM</span>}</div>
        </div>
        <div className={styles.trackRow} data-track-hidden={subtitleTrackHidden}>
          <label><Subtitles /><span>字幕</span><button title={subtitleTrackHidden ? '显示字幕轨' : '隐藏字幕轨'} aria-pressed={subtitleTrackHidden} onClick={toggleSubtitleTrackVisibility}>{subtitleTrackHidden ? <EyeOff /> : <Eye />}</button></label>
          <div className={styles.trackLane} data-empty={!subtitleItems.length} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); addAssetToTrack('subtitle') }}>{subtitleItems.length ? subtitleItems.map(item => <button key={item.id} data-selected={selectedItem?.id === item.id} className={styles.subtitleClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }} onClick={() => selectItem(item)}><Subtitles /><strong>{item.label}</strong></button>) : <span>拖入已批准字幕</span>}</div>
        </div>
        <i className={styles.playhead} style={{ left: `${84 + timelineWidth * (playheadMs / durationMs)}px` }}><b /></i>
        </div>
      </div>
      <footer><span><Sparkles />AI 初剪依据和版本证据已收进右侧抽屉</span><span>拖动时间尺寻帧 · ←/→ 逐帧 · Shift 秒级 · Space 播放</span></footer>
    </section>
    {versionOpen && <div className={styles.modal}><section className={styles.versionModal}>
      <header><Layers3 /><div><span>VERSION EVIDENCE</span><h2>时间线版本与审计证据</h2></div><button title="关闭" onClick={() => setVersionOpen(false)}><X /></button></header>
      <p>版本按新到旧排列。当前工作区只编辑最新基线；历史合同保持只读，不会被本地草稿覆盖。</p>
      <div className={styles.versionList}>
        {workspace.data.timelines.map((timeline, index) => {
          const videoCount = timeline.items.filter(item => item.track_type === 'main_video').length
          const audioCount = timeline.items.filter(item => item.track_type === 'audio').length
          const subtitleCount = timeline.items.filter(item => item.track_type === 'subtitle').length
          return <details key={timeline.id} open={index === 0} data-current={timeline.id === sourceTimeline?.id}>
            <summary>
              <i>v{timeline.version_number}</i>
              <span><strong>{({
                candidate: '候选',
                review: '待确认',
                confirmed: '已确认',
                exported: '已导出',
                superseded: '已被修订',
              } as Record<string, string>)[timeline.status]}</strong><small>{videoCount} 画面 · {audioCount} 声音 · {subtitleCount} 字幕 · {timeline.validation_report.length} 个检查问题</small></span>
              {timeline.id === sourceTimeline?.id && <em>当前</em>}
            </summary>
            <dl>
              <div><dt>来源</dt><dd>{timeline.source === 'editor_assistant' ? '剪辑助理候选' : '用户修订'}</dd></div>
              <div><dt>创建时间</dt><dd>{new Date(timeline.created_at).toLocaleString('zh-CN', { hour12: false })}</dd></div>
              <div><dt>输出规格</dt><dd>{String(timeline.output_spec.width ?? '—')}×{String(timeline.output_spec.height ?? '—')} · {String(timeline.output_spec.fps ?? '—')}fps</dd></div>
              <div><dt>复核证据</dt><dd>{timeline.preview_review ? '已绑定低清预览复核' : '尚未绑定'}</dd></div>
              <div><dt>行版本</dt><dd>{timeline.row_version}</dd></div>
              <div><dt>创建者</dt><dd>{timeline.created_by}</dd></div>
            </dl>
            <div className={styles.versionHash}><span>合同哈希</span><code>{timeline.contract_hash ?? '尚未形成合同哈希'}</code></div>
            {timeline.validation_report.length > 0 && <div className={styles.versionIssues}>{timeline.validation_report.map((issue, issueIndex) => <p key={`${issue.code}-${issueIndex}`}><AlertTriangle /><span><strong>{issue.message}</strong><code>{issue.code} · {issue.path}</code></span></p>)}</div>}
          </details>
        })}
      </div>
      <footer><button onClick={() => setVersionOpen(false)}>关闭</button></footer>
    </section></div>}
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
        <div className={styles.previewCompareToolbar}>
          <span>预览方式</span>
          <button data-active={previewCompareMode === 'result'} onClick={() => setPreviewCompareMode('result')}>只看结果</button>
          <button data-active={previewCompareMode === 'compare'} onClick={() => setPreviewCompareMode('compare')}>源时间线对比</button>
          {previewCompareMode === 'compare' && <code>{timecode(previewCompareMs, lastPreview.fps)}</code>}
        </div>
        <div className={styles.renderedPreview} data-compare={previewCompareMode === 'compare'}>
          {previewCompareMode === 'compare' && <figure>
            <figcaption>源时间线监看 · {comparisonItem?.label ?? '空位'}</figcaption>
            {comparisonItem?.asset_id
              ? <video
                  ref={sourceCompareRef}
                  muted
                  playsInline
                  src={`/api/v1/projects/${projectId}/assets/${comparisonItem.asset_id}/content`}
                />
              : <div className={styles.previewCompareGap}><AlertTriangle /><span>该时点是显式空位</span></div>}
          </figure>}
          <figure>
            <figcaption>低清渲染结果</figcaption>
            <video
              ref={renderedPreviewRef}
              controls
              playsInline
              src={lastPreview.content_url}
              onLoadedMetadata={event => {
                event.currentTarget.currentTime = previewCompareMs / 1000
              }}
              onTimeUpdate={event => setPreviewCompareMs(Math.round(event.currentTarget.currentTime * 1000))}
              onSeeked={event => setPreviewCompareMs(Math.round(event.currentTarget.currentTime * 1000))}
              onPlay={() => {
                const source = sourceCompareRef.current
                if (source && previewCompareMode === 'compare') void source.play().catch(() => undefined)
              }}
              onPause={() => sourceCompareRef.current?.pause()}
            />
          </figure>
        </div>
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
        {lastPreview.quality_report && lastPreview.quality_report.status !== 'blocked' && !previewReviewSaved && <fieldset className={styles.previewReviewChecklist}>
          <legend>观看后逐项确认</legend>
          <label><input type="checkbox" checked={previewReviewChecks.visualContinuity} onChange={event => setPreviewReviewChecks(value => ({ ...value, visualContinuity: event.target.checked }))} /><span><strong>画面连续性</strong><small>已完整观看镜头衔接、主体一致性、动作连续性和异常闪跳。</small></span></label>
          <label><input type="checkbox" checked={previewReviewChecks.subjectiveSync} onChange={event => setPreviewReviewChecks(value => ({ ...value, subjectiveSync: event.target.checked }))} /><span><strong>主观音画同步</strong><small>已检查旁白、音乐、字幕与画面节奏是否符合预期。</small></span></label>
          {sourceTimeline?.track_config.subtitle_enabled && <label><input type="checkbox" checked={previewReviewChecks.subtitleReadability} onChange={event => setPreviewReviewChecks(value => ({ ...value, subtitleReadability: event.target.checked }))} /><span><strong>字幕可读性</strong><small>已检查文字、换行、遮挡和画面安全区。</small></span></label>}
          {lastPreview.quality_report.checks.some(check => check.state === 'warning') && <label><input type="checkbox" checked={previewReviewChecks.warnings} onChange={event => setPreviewReviewChecks(value => ({ ...value, warnings: event.target.checked }))} /><span><strong>警告项已逐项确认</strong><small>已确认检测到的黑画面或其他警告均为有意效果或可接受结果。</small></span></label>}
        </fieldset>}
        {previewReviewSaved && <div className={styles.previewReviewSaved}><CheckCircle2 /><span><strong>人工复核已保存</strong><small>记录已绑定当前时间线合同和本次预览文件哈希。</small></span></div>}
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
        {lastPreview.state === 'ready' && lastPreview.quality_report?.status !== 'blocked' && !previewReviewSaved && <button
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
        {previewReviewSaved && sourceTimeline?.status === 'review' && <button className={styles.confirmButton} disabled={confirmTimeline.isPending} onClick={() => confirmTimeline.mutate()}>{confirmTimeline.isPending ? '正在确认…' : '确认时间线并继续'}</button>}
        {previewReviewSaved && sourceTimeline?.status === 'confirmed' && <button className={styles.confirmButton} onClick={() => {
          setPreviewOpen(false)
          setDeliveryAuthorizeOpen(true)
        }}>继续授权交付</button>}
      </footer>
    </section></div>}
    {deliveryAuthorizeOpen && <div className={styles.modal}><section>
      <header><ShieldCheck /><div><span>FINAL DELIVERY</span><h2>授权正式交付</h2></div><button title="关闭" onClick={() => setDeliveryAuthorizeOpen(false)}><X /></button></header>
      <p>当前确认时间线已经绑定低清预览人工复核。请选择一次明确的交付方式；失败不会自动重试或切换方式。</p>
      <div className={styles.deliveryMethods}>
        {(deliveryWorkspace.data?.delivery_methods ?? []).map(method => <label key={method.kind} data-selected={deliveryMethod === method.kind} data-disabled={!method.available}>
          <input type="radio" name="prototype-delivery-method" checked={deliveryMethod === method.kind} disabled={!method.available} onChange={() => setDeliveryMethod(method.kind)} />
          {method.kind === 'local_ffmpeg' ? <Film /> : <Upload />}
          <span><strong>{method.label}</strong><small>{method.available ? (method.renderer_version ?? '等待上传最终 MP4') : method.reason}</small></span>
        </label>)}
      </div>
      <footer><button onClick={() => setDeliveryAuthorizeOpen(false)}>取消</button><button className={styles.confirmButton} disabled={!deliveryMethod || authorizeDelivery.isPending} onClick={() => authorizeDelivery.mutate()}>{authorizeDelivery.isPending ? '正在授权…' : '确认授权'}</button></footer>
    </section></div>}
    {deliveryStatusOpen && deliveryWorkspace.data && <div className={styles.modal}><section className={styles.deliveryStatusModal}>
      <header><ShieldCheck /><div><span>DELIVERY STATUS</span><h2>最终交付闭环</h2></div><button title="关闭" onClick={() => setDeliveryStatusOpen(false)}><X /></button></header>
      <dl>
        <div><dt>确认时间线</dt><dd>{deliveryWorkspace.data.confirmed_timeline ? `v${deliveryWorkspace.data.confirmed_timeline.version_number}` : '尚未确认'}</dd></div>
        <div><dt>预览复核</dt><dd>{deliveryWorkspace.data.preview_review ? '已绑定精确复核' : '尚未完成'}</dd></div>
        <div><dt>当前状态</dt><dd>{deliveryAttempt ? ({
          authorized: '等待外部上传',
          queued: '等待本机生成',
          rendering: '正在生成',
          output_registered: '等待文件验证',
          verified: '交付完成',
          blocked: '交付阻断',
        } as Record<string, string>)[deliveryAttempt.status] : '尚未授权'}</dd></div>
      </dl>
      {!deliveryAttempt && deliveryWorkspace.data.confirmed_timeline && deliveryWorkspace.data.preview_review && <div className={styles.deliveryStep}>
        <ShieldCheck /><span><strong>等待明确授权</strong><small>请选择本机 FFmpeg 或外部上传；系统不会默认选择。</small></span>
        <button className={styles.confirmButton} onClick={() => { setDeliveryStatusOpen(false); setDeliveryAuthorizeOpen(true) }}>选择交付方式</button>
      </div>}
      {!deliveryAttempt && (!deliveryWorkspace.data.confirmed_timeline || !deliveryWorkspace.data.preview_review) && <div className={styles.deliveryStep} data-warning="true">
        <AlertTriangle /><span><strong>交付条件尚未满足</strong><small>请先完成低清预览复核并确认当前时间线。</small></span>
        <button onClick={() => setDeliveryStatusOpen(false)}>返回剪辑</button>
      </div>}
      {(deliveryAttempt?.status === 'queued' || deliveryAttempt?.status === 'rendering') && <div className={styles.deliveryStep}>
        <RefreshCw className={deliveryAttempt.status === 'rendering' ? styles.spinning : undefined} />
        <span><strong>{deliveryAttempt.status === 'queued' ? '等待本机生成' : '正在生成最终 MP4'}</strong><small>Worker 只执行本次冻结请求；页面会自动刷新状态。</small></span>
        <button disabled={deliveryWorkspace.isFetching} onClick={() => deliveryWorkspace.refetch()}>{deliveryWorkspace.isFetching ? '刷新中…' : '立即刷新'}</button>
      </div>}
      {deliveryAttempt?.status === 'authorized' && <div className={styles.deliveryStep}>
        <Upload /><span><strong>上传最终 MP4</strong><small>{deliveryFile?.name ?? '尚未选择文件；只接受 MP4。'}</small></span>
        <label className={styles.deliveryFileButton}><Upload />选择文件<input type="file" accept="video/mp4,.mp4" onChange={event => setDeliveryFile(event.target.files?.[0] ?? null)} /></label>
        <button className={styles.confirmButton} disabled={!deliveryFile || uploadDelivery.isPending} onClick={() => uploadDelivery.mutate()}>{uploadDelivery.isPending ? '上传中…' : '上传并登记'}</button>
      </div>}
      {deliveryAttempt?.status === 'output_registered' && <div className={styles.deliveryStep}>
        <ShieldCheck /><span><strong>输出已经登记，尚未验证</strong><small>{deliveryAttempt.final_asset?.byte_size?.toLocaleString() ?? 0} bytes · 验证将复查 MP4、画幅、时长与音频合同。</small></span>
        <button className={styles.confirmButton} disabled={verifyDelivery.isPending} onClick={() => verifyDelivery.mutate()}>{verifyDelivery.isPending ? '验证中…' : '验证交付文件'}</button>
      </div>}
      {deliveryAttempt?.status === 'blocked' && <div className={styles.deliveryBlocked}>
        <AlertTriangle /><div><strong>{deliveryAttempt.error_code ?? 'DELIVERY_BLOCKED'}</strong><pre>{JSON.stringify(deliveryAttempt.error_detail, null, 2)}</pre></div>
      </div>}
      {deliveryAttempt?.status === 'verified' && deliveryAttempt.final_asset && <div className={styles.deliveryStep} data-complete="true">
        <CheckCircle2 /><span><strong>最终 MP4 已通过验证</strong><small>{deliveryAttempt.final_asset.width}×{deliveryAttempt.final_asset.height} · {seconds(deliveryAttempt.final_asset.duration_ms)}</small></span>
        <a className={styles.confirmButton} download href={`/api/v1/projects/${projectId}/assets/${deliveryAttempt.final_asset.id}/content`}><Download />下载 MP4</a>
      </div>}
      {deliveryAttempt && <details className={styles.deliveryEvidence}><summary>查看交付证据</summary><dl><div><dt>Attempt</dt><dd><code>{deliveryAttempt.id}</code></dd></div><div><dt>请求指纹</dt><dd><code>{deliveryAttempt.request_fingerprint}</code></dd></div><div><dt>执行方式</dt><dd>{deliveryAttempt.execution_kind}</dd></div></dl></details>}
      <footer><button onClick={() => setDeliveryStatusOpen(false)}>关闭</button></footer>
    </section></div>}
  </main>
}
