import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Film,
  Cloud, CloudOff, Download, Eye, EyeOff, Layers3, Lock, Maximize2, Music2, Pause, Play, Plus, Redo2,
  RefreshCw, Minus, Repeat2,
  RotateCcw, Scissors, Search, ShieldCheck, Sparkles, Subtitles, Trash2, Undo2, Unlock,
  Upload, Volume2, VolumeX, WandSparkles, X,
} from 'lucide-react'
import {
  useCallback, useEffect, useMemo, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { DeliveryAttempt, DeliveryWorkspace, Timeline, TimelineItem, TimelineItemDraft, TimelinePreview } from '../api/types'
import styles from './EditorPrototypePage.module.css'

const LOCAL_DRAFT_SCHEMA = 'editor-local-draft.v3'

interface LocalEditorDraft {
  schema_version: typeof LOCAL_DRAFT_SCHEMA
  base_timeline_id: string
  base_row_version: number
  items: TimelineItem[]
  timeline_zoom: number
  snap_enabled: boolean
  saved_at: string
}

interface SubtitleCue {
  sequence: number
  start_ms: number
  end_ms: number
  text: string
}

type ContinuityRelation = 'same_moment' | 'time_jump' | 'location_change' | 'outfit_change'

const CONTINUITY_RELATION_COPY: Record<ContinuityRelation, { label: string; tone: 'locked' | 'change'; summary: string }> = {
  same_moment: { label: '同一时刻', tone: 'locked', summary: '重点核对主体、动作阶段和运动方向是否连续。' },
  time_jump: { label: '时间跳转', tone: 'change', summary: '允许时间变化，但跳转意图应清楚且主体身份可辨。' },
  location_change: { label: '地点变化', tone: 'change', summary: '允许场景变化，但地点切换应清楚且叙事承接成立。' },
  outfit_change: { label: '换装', tone: 'change', summary: '允许服装变化，但换装应有明确叙事依据。' },
}

const CONTINUITY_CHECKS: Record<ContinuityRelation, Array<{ id: string; label: string }>> = {
  same_moment: [
    { id: 'subject', label: '主体身份、服装和场景保持一致' },
    { id: 'motion', label: '动作阶段与运动方向自然承接' },
    { id: 'eyeline', label: '构图、视线与主体位置没有异常跳变' },
  ],
  time_jump: [
    { id: 'jump-readable', label: '时间跳转在画面或叙事中足够清楚' },
    { id: 'subject', label: '跳转前后主体身份仍可辨认' },
    { id: 'new-information', label: '后镜提供了符合跳转意图的新信息' },
  ],
  location_change: [
    { id: 'location-readable', label: '新地点在切点后能够被清楚识别' },
    { id: 'subject', label: '跨地点的主体与叙事承接一致' },
    { id: 'orientation', label: '空间方向变化不会造成误读' },
  ],
  outfit_change: [
    { id: 'outfit-readable', label: '服装变化明确且不是生成漂移' },
    { id: 'reason', label: '换装与时间、地点或剧情变化一致' },
    { id: 'subject', label: '人物身份和其他稳定特征保持一致' },
  ],
}

const GENERAL_CONTINUITY_CHECKS = [
  { id: 'subject', label: '主体身份和外观没有非预期漂移' },
  { id: 'motion', label: '动作阶段、运动方向与切点节奏自然' },
  { id: 'change-readable', label: '时间、地点或服装变化是有意且可读的' },
]

function normalizeContinuityRelation(value: string | undefined): ContinuityRelation {
  return value && value in CONTINUITY_RELATION_COPY ? value as ContinuityRelation : 'same_moment'
}

function minimumVideoDurationForTransitions(item: TimelineItem) {
  const transitionIn = item.transform.transition_in as { type?: string; duration_ms?: number } | undefined
  const transitionOut = item.transform.transition_out as { type?: string; duration_ms?: number } | undefined
  const fadeDuration = Math.max(
    transitionIn?.type === 'fade' ? transitionIn.duration_ms ?? 0 : 0,
    transitionOut?.type === 'fade' ? transitionOut.duration_ms ?? 0 : 0,
  )
  return Math.max(200, fadeDuration * 2)
}

function pairedFadeDuration(left: TimelineItem, right: TimelineItem) {
  const transitionOut = left.transform.transition_out as { type?: string; duration_ms?: number } | undefined
  const transitionIn = right.transform.transition_in as { type?: string; duration_ms?: number } | undefined
  if (
    transitionOut?.type !== 'fade'
    || transitionIn?.type !== 'fade'
    || transitionOut.duration_ms !== transitionIn.duration_ms
  ) return null
  return transitionOut.duration_ms ?? 0
}

function reconcileStructuralTransitions(previousRows: TimelineItem[], nextRows: TimelineItem[]) {
  const nextBoundaryKeys = new Set(nextRows.slice(0, -1).map((row, index) => `${row.id}\u0000${nextRows[index + 1].id}`))
  const clearTransitionOut = new Set<string>()
  const clearTransitionIn = new Set<string>()
  let resetBoundaryCount = 0
  previousRows.slice(0, -1).forEach((left, index) => {
    const right = previousRows[index + 1]
    if (pairedFadeDuration(left, right) == null || nextBoundaryKeys.has(`${left.id}\u0000${right.id}`)) return
    clearTransitionOut.add(left.id)
    clearTransitionIn.add(right.id)
    resetBoundaryCount += 1
  })
  return {
    resetBoundaryCount,
    rows: nextRows.map(row => {
      if (!clearTransitionOut.has(row.id) && !clearTransitionIn.has(row.id)) return row
      const transform = { ...row.transform }
      if (clearTransitionOut.has(row.id)) transform.transition_out = { type: 'cut', duration_ms: 0 }
      if (clearTransitionIn.has(row.id)) transform.transition_in = { type: 'cut', duration_ms: 0 }
      return { ...row, transform }
    }),
  }
}

function isMediaPlaybackInterruption(error: unknown) {
  return typeof error === 'object'
    && error !== null
    && 'name' in error
    && (error as { name?: unknown }).name === 'AbortError'
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

function previewSeconds(ms: number) {
  return `${Number((ms / 1000).toFixed(2))}s`
}

function deliveryBlockCopy(attempt: DeliveryAttempt) {
  const code = attempt.error_code ?? 'DELIVERY_BLOCKED'
  if (code.includes('DIMENSIONS')) {
    return {
      title: '成片画幅与交付合同不一致',
      reason: '输出文件的宽高不符合已确认时间线冻结的画幅规格。',
    }
  }
  if (code.includes('DURATION')) {
    return {
      title: '成片时长与交付合同不一致',
      reason: '输出文件的实际时长超出已确认时长允许的误差范围。',
    }
  }
  if (code.includes('AUDIO_LOUDNESS') || code.includes('TRUE_PEAK')) {
    return {
      title: '成片声音没有通过母带检查',
      reason: '响度或峰值超出当前时间线冻结的声音交付标准。',
    }
  }
  if (code.includes('AUDIO_QC')) {
    return {
      title: '成片声音无法完成技术检测',
      reason: '系统没有取得足够的音频分析证据，不能把文件标记为已验证。',
    }
  }
  if (code.includes('MIME') || code.includes('CONTAINER')) {
    return {
      title: '文件格式不符合交付要求',
      reason: '当前正式交付只接受可验证的 MP4 文件。',
    }
  }
  if (code.includes('FILE_MISSING') || code.includes('FILE_INVALID') || code.includes('FILE_FACT')) {
    return {
      title: '交付文件缺失、损坏或已发生变化',
      reason: '登记文件与验证时读取到的文件事实不一致，系统无法确认它是原始交付输出。',
    }
  }
  if (code.includes('INPUT') || code.includes('FINGERPRINT') || code.includes('HASH_MISMATCH')) {
    return {
      title: '交付输入与已冻结合同不一致',
      reason: '时间线、素材或请求指纹在生成与验证之间发生了变化。',
    }
  }
  if (code.includes('STORAGE') || code.includes('OUTPUT_PATH') || code.includes('ASSET_ALREADY')) {
    return {
      title: '交付文件无法按冻结策略登记',
      reason: '输出路径、文件大小、存储类型或素材登记与当前交付策略冲突。',
    }
  }
  return {
    title: '本次交付已被确定性检查阻断',
    reason: '系统保留了失败事实，但没有足够依据把本次输出标记为已完成。',
  }
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

function BoundaryFrameStill({
  projectId,
  item,
  sourceTimeMs,
  label,
  fps,
  onActivate,
}: {
  projectId: string
  item: TimelineItem
  sourceTimeMs: number
  label: string
  fps: number
  onActivate: () => void
}) {
  const frameRef = useRef<HTMLVideoElement | null>(null)
  const [ready, setReady] = useState(false)
  const seekFrame = useCallback(() => {
    const video = frameRef.current
    if (!video || !Number.isFinite(video.duration)) return
    const latestTime = Math.max(0, video.duration - .001)
    video.currentTime = Math.min(latestTime, Math.max(0, sourceTimeMs / 1000))
  }, [sourceTimeMs])

  useEffect(() => {
    setReady(false)
    seekFrame()
  }, [seekFrame])

  return <button className={styles.boundaryFrame} data-ready={ready} onClick={onActivate}>
    <video
      ref={frameRef}
      aria-label={label}
      muted
      playsInline
      preload="auto"
      src={`/api/v1/projects/${projectId}/assets/${item.asset_id}/content`}
      onLoadedMetadata={seekFrame}
      onSeeked={() => setReady(true)}
    />
    <span><strong>{label}</strong><code>{timecode(sourceTimeMs, fps)}</code></span>
  </button>
}

function BoundaryFrameOverlay({
  projectId,
  left,
  right,
  leftSourceTimeMs,
  rightSourceTimeMs,
  leftLabel,
  rightLabel,
  fps,
  blendPercent,
  onLeftActivate,
  onRightActivate,
}: {
  projectId: string
  left: TimelineItem
  right: TimelineItem
  leftSourceTimeMs: number
  rightSourceTimeMs: number
  leftLabel: string
  rightLabel: string
  fps: number
  blendPercent: number
  onLeftActivate: () => void
  onRightActivate: () => void
}) {
  const leftRef = useRef<HTMLVideoElement | null>(null)
  const rightRef = useRef<HTMLVideoElement | null>(null)
  const [leftReady, setLeftReady] = useState(false)
  const [rightReady, setRightReady] = useState(false)
  const seekFrame = useCallback((video: HTMLVideoElement | null, sourceTimeMs: number) => {
    if (!video || !Number.isFinite(video.duration)) return
    const latestTime = Math.max(0, video.duration - .001)
    video.currentTime = Math.min(latestTime, Math.max(0, sourceTimeMs / 1000))
  }, [])

  useEffect(() => {
    setLeftReady(false)
    setRightReady(false)
    seekFrame(leftRef.current, leftSourceTimeMs)
    seekFrame(rightRef.current, rightSourceTimeMs)
  }, [leftSourceTimeMs, rightSourceTimeMs, seekFrame])

  return <div className={styles.boundaryOverlay} data-ready={leftReady && rightReady}>
    <div
      className={styles.boundaryOverlayCanvas}
      role="img"
      aria-label={`${leftLabel} 与 ${rightLabel} 叠加对齐，首帧透明度 ${blendPercent}%`}
    >
      <video
        ref={leftRef}
        aria-label={leftLabel}
        muted
        playsInline
        preload="auto"
        src={`/api/v1/projects/${projectId}/assets/${left.asset_id}/content`}
        onLoadedMetadata={event => seekFrame(event.currentTarget, leftSourceTimeMs)}
        onSeeked={() => setLeftReady(true)}
      />
      <video
        ref={rightRef}
        aria-label={rightLabel}
        muted
        playsInline
        preload="auto"
        style={{ opacity: rightReady ? blendPercent / 100 : .35 }}
        src={`/api/v1/projects/${projectId}/assets/${right.asset_id}/content`}
        onLoadedMetadata={event => seekFrame(event.currentTarget, rightSourceTimeMs)}
        onSeeked={() => setRightReady(true)}
      />
    </div>
    <div className={styles.boundaryOverlayFacts}>
      <button onClick={onLeftActivate}><strong>{leftLabel}</strong><code>{timecode(leftSourceTimeMs, fps)}</code></button>
      <span>叠加对齐</span>
      <button onClick={onRightActivate}><strong>{rightLabel}</strong><code>{timecode(rightSourceTimeMs, fps)}</code></button>
    </div>
  </div>
}

function srtTimestampMs(value: string) {
  const match = /^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$/.exec(value.trim())
  if (!match) return null
  return (((Number(match[1]) * 60 + Number(match[2])) * 60 + Number(match[3])) * 1000) + Number(match[4])
}

function parseSrtCues(srt: string): SubtitleCue[] {
  const cues: SubtitleCue[] = []
  for (const block of srt.replace(/\r\n/g, '\n').split(/\n{2,}/)) {
    const lines = block.split('\n').map(line => line.trim()).filter(Boolean)
    const timingIndex = lines.findIndex(line => line.includes('-->'))
    if (timingIndex < 0) continue
    const [startText, endText] = lines[timingIndex].split('-->').map(value => value.trim().split(/\s+/)[0])
    const start = srtTimestampMs(startText)
    const end = srtTimestampMs(endText)
    const text = lines.slice(timingIndex + 1).join('\n').trim()
    if (start == null || end == null || end <= start || !text) continue
    cues.push({ sequence: cues.length + 1, start_ms: start, end_ms: end, text })
  }
  return cues
}

function effectiveSubtitleCues(item: TimelineItem, srt: string | undefined) {
  const frozen = item.transform.subtitle_cues
  if (Array.isArray(frozen)) return frozen as unknown as SubtitleCue[]
  return srt ? parseSrtCues(srt) : []
}

function activeSubtitleText(cues: SubtitleCue[], sourceTimeMs: number) {
  return cues.find(cue => sourceTimeMs >= cue.start_ms && sourceTimeMs < cue.end_ms)?.text ?? ''
}

function subtitleCueError(cues: SubtitleCue[], durationMs: number) {
  if (!cues.length || cues.length > 200) return '字幕必须保留 1 到 200 条 cue。'
  let previousEnd = 0
  for (const [index, cue] of cues.entries()) {
    if (cue.sequence !== index + 1) return '字幕序号必须从 1 连续递增。'
    if (!Number.isInteger(cue.start_ms) || !Number.isInteger(cue.end_ms)
      || cue.start_ms < previousEnd || cue.end_ms <= cue.start_ms || cue.end_ms > durationMs) {
      return `第 ${index + 1} 条字幕必须按顺序、互不重叠并位于片段范围内。`
    }
    if (!cue.text.trim() || cue.text.length > 500 || cue.text.includes('\0')) {
      return `第 ${index + 1} 条字幕不能为空且不能超过 500 字符。`
    }
    previousEnd = cue.end_ms
  }
  return null
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
  const text = activeSubtitleText(effectiveSubtitleCues(item, subtitle.data), sourceTimeMs)
  return text ? <div className={styles.timelineSubtitle}>{text}</div> : null
}

function SubtitleCueStrip({ projectId, item }: { projectId: string; item: TimelineItem }) {
  const subtitle = useQuery({
    queryKey: ['editor-prototype-subtitle', projectId, item.asset_id],
    queryFn: async () => {
      const response = await fetch(`/api/v1/projects/${projectId}/assets/${item.asset_id}/content`)
      if (!response.ok) throw new Error(`字幕读取失败（${response.status}）`)
      return response.text()
    },
    staleTime: Infinity,
  })
  const cues = effectiveSubtitleCues(item, subtitle.data)
  const duration = Math.max(1, item.timeline_out_ms - item.timeline_in_ms)
  return <span className={styles.subtitleCueStrip} aria-label={`${cues.length} 条字幕 cue`}>
    {cues.map(cue => <i
      key={`${cue.sequence}-${cue.start_ms}-${cue.end_ms}`}
      title={`${cue.sequence}. ${cue.text}`}
      style={{
        left: `${Math.max(0, Math.min(100, (cue.start_ms / duration) * 100))}%`,
        width: `${Math.max(0.5, Math.min(100, ((cue.end_ms - cue.start_ms) / duration) * 100))}%`,
      }}
    />)}
  </span>
}

export function EditorPrototypePage() {
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const projects = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.projects(),
    refetchInterval: 5000,
  })
  const editorProjects = projects.data?.filter(project => ['quality_review', 'editing', 'delivery_ready', 'blocked', 'completed'].includes(project.status)) ?? []
  const requestedProjectId = params.get('project') ?? ''
  const projectId = requestedProjectId || editorProjects[0]?.id || ''
  const workspace = useQuery({
    queryKey: ['editor-prototype-workspace', projectId],
    queryFn: () => api.editorWorkspace(projectId),
    enabled: Boolean(projectId),
  })
  const serverDraft = useQuery({
    queryKey: ['editor-prototype-draft', projectId],
    queryFn: () => api.editorDraft(projectId),
    enabled: Boolean(projectId),
  })
  const deliveryWorkspace = useQuery({
    queryKey: ['editor-prototype-delivery', projectId],
    queryFn: () => api.deliveryWorkspace(projectId),
    enabled: Boolean(projectId),
    refetchInterval: query => {
      if (query.state.error) return false
      const currentTimelineId = query.state.data?.confirmed_timeline?.id
      const status = query.state.data?.attempts.find(attempt => attempt.timeline_id === currentTimelineId)?.status
      return status === 'queued' || status === 'rendering' ? 3000 : false
    },
  })
  const [items, setItems] = useState<TimelineItem[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [playheadMs, setPlayheadMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [boundaryPreviewEndMs, setBoundaryPreviewEndMs] = useState<number | null>(null)
  const [boundaryPreviewBeforeMs, setBoundaryPreviewBeforeMs] = useState<250 | 500 | 1000 | 2000>(1000)
  const [boundaryPreviewAfterMs, setBoundaryPreviewAfterMs] = useState<250 | 500 | 1000 | 2000>(1000)
  const [boundaryPreviewRate, setBoundaryPreviewRate] = useState<0.25 | 0.5 | 1>(1)
  const [boundaryFocusKey, setBoundaryFocusKey] = useState<string | null>(null)
  const [boundaryReviewSession, setBoundaryReviewSession] = useState<{
    boundaryIndexes: number[]
    position: number
    skippedCount: number
  } | null>(null)
  const [boundaryPreviewLoop, setBoundaryPreviewLoop] = useState<{
    boundaryKey: string
    leftItemId: string
    startMs: number
    endMs: number
    beforeMs: number
    afterMs: number
    label: string
    iteration: number
  } | null>(null)
  const [boundaryFrameComparisonKey, setBoundaryFrameComparisonKey] = useState<string | null>(null)
  const [boundaryFrameOverlayKey, setBoundaryFrameOverlayKey] = useState<string | null>(null)
  const [boundaryFrameStripKey, setBoundaryFrameStripKey] = useState<string | null>(null)
  const [pendingBoundaryPreviewKey, setPendingBoundaryPreviewKey] = useState<string | null>(null)
  const [boundaryFrameBlendPercent, setBoundaryFrameBlendPercent] = useState(50)
  const [boundaryContinuityChecks, setBoundaryContinuityChecks] = useState<Record<string, string[]>>({})
  const [monitorScale, setMonitorScale] = useState<'fit' | 'actual'>('fit')
  const [monitorFullscreen, setMonitorFullscreen] = useState(false)
  const [assetFilter, setAssetFilter] = useState<'all' | 'video' | 'audio' | 'subtitle'>('all')
  const [assetSearchOpen, setAssetSearchOpen] = useState(false)
  const [assetSearchQuery, setAssetSearchQuery] = useState('')
  const [gapAssetSelection, setGapAssetSelection] = useState(false)
  const [notice, setNotice] = useState('剪辑调整会自动保存为项目草稿；生成可导出版本时才冻结新时间线。')
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
  const [lastAutoSavedAt, setLastAutoSavedAt] = useState<string | null>(null)
  const [lastAutoSavedFingerprint, setLastAutoSavedFingerprint] = useState<string | null>(null)
  const [lastAutoSaveAttemptFingerprint, setLastAutoSaveAttemptFingerprint] = useState<string | null>(null)
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
  const pendingTimelineViewRef = useRef<{
    playheadRatio: number
    playheadViewportX: number
    fit: boolean
  } | null>(null)
  const audioPointerMovedRef = useRef(false)
  const timelineAudioRefs = useRef<Record<string, HTMLAudioElement | null>>({})

  const sourceTimeline = workspace.data?.timelines[0] ?? null
  const localDraftKey = `agency-studio.editor-draft.${projectId}`
  useEffect(() => {
    setItems([])
    setHistory([])
    setFuture([])
    setSelectedIndex(0)
    setPlayheadMs(0)
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryPreviewRate(1)
    setBoundaryFocusKey(null)
    setBoundaryReviewSession(null)
    setBoundaryFrameComparisonKey(null)
    setBoundaryFrameOverlayKey(null)
    setBoundaryFrameStripKey(null)
    setPendingBoundaryPreviewKey(null)
    setBoundaryFrameBlendPercent(50)
    setBoundaryContinuityChecks({})
    setDirty(false)
    setLastValidation(null)
    setLastPreview(null)
    setLastAutoSavedAt(null)
    setLastAutoSavedFingerprint(null)
    setLastAutoSaveAttemptFingerprint(null)
  }, [projectId])

  useEffect(() => {
    if (!sourceTimeline || !workspace.data || serverDraft.isPending) return
    let localRestored: LocalEditorDraft | null = null
    try {
      const raw = window.localStorage.getItem(localDraftKey)
      const parsed = raw ? JSON.parse(raw) as LocalEditorDraft : null
      if (
        parsed?.schema_version === LOCAL_DRAFT_SCHEMA
        && parsed.base_timeline_id === sourceTimeline.id
        && parsed.base_row_version === sourceTimeline.row_version
        && Array.isArray(parsed.items)
      ) localRestored = parsed
      else if (raw) window.localStorage.removeItem(localDraftKey)
    } catch {
      window.localStorage.removeItem(localDraftKey)
    }
    const remote = serverDraft.data
    const remoteMatches = Boolean(
      remote
      && remote.base_timeline_id === sourceTimeline.id
      && remote.base_timeline_row_version === sourceTimeline.row_version
    )
    const useRemote = Boolean(
      remoteMatches
      && (!localRestored || new Date(remote!.updated_at).getTime() >= new Date(localRestored.saved_at).getTime())
    )
    const remoteItems: TimelineItem[] | null = useRemote && remote
      ? remote.items.map(item => {
        const asset = workspace.data.available_assets.find(row => row.id === item.asset_id)
        return {
          id: item.client_item_id,
          track_type: item.track_type,
          sequence_number: item.sequence_number,
          asset_id: item.asset_id,
          asset_state: asset?.state ?? null,
          asset_type: asset?.asset_type ?? null,
          asset_duration_ms: asset?.duration_ms ?? null,
          label: item.label,
          gap_reason: item.gap_reason ?? null,
          source_in_ms: item.source_in_ms,
          source_out_ms: item.source_out_ms,
          timeline_in_ms: item.timeline_in_ms,
          timeline_out_ms: item.timeline_out_ms,
          transform: item.transform,
        }
      })
      : null
    setItems(remoteItems ?? localRestored?.items ?? sourceTimeline.items)
    setTimelineZoom(remote?.track_config.pixels_per_second && useRemote
      ? remote.track_config.pixels_per_second
      : localRestored?.timeline_zoom ?? sourceTimeline.track_config.pixels_per_second ?? 82)
    setSnapEnabled(useRemote && remote ? remote.track_config.snap_enabled : localRestored?.snap_enabled ?? sourceTimeline.track_config.snap_enabled)
    setDirty(Boolean(remoteItems || localRestored))
    setLastAutoSavedAt(useRemote && remote ? remote.updated_at : null)
    setLastAutoSavedFingerprint(null)
    setLastAutoSaveAttemptFingerprint(null)
    setHistory([])
    setFuture([])
    setSelectedIndex(0)
    setPlayheadMs(useRemote && remote ? remote.playhead_ms : 0)
    setPlaying(false)
    setBoundaryFocusKey(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
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
    setNotice(useRemote && remote
      ? `已恢复 ${new Date(remote.updated_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 自动保存的项目草稿。`
      : localRestored
        ? `已恢复 ${new Date(localRestored.saved_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 的本地草稿。`
        : '当前时间线已同步；开始调整后会自动保存到项目。')
  }, [sourceTimeline?.id, sourceTimeline?.row_version, localDraftKey, serverDraft.data, serverDraft.isPending, workspace.data])

  const durationMs = workspace.data?.duration_ms ?? 15000
  const outputFps = Math.max(1, Number(sourceTimeline?.output_spec.fps) || 24)
  const frameStepMs = Math.max(1, Math.round(1000 / outputFps))
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
  const selectedSubtitleCues = useMemo(
    () => selectedItem?.track_type === 'subtitle'
      ? effectiveSubtitleCues(selectedItem, subtitlePreview.data)
      : [],
    [selectedItem, subtitlePreview.data],
  )
  const mainItems = useMemo(() => items.filter(item => item.track_type === 'main_video'), [items])
  const audioItems = useMemo(() => items.filter(item => item.track_type === 'audio'), [items])
  const subtitleItems = useMemo(() => items.filter(item => item.track_type === 'subtitle'), [items])
  const shotSequenceByAssetId = useMemo(() => new Map(
    (workspace.data?.available_assets ?? [])
      .filter(asset => asset.shot_sequence_number != null)
      .map(asset => [asset.id, asset.shot_sequence_number as number]),
  ), [workspace.data?.available_assets])
  const shotCodeByAssetId = useMemo(() => new Map(
    (workspace.data?.available_assets ?? [])
      .filter(asset => asset.shot_code)
      .map(asset => [asset.id, asset.shot_code as string]),
  ), [workspace.data?.available_assets])
  const formalShotByCode = useMemo(() => new Map(
    (workspace.data?.shot_sequence ?? []).map(shot => [shot.shot_code, shot]),
  ), [workspace.data?.shot_sequence])
  const shotOrderIssues = useMemo(() => {
    const issues: Array<{ left: TimelineItem; right: TimelineItem; leftSequence: number; rightSequence: number }> = []
    let previous: { item: TimelineItem; sequence: number } | null = null
    for (const item of mainItems) {
      const sequence = item.asset_id ? shotSequenceByAssetId.get(item.asset_id) : undefined
      if (sequence == null) continue
      if (previous && previous.sequence > sequence) {
        issues.push({
          left: previous.item,
          right: item,
          leftSequence: previous.sequence,
          rightSequence: sequence,
        })
      }
      previous = { item, sequence }
    }
    return issues
  }, [mainItems, shotSequenceByAssetId])
  const shotOrderIssueItemIds = useMemo(
    () => new Set(shotOrderIssues.flatMap(issue => [issue.left.id, issue.right.id])),
    [shotOrderIssues],
  )
  const formalShotOrderText = (workspace.data?.shot_sequence ?? []).map(shot => shot.shot_code).join(' → ')
  const currentShotOrderText = mainItems
    .flatMap(item => item.asset_id && shotCodeByAssetId.get(item.asset_id) ? [shotCodeByAssetId.get(item.asset_id)!] : [])
    .join(' → ')
  const selectedMainPosition = mainItems.findIndex(item => item.id === selectedItem?.id)
  const previousMainItem = selectedMainPosition > 0 ? mainItems[selectedMainPosition - 1] : null
  const nextMainItem = selectedMainPosition >= 0 ? mainItems[selectedMainPosition + 1] ?? null : null
  const mainBoundaries = useMemo(
    () => mainItems.slice(0, -1).map((left, index) => ({
      key: `${left.id}-${mainItems[index + 1].id}`,
      left,
      right: mainItems[index + 1],
    })),
    [mainItems],
  )
  const focusedBoundaryIndex = mainBoundaries.findIndex(boundary => boundary.key === boundaryFocusKey)
  const inferredBoundaryIndex = selectedMainPosition < 0 || !mainBoundaries.length
    ? -1
    : Math.min(selectedMainPosition, mainBoundaries.length - 1)
  const activeBoundaryIndex = focusedBoundaryIndex >= 0 ? focusedBoundaryIndex : inferredBoundaryIndex
  const activeBoundaryKey = activeBoundaryIndex >= 0 ? mainBoundaries[activeBoundaryIndex]?.key ?? null : null
  const reviewableBoundaryIndexes = useMemo(
    () => mainBoundaries.flatMap((boundary, index) => boundary.left.asset_id && boundary.right.asset_id ? [index] : []),
    [mainBoundaries],
  )
  const nextMainAsset = workspace.data?.available_assets.find(asset => asset.id === nextMainItem?.asset_id) ?? null
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
  const deliveryAttempt = deliveryWorkspace.data?.attempts.find(
    attempt => attempt.timeline_id === deliveryWorkspace.data?.confirmed_timeline?.id,
  ) ?? null
  const cacheDeliveryWorkspace = (current: DeliveryWorkspace) => {
    queryClient.setQueryData(['editor-prototype-delivery', projectId], current)
    queryClient.setQueryData(['delivery-workspace', projectId], current)
  }
  const cacheDeliveryAttempt = (attempt: DeliveryAttempt) => {
    for (const queryKey of [
      ['editor-prototype-delivery', projectId],
      ['delivery-workspace', projectId],
    ] as const) {
      queryClient.setQueryData<DeliveryWorkspace>(queryKey, current => current
        ? { ...current, attempts: [attempt, ...current.attempts.filter(row => row.id !== attempt.id)] }
        : current)
    }
  }
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
  const autoSaveFingerprint = useMemo(() => JSON.stringify({
    base: sourceTimeline ? [sourceTimeline.id, sourceTimeline.row_version] : null,
    items,
    playhead_ms: Math.max(0, Math.round(playheadMs)),
    snap_enabled: snapEnabled,
    pixels_per_second: timelineZoom,
  }), [items, playheadMs, snapEnabled, sourceTimeline, timelineZoom])

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
    const sourceIn = selectedItem.source_in_ms ?? 0
    const sourceOut = selectedItem.source_out_ms ?? selectedItem.asset_duration_ms ?? sourceIn
    const timelineOffset = Math.max(0, Math.min(
      selectedItem.timeline_out_ms - selectedItem.timeline_in_ms,
      playheadMs - selectedItem.timeline_in_ms,
    ))
    const expectedTime = Math.min(sourceOut, sourceIn + timelineOffset) / 1000
    if (Math.abs(video.currentTime - expectedTime) > .05) video.currentTime = expectedTime
    if (playing) {
      void video.play().catch(error => {
        if (!isMediaPlaybackInterruption(error)) setNotice('浏览器阻止了时间线视频播放，请再次点击播放。')
      })
    }
    else video.pause()
  }, [
    playing,
    selectedItem?.id,
    selectedItem?.asset_id,
    selectedItem?.source_in_ms,
    selectedItem?.source_out_ms,
    selectedItem?.asset_duration_ms,
    selectedItem?.timeline_in_ms,
    selectedItem?.timeline_out_ms,
  ])

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
    const rate = boundaryPreviewEndMs == null ? 1 : boundaryPreviewRate
    if (videoRef.current) videoRef.current.playbackRate = rate
    Object.values(timelineAudioRefs.current).forEach(audio => {
      if (audio) audio.playbackRate = rate
    })
  }, [boundaryPreviewEndMs, boundaryPreviewRate, selectedItem?.id])

  useEffect(() => {
    for (const item of audioItems) {
      const audio = timelineAudioRefs.current[item.id]
      if (!audio || !item.asset_id) continue
      const active = playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms
      audio.muted = audioTrackMuted
      audio.playbackRate = boundaryPreviewEndMs == null ? 1 : boundaryPreviewRate
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
  }, [audioItems, audioTrackMuted, boundaryPreviewEndMs, boundaryPreviewRate, playheadMs, playing])

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
      snap_enabled: snapEnabled,
      saved_at: new Date().toISOString(),
    }
    window.localStorage.setItem(localDraftKey, JSON.stringify(draft))
  }, [dirty, items, localDraftKey, snapEnabled, sourceTimeline, timelineZoom])

  const autoSaveDraft = useMutation({
    mutationFn: async (fingerprint: string) => {
      if (!sourceTimeline) throw new Error('当前没有可保存的剪辑基线。')
      const draft = await api.saveEditorDraft(projectId, sourceTimeline, {
        ...sourceTimeline.track_config,
        audio_enabled: audioItems.length > 0,
        subtitle_enabled: subtitleItems.length > 0,
        snap_enabled: snapEnabled,
        pixels_per_second: timelineZoom,
      }, items, playheadMs)
      return { draft, fingerprint }
    },
    onSuccess: ({ draft, fingerprint }) => {
      setLastAutoSavedAt(draft.updated_at)
      setLastAutoSavedFingerprint(fingerprint)
    },
  })

  useEffect(() => {
    if (
      !sourceTimeline
      || !dirty
      || !items.length
      || autoSaveDraft.isPending
      || autoSaveFingerprint === lastAutoSavedFingerprint
      || autoSaveFingerprint === lastAutoSaveAttemptFingerprint
    ) return
    const timer = window.setTimeout(() => {
      setLastAutoSaveAttemptFingerprint(autoSaveFingerprint)
      autoSaveDraft.mutate(autoSaveFingerprint)
    }, 900)
    return () => window.clearTimeout(timer)
  }, [
    dirty,
    items,
    playheadMs,
    snapEnabled,
    sourceTimeline,
    timelineZoom,
    autoSaveDraft.isPending,
    autoSaveFingerprint,
    lastAutoSaveAttemptFingerprint,
    lastAutoSavedFingerprint,
  ])

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
        snap_enabled: snapEnabled,
        pixels_per_second: timelineZoom,
      }, draftItems())
      return api.validateTimeline(projectId, revised)
    },
    onSuccess: async timeline => {
      window.localStorage.removeItem(localDraftKey)
      await api.discardEditorDraft(projectId)
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
      if (dirty) throw new Error('请先生成一个可导出版本，再生成低清预览。')
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
    onSuccess: async review => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-workspace', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['editor-workspace', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['editor-prototype-delivery', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['delivery-workspace', projectId] }),
      ])
      setNotice(`低清预览人工复核已保存（${review.review_id}），可以继续确认时间线与正式交付。`)
      setPreviewReviewSaved(true)
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
      const refreshedDelivery = await deliveryWorkspace.refetch()
      const currentTimelineId = refreshedDelivery.data?.confirmed_timeline?.id
      const currentAttempt = refreshedDelivery.data?.attempts.find(attempt => attempt.timeline_id === currentTimelineId)
      if (currentAttempt) setDeliveryStatusOpen(true)
      else setDeliveryAuthorizeOpen(true)
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
      cacheDeliveryAttempt(attempt)
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
      if (!deliveryFile) {
        throw new Error('请先选择要上传的 MP4。')
      }
      const current = await api.deliveryWorkspace(projectId)
      cacheDeliveryWorkspace(current)
      const currentAttempt = current.attempts.find(attempt => attempt.timeline_id === current.confirmed_timeline?.id) ?? null
      if (!currentAttempt || currentAttempt.id !== deliveryAttempt?.id || currentAttempt.status !== 'authorized') {
        throw new Error('交付状态已在其他窗口或后台发生变化，已刷新为最新状态；请按当前可用动作继续。')
      }
      return api.uploadDelivery(projectId, currentAttempt, deliveryFile)
    },
    onSuccess: async attempt => {
      cacheDeliveryAttempt(attempt)
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
      const current = await api.deliveryWorkspace(projectId)
      cacheDeliveryWorkspace(current)
      const currentAttempt = current.attempts.find(attempt => attempt.timeline_id === current.confirmed_timeline?.id) ?? null
      if (!currentAttempt || currentAttempt.id !== deliveryAttempt?.id || currentAttempt.status !== 'output_registered' || !currentAttempt.final_asset) {
        throw new Error('交付状态已在其他窗口或后台发生变化，已刷新为最新状态；请按当前可用动作继续。')
      }
      return api.verifyDelivery(projectId, currentAttempt)
    },
    onSuccess: async attempt => {
      cacheDeliveryAttempt(attempt)
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
    void api.discardEditorDraft(projectId)
    setItems(sourceTimeline.items)
    setTimelineZoom(sourceTimeline.track_config.pixels_per_second)
    setSnapEnabled(sourceTimeline.track_config.snap_enabled)
    setHistory([])
    setFuture([])
    setDirty(false)
    setLastAutoSavedAt(null)
    setLastAutoSavedFingerprint(null)
    setLastAutoSaveAttemptFingerprint(null)
    setSelectedIndex(0)
    setPlayheadMs(0)
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryFocusKey(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setNotice('已丢弃自动保存的项目草稿，恢复到当前时间线版本。')
  }

  const boundaryKeyForItem = (item: TimelineItem) => {
    if (item.track_type !== 'main_video') return null
    const position = mainItems.findIndex(row => row.id === item.id)
    if (position < 0 || mainItems.length < 2) return null
    const leftPosition = Math.min(position, mainItems.length - 2)
    return `${mainItems[leftPosition].id}-${mainItems[leftPosition + 1].id}`
  }

  const selectItem = (item: TimelineItem, preserveBoundaryPreview = false) => {
    const index = items.findIndex(row => row.id === item.id)
    setSelectedIndex(index)
    setPlayheadMs(item.timeline_in_ms)
    setPlaying(false)
    if (!preserveBoundaryPreview) {
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setBoundaryFocusKey(boundaryKeyForItem(item))
      setBoundaryReviewSession(null)
      setPendingBoundaryPreviewKey(null)
    }
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
      setBoundaryFocusKey(boundaryKeyForItem(target))
    }
    setPlayheadMs(position)
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
  }

  const focusBoundaryAt = (targetIndex: number) => {
    const target = mainBoundaries[targetIndex]
    if (!target) return
    const focusItem = target.right.asset_id ? target.right : target.left
    const itemIndex = items.findIndex(item => item.id === focusItem.id)
    if (itemIndex < 0) return
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setBoundaryFrameComparisonKey(null)
    setBoundaryFrameOverlayKey(null)
    setBoundaryFrameStripKey(null)
    setPendingBoundaryPreviewKey(null)
    setSelectedIndex(itemIndex)
    setPlayheadMs(target.left.timeline_out_ms)
    setBoundaryFocusKey(target.key)
    setNotice(`已定位第 ${targetIndex + 1}/${mainBoundaries.length} 个切点：${target.left.label} → ${target.right.label}。`)
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

  const handlePlayButtonKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.code !== 'Space') return
    event.preventDefault()
    event.stopPropagation()
    togglePlayback()
  }

  const changeTimelineZoom = (value: number, fit = false) => {
    const viewport = timelineViewportRef.current
    const playheadRatio = durationMs > 0 ? playheadMs / durationMs : 0
    const nextZoom = Math.max(40, Math.min(180, Math.round(value)))
    if (viewport) {
      const currentPlayheadX = 84 + timelineWidth * playheadRatio
      pendingTimelineViewRef.current = {
        playheadRatio,
        playheadViewportX: Math.max(24, Math.min(
          viewport.clientWidth - 24,
          currentPlayheadX - viewport.scrollLeft,
        )),
        fit,
      }
    }
    if (nextZoom === timelineZoom) {
      pendingTimelineViewRef.current = null
      if (fit && viewport) viewport.scrollLeft = 0
      return
    }
    setTimelineZoom(nextZoom)
    setDirty(true)
  }

  const fitTimelineToViewport = () => {
    const viewport = timelineViewportRef.current
    if (!viewport || durationMs <= 0) return
    const usableWidth = Math.max(1, viewport.clientWidth - 84 - 12)
    const fitZoom = Math.floor(usableWidth / (durationMs / 1000))
    changeTimelineZoom(fitZoom, true)
    setNotice(fitZoom < 40
      ? '时间线已缩放到最小；当前窗口仍可横向滚动查看完整内容。'
      : '时间线已适应当前窗口，并回到时间线起点。')
  }

  const toggleSnap = () => {
    const next = !snapEnabled
    setSnapEnabled(next)
    setDirty(true)
    setNotice(next
      ? `已开启 ${snapIntervalMs}ms 磁吸；该设置会随本地草稿恢复，并在保存后冻结到新时间线版本。`
      : '已关闭磁吸；该设置会随本地草稿恢复，并在保存后冻结到新时间线版本。')
  }

  const togglePlayback = () => {
    if (playing) {
      setPlaying(false)
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setBoundaryReviewSession(null)
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
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPlaying(true)
  }

  const previewBoundary = (left: TimelineItem, right: TimelineItem, loop = false) => {
    if (!left.asset_id || !right.asset_id) {
      setNotice('切点两侧必须都有已批准画面，才能预览衔接。')
      return
    }
    const boundaryMs = left.timeline_out_ms
    const startMs = Math.max(left.timeline_in_ms, boundaryMs - boundaryPreviewBeforeMs)
    const endMs = Math.min(right.timeline_out_ms, boundaryMs + boundaryPreviewAfterMs)
    const boundaryKey = `${left.id}-${right.id}`
    const label = `${left.label} → ${right.label}`
    setBoundaryReviewSession(null)
    selectItem(left)
    setBoundaryFocusKey(boundaryKey)
    setPlayheadMs(startMs)
    advancingPlaybackRef.current = false
    setBoundaryPreviewEndMs(endMs)
    setBoundaryPreviewLoop(loop ? {
      boundaryKey,
      leftItemId: left.id,
      startMs,
      endMs,
      beforeMs: boundaryPreviewBeforeMs,
      afterMs: boundaryPreviewAfterMs,
      label,
      iteration: 0,
    } : null)
    setPlaying(true)
    setNotice(`正在以 ${boundaryPreviewRate}× ${loop ? '循环' : ''}预览 ${label}：切前 ${previewSeconds(boundaryPreviewBeforeMs)}，切后 ${previewSeconds(boundaryPreviewAfterMs)}。`)
  }

  useEffect(() => {
    if (!pendingBoundaryPreviewKey) return
    const boundary = mainBoundaries.find(row => row.key === pendingBoundaryPreviewKey)
    setPendingBoundaryPreviewKey(null)
    if (!boundary) {
      setNotice('切点应用成功，但更新后的相邻边界已不存在，未启动自动预览。')
      return
    }
    previewBoundary(boundary.left, boundary.right)
  }, [items, mainBoundaries, pendingBoundaryPreviewKey])

  const toggleBoundaryLoop = (left: TimelineItem, right: TimelineItem) => {
    const boundaryKey = `${left.id}-${right.id}`
    if (boundaryPreviewLoop?.boundaryKey === boundaryKey) {
      setPlaying(false)
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setNotice(`已停止 ${left.label} → ${right.label} 的切点循环预览。`)
      return
    }
    previewBoundary(left, right, true)
  }

  useEffect(() => {
    if (!boundaryPreviewLoop || boundaryPreviewLoop.iteration === 0) return
    const leftIndex = items.findIndex(item => item.id === boundaryPreviewLoop.leftItemId)
    if (leftIndex < 0) {
      setBoundaryPreviewLoop(null)
      setNotice('循环预览对应的切点已不存在，播放已安全停止。')
      return
    }
    advancingPlaybackRef.current = false
    setSelectedIndex(leftIndex)
    setBoundaryFocusKey(boundaryPreviewLoop.boundaryKey)
    setPlayheadMs(boundaryPreviewLoop.startMs)
    setBoundaryPreviewEndMs(boundaryPreviewLoop.endMs)
    setPlaying(true)
    setNotice(`正在以 ${boundaryPreviewRate}× 循环预览 ${boundaryPreviewLoop.label}：切前 ${previewSeconds(boundaryPreviewLoop.beforeMs)}，切后 ${previewSeconds(boundaryPreviewLoop.afterMs)}。`)
  }, [boundaryPreviewLoop?.iteration])

  const toggleBoundaryReview = () => {
    if (boundaryReviewSession) {
      setPlaying(false)
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setBoundaryReviewSession(null)
      setNotice('已停止全时间线切点连续巡检。')
      return
    }
    if (!reviewableBoundaryIndexes.length) {
      setNotice('当前时间线没有两侧画面都已补齐的切点，无法连续巡检。')
      return
    }
    videoRef.current?.pause()
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryFrameComparisonKey(null)
    setBoundaryFrameOverlayKey(null)
    setBoundaryFrameStripKey(null)
    setBoundaryReviewSession({
      boundaryIndexes: reviewableBoundaryIndexes,
      position: 0,
      skippedCount: mainBoundaries.length - reviewableBoundaryIndexes.length,
    })
  }

  useEffect(() => {
    if (!boundaryReviewSession) return
    const boundaryIndex = boundaryReviewSession.boundaryIndexes[boundaryReviewSession.position]
    const boundary = mainBoundaries[boundaryIndex]
    if (!boundary?.left.asset_id || !boundary.right.asset_id) {
      setPlaying(false)
      setBoundaryPreviewEndMs(null)
      setBoundaryReviewSession(null)
      setNotice('时间线在连续巡检期间发生变化，已安全停止；请重新开始。')
      return
    }
    const leftIndex = items.findIndex(item => item.id === boundary.left.id)
    if (leftIndex < 0) {
      setBoundaryReviewSession(null)
      return
    }
    const boundaryMs = boundary.left.timeline_out_ms
    const startMs = Math.max(boundary.left.timeline_in_ms, boundaryMs - boundaryPreviewBeforeMs)
    const endMs = Math.min(boundary.right.timeline_out_ms, boundaryMs + boundaryPreviewAfterMs)
    advancingPlaybackRef.current = false
    setSelectedIndex(leftIndex)
    setBoundaryFocusKey(boundary.key)
    setPlayheadMs(startMs)
    setBoundaryPreviewEndMs(endMs)
    setBoundaryPreviewLoop(null)
    setPlaying(true)
    setNotice(`连续巡检 ${boundaryReviewSession.position + 1}/${boundaryReviewSession.boundaryIndexes.length}：正在以 ${boundaryPreviewRate}× 预览 ${boundary.left.label} → ${boundary.right.label}。`)
  }, [boundaryReviewSession, boundaryPreviewAfterMs, boundaryPreviewBeforeMs, items, mainBoundaries])

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
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
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

  const resetStructuralPreviewState = () => {
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryFrameComparisonKey(null)
    setBoundaryFrameOverlayKey(null)
    setBoundaryFrameStripKey(null)
    setBoundaryFocusKey(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setBoundaryContinuityChecks({})
  }

  const undo = () => {
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
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
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
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
    const reconciled = reconcileStructuralTransitions(mainItems, normalized)
    resetStructuralPreviewState()
    commitItems(
      replaceMainTrack(items, reconciled.rows),
      `已在本地草稿中调整片段顺序${reconciled.resetBoundaryCount ? `；${reconciled.resetBoundaryCount} 组失效的成对转场已恢复为直接切换` : ''}。`,
      selectedItem.id,
    )
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
    const reconciled = reconcileStructuralTransitions(mainItems, normalized)
    resetStructuralPreviewState()
    commitItems(
      replaceMainTrack(items, reconciled.rows),
      `已把 ${moved.label} 拖到新的位置${reconciled.resetBoundaryCount ? `；${reconciled.resetBoundaryCount} 组失效的成对转场已恢复为直接切换` : ''}。`,
      moved.id,
    )
    setDraggedItemId(null)
  }

  const organizeMainTrackByShotOrder = () => {
    if (!shotOrderIssues.length || blockMainTrackEdit(mainItems[0] ?? null)) return
    const sortable = mainItems
      .map((item, index) => ({
        item,
        index,
        sequence: item.asset_id ? shotSequenceByAssetId.get(item.asset_id) : undefined,
      }))
      .filter((row): row is { item: TimelineItem; index: number; sequence: number } => row.sequence != null)
      .sort((left, right) => left.sequence - right.sequence || left.index - right.index)
    let sortableIndex = 0
    const reordered = mainItems.map(item => (
      item.asset_id && shotSequenceByAssetId.has(item.asset_id)
        ? sortable[sortableIndex++].item
        : item
    ))
    const normalized = normalizeMainTrack(reordered, durationMs)
    const reconciled = reconcileStructuralTransitions(mainItems, normalized)
    resetStructuralPreviewState()
    commitItems(
      replaceMainTrack(items, reconciled.rows),
      `已按正式分镜顺序整理 ${sortable.length} 个画面片段；补充素材、声音和字幕保持原位置${reconciled.resetBoundaryCount ? `，${reconciled.resetBoundaryCount} 组失效的成对转场已恢复为直接切换` : ''}，请重新预览切点。`,
      selectedItem?.id ?? normalized[0]?.id ?? null,
    )
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
    const targetDuration = Math.max(200, target.timeline_out_ms - target.timeline_in_ms)
    const replacementDuration = Math.min(asset.duration_ms, targetDuration)
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
      source_out_ms: replacementDuration,
      timeline_out_ms: target.timeline_in_ms + replacementDuration,
      transform: { fit: 'cover', transition_in: { type: 'cut', duration_ms: 0 }, transition_out: { type: 'cut', duration_ms: 0 } },
    }
    const rows = mainItems.map(item => item.id === target.id ? replacement : item)
    const normalized = normalizeMainTrack(rows, durationMs)
    commitItems(
      replaceMainTrack(items, normalized),
      asset.duration_ms > replacementDuration
        ? `已把 ${replacement.label} 投放到时间线，并按 ${(replacementDuration / 1000).toFixed(1)}s 缺口裁切。`
        : `已把 ${replacement.label} 投放到时间线。`,
      replacement.id,
    )
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
        : { render: 'burn_in', subtitle_cues: null },
    }
    commitItems([...items, newItem], `已把 ${newItem.label} 加入${trackType === 'audio' ? '声音' : '字幕'}轨。`, newItem.id)
    if (assetId === draggedAssetId) setDraggedAssetId(null)
  }

  const audioItemsConflict = (
    item: TimelineItem,
    startMs: number,
    endMs: number,
    rows: TimelineItem[],
  ) => rows.find(other => {
    if (other.id === item.id || startMs >= other.timeline_out_ms || endMs <= other.timeline_in_ms) return false
    const roles = new Set([
      String(item.transform.mix ?? 'voiceover'),
      String(other.transform.mix ?? 'voiceover'),
    ])
    return roles.size !== 2 || !roles.has('voiceover') || !roles.has('background_music')
  }) ?? null

  const refreshEnabledDucking = (rows: TimelineItem[]) => {
    const voiceovers = rows.filter(row => String(row.transform.mix ?? 'voiceover') === 'voiceover')
    return rows.map(row => {
      if (String(row.transform.mix ?? 'voiceover') !== 'background_music') return row
      const ducking = row.transform.ducking as Record<string, unknown> | undefined
      if (!ducking?.enabled) return row
      const regions = voiceovers
        .filter(voice => Math.max(voice.timeline_in_ms, row.timeline_in_ms) < Math.min(voice.timeline_out_ms, row.timeline_out_ms))
        .map(voice => ({
          start_ms: Math.max(voice.timeline_in_ms, row.timeline_in_ms) - row.timeline_in_ms,
          end_ms: Math.min(voice.timeline_out_ms, row.timeline_out_ms) - row.timeline_in_ms,
        }))
      return { ...row, transform: { ...row.transform, ducking: { ...ducking, regions } } }
    })
  }

  const envelopeGainAt = (points: Array<{ time_ms: number, gain_db: number }>, timeMs: number) => {
    if (!points.length) return 0
    if (timeMs <= points[0].time_ms) return points[0].gain_db
    if (timeMs >= points[points.length - 1].time_ms) return points[points.length - 1].gain_db
    const rightIndex = points.findIndex(point => point.time_ms >= timeMs)
    const left = points[Math.max(0, rightIndex - 1)]
    const right = points[rightIndex]
    const ratio = (timeMs - left.time_ms) / Math.max(1, right.time_ms - left.time_ms)
    return left.gain_db + ((right.gain_db - left.gain_db) * ratio)
  }

  const rebaseAudioEnvelope = (
    transform: Record<string, unknown>,
    trimStartDeltaMs: number,
    nextDurationMs: number,
  ) => {
    const raw = transform.volume_envelope
    if (!Array.isArray(raw)) return transform
    const points = raw
      .filter((point): point is { time_ms: number, gain_db: number } => (
        Boolean(point)
        && typeof point === 'object'
        && Number.isFinite((point as { time_ms?: unknown }).time_ms)
        && Number.isFinite((point as { gain_db?: unknown }).gain_db)
      ))
      .map(point => ({ time_ms: Number(point.time_ms), gain_db: Number(point.gain_db) }))
      .sort((left, right) => left.time_ms - right.time_ms)
    if (points.length < 2) return transform
    const sourceStartMs = trimStartDeltaMs
    const sourceEndMs = trimStartDeltaMs + nextDurationMs
    const nextEnvelope = [
      { time_ms: 0, gain_db: envelopeGainAt(points, sourceStartMs) },
      ...points
        .filter(point => point.time_ms > sourceStartMs && point.time_ms < sourceEndMs)
        .map(point => ({ time_ms: Math.round(point.time_ms - sourceStartMs), gain_db: point.gain_db })),
      { time_ms: nextDurationMs, gain_db: envelopeGainAt(points, sourceEndMs) },
    ]
    return { ...transform, volume_envelope: nextEnvelope }
  }

  const buildMovedAudioItems = (baseItems: TimelineItem[], item: TimelineItem, requestedStartMs: number) => {
    if (item.track_type !== 'audio') return false
    const baseAudio = baseItems.filter(row => row.track_type === 'audio')
    const clipDuration = item.timeline_out_ms - item.timeline_in_ms
    const nextStart = Math.max(0, Math.min(durationMs - clipDuration, snapMs(requestedStartMs)))
    const nextEnd = nextStart + clipDuration
    const conflict = audioItemsConflict(item, nextStart, nextEnd, baseAudio)
    if (conflict) return { nextItems: null, nextStart, conflict }
    if (nextStart === item.timeline_in_ms) return { nextItems: baseItems, nextStart, conflict: null }
    const moved = { ...item, timeline_in_ms: nextStart, timeline_out_ms: nextEnd }
    const nextAudio = refreshEnabledDucking(baseAudio.map(row => row.id === item.id ? moved : row))
      .sort((left, right) => left.timeline_in_ms - right.timeline_in_ms || left.sequence_number - right.sequence_number)
      .map((row, index) => ({ ...row, sequence_number: index + 1 }))
    const nextItems = [
      ...baseItems.filter(row => row.track_type === 'main_video'),
      ...nextAudio,
      ...baseItems.filter(row => row.track_type === 'subtitle'),
    ]
    return { nextItems, nextStart, conflict: null }
  }

  const moveAudioTrackItem = (item: TimelineItem, requestedStartMs: number) => {
    const result = buildMovedAudioItems(items, item, requestedStartMs)
    if (!result) return false
    if (result.conflict) {
      setNotice(`无法移动：会与同类声音 ${result.conflict.label} 重叠。旁白与 BGM 可以重叠，同类声音不能。`)
      return false
    }
    if (result.nextStart === item.timeline_in_ms) return false
    setPlaying(false)
    setPlayheadMs(result.nextStart)
    commitItems(result.nextItems, `已把 ${item.label} 移到 ${timecode(result.nextStart, outputFps)}。`, item.id)
    return true
  }

  const beginAudioTrackDrag = (event: ReactPointerEvent<HTMLButtonElement>, item: TimelineItem) => {
    if (event.button !== 0) return
    event.currentTarget.focus()
    event.preventDefault()
    event.stopPropagation()
    const lane = event.currentTarget.parentElement
    if (!lane) return
    const laneRect = lane.getBoundingClientRect()
    const clipRect = event.currentTarget.getBoundingClientRect()
    const grabOffsetMs = ((event.clientX - clipRect.left) / Math.max(1, clipRect.width))
      * (item.timeline_out_ms - item.timeline_in_ms)
    const originalItems = items
    let latestItems = originalItems
    let latestStart = item.timeline_in_ms
    let conflictLabel: string | null = null
    audioPointerMovedRef.current = false
    setPlaying(false)
    setSelectedIndex(items.findIndex(row => row.id === item.id))
    const onMove = (moveEvent: PointerEvent) => {
      if (Math.abs(moveEvent.clientX - event.clientX) >= 3) audioPointerMovedRef.current = true
      const positionMs = ((moveEvent.clientX - laneRect.left) / Math.max(1, laneRect.width)) * durationMs
      const result = buildMovedAudioItems(originalItems, item, positionMs - grabOffsetMs)
      if (!result) return
      if (result.conflict) {
        conflictLabel = result.conflict.label
        return
      }
      conflictLabel = null
      latestItems = result.nextItems
      latestStart = result.nextStart
      setItems(result.nextItems)
      setPlayheadMs(result.nextStart)
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
    }
    const onUp = () => {
      cleanup()
      if (latestStart === item.timeline_in_ms) {
        setItems(originalItems)
        if (conflictLabel) {
          setNotice(`无法移动：会与同类声音 ${conflictLabel} 重叠。旁白与 BGM 可以重叠，同类声音不能。`)
        }
        return
      }
      setHistory(rows => [...rows.slice(-49), originalItems])
      setFuture([])
      setItems(latestItems)
      setDirty(true)
      setSelectedIndex(latestItems.findIndex(row => row.id === item.id))
      setNotice(`已把 ${item.label} 拖到 ${timecode(latestStart, outputFps)}。`)
    }
    const onCancel = () => {
      cleanup()
      setItems(originalItems)
      setPlayheadMs(item.timeline_in_ms)
      setNotice('声音片段拖动已取消，时间线保持不变。')
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
  }

  const buildTrimmedAudioItems = (
    baseItems: TimelineItem[],
    item: TimelineItem,
    edge: 'start' | 'end',
    requestedDeltaMs: number,
  ) => {
    if (item.track_type !== 'audio') return null
    const sourceIn = item.source_in_ms ?? 0
    const sourceOut = item.source_out_ms ?? item.asset_duration_ms ?? 0
    const timelineIn = item.timeline_in_ms
    const timelineOut = item.timeline_out_ms
    const oldDuration = timelineOut - timelineIn
    const playbackMode = String((item.transform.playback as { mode?: string } | undefined)?.mode ?? 'trim')
    let nextSourceIn = sourceIn
    let nextSourceOut = sourceOut
    let nextTimelineIn = timelineIn
    let nextTimelineOut = timelineOut
    if (playbackMode === 'loop') {
      const minimumDuration = Math.max(200, sourceOut - sourceIn)
      if (edge === 'start') {
        nextTimelineIn = Math.max(0, Math.min(timelineOut - minimumDuration, timelineIn + requestedDeltaMs))
      } else {
        nextTimelineOut = Math.max(timelineIn + minimumDuration, Math.min(durationMs, timelineOut + requestedDeltaMs))
      }
    } else if (edge === 'start') {
      const deltaMs = Math.max(
        Math.max(-sourceIn, -timelineIn),
        Math.min(sourceOut - sourceIn - 200, requestedDeltaMs),
      )
      nextSourceIn = sourceIn + deltaMs
      nextTimelineIn = timelineIn + deltaMs
    } else {
      const maximumDelta = Math.min(
        (item.asset_duration_ms ?? sourceOut) - sourceOut,
        durationMs - timelineOut,
      )
      const deltaMs = Math.max(-(sourceOut - sourceIn - 200), Math.min(maximumDelta, requestedDeltaMs))
      nextSourceOut = sourceOut + deltaMs
      nextTimelineOut = timelineOut + deltaMs
    }
    const changed = nextTimelineIn !== timelineIn || nextTimelineOut !== timelineOut
      || nextSourceIn !== sourceIn || nextSourceOut !== sourceOut
    if (!changed) {
      return {
        nextItems: baseItems,
        changed,
        conflict: null,
        sourceIn,
        sourceOut,
        timelineIn,
        timelineOut,
      }
    }
    const baseAudio = baseItems.filter(row => row.track_type === 'audio')
    const conflict = audioItemsConflict(item, nextTimelineIn, nextTimelineOut, baseAudio)
    if (conflict) {
      return {
        nextItems: null,
        changed: false,
        conflict,
        sourceIn,
        sourceOut,
        timelineIn,
        timelineOut,
      }
    }
    const nextDuration = nextTimelineOut - nextTimelineIn
    const trimStartDelta = nextTimelineIn - timelineIn
    const transform = rebaseAudioEnvelope(item.transform, trimStartDelta, nextDuration)
    const trimmed: TimelineItem = {
      ...item,
      source_in_ms: nextSourceIn,
      source_out_ms: nextSourceOut,
      timeline_in_ms: nextTimelineIn,
      timeline_out_ms: nextTimelineOut,
      transform,
    }
    const nextAudio = refreshEnabledDucking(baseAudio.map(row => row.id === item.id ? trimmed : row))
      .sort((left, right) => left.timeline_in_ms - right.timeline_in_ms || left.sequence_number - right.sequence_number)
      .map((row, index) => ({ ...row, sequence_number: index + 1 }))
    return {
      nextItems: [
        ...baseItems.filter(row => row.track_type === 'main_video'),
        ...nextAudio,
        ...baseItems.filter(row => row.track_type === 'subtitle'),
      ],
      changed,
      conflict: null,
      sourceIn: nextSourceIn,
      sourceOut: nextSourceOut,
      timelineIn: nextTimelineIn,
      timelineOut: nextTimelineOut,
    }
  }

  const handleAudioTrimKeyDown = (
    event: ReactKeyboardEvent<HTMLElement>,
    item: TimelineItem,
    edge: 'start' | 'end',
  ) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    event.stopPropagation()
    const stepMs = event.shiftKey
      ? 1000
      : snapEnabled ? snapIntervalMs : Math.max(1, Math.round(1000 / outputFps))
    const result = buildTrimmedAudioItems(
      items,
      item,
      edge,
      event.key === 'ArrowRight' ? stepMs : -stepMs,
    )
    if (!result) return
    if (result.conflict) {
      setNotice(`无法裁切：会与同类声音 ${result.conflict.label} 重叠。`)
      return
    }
    if (!result.changed) {
      setNotice(`${edge === 'start' ? '左' : '右'}侧声音裁切已到合法边界。`)
      return
    }
    setPlaying(false)
    setPlayheadMs(edge === 'start' ? result.timelineIn : result.timelineOut)
    commitItems(
      result.nextItems,
      `已用键盘裁切 ${item.label} 的${edge === 'start' ? '左' : '右'}边缘。`,
      item.id,
    )
  }

  const beginAudioTrim = (
    event: ReactPointerEvent<HTMLElement>,
    item: TimelineItem,
    edge: 'start' | 'end',
  ) => {
    event.currentTarget.focus()
    event.preventDefault()
    event.stopPropagation()
    setPlaying(false)
    const originalItems = items
    const startX = event.clientX
    let latestItems = originalItems
    let latestResult = buildTrimmedAudioItems(originalItems, item, edge, 0)
    let conflictLabel: string | null = null
    const onMove = (moveEvent: PointerEvent) => {
      const rawDeltaMs = ((moveEvent.clientX - startX) / timelineZoom) * 1000
      const deltaMs = snapEnabled
        ? Math.round(rawDeltaMs / snapIntervalMs) * snapIntervalMs
        : Math.round(rawDeltaMs)
      const result = buildTrimmedAudioItems(originalItems, item, edge, deltaMs)
      if (!result) return
      if (result.conflict) {
        conflictLabel = result.conflict.label
        return
      }
      conflictLabel = null
      latestResult = result
      latestItems = result.nextItems
      setItems(result.nextItems)
      setPlayheadMs(edge === 'start' ? result.timelineIn : result.timelineOut)
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
    }
    const onUp = () => {
      cleanup()
      if (!latestResult || !latestResult.changed) {
        setItems(originalItems)
        if (conflictLabel) setNotice(`无法裁切：会与同类声音 ${conflictLabel} 重叠。`)
        return
      }
      setHistory(rows => [...rows.slice(-49), originalItems])
      setFuture([])
      setItems(latestItems)
      setDirty(true)
      setSelectedIndex(latestItems.findIndex(row => row.id === item.id))
      setNotice(`已拖动 ${item.label} 的${edge === 'start' ? '左' : '右'}边缘完成声音裁切。`)
    }
    const onCancel = () => {
      cleanup()
      setItems(originalItems)
      setPlayheadMs(item.timeline_in_ms)
      setNotice('声音裁切手势已取消，时间线保持不变。')
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
  }

  const updateSelectedTransform = (key: string, value: unknown) => {
    if (!selectedItem || blockMainTrackEdit()) return
    commitItems(items.map(item => item.id === selectedItem.id
      ? { ...item, transform: { ...item.transform, [key]: value } }
      : item), `已更新 ${selectedItem.label} 的片段设置。`, selectedItem.id)
  }

  const setSelectedSubtitleCues = (cues: SubtitleCue[] | null, message: string) => {
    if (!selectedItem || selectedItem.track_type !== 'subtitle') return false
    if (cues) {
      const error = subtitleCueError(cues, selectedItem.timeline_out_ms - selectedItem.timeline_in_ms)
      if (error) {
        setNotice(error)
        return false
      }
    }
    commitItems(items.map(item => item.id === selectedItem.id
      ? { ...item, transform: { ...item.transform, subtitle_cues: cues } }
      : item), message, selectedItem.id)
    return true
  }

  const updateSelectedSubtitleCue = (
    cueIndex: number,
    patch: Partial<Pick<SubtitleCue, 'start_ms' | 'end_ms' | 'text'>>,
  ) => {
    const cue = selectedSubtitleCues[cueIndex]
    if (!cue) return false
    if (
      (patch.start_ms == null || patch.start_ms === cue.start_ms)
      && (patch.end_ms == null || patch.end_ms === cue.end_ms)
      && (patch.text == null || patch.text === cue.text)
    ) return true
    const next = selectedSubtitleCues.map((row, index) => index === cueIndex
      ? { ...row, ...patch }
      : row)
    const changed = next[cueIndex]
    return setSelectedSubtitleCues(
      next,
      `已修订第 ${changed.sequence} 条字幕的${patch.text != null ? '文字' : '时点'}。`,
    )
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

  const setBoundaryTransition = (
    left: TimelineItem,
    right: TimelineItem,
    type: 'cut' | 'fade',
    requestedDurationMs = 300,
  ) => {
    if (blockMainTrackEdit(left)) return
    if (!left.asset_id || !right.asset_id || left.timeline_out_ms !== right.timeline_in_ms) {
      setNotice('只有紧邻且两侧都有画面的片段才能设置成对转场。')
      return
    }
    const maximum = Math.min(
      2000,
      Math.floor((left.timeline_out_ms - left.timeline_in_ms) / 2),
      Math.floor((right.timeline_out_ms - right.timeline_in_ms) / 2),
    )
    const durationMs = type === 'cut'
      ? 0
      : Math.max(100, Math.min(maximum, requestedDurationMs))
    const nextItems = items.map(item => {
      if (item.id === left.id) {
        return {
          ...item,
          transform: { ...item.transform, transition_out: { type, duration_ms: durationMs } },
        }
      }
      if (item.id === right.id) {
        return {
          ...item,
          transform: { ...item.transform, transition_in: { type, duration_ms: durationMs } },
        }
      }
      return item
    })
    commitItems(
      nextItems,
      type === 'fade'
        ? `已为 ${left.label} → ${right.label} 成对设置 ${seconds(durationMs)} 淡出/淡入；一次撤销可整体恢复。`
        : `已把 ${left.label} → ${right.label} 恢复为直接切换；一次撤销可整体恢复。`,
      right.id,
    )
  }

  const rollBoundary = (left: TimelineItem, right: TimelineItem, requestedDeltaMs: number) => {
    if (blockMainTrackEdit(left)) return false
    if (
      !left.asset_id
      || !right.asset_id
      || left.timeline_out_ms !== right.timeline_in_ms
      || left.source_in_ms == null
      || left.source_out_ms == null
      || right.source_in_ms == null
      || right.source_out_ms == null
    ) {
      setNotice('只有紧邻且两侧都有完整源区间的画面，才能滚动剪辑切点。')
      return false
    }
    const leftDuration = left.source_out_ms - left.source_in_ms
    const rightDuration = right.source_out_ms - right.source_in_ms
    const minimumLeftDuration = minimumVideoDurationForTransitions(left)
    const minimumRightDuration = minimumVideoDurationForTransitions(right)
    const minimumDelta = Math.max(
      -(leftDuration - minimumLeftDuration),
      -right.source_in_ms,
    )
    const maximumDelta = Math.min(
      (left.asset_duration_ms ?? left.source_out_ms) - left.source_out_ms,
      rightDuration - minimumRightDuration,
    )
    const deltaMs = Math.max(minimumDelta, Math.min(maximumDelta, requestedDeltaMs))
    if (!deltaMs) {
      setNotice(requestedDeltaMs < 0
        ? '切点已到可前移边界：前镜不能再缩短，或后镜源入点已到素材开头。'
        : '切点已到可后移边界：前镜没有更多源画面，或后镜不能再缩短。')
      return false
    }
    const nextBoundaryMs = left.timeline_out_ms + deltaMs
    const boundaryKey = `${left.id}-${right.id}`
    const nextItems = items.map(item => {
      if (item.id === left.id) {
        return {
          ...item,
          source_out_ms: left.source_out_ms! + deltaMs,
          timeline_out_ms: nextBoundaryMs,
        }
      }
      if (item.id === right.id) {
        return {
          ...item,
          source_in_ms: right.source_in_ms! + deltaMs,
          timeline_in_ms: nextBoundaryMs,
        }
      }
      return item
    })
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryContinuityChecks(current => {
      const next = { ...current }
      delete next[boundaryKey]
      return next
    })
    setPlayheadMs(nextBoundaryMs)
    commitItems(
      nextItems,
      `已把 ${left.label} → ${right.label} 的切点${deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(deltaMs), outputFps)}；成片总时长不变，连续性检查已重置。`,
      right.id,
    )
    return true
  }

  const applyBoundaryRoll = (left: TimelineItem, right: TimelineItem, deltaMs: number) => {
    if (rollBoundary(left, right, deltaMs)) {
      setPendingBoundaryPreviewKey(`${left.id}-${right.id}`)
    }
  }

  const slipBoundaryItem = (item: TimelineItem, requestedDeltaMs: number, focusTimelineMs: number) => {
    if (blockMainTrackEdit(item)) return
    if (!item.asset_id || item.source_in_ms == null || item.source_out_ms == null || item.asset_duration_ms == null) {
      setNotice('只有具备完整源区间和素材时长的画面，才能滑移源窗口。')
      return
    }
    const minimumDelta = -item.source_in_ms
    const maximumDelta = item.asset_duration_ms - item.source_out_ms
    const deltaMs = Math.max(minimumDelta, Math.min(maximumDelta, requestedDeltaMs))
    if (!deltaMs) {
      setNotice(requestedDeltaMs < 0
        ? `${item.label} 的源窗口已到素材开头。`
        : `${item.label} 的源窗口已到素材结尾。`)
      return
    }
    const itemPosition = mainItems.findIndex(row => row.id === item.id)
    const affectedBoundaryKeys = new Set<string>()
    if (itemPosition > 0) affectedBoundaryKeys.add(`${mainItems[itemPosition - 1].id}-${item.id}`)
    if (itemPosition >= 0 && itemPosition < mainItems.length - 1) {
      affectedBoundaryKeys.add(`${item.id}-${mainItems[itemPosition + 1].id}`)
    }
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryContinuityChecks(current => Object.fromEntries(
      Object.entries(current).filter(([key]) => !affectedBoundaryKeys.has(key)),
    ))
    setPlayheadMs(Math.max(item.timeline_in_ms, Math.min(item.timeline_out_ms, focusTimelineMs)))
    commitItems(
      items.map(row => row.id === item.id
        ? {
          ...row,
          source_in_ms: item.source_in_ms! + deltaMs,
          source_out_ms: item.source_out_ms! + deltaMs,
        }
        : row),
      `已把 ${item.label} 的源窗口${deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(deltaMs), outputFps)}；成片位置和时长不变，相邻连续性检查已重置。`,
      item.id,
    )
  }

  const slideMainItem = (item: TimelineItem, requestedDeltaMs: number) => {
    if (blockMainTrackEdit(item)) return
    const itemPosition = mainItems.findIndex(row => row.id === item.id)
    const previous = itemPosition > 0 ? mainItems[itemPosition - 1] : null
    const next = itemPosition >= 0 ? mainItems[itemPosition + 1] ?? null : null
    if (
      !previous?.asset_id
      || !item.asset_id
      || !next?.asset_id
      || previous.timeline_out_ms !== item.timeline_in_ms
      || item.timeline_out_ms !== next.timeline_in_ms
      || previous.source_in_ms == null
      || previous.source_out_ms == null
      || previous.asset_duration_ms == null
      || item.source_in_ms == null
      || item.source_out_ms == null
      || next.source_in_ms == null
      || next.source_out_ms == null
    ) {
      setNotice('只有前后紧邻且三段都有完整源区间的中间画面，才能滑动片段。')
      return
    }
    const previousDuration = previous.source_out_ms - previous.source_in_ms
    const nextDuration = next.source_out_ms - next.source_in_ms
    const minimumDelta = Math.max(
      -(previousDuration - minimumVideoDurationForTransitions(previous)),
      -next.source_in_ms,
    )
    const maximumDelta = Math.min(
      previous.asset_duration_ms - previous.source_out_ms,
      nextDuration - minimumVideoDurationForTransitions(next),
    )
    const deltaMs = Math.max(minimumDelta, Math.min(maximumDelta, requestedDeltaMs))
    if (!deltaMs) {
      setNotice(requestedDeltaMs < 0
        ? `${item.label} 已到可前移边界：前镜不能再缩短，或后镜没有更早的源画面。`
        : `${item.label} 已到可后移边界：前镜没有更多源画面，或后镜不能再缩短。`)
      return
    }
    const nextTimelineIn = item.timeline_in_ms + deltaMs
    const nextTimelineOut = item.timeline_out_ms + deltaMs
    const affectedBoundaryKeys = new Set([
      `${previous.id}-${item.id}`,
      `${item.id}-${next.id}`,
    ])
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryContinuityChecks(current => Object.fromEntries(
      Object.entries(current).filter(([key]) => !affectedBoundaryKeys.has(key)),
    ))
    setPlayheadMs(nextTimelineIn)
    commitItems(
      items.map(row => {
        if (row.id === previous.id) {
          return {
            ...row,
            source_out_ms: previous.source_out_ms! + deltaMs,
            timeline_out_ms: nextTimelineIn,
          }
        }
        if (row.id === item.id) {
          return {
            ...row,
            timeline_in_ms: nextTimelineIn,
            timeline_out_ms: nextTimelineOut,
          }
        }
        if (row.id === next.id) {
          return {
            ...row,
            source_in_ms: next.source_in_ms! + deltaMs,
            timeline_in_ms: nextTimelineOut,
          }
        }
        return row
      }),
      `已把 ${item.label} ${deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(deltaMs), outputFps)}；片段内容和时长不变，前后切点及连续性检查已联动。`,
      item.id,
    )
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
    const changedItem = { ...selectedItem, transform }
    const baseAudio = audioItems.map(item => item.id === selectedItem.id ? changedItem : item)
    const conflict = audioItemsConflict(
      changedItem,
      changedItem.timeline_in_ms,
      changedItem.timeline_out_ms,
      baseAudio,
    )
    if (conflict) {
      setNotice(`无法更改用途：会与同类声音 ${conflict.label} 重叠。请先调整成片位置。`)
      return
    }
    const nextAudio = refreshEnabledDucking(baseAudio)
    const nextItems = [
      ...items.filter(item => item.track_type === 'main_video'),
      ...nextAudio,
      ...items.filter(item => item.track_type === 'subtitle'),
    ]
    commitItems(nextItems, `已设为${mix === 'background_music' ? '背景音乐' : '旁白 / 对白'}。`, selectedItem.id)
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
      transform: {
        ...selectedItem.transform,
        transition_out: { type: 'cut', duration_ms: 0 },
      },
    }
    const right = {
      ...selectedItem,
      id: `${selectedItem.id}-right-${Date.now()}`,
      label: `${selectedItem.label} B`,
      source_in_ms: sourceIn + leftDuration,
      timeline_in_ms: splitAt,
      transform: {
        ...selectedItem.transform,
        transition_in: { type: 'cut', duration_ms: 0 },
      },
    }
    const rows = mainItems.flatMap(item => item.id === selectedItem.id ? [left, right] : [item])
    const nextItems = replaceMainTrack(items, normalizeMainTrack(rows, durationMs))
    resetStructuralPreviewState()
    commitItems(nextItems, `已在 ${timecode(splitAt, outputFps)} 分割片段；新内部切点使用直接切换，原片段外侧转场保持不变。`, right.id)
  }

  const deleteSelected = () => {
    if (!selectedItem) return
    if (selectedItem.track_type === 'main_video') {
      if (blockMainTrackEdit()) return
      const rows = mainItems.filter(item => item.id !== selectedItem.id)
      const normalized = normalizeMainTrack(rows, durationMs)
      const reconciled = reconcileStructuralTransitions(mainItems, normalized)
      const nextItems = replaceMainTrack(items, reconciled.rows)
      resetStructuralPreviewState()
      commitItems(
        nextItems,
        `已删除 ${selectedItem.label}，后续片段已波纹前移${reconciled.resetBoundaryCount ? `；${reconciled.resetBoundaryCount} 组失效的成对转场已恢复为直接切换` : ''}。`,
        reconciled.rows[0]?.id ?? null,
      )
    } else if (selectedItem.track_type === 'audio') {
      const nextAudio = refreshEnabledDucking(
        audioItems
          .filter(item => item.id !== selectedItem.id)
          .map((item, index) => ({ ...item, sequence_number: index + 1 })),
      )
      const nextItems = [
        ...items.filter(item => item.track_type === 'main_video'),
        ...nextAudio,
        ...items.filter(item => item.track_type === 'subtitle'),
      ]
      commitItems(nextItems, `已从声音轨移除 ${selectedItem.label}。`, nextAudio[0]?.id ?? nextItems[0]?.id ?? null)
    } else {
      let sequence = 0
      const nextItems = items
        .filter(item => item.id !== selectedItem.id)
        .map(item => item.track_type === selectedItem.track_type ? { ...item, sequence_number: ++sequence } : item)
      commitItems(nextItems, `已从字幕轨移除 ${selectedItem.label}。`, nextItems[0]?.id ?? null)
    }
    setPlayheadMs(Math.min(playheadMs, durationMs))
  }

  const buildTrimmedItems = (
    baseItems: TimelineItem[],
    item: TimelineItem,
    edge: 'start' | 'end',
    deltaMs: number,
  ) => {
    const sourceIn = item.source_in_ms ?? 0
    const sourceOut = item.source_out_ms ?? item.asset_duration_ms ?? 0
    const nextSourceIn = edge === 'start'
      ? Math.max(0, Math.min(sourceOut - 200, sourceIn + deltaMs))
      : sourceIn
    const nextSourceOut = edge === 'end'
      ? Math.max(sourceIn + 200, Math.min(item.asset_duration_ms ?? sourceOut, sourceOut + deltaMs))
      : sourceOut
    const changed = nextSourceIn !== sourceIn || nextSourceOut !== sourceOut
    if (!changed) return { items: baseItems, changed, sourceIn, sourceOut }
    const mainTrack = baseItems.filter(row => row.track_type === 'main_video').map(row => row.id === item.id
      ? {
        ...row,
        source_in_ms: nextSourceIn,
        source_out_ms: nextSourceOut,
        timeline_out_ms: row.timeline_in_ms + (nextSourceOut - nextSourceIn),
      }
      : row)
    return {
      items: replaceMainTrack(baseItems, normalizeMainTrack(mainTrack, durationMs)),
      changed,
      sourceIn: nextSourceIn,
      sourceOut: nextSourceOut,
    }
  }

  const handleTrimKeyDown = (
    event: ReactKeyboardEvent<HTMLElement>,
    item: TimelineItem,
    edge: 'start' | 'end',
  ) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    event.stopPropagation()
    if (!item.asset_id || blockMainTrackEdit(item)) return
    const stepMs = event.shiftKey ? 1000 : Math.max(1, Math.round(1000 / outputFps))
    const result = buildTrimmedItems(items, item, edge, event.key === 'ArrowRight' ? stepMs : -stepMs)
    if (!result.changed) {
      setNotice(`${edge === 'start' ? '左' : '右'}侧裁切已到素材边界。`)
      return
    }
    setPlaying(false)
    commitItems(
      result.items,
      `已用键盘把${edge === 'start' ? '左' : '右'}侧裁切到源时点 ${timecode(edge === 'start' ? result.sourceIn : result.sourceOut, outputFps)}。`,
      item.id,
    )
  }

  const beginTrim = (event: ReactPointerEvent<HTMLElement>, item: TimelineItem, edge: 'start' | 'end') => {
    event.currentTarget.focus()
    event.preventDefault()
    event.stopPropagation()
    if (!item.asset_id || blockMainTrackEdit(item)) return
    setPlaying(false)
    const originalItems = items
    const startX = event.clientX
    let changed = false
    const onMove = (moveEvent: PointerEvent) => {
      const rawDeltaMs = ((moveEvent.clientX - startX) / timelineZoom) * 1000
      const deltaMs = snapEnabled
        ? Math.round(rawDeltaMs / snapIntervalMs) * snapIntervalMs
        : Math.round(rawDeltaMs)
      const result = buildTrimmedItems(originalItems, item, edge, deltaMs)
      changed = result.changed
      setItems(result.items)
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
    }
    const onUp = () => {
      cleanup()
      if (!changed) {
        setItems(originalItems)
        return
      }
      setHistory(rows => [...rows.slice(-49), originalItems])
      setFuture([])
      setDirty(true)
      setNotice(`已拖动${edge === 'start' ? '左' : '右'}边缘裁切片段，后续片段自动波纹对齐。`)
    }
    const onCancel = () => {
      cleanup()
      setItems(originalItems)
      setNotice('裁切手势已取消，时间线保持不变。')
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
  }

  const overlayOpen = versionOpen
    || confirmSaveOpen
    || validationOpen
    || previewOpen
    || deliveryAuthorizeOpen
    || deliveryStatusOpen

  const closeTopOverlay = () => {
    if (deliveryStatusOpen) setDeliveryStatusOpen(false)
    else if (deliveryAuthorizeOpen) setDeliveryAuthorizeOpen(false)
    else if (previewOpen) setPreviewOpen(false)
    else if (validationOpen) setValidationOpen(false)
    else if (confirmSaveOpen) setConfirmSaveOpen(false)
    else if (versionOpen) setVersionOpen(false)
    else return false
    return true
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (event.key === 'Escape' && closeTopOverlay()) {
        event.preventDefault()
        event.stopPropagation()
        return
      }
      if (overlayOpen) return
      if (target?.closest('input, select, textarea, [contenteditable="true"]')) return
      if (event.key === '[' || event.key === ']') {
        event.preventDefault()
        focusBoundaryAt(activeBoundaryIndex + (event.key === ']' ? 1 : -1))
        return
      }
      if (event.code === 'Space') {
        if (target?.closest('button, a, [role="button"], audio, video')) return
        event.preventDefault()
        togglePlayback()
      }
      if (event.key === '\\') {
        event.preventDefault()
        fitTimelineToViewport()
      }
      if (
        !event.altKey
        && !event.ctrlKey
        && !event.metaKey
        && (event.code === 'Comma' || event.code === 'Period')
      ) {
        event.preventDefault()
        const boundary = mainBoundaries[activeBoundaryIndex]
        if (!boundary) {
          setNotice('当前没有可修剪的相邻切点，请先选择画面片段或定位切点。')
          return
        }
        const stepMs = event.shiftKey ? 1000 : frameStepMs
        applyBoundaryRoll(boundary.left, boundary.right, event.code === 'Period' ? stepMs : -stepMs)
        return
      }
      if (
        event.altKey
        && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
        && selectedItem?.track_type === 'audio'
      ) {
        event.preventDefault()
        const stepMs = event.shiftKey
          ? 1000
          : snapEnabled ? snapIntervalMs : Math.max(1, Math.round(1000 / outputFps))
        moveAudioTrackItem(
          selectedItem,
          selectedItem.timeline_in_ms + (event.key === 'ArrowRight' ? stepMs : -stepMs),
        )
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
    const pendingView = pendingTimelineViewRef.current
    if (pendingView) {
      pendingTimelineViewRef.current = null
      viewport.scrollLeft = pendingView.fit
        ? 0
        : Math.max(0, 84 + timelineWidth * pendingView.playheadRatio - pendingView.playheadViewportX)
      return
    }
    const playheadX = 84 + timelineWidth * (playheadMs / durationMs)
    const visibleStart = viewport.scrollLeft + 24
    const visibleEnd = viewport.scrollLeft + viewport.clientWidth - 24
    if (playheadX >= visibleStart && playheadX <= visibleEnd) return
    viewport.scrollLeft = Math.max(0, playheadX - viewport.clientWidth / 2)
  }, [durationMs, playheadMs, timelineWidth])

  const advancePlayback = useCallback(() => {
    if (advancingPlaybackRef.current) return
    advancingPlaybackRef.current = true
    videoRef.current?.pause()
    const position = mainItems.findIndex(item => item.id === selectedItem?.id)
    const next = position >= 0 ? mainItems[position + 1] : null
    if (!next) {
      setPlayheadMs(durationMs)
      setPlaying(false)
      setNotice('时间线预览播放完成。')
      return
    }
    selectItem(next, true)
    if (!next.asset_id) {
      setPlaying(false)
      setNotice('播放到缺口：需要先选择一种补齐方式。')
      return
    }
    setPlaying(true)
  }, [durationMs, mainItems, selectedItem?.id])

  const handleTimeUpdate = () => {
    const video = videoRef.current
    if (!video || !selectedItem || advancingPlaybackRef.current) return
    const sourceIn = selectedItem.source_in_ms ?? 0
    const next = selectedItem.timeline_in_ms + Math.max(0, video.currentTime * 1000 - sourceIn)
    setPlayheadMs(Math.min(next, selectedItem.timeline_out_ms))
  }

  const handleEnded = (event: React.SyntheticEvent<HTMLVideoElement>) => {
    if (videoRef.current !== event.currentTarget || !selectedItem?.asset_id) return
    const sourceOut = selectedItem.source_out_ms ?? selectedItem.asset_duration_ms ?? 0
    if (event.currentTarget.currentTime * 1000 + (1000 / outputFps) < sourceOut) return
    advancePlayback()
  }

  useEffect(() => {
    const video = videoRef.current
    if (!playing || !video || !selectedItem?.asset_id || selectedItem.track_type !== 'main_video') return
    const sourceIn = selectedItem.source_in_ms ?? 0
    const sourceOut = selectedItem.source_out_ms ?? selectedItem.asset_duration_ms ?? sourceIn
    if (sourceOut <= sourceIn) return
    const boundaryLeadMs = 1000 / outputFps
    let cancelled = false
    let videoFrameId: number | null = null
    let animationFrameId: number | null = null
    const frameApi = video as unknown as {
      requestVideoFrameCallback?: (callback: (now: number, metadata: { mediaTime: number }) => void) => number
      cancelVideoFrameCallback?: (id: number) => void
    }

    const inspectFrame = (mediaTimeSeconds: number) => {
      if (cancelled || advancingPlaybackRef.current || videoRef.current !== video) return
      const mediaTimeMs = mediaTimeSeconds * 1000
      const timelinePosition = selectedItem.timeline_in_ms + Math.max(0, mediaTimeMs - sourceIn)
      setPlayheadMs(Math.min(timelinePosition, selectedItem.timeline_out_ms))
      if (boundaryPreviewEndMs != null && timelinePosition >= boundaryPreviewEndMs - boundaryLeadMs) {
        if (boundaryPreviewLoop) {
          video.pause()
          advancingPlaybackRef.current = true
          setPlaying(false)
          setBoundaryPreviewEndMs(null)
          setBoundaryPreviewLoop({ ...boundaryPreviewLoop, iteration: boundaryPreviewLoop.iteration + 1 })
          return
        }
        if (boundaryReviewSession) {
          const nextPosition = boundaryReviewSession.position + 1
          if (nextPosition < boundaryReviewSession.boundaryIndexes.length) {
            video.pause()
            advancingPlaybackRef.current = true
            setPlaying(false)
            setBoundaryPreviewEndMs(null)
            setBoundaryReviewSession({ ...boundaryReviewSession, position: nextPosition })
            return
          }
          setPlayheadMs(boundaryPreviewEndMs)
          setPlaying(false)
          setBoundaryPreviewEndMs(null)
          setBoundaryPreviewLoop(null)
          setBoundaryReviewSession(null)
          setNotice(`全时间线切点连续巡检播放完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个可用切点${boundaryReviewSession.skippedCount ? `，跳过 ${boundaryReviewSession.skippedCount} 个含缺口边界` : ''}；人工检查项仍需逐项确认。`)
          return
        }
        setPlayheadMs(boundaryPreviewEndMs)
        setPlaying(false)
        setBoundaryPreviewEndMs(null)
        setBoundaryPreviewLoop(null)
        setNotice(`切前 ${previewSeconds(boundaryPreviewBeforeMs)}、切后 ${previewSeconds(boundaryPreviewAfterMs)} 预览完成；可调整切点或过渡后再次对比。`)
        return
      }
      if (mediaTimeMs >= sourceOut - boundaryLeadMs) {
        setPlayheadMs(selectedItem.timeline_out_ms)
        advancePlayback()
        return
      }
      schedule()
    }
    const schedule = () => {
      if (cancelled) return
      if (frameApi.requestVideoFrameCallback) {
        videoFrameId = frameApi.requestVideoFrameCallback((_now, metadata) => inspectFrame(metadata.mediaTime))
      } else {
        animationFrameId = window.requestAnimationFrame(() => inspectFrame(video.currentTime))
      }
    }
    schedule()
    return () => {
      cancelled = true
      if (videoFrameId != null) frameApi.cancelVideoFrameCallback?.(videoFrameId)
      if (animationFrameId != null) window.cancelAnimationFrame(animationFrameId)
    }
  }, [
    advancePlayback,
    boundaryPreviewEndMs,
    boundaryPreviewLoop,
    boundaryPreviewRate,
    boundaryReviewSession,
    boundaryPreviewAfterMs,
    boundaryPreviewBeforeMs,
    items,
    outputFps,
    playing,
    selectedItem?.asset_duration_ms,
    selectedItem?.asset_id,
    selectedItem?.id,
    selectedItem?.source_in_ms,
    selectedItem?.source_out_ms,
    selectedItem?.timeline_in_ms,
    selectedItem?.timeline_out_ms,
    selectedItem?.track_type,
  ])

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

  if (projects.isPending || workspace.isPending) return <main className={styles.loading}><Film /><strong>正在装载剪辑台…</strong></main>
  if (!projectId) return <main className={styles.loading}><AlertTriangle /><strong>当前没有可进入剪辑台的项目</strong></main>
  if (!workspace.data || workspace.error) return <main className={styles.loading}><AlertTriangle /><strong>无法读取当前剪辑项目</strong></main>
  if (!sourceTimeline) return <main className={styles.loading}>
    <AlertTriangle />
    <strong>{workspace.data.project_title} 还没有可编辑时间线</strong>
    <span>先创建或选择一个时间线版本，再进入剪辑工作台。</span>
    <Link className="primaryButton" to={`/editor/setup?project=${projectId}`}>前往时间线准备</Link>
  </main>

  return <main className={styles.prototype}>
    <header className={styles.topbar}>
      <Link to="/" title="返回项目列表"><ArrowLeft /></Link>
      <div className={styles.projectTitle}>
        <span>剪辑台</span>
        <strong>{workspace.data.project_title}</strong>
      </div>
      <select
        className={styles.projectSwitcher}
        aria-label="切换剪辑项目"
        value={projectId}
        onChange={event => {
          const next = new URLSearchParams(params)
          next.set('project', event.target.value)
          setParams(next)
        }}
      >
        {editorProjects.map(project => <option key={project.id} value={project.id}>{project.title}</option>)}
      </select>
      <button className={styles.versionButton} onClick={() => setVersionOpen(true)}>时间线 v{sourceTimeline?.version_number ?? '--'} <small>{autoSaveDraft.isPending ? '正在自动保存…' : autoSaveDraft.error ? '云端保存失败' : dirty ? '项目草稿已自动保存' : '已同步'} · 查看版本证据</small></button>
      <div className={styles.topActions}>
        <button title="撤销" disabled={!history.length} onClick={undo}><Undo2 /></button>
        <button title="重做" disabled={!future.length} onClick={redo}><Redo2 /></button>
        {dirty && <button title="丢弃自动保存的项目草稿" onClick={discardDraft}><RotateCcw /></button>}
        <button className={styles.primaryAction} disabled={saveAndValidate.isPending} onClick={() => {
          if (dirty) setConfirmSaveOpen(true)
          else if (validationErrors.length || unresolvedCount) setValidationOpen(true)
          else setNotice('当前版本已经通过检查，可以进入确认阶段。')
        }}>
          {dirty ? <CheckCircle2 /> : unresolvedCount || validationErrors.length ? <AlertTriangle /> : <CheckCircle2 />}
          {saveAndValidate.isPending ? '正在生成…' : dirty ? '生成可导出版本' : unresolvedCount || validationErrors.length ? `处理 ${Math.max(unresolvedCount, validationErrors.length)} 个问题` : '版本已通过'}
        </button>
      </div>
    </header>

    <section className={styles.statusbar} data-warning={unresolvedCount > 0 || validationErrors.length > 0 || Boolean(autoSaveDraft.error) || Boolean(saveAndValidate.error) || Boolean(renderPreview.error) || Boolean(reviewPreview.error) || Boolean(confirmTimeline.error) || Boolean(authorizeDelivery.error) || Boolean(uploadDelivery.error) || Boolean(verifyDelivery.error)}>
      {autoSaveDraft.isPending ? <Cloud /> : autoSaveDraft.error ? <CloudOff /> : unresolvedCount || validationErrors.length || saveAndValidate.error || renderPreview.error || reviewPreview.error || confirmTimeline.error || authorizeDelivery.error || uploadDelivery.error || verifyDelivery.error ? <AlertTriangle /> : <CheckCircle2 />}
      <span>{notice}</span>
      {autoSaveDraft.error && <button disabled={autoSaveDraft.isPending} onClick={() => {
        setLastAutoSaveAttemptFingerprint(autoSaveFingerprint)
        autoSaveDraft.mutate(autoSaveFingerprint)
      }}>{autoSaveDraft.error instanceof Error ? `${autoSaveDraft.error.message} · 重试保存` : '项目草稿保存失败 · 重试保存'}</button>}
      {saveAndValidate.error && <button onClick={() => setConfirmSaveOpen(true)}>{saveAndValidate.error instanceof Error ? saveAndValidate.error.message : '保存失败，请重试'}</button>}
      {renderPreview.error && <button disabled={renderPreview.isPending} onClick={() => renderPreview.mutate()}>{renderPreview.error instanceof Error ? renderPreview.error.message : '低清预览失败，请重试'}</button>}
      {reviewPreview.error && <button onClick={() => reviewPreview.mutate()}>{reviewPreview.error instanceof Error ? reviewPreview.error.message : '人工复核保存失败，请重试'}</button>}
      {confirmTimeline.error && <button onClick={() => confirmTimeline.mutate()}>{confirmTimeline.error instanceof Error ? confirmTimeline.error.message : '时间线确认失败，请重试'}</button>}
      {authorizeDelivery.error && <button onClick={() => setDeliveryAuthorizeOpen(true)}>{authorizeDelivery.error instanceof Error ? authorizeDelivery.error.message : '交付授权失败，请重试'}</button>}
      {uploadDelivery.error && <button onClick={() => setDeliveryStatusOpen(true)}>{uploadDelivery.error instanceof Error ? uploadDelivery.error.message : '交付文件上传失败'}</button>}
      {verifyDelivery.error && <button onClick={() => setDeliveryStatusOpen(true)}>{verifyDelivery.error instanceof Error ? verifyDelivery.error.message : '交付文件验证失败'}</button>}
      {validationErrors.length > 0 && <button onClick={() => setValidationOpen(true)}>查看 {validationErrors.length} 个检查问题</button>}
      <code>{lastAutoSavedAt ? `自动保存 ${new Date(lastAutoSavedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} · ` : ''}{workspace.data.aspect_ratio} · {outputFps}fps · {seconds(durationMs)}</code>
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
            preload="auto"
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
              event.currentTarget.playbackRate = boundaryPreviewEndMs == null ? 1 : boundaryPreviewRate
              if (playing) void event.currentTarget.play().catch(() => setNotice('浏览器阻止了时间线视频播放，请再次点击播放。'))
            }}
            onTimeUpdate={handleTimeUpdate}
            onEnded={handleEnded}
          />}
          {nextMainItem?.asset_id && nextMainAsset?.asset_type === 'video' && <video
            key={`preload-${nextMainItem.id}`}
            className={styles.nextVideoPreload}
            aria-hidden="true"
            tabIndex={-1}
            src={`/api/v1/projects/${projectId}/assets/${nextMainItem.asset_id}/content`}
            preload="auto"
            muted
            playsInline
            onLoadedMetadata={event => {
              event.currentTarget.currentTime = (nextMainItem.source_in_ms ?? 0) / 1000
            }}
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
          <button title={playing ? '暂停' : '播放'} className={styles.playButton} onClick={togglePlayback} onKeyDown={handlePlayButtonKeyDown}>{playing ? <Pause /> : <Play />}</button>
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
              : selectedItem.track_type === 'audio'
                ? '拖动声音片段两侧把手裁切；方向键精调，Shift 按 1 秒调整'
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
            {previousMainItem && nextMainItem && (() => {
              const slideContractReady = Boolean(
                previousMainItem.asset_id
                && selectedItem.asset_id
                && nextMainItem.asset_id
                && previousMainItem.timeline_out_ms === selectedItem.timeline_in_ms
                && selectedItem.timeline_out_ms === nextMainItem.timeline_in_ms
                && previousMainItem.source_in_ms != null
                && previousMainItem.source_out_ms != null
                && previousMainItem.asset_duration_ms != null
                && selectedItem.source_in_ms != null
                && selectedItem.source_out_ms != null
                && nextMainItem.source_in_ms != null
                && nextMainItem.source_out_ms != null,
              )
              const previousDuration = previousMainItem.source_in_ms != null && previousMainItem.source_out_ms != null
                ? previousMainItem.source_out_ms - previousMainItem.source_in_ms
                : 0
              const nextDuration = nextMainItem.source_in_ms != null && nextMainItem.source_out_ms != null
                ? nextMainItem.source_out_ms - nextMainItem.source_in_ms
                : 0
              const minimumSlideDelta = slideContractReady
                && previousMainItem.source_in_ms != null
                && previousMainItem.source_out_ms != null
                && nextMainItem.source_in_ms != null
                ? Math.max(
                  -(previousDuration - minimumVideoDurationForTransitions(previousMainItem)),
                  -nextMainItem.source_in_ms,
                )
                : 0
              const maximumSlideDelta = slideContractReady
                && previousMainItem.source_out_ms != null
                && previousMainItem.asset_duration_ms != null
                && nextMainItem.source_in_ms != null
                && nextMainItem.source_out_ms != null
                ? Math.min(
                  previousMainItem.asset_duration_ms - previousMainItem.source_out_ms,
                  nextDuration - minimumVideoDurationForTransitions(nextMainItem),
                )
                : 0
              return <div className={styles.clipSlide}>
                <div><strong>片段滑动</strong><span>内容和时长不变，联动前后切点</span></div>
                <div>
                  <button aria-label={`${selectedItem.label} 向前滑动 1 秒`} disabled={videoTrackLocked || minimumSlideDelta >= 0} onClick={() => slideMainItem(selectedItem, -1000)}>−1s</button>
                  <button aria-label={`${selectedItem.label} 向前滑动 1 帧`} disabled={videoTrackLocked || minimumSlideDelta >= 0} onClick={() => slideMainItem(selectedItem, -frameStepMs)}>−1帧</button>
                  <code>{timecode(selectedItem.timeline_in_ms, outputFps)}–{timecode(selectedItem.timeline_out_ms, outputFps)}</code>
                  <button aria-label={`${selectedItem.label} 向后滑动 1 帧`} disabled={videoTrackLocked || maximumSlideDelta <= 0} onClick={() => slideMainItem(selectedItem, frameStepMs)}>+1帧</button>
                  <button aria-label={`${selectedItem.label} 向后滑动 1 秒`} disabled={videoTrackLocked || maximumSlideDelta <= 0} onClick={() => slideMainItem(selectedItem, 1000)}>+1s</button>
                </div>
              </div>
            })()}
            {videoTrackLocked && <div className={styles.trimHint}>画面轨锁定期间只允许选择、寻帧和预览，不写入本地草稿。</div>}
          </section>}
          {selectedItem.track_type === 'main_video' && (previousMainItem || nextMainItem) && <section>
            <div className={styles.boundarySectionHeader}>
              <h3>镜头衔接</h3>
              <div aria-label="切点顺序导航">
                <button
                  title="上一个切点（[）"
                  aria-label="上一个切点"
                  aria-keyshortcuts="["
                  disabled={activeBoundaryIndex <= 0}
                  onClick={() => focusBoundaryAt(activeBoundaryIndex - 1)}
                ><ChevronLeft /></button>
                <span><b>{activeBoundaryIndex + 1}</b> / {mainBoundaries.length}</span>
                <button
                  title="下一个切点（]）"
                  aria-label="下一个切点"
                  aria-keyshortcuts="]"
                  disabled={activeBoundaryIndex < 0 || activeBoundaryIndex >= mainBoundaries.length - 1}
                  onClick={() => focusBoundaryAt(activeBoundaryIndex + 1)}
                ><ChevronRight /></button>
              </div>
            </div>
            <button
              className={styles.boundaryReviewRun}
              aria-pressed={Boolean(boundaryReviewSession)}
              disabled={!reviewableBoundaryIndexes.length}
              onClick={toggleBoundaryReview}
            ><Repeat2 />{boundaryReviewSession
                ? `停止连续巡检（${boundaryReviewSession.position + 1}/${boundaryReviewSession.boundaryIndexes.length}）`
                : `连续巡检 ${reviewableBoundaryIndexes.length} 个可播放切点${mainBoundaries.length > reviewableBoundaryIndexes.length ? ` · 跳过 ${mainBoundaries.length - reviewableBoundaryIndexes.length} 个缺口` : ''}`}</button>
            <div className={styles.boundaryList}>
              {([
                previousMainItem ? [previousMainItem, selectedItem] : null,
                nextMainItem ? [selectedItem, nextMainItem] : null,
              ] as Array<[TimelineItem, TimelineItem] | null>).filter((boundary): boundary is [TimelineItem, TimelineItem] => Boolean(boundary)).map(([left, right]) => {
                const transitionOut = left.transform.transition_out as { type?: string; duration_ms?: number } | undefined
                const transitionIn = right.transform.transition_in as { type?: string; duration_ms?: number } | undefined
                const pairedFade = transitionOut?.type === 'fade'
                  && transitionIn?.type === 'fade'
                  && transitionOut.duration_ms === transitionIn.duration_ms
                const pairedCut = (transitionOut?.type ?? 'cut') === 'cut' && (transitionIn?.type ?? 'cut') === 'cut'
                const durationMs = pairedFade ? transitionOut.duration_ms ?? 300 : 0
                const presetValue = pairedCut
                  ? 'cut'
                  : pairedFade ? `fade:${durationMs}` : 'mixed'
                const boundaryKey = `${left.id}-${right.id}`
                 const leftShotSequence = left.asset_id ? shotSequenceByAssetId.get(left.asset_id) : undefined
                 const rightShotSequence = right.asset_id ? shotSequenceByAssetId.get(right.asset_id) : undefined
                 const orderWarning = leftShotSequence != null && rightShotSequence != null && leftShotSequence > rightShotSequence
                 const leftShotCode = left.asset_id ? shotCodeByAssetId.get(left.asset_id) : undefined
                 const rightShotCode = right.asset_id ? shotCodeByAssetId.get(right.asset_id) : undefined
                 const leftFormalShot = leftShotCode ? formalShotByCode.get(leftShotCode) : undefined
                 const rightFormalShot = rightShotCode ? formalShotByCode.get(rightShotCode) : undefined
                 const formalAdjacent = leftShotSequence != null && rightShotSequence === leftShotSequence + 1
                 const continuityRelation = normalizeContinuityRelation(rightFormalShot?.continuity_relation)
                 const continuityCopy = CONTINUITY_RELATION_COPY[continuityRelation]
                 const continuityChecks = formalAdjacent
                   ? CONTINUITY_CHECKS[continuityRelation]
                   : GENERAL_CONTINUITY_CHECKS
                 const completedContinuityChecks = boundaryContinuityChecks[boundaryKey] ?? []
                 const sharedContinuityGroup = formalAdjacent
                   && leftFormalShot?.continuity_group_id
                   && leftFormalShot.continuity_group_id === rightFormalShot?.continuity_group_id
                   ? leftFormalShot.continuity_group_id
                   : null
                 const leftFrameSourceMs = Math.max(left.source_in_ms ?? 0, (left.source_out_ms ?? 0) - frameStepMs)
                 const rightFrameSourceMs = right.source_in_ms ?? 0
                 const framesOpen = boundaryFrameComparisonKey === boundaryKey
                 const overlayFrames = boundaryFrameOverlayKey === boundaryKey
                 const stripFrames = boundaryFrameStripKey === boundaryKey
                 const leftSourceInMs = left.source_in_ms ?? 0
                 const leftSourceOutMs = left.source_out_ms ?? leftSourceInMs
                 const rightSourceInMs = right.source_in_ms ?? 0
                 const rightSourceOutMs = right.source_out_ms ?? rightSourceInMs
                 const leftStripFrames = [3, 2, 1].map(offset => {
                   const sourceTimeMs = Math.max(leftSourceInMs, leftSourceOutMs - offset * frameStepMs)
                   return {
                     key: `left-${offset}`,
                     label: `切前 ${offset} 帧`,
                     rollDeltaMs: sourceTimeMs + frameStepMs - leftSourceOutMs,
                     sourceTimeMs,
                     timelineTimeMs: Math.min(left.timeline_out_ms - frameStepMs, left.timeline_in_ms + sourceTimeMs - leftSourceInMs),
                   }
                 })
                 const rightStripFrames = [0, 1, 2].map(offset => {
                   const sourceTimeMs = Math.min(
                     Math.max(rightSourceInMs, rightSourceOutMs - frameStepMs),
                     rightSourceInMs + offset * frameStepMs,
                   )
                   return {
                     key: `right-${offset}`,
                     label: `切后 ${offset} 帧`,
                     rollDeltaMs: sourceTimeMs - rightSourceInMs,
                     sourceTimeMs,
                     timelineTimeMs: Math.min(right.timeline_out_ms - frameStepMs, right.timeline_in_ms + sourceTimeMs - rightSourceInMs),
                   }
                 })
                 const rollReady = Boolean(
                   left.asset_id
                   && right.asset_id
                   && left.timeline_out_ms === right.timeline_in_ms
                   && left.source_in_ms != null
                   && left.source_out_ms != null
                   && right.source_in_ms != null
                   && right.source_out_ms != null,
                 )
                 const rollMinimumDelta = rollReady
                   ? Math.max(
                     -((left.source_out_ms! - left.source_in_ms!) - minimumVideoDurationForTransitions(left)),
                     -right.source_in_ms!,
                   )
                   : 0
                 const rollMaximumDelta = rollReady
                   ? Math.min(
                     (left.asset_duration_ms ?? left.source_out_ms!) - left.source_out_ms!,
                     (right.source_out_ms! - right.source_in_ms!) - minimumVideoDurationForTransitions(right),
                   )
                   : 0
                 const canRollEarlier = rollMinimumDelta < 0
                 const canRollLater = rollMaximumDelta > 0
                 const loopPreviewActive = boundaryPreviewLoop?.boundaryKey === boundaryKey
                 const canSlipLeftEarlier = left.source_in_ms != null && left.source_in_ms > 0
                 const canSlipLeftLater = left.source_out_ms != null
                   && left.asset_duration_ms != null
                   && left.source_out_ms < left.asset_duration_ms
                 const canSlipRightEarlier = right.source_in_ms != null && right.source_in_ms > 0
                 const canSlipRightLater = right.source_out_ms != null
                   && right.asset_duration_ms != null
                   && right.source_out_ms < right.asset_duration_ms
                 return <div
                   className={styles.boundaryControl}
                   data-order-warning={orderWarning}
                   data-focused={activeBoundaryKey === boundaryKey}
                   key={boundaryKey}
                   onFocusCapture={() => setBoundaryFocusKey(boundaryKey)}
                 >
                   <header>
                    <span><strong>{left.label}</strong><i>→</i><strong>{right.label}</strong></span>
                    {orderWarning && <em>顺序倒退</em>}
                     <code>{timecode(left.timeline_out_ms, outputFps)}</code>
                   </header>
                   {formalAdjacent && leftFormalShot && rightFormalShot ? <div className={styles.continuityContract} data-tone={continuityCopy.tone}>
                     <div>
                       <span>{continuityCopy.label}</span>
                       {sharedContinuityGroup && <code>{sharedContinuityGroup}</code>}
                       <em>{completedContinuityChecks.length}/{continuityChecks.length} 已检查</em>
                     </div>
                     <p>{continuityCopy.summary}</p>
                   </div> : <div className={styles.continuityUnavailable}>
                     <span>{leftShotSequence == null || rightShotSequence == null
                       ? '边界含补充素材，正式分镜没有声明这组衔接关系，请完整人工检查。'
                       : '当前两镜不是正式相邻分镜，不能套用原连续性关系，请按当前叙事人工判断。'}</span>
                     <em>{completedContinuityChecks.length}/{continuityChecks.length} 已检查</em>
                   </div>}
                   <div className={styles.boundaryActions}>
                    <button
                      disabled={!left.asset_id || !right.asset_id}
                      onClick={() => previewBoundary(left, right)}
                    ><Play />预览切点</button>
                    <select
                      aria-label={`${left.label} 到 ${right.label} 的衔接方式`}
                      disabled={videoTrackLocked || !left.asset_id || !right.asset_id}
                      value={presetValue}
                      onChange={event => {
                        const [type, rawDuration] = event.target.value.split(':')
                        setBoundaryTransition(left, right, type as 'cut' | 'fade', Number(rawDuration) || 0)
                      }}
                    >
                      {presetValue === 'mixed' && <option value="mixed" disabled>两侧设置不一致</option>}
                      {pairedFade && ![200, 300, 500].includes(durationMs) && <option value={`fade:${durationMs}`}>淡出淡入 · {seconds(durationMs)}</option>}
                      <option value="cut">直接切换</option>
                      <option value="fade:200">淡出淡入 · 0.2s</option>
                      <option value="fade:300">淡出淡入 · 0.3s</option>
                      <option value="fade:500">淡出淡入 · 0.5s</option>
                     </select>
                    </div>
                   <div className={styles.boundaryPreviewTools}>
                     <label>
                       <span>切前</span>
                       <select
                         aria-label={`${left.label} 到 ${right.label} 的切前预览窗口`}
                         disabled={!left.asset_id || !right.asset_id}
                         value={boundaryPreviewBeforeMs}
                         onChange={event => {
                           setBoundaryPreviewBeforeMs(Number(event.target.value) as 250 | 500 | 1000 | 2000)
                           setPlaying(false)
                           setBoundaryPreviewEndMs(null)
                           setBoundaryPreviewLoop(null)
                           setBoundaryReviewSession(null)
                           setNotice('已更新切前预览窗口；重新预览后生效。')
                         }}
                       >
                         <option value={250}>0.25 秒</option>
                         <option value={500}>0.5 秒</option>
                         <option value={1000}>1 秒</option>
                         <option value={2000}>2 秒</option>
                       </select>
                     </label>
                     <label>
                       <span>切后</span>
                       <select
                         aria-label={`${left.label} 到 ${right.label} 的切后预览窗口`}
                         disabled={!left.asset_id || !right.asset_id}
                         value={boundaryPreviewAfterMs}
                         onChange={event => {
                           setBoundaryPreviewAfterMs(Number(event.target.value) as 250 | 500 | 1000 | 2000)
                           setPlaying(false)
                           setBoundaryPreviewEndMs(null)
                           setBoundaryPreviewLoop(null)
                           setBoundaryReviewSession(null)
                           setNotice('已更新切后预览窗口；重新预览后生效。')
                         }}
                       >
                         <option value={250}>0.25 秒</option>
                         <option value={500}>0.5 秒</option>
                         <option value={1000}>1 秒</option>
                         <option value={2000}>2 秒</option>
                       </select>
                     </label>
                     <label>
                       <span>速度</span>
                       <select
                         aria-label={`${left.label} 到 ${right.label} 的切点预览速度`}
                         disabled={!left.asset_id || !right.asset_id}
                         value={boundaryPreviewRate}
                         onChange={event => {
                           const rate = Number(event.target.value) as 0.25 | 0.5 | 1
                           setBoundaryPreviewRate(rate)
                           setNotice(boundaryPreviewEndMs == null
                             ? `已把切点预览速度设为 ${rate}×；下次预览生效。`
                             : `当前切点预览已切换为 ${rate}×。`)
                         }}
                       >
                         <option value={0.25}>0.25×</option>
                         <option value={0.5}>0.5×</option>
                         <option value={1}>1×</option>
                       </select>
                     </label>
                     <button
                       disabled={!left.asset_id || !right.asset_id}
                       aria-pressed={loopPreviewActive}
                       onClick={() => toggleBoundaryLoop(left, right)}
                     ><Repeat2 />{loopPreviewActive ? '停止循环' : '循环预览'}</button>
                   </div>
                   <div className={styles.boundaryRoll}>
                     <div><strong>滚动剪辑</strong><span>联动源区间并自动试听；, / . 逐帧，Shift 粗调</span></div>
                     <div>
                       <button aria-label={`${left.label} 到 ${right.label} 切点前移 1 秒`} disabled={videoTrackLocked || !canRollEarlier} onClick={() => applyBoundaryRoll(left, right, -1000)}>−1s</button>
                       <button aria-keyshortcuts="," aria-label={`${left.label} 到 ${right.label} 切点前移 1 帧`} disabled={videoTrackLocked || !canRollEarlier} onClick={() => applyBoundaryRoll(left, right, -frameStepMs)}>−1帧</button>
                       <code>{timecode(left.timeline_out_ms, outputFps)}</code>
                       <button aria-keyshortcuts="." aria-label={`${left.label} 到 ${right.label} 切点后移 1 帧`} disabled={videoTrackLocked || !canRollLater} onClick={() => applyBoundaryRoll(left, right, frameStepMs)}>+1帧</button>
                       <button aria-label={`${left.label} 到 ${right.label} 切点后移 1 秒`} disabled={videoTrackLocked || !canRollLater} onClick={() => applyBoundaryRoll(left, right, 1000)}>+1s</button>
                     </div>
                   </div>
                   <div className={styles.boundarySlip}>
                     <div><strong>源窗口滑移</strong><span>更换动作帧，成片位置不变</span></div>
                     {([
                       { item: left, role: '前镜', earlier: canSlipLeftEarlier, later: canSlipLeftLater, focusMs: Math.max(left.timeline_in_ms, left.timeline_out_ms - frameStepMs) },
                       { item: right, role: '后镜', earlier: canSlipRightEarlier, later: canSlipRightLater, focusMs: right.timeline_in_ms },
                     ] as const).map(row => <div key={`${boundaryKey}-${row.role}`}>
                       <span><b>{row.role}</b><small>{row.item.label}</small></span>
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口前移 1 秒`} disabled={videoTrackLocked || !row.earlier} onClick={() => slipBoundaryItem(row.item, -1000, row.focusMs)}>−1s</button>
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口前移 1 帧`} disabled={videoTrackLocked || !row.earlier} onClick={() => slipBoundaryItem(row.item, -frameStepMs, row.focusMs)}>−1帧</button>
                       <code>{timecode(row.item.source_in_ms ?? 0, outputFps)}–{timecode(row.item.source_out_ms ?? 0, outputFps)}</code>
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口后移 1 帧`} disabled={videoTrackLocked || !row.later} onClick={() => slipBoundaryItem(row.item, frameStepMs, row.focusMs)}>+1帧</button>
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口后移 1 秒`} disabled={videoTrackLocked || !row.later} onClick={() => slipBoundaryItem(row.item, 1000, row.focusMs)}>+1s</button>
                     </div>)}
                   </div>
                   <button
                    className={styles.boundaryFrameToggle}
                    disabled={!left.asset_id || !right.asset_id}
                    aria-expanded={framesOpen}
                    aria-label={`${left.label} 到 ${right.label} ${framesOpen ? '收起切点定格' : '对比末帧 / 首帧'}`}
                    onClick={() => setBoundaryFrameComparisonKey(value => value === boundaryKey ? null : boundaryKey)}
                  ><Layers3 />{framesOpen ? '收起切点定格' : '对比末帧 / 首帧'}</button>
                    {framesOpen && left.asset_id && right.asset_id && <>
                     <div className={styles.boundaryFrameModes}>
                       <div>
                         <button aria-pressed={!overlayFrames && !stripFrames} onClick={() => {
                           setBoundaryFrameOverlayKey(null)
                           setBoundaryFrameStripKey(null)
                         }}>并排</button>
                         <button aria-pressed={overlayFrames} onClick={() => {
                           setBoundaryFrameOverlayKey(boundaryKey)
                           setBoundaryFrameStripKey(null)
                         }}>叠加对齐</button>
                         <button aria-pressed={stripFrames} onClick={() => {
                           setBoundaryFrameOverlayKey(null)
                           setBoundaryFrameStripKey(boundaryKey)
                         }}>动作帧带</button>
                       </div>
                       {overlayFrames && <label>首帧透明度
                         <input
                           aria-label={`${left.label} 到 ${right.label} 叠加首帧透明度`}
                           type="range"
                           min="0"
                           max="100"
                           step="5"
                           value={boundaryFrameBlendPercent}
                           onInput={event => setBoundaryFrameBlendPercent(Number(event.currentTarget.value))}
                         />
                         <code>{boundaryFrameBlendPercent}%</code>
                       </label>}
                     </div>
                     {stripFrames
                       ? <div className={styles.boundaryFrameStrip} aria-label={`${left.label} 到 ${right.label} 的动作连续帧带`}>
                         {([
                           { role: '前镜末端', item: left, frames: leftStripFrames, applyLabel: '设为前镜末帧', currentLabel: '当前末帧' },
                           { role: '后镜开头', item: right, frames: rightStripFrames, applyLabel: '设为后镜首帧', currentLabel: '当前首帧' },
                         ] as const).map(row => <section key={`${boundaryKey}-${row.role}`}>
                           <header><strong>{row.role}</strong><span>{row.item.label}</span></header>
                           <div>{row.frames.map(frame => {
                             const currentFrame = frame.rollDeltaMs === 0
                             const canApplyFrame = rollReady
                               && !currentFrame
                               && frame.rollDeltaMs >= rollMinimumDelta
                               && frame.rollDeltaMs <= rollMaximumDelta
                             return <div className={styles.boundaryFrameChoice} key={frame.key}>
                               <BoundaryFrameStill
                                 projectId={projectId}
                                 item={row.item}
                                 sourceTimeMs={frame.sourceTimeMs}
                                 label={`${row.item.label} ${frame.label}`}
                                 fps={outputFps}
                                 onActivate={() => seekTimeline(frame.timelineTimeMs)}
                               />
                               <button
                                 aria-label={`${left.label} 到 ${right.label} ${frame.label} ${row.applyLabel}`}
                                 disabled={videoTrackLocked || !canApplyFrame}
                                 title={videoTrackLocked
                                   ? '画面轨已锁定，不能修改切点。'
                                   : currentFrame
                                   ? '当前切点已经使用这一帧。'
                                   : canApplyFrame
                                   ? `把${frame.label}直接应用为${row.role}`
                                   : '该帧超出当前切点的合法滚动范围。'}
                                 onClick={() => applyBoundaryRoll(left, right, frame.rollDeltaMs)}
                               >{currentFrame ? row.currentLabel : row.applyLabel}</button>
                             </div>
                           })}</div>
                         </section>)}
                         <small>点击画面只定位主监看；“设为末帧 / 首帧”会形成一次可撤销操作，并自动播放调整后的切点窗口。</small>
                       </div>
                       : overlayFrames
                       ? <BoundaryFrameOverlay
                         projectId={projectId}
                         left={left}
                         right={right}
                         leftSourceTimeMs={leftFrameSourceMs}
                         rightSourceTimeMs={rightFrameSourceMs}
                         leftLabel={`${left.label} 末帧`}
                         rightLabel={`${right.label} 首帧`}
                         fps={outputFps}
                         blendPercent={boundaryFrameBlendPercent}
                         onLeftActivate={() => seekTimeline(Math.max(left.timeline_in_ms, left.timeline_out_ms - frameStepMs))}
                         onRightActivate={() => seekTimeline(right.timeline_in_ms)}
                       />
                       : <div className={styles.boundaryFrames}>
                         <BoundaryFrameStill
                           projectId={projectId}
                           item={left}
                           sourceTimeMs={leftFrameSourceMs}
                           label={`${left.label} 末帧`}
                           fps={outputFps}
                           onActivate={() => seekTimeline(Math.max(left.timeline_in_ms, left.timeline_out_ms - frameStepMs))}
                         />
                         <BoundaryFrameStill
                           projectId={projectId}
                           item={right}
                           sourceTimeMs={rightFrameSourceMs}
                           label={`${right.label} 首帧`}
                           fps={outputFps}
                           onActivate={() => seekTimeline(right.timeline_in_ms)}
                         />
                       </div>}
                   </>}
                   <div className={styles.continuityChecklist} aria-label={`${left.label} 到 ${right.label} 的人工连续性检查`}>
                     {continuityChecks.map(check => {
                       const checked = completedContinuityChecks.includes(check.id)
                       return <button
                         key={check.id}
                         type="button"
                         role="checkbox"
                         aria-checked={checked}
                         data-checked={checked}
                         onClick={() => setBoundaryContinuityChecks(current => {
                           const completed = current[boundaryKey] ?? []
                           return {
                             ...current,
                             [boundaryKey]: checked
                               ? completed.filter(value => value !== check.id)
                               : [...completed, check.id],
                           }
                         })}
                       ><CheckCircle2 />{check.label}</button>
                     })}
                     <small>仅记录本次页面的人工检查进度，不写入草稿，也不代表自动视觉分析或正式复核。</small>
                   </div>
                 </div>
              })}
            </div>
            <div className={styles.trimHint}>淡出淡入会同时设置前镜淡出和后镜淡入，并作为一个撤销步骤写入草稿；它不是交叉叠化。正式预览和导出使用同一冻结参数。</div>
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
            <div className={styles.audioPosition}>
              <span>成片位置</span>
              <strong>{timecode(selectedItem.timeline_in_ms, outputFps)} – {timecode(selectedItem.timeline_out_ms, outputFps)}</strong>
              <div>
                <button
                  disabled={selectedItem.timeline_in_ms <= 0}
                  onClick={() => moveAudioTrackItem(selectedItem, selectedItem.timeline_in_ms - (snapEnabled ? snapIntervalMs : Math.max(1, Math.round(1000 / outputFps))))}
                ><ChevronLeft />向左</button>
                <button
                  disabled={selectedItem.timeline_out_ms >= durationMs}
                  onClick={() => moveAudioTrackItem(selectedItem, selectedItem.timeline_in_ms + (snapEnabled ? snapIntervalMs : Math.max(1, Math.round(1000 / outputFps))))}
                >向右<ChevronRight /></button>
              </div>
              <small>拖动声音片段定位；Alt+方向键精调，Shift+Alt+方向键移动 1 秒。</small>
            </div>
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
              <small>压低 {String(((selectedItem.transform.ducking as Record<string, unknown> | undefined)?.reduction_db) ?? -12)} dB · attack {String(((selectedItem.transform.ducking as Record<string, unknown> | undefined)?.attack_ms) ?? 200)}ms · release {String(((selectedItem.transform.ducking as Record<string, unknown> | undefined)?.release_ms) ?? 500)}ms · 区间 {Array.isArray((selectedItem.transform.ducking as Record<string, unknown> | undefined)?.regions) ? ((selectedItem.transform.ducking as Record<string, unknown>).regions as unknown[]).length : 0} 个（随位置自动更新）</small>
            </>}
            <button className={styles.removeTrackItem} onClick={deleteSelected}>从声音轨移除</button>
          </section>}
          {selectedItem.track_type === 'subtitle' && <section className={styles.subtitleInspector}>
            <div className={styles.subtitleInspectorHeader}>
              <span><h3>逐条字幕</h3><small>{selectedSubtitleCues.length} 条 · {Array.isArray(selectedItem.transform.subtitle_cues) ? '本地修订' : '原始 SRT'}</small></span>
              <button
                disabled={!Array.isArray(selectedItem.transform.subtitle_cues)}
                onClick={() => setSelectedSubtitleCues(null, '已撤销逐条字幕修订，恢复冻结的原始 SRT。')}
              >恢复原文</button>
            </div>
            {subtitlePreview.isPending
              ? <div className={styles.subtitleCueState}>正在读取字幕…</div>
              : subtitlePreview.error
                ? <div className={styles.subtitleCueState}>字幕读取失败，不能安全修订。</div>
                : selectedSubtitleCues.length
                  ? <ol className={styles.subtitleCueList}>
                    {selectedSubtitleCues.map((cue, cueIndex) => <li key={`${cue.sequence}-${cue.start_ms}-${cue.end_ms}-${cue.text}`}>
                      <div>
                        <button
                          aria-label={`定位第 ${cue.sequence} 条字幕`}
                          onClick={() => {
                            setPlaying(false)
                            setPlayheadMs(selectedItem.timeline_in_ms + cue.start_ms)
                          }}
                        >#{cue.sequence}</button>
                        <label>开始<input
                          key={`start-${cue.sequence}-${cue.start_ms}`}
                          aria-label={`第 ${cue.sequence} 条字幕开始时间`}
                          type="number"
                          min="0"
                          max={(selectedItem.timeline_out_ms - selectedItem.timeline_in_ms) / 1000}
                          step=".001"
                          defaultValue={(cue.start_ms / 1000).toFixed(3)}
                          onBlur={event => {
                            const nextMs = Math.round(Number(event.currentTarget.value) * 1000)
                            if (!Number.isFinite(nextMs) || !updateSelectedSubtitleCue(cueIndex, { start_ms: nextMs })) {
                              event.currentTarget.value = (cue.start_ms / 1000).toFixed(3)
                            }
                          }}
                        /><small>秒</small></label>
                        <label>结束<input
                          key={`end-${cue.sequence}-${cue.end_ms}`}
                          aria-label={`第 ${cue.sequence} 条字幕结束时间`}
                          type="number"
                          min="0"
                          max={(selectedItem.timeline_out_ms - selectedItem.timeline_in_ms) / 1000}
                          step=".001"
                          defaultValue={(cue.end_ms / 1000).toFixed(3)}
                          onBlur={event => {
                            const nextMs = Math.round(Number(event.currentTarget.value) * 1000)
                            if (!Number.isFinite(nextMs) || !updateSelectedSubtitleCue(cueIndex, { end_ms: nextMs })) {
                              event.currentTarget.value = (cue.end_ms / 1000).toFixed(3)
                            }
                          }}
                        /><small>秒</small></label>
                      </div>
                      <textarea
                        key={`text-${cue.sequence}-${cue.text}`}
                        aria-label={`第 ${cue.sequence} 条字幕文字`}
                        defaultValue={cue.text}
                        maxLength={500}
                        rows={2}
                        onBlur={event => {
                          const nextText = event.currentTarget.value.replace(/\r\n/g, '\n').trim()
                          if (!updateSelectedSubtitleCue(cueIndex, { text: nextText })) {
                            event.currentTarget.value = cue.text
                          }
                        }}
                      />
                    </li>)}
                  </ol>
                  : <div className={styles.subtitleCueState}>字幕文件没有可编辑的有效 cue。</div>}
            <div className={styles.trimHint}>文字和时点修订会自动保存到项目草稿；生成可导出版本后，低清预览和最终 FFmpeg 使用同一冻结 cue。</div>
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

    <section className={styles.timelinePanel} data-shot-order-warning={shotOrderIssues.length > 0}>
      <header className={styles.timelineToolbar}>
        <div><strong>时间线</strong><span>{mainItems.length} 个画面片段 · {audioItems.length} 个音频 · {subtitleItems.length} 个字幕</span></div>
        <button disabled={videoTrackLocked || selectedItem?.track_type !== 'main_video' || !selectedItem.asset_id} onClick={splitSelected}><Scissors />分割</button>
        <button
          className={styles.deleteAction}
          title={selectedItem ? `删除所选：${selectedItem.label}（Delete）` : '请先选择时间轴上的片段'}
          disabled={!selectedItem || (selectedItem.track_type === 'main_video' && videoTrackLocked)}
          onClick={deleteSelected}
        ><Trash2 />删除所选</button>
        <button disabled={renderPreview.isPending} onClick={() => {
          if (dirty) setNotice('请先生成一个可导出版本，再生成低清预览。')
          else renderPreview.mutate()
        }}><Film />{renderPreview.isPending ? '预览生成中…' : '低清预览'}</button>
        {(deliveryWorkspace.data?.confirmed_timeline || deliveryAttempt) && <button onClick={() => setDeliveryStatusOpen(true)}>
          {deliveryAttempt?.status === 'queued' || deliveryAttempt?.status === 'rendering' ? <RefreshCw /> : deliveryAttempt?.status === 'verified' ? <CheckCircle2 /> : <ShieldCheck />}
          {deliveryAttempt?.status === 'verified' ? '成片交付' : deliveryAttempt ? '交付状态' : '授权交付'}
        </button>}
        <button
          data-active={snapEnabled}
          aria-pressed={snapEnabled}
          title={snapEnabled ? `已开启 ${snapIntervalMs}ms 磁吸` : '磁吸已关闭'}
          onClick={toggleSnap}
        >磁吸 {snapEnabled ? `${snapIntervalMs}ms` : '关闭'}</button>
        <button title="缩小时间线" disabled={timelineZoom <= 40} onClick={() => changeTimelineZoom(timelineZoom - 20)}><Minus /></button>
        <label>缩放<input aria-label="时间线缩放" type="range" min="40" max="180" value={timelineZoom} onChange={event => changeTimelineZoom(Number(event.target.value))} /></label>
        <button title="放大时间线" disabled={timelineZoom >= 180} onClick={() => changeTimelineZoom(timelineZoom + 20)}><Plus /></button>
        <button title="时间线适应窗口（\\）" onClick={fitTimelineToViewport}><Maximize2 />适应</button>
        <code>{timecode(playheadMs, outputFps)}</code>
      </header>
      {shotOrderIssues.length > 0 && <div className={styles.shotOrderWarning}>
        <AlertTriangle />
        <span>
          <strong>发现 {shotOrderIssues.length} 处分镜顺序倒退</strong>
          <small>当前：{currentShotOrderText || '未识别'} · 正式：{formalShotOrderText || '暂无正式分镜顺序'}</small>
        </span>
        <button disabled={videoTrackLocked} onClick={organizeMainTrackByShotOrder}>
          {videoTrackLocked ? '解锁后整理' : '按正式分镜整理'}
        </button>
      </div>}
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
              data-order-warning={shotOrderIssueItemIds.has(item.id)}
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
            >{item.asset_id && <i
              role="slider"
              aria-label={`${item.label} 左侧裁切把手`}
              aria-disabled={videoTrackLocked}
              aria-valuemin={0}
              aria-valuemax={item.asset_duration_ms ?? 0}
              aria-valuenow={item.source_in_ms ?? 0}
              aria-valuetext={timecode(item.source_in_ms ?? 0, outputFps)}
              tabIndex={videoTrackLocked ? -1 : 0}
              className={styles.trimHandle}
              data-edge="start"
              onPointerDown={event => beginTrim(event, item, 'start')}
              onKeyDown={event => handleTrimKeyDown(event, item, 'start')}
            />}
              {item.asset_id ? <><Film /><span><strong>{item.label}</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></> : <><AlertTriangle /><span><strong>缺少画面</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></>}
              {item.asset_id && <i
                role="slider"
                aria-label={`${item.label} 右侧裁切把手`}
                aria-disabled={videoTrackLocked}
                aria-valuemin={0}
                aria-valuemax={item.asset_duration_ms ?? 0}
                aria-valuenow={item.source_out_ms ?? 0}
                aria-valuetext={timecode(item.source_out_ms ?? 0, outputFps)}
                tabIndex={videoTrackLocked ? -1 : 0}
                className={styles.trimHandle}
                data-edge="end"
                onPointerDown={event => beginTrim(event, item, 'end')}
                onKeyDown={event => handleTrimKeyDown(event, item, 'end')}
              />}
            </button>)}
          </div>
        </div>
        <div className={styles.trackRow} data-track-muted={audioTrackMuted}>
          <label><Music2 /><span>声音</span><button title={audioTrackMuted ? '恢复声音轨' : '静音声音轨'} aria-pressed={audioTrackMuted} onClick={toggleAudioTrackMute}>{audioTrackMuted ? <VolumeX /> : <Volume2 />}</button></label>
          <div className={styles.trackLane} data-empty={!audioItems.length} onDragOver={event => event.preventDefault()} onDrop={event => {
            event.preventDefault()
            addAssetToTrack('audio')
          }}>{audioItems.length ? audioItems.map(item => <button
            key={item.id}
            data-selected={selectedItem?.id === item.id}
            className={styles.audioClip}
            style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }}
            onPointerDown={event => beginAudioTrackDrag(event, item)}
            onClick={event => {
              if (audioPointerMovedRef.current) {
                event.preventDefault()
                audioPointerMovedRef.current = false
                return
              }
              selectItem(item)
            }}
          ><i
              role="slider"
              aria-label={`${item.label} 声音左侧裁切把手`}
              aria-valuemin={0}
              aria-valuemax={durationMs}
              aria-valuenow={item.timeline_in_ms}
              aria-valuetext={`${timecode(item.timeline_in_ms, outputFps)}，源入点 ${timecode(item.source_in_ms ?? 0, outputFps)}`}
              tabIndex={0}
              className={styles.trimHandle}
              data-edge="start"
              onPointerDown={event => beginAudioTrim(event, item, 'start')}
              onKeyDown={event => handleAudioTrimKeyDown(event, item, 'start')}
            />
            <Waveform projectId={projectId} assetId={item.asset_id!} /><strong>{item.label}</strong>
            <i
              role="slider"
              aria-label={`${item.label} 声音右侧裁切把手`}
              aria-valuemin={0}
              aria-valuemax={durationMs}
              aria-valuenow={item.timeline_out_ms}
              aria-valuetext={`${timecode(item.timeline_out_ms, outputFps)}，源出点 ${timecode(item.source_out_ms ?? 0, outputFps)}`}
              tabIndex={0}
              className={styles.trimHandle}
              data-edge="end"
              onPointerDown={event => beginAudioTrim(event, item, 'end')}
              onKeyDown={event => handleAudioTrimKeyDown(event, item, 'end')}
            />
          </button>) : <span>拖入已批准配音或 BGM</span>}</div>
        </div>
        <div className={styles.trackRow} data-track-hidden={subtitleTrackHidden}>
          <label><Subtitles /><span>字幕</span><button title={subtitleTrackHidden ? '显示字幕轨' : '隐藏字幕轨'} aria-pressed={subtitleTrackHidden} onClick={toggleSubtitleTrackVisibility}>{subtitleTrackHidden ? <EyeOff /> : <Eye />}</button></label>
          <div className={styles.trackLane} data-empty={!subtitleItems.length} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); addAssetToTrack('subtitle') }}>{subtitleItems.length ? subtitleItems.map(item => <button key={item.id} data-selected={selectedItem?.id === item.id} className={styles.subtitleClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }} onClick={() => selectItem(item)}><SubtitleCueStrip projectId={projectId} item={item} /><Subtitles /><strong>{item.label}</strong></button>) : <span>拖入已批准字幕</span>}</div>
        </div>
        <i className={styles.playhead} style={{ left: `${84 + timelineWidth * (playheadMs / durationMs)}px` }}><b /></i>
        </div>
      </div>
      <footer><span><Sparkles />AI 初剪依据和版本证据已收进右侧抽屉</span><span>拖动时间尺寻帧 · ←/→ 逐帧 · [ / ] 巡检 · , / . 修剪切点 · \ 适应 · Space 播放</span></footer>
    </section>
    {versionOpen && <div className={styles.modal} role="dialog" aria-modal="true" aria-label="时间线版本与审计证据"><section className={styles.versionModal}>
      <header><Layers3 /><div><span>VERSION EVIDENCE</span><h2>时间线版本与审计证据</h2></div><button title="关闭" onClick={() => setVersionOpen(false)}><X /></button></header>
      <p>版本按新到旧排列。项目草稿随时自动保存；只有生成可导出版本时才冻结新合同，历史版本和历次成片都不会被覆盖。</p>
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
    {confirmSaveOpen && <div className={styles.modal} role="dialog" aria-modal="true" aria-label="生成可导出时间线版本"><section>
      <header><CheckCircle2 /><div><span>EXPORTABLE REVISION</span><h2>生成可导出版本并检查</h2></div><button title="关闭" onClick={() => setConfirmSaveOpen(false)}><X /></button></header>
      <p>当前项目草稿已经自动保存。这里会基于时间线 v{sourceTimeline?.version_number} 冻结一个新的可导出版本并立即检查；不会自动导出、覆盖旧成片或产生供应商费用。</p>
      <dl>
        <div><dt>新版本</dt><dd>v{(sourceTimeline?.version_number ?? 0) + 1}</dd></div>
        <div><dt>画面片段</dt><dd>{mainItems.length}</dd></div>
        <div><dt>显式空位</dt><dd>{unresolvedCount}</dd></div>
        <div><dt>基线版本</dt><dd>v{sourceTimeline?.version_number} · row {sourceTimeline?.row_version}</dd></div>
      </dl>
      {unresolvedCount > 0 && <div className={styles.modalWarning}><AlertTriangle /><span>允许保存含空位的候选，但检查不会通过；保存后会精确定位这些问题。</span></div>}
      <footer><button onClick={() => setConfirmSaveOpen(false)}>继续调整</button><button className={styles.confirmButton} disabled={saveAndValidate.isPending} onClick={() => saveAndValidate.mutate()}>{saveAndValidate.isPending ? '正在生成并检查…' : '生成可导出版本'}</button></footer>
    </section></div>}
    {validationOpen && <div className={styles.modal} role="dialog" aria-modal="true" aria-label="时间线检查问题"><section>
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
    {previewOpen && lastPreview && <div className={styles.modal} role="dialog" aria-modal="true" aria-label="时间线低清预览"><section className={styles.previewModal}>
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
        {lastPreview.state === 'ready' && <button disabled={renderPreview.isPending || reviewPreview.isPending || confirmTimeline.isPending} onClick={() => renderPreview.mutate()}>{renderPreview.isPending ? '检查中…' : '重新检查缓存'}</button>}
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
    {deliveryAuthorizeOpen && <div className={styles.modal} role="dialog" aria-modal="true" aria-label="授权正式交付"><section>
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
    {deliveryStatusOpen && deliveryWorkspace.data && <div className={styles.modal} role="dialog" aria-modal="true" aria-label="最终交付状态"><section className={styles.deliveryStatusModal}>
      <header><ShieldCheck /><div><span>DELIVERY STATUS</span><h2>最终交付闭环</h2></div><button title="关闭" onClick={() => setDeliveryStatusOpen(false)}><X /></button></header>
      {deliveryWorkspace.error && <div className={styles.deliveryRefreshError}>
        <AlertTriangle />
        <span><strong>交付状态刷新失败，自动刷新已暂停</strong><small>{deliveryWorkspace.error instanceof Error ? deliveryWorkspace.error.message : '暂时无法读取最新交付状态。'} 页面保留的是上一次成功读取的状态。</small></span>
        <button disabled={deliveryWorkspace.isFetching} onClick={() => deliveryWorkspace.refetch()}>{deliveryWorkspace.isFetching ? '重试中…' : '重新连接'}</button>
      </div>}
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
        <ShieldCheck /><span><strong>输出已经登记，尚未验证</strong><small>{deliveryAttempt.final_asset?.byte_size
          ? `${deliveryAttempt.final_asset.byte_size.toLocaleString()} bytes`
          : '文件大小将在验证时读取'} · 验证将复查 MP4、画幅、时长与音频合同。</small></span>
        <button className={styles.confirmButton} disabled={verifyDelivery.isPending} onClick={() => verifyDelivery.mutate()}>{verifyDelivery.isPending ? '验证中…' : '验证交付文件'}</button>
      </div>}
      {deliveryAttempt?.status === 'blocked' && (() => {
        const copy = deliveryBlockCopy(deliveryAttempt)
        return <div className={styles.deliveryBlocked}>
          <AlertTriangle />
          <div>
            <strong>{copy.title}</strong>
            <p>{copy.reason}</p>
            <small>本次 Attempt 已结束，系统不会自动重试、改写输出或切换交付方式。请返回剪辑台，创建并复核新的时间线版本后，再明确授权一次新交付。</small>
            <div className={styles.deliveryBlockedActions}>
              <button onClick={() => setDeliveryStatusOpen(false)}>返回剪辑处理</button>
              <button onClick={() => { setDeliveryStatusOpen(false); setVersionOpen(true) }}>查看时间线证据</button>
            </div>
          </div>
        </div>
      })()}
      {deliveryAttempt?.status === 'verified' && deliveryAttempt.final_asset && <div className={styles.deliveryStep} data-complete="true">
        <CheckCircle2 /><span><strong>本次 MP4 已通过验证</strong><small>{deliveryAttempt.final_asset.width}×{deliveryAttempt.final_asset.height} · {seconds(deliveryAttempt.final_asset.duration_ms)} · 可随时返回时间线继续剪辑并再次导出</small></span>
        <a className={styles.confirmButton} download href={`/api/v1/projects/${projectId}/assets/${deliveryAttempt.final_asset.id}/content`}><Download />下载 MP4</a>
      </div>}
      {deliveryAttempt && <details className={styles.deliveryEvidence}><summary>查看交付证据</summary><dl><div><dt>Attempt</dt><dd><code>{deliveryAttempt.id}</code></dd></div><div><dt>请求指纹</dt><dd><code>{deliveryAttempt.request_fingerprint}</code></dd></div><div><dt>执行方式</dt><dd>{deliveryAttempt.execution_kind}</dd></div>{deliveryAttempt.error_code && <div><dt>阻断代码</dt><dd><code>{deliveryAttempt.error_code}</code></dd></div>}</dl>{deliveryAttempt.error_detail && <pre>{JSON.stringify(deliveryAttempt.error_detail, null, 2)}</pre>}</details>}
      <footer><button onClick={() => setDeliveryStatusOpen(false)}>{deliveryAttempt?.status === 'verified' ? '继续剪辑' : '关闭'}</button></footer>
    </section></div>}
  </main>
}
