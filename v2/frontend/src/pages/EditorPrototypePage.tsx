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
  type SyntheticEvent as ReactSyntheticEvent,
} from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { DeliveryAttempt, DeliveryWorkspace, EditorActionSequenceEvidence, EditorBoundaryCandidateReviewSession, EditorContinuityObservation, Timeline, TimelineItem, TimelineItemDraft, TimelinePreview } from '../api/types'
import styles from './EditorPrototypePage.module.css'

const LOCAL_DRAFT_SCHEMA = 'editor-local-draft.v12'

interface LocalEditorDraft {
  schema_version: typeof LOCAL_DRAFT_SCHEMA
  base_timeline_id: string
  base_row_version: number
  items: TimelineItem[]
  timeline_zoom: number
  snap_enabled: boolean
  playhead_ms: number
  boundary_continuity_outcomes: Record<string, Record<string, BoundaryContinuityCheckOutcome>>
  boundary_continuity_issue_contexts: Record<string, BoundaryContinuityIssueContext[]>
  boundary_continuity_observations: BoundaryContinuityObservations
  boundary_candidate_review_sessions: Record<string, BoundaryCandidateReviewSession>
  saved_at: string
}

interface SubtitleCue {
  sequence: number
  start_ms: number
  end_ms: number
  text: string
}

interface BoundaryPixelAnalysis {
  luminance_delta_percent: number
  color_delta_percent: number
  pixel_change_percent: number
  level: 'low' | 'medium' | 'high'
}

interface BoundaryMotionAnalysis {
  left_change_percent: number
  right_change_percent: number
  right_minus_left_percentage_points: number
  left_grid_change_percent: number[]
  right_grid_change_percent: number[]
  right_minus_left_grid_percentage_points: number[]
  left_centroid: MotionChangeCentroid | null
  right_centroid: MotionChangeCentroid | null
  left_rhythm_change_percent: Array<number | null>
  right_rhythm_change_percent: Array<number | null>
  left_rhythm_centroids: Array<MotionChangeCentroid | null>
  right_rhythm_centroids: Array<MotionChangeCentroid | null>
  left_centroid_path: MotionCentroidPath | null
  right_centroid_path: MotionCentroidPath | null
  centroid_path_continuity: MotionCentroidContinuity | null
  left_rhythm_slope_percentage_points: number | null
  right_rhythm_slope_percentage_points: number | null
  right_minus_left_rhythm_slope_percentage_points: number | null
}

type BoundaryCandidateComparisonOutcome = 'completed' | 'kept_baseline' | 'shortlisted'
type BoundaryContinuityCheckOutcome = 'passed' | 'needs_adjustment'
type BoundaryContinuityObservations = Record<
  string,
  Partial<Record<BoundaryContinuityReviewMode, EditorContinuityObservation>>
>
type BoundaryContinuityReadyEvidence = Record<string, {
  'frames-left'?: true
  'frames-right'?: true
  overlay?: true
  'action-synchronous'?: true
  'action-sequence-realtime-context'?: EditorActionSequenceEvidence
}>

interface BoundaryContinuityIssueContext {
  checkId: string
  checkLabel: string
  mode: BoundaryContinuityReviewMode
}

interface EditorHistorySnapshot {
  items: TimelineItem[]
  boundaryContinuityOutcomes: Record<string, Record<string, BoundaryContinuityCheckOutcome>>
  boundaryContinuityIssueContexts: Record<string, BoundaryContinuityIssueContext[]>
}

interface BoundaryCandidateReviewSession {
  measuredMotionEvidence: Record<string, BoundaryMotionAnalysis>
  comparisonOutcomes: Record<string, BoundaryCandidateComparisonOutcome>
  alternativeOutcomes: Record<string, 'kept_baseline'>
}

const EMPTY_BOUNDARY_CANDIDATE_REVIEW_SESSION: BoundaryCandidateReviewSession = {
  measuredMotionEvidence: {},
  comparisonOutcomes: {},
  alternativeOutcomes: {},
}

function boundaryCandidateReviewSessionKey(
  projectId: string,
  left: TimelineItem,
  right: TimelineItem,
  frameStepMs: number,
  fps: number,
) {
  return [
    projectId,
    left.id,
    left.asset_id,
    left.source_in_ms ?? 0,
    left.source_out_ms ?? left.source_in_ms ?? 0,
    right.id,
    right.asset_id,
    right.source_in_ms ?? 0,
    right.source_out_ms ?? right.source_in_ms ?? 0,
    frameStepMs,
    fps,
  ].join(':')
}

interface MotionChangeCentroid {
  x_percent: number
  y_percent: number
  dispersion_percent: number
}

interface MotionCentroidPath {
  x_percentage_points: number
  y_percentage_points: number
  distance_percent: number
}

interface MotionCentroidContinuity {
  x_gap_percentage_points: number
  y_gap_percentage_points: number
  distance_gap_percentage_points: number
  angle_degrees: number | null
}

function MotionCentroidContinuityDiagram({
  leftCentroids,
  rightCentroids,
}: {
  leftCentroids: Array<MotionChangeCentroid | null>
  rightCentroids: Array<MotionChangeCentroid | null>
}) {
  const [leftStart, leftEnd] = leftCentroids
  const [rightStart, rightEnd] = rightCentroids
  if (!leftStart || !leftEnd || !rightStart || !rightEnd) return null
  const pointSummary = (point: MotionChangeCentroid) => `X ${point.x_percent.toFixed(1)}%、Y ${point.y_percent.toFixed(1)}%`
  return <div className={styles.boundaryMotionCentroidDiagram}>
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`变化重心接续图：前镜 ${pointSummary(leftStart)} 到 ${pointSummary(leftEnd)}，切点连接到后镜 ${pointSummary(rightStart)}，再到 ${pointSummary(rightEnd)}`}
    >
      <title>变化重心接续图；虚线是切点两侧变化区域重心的空间连接</title>
      <rect x="0.5" y="0.5" width="99" height="99" rx="3" />
      {[33.3, 66.7].map(value => <g key={value}>
        <line className={styles.boundaryMotionCentroidDiagramGrid} x1={value} y1="1" x2={value} y2="99" />
        <line className={styles.boundaryMotionCentroidDiagramGrid} x1="1" y1={value} x2="99" y2={value} />
      </g>)}
      <line className={styles.boundaryMotionCentroidDiagramCut} x1={leftEnd.x_percent} y1={leftEnd.y_percent} x2={rightStart.x_percent} y2={rightStart.y_percent} />
      <line className={styles.boundaryMotionCentroidDiagramLeft} x1={leftStart.x_percent} y1={leftStart.y_percent} x2={leftEnd.x_percent} y2={leftEnd.y_percent} />
      <circle className={styles.boundaryMotionCentroidDiagramLeftStart} cx={leftStart.x_percent} cy={leftStart.y_percent} r="2.5" />
      <circle className={styles.boundaryMotionCentroidDiagramLeftEnd} cx={leftEnd.x_percent} cy={leftEnd.y_percent} r="2.8" />
      <line className={styles.boundaryMotionCentroidDiagramRight} x1={rightStart.x_percent} y1={rightStart.y_percent} x2={rightEnd.x_percent} y2={rightEnd.y_percent} />
      <circle className={styles.boundaryMotionCentroidDiagramRightStart} cx={rightStart.x_percent} cy={rightStart.y_percent} r="2.5" />
      <circle className={styles.boundaryMotionCentroidDiagramRightEnd} cx={rightEnd.x_percent} cy={rightEnd.y_percent} r="2.8" />
    </svg>
    <small><i data-tone="left" />前镜路径 <i data-tone="cut" />切点连接 <i data-tone="right" />后镜路径</small>
    <small>空心为每侧第一步，实心为第二步；坐标按采样画面归一化。</small>
  </div>
}

const MOTION_GRID_REGIONS = ['左上', '上中', '右上', '左中', '中心', '右中', '左下', '下中', '右下']

function boundaryPixelDeltas(baseline: BoundaryPixelAnalysis, candidate: BoundaryPixelAnalysis) {
  return {
    luminance: Math.round((candidate.luminance_delta_percent - baseline.luminance_delta_percent) * 10) / 10,
    color: Math.round((candidate.color_delta_percent - baseline.color_delta_percent) * 10) / 10,
    pixel: Math.round((candidate.pixel_change_percent - baseline.pixel_change_percent) * 10) / 10,
  }
}

function boundaryMotionDeltas(baseline: BoundaryMotionAnalysis, candidate: BoundaryMotionAnalysis) {
  const delta = (candidateValue: number, baselineValue: number) => (
    Math.round((candidateValue - baselineValue) * 10) / 10
  )
  const nullableDelta = (candidateValue: number | null, baselineValue: number | null) => (
    candidateValue == null || baselineValue == null ? null : delta(candidateValue, baselineValue)
  )
  return {
    left_change: delta(candidate.left_change_percent, baseline.left_change_percent),
    right_change: delta(candidate.right_change_percent, baseline.right_change_percent),
    balance: delta(candidate.right_minus_left_percentage_points, baseline.right_minus_left_percentage_points),
    left_grid: candidate.left_grid_change_percent.map((value, index) => delta(value, baseline.left_grid_change_percent[index])),
    right_grid: candidate.right_grid_change_percent.map((value, index) => delta(value, baseline.right_grid_change_percent[index])),
    left_rhythm: candidate.left_rhythm_change_percent.map((value, index) => (
      value == null || baseline.left_rhythm_change_percent[index] == null
        ? null
        : delta(value, baseline.left_rhythm_change_percent[index] as number)
    )),
    right_rhythm: candidate.right_rhythm_change_percent.map((value, index) => (
      value == null || baseline.right_rhythm_change_percent[index] == null
        ? null
        : delta(value, baseline.right_rhythm_change_percent[index] as number)
    )),
    left_centroid_path: baseline.left_centroid_path && candidate.left_centroid_path ? {
      x: delta(candidate.left_centroid_path.x_percentage_points, baseline.left_centroid_path.x_percentage_points),
      y: delta(candidate.left_centroid_path.y_percentage_points, baseline.left_centroid_path.y_percentage_points),
      distance: delta(candidate.left_centroid_path.distance_percent, baseline.left_centroid_path.distance_percent),
    } : null,
    right_centroid_path: baseline.right_centroid_path && candidate.right_centroid_path ? {
      x: delta(candidate.right_centroid_path.x_percentage_points, baseline.right_centroid_path.x_percentage_points),
      y: delta(candidate.right_centroid_path.y_percentage_points, baseline.right_centroid_path.y_percentage_points),
      distance: delta(candidate.right_centroid_path.distance_percent, baseline.right_centroid_path.distance_percent),
    } : null,
    centroid_path_continuity: baseline.centroid_path_continuity && candidate.centroid_path_continuity ? {
      x: delta(candidate.centroid_path_continuity.x_gap_percentage_points, baseline.centroid_path_continuity.x_gap_percentage_points),
      y: delta(candidate.centroid_path_continuity.y_gap_percentage_points, baseline.centroid_path_continuity.y_gap_percentage_points),
      distance: delta(candidate.centroid_path_continuity.distance_gap_percentage_points, baseline.centroid_path_continuity.distance_gap_percentage_points),
      angle: nullableDelta(candidate.centroid_path_continuity.angle_degrees, baseline.centroid_path_continuity.angle_degrees),
    } : null,
    left_rhythm_slope: nullableDelta(candidate.left_rhythm_slope_percentage_points, baseline.left_rhythm_slope_percentage_points),
    right_rhythm_slope: nullableDelta(candidate.right_rhythm_slope_percentage_points, baseline.right_rhythm_slope_percentage_points),
    rhythm_slope_gap: nullableDelta(candidate.right_minus_left_rhythm_slope_percentage_points, baseline.right_minus_left_rhythm_slope_percentage_points),
    left_centroid: baseline.left_centroid && candidate.left_centroid ? {
      x: delta(candidate.left_centroid.x_percent, baseline.left_centroid.x_percent),
      y: delta(candidate.left_centroid.y_percent, baseline.left_centroid.y_percent),
      dispersion: delta(candidate.left_centroid.dispersion_percent, baseline.left_centroid.dispersion_percent),
    } : null,
    right_centroid: baseline.right_centroid && candidate.right_centroid ? {
      x: delta(candidate.right_centroid.x_percent, baseline.right_centroid.x_percent),
      y: delta(candidate.right_centroid.y_percent, baseline.right_centroid.y_percent),
      dispersion: delta(candidate.right_centroid.dispersion_percent, baseline.right_centroid.dispersion_percent),
    } : null,
  }
}

function signedPercentagePoint(value: number) {
  return value === 0
    ? '0.0 个百分点'
    : `${value > 0 ? '+' : '−'}${Math.abs(value).toFixed(1)} 个百分点`
}

function canonicalDraftValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalDraftValue)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, child]) => [key, canonicalDraftValue(child)]))
}

function editorDraftFingerprint(
  sourceTimeline: Timeline | null,
  items: TimelineItem[],
  playheadMs: number,
  snapEnabled: boolean,
  timelineZoom: number,
  boundaryContinuityOutcomes: Record<string, Record<string, BoundaryContinuityCheckOutcome>>,
  boundaryContinuityIssueContexts: Record<string, BoundaryContinuityIssueContext[]>,
  boundaryContinuityObservations: BoundaryContinuityObservations,
  boundaryCandidateReviewSessions: Record<string, BoundaryCandidateReviewSession>,
) {
  return JSON.stringify(canonicalDraftValue({
    base: sourceTimeline ? [sourceTimeline.id, sourceTimeline.row_version] : null,
    items: items.map(item => ({
      id: item.id,
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
    })),
    playhead_ms: Math.max(0, Math.round(playheadMs)),
    snap_enabled: snapEnabled,
    pixels_per_second: timelineZoom,
    boundary_continuity_outcomes: boundaryContinuityOutcomes,
    boundary_continuity_issue_contexts: boundaryContinuityIssueContexts,
    boundary_continuity_observations: boundaryContinuityObservations,
    boundary_candidate_review_sessions: boundaryCandidateReviewSessions,
  }))
}

function analyzeBoundaryPixels(leftVideo: HTMLVideoElement, rightVideo: HTMLVideoElement): BoundaryPixelAnalysis {
  if (!leftVideo.videoWidth || !leftVideo.videoHeight || !rightVideo.videoWidth || !rightVideo.videoHeight) {
    throw new Error('当前定格尚未具备可读取的视频画面。')
  }
  const size = 48
  const canvas = document.createElement('canvas')
  canvas.width = size * 2
  canvas.height = size
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('浏览器没有提供画面像素读取能力。')
  context.drawImage(leftVideo, 0, 0, size, size)
  context.drawImage(rightVideo, size, 0, size, size)
  const leftPixels = context.getImageData(0, 0, size, size).data
  const rightPixels = context.getImageData(size, 0, size, size).data
  let leftRed = 0
  let leftGreen = 0
  let leftBlue = 0
  let rightRed = 0
  let rightGreen = 0
  let rightBlue = 0
  let pixelDifference = 0
  const pixelCount = size * size
  for (let index = 0; index < leftPixels.length; index += 4) {
    leftRed += leftPixels[index]
    leftGreen += leftPixels[index + 1]
    leftBlue += leftPixels[index + 2]
    rightRed += rightPixels[index]
    rightGreen += rightPixels[index + 1]
    rightBlue += rightPixels[index + 2]
    pixelDifference += (
      Math.abs(leftPixels[index] - rightPixels[index])
      + Math.abs(leftPixels[index + 1] - rightPixels[index + 1])
      + Math.abs(leftPixels[index + 2] - rightPixels[index + 2])
    ) / 3
  }
  const leftAverage = [leftRed, leftGreen, leftBlue].map(value => value / pixelCount)
  const rightAverage = [rightRed, rightGreen, rightBlue].map(value => value / pixelCount)
  const leftLuminance = .2126 * leftAverage[0] + .7152 * leftAverage[1] + .0722 * leftAverage[2]
  const rightLuminance = .2126 * rightAverage[0] + .7152 * rightAverage[1] + .0722 * rightAverage[2]
  const luminanceDelta = Math.abs(leftLuminance - rightLuminance) / 255 * 100
  const colorDelta = Math.sqrt(
    (leftAverage[0] - rightAverage[0]) ** 2
    + (leftAverage[1] - rightAverage[1]) ** 2
    + (leftAverage[2] - rightAverage[2]) ** 2,
  ) / (Math.sqrt(3) * 255) * 100
  const pixelChange = pixelDifference / pixelCount / 255 * 100
  const level = luminanceDelta >= 20 || colorDelta >= 20 || pixelChange >= 55
    ? 'high'
    : luminanceDelta >= 8 || colorDelta >= 10 || pixelChange >= 25
    ? 'medium'
    : 'low'
  return {
    luminance_delta_percent: Math.round(luminanceDelta * 10) / 10,
    color_delta_percent: Math.round(colorDelta * 10) / 10,
    pixel_change_percent: Math.round(pixelChange * 10) / 10,
    level,
  }
}

function readFramePixels(video: HTMLVideoElement) {
  if (!video.videoWidth || !video.videoHeight) throw new Error('当前连续帧尚未具备可读取的视频画面。')
  const size = 48
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('浏览器没有提供画面像素读取能力。')
  context.drawImage(video, 0, 0, size, size)
  return context.getImageData(0, 0, size, size).data
}

function analyzeFrameMotion(firstVideo: HTMLVideoElement, secondVideo: HTMLVideoElement) {
  const firstPixels = readFramePixels(firstVideo)
  const secondPixels = readFramePixels(secondVideo)
  const size = 48
  const gridSize = 3
  const cellSize = size / gridSize
  const cellDifference = Array.from({ length: gridSize * gridSize }, () => 0)
  const pixelDifference = new Float32Array(size * size)
  let totalDifference = 0
  let weightedX = 0
  let weightedY = 0
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const index = (y * size + x) * 4
      const difference = (
        Math.abs(firstPixels[index] - secondPixels[index])
        + Math.abs(firstPixels[index + 1] - secondPixels[index + 1])
        + Math.abs(firstPixels[index + 2] - secondPixels[index + 2])
      ) / 3
      totalDifference += difference
      pixelDifference[y * size + x] = difference
      weightedX += difference * (x + .5)
      weightedY += difference * (y + .5)
      const gridIndex = Math.floor(y / cellSize) * gridSize + Math.floor(x / cellSize)
      cellDifference[gridIndex] += difference
    }
  }
  const percent = (difference: number, pixelCount: number) => Math.round(difference / pixelCount / 255 * 1000) / 10
  let centroid: MotionChangeCentroid | null = null
  if (totalDifference > 0) {
    const centerX = weightedX / totalDifference
    const centerY = weightedY / totalDifference
    let weightedDistance = 0
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        const difference = pixelDifference[y * size + x]
        weightedDistance += difference * Math.hypot(x + .5 - centerX, y + .5 - centerY)
      }
    }
    centroid = {
      x_percent: Math.round(centerX / size * 1000) / 10,
      y_percent: Math.round(centerY / size * 1000) / 10,
      dispersion_percent: Math.round(weightedDistance / totalDifference / (Math.sqrt(2) * size) * 1000) / 10,
    }
  }
  return {
    change_percent: percent(totalDifference, size * size),
    grid_change_percent: cellDifference.map(difference => percent(difference, cellSize * cellSize)),
    centroid,
  }
}

type ContinuityRelation = 'same_moment' | 'time_jump' | 'location_change' | 'outfit_change'
type BoundaryContinuityReviewMode = 'frames' | 'overlay' | 'action'

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

function continuityReviewModeForCheckId(checkId: string): BoundaryContinuityReviewMode {
  if (checkId === 'motion') return 'action'
  if (checkId === 'eyeline' || checkId === 'orientation') return 'overlay'
  return 'frames'
}

function continuityReviewModeLabel(mode: BoundaryContinuityReviewMode) {
  if (mode === 'action') return '同步动作与 1× 完整上下文切点'
  if (mode === 'overlay') return '叠加对齐'
  return '并排复核'
}

function mergeBoundaryContinuityIssueContexts(
  current: BoundaryContinuityIssueContext[],
  additions: BoundaryContinuityIssueContext[],
) {
  const merged = [...current]
  for (const addition of additions) {
    const index = merged.findIndex(context => context.checkId === addition.checkId)
    if (index >= 0) merged[index] = addition
    else merged.push(addition)
  }
  return merged
}

function continuityBoundaryFingerprint(left: TimelineItem, right: TimelineItem) {
  const itemFingerprint = (item: TimelineItem, side: 'left' | 'right') => {
    const transition = item.transform[side === 'left' ? 'transition_out' : 'transition_in'] as {
      type?: string
      duration_ms?: number
    } | undefined
    return [
      item.id,
      item.asset_id,
      item.source_in_ms,
      item.source_out_ms,
      item.timeline_in_ms,
      item.timeline_out_ms,
      item.transform.fit,
      transition?.type ?? 'cut',
      transition?.duration_ms ?? 0,
    ]
  }
  return JSON.stringify([
    itemFingerprint(left, 'left'),
    itemFingerprint(right, 'right'),
  ])
}

function boundarySequentialObservationEvidence(
  left: TimelineItem,
  right: TimelineItem,
  beforeMs: number,
  afterMs: number,
  playbackRate: number,
): EditorActionSequenceEvidence | null {
  if (playbackRate !== 1) return null
  const leftDurationMs = Math.max(0, (left.source_out_ms ?? 0) - (left.source_in_ms ?? 0))
  const rightDurationMs = Math.max(0, (right.source_out_ms ?? 0) - (right.source_in_ms ?? 0))
  const leftContextMs = Math.min(beforeMs, leftDurationMs)
  const rightContextMs = Math.min(afterMs, rightDurationMs)
  const requiredLeftContextMs = Math.min(1000, leftDurationMs)
  const requiredRightContextMs = Math.min(1000, rightDurationMs)
  if (leftContextMs < requiredLeftContextMs || rightContextMs < requiredRightContextMs) return null
  return {
    playback_rate: 1,
    left_context_ms: leftContextMs,
    right_context_ms: rightContextMs,
  }
}

function continuityBoundaryFingerprints(items: TimelineItem[]) {
  const mainItems = items.filter(item => item.track_type === 'main_video')
  return new Map(mainItems.slice(0, -1).map((left, index) => {
    const right = mainItems[index + 1]
    return [`${left.id}-${right.id}`, continuityBoundaryFingerprint(left, right)]
  }))
}

function changedContinuityBoundaryKeys(beforeItems: TimelineItem[], afterItems: TimelineItem[]) {
  const before = continuityBoundaryFingerprints(beforeItems)
  const after = continuityBoundaryFingerprints(afterItems)
  return new Set([...before.keys(), ...after.keys()].filter(key => before.get(key) !== after.get(key)))
}

function restoreBoundaryStateForKeys<T>(
  current: Record<string, T>,
  snapshot: Record<string, T>,
  keys: Set<string>,
) {
  if (keys.size === 0) return current
  const next = { ...current }
  for (const key of keys) {
    if (snapshot[key] !== undefined) next[key] = snapshot[key]
    else delete next[key]
  }
  return next
}

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

function boundaryFrameIsCurrent(video: HTMLVideoElement, sourceTimeMs: number) {
  if (!Number.isFinite(video.duration)) return false
  const expected = Math.min(Math.max(0, video.duration - .001), Math.max(0, sourceTimeMs / 1000))
  return Math.abs(video.currentTime - expected) <= .02
}

function BoundaryFrameStill({
  projectId,
  item,
  sourceTimeMs,
  label,
  fps,
  onActivate,
  observationKey,
  observationSide,
  onObserved,
}: {
  projectId: string
  item: TimelineItem
  sourceTimeMs: number
  label: string
  fps: number
  onActivate: () => void
  observationKey: string
  observationSide: 'frames-left' | 'frames-right'
  onObserved: (observationKey: string, evidence: 'frames-left' | 'frames-right') => void
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
      onSeeked={event => {
        if (!boundaryFrameIsCurrent(event.currentTarget, sourceTimeMs)) return
        setReady(true)
        onObserved(observationKey, observationSide)
      }}
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
  observationKey,
  onObserved,
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
  observationKey: string
  onObserved: (observationKey: string, evidence: 'overlay') => void
}) {
  const leftRef = useRef<HTMLVideoElement | null>(null)
  const rightRef = useRef<HTMLVideoElement | null>(null)
  const [leftReady, setLeftReady] = useState(false)
  const [rightReady, setRightReady] = useState(false)
  const [pixelAnalysis, setPixelAnalysis] = useState<BoundaryPixelAnalysis | null>(null)
  const [pixelAnalysisError, setPixelAnalysisError] = useState<string | null>(null)
  const seekFrame = useCallback((video: HTMLVideoElement | null, sourceTimeMs: number) => {
    if (!video || !Number.isFinite(video.duration)) return
    const latestTime = Math.max(0, video.duration - .001)
    video.currentTime = Math.min(latestTime, Math.max(0, sourceTimeMs / 1000))
  }, [])

  useEffect(() => {
    setLeftReady(false)
    setRightReady(false)
    setPixelAnalysis(null)
    setPixelAnalysisError(null)
    seekFrame(leftRef.current, leftSourceTimeMs)
    seekFrame(rightRef.current, rightSourceTimeMs)
  }, [leftSourceTimeMs, rightSourceTimeMs, seekFrame])

  useEffect(() => {
    if (!leftReady || !rightReady || !leftRef.current || !rightRef.current) return
    onObserved(observationKey, 'overlay')
    const leftVideo = leftRef.current
    const rightVideo = rightRef.current
    let cancelled = false
    const frame = window.requestAnimationFrame(() => {
      try {
        const result = analyzeBoundaryPixels(leftVideo, rightVideo)
        if (!cancelled) setPixelAnalysis(result)
      } catch (reason) {
        if (!cancelled) setPixelAnalysisError(reason instanceof Error ? reason.message : '画面像素读取失败。')
      }
    })
    return () => {
      cancelled = true
      window.cancelAnimationFrame(frame)
    }
  }, [leftReady, leftSourceTimeMs, observationKey, onObserved, rightReady, rightSourceTimeMs])

  const analysisLevelLabel = pixelAnalysis?.level === 'high'
    ? '变化较高'
    : pixelAnalysis?.level === 'medium' ? '变化中等' : '变化较低'

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
        onSeeked={event => {
          if (boundaryFrameIsCurrent(event.currentTarget, leftSourceTimeMs)) setLeftReady(true)
        }}
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
        onSeeked={event => {
          if (boundaryFrameIsCurrent(event.currentTarget, rightSourceTimeMs)) setRightReady(true)
        }}
      />
    </div>
    <div className={styles.boundaryOverlayFacts}>
      <button onClick={onLeftActivate}><strong>{leftLabel}</strong><code>{timecode(leftSourceTimeMs, fps)}</code></button>
      <span>叠加对齐</span>
      <button onClick={onRightActivate}><strong>{rightLabel}</strong><code>{timecode(rightSourceTimeMs, fps)}</code></button>
    </div>
    <div className={styles.boundaryPixelAnalysis} data-level={pixelAnalysis?.level ?? 'pending'} aria-live="polite">
      {pixelAnalysis ? <>
        <header><strong>切点像素跳变</strong><em>{analysisLevelLabel}</em></header>
        <div>
          <span><b>明暗</b><code>{pixelAnalysis.luminance_delta_percent}%</code></span>
          <span><b>综合色彩</b><code>{pixelAnalysis.color_delta_percent}%</code></span>
          <span><b>逐像素</b><code>{pixelAnalysis.pixel_change_percent}%</code></span>
        </div>
        <small>数值只比较当前末帧与首帧。较高变化提示检查闪跳、构图或曝光，不代表衔接一定有问题，也不是自动视觉结论。</small>
      </> : pixelAnalysisError
        ? <small>像素跳变暂不可用：{pixelAnalysisError}</small>
        : <small>正在读取当前末帧与首帧的本地像素证据…</small>}
    </div>
  </div>
}

function BoundaryPixelProbe({
  projectId,
  left,
  right,
  leftSourceTimeMs,
  rightSourceTimeMs,
  fps,
  label,
  note,
  onAnalysis,
}: {
  projectId: string
  left: TimelineItem
  right: TimelineItem
  leftSourceTimeMs: number
  rightSourceTimeMs: number
  fps: number
  label: string
  note: string
  onAnalysis: (analysis: BoundaryPixelAnalysis | null) => void
}) {
  const leftRef = useRef<HTMLVideoElement | null>(null)
  const rightRef = useRef<HTMLVideoElement | null>(null)
  const [leftReady, setLeftReady] = useState(false)
  const [rightReady, setRightReady] = useState(false)
  const [analysis, setAnalysis] = useState<BoundaryPixelAnalysis | null>(null)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const seekFrame = useCallback((video: HTMLVideoElement | null, sourceTimeMs: number) => {
    if (!video || !Number.isFinite(video.duration)) return
    video.currentTime = Math.min(Math.max(0, video.duration - .001), Math.max(0, sourceTimeMs / 1000))
  }, [])
  const frameIsCurrent = useCallback((video: HTMLVideoElement, sourceTimeMs: number) => {
    if (!Number.isFinite(video.duration)) return false
    const expected = Math.min(Math.max(0, video.duration - .001), Math.max(0, sourceTimeMs / 1000))
    return Math.abs(video.currentTime - expected) <= .02
  }, [])

  useEffect(() => {
    setLeftReady(false)
    setRightReady(false)
    setAnalysis(null)
    setAnalysisError(null)
    onAnalysis(null)
    seekFrame(leftRef.current, leftSourceTimeMs)
    seekFrame(rightRef.current, rightSourceTimeMs)
  }, [left.asset_id, leftSourceTimeMs, onAnalysis, right.asset_id, rightSourceTimeMs, seekFrame])

  useEffect(() => {
    if (!leftReady || !rightReady || !leftRef.current || !rightRef.current) return
    const leftVideo = leftRef.current
    const rightVideo = rightRef.current
    let cancelled = false
    const frame = window.requestAnimationFrame(() => {
      try {
        const result = analyzeBoundaryPixels(leftVideo, rightVideo)
        if (!cancelled) {
          setAnalysis(result)
          onAnalysis(result)
        }
      } catch (reason) {
        if (!cancelled) {
          setAnalysisError(reason instanceof Error ? reason.message : '画面像素读取失败。')
          onAnalysis(null)
        }
      }
    })
    return () => {
      cancelled = true
      window.cancelAnimationFrame(frame)
    }
  }, [left.asset_id, leftReady, leftSourceTimeMs, onAnalysis, right.asset_id, rightReady, rightSourceTimeMs])

  const levelLabel = analysis?.level === 'high'
    ? '变化较高'
    : analysis?.level === 'medium' ? '变化中等' : '变化较低'

  return <article className={styles.boundaryABPixelCard} data-level={analysis?.level ?? 'pending'}>
    <div className={styles.boundaryPixelProbeMedia} aria-hidden="true">
      <video
        ref={leftRef}
        muted
        playsInline
        preload="auto"
        src={`/api/v1/projects/${projectId}/assets/${left.asset_id}/content`}
        onLoadedMetadata={event => seekFrame(event.currentTarget, leftSourceTimeMs)}
        onSeeked={event => {
          if (frameIsCurrent(event.currentTarget, leftSourceTimeMs)) setLeftReady(true)
        }}
      />
      <video
        ref={rightRef}
        muted
        playsInline
        preload="auto"
        src={`/api/v1/projects/${projectId}/assets/${right.asset_id}/content`}
        onLoadedMetadata={event => seekFrame(event.currentTarget, rightSourceTimeMs)}
        onSeeked={event => {
          if (frameIsCurrent(event.currentTarget, rightSourceTimeMs)) setRightReady(true)
        }}
      />
    </div>
    <header><strong>{label}</strong><em>{analysis ? levelLabel : '读取中'}</em></header>
    <code>{timecode(leftSourceTimeMs, fps)} → {timecode(rightSourceTimeMs, fps)}</code>
    {analysis ? <div>
      <span><b>明暗</b><code>{analysis.luminance_delta_percent}%</code></span>
      <span><b>综合色彩</b><code>{analysis.color_delta_percent}%</code></span>
      <span><b>逐像素</b><code>{analysis.pixel_change_percent}%</code></span>
    </div> : analysisError
      ? <small>像素证据暂不可用：{analysisError}</small>
      : <small>正在读取末帧与首帧的本地像素…</small>}
    <small>{note}</small>
  </article>
}

function BoundaryMotionProbe({
  projectId,
  left,
  right,
  leftEarlierSourceTimeMs,
  leftPreviousSourceTimeMs,
  leftCurrentSourceTimeMs,
  rightCurrentSourceTimeMs,
  rightNextSourceTimeMs,
  rightLaterSourceTimeMs,
  fps,
  label,
  note,
  onAnalysis,
}: {
  projectId: string
  left: TimelineItem
  right: TimelineItem
  leftEarlierSourceTimeMs: number
  leftPreviousSourceTimeMs: number
  leftCurrentSourceTimeMs: number
  rightCurrentSourceTimeMs: number
  rightNextSourceTimeMs: number
  rightLaterSourceTimeMs: number
  fps: number
  label: string
  note: string
  onAnalysis: (analysis: BoundaryMotionAnalysis | null) => void
}) {
  const leftEarlierRef = useRef<HTMLVideoElement | null>(null)
  const leftPreviousRef = useRef<HTMLVideoElement | null>(null)
  const leftCurrentRef = useRef<HTMLVideoElement | null>(null)
  const rightCurrentRef = useRef<HTMLVideoElement | null>(null)
  const rightNextRef = useRef<HTMLVideoElement | null>(null)
  const rightLaterRef = useRef<HTMLVideoElement | null>(null)
  const [readyMask, setReadyMask] = useState(0)
  const [analysis, setAnalysis] = useState<BoundaryMotionAnalysis | null>(null)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const hasLeftPair = leftCurrentSourceTimeMs - leftPreviousSourceTimeMs >= 1
  const hasRightPair = rightNextSourceTimeMs - rightCurrentSourceTimeMs >= 1
  const hasLeftEarlierStep = leftPreviousSourceTimeMs - leftEarlierSourceTimeMs >= 1
  const hasRightLaterStep = rightLaterSourceTimeMs - rightNextSourceTimeMs >= 1
  const requiredReadyMask = 2 | 4 | 8 | 16 | (hasLeftEarlierStep ? 1 : 0) | (hasRightLaterStep ? 32 : 0)
  const seekFrame = useCallback((video: HTMLVideoElement | null, sourceTimeMs: number) => {
    if (!video || !Number.isFinite(video.duration)) return
    video.currentTime = Math.min(Math.max(0, video.duration - .001), Math.max(0, sourceTimeMs / 1000))
  }, [])
  const frameIsCurrent = useCallback((video: HTMLVideoElement, sourceTimeMs: number) => {
    if (!Number.isFinite(video.duration)) return false
    const expected = Math.min(Math.max(0, video.duration - .001), Math.max(0, sourceTimeMs / 1000))
    return Math.abs(video.currentTime - expected) <= .02
  }, [])
  const markReady = useCallback((bit: number, video: HTMLVideoElement, sourceTimeMs: number) => {
    if (frameIsCurrent(video, sourceTimeMs)) setReadyMask(current => current | bit)
  }, [frameIsCurrent])

  useEffect(() => {
    setReadyMask(0)
    setAnalysis(null)
    setAnalysisError(null)
    onAnalysis(null)
    if (hasLeftEarlierStep) seekFrame(leftEarlierRef.current, leftEarlierSourceTimeMs)
    seekFrame(leftPreviousRef.current, leftPreviousSourceTimeMs)
    seekFrame(leftCurrentRef.current, leftCurrentSourceTimeMs)
    seekFrame(rightCurrentRef.current, rightCurrentSourceTimeMs)
    seekFrame(rightNextRef.current, rightNextSourceTimeMs)
    if (hasRightLaterStep) seekFrame(rightLaterRef.current, rightLaterSourceTimeMs)
  }, [hasLeftEarlierStep, hasRightLaterStep, left.asset_id, leftCurrentSourceTimeMs, leftEarlierSourceTimeMs, leftPreviousSourceTimeMs, onAnalysis, right.asset_id, rightCurrentSourceTimeMs, rightLaterSourceTimeMs, rightNextSourceTimeMs, seekFrame])

  useEffect(() => {
    if (!hasLeftPair || !hasRightPair || readyMask !== requiredReadyMask) return
    const leftEarlierVideo = leftEarlierRef.current
    const leftPreviousVideo = leftPreviousRef.current
    const leftCurrentVideo = leftCurrentRef.current
    const rightCurrentVideo = rightCurrentRef.current
    const rightNextVideo = rightNextRef.current
    const rightLaterVideo = rightLaterRef.current
    if (!leftPreviousVideo || !leftCurrentVideo || !rightCurrentVideo || !rightNextVideo) return
    let cancelled = false
    const frame = window.requestAnimationFrame(() => {
      try {
        const leftMotion = analyzeFrameMotion(leftPreviousVideo, leftCurrentVideo)
        const rightMotion = analyzeFrameMotion(rightCurrentVideo, rightNextVideo)
        const leftEarlierMotion = hasLeftEarlierStep && leftEarlierVideo
          ? analyzeFrameMotion(leftEarlierVideo, leftPreviousVideo)
          : null
        const rightLaterMotion = hasRightLaterStep && rightLaterVideo
          ? analyzeFrameMotion(rightNextVideo, rightLaterVideo)
          : null
        if (!cancelled) {
          const leftRhythm = [leftEarlierMotion?.change_percent ?? null, leftMotion.change_percent]
          const rightRhythm = [rightMotion.change_percent, rightLaterMotion?.change_percent ?? null]
          const leftRhythmCentroids = [leftEarlierMotion?.centroid ?? null, leftMotion.centroid]
          const rightRhythmCentroids = [rightMotion.centroid, rightLaterMotion?.centroid ?? null]
          const rhythmSlope = (values: Array<number | null>) => values[0] == null || values[1] == null
            ? null
            : Math.round((values[1] - values[0]) * 10) / 10
          const centroidPath = (centroids: Array<MotionChangeCentroid | null>): MotionCentroidPath | null => {
            const [first, second] = centroids
            if (!first || !second) return null
            const x = second.x_percent - first.x_percent
            const y = second.y_percent - first.y_percent
            return {
              x_percentage_points: Math.round(x * 10) / 10,
              y_percentage_points: Math.round(y * 10) / 10,
              distance_percent: Math.round(Math.hypot(x, y) / Math.sqrt(2) * 10) / 10,
            }
          }
          const centroidPathContinuity = (leftPath: MotionCentroidPath | null, rightPath: MotionCentroidPath | null): MotionCentroidContinuity | null => {
            if (!leftPath || !rightPath) return null
            const leftMagnitude = Math.hypot(leftPath.x_percentage_points, leftPath.y_percentage_points)
            const rightMagnitude = Math.hypot(rightPath.x_percentage_points, rightPath.y_percentage_points)
            const angle = leftMagnitude === 0 || rightMagnitude === 0
              ? null
              : Math.acos(Math.max(-1, Math.min(1, (
                leftPath.x_percentage_points * rightPath.x_percentage_points
                + leftPath.y_percentage_points * rightPath.y_percentage_points
              ) / (leftMagnitude * rightMagnitude)))) * 180 / Math.PI
            return {
              x_gap_percentage_points: Math.round((rightPath.x_percentage_points - leftPath.x_percentage_points) * 10) / 10,
              y_gap_percentage_points: Math.round((rightPath.y_percentage_points - leftPath.y_percentage_points) * 10) / 10,
              distance_gap_percentage_points: Math.round((rightPath.distance_percent - leftPath.distance_percent) * 10) / 10,
              angle_degrees: angle == null ? null : Math.round(angle * 10) / 10,
            }
          }
          const leftRhythmSlope = rhythmSlope(leftRhythm)
          const rightRhythmSlope = rhythmSlope(rightRhythm)
          const leftCentroidPath = centroidPath(leftRhythmCentroids)
          const rightCentroidPath = centroidPath(rightRhythmCentroids)
          const nextAnalysis: BoundaryMotionAnalysis = {
            left_change_percent: leftMotion.change_percent,
            right_change_percent: rightMotion.change_percent,
            right_minus_left_percentage_points: Math.round((rightMotion.change_percent - leftMotion.change_percent) * 10) / 10,
            left_grid_change_percent: leftMotion.grid_change_percent,
            right_grid_change_percent: rightMotion.grid_change_percent,
            right_minus_left_grid_percentage_points: rightMotion.grid_change_percent.map((value, index) => (
              Math.round((value - leftMotion.grid_change_percent[index]) * 10) / 10
            )),
            left_centroid: leftMotion.centroid,
            right_centroid: rightMotion.centroid,
            left_rhythm_change_percent: leftRhythm,
            right_rhythm_change_percent: rightRhythm,
            left_rhythm_centroids: leftRhythmCentroids,
            right_rhythm_centroids: rightRhythmCentroids,
            left_centroid_path: leftCentroidPath,
            right_centroid_path: rightCentroidPath,
            centroid_path_continuity: centroidPathContinuity(leftCentroidPath, rightCentroidPath),
            left_rhythm_slope_percentage_points: leftRhythmSlope,
            right_rhythm_slope_percentage_points: rightRhythmSlope,
            right_minus_left_rhythm_slope_percentage_points: leftRhythmSlope == null || rightRhythmSlope == null
              ? null
              : Math.round((rightRhythmSlope - leftRhythmSlope) * 10) / 10,
          }
          setAnalysis(nextAnalysis)
          onAnalysis(nextAnalysis)
        }
      } catch (reason) {
        if (!cancelled) setAnalysisError(reason instanceof Error ? reason.message : '局部动作幅度读取失败。')
      }
    })
    return () => {
      cancelled = true
      window.cancelAnimationFrame(frame)
    }
  }, [hasLeftEarlierStep, hasLeftPair, hasRightLaterStep, hasRightPair, onAnalysis, readyMask, requiredReadyMask])

  const media = [
    ...(hasLeftEarlierStep ? [{ ref: leftEarlierRef, item: left, sourceTimeMs: leftEarlierSourceTimeMs, bit: 1 }] : []),
    { ref: leftPreviousRef, item: left, sourceTimeMs: leftPreviousSourceTimeMs, bit: 2 },
    { ref: leftCurrentRef, item: left, sourceTimeMs: leftCurrentSourceTimeMs, bit: 4 },
    { ref: rightCurrentRef, item: right, sourceTimeMs: rightCurrentSourceTimeMs, bit: 8 },
    { ref: rightNextRef, item: right, sourceTimeMs: rightNextSourceTimeMs, bit: 16 },
    ...(hasRightLaterStep ? [{ ref: rightLaterRef, item: right, sourceTimeMs: rightLaterSourceTimeMs, bit: 32 }] : []),
  ]
  const unavailableMessage = !hasLeftPair
    ? '前镜切点前不足两个可用帧。'
    : !hasRightPair ? '后镜切点后不足两个可用帧。' : null

  return <article className={styles.boundaryMotionCard}>
    <div className={styles.boundaryPixelProbeMedia} aria-hidden="true">
      {media.map(({ ref, item, sourceTimeMs, bit }) => <video
        key={`${item.asset_id}-${sourceTimeMs}-${bit}`}
        ref={ref}
        muted
        playsInline
        preload="auto"
        src={`/api/v1/projects/${projectId}/assets/${item.asset_id}/content`}
        onLoadedMetadata={event => seekFrame(event.currentTarget, sourceTimeMs)}
        onSeeked={event => markReady(bit, event.currentTarget, sourceTimeMs)}
      />)}
    </div>
    <header><strong>{label}</strong><code>{hasLeftEarlierStep ? `${timecode(leftEarlierSourceTimeMs, fps)}→` : ''}{timecode(leftPreviousSourceTimeMs, fps)}→{timecode(leftCurrentSourceTimeMs, fps)} · {timecode(rightCurrentSourceTimeMs, fps)}→{timecode(rightNextSourceTimeMs, fps)}{hasRightLaterStep ? `→${timecode(rightLaterSourceTimeMs, fps)}` : ''}</code></header>
    {analysis ? <>
      <div className={styles.boundaryMotionSummary}>
        <span><b>前镜末段</b><code>{analysis.left_change_percent}%</code></span>
        <span><b>后镜开端</b><code>{analysis.right_change_percent}%</code></span>
        <span><b>后镜 − 前镜</b><code>{signedPercentagePoint(analysis.right_minus_left_percentage_points)}</code></span>
      </div>
      <div className={styles.boundaryMotionRhythm} aria-label="动作节奏轨迹">
        <strong>动作节奏轨迹</strong>
        {[
          { label: '前镜', steps: ['较早→前一', '前一→切点'], values: analysis.left_rhythm_change_percent },
          { label: '后镜', steps: ['切点→后一', '后一→更后'], values: analysis.right_rhythm_change_percent },
        ].map(group => <section key={group.label}>
          <b>{group.label}</b>
          {group.values.map((value, index) => <span key={group.steps[index]}><small>{group.steps[index]}</small><code>{value == null ? '边缘无帧' : `${value.toFixed(1)}%`}</code></span>)}
        </section>)}
        <div className={styles.boundaryMotionRhythmSlope}>
          <span><b>前镜趋向切点</b><code>{analysis.left_rhythm_slope_percentage_points == null ? '不可用' : signedPercentagePoint(analysis.left_rhythm_slope_percentage_points).replace(' 个百分点', ' 点')}</code></span>
          <span><b>后镜离开切点</b><code>{analysis.right_rhythm_slope_percentage_points == null ? '不可用' : signedPercentagePoint(analysis.right_rhythm_slope_percentage_points).replace(' 个百分点', ' 点')}</code></span>
          <span><b>后镜 − 前镜斜率</b><code>{analysis.right_minus_left_rhythm_slope_percentage_points == null ? '不可用' : signedPercentagePoint(analysis.right_minus_left_rhythm_slope_percentage_points).replace(' 个百分点', ' 点')}</code></span>
        </div>
        <div className={styles.boundaryMotionCentroidPaths} aria-label="变化重心轨迹">
          {[
            { label: '前镜重心轨迹', path: analysis.left_centroid_path },
            { label: '后镜重心轨迹', path: analysis.right_centroid_path },
          ].map(({ label: pathLabel, path }) => <span key={pathLabel}>
            <b>{pathLabel}</b>
            {path
              ? <code>X {signedPercentagePoint(path.x_percentage_points).replace(' 个百分点', '')} · Y {signedPercentagePoint(path.y_percentage_points).replace(' 个百分点', '')} · 距离 {path.distance_percent.toFixed(1)}%</code>
              : <small>至少一步没有可定位变化。</small>}
          </span>)}
        </div>
        <div className={styles.boundaryMotionCentroidContinuity} aria-label="切点重心轨迹接续">
          <b>切点轨迹接续（后镜 − 前镜）</b>
          {analysis.centroid_path_continuity
            ? <span>
              <code>X {signedPercentagePoint(analysis.centroid_path_continuity.x_gap_percentage_points).replace(' 个百分点', '')}</code>
              <code>Y {signedPercentagePoint(analysis.centroid_path_continuity.y_gap_percentage_points).replace(' 个百分点', '')}</code>
              <code>距离 {signedPercentagePoint(analysis.centroid_path_continuity.distance_gap_percentage_points).replace(' 个百分点', '')}</code>
              <code>夹角 {analysis.centroid_path_continuity.angle_degrees == null ? '不可用' : `${analysis.centroid_path_continuity.angle_degrees.toFixed(1)}°`}</code>
            </span>
            : <small>前镜或后镜轨迹不可用，无法比较接续。</small>}
          <MotionCentroidContinuityDiagram
            leftCentroids={analysis.left_rhythm_centroids}
            rightCentroids={analysis.right_rhythm_centroids}
          />
        </div>
        <small>重心轨迹只描述两步像素变化区域的重心迁移，不代表主体运动方向、速度或光流。</small>
        <small>正值表示靠后的步长变化幅度更高，负值表示更低；只描述节奏斜率，不代表衔接优劣。</small>
        {(!hasLeftEarlierStep || !hasRightLaterStep) && <small>素材边缘不足三帧的一侧只保留真实可用步长，不用重复帧补零。</small>}
      </div>
      <div className={styles.boundaryMotionGrids}>
        <section aria-label="前镜末段局部动作分区">
          <header><strong>前镜末段分区</strong><small>X/Y 从画面左上起算</small></header>
          <div>
            {analysis.left_grid_change_percent.map((value, index) => <span
              key={MOTION_GRID_REGIONS[index]}
              style={{ backgroundColor: `rgba(178, 145, 75, ${Math.min(.62, .08 + value / 100)})` }}
            ><b>{MOTION_GRID_REGIONS[index]}</b><code>{value.toFixed(1)}%</code></span>)}
            {analysis.left_centroid && <i
              className={styles.boundaryMotionCentroidPoint}
              style={{ left: `${analysis.left_centroid.x_percent}%`, top: `${analysis.left_centroid.y_percent}%` }}
              aria-hidden="true"
            />}
          </div>
          {analysis.left_centroid
            ? <p><b>变化重心</b><code>X {analysis.left_centroid.x_percent.toFixed(1)}% · Y {analysis.left_centroid.y_percent.toFixed(1)}%</code><small>分散 {analysis.left_centroid.dispersion_percent.toFixed(1)}%</small></p>
            : <p><small>连续帧无可定位的像素变化。</small></p>}
        </section>
        <section aria-label="后镜开端局部动作分区">
          <header><strong>后镜开端分区</strong><small>括号为同格后镜 − 前镜</small></header>
          <div>
            {analysis.right_grid_change_percent.map((value, index) => <span
              key={MOTION_GRID_REGIONS[index]}
              style={{ backgroundColor: `rgba(104, 151, 139, ${Math.min(.62, .08 + value / 100)})` }}
            ><b>{MOTION_GRID_REGIONS[index]}</b><code>{value.toFixed(1)}%</code><small>{signedPercentagePoint(analysis.right_minus_left_grid_percentage_points[index]).replace(' 个百分点', '')}</small></span>)}
            {analysis.right_centroid && <i
              className={styles.boundaryMotionCentroidPoint}
              style={{ left: `${analysis.right_centroid.x_percent}%`, top: `${analysis.right_centroid.y_percent}%` }}
              aria-hidden="true"
            />}
          </div>
          {analysis.right_centroid
            ? <p><b>变化重心</b><code>X {analysis.right_centroid.x_percent.toFixed(1)}% · Y {analysis.right_centroid.y_percent.toFixed(1)}%</code><small>分散 {analysis.right_centroid.dispersion_percent.toFixed(1)}%</small></p>
            : <p><small>连续帧无可定位的像素变化。</small></p>}
        </section>
      </div>
      {analysis.left_centroid && analysis.right_centroid && <div className={styles.boundaryMotionCentroidDelta}>
        <b>重心坐标差（后镜 − 前镜）</b>
        <code>X {signedPercentagePoint(Math.round((analysis.right_centroid.x_percent - analysis.left_centroid.x_percent) * 10) / 10).replace(' 个百分点', '')} · Y {signedPercentagePoint(Math.round((analysis.right_centroid.y_percent - analysis.left_centroid.y_percent) * 10) / 10).replace(' 个百分点', '')}</code>
        <small>分散 {signedPercentagePoint(Math.round((analysis.right_centroid.dispersion_percent - analysis.left_centroid.dispersion_percent) * 10) / 10)}</small>
      </div>}
    </> : unavailableMessage
      ? <small>动作幅度证据暂不可用：{unavailableMessage}</small>
      : analysisError
      ? <small>动作幅度证据暂不可用：{analysisError}</small>
      : <small>正在读取切点两侧最多各三个连续帧的本地像素变化…</small>}
    <small>{note}</small>
  </article>
}

function BoundaryPhaseCandidate({
  elementId,
  projectId,
  left,
  right,
  leftSourceTimeMs,
  rightSourceTimeMs,
  fps,
  label,
  baselineAnalysis,
  baselineMotionAnalysis,
  measuredMotionAnalysis,
  comparisonOutcome,
  selected,
  comparePending,
  onSelect,
  onCompare,
}: {
  elementId: string
  projectId: string
  left: TimelineItem
  right: TimelineItem
  leftSourceTimeMs: number
  rightSourceTimeMs: number
  fps: number
  label: string
  baselineAnalysis: BoundaryPixelAnalysis | null
  baselineMotionAnalysis: BoundaryMotionAnalysis | null
  measuredMotionAnalysis: BoundaryMotionAnalysis | null
  comparisonOutcome: 'completed' | 'kept_baseline' | 'shortlisted' | null
  selected: boolean
  comparePending: boolean
  onSelect: () => void
  onCompare: () => void
}) {
  const [analysis, setAnalysis] = useState<BoundaryPixelAnalysis | null>(null)
  const deltas = baselineAnalysis && analysis ? boundaryPixelDeltas(baselineAnalysis, analysis) : null
  const motionDeltas = baselineMotionAnalysis && measuredMotionAnalysis
    ? boundaryMotionDeltas(baselineMotionAnalysis, measuredMotionAnalysis)
    : null
  return <div id={elementId} tabIndex={-1} className={styles.boundaryPhaseCandidate} data-selected={selected}>
    <BoundaryPixelProbe
      projectId={projectId}
      left={left}
      right={right}
      leftSourceTimeMs={leftSourceTimeMs}
      rightSourceTimeMs={rightSourceTimeMs}
      fps={fps}
      label={label}
      note="相对 A 的单侧邻帧候选；尚未写入草稿。"
      onAnalysis={setAnalysis}
    />
    <div>
      {deltas ? <span aria-label={`${label} 相对 A 的精确差值`}>
        <code>明暗 {signedPercentagePoint(deltas.luminance)}</code>
        <code>色彩 {signedPercentagePoint(deltas.color)}</code>
        <code>像素 {signedPercentagePoint(deltas.pixel)}</code>
      </span> : <small>等待 A 与候选帧证据…</small>}
      {comparisonOutcome && <span
        className={styles.boundaryPhaseCandidateCompared}
        data-outcome={comparisonOutcome}
        aria-label={comparisonOutcome === 'kept_baseline'
          ? `${label} 本次对照选择保留 A`
          : comparisonOutcome === 'shortlisted' ? `${label} 已暂存 B 待复看` : `${label} 已完整对照 A 到 B`}
      >
        <strong>{comparisonOutcome === 'kept_baseline'
          ? '本次已选择保留 A'
          : comparisonOutcome === 'shortlisted' ? '已暂存 B 待复看' : '已完整对照 A→B'}</strong>
        <small>{comparisonOutcome === 'kept_baseline'
          ? '人工结果已保存到项目草稿；可再次对照。'
          : comparisonOutcome === 'shortlisted'
            ? '人工短名单已保存到项目草稿；可再次对照。'
            : '已看完，尚未选择保留或采用。'}</small>
      </span>}
      {measuredMotionAnalysis ? <section className={styles.boundaryPhaseCandidateMeasured} aria-label={`${label} 已实测动作证据`}>
        <strong>已实测动作</strong>
        <span>
          <code>前 {measuredMotionAnalysis.left_change_percent.toFixed(1)}%</code>
          <code>后 {measuredMotionAnalysis.right_change_percent.toFixed(1)}%</code>
          <code>后−前 {signedPercentagePoint(measuredMotionAnalysis.right_minus_left_percentage_points).replace(' 个百分点', ' 点')}</code>
        </span>
        {measuredMotionAnalysis.centroid_path_continuity
          ? <span>
            <code>接续 X {signedPercentagePoint(measuredMotionAnalysis.centroid_path_continuity.x_gap_percentage_points).replace(' 个百分点', '')}</code>
            <code>Y {signedPercentagePoint(measuredMotionAnalysis.centroid_path_continuity.y_gap_percentage_points).replace(' 个百分点', '')}</code>
            <code>距 {signedPercentagePoint(measuredMotionAnalysis.centroid_path_continuity.distance_gap_percentage_points).replace(' 个百分点', '')}</code>
            <code>角 {measuredMotionAnalysis.centroid_path_continuity.angle_degrees == null ? '不可用' : `${measuredMotionAnalysis.centroid_path_continuity.angle_degrees.toFixed(1)}°`}</code>
          </span>
          : <small>已实测；前镜或后镜轨迹不可用。</small>}
        {motionDeltas ? <>
          <strong>相对 A</strong>
          <span aria-label={`${label} 动作幅度相对 A 的精确影响`}>
            <code>前 {signedPercentagePoint(motionDeltas.left_change).replace(' 个百分点', '点')}</code>
            <code>后 {signedPercentagePoint(motionDeltas.right_change).replace(' 个百分点', '点')}</code>
            <code>差 {signedPercentagePoint(motionDeltas.balance).replace(' 个百分点', '点')}</code>
          </span>
          {motionDeltas.centroid_path_continuity
            ? <span aria-label={`${label} 接续几何相对 A 的精确影响`}>
              <code>X {signedPercentagePoint(motionDeltas.centroid_path_continuity.x).replace(' 个百分点', '')}</code>
              <code>Y {signedPercentagePoint(motionDeltas.centroid_path_continuity.y).replace(' 个百分点', '')}</code>
              <code>距 {signedPercentagePoint(motionDeltas.centroid_path_continuity.distance).replace(' 个百分点', '')}</code>
              <code>角 {motionDeltas.centroid_path_continuity.angle == null ? '不可比' : `${motionDeltas.centroid_path_continuity.angle > 0 ? '+' : motionDeltas.centroid_path_continuity.angle < 0 ? '−' : ''}${Math.abs(motionDeltas.centroid_path_continuity.angle).toFixed(1)}°`}</code>
            </span>
            : <small>接续几何与 A 不可比。</small>}
        </> : <small>等待 A 动作证据后显示精确影响。</small>}
      </section> : <small>设为 B 并等待读取后，会在此保留本次页面会话的动作证据。</small>}
      <div className={styles.boundaryPhaseCandidateActions}>
        <button aria-pressed={selected} onClick={onSelect}>{selected ? '当前 B' : '设为单侧 B'}</button>
        <button aria-busy={comparePending} onClick={onCompare}>{comparePending ? '等待证据…' : '设为 B 并对照'}</button>
      </div>
    </div>
  </div>
}

function BoundaryRollTrimMonitor({
  projectId,
  left,
  right,
  leftSourceTimeMs,
  rightSourceTimeMs,
  deltaMs,
  fps,
}: {
  projectId: string
  left: TimelineItem
  right: TimelineItem
  leftSourceTimeMs: number
  rightSourceTimeMs: number
  deltaMs: number
  fps: number
}) {
  const leftRef = useRef<HTMLVideoElement | null>(null)
  const rightRef = useRef<HTMLVideoElement | null>(null)
  const [leftReady, setLeftReady] = useState(false)
  const [rightReady, setRightReady] = useState(false)
  const seekFrame = useCallback((video: HTMLVideoElement | null, sourceTimeMs: number) => {
    if (!video || !Number.isFinite(video.duration)) return
    video.currentTime = Math.min(Math.max(0, video.duration - .001), Math.max(0, sourceTimeMs / 1000))
  }, [])

  useEffect(() => {
    setLeftReady(false)
    setRightReady(false)
    seekFrame(leftRef.current, leftSourceTimeMs)
    seekFrame(rightRef.current, rightSourceTimeMs)
  }, [leftSourceTimeMs, rightSourceTimeMs, seekFrame])

  const deltaLabel = deltaMs === 0
    ? '原切点'
    : `${deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(deltaMs), fps)}`
  return <section
    className={styles.boundaryRollTrimMonitor}
    data-ready={leftReady && rightReady}
    aria-label={`${left.label} 到 ${right.label} 的滚动剪辑双画面监看，${deltaLabel}`}
  >
    <header><strong>滚动剪辑监看</strong><code>{deltaLabel}</code><span>Esc 取消 · 松开并试听</span></header>
    <div>
      <figure data-ready={leftReady}>
        <video
          ref={leftRef}
          aria-label={`${left.label} 前镜末帧`}
          muted
          playsInline
          preload="auto"
          src={`/api/v1/projects/${projectId}/assets/${left.asset_id}/content`}
          onLoadedMetadata={event => seekFrame(event.currentTarget, leftSourceTimeMs)}
          onSeeked={() => setLeftReady(true)}
        />
        <figcaption><strong>{left.label}</strong><span>前镜末帧</span><code>{timecode(leftSourceTimeMs, fps)}</code></figcaption>
      </figure>
      <i aria-hidden="true" />
      <figure data-ready={rightReady}>
        <video
          ref={rightRef}
          aria-label={`${right.label} 后镜首帧`}
          muted
          playsInline
          preload="auto"
          src={`/api/v1/projects/${projectId}/assets/${right.asset_id}/content`}
          onLoadedMetadata={event => seekFrame(event.currentTarget, rightSourceTimeMs)}
          onSeeked={() => setRightReady(true)}
        />
        <figcaption><strong>{right.label}</strong><span>后镜首帧</span><code>{timecode(rightSourceTimeMs, fps)}</code></figcaption>
      </figure>
    </div>
  </section>
}

function BoundaryActionComparison({
  projectId,
  left,
  right,
  beforeMs,
  afterMs,
  rate,
  frameStepMs,
  fps,
  editLocked,
  rollMinimumDeltaMs,
  rollMaximumDeltaMs,
  onBeforePlay,
  onApplyRoll,
  onApplyTransition,
  onApplyLeftPhase,
  onApplyRightPhase,
  onApplyPhasePair,
  candidateReviewSessionKey,
  candidateReviewSession,
  guidedScanRequest,
  onConsumeGuidedScanRequest,
  onRememberCandidateMotionEvidence,
  onRememberCandidateComparisonOutcome,
  onRememberAlternativeOutcome,
  onReplaceLeftAsset,
  onReplaceRightAsset,
  formalOrderSwapAvailable,
  onSwapToFormalOrder,
  onAdjustStructure,
  onNotice,
  observationKey,
  onObserved,
}: {
  projectId: string
  left: TimelineItem
  right: TimelineItem
  beforeMs: number
  afterMs: number
  rate: number
  frameStepMs: number
  fps: number
  editLocked: boolean
  rollMinimumDeltaMs: number
  rollMaximumDeltaMs: number
  onBeforePlay: () => void
  onApplyRoll: (deltaMs: number) => void
  onApplyTransition: (type: 'cut' | 'fade', durationMs: number) => void
  onApplyLeftPhase: (deltaMs: number) => void
  onApplyRightPhase: (deltaMs: number) => void
  onApplyPhasePair: (leftDeltaMs: number, rightDeltaMs: number) => void
  candidateReviewSessionKey: string
  candidateReviewSession: BoundaryCandidateReviewSession
  guidedScanRequest: { requestToken: number; issueLabel: string; intent: 'issue' | 'resume' } | null
  onConsumeGuidedScanRequest: (requestToken: number) => void
  onRememberCandidateMotionEvidence: (sessionKey: string, sourceKey: string, analysis: BoundaryMotionAnalysis) => void
  onRememberCandidateComparisonOutcome: (sessionKey: string, sourceKey: string, outcome: BoundaryCandidateComparisonOutcome) => void
  onRememberAlternativeOutcome: (sessionKey: string, alternativeKey: string, outcome: 'kept_baseline') => void
  onReplaceLeftAsset: () => void
  onReplaceRightAsset: () => void
  formalOrderSwapAvailable: boolean
  onSwapToFormalOrder: () => void
  onAdjustStructure: () => void
  onNotice: (message: string) => void
  observationKey: string
  onObserved: (
    observationKey: string,
    evidence: 'action-synchronous' | 'action-sequence-realtime-context',
    actionSequenceEvidence?: EditorActionSequenceEvidence,
  ) => void
}) {
  const leftRef = useRef<HTMLVideoElement | null>(null)
  const rightRef = useRef<HTMLVideoElement | null>(null)
  const animationRef = useRef<number | null>(null)
  const sequenceLeftRef = useRef<HTMLVideoElement | null>(null)
  const sequenceRightRef = useRef<HTMLVideoElement | null>(null)
  const sequenceAnimationRef = useRef<number | null>(null)
  const sequenceSideRef = useRef<'left' | 'right'>('left')
  const phaseSequenceCompareStageRef = useRef<'idle' | 'baseline' | 'tuned'>('idle')
  const phaseSequenceStartTokenRef = useRef(0)
  const [playing, setComparisonPlaying] = useState(false)
  const [progressMs, setProgressMs] = useState(0)
  const [sequencePlaying, setSequencePlaying] = useState(false)
  const [sequenceSide, setSequenceSide] = useState<'left' | 'right'>('left')
  const [sequenceProgressMs, setSequenceProgressMs] = useState(0)
  const [sequenceLeftEvidenceKey, setSequenceLeftEvidenceKey] = useState<string | null>(null)
  const [sequenceRightEvidenceKey, setSequenceRightEvidenceKey] = useState<string | null>(null)
  const [phaseSequenceCompareStage, setPhaseSequenceCompareStage] = useState<'idle' | 'baseline' | 'tuned'>('idle')
  const [phaseDecisionSourceKey, setPhaseDecisionSourceKey] = useState<string | null>(null)
  const [rollTrialDeltaMs, setRollTrialDeltaMs] = useState(0)
  const [transitionTrial, setTransitionTrial] = useState<{ type: 'cut' | 'fade'; durationMs: number } | null>(null)
  const [leftPhaseDeltaMs, setLeftPhaseDeltaMs] = useState(0)
  const [rightPhaseDeltaMs, setRightPhaseDeltaMs] = useState(0)
  const [phaseView, setPhaseView] = useState<'baseline' | 'tuned'>('baseline')
  const [baselinePixelEvidence, setBaselinePixelEvidence] = useState<{ sourceKey: string; analysis: BoundaryPixelAnalysis } | null>(null)
  const [tunedPixelEvidence, setTunedPixelEvidence] = useState<{ sourceKey: string; analysis: BoundaryPixelAnalysis } | null>(null)
  const [baselineMotionEvidence, setBaselineMotionEvidence] = useState<{ sourceKey: string; analysis: BoundaryMotionAnalysis } | null>(null)
  const [tunedMotionEvidence, setTunedMotionEvidence] = useState<{ sourceKey: string; analysis: BoundaryMotionAnalysis } | null>(null)
  const measuredCandidateMotionEvidence = candidateReviewSession.measuredMotionEvidence
  const candidateComparisonOutcomes = candidateReviewSession.comparisonOutcomes
  const alternativeOutcomes = candidateReviewSession.alternativeOutcomes
  const [phaseCandidateScanSide, setPhaseCandidateScanSide] = useState<'left' | 'right' | null>(null)
  const [phaseCandidateScanExpanded, setPhaseCandidateScanExpanded] = useState(false)
  const [guidedIssueLabel, setGuidedIssueLabel] = useState<string | null>(null)
  const [alternativeReviewTrialKey, setAlternativeReviewTrialKey] = useState<string | null>(null)
  const [pendingPhaseCandidateCompare, setPendingPhaseCandidateCompare] = useState<{ side: 'left' | 'right'; deltaMs: number } | null>(null)
  const [evidenceDetailsOpen, setEvidenceDetailsOpen] = useState(false)
  const [candidateDetailsOpen, setCandidateDetailsOpen] = useState(false)
  const [advancedTrialsOpen, setAdvancedTrialsOpen] = useState(false)
  const leftBaseSourceInMs = left.source_in_ms ?? 0
  const leftBaseSourceOutMs = left.source_out_ms ?? leftBaseSourceInMs
  const rightBaseSourceInMs = right.source_in_ms ?? 0
  const rightBaseSourceOutMs = right.source_out_ms ?? rightBaseSourceInMs
  const leftMinimumPhaseMs = -leftBaseSourceInMs
  const leftMaximumPhaseMs = Math.max(0, (left.asset_duration_ms ?? leftBaseSourceOutMs) - leftBaseSourceOutMs)
  const rightMinimumPhaseMs = -rightBaseSourceInMs
  const rightMaximumPhaseMs = Math.max(0, (right.asset_duration_ms ?? rightBaseSourceOutMs) - rightBaseSourceOutMs)
  const hasPhaseTrial = leftPhaseDeltaMs !== 0 || rightPhaseDeltaMs !== 0
  const hasMotionTrial = hasPhaseTrial || rollTrialDeltaMs !== 0
  const hasComparisonTrial = hasMotionTrial || transitionTrial != null
  const viewingTunedPhase = phaseView === 'tuned' && hasComparisonTrial
  const leftActivePhaseDeltaMs = viewingTunedPhase ? leftPhaseDeltaMs : 0
  const rightActivePhaseDeltaMs = viewingTunedPhase ? rightPhaseDeltaMs : 0
  const activeRollTrialDeltaMs = viewingTunedPhase ? rollTrialDeltaMs : 0
  const leftSourceInMs = leftBaseSourceInMs + leftActivePhaseDeltaMs
  const leftSourceOutMs = leftBaseSourceOutMs + leftActivePhaseDeltaMs + activeRollTrialDeltaMs
  const rightSourceInMs = rightBaseSourceInMs + rightActivePhaseDeltaMs + activeRollTrialDeltaMs
  const rightSourceOutMs = rightBaseSourceOutMs + rightActivePhaseDeltaMs
  const leftEndMs = Math.max(leftSourceInMs, leftSourceOutMs - frameStepMs)
  const rightMaximumEndMs = Math.max(rightSourceInMs, rightSourceOutMs - frameStepMs)
  const baselinePixelLeftMs = Math.max(leftBaseSourceInMs, leftBaseSourceOutMs - frameStepMs)
  const baselinePixelRightMs = rightBaseSourceInMs
  const tunedPixelLeftMs = Math.max(
    leftBaseSourceInMs + leftPhaseDeltaMs,
    leftBaseSourceOutMs + leftPhaseDeltaMs + rollTrialDeltaMs - frameStepMs,
  )
  const tunedPixelRightMs = rightBaseSourceInMs + rightPhaseDeltaMs + rollTrialDeltaMs
  const baselineMotionLeftPreviousMs = Math.max(leftBaseSourceInMs, baselinePixelLeftMs - frameStepMs)
  const baselineMotionLeftEarlierMs = Math.max(leftBaseSourceInMs, baselineMotionLeftPreviousMs - frameStepMs)
  const baselineMotionRightNextMs = Math.min(
    Math.max(rightBaseSourceInMs, rightBaseSourceOutMs - frameStepMs),
    baselinePixelRightMs + frameStepMs,
  )
  const baselineMotionRightMaximumMs = Math.max(rightBaseSourceInMs, rightBaseSourceOutMs - frameStepMs)
  const baselineMotionRightLaterMs = Math.min(baselineMotionRightMaximumMs, baselineMotionRightNextMs + frameStepMs)
  const tunedMotionLeftMinimumMs = leftBaseSourceInMs + leftPhaseDeltaMs
  const tunedMotionLeftPreviousMs = Math.max(tunedMotionLeftMinimumMs, tunedPixelLeftMs - frameStepMs)
  const tunedMotionLeftEarlierMs = Math.max(tunedMotionLeftMinimumMs, tunedMotionLeftPreviousMs - frameStepMs)
  const tunedMotionRightMaximumMs = Math.max(
    rightBaseSourceInMs + rightPhaseDeltaMs + rollTrialDeltaMs,
    rightBaseSourceOutMs + rightPhaseDeltaMs - frameStepMs,
  )
  const tunedMotionRightNextMs = Math.min(tunedMotionRightMaximumMs, tunedPixelRightMs + frameStepMs)
  const tunedMotionRightLaterMs = Math.min(tunedMotionRightMaximumMs, tunedMotionRightNextMs + frameStepMs)
  const candidateMotionSourceKey = useCallback((side: 'left' | 'right', deltaMs: number) => {
    const candidateLeftMinimumMs = leftBaseSourceInMs + (side === 'left' ? deltaMs : 0)
    const candidateLeftCurrentMs = Math.max(
      candidateLeftMinimumMs,
      leftBaseSourceOutMs + (side === 'left' ? deltaMs : 0) - frameStepMs,
    )
    const candidateLeftPreviousMs = Math.max(candidateLeftMinimumMs, candidateLeftCurrentMs - frameStepMs)
    const candidateLeftEarlierMs = Math.max(candidateLeftMinimumMs, candidateLeftPreviousMs - frameStepMs)
    const candidateRightCurrentMs = rightBaseSourceInMs + (side === 'right' ? deltaMs : 0)
    const candidateRightMaximumMs = Math.max(
      candidateRightCurrentMs,
      rightBaseSourceOutMs + (side === 'right' ? deltaMs : 0) - frameStepMs,
    )
    const candidateRightNextMs = Math.min(candidateRightMaximumMs, candidateRightCurrentMs + frameStepMs)
    const candidateRightLaterMs = Math.min(candidateRightMaximumMs, candidateRightNextMs + frameStepMs)
    return [
      left.id,
      left.asset_id,
      leftBaseSourceInMs,
      leftBaseSourceOutMs,
      right.id,
      right.asset_id,
      rightBaseSourceInMs,
      rightBaseSourceOutMs,
      side,
      deltaMs,
      candidateLeftEarlierMs,
      candidateLeftPreviousMs,
      candidateLeftCurrentMs,
      candidateRightCurrentMs,
      candidateRightNextMs,
      candidateRightLaterMs,
    ].join(':')
  }, [frameStepMs, left.asset_id, left.id, leftBaseSourceInMs, leftBaseSourceOutMs, right.asset_id, right.id, rightBaseSourceInMs, rightBaseSourceOutMs])
  const defaultPhaseCandidateFrameOffsets = [-2, -1, 1, 2]
  const allPhaseCandidateFrameOffsets = [-4, -3, -2, -1, 1, 2, 3, 4]
  const phaseCandidateFrameOffsets = phaseCandidateScanExpanded
    ? allPhaseCandidateFrameOffsets
    : defaultPhaseCandidateFrameOffsets
  const activePhaseCandidateSide = rollTrialDeltaMs === 0 && !transitionTrial
    ? leftPhaseDeltaMs !== 0 && rightPhaseDeltaMs === 0
      ? 'left'
      : rightPhaseDeltaMs !== 0 && leftPhaseDeltaMs === 0 ? 'right' : null
    : null
  const activePhaseCandidateDeltaMs = activePhaseCandidateSide === 'left' ? leftPhaseDeltaMs : rightPhaseDeltaMs
  const activePhaseCandidateSourceKey = activePhaseCandidateSide
    && phaseCandidateFrameOffsets.some(offset => offset * frameStepMs === activePhaseCandidateDeltaMs)
    ? candidateMotionSourceKey(activePhaseCandidateSide, activePhaseCandidateDeltaMs)
    : null
  const baselineMotionSourceKey = `${left.asset_id}:${baselineMotionLeftEarlierMs}:${baselineMotionLeftPreviousMs}:${baselinePixelLeftMs}:${right.asset_id}:${baselinePixelRightMs}:${baselineMotionRightNextMs}:${baselineMotionRightLaterMs}`
  const tunedMotionSourceKey = `${left.asset_id}:${tunedMotionLeftEarlierMs}:${tunedMotionLeftPreviousMs}:${tunedPixelLeftMs}:${right.asset_id}:${tunedPixelRightMs}:${tunedMotionRightNextMs}:${tunedMotionRightLaterMs}`
  const baselineMotionAnalysis = baselineMotionEvidence?.sourceKey === baselineMotionSourceKey ? baselineMotionEvidence.analysis : null
  const tunedMotionAnalysis = tunedMotionEvidence?.sourceKey === tunedMotionSourceKey ? tunedMotionEvidence.analysis : null
  const handleBaselineMotionAnalysis = useCallback((analysis: BoundaryMotionAnalysis | null) => {
    setBaselineMotionEvidence(analysis ? { sourceKey: baselineMotionSourceKey, analysis } : null)
  }, [baselineMotionSourceKey])
  const handleTunedMotionAnalysis = useCallback((analysis: BoundaryMotionAnalysis | null) => {
    setTunedMotionEvidence(analysis ? { sourceKey: tunedMotionSourceKey, analysis } : null)
    if (!analysis || !activePhaseCandidateSourceKey) return
    onRememberCandidateMotionEvidence(candidateReviewSessionKey, activePhaseCandidateSourceKey, analysis)
  }, [activePhaseCandidateSourceKey, candidateReviewSessionKey, onRememberCandidateMotionEvidence, tunedMotionSourceKey])
  const baselinePixelSourceKey = `${left.asset_id}:${baselinePixelLeftMs}:${right.asset_id}:${baselinePixelRightMs}`
  const baselinePixelAnalysis = baselinePixelEvidence?.sourceKey === baselinePixelSourceKey ? baselinePixelEvidence.analysis : null
  const handleBaselinePixelAnalysis = useCallback((analysis: BoundaryPixelAnalysis | null) => {
    setBaselinePixelEvidence(analysis ? { sourceKey: baselinePixelSourceKey, analysis } : null)
  }, [baselinePixelSourceKey])
  const tunedPixelSourceKey = `${left.asset_id}:${tunedPixelLeftMs}:${right.asset_id}:${tunedPixelRightMs}`
  const tunedPixelAnalysis = tunedPixelEvidence?.sourceKey === tunedPixelSourceKey ? tunedPixelEvidence.analysis : null
  const handleTunedPixelAnalysis = useCallback((analysis: BoundaryPixelAnalysis | null) => {
    setTunedPixelEvidence(analysis ? { sourceKey: tunedPixelSourceKey, analysis } : null)
  }, [tunedPixelSourceKey])
  const legalPhaseCandidatesForSide = (side: 'left' | 'right', frameOffsets: number[]) => frameOffsets
    .map(frameOffset => ({ frameOffset, deltaMs: frameOffset * frameStepMs }))
    .filter(candidate => side === 'left'
      ? candidate.deltaMs >= leftMinimumPhaseMs && candidate.deltaMs <= leftMaximumPhaseMs
      : candidate.deltaMs >= rightMinimumPhaseMs && candidate.deltaMs <= rightMaximumPhaseMs)
  const leftDefaultPhaseCandidates = legalPhaseCandidatesForSide('left', defaultPhaseCandidateFrameOffsets)
  const rightDefaultPhaseCandidates = legalPhaseCandidatesForSide('right', defaultPhaseCandidateFrameOffsets)
  const leftAllPhaseCandidates = legalPhaseCandidatesForSide('left', allPhaseCandidateFrameOffsets)
  const rightAllPhaseCandidates = legalPhaseCandidatesForSide('right', allPhaseCandidateFrameOffsets)
  const allLegalPhaseCandidates = [
    ...leftAllPhaseCandidates.map(candidate => ({ ...candidate, side: 'left' as const })),
    ...rightAllPhaseCandidates.map(candidate => ({ ...candidate, side: 'right' as const })),
  ]
  const phaseCandidateReviewExhausted = allLegalPhaseCandidates.length > 0
    && allLegalPhaseCandidates.every(candidate => (
      candidateComparisonOutcomes[candidateMotionSourceKey(candidate.side, candidate.deltaMs)] === 'kept_baseline'
    ))
  const pendingDefaultPhaseCandidateCount = (side: 'left' | 'right', candidates: Array<{ frameOffset: number; deltaMs: number }>) => candidates
    .filter(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(side, candidate.deltaMs)] !== 'kept_baseline')
    .length
  const leftPendingDefaultPhaseCandidateCount = pendingDefaultPhaseCandidateCount('left', leftDefaultPhaseCandidates)
  const rightPendingDefaultPhaseCandidateCount = pendingDefaultPhaseCandidateCount('right', rightDefaultPhaseCandidates)
  const allNearbyPhaseCandidates = phaseCandidateScanSide
    ? legalPhaseCandidatesForSide(phaseCandidateScanSide, allPhaseCandidateFrameOffsets)
    : []
  const nearbyPhaseCandidates = allNearbyPhaseCandidates.filter(candidate => phaseCandidateScanExpanded || Math.abs(candidate.frameOffset) <= 2)
  const expandablePhaseCandidates = allNearbyPhaseCandidates.filter(candidate => Math.abs(candidate.frameOffset) > 2)
  const expandablePhaseCandidateCount = expandablePhaseCandidates.length
  const reviewedPhaseCandidateCount = phaseCandidateScanSide
    ? nearbyPhaseCandidates.filter(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)]).length
    : 0
  const keptBaselinePhaseCandidateCount = phaseCandidateScanSide
    ? nearbyPhaseCandidates.filter(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] === 'kept_baseline').length
    : 0
  const undecidedPhaseCandidateCount = phaseCandidateScanSide
    ? nearbyPhaseCandidates.filter(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] === 'completed').length
    : 0
  const shortlistedPhaseCandidateCount = phaseCandidateScanSide
    ? nearbyPhaseCandidates.filter(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] === 'shortlisted').length
    : 0
  const pendingPhaseCandidateForSide = (
    side: 'left' | 'right',
    candidates: Array<{ frameOffset: number; deltaMs: number }>,
    excludedSourceKey?: string | null,
  ) => {
    const eligibleCandidates = candidates.filter(candidate => {
      const sourceKey = candidateMotionSourceKey(side, candidate.deltaMs)
      return sourceKey !== excludedSourceKey && candidateComparisonOutcomes[sourceKey] !== 'kept_baseline'
    })
    return eligibleCandidates.find(candidate => !candidateComparisonOutcomes[candidateMotionSourceKey(side, candidate.deltaMs)])
      ?? eligibleCandidates.find(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(side, candidate.deltaMs)] === 'completed')
      ?? eligibleCandidates.find(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(side, candidate.deltaMs)] === 'shortlisted')
      ?? null
  }
  const nextUnreviewedPhaseCandidate = phaseCandidateScanSide
    ? nearbyPhaseCandidates.find(candidate => !candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)]) ?? null
    : null
  const nextUndecidedPhaseCandidate = phaseCandidateScanSide
    ? nearbyPhaseCandidates.find(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] === 'completed') ?? null
    : null
  const nextShortlistedPhaseCandidate = phaseCandidateScanSide
    ? nearbyPhaseCandidates.find(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] === 'shortlisted') ?? null
    : null
  const nextReviewPhaseCandidate = nextUnreviewedPhaseCandidate ?? nextUndecidedPhaseCandidate ?? nextShortlistedPhaseCandidate
  const nextExpandablePhaseCandidate = phaseCandidateScanSide
    ? pendingPhaseCandidateForSide(phaseCandidateScanSide, expandablePhaseCandidates)
    : null
  const pendingExpandablePhaseCandidateCount = phaseCandidateScanSide
    ? expandablePhaseCandidates.filter(candidate => candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] !== 'kept_baseline').length
    : 0
  const otherGuidedScanSide = phaseCandidateScanSide === 'left' ? 'right' : 'left'
  const otherGuidedScanCandidates = otherGuidedScanSide === 'left' ? leftDefaultPhaseCandidates : rightDefaultPhaseCandidates
  const otherGuidedScanCandidateCount = phaseCandidateScanSide === 'left'
    ? rightPendingDefaultPhaseCandidateCount
    : leftPendingDefaultPhaseCandidateCount
  const activePhaseCandidateIsExpanded = Math.abs(activePhaseCandidateDeltaMs) > 2 * frameStepMs
  const sameSideContinuationCandidates = phaseCandidateScanExpanded && activePhaseCandidateIsExpanded
    ? expandablePhaseCandidates
    : nearbyPhaseCandidates
  const nextPhaseCandidateAfterDecision = phaseCandidateScanSide && activePhaseCandidateSourceKey
    ? pendingPhaseCandidateForSide(phaseCandidateScanSide, sameSideContinuationCandidates, activePhaseCandidateSourceKey)
    : null
  const nextOtherGuidedPhaseCandidateAfterDecision = guidedIssueLabel && activePhaseCandidateSourceKey && !activePhaseCandidateIsExpanded
    ? pendingPhaseCandidateForSide(otherGuidedScanSide, otherGuidedScanCandidates)
    : null
  const candidateDecisionWillContinue = Boolean(
    activePhaseCandidateSourceKey
    && (nextPhaseCandidateAfterDecision || nextOtherGuidedPhaseCandidateAfterDecision),
  )
  const phaseCandidateElementId = (side: 'left' | 'right', frameOffset: number) => `phase-candidate-${left.id}-${right.id}-${side}-${frameOffset}`
  const isPhaseCandidateSelected = (side: 'left' | 'right', deltaMs: number) => side === 'left'
    ? leftPhaseDeltaMs === deltaMs && rightPhaseDeltaMs === 0
    : rightPhaseDeltaMs === deltaMs && leftPhaseDeltaMs === 0
  const phaseCandidateStatusLabel = (side: 'left' | 'right', deltaMs: number) => {
    if (isPhaseCandidateSelected(side, deltaMs)) return '当前 B'
    const sourceKey = candidateMotionSourceKey(side, deltaMs)
    const outcome = candidateComparisonOutcomes[sourceKey]
    if (outcome === 'kept_baseline') return '保留 A'
    if (outcome === 'shortlisted') return '待复看'
    if (outcome === 'completed') return '待决定'
    return measuredCandidateMotionEvidence[sourceKey] ? '已实测' : '未看'
  }
  const focusPhaseCandidate = (side: 'left' | 'right', frameOffset: number) => {
    const candidateElement = document.getElementById(phaseCandidateElementId(side, frameOffset))
    candidateElement?.focus({ preventScroll: true })
    candidateElement?.scrollIntoView({ block: 'nearest' })
  }

  const comparisonDurationMs = Math.max(0, Math.min(
    beforeMs,
    afterMs,
    leftEndMs - leftSourceInMs,
    rightMaximumEndMs - rightSourceInMs,
  ))
  const leftStartMs = leftEndMs - comparisonDurationMs
  const rightStartMs = rightSourceInMs
  const rightEndMs = rightStartMs + comparisonDurationMs
  const sequenceLeftStartMs = Math.max(leftSourceInMs, leftSourceOutMs - beforeMs)
  const sequenceLeftEndMs = leftSourceOutMs
  const sequenceRightStartMs = rightSourceInMs
  const sequenceRightEndMs = Math.min(rightSourceOutMs, rightSourceInMs + afterMs)
  const sequenceLeftDurationMs = Math.max(0, sequenceLeftEndMs - sequenceLeftStartMs)
  const sequenceRightDurationMs = Math.max(0, sequenceRightEndMs - sequenceRightStartMs)
  const requiredSequenceLeftContextMs = Math.min(1000, Math.max(0, leftSourceOutMs - leftSourceInMs))
  const requiredSequenceRightContextMs = Math.min(1000, Math.max(0, rightSourceOutMs - rightSourceInMs))
  const sequenceContextComplete = sequenceLeftDurationMs >= requiredSequenceLeftContextMs
    && sequenceRightDurationMs >= requiredSequenceRightContextMs
  const sequenceDurationMs = sequenceLeftDurationMs + sequenceRightDurationMs
  const sequenceLeftSourceKey = `${left.asset_id}:${sequenceLeftStartMs}:${sequenceLeftEndMs}`
  const sequenceRightSourceKey = `${right.asset_id}:${sequenceRightStartMs}:${sequenceRightEndMs}`
  const sequenceLeftReady = sequenceLeftEvidenceKey === sequenceLeftSourceKey
  const sequenceRightReady = sequenceRightEvidenceKey === sequenceRightSourceKey
  const phaseTrialSourceKey = [
    left.id,
    left.asset_id,
    right.id,
    right.asset_id,
    leftBaseSourceInMs,
    leftBaseSourceOutMs,
    rightBaseSourceInMs,
    rightBaseSourceOutMs,
    leftPhaseDeltaMs,
    rightPhaseDeltaMs,
    rollTrialDeltaMs,
    transitionTrial?.type ?? 'none',
    transitionTrial?.durationMs ?? 0,
  ].join(':')
  const comparisonEvidenceReady = Boolean(
    hasComparisonTrial
    && baselinePixelAnalysis
    && tunedPixelAnalysis
    && baselineMotionAnalysis
    && tunedMotionAnalysis,
  )
  const phaseDecisionReady = phaseDecisionSourceKey === phaseTrialSourceKey && comparisonEvidenceReady
  const sequenceVisible = sequencePlaying || sequenceProgressMs > 0 || phaseSequenceCompareStage !== 'idle'
  const baselineTransitionOut = left.transform.transition_out as { type?: string; duration_ms?: number } | undefined
  const baselineTransitionIn = right.transform.transition_in as { type?: string; duration_ms?: number } | undefined
  const baselinePairedFade = baselineTransitionOut?.type === 'fade'
    && baselineTransitionIn?.type === 'fade'
    && baselineTransitionOut.duration_ms === baselineTransitionIn.duration_ms
  const baselinePairedCut = (baselineTransitionOut?.type ?? 'cut') === 'cut'
    && (baselineTransitionIn?.type ?? 'cut') === 'cut'
  const baselineTransitionLabel = baselinePairedFade
    ? `淡出淡入 ${seconds(baselineTransitionOut?.duration_ms ?? 0)}`
    : baselinePairedCut ? '直接切换' : '两侧设置不一致'
  const transitionMaximumDurationMs = Math.min(
    2000,
    Math.floor((left.timeline_out_ms - left.timeline_in_ms) / 2),
    Math.floor((right.timeline_out_ms - right.timeline_in_ms) / 2),
  )
  const exhaustedCandidateTransitionTrial = baselinePairedCut && transitionMaximumDurationMs >= 100
    ? { type: 'fade' as const, durationMs: Math.min(200, transitionMaximumDurationMs), label: `试用淡出淡入 ${seconds(Math.min(200, transitionMaximumDurationMs))}` }
    : !baselinePairedCut
      ? { type: 'cut' as const, durationMs: 0, label: '试用直接切换' }
      : null
  const exhaustedCandidateAlternatives = [
    ...(rollMinimumDeltaMs <= -frameStepMs ? [{ key: `roll:${-frameStepMs}`, label: '滚动切点 −1帧' }] : []),
    ...(rollMaximumDeltaMs >= frameStepMs ? [{ key: `roll:${frameStepMs}`, label: '滚动切点 +1帧' }] : []),
    ...(exhaustedCandidateTransitionTrial ? [{
      key: `transition:${exhaustedCandidateTransitionTrial.type}:${exhaustedCandidateTransitionTrial.durationMs}`,
      label: exhaustedCandidateTransitionTrial.label,
    }] : []),
  ]
  const pendingExhaustedCandidateAlternatives = exhaustedCandidateAlternatives.filter(alternative => !alternativeOutcomes[alternative.key])
  const activeTransitionTrial = viewingTunedPhase ? transitionTrial : null
  const activeLeftFadeMs = activeTransitionTrial
    ? activeTransitionTrial.type === 'fade' ? activeTransitionTrial.durationMs : 0
    : baselineTransitionOut?.type === 'fade' ? baselineTransitionOut.duration_ms ?? 0 : 0
  const activeRightFadeMs = activeTransitionTrial
    ? activeTransitionTrial.type === 'fade' ? activeTransitionTrial.durationMs : 0
    : baselineTransitionIn?.type === 'fade' ? baselineTransitionIn.duration_ms ?? 0 : 0
  const [sequenceLeftOpacity, setSequenceLeftOpacity] = useState(1)
  const [sequenceRightOpacity, setSequenceRightOpacity] = useState(1)

  useEffect(() => {
    if (!hasComparisonTrial) setTunedPixelEvidence(null)
  }, [hasComparisonTrial])

  const positionMedia = useCallback(() => {
    if (leftRef.current && Number.isFinite(leftRef.current.duration)) leftRef.current.currentTime = leftStartMs / 1000
    if (rightRef.current && Number.isFinite(rightRef.current.duration)) rightRef.current.currentTime = rightStartMs / 1000
    setProgressMs(0)
  }, [leftStartMs, rightStartMs])

  const pauseMedia = useCallback(() => {
    leftRef.current?.pause()
    rightRef.current?.pause()
    setComparisonPlaying(false)
  }, [])

  const positionSequenceMedia = useCallback(() => {
    const leftVideo = sequenceLeftRef.current
    const rightVideo = sequenceRightRef.current
    if (leftVideo && Number.isFinite(leftVideo.duration)) leftVideo.currentTime = sequenceLeftStartMs / 1000
    if (rightVideo && Number.isFinite(rightVideo.duration)) rightVideo.currentTime = sequenceRightStartMs / 1000
    sequenceSideRef.current = 'left'
    setSequenceSide('left')
    setSequenceProgressMs(0)
    setSequenceLeftOpacity(activeLeftFadeMs > 0
      ? Math.max(0, Math.min(1, (sequenceLeftEndMs - sequenceLeftStartMs) / activeLeftFadeMs))
      : 1)
    setSequenceRightOpacity(activeRightFadeMs > 0 ? 0 : 1)
  }, [activeLeftFadeMs, activeRightFadeMs, sequenceLeftEndMs, sequenceLeftStartMs, sequenceRightStartMs])

  const pauseSequenceMedia = useCallback(() => {
    sequenceLeftRef.current?.pause()
    sequenceRightRef.current?.pause()
    setSequencePlaying(false)
  }, [])

  const cancelPhaseSequenceComparison = useCallback(() => {
    phaseSequenceStartTokenRef.current += 1
    phaseSequenceCompareStageRef.current = 'idle'
    setPhaseSequenceCompareStage('idle')
    setPendingPhaseCandidateCompare(null)
    pauseSequenceMedia()
  }, [pauseSequenceMedia])

  useEffect(() => {
    pauseMedia()
    pauseSequenceMedia()
    positionMedia()
    positionSequenceMedia()
  }, [left.id, leftStartMs, pauseMedia, pauseSequenceMedia, positionMedia, positionSequenceMedia, right.id, rightStartMs])

  useEffect(() => {
    setRollTrialDeltaMs(0)
    setTransitionTrial(null)
    setLeftPhaseDeltaMs(0)
    setRightPhaseDeltaMs(0)
    setPhaseCandidateScanSide(null)
    setPhaseView('baseline')
    setPhaseDecisionSourceKey(null)
    cancelPhaseSequenceComparison()
  }, [
    baselineTransitionIn?.duration_ms,
    baselineTransitionIn?.type,
    baselineTransitionOut?.duration_ms,
    baselineTransitionOut?.type,
    cancelPhaseSequenceComparison,
    left.id,
    leftBaseSourceInMs,
    leftBaseSourceOutMs,
    right.id,
    rightBaseSourceInMs,
    rightBaseSourceOutMs,
  ])

  useEffect(() => {
    if (!guidedScanRequest) return
    setCandidateDetailsOpen(true)
    if (guidedScanRequest.intent === 'resume') {
      const resumableCandidate = (side: 'left' | 'right') => legalPhaseCandidatesForSide(side, allPhaseCandidateFrameOffsets)
        .find(candidate => {
          const sourceKey = candidateMotionSourceKey(side, candidate.deltaMs)
          const outcome = candidateComparisonOutcomes[sourceKey]
          return outcome === 'completed'
            || outcome === 'shortlisted'
            || (!outcome && Boolean(measuredCandidateMotionEvidence[sourceKey]))
        })
      const leftCandidate = resumableCandidate('left')
      const rightCandidate = resumableCandidate('right')
      const preferredSide = leftCandidate ? 'left' : rightCandidate ? 'right' : null
      const preferredCandidate = leftCandidate ?? rightCandidate
      setGuidedIssueLabel(null)
      setPhaseCandidateScanSide(preferredSide)
      setPhaseCandidateScanExpanded(Boolean(preferredCandidate && Math.abs(preferredCandidate.frameOffset) > 2))
      onNotice(preferredSide && preferredCandidate
        ? `已打开${preferredSide === 'left' ? '前镜' : '后镜'} ${preferredCandidate.frameOffset < 0 ? '−' : '+'}${Math.abs(preferredCandidate.frameOffset)} 帧待办并定位候选卡；未设置 B、未播放，也未作出结论。`
        : `当前切点没有仍与画面合同匹配的候选审核待办。`)
      if (preferredSide && preferredCandidate) {
        requestAnimationFrame(() => focusPhaseCandidate(preferredSide, preferredCandidate.frameOffset))
      }
      onConsumeGuidedScanRequest(guidedScanRequest.requestToken)
      return
    }
    const preferredSide = leftPendingDefaultPhaseCandidateCount > 0
      ? 'left'
      : rightPendingDefaultPhaseCandidateCount > 0
        ? 'right'
        : leftDefaultPhaseCandidates.length > 0
          ? 'left'
          : rightDefaultPhaseCandidates.length > 0 ? 'right' : null
    const skippedCompletedSide = preferredSide === 'right'
      && leftDefaultPhaseCandidates.length > 0
      && leftPendingDefaultPhaseCandidateCount === 0
    setGuidedIssueLabel(guidedScanRequest.issueLabel)
    setPhaseCandidateScanSide(preferredSide)
    setPhaseCandidateScanExpanded(false)
    onNotice(preferredSide
      ? `正在处理“${guidedScanRequest.issueLabel}”；${skippedCompletedSide ? '前镜默认候选均已保留 A，已继续打开' : '已自动打开'}${preferredSide === 'left' ? '前镜' : '后镜'} ±2 帧内的${preferredSide === 'left' ? leftPendingDefaultPhaseCandidateCount : rightPendingDefaultPhaseCandidateCount} 个待处理候选，不排序、不推荐。`
      : `正在处理“${guidedScanRequest.issueLabel}”；当前切点两侧在 ±2 帧内都没有合法源窗口候选。`)
    onConsumeGuidedScanRequest(guidedScanRequest.requestToken)
  }, [
    guidedScanRequest,
    candidateComparisonOutcomes,
    candidateMotionSourceKey,
    leftDefaultPhaseCandidates.length,
    leftPendingDefaultPhaseCandidateCount,
    measuredCandidateMotionEvidence,
    onConsumeGuidedScanRequest,
    onNotice,
    rightDefaultPhaseCandidates.length,
    rightPendingDefaultPhaseCandidateCount,
  ])

  useEffect(() => {
    setSequenceLeftEvidenceKey(null)
    setSequenceRightEvidenceKey(null)
  }, [left.asset_id, right.asset_id])

  useEffect(() => {
    if (leftRef.current) leftRef.current.playbackRate = rate
    if (rightRef.current) rightRef.current.playbackRate = rate
    if (sequenceLeftRef.current) sequenceLeftRef.current.playbackRate = rate
    if (sequenceRightRef.current) sequenceRightRef.current.playbackRate = rate
  }, [rate])

  useEffect(() => {
    if (phaseSequenceCompareStage === 'idle') return
    if (!comparisonEvidenceReady || !sequenceLeftReady || !sequenceRightReady) return
    const expectedView = phaseSequenceCompareStage === 'baseline' ? 'baseline' : 'tuned'
    if (phaseView !== expectedView || (expectedView === 'tuned' && !hasComparisonTrial)) return
    const token = phaseSequenceStartTokenRef.current + 1
    phaseSequenceStartTokenRef.current = token
    pauseMedia()
    positionMedia()
    positionSequenceMedia()
    const leftVideo = sequenceLeftRef.current
    const rightVideo = sequenceRightRef.current
    if (!leftVideo || !rightVideo) return
    leftVideo.playbackRate = rate
    rightVideo.playbackRate = rate
    setSequencePlaying(true)
    void leftVideo.play().catch(reason => {
      if (token !== phaseSequenceStartTokenRef.current || isMediaPlaybackInterruption(reason)) return
      cancelPhaseSequenceComparison()
      onNotice('浏览器没有允许 A→B 连续对照试播，请再点击一次。')
    })
    return () => {
      if (phaseSequenceStartTokenRef.current === token) phaseSequenceStartTokenRef.current += 1
    }
  }, [cancelPhaseSequenceComparison, comparisonEvidenceReady, hasComparisonTrial, onNotice, pauseMedia, phaseSequenceCompareStage, phaseView, positionMedia, positionSequenceMedia, rate, sequenceLeftReady, sequenceRightReady])

  useEffect(() => {
    if (phaseSequenceCompareStage === 'idle' || comparisonEvidenceReady) return
    setPhaseDecisionSourceKey(null)
    cancelPhaseSequenceComparison()
    onNotice('当前 A/B 像素或动作证据已失效；已停止本轮对照，请等待当前证据完成后重试。')
  }, [cancelPhaseSequenceComparison, comparisonEvidenceReady, onNotice, phaseSequenceCompareStage])

  useEffect(() => {
    if (!playing) return
    const tick = () => {
      const leftVideo = leftRef.current
      const rightVideo = rightRef.current
      if (!leftVideo || !rightVideo) return
      const leftProgress = Math.max(0, leftVideo.currentTime * 1000 - leftStartMs)
      const rightProgress = Math.max(0, rightVideo.currentTime * 1000 - rightStartMs)
      const nextProgress = Math.min(comparisonDurationMs, Math.max(leftProgress, rightProgress))
      setProgressMs(nextProgress)
      const leftDone = leftVideo.currentTime * 1000 >= leftEndMs - 4
      const rightDone = rightVideo.currentTime * 1000 >= rightEndMs - 4
      if (leftDone) {
        leftVideo.pause()
        leftVideo.currentTime = leftEndMs / 1000
      }
      if (rightDone) {
        rightVideo.pause()
        rightVideo.currentTime = rightEndMs / 1000
      }
      if (leftDone && rightDone) {
        setProgressMs(comparisonDurationMs)
        setComparisonPlaying(false)
        if (!viewingTunedPhase) onObserved(observationKey, 'action-synchronous')
        onNotice(`${left.label} 与 ${right.label} 的${viewingTunedPhase ? '当前试调' : '原切点'}同步动作对比已完成；可重播或继续 A/B 对照。`)
        return
      }
      animationRef.current = window.requestAnimationFrame(tick)
    }
    animationRef.current = window.requestAnimationFrame(tick)
    return () => {
      if (animationRef.current != null) window.cancelAnimationFrame(animationRef.current)
      animationRef.current = null
    }
  }, [comparisonDurationMs, left.label, leftEndMs, leftStartMs, observationKey, onNotice, onObserved, playing, right.label, rightEndMs, rightStartMs, viewingTunedPhase])

  useEffect(() => {
    if (!sequencePlaying) return
    const tick = () => {
      const leftVideo = sequenceLeftRef.current
      const rightVideo = sequenceRightRef.current
      if (!leftVideo || !rightVideo) return
      if (sequenceSideRef.current === 'left') {
        const leftProgressMs = Math.max(0, leftVideo.currentTime * 1000 - sequenceLeftStartMs)
        setSequenceProgressMs(Math.min(sequenceLeftDurationMs, leftProgressMs))
        setSequenceLeftOpacity(activeLeftFadeMs > 0
          ? Math.max(0, Math.min(1, (sequenceLeftEndMs - leftVideo.currentTime * 1000) / activeLeftFadeMs))
          : 1)
        if (leftVideo.currentTime * 1000 >= sequenceLeftEndMs - 4 || leftVideo.ended) {
          leftVideo.pause()
          leftVideo.currentTime = sequenceLeftEndMs / 1000
          setSequenceLeftOpacity(activeLeftFadeMs > 0 ? 0 : 1)
          rightVideo.currentTime = sequenceRightStartMs / 1000
          rightVideo.playbackRate = rate
          setSequenceRightOpacity(activeRightFadeMs > 0 ? 0 : 1)
          sequenceSideRef.current = 'right'
          setSequenceSide('right')
          void rightVideo.play().catch(reason => {
            if (isMediaPlaybackInterruption(reason)) return
            pauseSequenceMedia()
            onNotice('浏览器没有允许当前相位顺序试播，请再点击一次。')
          })
        }
      } else {
        const rightProgressMs = Math.max(0, rightVideo.currentTime * 1000 - sequenceRightStartMs)
        setSequenceProgressMs(Math.min(sequenceDurationMs, sequenceLeftDurationMs + rightProgressMs))
        setSequenceRightOpacity(activeRightFadeMs > 0
          ? Math.max(0, Math.min(1, rightProgressMs / activeRightFadeMs))
          : 1)
        if (rightVideo.currentTime * 1000 >= sequenceRightEndMs - 4 || rightVideo.ended) {
          rightVideo.pause()
          rightVideo.currentTime = sequenceRightEndMs / 1000
          setSequenceProgressMs(sequenceDurationMs)
          setSequencePlaying(false)
          if (phaseSequenceCompareStageRef.current === 'baseline') {
            phaseSequenceCompareStageRef.current = 'tuned'
            setPhaseSequenceCompareStage('tuned')
            setPhaseView('tuned')
            onNotice('A 原切点已播放完成，正在自动切换到 B 当前试调。')
            return
          }
          if (phaseSequenceCompareStageRef.current === 'tuned') {
            phaseSequenceCompareStageRef.current = 'idle'
            setPhaseSequenceCompareStage('idle')
            if (!comparisonEvidenceReady) {
              cancelPhaseSequenceComparison()
              onNotice('当前 A/B 动作证据已失效；请等待证据重新完成后再对照。')
              return
            }
            setPhaseDecisionSourceKey(phaseTrialSourceKey)
            if (activePhaseCandidateSourceKey) {
              onRememberCandidateComparisonOutcome(candidateReviewSessionKey, activePhaseCandidateSourceKey, 'completed')
            }
            onNotice(`${left.label} → ${right.label} 的 A→B 连续对照已完成；请选择保留 A 或采用 B。`)
            return
          }
          if (!viewingTunedPhase && rate === 1 && sequenceContextComplete) {
            onObserved(observationKey, 'action-sequence-realtime-context', {
              playback_rate: 1,
              left_context_ms: sequenceLeftDurationMs,
              right_context_ms: sequenceRightDurationMs,
            })
          }
          onNotice(!viewingTunedPhase && rate !== 1
            ? `${left.label} → ${right.label} 的原切点 ${rate}× 慢放已完成；慢放只用于分析动作，仍需在 1× 下完整观看顺序切点才能通过。`
            : !viewingTunedPhase && !sequenceContextComplete
              ? `${left.label} → ${right.label} 的 1× 短窗口试播已完成，但上下文不足；请把切前设为至少 ${previewSeconds(requiredSequenceLeftContextMs)}、切后设为至少 ${previewSeconds(requiredSequenceRightContextMs)}后重新完整观看。`
              : `${left.label} → ${right.label} 的${viewingTunedPhase ? '当前试调' : '原切点'}顺序试播已完成；可切换 A/B 后重播。`)
          return
        }
      }
      sequenceAnimationRef.current = window.requestAnimationFrame(tick)
    }
    sequenceAnimationRef.current = window.requestAnimationFrame(tick)
    return () => {
      if (sequenceAnimationRef.current != null) window.cancelAnimationFrame(sequenceAnimationRef.current)
      sequenceAnimationRef.current = null
    }
  }, [activeLeftFadeMs, activePhaseCandidateSourceKey, activeRightFadeMs, cancelPhaseSequenceComparison, candidateReviewSessionKey, comparisonEvidenceReady, left.label, observationKey, onNotice, onObserved, onRememberCandidateComparisonOutcome, pauseSequenceMedia, phaseTrialSourceKey, rate, requiredSequenceLeftContextMs, requiredSequenceRightContextMs, right.label, sequenceContextComplete, sequenceDurationMs, sequenceLeftDurationMs, sequenceLeftEndMs, sequenceLeftStartMs, sequencePlaying, sequenceRightEndMs, sequenceRightStartMs, viewingTunedPhase])

  useEffect(() => () => {
    leftRef.current?.pause()
    rightRef.current?.pause()
    sequenceLeftRef.current?.pause()
    sequenceRightRef.current?.pause()
  }, [])

  const startComparison = async () => {
    if (comparisonDurationMs <= 0) return
    onBeforePlay()
    onNotice(`正在以 ${rate}× 同步对比 ${left.label} 切前动作与 ${right.label} 切后动作。`)
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    positionMedia()
    const leftVideo = leftRef.current
    const rightVideo = rightRef.current
    if (!leftVideo || !rightVideo) return
    leftVideo.playbackRate = rate
    rightVideo.playbackRate = rate
    setComparisonPlaying(true)
    const results = await Promise.allSettled([leftVideo.play(), rightVideo.play()])
    const unexpected = results.find(result => result.status === 'rejected' && !isMediaPlaybackInterruption(result.reason))
    if (unexpected?.status === 'rejected') {
      pauseMedia()
      onNotice('浏览器没有允许同步动作对比播放，请再点击一次。')
    }
  }

  const startSequencePreview = async () => {
    if (sequenceDurationMs <= 0 || !sequenceLeftReady || !sequenceRightReady) return
    onBeforePlay()
    cancelPhaseSequenceComparison()
    onNotice(`正在以 ${rate}× 顺序试播 ${left.label} → ${right.label} 的${viewingTunedPhase ? '当前试调' : '原切点'}。`)
    pauseMedia()
    positionMedia()
    positionSequenceMedia()
    const leftVideo = sequenceLeftRef.current
    const rightVideo = sequenceRightRef.current
    if (!leftVideo || !rightVideo) return
    leftVideo.playbackRate = rate
    rightVideo.playbackRate = rate
    setSequencePlaying(true)
    try {
      await leftVideo.play()
    } catch (reason) {
      if (isMediaPlaybackInterruption(reason)) return
      pauseSequenceMedia()
      onNotice('浏览器没有允许当前相位顺序试播，请再点击一次。')
    }
  }

  const startPhaseSequenceComparison = () => {
    if (!comparisonEvidenceReady || sequenceDurationMs <= 0 || !sequenceLeftReady || !sequenceRightReady) return
    setPendingPhaseCandidateCompare(null)
    onBeforePlay()
    pauseMedia()
    positionMedia()
    pauseSequenceMedia()
    phaseSequenceCompareStageRef.current = 'baseline'
    setPhaseSequenceCompareStage('baseline')
    setPhaseDecisionSourceKey(null)
    setPhaseView('baseline')
    onNotice(`正在连续对照 ${left.label} → ${right.label}：先播放 A 原切点，再自动播放 B 当前试调。`)
  }

  const adjustPhase = (side: 'left' | 'right', requestedDeltaMs: number) => {
    if (rollTrialDeltaMs !== 0 || transitionTrial) return
    const currentDeltaMs = side === 'left' ? leftPhaseDeltaMs : rightPhaseDeltaMs
    const minimumDeltaMs = side === 'left' ? leftMinimumPhaseMs : rightMinimumPhaseMs
    const maximumDeltaMs = side === 'left' ? leftMaximumPhaseMs : rightMaximumPhaseMs
    const nextDeltaMs = Math.max(minimumDeltaMs, Math.min(maximumDeltaMs, currentDeltaMs + requestedDeltaMs))
    if (nextDeltaMs === currentDeltaMs) return
    onBeforePlay()
    pauseMedia()
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    setPhaseDecisionSourceKey(null)
    setPhaseView('tuned')
    if (side === 'left') {
      setLeftPhaseDeltaMs(nextDeltaMs)
    } else {
      setRightPhaseDeltaMs(nextDeltaMs)
    }
    setProgressMs(0)
    onNotice(nextDeltaMs === 0
      ? `${side === 'left' ? '前镜' : '后镜'}相位试调已回到 A 原方案，尚未写入草稿。`
      : `已在本地把${side === 'left' ? '前镜' : '后镜'}${nextDeltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(nextDeltaMs), fps)}；可顺序试播或使用 A→B 连续对照。`)
  }

  const selectPhaseCandidate = (side: 'left' | 'right', deltaMs: number, compareAfterEvidence = false) => {
    if (rollTrialDeltaMs !== 0 || transitionTrial) return
    onBeforePlay()
    pauseMedia()
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    setPhaseDecisionSourceKey(null)
    setLeftPhaseDeltaMs(side === 'left' ? deltaMs : 0)
    setRightPhaseDeltaMs(side === 'right' ? deltaMs : 0)
    setPhaseView('tuned')
    setProgressMs(0)
    setPendingPhaseCandidateCompare(compareAfterEvidence ? { side, deltaMs } : null)
    onNotice(compareAfterEvidence
      ? `已把${side === 'left' ? '前镜' : '后镜'} ${deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(deltaMs), fps)} 设为本地 B；正在等待对应像素、动作证据与顺序媒体后自动开始 A→B 对照。`
      : `已把${side === 'left' ? '前镜' : '后镜'} ${deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(deltaMs), fps)} 设为本地 B；可继续顺序试播或 A→B 对照。`)
  }

  useEffect(() => {
    if (!pendingPhaseCandidateCompare) return
    const matchesCandidate = pendingPhaseCandidateCompare.side === 'left'
      ? leftPhaseDeltaMs === pendingPhaseCandidateCompare.deltaMs && rightPhaseDeltaMs === 0
      : rightPhaseDeltaMs === pendingPhaseCandidateCompare.deltaMs && leftPhaseDeltaMs === 0
    if (!matchesCandidate || phaseView !== 'tuned' || rollTrialDeltaMs !== 0 || transitionTrial) {
      setPendingPhaseCandidateCompare(null)
      return
    }
    if (!comparisonEvidenceReady || !sequenceLeftReady || !sequenceRightReady || sequenceDurationMs <= 0) return
    startPhaseSequenceComparison()
  }, [
    baselinePixelAnalysis,
    comparisonEvidenceReady,
    leftPhaseDeltaMs,
    pendingPhaseCandidateCompare,
    phaseView,
    rightPhaseDeltaMs,
    rollTrialDeltaMs,
    sequenceDurationMs,
    sequenceLeftReady,
    sequenceRightReady,
    transitionTrial,
    tunedPixelAnalysis,
  ])

  const togglePhaseCandidateScan = (side: 'left' | 'right') => {
    onBeforePlay()
    pauseMedia()
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    const opening = phaseCandidateScanSide !== side
    setPhaseCandidateScanSide(opening ? side : null)
    setPhaseCandidateScanExpanded(false)
    onNotice(opening
      ? `正在按需读取${side === 'left' ? '前镜' : '后镜'}前后 2 帧内的合法候选；不会写入草稿。`
      : `已收起${side === 'left' ? '前镜' : '后镜'}邻帧候选扫描。`)
  }

  const expandPhaseCandidateScan = () => {
    if (!phaseCandidateScanSide || phaseCandidateScanExpanded || hasComparisonTrial || !expandablePhaseCandidateCount) return
    setPhaseCandidateScanExpanded(true)
    if (nextExpandablePhaseCandidate) {
      selectPhaseCandidate(phaseCandidateScanSide, nextExpandablePhaseCandidate.deltaMs, true)
      onNotice(`已显式扩展${phaseCandidateScanSide === 'left' ? '前镜' : '后镜'}到 ±4 帧，并按固定顺序开始下一个额外待处理候选的 A→B 对照；不排序或推荐。`)
      return
    }
    onNotice(`已显式扩展${phaseCandidateScanSide === 'left' ? '前镜' : '后镜'}到 ±4 帧；额外合法候选均已保留 A，只展开供人工复看，不自动播放。`)
  }

  const adjustRollTrial = (requestedDeltaMs: number, reviewKey: string | null = null) => {
    if (hasPhaseTrial || transitionTrial) return
    const nextDeltaMs = Math.max(
      rollMinimumDeltaMs,
      Math.min(rollMaximumDeltaMs, rollTrialDeltaMs + requestedDeltaMs),
    )
    if (nextDeltaMs === rollTrialDeltaMs) return
    onBeforePlay()
    pauseMedia()
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    setPhaseDecisionSourceKey(null)
    setAlternativeReviewTrialKey(reviewKey)
    setPhaseView('tuned')
    setPhaseCandidateScanSide(null)
    setRollTrialDeltaMs(nextDeltaMs)
    setProgressMs(0)
    onNotice(nextDeltaMs === 0
      ? '滚动切位试调已回到 A 原切点，尚未写入草稿。'
      : `已在本地把切点${nextDeltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(nextDeltaMs), fps)}；可顺序试播或使用 A→B 连续对照。`)
  }

  const phaseLabel = (deltaMs: number) => deltaMs === 0
    ? '原相位'
    : `${deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(deltaMs), fps)}`

  const chooseTransitionTrial = (type: 'cut' | 'fade', requestedDurationMs = 0, reviewKey: string | null = null) => {
    if (hasMotionTrial) return
    const durationMs = type === 'fade'
      ? Math.max(100, Math.min(transitionMaximumDurationMs, requestedDurationMs))
      : 0
    const matchesBaseline = type === 'cut'
      ? baselinePairedCut
      : baselinePairedFade && (baselineTransitionOut?.duration_ms ?? 0) === durationMs
    onBeforePlay()
    pauseMedia()
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    setPhaseDecisionSourceKey(null)
    setAlternativeReviewTrialKey(reviewKey)
    setPhaseCandidateScanSide(null)
    setTransitionTrial(matchesBaseline ? null : { type, durationMs })
    setPhaseView(matchesBaseline ? 'baseline' : 'tuned')
    setProgressMs(0)
    onNotice(matchesBaseline
      ? `转场试用已回到 A 当前方案（${baselineTransitionLabel}），尚未写入草稿。`
      : `已在本地试用 B ${type === 'fade' ? `${seconds(durationMs)} 淡出淡入` : '直接切换'}；可顺序试播或使用 A→B 连续对照。`)
  }

  const resetPhase = (announce = true) => {
    onBeforePlay()
    pauseMedia()
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    setRollTrialDeltaMs(0)
    setTransitionTrial(null)
    setLeftPhaseDeltaMs(0)
    setRightPhaseDeltaMs(0)
    setPhaseDecisionSourceKey(null)
    setAlternativeReviewTrialKey(null)
    setPhaseView('baseline')
    setProgressMs(0)
    if (announce) onNotice(`已清除 ${left.label} → ${right.label} 的全部本地试调；草稿未改变。`)
  }

  const continueCandidateReviewAfterDecision = (decisionLabel: string) => {
    if (!phaseCandidateScanSide || !activePhaseCandidateSourceKey) return false
    const nextSide = nextPhaseCandidateAfterDecision
      ? phaseCandidateScanSide
      : nextOtherGuidedPhaseCandidateAfterDecision ? otherGuidedScanSide : null
    const nextCandidate = nextPhaseCandidateAfterDecision ?? nextOtherGuidedPhaseCandidateAfterDecision
    if (!nextSide || !nextCandidate) return false
    resetPhase(false)
    if (nextSide !== phaseCandidateScanSide) {
      setPhaseCandidateScanSide(nextSide)
      setPhaseCandidateScanExpanded(false)
    }
    selectPhaseCandidate(nextSide, nextCandidate.deltaMs, true)
    onNotice(`${decisionLabel}；已继续${nextSide === phaseCandidateScanSide ? '本侧' : `到${nextSide === 'left' ? '前镜' : '后镜'}`}下一个待处理候选，正在准备 A→B 对照。`)
    return true
  }

  const showPhaseView = (view: 'baseline' | 'tuned') => {
    if (view === 'tuned' && !hasComparisonTrial) return
    onBeforePlay()
    pauseMedia()
    cancelPhaseSequenceComparison()
    positionSequenceMedia()
    setPhaseView(view)
    setProgressMs(0)
    onNotice(`已切换到 ${view === 'baseline' ? 'A 原方案' : 'B 当前试调'}；保留的试调值未改变。`)
  }

  const keepBaselinePhase = () => {
    if (phaseDecisionReady && activePhaseCandidateSourceKey) {
      onRememberCandidateComparisonOutcome(candidateReviewSessionKey, activePhaseCandidateSourceKey, 'kept_baseline')
      if (continueCandidateReviewAfterDecision(`已保留 ${left.label} → ${right.label} 的 A 原切点，本次试调未写入草稿`)) return
    }
    if (phaseDecisionReady && alternativeReviewTrialKey) {
      onRememberAlternativeOutcome(candidateReviewSessionKey, alternativeReviewTrialKey, 'kept_baseline')
    }
    resetPhase()
    onNotice(`已保留 ${left.label} → ${right.label} 的 A 原切点；本次试调未写入草稿。`)
  }

  const shortlistCandidatePhase = () => {
    if (!phaseDecisionReady || !activePhaseCandidateSourceKey) return
    onRememberCandidateComparisonOutcome(candidateReviewSessionKey, activePhaseCandidateSourceKey, 'shortlisted')
    if (continueCandidateReviewAfterDecision(`已把 ${left.label} → ${right.label} 的当前 B 候选暂存为本页待复看`)) return
    resetPhase()
    onNotice(`已把 ${left.label} → ${right.label} 的当前 B 候选暂存为本页待复看；草稿未改变。`)
  }

  const applyTunedPhase = () => {
    if (editLocked || !viewingTunedPhase || !hasComparisonTrial) return
    pauseMedia()
    cancelPhaseSequenceComparison()
    setPhaseDecisionSourceKey(null)
    if (rollTrialDeltaMs !== 0) {
      onApplyRoll(rollTrialDeltaMs)
    } else if (transitionTrial) {
      onApplyTransition(transitionTrial.type, transitionTrial.durationMs)
    } else if (leftPhaseDeltaMs !== 0 && rightPhaseDeltaMs !== 0) {
      onApplyPhasePair(leftPhaseDeltaMs, rightPhaseDeltaMs)
    } else if (leftPhaseDeltaMs !== 0) {
      onApplyLeftPhase(leftPhaseDeltaMs)
    } else {
      onApplyRightPhase(rightPhaseDeltaMs)
    }
  }

  const tunedTrialSummary = rollTrialDeltaMs !== 0
    ? `B 将切点${rollTrialDeltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(rollTrialDeltaMs), fps)}，采用后仍只形成一次撤销。`
    : transitionTrial
    ? `B 将采用${transitionTrial.type === 'fade' ? `${seconds(transitionTrial.durationMs)} 淡出淡入` : '直接切换'}，一次写入前镜淡出与后镜淡入。`
    : 'B 会按当前单侧或双侧源窗口相位形成一次撤销。'
  const pixelTrialSourceSummary = transitionTrial && !hasMotionTrial
    ? '仅转场时间呈现变化，切点两帧不变。'
    : rollTrialDeltaMs !== 0
    ? `滚动切位使前镜末帧与后镜首帧同时${rollTrialDeltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(rollTrialDeltaMs), fps)}。`
    : leftPhaseDeltaMs !== 0 && rightPhaseDeltaMs !== 0
    ? '前镜末帧与后镜首帧都使用了当前相位试调。'
    : leftPhaseDeltaMs !== 0
    ? '只有前镜末帧使用了当前相位试调。'
    : '只有后镜首帧使用了当前相位试调。'
  const pixelDeltas = baselinePixelAnalysis && tunedPixelAnalysis
    ? boundaryPixelDeltas(baselinePixelAnalysis, tunedPixelAnalysis)
    : null
  const motionDeltas = baselineMotionAnalysis && tunedMotionAnalysis
    ? boundaryMotionDeltas(baselineMotionAnalysis, tunedMotionAnalysis)
    : null

  useEffect(() => {
    if (hasComparisonTrial) {
      setAdvancedTrialsOpen(true)
      setCandidateDetailsOpen(false)
    }
  }, [hasComparisonTrial])

  return <section className={styles.boundaryActionComparison} aria-label={`${left.label} 到 ${right.label} 的同步动作对比`}>
    <header>
      <span><strong>同步动作</strong><small>{viewingTunedPhase ? 'B 当前试调' : 'A 原方案'} · 共同窗口 {previewSeconds(comparisonDurationMs)} · {rate}×</small></span>
      <code>{timecode(progressMs, fps)} / {timecode(comparisonDurationMs, fps)}</code>
    </header>
    <section className={styles.boundaryPrimaryTask} aria-label="当前同步动作任务">
      <span>
        <strong>{phaseDecisionReady ? '对照已完成，请选择结果' : hasComparisonTrial ? 'B 已准备，下一步完成 A→B 对照' : guidedIssueLabel ? `正在处理：${guidedIssueLabel}` : '先检查最接近切点的邻帧'}</strong>
        <small>{phaseCandidateScanSide
          ? `${phaseCandidateScanSide === 'left' ? '前镜' : '后镜'}已对照 ${reviewedPhaseCandidateCount}/${nearbyPhaseCandidates.length}${undecidedPhaseCandidateCount ? ` · 待决定 ${undecidedPhaseCandidateCount}` : ''}`
          : hasComparisonTrial ? tunedTrialSummary : '只在你打开后加载候选；系统不排序、不推荐，也不会自动设置 B。'}</small>
      </span>
      {!hasComparisonTrial && !phaseDecisionReady && <button type="button" onClick={() => setCandidateDetailsOpen(value => !value)}>
        {candidateDetailsOpen ? '收起候选' : phaseCandidateScanSide ? '继续候选审核' : '检查邻帧候选'}
      </button>}
      {hasComparisonTrial && !phaseDecisionReady && <button
        type="button"
        disabled={phaseSequenceCompareStage === 'idle' && (!comparisonEvidenceReady || sequenceDurationMs <= 0 || !sequenceLeftReady || !sequenceRightReady)}
        onClick={phaseSequenceCompareStage !== 'idle' ? cancelPhaseSequenceComparison : startPhaseSequenceComparison}
      >{phaseSequenceCompareStage === 'baseline' ? '停止 A（1/2）' : phaseSequenceCompareStage === 'tuned' ? '停止 B（2/2）' : comparisonEvidenceReady && sequenceLeftReady && sequenceRightReady ? '开始 A→B 对照' : '正在准备证据'}</button>}
    </section>
    <div className={styles.boundaryPhaseViewSwitch} role="group" aria-label="同步动作切点 A/B 对照">
      <button aria-pressed={!viewingTunedPhase} onClick={() => showPhaseView('baseline')}>A 原方案</button>
      <button aria-pressed={viewingTunedPhase} disabled={!hasComparisonTrial} title={hasComparisonTrial ? '查看当前保留的试调' : '先试调转场、滚动切位或前后镜相位'} onClick={() => showPhaseView('tuned')}>B 当前试调</button>
    </div>
    <button type="button" className={styles.boundaryDisclosureButton} aria-expanded={evidenceDetailsOpen} onClick={() => setEvidenceDetailsOpen(value => !value)}>
      <span><strong>详细动作证据</strong><small>像素、局部幅度、九宫格与重心轨迹</small></span><b>{evidenceDetailsOpen ? '收起' : '查看'}</b>
    </button>
    <div className={styles.boundaryEvidenceDetails} data-open={evidenceDetailsOpen} aria-hidden={!evidenceDetailsOpen}>
    <section className={styles.boundaryABPixelEvidence} aria-label="A/B 切点像素证据">
      <header>
        <span><strong>切点像素证据</strong><small>同时比较末帧与首帧；数值只辅助人工判断，不自动给出好坏结论。</small></span>
        {!hasComparisonTrial && <em>先产生 B 试调</em>}
      </header>
      <div data-single={!hasComparisonTrial}>
        <BoundaryPixelProbe
          projectId={projectId}
          left={left}
          right={right}
          leftSourceTimeMs={baselinePixelLeftMs}
          rightSourceTimeMs={baselinePixelRightMs}
          fps={fps}
          label="A 原方案"
          note="当前草稿的末帧与首帧基线。"
          onAnalysis={handleBaselinePixelAnalysis}
        />
        {hasComparisonTrial && <BoundaryPixelProbe
          projectId={projectId}
          left={left}
          right={right}
          leftSourceTimeMs={tunedPixelLeftMs}
          rightSourceTimeMs={tunedPixelRightMs}
          fps={fps}
          label="B 当前试调"
          note={transitionTrial && !hasMotionTrial
            ? '本次只改变淡出、黑场与淡入的时间呈现，不改变切点两帧，因此指标应与 A 相同。'
            : '当前本地试调的末帧与首帧；继续调整后会重新计算。'}
          onAnalysis={handleTunedPixelAnalysis}
        />}
      </div>
      {hasComparisonTrial && <div className={styles.boundaryABPixelDelta} aria-live="polite">
        <header><strong>B − A 精确差值</strong><small>{pixelTrialSourceSummary}</small></header>
        {pixelDeltas ? <div>
          <span><b>明暗</b><code>{signedPercentagePoint(pixelDeltas.luminance)}</code></span>
          <span><b>综合色彩</b><code>{signedPercentagePoint(pixelDeltas.color)}</code></span>
          <span><b>逐像素</b><code>{signedPercentagePoint(pixelDeltas.pixel)}</code></span>
        </div> : <small>正在等待 A、B 两套帧证据完成后计算差值…</small>}
        <small>正值只表示 B 的该项变化幅度高于 A，负值表示低于 A；方向不代表衔接更好或更差。</small>
      </div>}
    </section>
    <section className={styles.boundaryMotionEvidence} aria-label="A/B 切点动作幅度证据">
      <header>
        <span><strong>切点动作幅度</strong><small>用切点两侧最多各三个真实连续帧，同时核对局部幅度与前后两步节奏。</small></span>
        {!hasComparisonTrial && <em>当前只显示 A</em>}
      </header>
      <div data-single={!hasComparisonTrial}>
        <BoundaryMotionProbe
          projectId={projectId}
          left={left}
          right={right}
          leftEarlierSourceTimeMs={baselineMotionLeftEarlierMs}
          leftPreviousSourceTimeMs={baselineMotionLeftPreviousMs}
          leftCurrentSourceTimeMs={baselinePixelLeftMs}
          rightCurrentSourceTimeMs={baselinePixelRightMs}
          rightNextSourceTimeMs={baselineMotionRightNextMs}
          rightLaterSourceTimeMs={baselineMotionRightLaterMs}
          fps={fps}
          label="A 原方案"
          note="后镜减前镜只表示切点后局部画面变化幅度的增减，不推断运动方向、主体或衔接优劣。"
          onAnalysis={handleBaselineMotionAnalysis}
        />
        {hasComparisonTrial && <BoundaryMotionProbe
          projectId={projectId}
          left={left}
          right={right}
          leftEarlierSourceTimeMs={tunedMotionLeftEarlierMs}
          leftPreviousSourceTimeMs={tunedMotionLeftPreviousMs}
          leftCurrentSourceTimeMs={tunedPixelLeftMs}
          rightCurrentSourceTimeMs={tunedPixelRightMs}
          rightNextSourceTimeMs={tunedMotionRightNextMs}
          rightLaterSourceTimeMs={tunedMotionRightLaterMs}
          fps={fps}
          label="B 当前试调"
          note={transitionTrial && !hasMotionTrial
            ? '本次只改变转场时间呈现，连续帧源时点与 A 相同。'
            : '继续调整相位或滚动切位后，会按新的连续帧重新计算。'}
          onAnalysis={handleTunedMotionAnalysis}
        />}
      </div>
      {hasComparisonTrial && <section className={styles.boundaryMotionTrialDelta} aria-label="B − A 动作证据变化" aria-live="polite">
        <header><strong>B − A 动作证据变化</strong><small>{pixelTrialSourceSummary}</small></header>
        {motionDeltas ? <>
          <div className={styles.boundaryMotionTrialDeltaSummary}>
            <span><b>前镜末段</b><code>{signedPercentagePoint(motionDeltas.left_change).replace(' 个百分点', ' 点')}</code></span>
            <span><b>后镜开端</b><code>{signedPercentagePoint(motionDeltas.right_change).replace(' 个百分点', ' 点')}</code></span>
            <span><b>后−前平衡</b><code>{signedPercentagePoint(motionDeltas.balance).replace(' 个百分点', ' 点')}</code></span>
          </div>
          <div className={styles.boundaryMotionTrialRhythm} aria-label="B − A 动作节奏轨迹">
            <strong>动作节奏轨迹 B − A</strong>
            {[
              { label: '前镜', steps: ['较早→前一', '前一→切点'], values: motionDeltas.left_rhythm },
              { label: '后镜', steps: ['切点→后一', '后一→更后'], values: motionDeltas.right_rhythm },
            ].map(group => <section key={group.label}>
              <b>{group.label}</b>
              {group.values.map((value, index) => <span key={group.steps[index]}><small>{group.steps[index]}</small><code>{value == null ? '不可比' : signedPercentagePoint(value).replace(' 个百分点', ' 点')}</code></span>)}
            </section>)}
            <div className={styles.boundaryMotionRhythmSlope}>
              <span><b>前镜斜率</b><code>{motionDeltas.left_rhythm_slope == null ? '不可比' : signedPercentagePoint(motionDeltas.left_rhythm_slope).replace(' 个百分点', ' 点')}</code></span>
              <span><b>后镜斜率</b><code>{motionDeltas.right_rhythm_slope == null ? '不可比' : signedPercentagePoint(motionDeltas.right_rhythm_slope).replace(' 个百分点', ' 点')}</code></span>
              <span><b>后−前斜率差</b><code>{motionDeltas.rhythm_slope_gap == null ? '不可比' : signedPercentagePoint(motionDeltas.rhythm_slope_gap).replace(' 个百分点', ' 点')}</code></span>
            </div>
            <div className={styles.boundaryMotionCentroidPaths} aria-label="B − A 变化重心轨迹">
              {[
                { label: '前镜轨迹 B − A', path: motionDeltas.left_centroid_path },
                { label: '后镜轨迹 B − A', path: motionDeltas.right_centroid_path },
              ].map(({ label: pathLabel, path }) => <span key={pathLabel}>
                <b>{pathLabel}</b>
                {path
                  ? <code>X {signedPercentagePoint(path.x).replace(' 个百分点', '')} · Y {signedPercentagePoint(path.y).replace(' 个百分点', '')} · 距离 {signedPercentagePoint(path.distance).replace(' 个百分点', '')}</code>
                  : <small>至少一套方案的某一步不可定位。</small>}
              </span>)}
            </div>
            <div className={styles.boundaryMotionCentroidContinuity} aria-label="B − A 切点重心轨迹接续">
              <b>切点轨迹接续 B − A</b>
              {motionDeltas.centroid_path_continuity
                ? <span>
                  <code>X {signedPercentagePoint(motionDeltas.centroid_path_continuity.x).replace(' 个百分点', '')}</code>
                  <code>Y {signedPercentagePoint(motionDeltas.centroid_path_continuity.y).replace(' 个百分点', '')}</code>
                  <code>距离 {signedPercentagePoint(motionDeltas.centroid_path_continuity.distance).replace(' 个百分点', '')}</code>
                  <code>夹角 {motionDeltas.centroid_path_continuity.angle == null ? '不可比' : `${motionDeltas.centroid_path_continuity.angle > 0 ? '+' : motionDeltas.centroid_path_continuity.angle < 0 ? '−' : ''}${Math.abs(motionDeltas.centroid_path_continuity.angle).toFixed(1)}°`}</code>
                </span>
                : <small>至少一套方案缺少前镜或后镜轨迹。</small>}
            </div>
            <small>轨迹差只比较变化重心迁移；正负不表示主体方向或衔接优劣。</small>
          </div>
          <div className={styles.boundaryMotionTrialDeltaGrids}>
            {[
              { label: '前镜九格 B − A', values: motionDeltas.left_grid },
              { label: '后镜九格 B − A', values: motionDeltas.right_grid },
            ].map(group => <section key={group.label} aria-label={group.label}>
              <strong>{group.label}</strong>
              <div>{group.values.map((value, index) => <span key={MOTION_GRID_REGIONS[index]}><b>{MOTION_GRID_REGIONS[index]}</b><code>{signedPercentagePoint(value).replace(' 个百分点', '')}</code></span>)}</div>
            </section>)}
          </div>
          <div className={styles.boundaryMotionTrialCentroidDelta}>
            <span><b>前镜重心 B − A</b>{motionDeltas.left_centroid
              ? <code>X {signedPercentagePoint(motionDeltas.left_centroid.x).replace(' 个百分点', '')} · Y {signedPercentagePoint(motionDeltas.left_centroid.y).replace(' 个百分点', '')} · 分散 {signedPercentagePoint(motionDeltas.left_centroid.dispersion).replace(' 个百分点', '')}</code>
              : <small>至少一套方案没有可定位变化。</small>}</span>
            <span><b>后镜重心 B − A</b>{motionDeltas.right_centroid
              ? <code>X {signedPercentagePoint(motionDeltas.right_centroid.x).replace(' 个百分点', '')} · Y {signedPercentagePoint(motionDeltas.right_centroid.y).replace(' 个百分点', '')} · 分散 {signedPercentagePoint(motionDeltas.right_centroid.dispersion).replace(' 个百分点', '')}</code>
              : <small>至少一套方案没有可定位变化。</small>}</span>
          </div>
        </> : <small>正在等待 A、B 两套连续帧证据完成后计算差值…</small>}
        <small>正负只表示 B 相对 A 的数值变化方向，不代表当前试调更好或更差。</small>
      </section>}
    </section>
    </div>
    <section className={styles.boundaryPhaseScan} data-open={candidateDetailsOpen} aria-label="单侧邻帧候选扫描">
      <header>
        <span><strong>邻帧候选扫描</strong><small>{phaseCandidateScanExpanded ? '已显式扩展到前后 4 帧' : '默认读取前后 2 帧，可按需扩展到 4 帧'}；不排序、不推荐，选择后才成为本地 B。</small></span>
        <div role="group" aria-label="选择邻帧扫描侧">
          <button
            aria-pressed={phaseCandidateScanSide === 'left'}
            disabled={rollTrialDeltaMs !== 0 || transitionTrial != null}
            onClick={() => togglePhaseCandidateScan('left')}
          >前镜</button>
          <button
            aria-pressed={phaseCandidateScanSide === 'right'}
            disabled={rollTrialDeltaMs !== 0 || transitionTrial != null}
            onClick={() => togglePhaseCandidateScan('right')}
          >后镜</button>
        </div>
      </header>
      {candidateDetailsOpen && guidedIssueLabel && <div className={styles.boundaryPhaseGuidance} role="status">
        <span>
          <strong>正在处理：{guidedIssueLabel}</strong>
          <small>{phaseCandidateScanSide
            ? `已打开${phaseCandidateScanSide === 'left' ? '前镜' : '后镜'}；默认 ±2 帧内待处理 ${phaseCandidateScanSide === 'left' ? leftPendingDefaultPhaseCandidateCount : rightPendingDefaultPhaseCandidateCount} / 合法 ${phaseCandidateScanSide === 'left' ? leftDefaultPhaseCandidates.length : rightDefaultPhaseCandidates.length}。`
            : '当前两侧在 ±2 帧内都没有合法源窗口候选。'}</small>
        </span>
        <small>优先继续仍有待处理候选的一侧；两侧都已处理时仍保留合法候选供复看。不排序、不推荐，也不会自动设置 B、播放或采用。</small>
      </div>}
      {candidateDetailsOpen && phaseCandidateScanSide && <>
        <div className={styles.boundaryPhaseReviewProgress} aria-label={`${phaseCandidateScanSide === 'left' ? '前镜' : '后镜'}邻帧候选对照进度`}>
          <span>
            <strong>{reviewedPhaseCandidateCount}/{nearbyPhaseCandidates.length} 已对照{undecidedPhaseCandidateCount > 0 ? ` · 待决定 ${undecidedPhaseCandidateCount}` : ''}</strong>
            <small>保留 A {keptBaselinePhaseCandidateCount} · 待复看 {shortlistedPhaseCandidateCount}</small>
          </span>
          <button
            disabled={!nextReviewPhaseCandidate || hasComparisonTrial}
            title={hasComparisonTrial ? '请先保留 A、采用 B 或清除当前试调，再继续下一个候选。' : undefined}
            onClick={() => nextReviewPhaseCandidate && selectPhaseCandidate(phaseCandidateScanSide, nextReviewPhaseCandidate.deltaMs, true)}
          >{!nearbyPhaseCandidates.length
            ? '没有合法候选'
            : nextUnreviewedPhaseCandidate
              ? '对照下一个未看候选'
              : nextUndecidedPhaseCandidate
                ? '复看下一个待决定'
                : nextShortlistedPhaseCandidate
                  ? '复看下一个待复看'
                  : '本侧候选已处理完'}</button>
        </div>
        {guidedIssueLabel && !nextReviewPhaseCandidate && !hasComparisonTrial && otherGuidedScanCandidateCount > 0 && <button
          type="button"
          className={styles.boundaryPhaseGuidanceContinue}
          onClick={() => togglePhaseCandidateScan(otherGuidedScanSide)}
        >继续检查{otherGuidedScanSide === 'left' ? '前镜' : '后镜'}候选（{otherGuidedScanCandidateCount}）</button>}
        {!phaseCandidateScanExpanded && expandablePhaseCandidateCount > 0 && <div className={styles.boundaryPhaseScanExpansion}>
          <span><strong>近邻仍不顺？</strong><small>±3/±4 帧额外候选：待处理 {pendingExpandablePhaseCandidateCount} / 合法 {expandablePhaseCandidateCount}。</small></span>
          <button
            disabled={hasComparisonTrial}
            title={hasComparisonTrial ? '请先处理或清除当前 B，再扩展候选范围。' : undefined}
            onClick={expandPhaseCandidateScan}
          >{nextExpandablePhaseCandidate ? '扩展并对照下一候选' : '扩展到 ±4 帧供复看'}</button>
        </div>}
        {phaseCandidateScanExpanded && <small className={styles.boundaryPhaseScanExpandedNote}>已扩展到 ±4 帧；审核进度与短名单继续沿用同一页面会话。</small>}
        {nearbyPhaseCandidates.length > 0 && <nav className={styles.boundaryPhaseCandidateNavigator} aria-label={`${phaseCandidateScanSide === 'left' ? '前镜' : '后镜'}邻帧候选快速导航`}>
          {nearbyPhaseCandidates.map(candidate => {
            const direction = candidate.frameOffset < 0 ? '−' : '+'
            const status = phaseCandidateStatusLabel(phaseCandidateScanSide, candidate.deltaMs)
            return <button
              key={`${phaseCandidateScanSide}-${candidate.frameOffset}`}
              type="button"
              data-status={status}
              data-selected={isPhaseCandidateSelected(phaseCandidateScanSide, candidate.deltaMs)}
              aria-controls={phaseCandidateElementId(phaseCandidateScanSide, candidate.frameOffset)}
              title="只定位候选卡，不会设为 B、播放或写入草稿。"
              onClick={() => focusPhaseCandidate(phaseCandidateScanSide, candidate.frameOffset)}
            ><code>{direction}{Math.abs(candidate.frameOffset)}帧</code><small>{status}</small></button>
          })}
        </nav>}
        <div>
          {nearbyPhaseCandidates.length ? nearbyPhaseCandidates.map(candidate => {
          const sideLabel = phaseCandidateScanSide === 'left' ? '前镜' : '后镜'
          const directionLabel = candidate.deltaMs < 0 ? '前移' : '后移'
          const selected = isPhaseCandidateSelected(phaseCandidateScanSide, candidate.deltaMs)
          return <BoundaryPhaseCandidate
            key={`${phaseCandidateScanSide}-${candidate.deltaMs}`}
            elementId={phaseCandidateElementId(phaseCandidateScanSide, candidate.frameOffset)}
            projectId={projectId}
            left={left}
            right={right}
            leftSourceTimeMs={phaseCandidateScanSide === 'left'
              ? Math.max(leftBaseSourceInMs + candidate.deltaMs, leftBaseSourceOutMs + candidate.deltaMs - frameStepMs)
              : baselinePixelLeftMs}
            rightSourceTimeMs={phaseCandidateScanSide === 'right'
              ? rightBaseSourceInMs + candidate.deltaMs
              : baselinePixelRightMs}
            fps={fps}
            label={`${sideLabel}${directionLabel} ${Math.abs(candidate.frameOffset)} 帧`}
            baselineAnalysis={baselinePixelAnalysis}
            baselineMotionAnalysis={baselineMotionAnalysis}
            measuredMotionAnalysis={measuredCandidateMotionEvidence[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] ?? null}
            comparisonOutcome={candidateComparisonOutcomes[candidateMotionSourceKey(phaseCandidateScanSide, candidate.deltaMs)] ?? null}
            selected={selected}
            comparePending={pendingPhaseCandidateCompare?.side === phaseCandidateScanSide
              && pendingPhaseCandidateCompare.deltaMs === candidate.deltaMs}
            onSelect={() => selectPhaseCandidate(phaseCandidateScanSide, candidate.deltaMs)}
            onCompare={() => selectPhaseCandidate(phaseCandidateScanSide, candidate.deltaMs, true)}
          />
          }) : <small>这一侧在当前素材把手内没有可扫描的邻近相位。</small>}
        </div>
      </>}
      {candidateDetailsOpen && !phaseCandidateScanSide && <small>选择前镜或后镜后再按需加载候选，不会后台扫描整段素材。</small>}
    </section>
    {phaseCandidateReviewExhausted && !hasComparisonTrial && <section
      className={styles.boundaryCandidateExhaustedNext}
      aria-label="邻帧候选全部排除后的无损续办"
    >
      <span>
        <strong>{allLegalPhaseCandidates.length} 项合法邻帧均已保留 A</strong>
        <small>相位微调已人工排除。可显式选择另一类 B 继续比较；系统不选择方向、不播放，也不自动采用。</small>
      </span>
      <div>
        {pendingExhaustedCandidateAlternatives.map(alternative => <button
          key={alternative.key}
          type="button"
          onClick={() => alternative.key.startsWith('roll:')
            ? adjustRollTrial(Number(alternative.key.slice('roll:'.length)), alternative.key)
            : exhaustedCandidateTransitionTrial && chooseTransitionTrial(
              exhaustedCandidateTransitionTrial.type,
              exhaustedCandidateTransitionTrial.durationMs,
              alternative.key,
            )}
        >{alternative.label}</button>)}
      </div>
      {!pendingExhaustedCandidateAlternatives.length && <>
        <small>{exhaustedCandidateAlternatives.length
          ? `其他 ${exhaustedCandidateAlternatives.length} 项合法原子试调也已完整对照并保留 A；当前镜头内容需要进入素材或结构层处理。`
          : '当前源窗和转场合同没有可用的下一类无损试调；当前镜头内容需要进入素材或结构层处理。'}</small>
        <nav aria-label="无损试调耗尽后的恢复操作">
          {formalOrderSwapAvailable && <button
            type="button"
            data-primary="true"
            disabled={editLocked}
            title={editLocked ? '画面轨已锁定，不能交换镜头顺序。' : `把 ${left.label} → ${right.label} 原子交换为正式分镜顺序，并自动复检新切点`}
            onClick={onSwapToFormalOrder}
          >按正式顺序交换两镜</button>}
          <button type="button" disabled={editLocked} title={editLocked ? '画面轨已锁定，不能替换素材。' : `从未使用的已批准视频中替换 ${left.label}`} onClick={onReplaceLeftAsset}>替换前镜素材</button>
          <button type="button" disabled={editLocked} title={editLocked ? '画面轨已锁定，不能替换素材。' : `从未使用的已批准视频中替换 ${right.label}`} onClick={onReplaceRightAsset}>替换后镜素材</button>
          <button type="button" disabled={editLocked} title={editLocked ? '画面轨已锁定，不能调整镜头结构。' : '退出动作试调并回到时间线片段顺序与移动操作'} onClick={onAdjustStructure}>调整镜头结构</button>
        </nav>
        <small>入口只切换工作区；不会自动选择素材、重排片段、播放或写入草稿。</small>
      </>}
    </section>}
    <button type="button" className={styles.boundaryDisclosureButton} aria-expanded={advancedTrialsOpen} onClick={() => setAdvancedTrialsOpen(value => !value)}>
      <span><strong>其他无损试调</strong><small>转场、滚动切位、双方相位与分析播放</small></span><b>{advancedTrialsOpen ? '收起' : '展开'}</b>
    </button>
    <div className={styles.boundaryAdvancedTrials} data-open={advancedTrialsOpen} aria-hidden={!advancedTrialsOpen}>
    <div className={styles.boundaryTransitionTrial}>
      <span><strong>转场无损试用</strong><small>A 为当前 {baselineTransitionLabel}；B 在顺序舞台真实显示淡出至黑场再淡入，不是交叉叠化。</small></span>
      <div>
        <button disabled={hasMotionTrial} aria-pressed={transitionTrial?.type === 'cut'} onClick={() => chooseTransitionTrial('cut')}>直接切换</button>
        {[200, 300, 500].map(durationMs => <button
          key={durationMs}
          disabled={hasMotionTrial || durationMs > transitionMaximumDurationMs}
          aria-pressed={transitionTrial?.type === 'fade' && transitionTrial.durationMs === durationMs}
          onClick={() => chooseTransitionTrial('fade', durationMs)}
        >淡出淡入 {seconds(durationMs)}</button>)}
      </div>
    </div>
    <div className={styles.boundaryRollTrial}>
      <span><strong>滚动切位试调</strong><small>临时联动前镜末端与后镜首端，不写草稿；与源窗口相位二选一。</small></span>
      <div>
        <button aria-label={`${left.label} 到 ${right.label} 滚动切位试调前移 1 秒`} disabled={hasPhaseTrial || transitionTrial != null || rollTrialDeltaMs <= rollMinimumDeltaMs} onClick={() => adjustRollTrial(-1000)}>−1s</button>
        <button aria-label={`${left.label} 到 ${right.label} 滚动切位试调前移 1 帧`} disabled={hasPhaseTrial || transitionTrial != null || rollTrialDeltaMs <= rollMinimumDeltaMs} onClick={() => adjustRollTrial(-frameStepMs)}>−1帧</button>
        <code>{rollTrialDeltaMs === 0 ? '原切点' : `${rollTrialDeltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(rollTrialDeltaMs), fps)}`}</code>
        <button aria-label={`${left.label} 到 ${right.label} 滚动切位试调后移 1 帧`} disabled={hasPhaseTrial || transitionTrial != null || rollTrialDeltaMs >= rollMaximumDeltaMs} onClick={() => adjustRollTrial(frameStepMs)}>+1帧</button>
        <button aria-label={`${left.label} 到 ${right.label} 滚动切位试调后移 1 秒`} disabled={hasPhaseTrial || transitionTrial != null || rollTrialDeltaMs >= rollMaximumDeltaMs} onClick={() => adjustRollTrial(1000)}>+1s</button>
      </div>
    </div>
    <div className={styles.boundarySequencePreview} data-open={sequenceVisible} aria-hidden={!sequenceVisible}>
      <video
        ref={sequenceLeftRef}
        aria-label={`${left.label} 当前试调顺序试播前镜`}
        data-visible={sequenceSide === 'left'}
        style={{ opacity: sequenceSide === 'left' ? sequenceLeftOpacity : 0 }}
        muted
        playsInline
        preload="auto"
        src={`/api/v1/projects/${projectId}/assets/${left.asset_id}/content`}
        onLoadedMetadata={event => {
          const targetSeconds = sequenceLeftStartMs / 1000
          event.currentTarget.currentTime = targetSeconds
          event.currentTarget.playbackRate = rate
          if (!event.currentTarget.seeking && Math.abs(event.currentTarget.currentTime - targetSeconds) <= 0.004) {
            setSequenceLeftEvidenceKey(sequenceLeftSourceKey)
          }
        }}
        onSeeked={event => {
          if (Math.abs(event.currentTarget.currentTime - sequenceLeftStartMs / 1000) <= 0.004) {
            setSequenceLeftEvidenceKey(sequenceLeftSourceKey)
          }
        }}
      />
      <video
        ref={sequenceRightRef}
        aria-label={`${right.label} 当前试调顺序试播后镜`}
        data-visible={sequenceSide === 'right'}
        style={{ opacity: sequenceSide === 'right' ? sequenceRightOpacity : 0 }}
        muted
        playsInline
        preload="auto"
        src={`/api/v1/projects/${projectId}/assets/${right.asset_id}/content`}
        onLoadedMetadata={event => {
          const targetSeconds = sequenceRightStartMs / 1000
          event.currentTarget.currentTime = targetSeconds
          event.currentTarget.playbackRate = rate
          if (!event.currentTarget.seeking && Math.abs(event.currentTarget.currentTime - targetSeconds) <= 0.004) {
            setSequenceRightEvidenceKey(sequenceRightSourceKey)
          }
        }}
        onSeeked={event => {
          if (Math.abs(event.currentTarget.currentTime - sequenceRightStartMs / 1000) <= 0.004) {
            setSequenceRightEvidenceKey(sequenceRightSourceKey)
          }
        }}
      />
      <span><strong>{sequenceSide === 'left' ? left.label : right.label}</strong><small>{sequenceSide === 'left' ? '切前' : '切后'} · {viewingTunedPhase ? 'B 当前试调' : 'A 原方案'}{phaseSequenceCompareStage !== 'idle' ? ` · ${phaseSequenceCompareStage === 'baseline' ? '1/2' : '2/2'}` : ''}</small><code>{timecode(sequenceProgressMs, fps)} / {timecode(sequenceDurationMs, fps)}</code></span>
    </div>
    </div>
    {phaseDecisionReady && <div className={styles.boundaryPhaseDecision} role="group" aria-label="A/B 切点对照结论">
      <span><strong>选择这次对照结果</strong><small>保留 A 不写草稿；{tunedTrialSummary}</small></span>
      <div data-columns={activePhaseCandidateSourceKey ? 3 : 2}>
        <button onClick={keepBaselinePhase}><RotateCcw />保留 A 原方案{candidateDecisionWillContinue ? '并继续' : ''}</button>
        {activePhaseCandidateSourceKey && <button onClick={shortlistCandidatePhase}><Clock3 />暂存 B 待复看{candidateDecisionWillContinue ? '并继续' : ''}</button>}
        <button
          disabled={editLocked || !viewingTunedPhase}
          title={editLocked ? '画面轨已锁定，只能比较或保留 A，不能采用 B。' : !viewingTunedPhase ? '请先切回 B 当前试调，确认要采用的画面。' : '把当前转场、滚动切位或全部非零相位试调作为一次操作写入草稿'}
          onClick={applyTunedPhase}
        ><CheckCircle2 />采用 B 当前试调</button>
      </div>
    </div>}
    <div className={styles.boundaryAdvancedTrials} data-open={advancedTrialsOpen} aria-hidden={!advancedTrialsOpen}>
    <div>
      <figure>
        <video
          ref={leftRef}
          aria-label={`${left.label} 切前动作窗口`}
          muted
          playsInline
          preload="auto"
          src={`/api/v1/projects/${projectId}/assets/${left.asset_id}/content`}
          onLoadedMetadata={event => { event.currentTarget.currentTime = leftStartMs / 1000; event.currentTarget.playbackRate = rate }}
        />
        <figcaption><strong>{left.label}</strong><span>切前动作</span><code>{timecode(leftStartMs, fps)}–{timecode(leftEndMs, fps)}</code></figcaption>
        <div className={styles.boundaryActionPhase}>
          <span><b>前镜相位</b><code>{phaseLabel(leftActivePhaseDeltaMs)}</code></span>
          <div>
            <button aria-label={`${left.label} 同步动作相位前移 1 秒`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || leftPhaseDeltaMs <= leftMinimumPhaseMs} onClick={() => adjustPhase('left', -1000)}>−1s</button>
            <button aria-label={`${left.label} 同步动作相位前移 1 帧`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || leftPhaseDeltaMs <= leftMinimumPhaseMs} onClick={() => adjustPhase('left', -frameStepMs)}>−1帧</button>
            <button aria-label={`${left.label} 同步动作相位后移 1 帧`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || leftPhaseDeltaMs >= leftMaximumPhaseMs} onClick={() => adjustPhase('left', frameStepMs)}>+1帧</button>
            <button aria-label={`${left.label} 同步动作相位后移 1 秒`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || leftPhaseDeltaMs >= leftMaximumPhaseMs} onClick={() => adjustPhase('left', 1000)}>+1s</button>
          </div>
          <button disabled={editLocked || leftPhaseDeltaMs === 0 || !viewingTunedPhase} title={editLocked ? '画面轨已锁定，只能试调，不能应用。' : !viewingTunedPhase && leftPhaseDeltaMs !== 0 ? '请先返回 B 当前试调，确认正在看到要应用的相位。' : '把当前试调写入前镜源窗口'} onClick={() => onApplyLeftPhase(leftPhaseDeltaMs)}>应用前镜相位</button>
        </div>
      </figure>
      <figure>
        <video
          ref={rightRef}
          aria-label={`${right.label} 切后动作窗口`}
          muted
          playsInline
          preload="auto"
          src={`/api/v1/projects/${projectId}/assets/${right.asset_id}/content`}
          onLoadedMetadata={event => { event.currentTarget.currentTime = rightStartMs / 1000; event.currentTarget.playbackRate = rate }}
        />
        <figcaption><strong>{right.label}</strong><span>切后动作</span><code>{timecode(rightStartMs, fps)}–{timecode(rightEndMs, fps)}</code></figcaption>
        <div className={styles.boundaryActionPhase}>
          <span><b>后镜相位</b><code>{phaseLabel(rightActivePhaseDeltaMs)}</code></span>
          <div>
            <button aria-label={`${right.label} 同步动作相位前移 1 秒`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || rightPhaseDeltaMs <= rightMinimumPhaseMs} onClick={() => adjustPhase('right', -1000)}>−1s</button>
            <button aria-label={`${right.label} 同步动作相位前移 1 帧`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || rightPhaseDeltaMs <= rightMinimumPhaseMs} onClick={() => adjustPhase('right', -frameStepMs)}>−1帧</button>
            <button aria-label={`${right.label} 同步动作相位后移 1 帧`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || rightPhaseDeltaMs >= rightMaximumPhaseMs} onClick={() => adjustPhase('right', frameStepMs)}>+1帧</button>
            <button aria-label={`${right.label} 同步动作相位后移 1 秒`} disabled={rollTrialDeltaMs !== 0 || transitionTrial != null || rightPhaseDeltaMs >= rightMaximumPhaseMs} onClick={() => adjustPhase('right', 1000)}>+1s</button>
          </div>
          <button disabled={editLocked || rightPhaseDeltaMs === 0 || !viewingTunedPhase} title={editLocked ? '画面轨已锁定，只能试调，不能应用。' : !viewingTunedPhase && rightPhaseDeltaMs !== 0 ? '请先返回 B 当前试调，确认正在看到要应用的相位。' : '把当前试调写入后镜源窗口'} onClick={() => onApplyRightPhase(rightPhaseDeltaMs)}>应用后镜相位</button>
        </div>
      </figure>
    </div>
    <footer>
      <button disabled={comparisonDurationMs <= 0} onClick={playing ? pauseMedia : startComparison}>{playing ? <Pause /> : <Play />}{playing ? '暂停' : progressMs > 0 ? '重播' : '同步播放'}</button>
      <button disabled={sequenceDurationMs <= 0 || !sequenceLeftReady || !sequenceRightReady} onClick={sequencePlaying && phaseSequenceCompareStage === 'idle' ? pauseSequenceMedia : startSequencePreview}>{sequencePlaying && phaseSequenceCompareStage === 'idle' ? <Pause /> : <Play />}{sequencePlaying && phaseSequenceCompareStage === 'idle' ? '暂停顺序试播' : sequenceProgressMs > 0 ? '重播顺序切点' : '顺序试播切点'}</button>
      <button
        disabled={phaseSequenceCompareStage === 'idle' && (!comparisonEvidenceReady || sequenceDurationMs <= 0 || !sequenceLeftReady || !sequenceRightReady)}
        title={!hasComparisonTrial
          ? '先试调转场、滚动切位或前后镜相位，再进行连续对照。'
          : !comparisonEvidenceReady
          ? '正在等待当前 A/B 的像素与动作证据完成。'
          : !sequenceLeftReady || !sequenceRightReady
          ? '正在等待两路顺序媒体定位到当前源时点。'
          : '先播放 A 原方案，再自动播放 B 当前试调；全程静音且不写入草稿。'}
        onClick={phaseSequenceCompareStage !== 'idle' ? cancelPhaseSequenceComparison : startPhaseSequenceComparison}
      >{phaseSequenceCompareStage !== 'idle' ? <Pause /> : <Play />}{phaseSequenceCompareStage === 'baseline' ? '停止 A（1/2）' : phaseSequenceCompareStage === 'tuned' ? '停止 B（2/2）' : hasComparisonTrial && (!comparisonEvidenceReady || !sequenceLeftReady || !sequenceRightReady) ? '等待 A/B 证据' : 'A→B 连续对照'}</button>
      <button onClick={() => { pauseMedia(); cancelPhaseSequenceComparison(); positionMedia(); positionSequenceMedia() }}><RotateCcw />回到窗口开头</button>
      <button disabled={!hasComparisonTrial} onClick={() => resetPhase()}><RotateCcw />清除当前试调</button>
      <button
        disabled={editLocked || leftPhaseDeltaMs === 0 || rightPhaseDeltaMs === 0 || !viewingTunedPhase}
        title={editLocked
          ? '画面轨已锁定，只能试调，不能应用。'
          : !viewingTunedPhase && leftPhaseDeltaMs !== 0 && rightPhaseDeltaMs !== 0
          ? '请先返回 B 当前试调，确认正在看到要应用的双方相位。'
          : leftPhaseDeltaMs === 0 || rightPhaseDeltaMs === 0
          ? '前镜和后镜都产生相位试调后，才能作为一组一次应用。'
          : '把双方当前试调作为一个组合写入，并只记录一次撤销'}
        onClick={() => onApplyPhasePair(leftPhaseDeltaMs, rightPhaseDeltaMs)}
      >应用双方相位</button>
    </footer>
    <small>同步播放用于并排看动作阶段；顺序试播会在同一黑色舞台播放前镜再切后镜，并真实消费当前 A/B 的淡出淡入透明度。只有 A 原方案在 1× 下看完切点两侧各最多 1 秒的完整可用上下文，才计入通过证据；慢放或更短窗口只用于分析。转场、滚动切位与源窗口相位均可先无损试调，再用 A→B 连续对照和明确结论决定是否写入；三类试调互斥。该会话不写草稿，也不包含音频或字幕。切回 A 不会丢失试调值；只有正在查看 B 时才能采用。锁轨时仍可试播，但不能采用。</small>
    </div>
  </section>
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
  const [boundaryPreviewSession, setBoundaryPreviewSession] = useState<{
    boundaryKey: string
    boundaryFingerprint: string
    beforeMs: number
    afterMs: number
    playbackRate: number
  } | null>(null)
  const [boundaryFocusKey, setBoundaryFocusKey] = useState<string | null>(null)
  const [boundaryInspectorOpen, setBoundaryInspectorOpen] = useState(false)
  const [boundaryReviewSession, setBoundaryReviewSession] = useState<{
    boundaryIndexes: number[]
    position: number
    skippedCount: number
    scope: 'timeline' | 'slide' | 'trim' | 'history' | 'repair' | 'asset' | 'structure'
    beforeMs: number
    afterMs: number
    playbackRate: number
  } | null>(null)
  const [boundaryPreviewLoop, setBoundaryPreviewLoop] = useState<{
    boundaryKey: string
    boundaryFingerprint: string
    leftItemId: string
    startMs: number
    endMs: number
    beforeMs: number
    afterMs: number
    playbackRate: number
    label: string
    iteration: number
    observationRecorded: boolean
  } | null>(null)
  const [boundaryFrameComparisonKey, setBoundaryFrameComparisonKey] = useState<string | null>(null)
  const [boundaryFrameOverlayKey, setBoundaryFrameOverlayKey] = useState<string | null>(null)
  const [boundaryFrameStripKey, setBoundaryFrameStripKey] = useState<string | null>(null)
  const [boundaryActionComparisonKey, setBoundaryActionComparisonKey] = useState<string | null>(null)
  const [boundaryCandidateGuidanceRequest, setBoundaryCandidateGuidanceRequest] = useState<{
    boundaryKey: string
    checkId: string
    checkLabel: string
    requestToken: number
    intent: 'issue' | 'resume'
  } | null>(null)
  const [boundaryCandidateReviewSessions, setBoundaryCandidateReviewSessions] = useState<Record<string, BoundaryCandidateReviewSession>>({})
  const candidateReviewSessionsLoadedRef = useRef(false)
  const suppressDraftWritesRef = useRef(false)
  const boundaryCandidateReviewSessionsRef = useRef<Record<string, BoundaryCandidateReviewSession>>({})
  const replaceBoundaryCandidateReviewSessions = useCallback((sessions: Record<string, BoundaryCandidateReviewSession>) => {
    boundaryCandidateReviewSessionsRef.current = sessions
    setBoundaryCandidateReviewSessions(sessions)
  }, [])
  const rememberBoundaryCandidateMotionEvidence = useCallback((
    sessionKey: string,
    sourceKey: string,
    analysis: BoundaryMotionAnalysis,
  ) => {
    candidateReviewSessionsLoadedRef.current = true
    const current = boundaryCandidateReviewSessionsRef.current
    const session = current[sessionKey] ?? EMPTY_BOUNDARY_CANDIDATE_REVIEW_SESSION
    if (JSON.stringify(session.measuredMotionEvidence[sourceKey]) === JSON.stringify(analysis)) return
    replaceBoundaryCandidateReviewSessions({
      ...current,
      [sessionKey]: {
        ...session,
        measuredMotionEvidence: { ...session.measuredMotionEvidence, [sourceKey]: analysis },
      },
    })
    setDirty(true)
  }, [replaceBoundaryCandidateReviewSessions])
  const rememberBoundaryCandidateComparisonOutcome = useCallback((
    sessionKey: string,
    sourceKey: string,
    outcome: BoundaryCandidateComparisonOutcome,
  ) => {
    candidateReviewSessionsLoadedRef.current = true
    const current = boundaryCandidateReviewSessionsRef.current
    const session = current[sessionKey] ?? EMPTY_BOUNDARY_CANDIDATE_REVIEW_SESSION
    if (session.comparisonOutcomes[sourceKey] === outcome) return
    replaceBoundaryCandidateReviewSessions({
      ...current,
      [sessionKey]: {
        ...session,
        comparisonOutcomes: { ...session.comparisonOutcomes, [sourceKey]: outcome },
      },
    })
    setDirty(true)
  }, [replaceBoundaryCandidateReviewSessions])
  const rememberBoundaryAlternativeOutcome = useCallback((
    sessionKey: string,
    alternativeKey: string,
    outcome: 'kept_baseline',
  ) => {
    candidateReviewSessionsLoadedRef.current = true
    const current = boundaryCandidateReviewSessionsRef.current
    const session = current[sessionKey] ?? EMPTY_BOUNDARY_CANDIDATE_REVIEW_SESSION
    if (session.alternativeOutcomes[alternativeKey] === outcome) return
    replaceBoundaryCandidateReviewSessions({
      ...current,
      [sessionKey]: {
        ...session,
        alternativeOutcomes: { ...session.alternativeOutcomes, [alternativeKey]: outcome },
      },
    })
    setDirty(true)
  }, [replaceBoundaryCandidateReviewSessions])
  const [pendingBoundaryPreviewKey, setPendingBoundaryPreviewKey] = useState<string | null>(null)
  const [boundaryRollMonitor, setBoundaryRollMonitor] = useState<{
    boundaryKey: string
    active: boolean
    left: TimelineItem
    right: TimelineItem
    leftSourceTimeMs: number
    rightSourceTimeMs: number
    deltaMs: number
  } | null>(null)
  const [pendingBoundaryReview, setPendingBoundaryReview] = useState<{
    keys: string[]
    scope: 'slide' | 'trim' | 'history' | 'repair' | 'asset' | 'structure'
  } | null>(null)
  const [boundaryFrameBlendPercent, setBoundaryFrameBlendPercent] = useState(50)
  const [boundaryContinuityOutcomes, setBoundaryContinuityOutcomes] = useState<
    Record<string, Record<string, BoundaryContinuityCheckOutcome>>
  >({})
  const [boundaryContinuityIssueContexts, setBoundaryContinuityIssueContexts] = useState<
    Record<string, BoundaryContinuityIssueContext[]>
  >({})
  const [boundaryContinuityObservations, setBoundaryContinuityObservations] = useState<BoundaryContinuityObservations>({})
  const [boundaryContinuityReadyEvidence, setBoundaryContinuityReadyEvidence] = useState<BoundaryContinuityReadyEvidence>({})
  const recordBoundaryContinuityReadyEvidence = useCallback((
    observationKey: string,
    evidence: 'frames-left' | 'frames-right' | 'overlay' | 'action-synchronous' | 'action-sequence-realtime-context',
    actionSequenceEvidence?: EditorActionSequenceEvidence,
  ) => {
    setBoundaryContinuityReadyEvidence(current => {
      const value = evidence === 'action-sequence-realtime-context' ? actionSequenceEvidence : true
      if (!value || current[observationKey]?.[evidence] === value) return current
      return {
        ...current,
        [observationKey]: { ...(current[observationKey] ?? {}), [evidence]: value },
      }
    })
  }, [])
  const [monitorScale, setMonitorScale] = useState<'fit' | 'actual'>('fit')
  const [monitorFullscreen, setMonitorFullscreen] = useState(false)
  const [assetFilter, setAssetFilter] = useState<'all' | 'video' | 'audio' | 'subtitle'>('all')
  const [assetSearchOpen, setAssetSearchOpen] = useState(false)
  const [assetSearchQuery, setAssetSearchQuery] = useState('')
  const [gapAssetSelection, setGapAssetSelection] = useState(false)
  const [boundaryAssetReplacementTargetId, setBoundaryAssetReplacementTargetId] = useState<string | null>(null)
  const [notice, setNotice] = useState('剪辑调整会自动保存为项目草稿；生成可导出版本时才冻结新时间线。')
  const [history, setHistory] = useState<EditorHistorySnapshot[]>([])
  const [future, setFuture] = useState<EditorHistorySnapshot[]>([])
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
  const [previewWatchCompleteKey, setPreviewWatchCompleteKey] = useState<string | null>(null)
  const [previewWatchProgressMs, setPreviewWatchProgressMs] = useState(0)
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
  const previewWatchSessionRef = useRef<{
    previewKey: string
    lastTimeSeconds: number
  } | null>(null)
  const advancingPlaybackRef = useRef(false)
  const timelinePlaybackObservationRef = useRef<{
    boundaries: Array<{
      boundaryKey: string
      boundaryFingerprint: string
      boundaryMs: number
      evidence: EditorActionSequenceEvidence
      status: 'pending' | 'recorded' | 'invalid'
    }>
  } | null>(null)
  const preservePlayheadOnReviewFocusRef = useRef(false)
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
  const resetProjectIdRef = useRef<string | null>(null)
  const restoredDraftIdentityRef = useRef<string | null>(null)
  useEffect(() => {
    if (resetProjectIdRef.current === projectId) return
    resetProjectIdRef.current = projectId
    restoredDraftIdentityRef.current = null
    candidateReviewSessionsLoadedRef.current = false
    suppressDraftWritesRef.current = true
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
    setBoundaryActionComparisonKey(null)
    setBoundaryCandidateGuidanceRequest(null)
    setBoundaryAssetReplacementTargetId(null)
    setPendingBoundaryPreviewKey(null)
    setBoundaryRollMonitor(null)
    setBoundaryFrameBlendPercent(50)
    setBoundaryContinuityOutcomes({})
    setBoundaryContinuityIssueContexts({})
    setBoundaryContinuityObservations({})
    setBoundaryContinuityReadyEvidence({})
    setBoundaryContinuityObservations({})
    setBoundaryContinuityReadyEvidence({})
    setDirty(false)
    setLastValidation(null)
    setLastPreview(null)
    setPreviewWatchCompleteKey(null)
    setPreviewWatchProgressMs(0)
    previewWatchSessionRef.current = null
    setLastAutoSavedAt(null)
    setLastAutoSavedFingerprint(null)
    setLastAutoSaveAttemptFingerprint(null)
  }, [projectId, replaceBoundaryCandidateReviewSessions])

  useEffect(() => {
    if (!sourceTimeline || !workspace.data || serverDraft.isPending) return
    const restoreIdentity = [projectId, sourceTimeline.id, sourceTimeline.row_version].join(':')
    if (restoredDraftIdentityRef.current === restoreIdentity) return
    restoredDraftIdentityRef.current = restoreIdentity
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
    const remoteItems: TimelineItem[] | null = remoteMatches && remote
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
    const remoteZoom = remote?.track_config.pixels_per_second ?? sourceTimeline.track_config.pixels_per_second ?? 82
    const remoteSnapEnabled = remote?.track_config.snap_enabled ?? sourceTimeline.track_config.snap_enabled
    const remotePlayheadMs = remote?.playhead_ms ?? 0
    const remoteContinuityOutcomes = remote?.continuity_outcomes ?? {}
    const remoteContinuityObservations = remote?.continuity_observations ?? {}
    const remoteCandidateReviewSessions: Record<string, BoundaryCandidateReviewSession> = Object.fromEntries(
      Object.entries(remote?.candidate_review_sessions ?? {}).map(([key, session]) => [key, {
        measuredMotionEvidence: session.measured_motion_evidence,
        comparisonOutcomes: session.comparison_outcomes,
        alternativeOutcomes: session.alternative_outcomes,
      }]),
    )
    const remoteContinuityIssueContexts = Object.fromEntries(
      Object.entries(remote?.continuity_issue_contexts ?? {}).map(([key, contexts]) => [
        key,
        contexts.map(context => ({
          checkId: context.check_id,
          checkLabel: context.check_label,
          mode: context.mode,
        })),
      ]),
    )
    const remoteFingerprint = remoteItems
      ? editorDraftFingerprint(
        sourceTimeline,
        remoteItems,
        remotePlayheadMs,
        remoteSnapEnabled,
        remoteZoom,
        remoteContinuityOutcomes,
        remoteContinuityIssueContexts,
        remoteContinuityObservations,
        remoteCandidateReviewSessions,
      )
      : null
    const useRemote = remoteMatches
    const restoredItems = useRemote && remoteItems
      ? remoteItems
      : localRestored?.items ?? sourceTimeline.items
    const restoredZoom = useRemote ? remoteZoom : localRestored?.timeline_zoom ?? sourceTimeline.track_config.pixels_per_second ?? 82
    const restoredSnapEnabled = useRemote ? remoteSnapEnabled : localRestored?.snap_enabled ?? sourceTimeline.track_config.snap_enabled
    const restoredPlayheadMs = useRemote ? remotePlayheadMs : localRestored?.playhead_ms ?? 0
    const restoredContinuityOutcomes = useRemote
      ? remoteContinuityOutcomes
      : localRestored?.boundary_continuity_outcomes ?? {}
    const restoredContinuityIssueContexts = useRemote
      ? remoteContinuityIssueContexts
      : localRestored?.boundary_continuity_issue_contexts ?? {}
    const restoredContinuityObservations = useRemote
      ? remoteContinuityObservations
      : localRestored?.boundary_continuity_observations ?? {}
    const restoredCandidateReviewSessions = useRemote
      ? remoteCandidateReviewSessions
      : localRestored?.boundary_candidate_review_sessions ?? {}
    setItems(restoredItems)
    setTimelineZoom(restoredZoom)
    setSnapEnabled(restoredSnapEnabled)
    setBoundaryContinuityOutcomes(restoredContinuityOutcomes)
    setBoundaryContinuityIssueContexts(restoredContinuityIssueContexts)
    setBoundaryContinuityObservations(restoredContinuityObservations)
    const mergedCandidateReviewSessions = candidateReviewSessionsLoadedRef.current
      ? Object.fromEntries(Array.from(new Set([
        ...Object.keys(restoredCandidateReviewSessions),
        ...Object.keys(boundaryCandidateReviewSessionsRef.current),
      ])).map(key => {
        const restoredSession = restoredCandidateReviewSessions[key] ?? EMPTY_BOUNDARY_CANDIDATE_REVIEW_SESSION
        const currentSession = boundaryCandidateReviewSessionsRef.current[key] ?? EMPTY_BOUNDARY_CANDIDATE_REVIEW_SESSION
        return [key, {
          measuredMotionEvidence: {
            ...restoredSession.measuredMotionEvidence,
            ...currentSession.measuredMotionEvidence,
          },
          comparisonOutcomes: {
            ...restoredSession.comparisonOutcomes,
            ...currentSession.comparisonOutcomes,
          },
          alternativeOutcomes: {
            ...restoredSession.alternativeOutcomes,
            ...currentSession.alternativeOutcomes,
          },
        }]
      }))
      : restoredCandidateReviewSessions
    boundaryCandidateReviewSessionsRef.current = mergedCandidateReviewSessions
    setBoundaryCandidateReviewSessions(mergedCandidateReviewSessions)
    candidateReviewSessionsLoadedRef.current = true
    window.setTimeout(() => { suppressDraftWritesRef.current = false }, 0)
    setBoundaryContinuityReadyEvidence({})
    setDirty(Boolean(localRestored && !useRemote))
    setLastAutoSavedAt(useRemote && remote ? remote.updated_at : null)
    const restoredFingerprint = editorDraftFingerprint(
      sourceTimeline,
      restoredItems,
      restoredPlayheadMs,
      restoredSnapEnabled,
      restoredZoom,
      restoredContinuityOutcomes,
      restoredContinuityIssueContexts,
      restoredContinuityObservations,
      mergedCandidateReviewSessions,
    )
    setLastAutoSavedFingerprint(useRemote ? restoredFingerprint : null)
    setLastAutoSaveAttemptFingerprint(useRemote ? restoredFingerprint : null)
    setHistory([])
    setFuture([])
    setSelectedIndex(0)
    setPlayheadMs(restoredPlayheadMs)
    setPlaying(false)
    setBoundaryFocusKey(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setLastValidation(sourceTimeline)
    setLastPreview(null)
    setPreviewCompareMode('result')
    setPreviewCompareMs(0)
    setPreviewWatchCompleteKey(null)
    setPreviewWatchProgressMs(0)
    previewWatchSessionRef.current = null
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
  }, [sourceTimeline?.id, sourceTimeline?.row_version, localDraftKey, serverDraft.data, serverDraft.isPending])

  const durationMs = workspace.data?.duration_ms ?? 15000
  const outputFps = Math.max(1, Number(sourceTimeline?.output_spec.fps) || 24)
  const previewWatchKey = lastPreview?.preview_key && lastPreview.content_hash
    ? `${lastPreview.preview_key}:${lastPreview.content_hash}`
    : null
  const previewWatchComplete = Boolean(previewWatchKey && previewWatchCompleteKey === previewWatchKey)

  useEffect(() => {
    if (previewOpen) return
    previewWatchSessionRef.current = null
    if (!previewWatchComplete) setPreviewWatchProgressMs(0)
  }, [previewOpen, previewWatchComplete])
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
  const boundaryContinuityReviewProgress = useMemo(
    () => mainBoundaries.flatMap((boundary, index) => {
      if (!boundary.left.asset_id || !boundary.right.asset_id) return []
      const leftSequence = shotSequenceByAssetId.get(boundary.left.asset_id)
      const rightSequence = shotSequenceByAssetId.get(boundary.right.asset_id)
      const rightShotCode = shotCodeByAssetId.get(boundary.right.asset_id)
      const rightFormalShot = rightShotCode ? formalShotByCode.get(rightShotCode) : undefined
      const checks = leftSequence != null && rightSequence === leftSequence + 1
        ? CONTINUITY_CHECKS[normalizeContinuityRelation(rightFormalShot?.continuity_relation)]
        : GENERAL_CONTINUITY_CHECKS
      const outcomes = boundaryContinuityOutcomes[boundary.key] ?? {}
      const passedCount = checks.filter(check => outcomes[check.id] === 'passed').length
      const needsAdjustmentChecks = checks.filter(check => outcomes[check.id] === 'needs_adjustment')
      const needsAdjustmentCount = needsAdjustmentChecks.length
      const recheckContexts = (boundaryContinuityIssueContexts[boundary.key] ?? []).filter(context => (
        checks.some(check => check.id === context.checkId)
        && outcomes[context.checkId] !== 'needs_adjustment'
        && outcomes[context.checkId] !== 'passed'
      ))
      const recheckCount = recheckContexts.length
      const unreviewedCount = checks.length - passedCount - needsAdjustmentCount - recheckCount
      return [{
        ...boundary,
        index,
        passedCount,
        requiredCount: checks.length,
        needsAdjustmentChecks,
        needsAdjustmentCount,
        recheckContexts,
        recheckCount,
        unreviewedCount,
        unresolvedCount: needsAdjustmentCount + recheckCount + unreviewedCount,
      }]
    }),
    [boundaryContinuityIssueContexts, boundaryContinuityOutcomes, formalShotByCode, mainBoundaries, shotCodeByAssetId, shotSequenceByAssetId],
  )
  const unresolvedBoundaryContinuityReviews = boundaryContinuityReviewProgress.filter(boundary => boundary.unresolvedCount > 0)
  const passedBoundaryContinuityReviewCount = boundaryContinuityReviewProgress.length - unresolvedBoundaryContinuityReviews.length
  const unreviewedBoundaryContinuityCheckCount = boundaryContinuityReviewProgress.reduce(
    (total, boundary) => total + boundary.unreviewedCount,
    0,
  )
  const needsAdjustmentBoundaryContinuityCheckCount = boundaryContinuityReviewProgress.reduce(
    (total, boundary) => total + boundary.needsAdjustmentCount,
    0,
  )
  const recheckBoundaryContinuityCheckCount = boundaryContinuityReviewProgress.reduce(
    (total, boundary) => total + boundary.recheckCount,
    0,
  )
  const continuityReviewIssueCount = unreviewedBoundaryContinuityCheckCount
    + needsAdjustmentBoundaryContinuityCheckCount
    + recheckBoundaryContinuityCheckCount
  const activeBoundaryContinuityReview = boundaryContinuityReviewProgress.find(
    boundary => boundary.index === activeBoundaryIndex,
  ) ?? null
  const nextUnresolvedBoundaryContinuityReview = unresolvedBoundaryContinuityReviews.find(
    boundary => boundary.index > activeBoundaryIndex,
  ) ?? unresolvedBoundaryContinuityReviews[0] ?? null
  useEffect(() => {
    const currentBoundaryKeys = new Set(mainBoundaries.map(boundary => boundary.key))
    setBoundaryContinuityIssueContexts(current => {
      const retained = Object.entries(current).filter(([key]) => currentBoundaryKeys.has(key))
      return retained.length === Object.keys(current).length ? current : Object.fromEntries(retained)
    })
    setBoundaryContinuityObservations(current => {
      const retained = Object.entries(current).filter(([key]) => currentBoundaryKeys.has(key))
      return retained.length === Object.keys(current).length ? current : Object.fromEntries(retained)
    })
  }, [mainBoundaries])
  const candidateReviewFollowUpBoundaries = useMemo(
    () => mainBoundaries.flatMap((boundary, index) => {
      if (!boundary.left.asset_id || !boundary.right.asset_id) return []
      const sessionKey = boundaryCandidateReviewSessionKey(projectId, boundary.left, boundary.right, frameStepMs, outputFps)
      const session = boundaryCandidateReviewSessions[sessionKey]
      if (!session) return []
      const followUpCount = Object.values(session.comparisonOutcomes)
        .filter(outcome => outcome === 'completed' || outcome === 'shortlisted').length
        + Object.keys(session.measuredMotionEvidence)
          .filter(sourceKey => !session.comparisonOutcomes[sourceKey]).length
      return followUpCount > 0 ? [{ ...boundary, index, followUpCount }] : []
    }),
    [boundaryCandidateReviewSessions, frameStepMs, mainBoundaries, outputFps, projectId],
  )
  const nextCandidateReviewFollowUpBoundary = candidateReviewFollowUpBoundaries.find(
    boundary => boundary.index > activeBoundaryIndex,
  ) ?? candidateReviewFollowUpBoundaries[0] ?? null
  const candidateReviewFollowUpCount = candidateReviewFollowUpBoundaries.reduce(
    (total, boundary) => total + boundary.followUpCount,
    0,
  )
  const nextMainAsset = workspace.data?.available_assets.find(asset => asset.id === nextMainItem?.asset_id) ?? null
  const usedMainVideoAssetIds = useMemo(
    () => new Set(mainItems.flatMap(item => item.asset_id ? [item.asset_id] : [])),
    [mainItems],
  )
  const selectedGapFormalRecommendations = useMemo(() => {
    if (!selectedItem || selectedItem.track_type !== 'main_video' || selectedItem.asset_id) return []
    const selectedGapIndex = mainItems.findIndex(item => item.id === selectedItem.id)
    if (selectedGapIndex < 0) return []
    const sequenceForItem = (item: TimelineItem) => item.asset_id
      ? shotSequenceByAssetId.get(item.asset_id)
      : undefined
    let leftSequence: number | undefined
    for (let index = selectedGapIndex - 1; index >= 0; index -= 1) {
      leftSequence = sequenceForItem(mainItems[index])
      if (leftSequence != null) break
    }
    let rightSequence: number | undefined
    for (let index = selectedGapIndex + 1; index < mainItems.length; index += 1) {
      rightSequence = sequenceForItem(mainItems[index])
      if (rightSequence != null) break
    }
    const usedShotCodes = new Set(mainItems.flatMap(item => {
      const code = item.asset_id ? shotCodeByAssetId.get(item.asset_id) : undefined
      return code ? [code] : []
    }))
    return (workspace.data?.shot_sequence ?? [])
      .filter(shot => (
        !usedShotCodes.has(shot.shot_code)
        && (leftSequence == null || shot.sequence_number > leftSequence)
        && (rightSequence == null || shot.sequence_number < rightSequence)
      ))
      .map(shot => ({
        shot,
        assets: (workspace.data?.available_assets ?? [])
          .filter(asset => (
            asset.asset_type === 'video'
            && asset.duration_ms != null
            && asset.duration_ms > 0
            && asset.shot_code === shot.shot_code
            && !usedMainVideoAssetIds.has(asset.id)
          ))
          .sort((left, right) => left.id.localeCompare(right.id)),
      }))
      .filter(recommendation => recommendation.assets.length > 0)
      .sort((left, right) => left.shot.sequence_number - right.shot.sequence_number)
  }, [mainItems, selectedItem, shotCodeByAssetId, shotSequenceByAssetId, usedMainVideoAssetIds, workspace.data?.available_assets, workspace.data?.shot_sequence])
  const selectedGapFormalRecommendation = selectedGapFormalRecommendations[0] ?? null
  const selectedGapPrecedingExtension = useMemo(() => {
    if (!selectedItem || selectedItem.track_type !== 'main_video' || selectedItem.asset_id) return null
    const selectedGapIndex = mainItems.findIndex(item => item.id === selectedItem.id)
    const precedingItem = selectedGapIndex > 0 ? mainItems[selectedGapIndex - 1] : null
    if (
      !precedingItem?.asset_id
      || precedingItem.source_out_ms == null
      || precedingItem.asset_duration_ms == null
      || precedingItem.timeline_out_ms !== selectedItem.timeline_in_ms
    ) return null
    const gapDurationMs = selectedItem.timeline_out_ms - selectedItem.timeline_in_ms
    const availableSourceTailMs = precedingItem.asset_duration_ms - precedingItem.source_out_ms
    const extensionMs = Math.min(gapDurationMs, availableSourceTailMs)
    if (extensionMs <= 0) return null
    const remainingGapMs = gapDurationMs - extensionMs
    const safeExtensionMs = remainingGapMs > 0 && remainingGapMs < 200
      ? Math.max(0, gapDurationMs - 200)
      : extensionMs
    if (safeExtensionMs <= 0) return null
    return {
      item: precedingItem,
      extensionMs: safeExtensionMs,
      remainingGapMs: gapDurationMs - safeExtensionMs,
    }
  }, [mainItems, selectedItem])
  const selectedGapCombinedRepair = useMemo(() => {
    if (
      !selectedItem
      || !selectedGapPrecedingExtension
      || !selectedGapFormalRecommendation
      || selectedGapFormalRecommendation.assets.length !== 1
    ) return null
    const asset = selectedGapFormalRecommendation.assets[0]
    const gapAfterExtensionMs = selectedItem.timeline_out_ms - selectedItem.timeline_in_ms
      - selectedGapPrecedingExtension.extensionMs
    const insertedDurationMs = Math.min(asset.duration_ms ?? 0, gapAfterExtensionMs)
    if (insertedDurationMs < 200) return null
    return {
      asset,
      insertedDurationMs,
      remainingGapMs: gapAfterExtensionMs - insertedDurationMs,
    }
  }, [selectedGapFormalRecommendation, selectedGapPrecedingExtension, selectedItem])
  const selectedGapCompleteRepair = useMemo(() => {
    if (!selectedGapCombinedRepair || selectedGapCombinedRepair.remainingGapMs < 200) return null
    const formalShotCodes = new Set((workspace.data?.shot_sequence ?? []).map(shot => shot.shot_code))
    const fillerCandidates = (workspace.data?.available_assets ?? [])
      .filter(asset => (
        asset.asset_type === 'video'
        && asset.duration_ms != null
        && asset.duration_ms >= selectedGapCombinedRepair.remainingGapMs
        && asset.id !== selectedGapCombinedRepair.asset.id
        && !usedMainVideoAssetIds.has(asset.id)
        && (!asset.shot_code || !formalShotCodes.has(asset.shot_code))
      ))
      .sort((left, right) => left.id.localeCompare(right.id))
    if (fillerCandidates.length !== 1) return null
    return {
      asset: fillerCandidates[0],
      insertedDurationMs: selectedGapCombinedRepair.remainingGapMs,
      trimmedDurationMs: (fillerCandidates[0].duration_ms ?? 0) - selectedGapCombinedRepair.remainingGapMs,
    }
  }, [selectedGapCombinedRepair, usedMainVideoAssetIds, workspace.data?.available_assets, workspace.data?.shot_sequence])
  const selectedGapDurationMs = selectedItem?.track_type === 'main_video' && !selectedItem.asset_id
    ? selectedItem.timeline_out_ms - selectedItem.timeline_in_ms
    : 0
  const boundaryAssetReplacementTarget = boundaryAssetReplacementTargetId
    ? mainItems.find(item => item.id === boundaryAssetReplacementTargetId) ?? null
    : null
  const normalizedAssetSearch = assetSearchQuery.trim().toLocaleLowerCase('zh-CN')
  const visibleAssets = workspace.data?.available_assets.filter(asset => (
    (assetFilter === 'all' || asset.asset_type === assetFilter)
    && (!gapAssetSelection || (asset.asset_type === 'video' && !usedMainVideoAssetIds.has(asset.id)))
    && (!boundaryAssetReplacementTarget || (asset.asset_type === 'video' && !usedMainVideoAssetIds.has(asset.id)))
    && (!normalizedAssetSearch || [asset.node_key, asset.role, asset.asset_type, asset.shot_code]
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
  const autoSaveFingerprint = useMemo(
    () => editorDraftFingerprint(
      sourceTimeline,
      items,
      playheadMs,
      snapEnabled,
      timelineZoom,
      boundaryContinuityOutcomes,
      boundaryContinuityIssueContexts,
      boundaryContinuityObservations,
      boundaryCandidateReviewSessions,
    ),
    [boundaryCandidateReviewSessions, boundaryContinuityIssueContexts, boundaryContinuityObservations, boundaryContinuityOutcomes, items, playheadMs, snapEnabled, sourceTimeline, timelineZoom],
  )

  useEffect(() => {
    const currentIndex = items.findIndex(item => item.track_type === 'main_video' && playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms)
    if (playing && currentIndex >= 0 && currentIndex !== selectedIndex) setSelectedIndex(currentIndex)
  }, [playheadMs, items, playing, selectedIndex])

  useEffect(() => {
    setBoundaryActionComparisonKey(null)
    setBoundaryContinuityReadyEvidence({})
  }, [items])

  useEffect(() => {
    if (playing) setBoundaryActionComparisonKey(null)
  }, [playing])

  useEffect(() => {
    if (preservePlayheadOnReviewFocusRef.current) {
      preservePlayheadOnReviewFocusRef.current = false
      return
    }
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
    const rate = boundaryPreviewEndMs == null
      ? 1
      : boundaryReviewSession?.playbackRate
        ?? boundaryPreviewSession?.playbackRate
        ?? boundaryPreviewLoop?.playbackRate
        ?? boundaryPreviewRate
    if (videoRef.current) videoRef.current.playbackRate = rate
    Object.values(timelineAudioRefs.current).forEach(audio => {
      if (audio) audio.playbackRate = rate
    })
  }, [boundaryPreviewEndMs, boundaryPreviewLoop?.playbackRate, boundaryPreviewRate, boundaryPreviewSession?.playbackRate, boundaryReviewSession?.playbackRate, selectedItem?.id])

  useEffect(() => {
    for (const item of audioItems) {
      const audio = timelineAudioRefs.current[item.id]
      if (!audio || !item.asset_id) continue
      const active = playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms
      audio.muted = audioTrackMuted
      audio.playbackRate = boundaryPreviewEndMs == null
        ? 1
        : boundaryReviewSession?.playbackRate
          ?? boundaryPreviewSession?.playbackRate
          ?? boundaryPreviewLoop?.playbackRate
          ?? boundaryPreviewRate
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
  }, [audioItems, audioTrackMuted, boundaryPreviewEndMs, boundaryPreviewLoop?.playbackRate, boundaryPreviewRate, boundaryPreviewSession?.playbackRate, boundaryReviewSession?.playbackRate, playheadMs, playing])

  useEffect(() => {
    if (boundaryPreviewEndMs == null) setBoundaryPreviewSession(null)
  }, [boundaryPreviewEndMs])

  useEffect(() => {
    if (!playing) timelinePlaybackObservationRef.current = null
  }, [playing])

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
    if (suppressDraftWritesRef.current || !sourceTimeline || !dirty || !items.length || boundaryRollMonitor?.active) return
    const draft: LocalEditorDraft = {
      schema_version: LOCAL_DRAFT_SCHEMA,
      base_timeline_id: sourceTimeline.id,
      base_row_version: sourceTimeline.row_version,
      items,
      timeline_zoom: timelineZoom,
      snap_enabled: snapEnabled,
      playhead_ms: Math.max(0, Math.round(playheadMs)),
      boundary_continuity_outcomes: boundaryContinuityOutcomes,
      boundary_continuity_issue_contexts: boundaryContinuityIssueContexts,
      boundary_continuity_observations: boundaryContinuityObservations,
      boundary_candidate_review_sessions: boundaryCandidateReviewSessions,
      saved_at: new Date().toISOString(),
    }
    window.localStorage.setItem(localDraftKey, JSON.stringify(draft))
  }, [boundaryCandidateReviewSessions, boundaryContinuityIssueContexts, boundaryContinuityObservations, boundaryContinuityOutcomes, boundaryRollMonitor, dirty, items, localDraftKey, playheadMs, snapEnabled, sourceTimeline, timelineZoom])

  const saveCurrentEditorDraft = async () => {
    if (!sourceTimeline) throw new Error('当前没有可保存的剪辑基线。')
    if (!candidateReviewSessionsLoadedRef.current) {
      throw new Error('候选审核进度仍在恢复，暂不能保存草稿。')
    }
    return api.saveEditorDraft(projectId, sourceTimeline, {
      ...sourceTimeline.track_config,
      audio_enabled: audioItems.length > 0,
      subtitle_enabled: subtitleItems.length > 0,
      snap_enabled: snapEnabled,
      pixels_per_second: timelineZoom,
    }, items, playheadMs, boundaryContinuityOutcomes, Object.fromEntries(
      Object.entries(boundaryContinuityIssueContexts).map(([key, contexts]) => [
        key,
        contexts.map(context => ({
          check_id: context.checkId,
          check_label: context.checkLabel,
          mode: context.mode,
        })),
      ]),
    ), boundaryContinuityObservations, Object.fromEntries(
      Object.entries(boundaryCandidateReviewSessionsRef.current).map(([key, session]) => [key, {
        measured_motion_evidence: session.measuredMotionEvidence,
        comparison_outcomes: session.comparisonOutcomes,
        alternative_outcomes: session.alternativeOutcomes,
      }]),
    ) as Record<string, EditorBoundaryCandidateReviewSession>)
  }

  const autoSaveDraft = useMutation({
    mutationFn: async (fingerprint: string) => {
      const draft = await saveCurrentEditorDraft()
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
      || suppressDraftWritesRef.current
      || !dirty
      || !items.length
      || boundaryRollMonitor?.active
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
    boundaryRollMonitor,
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
      if (continuityReviewIssueCount > 0) {
        throw new Error(`仍有 ${continuityReviewIssueCount} 项镜头连续性检查未通过，不能生成可导出版本。`)
      }
      const savedDraft = await saveCurrentEditorDraft()
      const revised = await api.reviseTimelineCandidate(projectId, sourceTimeline, savedDraft.row_version, {
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
      setPreviewWatchCompleteKey(null)
      setPreviewWatchProgressMs(0)
      previewWatchSessionRef.current = null
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
      if (!previewWatchComplete) {
        throw new Error('请先以 1× 从头完整播放当前低清预览到自然结尾。')
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

  const invalidatePreviewWatchAttempt = (message: string) => {
    if (previewWatchComplete) return
    previewWatchSessionRef.current = null
    setPreviewWatchProgressMs(0)
    setPreviewReviewChecks(current => ({
      ...current,
      visualContinuity: false,
      subjectiveSync: false,
      subtitleReadability: false,
      warnings: false,
    }))
    setNotice(message)
  }

  const handleRenderedPreviewPlay = (event: ReactSyntheticEvent<HTMLVideoElement>) => {
    const video = event.currentTarget
    const source = sourceCompareRef.current
    if (source && previewCompareMode === 'compare') void source.play().catch(() => undefined)
    if (!previewWatchKey || previewWatchComplete) return
    const toleranceSeconds = Math.max(.12, 2 / Math.max(1, lastPreview?.fps ?? 24))
    if (video.playbackRate !== 1) {
      invalidatePreviewWatchAttempt('低清预览完整观看只接受 1×；请恢复正常速度并从头重新播放。')
      return
    }
    const existing = previewWatchSessionRef.current
    if (!existing) {
      if (video.currentTime > toleranceSeconds) {
        invalidatePreviewWatchAttempt('请把低清预览播放头回到开头，再以 1× 完整播放到自然结尾。')
        return
      }
      previewWatchSessionRef.current = { previewKey: previewWatchKey, lastTimeSeconds: video.currentTime }
      setPreviewWatchProgressMs(Math.round(video.currentTime * 1000))
      setNotice('正在以 1× 完整观看低清预览；暂停后可从原位置继续，跳转或倍速会要求从头重看。')
      return
    }
    if (
      existing.previewKey !== previewWatchKey
      || Math.abs(video.currentTime - existing.lastTimeSeconds) > Math.max(.35, toleranceSeconds * 2)
    ) {
      invalidatePreviewWatchAttempt('低清预览播放位置已跳转；请回到开头并以 1× 重新完整播放。')
    }
  }

  const handleRenderedPreviewTimeUpdate = (event: ReactSyntheticEvent<HTMLVideoElement>) => {
    const video = event.currentTarget
    const currentMs = Math.round(video.currentTime * 1000)
    setPreviewCompareMs(currentMs)
    if (previewWatchComplete || !previewWatchKey) return
    const session = previewWatchSessionRef.current
    if (!session) return
    const deltaSeconds = video.currentTime - session.lastTimeSeconds
    if (
      session.previewKey !== previewWatchKey
      || video.playbackRate !== 1
      || deltaSeconds < -.12
      || deltaSeconds > 1.5
    ) {
      invalidatePreviewWatchAttempt('低清预览没有保持 1× 连续播放；请回到开头重新完整观看。')
      return
    }
    session.lastTimeSeconds = video.currentTime
    setPreviewWatchProgressMs(currentMs)
  }

  const handleRenderedPreviewEnded = (event: ReactSyntheticEvent<HTMLVideoElement>) => {
    const video = event.currentTarget
    const session = previewWatchSessionRef.current
    const toleranceSeconds = Math.max(.12, 2 / Math.max(1, lastPreview?.fps ?? 24))
    if (
      !previewWatchKey
      || !session
      || session.previewKey !== previewWatchKey
      || video.playbackRate !== 1
      || !Number.isFinite(video.duration)
      || video.currentTime < video.duration - toleranceSeconds
    ) {
      invalidatePreviewWatchAttempt('低清预览没有自然播放到结尾；请回到开头并以 1× 重新完整观看。')
      return
    }
    previewWatchSessionRef.current = null
    setPreviewWatchProgressMs(lastPreview?.duration_ms ?? Math.round(video.duration * 1000))
    setPreviewWatchCompleteKey(previewWatchKey)
    setNotice('当前低清预览已完成 1× 从头到尾观看，可以逐项确认人工复核。')
  }

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
    setBoundaryContinuityOutcomes({})
    setBoundaryContinuityIssueContexts({})
    setBoundaryContinuityObservations({})
    setBoundaryContinuityReadyEvidence({})
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
      timelinePlaybackObservationRef.current = null
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setBoundaryFocusKey(boundaryKeyForItem(item))
      setBoundaryReviewSession(null)
      setPendingBoundaryPreviewKey(null)
    }
  }

  const seekTimeline = (positionMs: number) => {
    timelinePlaybackObservationRef.current = null
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
    setBoundaryActionComparisonKey(null)
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

  const focusBoundaryForReviewAt = (targetIndex: number, mode: BoundaryContinuityReviewMode) => {
    const target = mainBoundaries[targetIndex]
    if (!target) return null
    const focusItem = target.right.asset_id ? target.right : target.left
    const itemIndex = items.findIndex(item => item.id === focusItem.id)
    if (itemIndex < 0) return null
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    if (itemIndex !== selectedIndex) preservePlayheadOnReviewFocusRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setSelectedIndex(itemIndex)
    setBoundaryFocusKey(target.key)
    setBoundaryFrameComparisonKey(target.key)
    setBoundaryFrameOverlayKey(mode === 'overlay' ? target.key : null)
    setBoundaryFrameStripKey(null)
    setBoundaryActionComparisonKey(mode === 'action' ? target.key : null)
    setBoundaryCandidateGuidanceRequest(null)
    setBoundaryInspectorOpen(true)
    return target
  }

  const focusCandidateReviewFollowUpAt = (targetIndex: number) => {
    const target = focusBoundaryForReviewAt(targetIndex, 'action')
    if (!target) return
    setBoundaryCandidateGuidanceRequest({
      boundaryKey: target.key,
      checkId: 'candidate-review-follow-up',
      checkLabel: '候选审核待办',
      requestToken: Date.now(),
      intent: 'resume',
    })
    setNotice(`正在定位候选审核待办：${target.left.label} → ${target.right.label}。`)
  }

  const focusIncompleteBoundaryContinuityReviewAt = (targetIndex: number) => {
    const review = boundaryContinuityReviewProgress.find(boundary => boundary.index === targetIndex)
    const firstAdjustment = review?.needsAdjustmentChecks[0]
    const firstRecheck = review?.recheckContexts[0]
    const mode = firstAdjustment
      ? continuityReviewModeForCheckId(firstAdjustment.id)
      : firstRecheck?.mode ?? 'frames'
    const target = focusBoundaryForReviewAt(targetIndex, mode)
    if (!target) return
    if (firstAdjustment) {
      setDirty(true)
      const additions = review?.needsAdjustmentChecks.map(check => ({
        checkId: check.id,
        checkLabel: check.label,
        mode: continuityReviewModeForCheckId(check.id),
      })) ?? []
      setBoundaryContinuityIssueContexts(current => ({
        ...current,
        [target.key]: mergeBoundaryContinuityIssueContexts(current[target.key] ?? [], additions),
      }))
      if (mode === 'action') {
        setBoundaryCandidateGuidanceRequest(current => ({
          boundaryKey: target.key,
          checkId: firstAdjustment.id,
          checkLabel: firstAdjustment.label,
          requestToken: (current?.requestToken ?? 0) + 1,
          intent: 'issue',
        }))
      }
    }
    setNotice(firstAdjustment
      ? `已定位待调整项“${firstAdjustment.label}”：${target.left.label} → ${target.right.label}；已打开${continuityReviewModeLabel(mode)}。`
      : firstRecheck
        ? `已定位待复检原问题“${firstRecheck.checkLabel}”：${target.left.label} → ${target.right.label}；已重新打开${continuityReviewModeLabel(mode)}。`
        : `已定位人工连续性待处理项：${target.left.label} → ${target.right.label}；请逐项检查末帧与首帧。`)
  }

  const openBoundaryContinuityAdjustmentAt = (targetIndex: number, checkId: string, checkLabel: string) => {
    const mode = continuityReviewModeForCheckId(checkId)
    const target = focusBoundaryForReviewAt(targetIndex, mode)
    if (!target) return
    setDirty(true)
    const review = boundaryContinuityReviewProgress.find(boundary => boundary.index === targetIndex)
    const additions = review?.needsAdjustmentChecks.map(check => ({
      checkId: check.id,
      checkLabel: check.label,
      mode: continuityReviewModeForCheckId(check.id),
    })) ?? []
    if (!additions.some(context => context.checkId === checkId)) {
      additions.push({ checkId, checkLabel, mode })
    }
    setBoundaryContinuityIssueContexts(current => ({
      ...current,
      [target.key]: mergeBoundaryContinuityIssueContexts(current[target.key] ?? [], additions),
    }))
    if (mode === 'action' && boundaryContinuityOutcomes[target.key]?.[checkId] === 'needs_adjustment') {
      setBoundaryCandidateGuidanceRequest(current => ({
        boundaryKey: target.key,
        checkId,
        checkLabel,
        requestToken: (current?.requestToken ?? 0) + 1,
        intent: 'issue',
      }))
    }
    setNotice(`正在处理“${checkLabel}”：${target.left.label} → ${target.right.label}；已打开${continuityReviewModeLabel(mode)}，不会自动修改切点。`)
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
      const observedCount = timelinePlaybackObservationRef.current?.boundaries
        .filter(boundary => boundary.status === 'recorded').length ?? 0
      timelinePlaybackObservationRef.current = null
      setPlaying(false)
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setBoundaryReviewSession(null)
      setBoundaryPreviewSession(null)
      setNotice(observedCount > 0
        ? `已暂停时间线预览；本次已记录 ${observedCount} 个切点的 1× 完整上下文顺序观察。`
        : '已暂停时间线预览。')
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
    const playbackStartMs = selectedItem?.id === target.id ? playheadMs : target.timeline_in_ms
    if (selectedItem?.id !== target.id) selectItem(target)
    advancingPlaybackRef.current = false
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setBoundaryPreviewSession(null)
    const targetMainIndex = mainItems.findIndex(item => item.id === target.id)
    const reachableBoundaryKeys = new Set<string>()
    for (let index = targetMainIndex; index >= 0 && index < mainItems.length - 1; index += 1) {
      const left = mainItems[index]
      const right = mainItems[index + 1]
      if (!left.asset_id || !right.asset_id) break
      reachableBoundaryKeys.add(`${left.id}-${right.id}`)
    }
    const observationBoundaries = mainBoundaries.flatMap(boundary => {
      if (!reachableBoundaryKeys.has(boundary.key)) return []
      const evidence = boundarySequentialObservationEvidence(boundary.left, boundary.right, 1000, 1000, 1)
      if (!evidence || playbackStartMs > boundary.left.timeline_out_ms - evidence.left_context_ms) return []
      return [{
        boundaryKey: boundary.key,
        boundaryFingerprint: continuityBoundaryFingerprint(boundary.left, boundary.right),
        boundaryMs: boundary.left.timeline_out_ms,
        evidence,
        status: 'pending' as const,
      }]
    })
    timelinePlaybackObservationRef.current = {
      boundaries: observationBoundaries,
    }
    setPlaying(true)
    setNotice(observationBoundaries.length > 0
      ? `正在预览时间线：当前连续可播放范围内有 ${observationBoundaries.length} 个切点可形成 1× 完整上下文观察。`
      : '正在预览时间线：当前起播位置到下一缺口或结尾前没有可形成完整上下文观察的切点。')
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
    timelinePlaybackObservationRef.current = null
    setBoundaryReviewSession(null)
    setBoundaryPreviewSession(loop ? null : {
      boundaryKey,
      boundaryFingerprint: continuityBoundaryFingerprint(left, right),
      beforeMs: boundaryPreviewBeforeMs,
      afterMs: boundaryPreviewAfterMs,
      playbackRate: boundaryPreviewRate,
    })
    selectItem(left)
    setBoundaryFocusKey(boundaryKey)
    setPlayheadMs(startMs)
    advancingPlaybackRef.current = false
    setBoundaryPreviewEndMs(endMs)
    setBoundaryPreviewLoop(loop ? {
      boundaryKey,
      boundaryFingerprint: continuityBoundaryFingerprint(left, right),
      leftItemId: left.id,
      startMs,
      endMs,
      beforeMs: boundaryPreviewBeforeMs,
      afterMs: boundaryPreviewAfterMs,
      playbackRate: boundaryPreviewRate,
      label,
      iteration: 0,
      observationRecorded: false,
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

  useEffect(() => {
    if (!pendingBoundaryReview) return
    const boundaryIndexes = pendingBoundaryReview.keys
      .map(key => mainBoundaries.findIndex(boundary => (
        boundary.key === key && boundary.left.asset_id && boundary.right.asset_id
      )))
      .filter(index => index >= 0)
      .sort((left, right) => left - right)
    setPendingBoundaryReview(null)
    if (!boundaryIndexes.length) {
      setNotice(`${pendingBoundaryReview.scope === 'slide'
        ? '片段滑动'
        : pendingBoundaryReview.scope === 'trim'
        ? '片段裁切'
        : pendingBoundaryReview.scope === 'repair'
        ? '组合修复'
        : pendingBoundaryReview.scope === 'asset'
        ? '素材填入'
        : pendingBoundaryReview.scope === 'structure'
        ? '镜头顺序交换'
        : '撤销/重做'}成功，但更新后的受影响切点均缺少双侧画面或已不存在，未启动自动试听。`)
      return
    }
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession({
      boundaryIndexes,
      position: 0,
      skippedCount: 0,
      scope: pendingBoundaryReview.scope,
      beforeMs: boundaryPreviewBeforeMs,
      afterMs: boundaryPreviewAfterMs,
      playbackRate: boundaryPreviewRate,
    })
  }, [boundaryPreviewAfterMs, boundaryPreviewBeforeMs, boundaryPreviewRate, items, mainBoundaries, pendingBoundaryReview])

  const toggleBoundaryLoop = (left: TimelineItem, right: TimelineItem) => {
    const boundaryKey = `${left.id}-${right.id}`
    if (boundaryPreviewLoop?.boundaryKey === boundaryKey) {
      const observationRecorded = boundaryPreviewLoop.observationRecorded
      setPlaying(false)
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setNotice(observationRecorded
        ? `已停止 ${left.label} → ${right.label} 的切点循环预览；已记录 1× 完整上下文顺序观察。`
        : `已停止 ${left.label} → ${right.label} 的切点循环预览；尚未完成可登记的 1× 完整上下文观察。`)
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
    setNotice(`正在以 ${boundaryPreviewLoop.playbackRate}× 循环预览 ${boundaryPreviewLoop.label}（第 ${boundaryPreviewLoop.iteration + 1} 轮）：切前 ${previewSeconds(boundaryPreviewLoop.beforeMs)}，切后 ${previewSeconds(boundaryPreviewLoop.afterMs)}${boundaryPreviewLoop.observationRecorded ? '；首轮完整观察已登记。' : '。'}`)
  }, [boundaryPreviewLoop?.iteration])

  const toggleBoundaryReview = () => {
    if (boundaryReviewSession) {
      setPlaying(false)
      setBoundaryPreviewEndMs(null)
      setBoundaryPreviewLoop(null)
      setBoundaryReviewSession(null)
      setNotice(boundaryReviewSession.scope === 'slide'
        ? '已停止片段滑动后的前后切点试听。'
        : boundaryReviewSession.scope === 'trim'
        ? '已停止片段裁切后的受影响切点试听。'
        : boundaryReviewSession.scope === 'repair'
        ? '已停止组合修复后的新切点试听。'
        : boundaryReviewSession.scope === 'asset'
        ? '已停止素材填入后的新切点试听。'
        : boundaryReviewSession.scope === 'structure'
        ? '已停止镜头顺序交换后的新切点试听。'
        : boundaryReviewSession.scope === 'history'
        ? '已停止撤销/重做后的受影响切点试听。'
        : '已停止全时间线切点连续巡检。')
      return
    }
    if (!reviewableBoundaryIndexes.length) {
      setNotice('当前时间线没有两侧画面都已补齐的切点，无法连续巡检。')
      return
    }
    timelinePlaybackObservationRef.current = null
    videoRef.current?.pause()
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryPreviewSession(null)
    setBoundaryFrameComparisonKey(null)
    setBoundaryFrameOverlayKey(null)
    setBoundaryFrameStripKey(null)
    setBoundaryReviewSession({
      boundaryIndexes: reviewableBoundaryIndexes,
      position: 0,
      skippedCount: mainBoundaries.length - reviewableBoundaryIndexes.length,
      scope: 'timeline',
      beforeMs: boundaryPreviewBeforeMs,
      afterMs: boundaryPreviewAfterMs,
      playbackRate: boundaryPreviewRate,
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
    const startMs = Math.max(boundary.left.timeline_in_ms, boundaryMs - boundaryReviewSession.beforeMs)
    const endMs = Math.min(boundary.right.timeline_out_ms, boundaryMs + boundaryReviewSession.afterMs)
    advancingPlaybackRef.current = false
    setSelectedIndex(leftIndex)
    setBoundaryFocusKey(boundary.key)
    setPlayheadMs(startMs)
    setBoundaryPreviewEndMs(endMs)
    setBoundaryPreviewLoop(null)
    setPlaying(true)
    const reviewLabel = boundaryReviewSession.scope === 'slide'
      ? '片段滑动后试听'
      : boundaryReviewSession.scope === 'trim'
      ? '片段裁切后试听'
      : boundaryReviewSession.scope === 'repair'
      ? '组合修复后试听'
      : boundaryReviewSession.scope === 'asset'
      ? '素材填入后试听'
      : boundaryReviewSession.scope === 'structure'
      ? '镜头顺序交换后试听'
      : boundaryReviewSession.scope === 'history'
      ? '撤销/重做后试听'
      : '连续巡检'
    setNotice(`${reviewLabel} ${boundaryReviewSession.position + 1}/${boundaryReviewSession.boundaryIndexes.length}：正在以 ${boundaryReviewSession.playbackRate}× 预览 ${boundary.left.label} → ${boundary.right.label}。`)
  }, [boundaryReviewSession, items, mainBoundaries])

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

  const historySnapshot = (snapshotItems: TimelineItem[]): EditorHistorySnapshot => ({
    items: snapshotItems,
    boundaryContinuityOutcomes,
    boundaryContinuityIssueContexts,
  })

  const commitItems = (nextItems: TimelineItem[], message: string, selectedId?: string | null) => {
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setHistory(rows => [...rows.slice(-49), historySnapshot(items)])
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
    setBoundaryActionComparisonKey(null)
    setBoundaryCandidateGuidanceRequest(null)
    setBoundaryFocusKey(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setBoundaryRollMonitor(null)
    setPendingBoundaryReview(null)
    setBoundaryContinuityOutcomes({})
    setBoundaryContinuityIssueContexts({})
    setBoundaryContinuityObservations({})
    setBoundaryContinuityReadyEvidence({})
  }

  const undo = () => {
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setPendingBoundaryReview(null)
    const previous = history[history.length - 1]
    if (!previous) return
    const affectedBoundaryKeys = changedContinuityBoundaryKeys(items, previous.items)
    setFuture(rows => [historySnapshot(items), ...rows].slice(0, 50))
    setHistory(rows => rows.slice(0, -1))
    setItems(previous.items)
    setBoundaryContinuityOutcomes(current => restoreBoundaryStateForKeys(
      current,
      previous.boundaryContinuityOutcomes,
      affectedBoundaryKeys,
    ))
    setBoundaryContinuityIssueContexts(current => restoreBoundaryStateForKeys(
      current,
      previous.boundaryContinuityIssueContexts,
      affectedBoundaryKeys,
    ))
    if (affectedBoundaryKeys.size > 0) {
      setPendingBoundaryReview({ keys: [...affectedBoundaryKeys], scope: 'history' })
    }
    setDirty(true)
    setSelectedIndex(index => Math.min(index, Math.max(0, previous.items.length - 1)))
    setNotice('已撤销上一步本地剪辑操作；相关人工连续性结果也已恢复。')
  }

  const redo = () => {
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setPendingBoundaryReview(null)
    const next = future[0]
    if (!next) return
    const affectedBoundaryKeys = changedContinuityBoundaryKeys(items, next.items)
    setHistory(rows => [...rows.slice(-49), historySnapshot(items)])
    setFuture(rows => rows.slice(1))
    setItems(next.items)
    setBoundaryContinuityOutcomes(current => restoreBoundaryStateForKeys(
      current,
      next.boundaryContinuityOutcomes,
      affectedBoundaryKeys,
    ))
    setBoundaryContinuityIssueContexts(current => restoreBoundaryStateForKeys(
      current,
      next.boundaryContinuityIssueContexts,
      affectedBoundaryKeys,
    ))
    if (affectedBoundaryKeys.size > 0) {
      setPendingBoundaryReview({ keys: [...affectedBoundaryKeys], scope: 'history' })
    }
    setDirty(true)
    setSelectedIndex(index => Math.min(index, Math.max(0, next.items.length - 1)))
    setNotice('已恢复下一步本地剪辑操作；相关人工连续性结果也已随编辑重现。')
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

  const swapBoundaryToFormalOrder = (left: TimelineItem, right: TimelineItem) => {
    if (blockMainTrackEdit(left)) return
    const leftIndex = mainItems.findIndex(item => item.id === left.id)
    const rightIndex = mainItems.findIndex(item => item.id === right.id)
    const leftSequence = left.asset_id ? shotSequenceByAssetId.get(left.asset_id) : undefined
    const rightSequence = right.asset_id ? shotSequenceByAssetId.get(right.asset_id) : undefined
    if (leftIndex < 0 || rightIndex !== leftIndex + 1 || leftSequence == null || rightSequence == null || leftSequence <= rightSequence) {
      setNotice('当前两镜已不再是可确定交换的正式分镜倒序边界；时间线没有修改。')
      return
    }
    const reordered = [...mainItems]
    ;[reordered[leftIndex], reordered[rightIndex]] = [reordered[rightIndex], reordered[leftIndex]]
    const normalized = normalizeMainTrack(reordered, durationMs)
    const reconciled = reconcileStructuralTransitions(mainItems, normalized)
    const affectedIds = new Set([left.id, right.id])
    const reviewKeys = reconciled.rows.slice(0, -1).flatMap((row, index) => {
      const next = reconciled.rows[index + 1]
      return row.asset_id && next.asset_id && (affectedIds.has(row.id) || affectedIds.has(next.id))
        ? [`${row.id}-${next.id}`]
        : []
    })
    resetStructuralPreviewState()
    setBoundaryAssetReplacementTargetId(null)
    commitItems(
      replaceMainTrack(items, reconciled.rows),
      `已按正式分镜顺序把 ${left.label} → ${right.label} 原子交换为 ${right.label} → ${left.label}${reconciled.resetBoundaryCount ? `；${reconciled.resetBoundaryCount} 组失效的成对转场已恢复为直接切换` : ''}，正在复检新切点。`,
      right.id,
    )
    if (reviewKeys.length) setPendingBoundaryReview({ keys: reviewKeys, scope: 'structure' })
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
    const reconciled = reconcileStructuralTransitions(mainItems, normalized)
    const targetIndex = mainItems.findIndex(item => item.id === target.id)
    const invalidatedBoundaryKeys = [
      targetIndex > 0 ? `${mainItems[targetIndex - 1].id}-${target.id}` : null,
      targetIndex >= 0 && targetIndex < mainItems.length - 1 ? `${target.id}-${mainItems[targetIndex + 1].id}` : null,
    ].filter((key): key is string => Boolean(key))
    const replacementIndex = reconciled.rows.findIndex(item => item.id === replacement.id)
    const replacementBoundaryKeys = [
      replacementIndex > 0 && reconciled.rows[replacementIndex - 1].asset_id
        ? `${reconciled.rows[replacementIndex - 1].id}-${replacement.id}`
        : null,
      replacementIndex >= 0
        && replacementIndex < reconciled.rows.length - 1
        && reconciled.rows[replacementIndex + 1].asset_id
        ? `${replacement.id}-${reconciled.rows[replacementIndex + 1].id}`
        : null,
    ].filter((key): key is string => Boolean(key))
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryFrameComparisonKey(null)
    setBoundaryFrameOverlayKey(null)
    setBoundaryFrameStripKey(null)
    setBoundaryActionComparisonKey(null)
    setBoundaryFocusKey(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setBoundaryRollMonitor(null)
    setPendingBoundaryReview(null)
    setBoundaryContinuityOutcomes(current => Object.fromEntries(
      Object.entries(current).filter(([key]) => !invalidatedBoundaryKeys.includes(key)),
    ))
    setBoundaryContinuityIssueContexts(current => Object.fromEntries(
      Object.entries(current).filter(([key]) => !invalidatedBoundaryKeys.includes(key)),
    ))
    setBoundaryContinuityObservations(current => Object.fromEntries(
      Object.entries(current).filter(([key]) => !invalidatedBoundaryKeys.includes(key)),
    ))
    setBoundaryContinuityReadyEvidence({})
    commitItems(
      replaceMainTrack(items, reconciled.rows),
      asset.duration_ms > replacementDuration
        ? `已把 ${replacement.label} 投放到时间线，按 ${seconds(replacementDuration)} 缺口裁切并准备试听新切点。`
        : `已把 ${replacement.label} 投放到时间线并准备试听新切点。`,
      replacement.id,
    )
    setDraggedAssetId(null)
    setGapAssetSelection(false)
    setBoundaryAssetReplacementTargetId(null)
    if (replacementBoundaryKeys.length) {
      setPendingBoundaryReview({ keys: replacementBoundaryKeys, scope: 'asset' })
    }
  }

  const startGapAssetSelection = (preferredShotCode?: string) => {
    if (blockMainTrackEdit()) return
    setAssetFilter('video')
    setGapAssetSelection(true)
    setAssetSearchOpen(Boolean(preferredShotCode))
    setAssetSearchQuery(preferredShotCode ?? '')
    const available = workspace.data?.available_assets.filter(asset => (
      asset.asset_type === 'video'
      && !usedMainVideoAssetIds.has(asset.id)
      && (!preferredShotCode || asset.shot_code === preferredShotCode)
    )) ?? []
    setNotice(available.length
      ? preferredShotCode
        ? `素材箱已定位 ${available.length} 个属于正式分镜 ${preferredShotCode} 的未使用视频；请选择一个填入当前缺口。`
        : `素材箱已只显示 ${available.length} 个未用于主画面的已批准视频；点击即可填入当前缺口。`
      : preferredShotCode
        ? `正式分镜 ${preferredShotCode} 当前没有可用的未使用视频。`
        : '当前没有未使用的已批准视频。请返回生产流程生成补充镜头，或先分析缩短目标时长的影响。')
  }

  const startBoundaryAssetReplacement = (target: TimelineItem, sideLabel: '前镜' | '后镜') => {
    if (blockMainTrackEdit(target)) return
    selectItem(target)
    setBoundaryActionComparisonKey(null)
    setBoundaryCandidateGuidanceRequest(null)
    setBoundaryAssetReplacementTargetId(target.id)
    setGapAssetSelection(false)
    setAssetFilter('video')
    setAssetSearchOpen(false)
    setAssetSearchQuery('')
    const availableCount = workspace.data?.available_assets.filter(asset => (
      asset.asset_type === 'video' && !usedMainVideoAssetIds.has(asset.id)
    )).length ?? 0
    setNotice(availableCount
      ? `素材箱已进入${sideLabel}替换：只显示 ${availableCount} 个未用于主画面的已批准视频；点击素材后才替换 ${target.label} 并自动复检新切点。`
      : `当前没有未使用的已批准视频可替换${sideLabel} ${target.label}；请返回生产流程生成补充镜头，或调整现有镜头结构。`)
  }

  const extendPrecedingItemIntoSelectedGap = () => {
    if (!selectedItem || !selectedGapPrecedingExtension || blockMainTrackEdit(selectedGapPrecedingExtension.item)) return
    const { item, extensionMs, remainingGapMs } = selectedGapPrecedingExtension
    const extendedSourceOutMs = (item.source_out_ms ?? 0) + extensionMs
    const extendedRows = mainItems.flatMap(row => {
      if (row.id === item.id) return [{
        ...row,
        source_out_ms: extendedSourceOutMs,
        timeline_out_ms: row.timeline_out_ms + extensionMs,
      }]
      if (row.id !== selectedItem.id) return [row]
      return remainingGapMs > 0 ? [{
        ...row,
        timeline_in_ms: row.timeline_in_ms + extensionMs,
        timeline_out_ms: row.timeline_out_ms,
      }] : []
    })
    const normalized = normalizeMainTrack(extendedRows, durationMs)
    const reconciled = reconcileStructuralTransitions(mainItems, normalized)
    commitItems(
      replaceMainTrack(items, reconciled.rows),
      `已把 ${item.label} 延长 ${seconds(extensionMs)} 到源时点 ${timecode(extendedSourceOutMs, outputFps)}，并等量缩短所选缺口${remainingGapMs > 0 ? `至 ${seconds(remainingGapMs)}` : '至 0 秒'}；请重新检查受影响切点。`,
      item.id,
    )
    queueTrimBoundaryReview(item, 'end', items)
  }

  const applySelectedGapCombinedRepair = (complete = false) => {
    if (
      !selectedItem
      || !selectedGapPrecedingExtension
      || !selectedGapFormalRecommendation
      || !selectedGapCombinedRepair
      || (complete && !selectedGapCompleteRepair)
      || blockMainTrackEdit(selectedGapPrecedingExtension.item)
    ) return
    const { item, extensionMs } = selectedGapPrecedingExtension
    const { asset, insertedDurationMs, remainingGapMs } = selectedGapCombinedRepair
    const extendedSourceOutMs = (item.source_out_ms ?? 0) + extensionMs
    const fillerItem: TimelineItem | null = complete && selectedGapCompleteRepair ? {
      ...selectedItem,
      id: `prototype-${selectedGapCompleteRepair.asset.id}-${Date.now()}`,
      asset_id: selectedGapCompleteRepair.asset.id,
      asset_state: selectedGapCompleteRepair.asset.state,
      asset_type: selectedGapCompleteRepair.asset.asset_type,
      asset_duration_ms: selectedGapCompleteRepair.asset.duration_ms,
      label: selectedGapCompleteRepair.asset.node_key ?? selectedGapCompleteRepair.asset.role,
      gap_reason: null,
      source_in_ms: 0,
      source_out_ms: selectedGapCompleteRepair.insertedDurationMs,
      timeline_in_ms: 0,
      timeline_out_ms: selectedGapCompleteRepair.insertedDurationMs,
      transform: { fit: 'cover', transition_in: { type: 'cut', duration_ms: 0 }, transition_out: { type: 'cut', duration_ms: 0 } },
    } : null
    const insertedItem: TimelineItem = {
      ...selectedItem,
      id: `prototype-${asset.id}-${Date.now()}`,
      asset_id: asset.id,
      asset_state: asset.state,
      asset_type: asset.asset_type,
      asset_duration_ms: asset.duration_ms,
      label: asset.node_key ?? asset.role,
      gap_reason: null,
      source_in_ms: 0,
      source_out_ms: insertedDurationMs,
      timeline_in_ms: 0,
      timeline_out_ms: insertedDurationMs,
      transform: { fit: 'cover', transition_in: { type: 'cut', duration_ms: 0 }, transition_out: { type: 'cut', duration_ms: 0 } },
    }
    const repairedRows = mainItems.flatMap(row => {
      if (row.id === item.id) return [{
        ...row,
        source_out_ms: extendedSourceOutMs,
        timeline_out_ms: row.timeline_out_ms + extensionMs,
      }]
      if (row.id !== selectedItem.id) return [row]
      return [
        insertedItem,
        ...(fillerItem ? [fillerItem] : []),
        ...(!fillerItem && remainingGapMs > 0 ? [{
          ...row,
          timeline_in_ms: 0,
          timeline_out_ms: remainingGapMs,
        }] : []),
      ]
    })
    const sortable = repairedRows
      .map((row, index) => ({
        row,
        index,
        sequence: row.asset_id ? shotSequenceByAssetId.get(row.asset_id) : undefined,
      }))
      .filter((entry): entry is { row: TimelineItem; index: number; sequence: number } => entry.sequence != null)
      .sort((left, right) => left.sequence - right.sequence || left.index - right.index)
    let sortableIndex = 0
    const reorderedRows = repairedRows.map(row => (
      row.asset_id && shotSequenceByAssetId.has(row.asset_id)
        ? sortable[sortableIndex++].row
        : row
    ))
    const normalized = normalizeMainTrack(reorderedRows, durationMs)
    const reconciled = reconcileStructuralTransitions(mainItems, normalized)
    const repairedBoundaryKeys = reconciled.rows.slice(0, -1).flatMap((left, index) => {
      const right = reconciled.rows[index + 1]
      return left.asset_id && right.asset_id ? [`${left.id}-${right.id}`] : []
    })
    resetStructuralPreviewState()
    commitItems(
      replaceMainTrack(items, reconciled.rows),
      fillerItem && selectedGapCompleteRepair
        ? `已一次完整修复：${item.label} 延长 ${seconds(extensionMs)}、补入正式分镜 ${selectedGapFormalRecommendation.shot.shot_code}、用 ${fillerItem.label} 覆盖剩余 ${seconds(selectedGapCompleteRepair.insertedDurationMs)} 并按正式顺序整理；画面缺口已补齐，正在复检全部新切点。`
        : `已组合修复：${item.label} 延长 ${seconds(extensionMs)}、补入正式分镜 ${selectedGapFormalRecommendation.shot.shot_code} 并按正式顺序整理；剩余缺口 ${seconds(remainingGapMs)}，正在复检新切点。`,
      fillerItem?.id ?? insertedItem.id,
    )
    setGapAssetSelection(false)
    setDraggedAssetId(null)
    if (repairedBoundaryKeys.length) setPendingBoundaryReview({ keys: repairedBoundaryKeys, scope: 'repair' })
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
      setHistory(rows => [...rows.slice(-49), historySnapshot(originalItems)])
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
      setHistory(rows => [...rows.slice(-49), historySnapshot(originalItems)])
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
    const currentTransition = selectedItem.transform[key] as { type?: string; duration_ms?: number } | undefined
    if (
      (currentTransition?.type ?? 'cut') === type
      && (currentTransition?.duration_ms ?? 0) === nextDuration
    ) return
    const mainPosition = mainItems.findIndex(item => item.id === selectedItem.id)
    const left = key === 'transition_in' ? mainItems[mainPosition - 1] : selectedItem
    const right = key === 'transition_in' ? selectedItem : mainItems[mainPosition + 1]
    const boundaryKey = left && right ? `${left.id}-${right.id}` : null
    videoRef.current?.pause()
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryReview(null)
    setPendingBoundaryPreviewKey(null)
    if (boundaryKey) {
      setBoundaryContinuityOutcomes(current => Object.fromEntries(
        Object.entries(current).filter(([candidate]) => candidate !== boundaryKey),
      ))
    }
    commitItems(items.map(item => item.id === selectedItem.id
      ? { ...item, transform: { ...item.transform, [key]: { type, duration_ms: nextDuration } } }
      : item), `${selectedItem.label} 的${key === 'transition_in' ? '入场' : '出场'}已改为${type === 'fade' ? `${seconds(nextDuration)} 淡${key === 'transition_in' ? '入' : '出'}` : '直接切换'}。`, selectedItem.id)
    if (boundaryKey && left && right && left.asset_id && right.asset_id && left.timeline_out_ms === right.timeline_in_ms) {
      setPendingBoundaryPreviewKey(boundaryKey)
    } else {
      setNotice(`${selectedItem.label} 的${key === 'transition_in' ? '入场' : '出场'}已更新；当前没有双侧完整画面的相邻切点，未启动自动试听。`)
    }
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
    const currentLeftTransition = left.transform.transition_out as { type?: string; duration_ms?: number } | undefined
    const currentRightTransition = right.transform.transition_in as { type?: string; duration_ms?: number } | undefined
    if (
      (currentLeftTransition?.type ?? 'cut') === type
      && (currentLeftTransition?.duration_ms ?? 0) === durationMs
      && (currentRightTransition?.type ?? 'cut') === type
      && (currentRightTransition?.duration_ms ?? 0) === durationMs
    ) return
    const boundaryKey = `${left.id}-${right.id}`
    videoRef.current?.pause()
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryReview(null)
    setPendingBoundaryPreviewKey(null)
    setBoundaryContinuityOutcomes(current => Object.fromEntries(
      Object.entries(current).filter(([candidate]) => candidate !== boundaryKey),
    ))
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
    setPendingBoundaryPreviewKey(boundaryKey)
  }

  const buildRolledBoundaryItems = (
    baseItems: TimelineItem[],
    left: TimelineItem,
    right: TimelineItem,
    requestedDeltaMs: number,
  ) => {
    const baseLeft = baseItems.find(item => item.id === left.id)
    const baseRight = baseItems.find(item => item.id === right.id)
    if (
      !baseLeft?.asset_id
      || !baseRight?.asset_id
      || baseLeft.timeline_out_ms !== baseRight.timeline_in_ms
      || baseLeft.source_in_ms == null
      || baseLeft.source_out_ms == null
      || baseRight.source_in_ms == null
      || baseRight.source_out_ms == null
    ) return null
    const leftDuration = baseLeft.source_out_ms - baseLeft.source_in_ms
    const rightDuration = baseRight.source_out_ms - baseRight.source_in_ms
    const minimumLeftDuration = minimumVideoDurationForTransitions(baseLeft)
    const minimumRightDuration = minimumVideoDurationForTransitions(baseRight)
    const minimumDelta = Math.max(
      -(leftDuration - minimumLeftDuration),
      -baseRight.source_in_ms,
    )
    const maximumDelta = Math.min(
      (baseLeft.asset_duration_ms ?? baseLeft.source_out_ms) - baseLeft.source_out_ms,
      rightDuration - minimumRightDuration,
    )
    const deltaMs = Math.max(minimumDelta, Math.min(maximumDelta, requestedDeltaMs))
    const nextBoundaryMs = baseLeft.timeline_out_ms + deltaMs
    return {
      nextItems: deltaMs ? baseItems.map(item => {
        if (item.id === baseLeft.id) {
          return {
            ...item,
            source_out_ms: baseLeft.source_out_ms! + deltaMs,
            timeline_out_ms: nextBoundaryMs,
          }
        }
        if (item.id === baseRight.id) {
          return {
            ...item,
            source_in_ms: baseRight.source_in_ms! + deltaMs,
            timeline_in_ms: nextBoundaryMs,
          }
        }
        return item
      }) : baseItems,
      deltaMs,
      nextBoundaryMs,
      minimumDelta,
      maximumDelta,
    }
  }

  const rollBoundary = (left: TimelineItem, right: TimelineItem, requestedDeltaMs: number) => {
    if (blockMainTrackEdit(left)) return false
    const result = buildRolledBoundaryItems(items, left, right, requestedDeltaMs)
    if (!result) {
      setNotice('只有紧邻且两侧都有完整源区间的画面，才能滚动剪辑切点。')
      return false
    }
    const { deltaMs, nextBoundaryMs, nextItems } = result
    if (!deltaMs) {
      setNotice(requestedDeltaMs < 0
        ? '切点已到可前移边界：前镜不能再缩短，或后镜源入点已到素材开头。'
        : '切点已到可后移边界：前镜没有更多源画面，或后镜不能再缩短。')
      return false
    }
    const boundaryKey = `${left.id}-${right.id}`
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryContinuityOutcomes(current => {
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

  const showBoundaryRollMonitor = (
    left: TimelineItem,
    right: TimelineItem,
    result: NonNullable<ReturnType<typeof buildRolledBoundaryItems>>,
    active: boolean,
  ) => {
    const nextLeft = result.nextItems.find(item => item.id === left.id)!
    const nextRight = result.nextItems.find(item => item.id === right.id)!
    setBoundaryRollMonitor({
      boundaryKey: `${left.id}-${right.id}`,
      active,
      left: nextLeft,
      right: nextRight,
      leftSourceTimeMs: Math.max(nextLeft.source_in_ms!, nextLeft.source_out_ms! - frameStepMs),
      rightSourceTimeMs: nextRight.source_in_ms!,
      deltaMs: result.deltaMs,
    })
  }

  const previewBoundaryRollMonitor = (left: TimelineItem, right: TimelineItem) => {
    const result = buildRolledBoundaryItems(items, left, right, 0)
    if (result) showBoundaryRollMonitor(left, right, result, false)
  }

  const beginBoundaryRoll = (
    event: ReactPointerEvent<HTMLElement>,
    left: TimelineItem,
    right: TimelineItem,
  ) => {
    event.currentTarget.focus()
    event.preventDefault()
    event.stopPropagation()
    if (blockMainTrackEdit(left)) return
    const originalItems = items
    const initial = buildRolledBoundaryItems(originalItems, left, right, 0)
    if (!initial) {
      setNotice('只有紧邻且两侧都有完整源区间的画面，才能拖动滚动剪辑切点。')
      return
    }
    if (initial.minimumDelta === 0 && initial.maximumDelta === 0) {
      setNotice('该切点两个方向都已到素材或最短时长边界，暂时无法拖动。')
      return
    }
    videoRef.current?.pause()
    advancingPlaybackRef.current = true
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setPendingBoundaryReview(null)
    const boundaryKey = `${left.id}-${right.id}`
    const startX = event.clientX
    const originalSelectedIndex = selectedIndex
    let latest = initial
    const updateMonitor = (result: typeof initial) => showBoundaryRollMonitor(left, right, result, true)
    updateMonitor(initial)
    const onMove = (moveEvent: PointerEvent) => {
      const rawDeltaMs = ((moveEvent.clientX - startX) / timelineZoom) * 1000
      const requestedDeltaMs = snapEnabled
        ? Math.round(rawDeltaMs / snapIntervalMs) * snapIntervalMs
        : Math.round(rawDeltaMs)
      const result = buildRolledBoundaryItems(originalItems, left, right, requestedDeltaMs)
      if (!result) return
      latest = result
      updateMonitor(result)
      setItems(result.nextItems)
      setPlayheadMs(result.nextBoundaryMs)
      if (result.deltaMs) {
        const rightIndex = result.nextItems.findIndex(item => item.id === right.id)
        if (rightIndex >= 0) setSelectedIndex(rightIndex)
      } else setSelectedIndex(originalSelectedIndex)
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      window.removeEventListener('keydown', onKeyDown)
    }
    const onUp = () => {
      cleanup()
      setBoundaryRollMonitor(null)
      if (!latest.deltaMs) {
        advancingPlaybackRef.current = false
        setItems(originalItems)
        setPlayheadMs(initial.nextBoundaryMs)
        setSelectedIndex(originalSelectedIndex)
        return
      }
      setHistory(rows => [...rows.slice(-49), historySnapshot(originalItems)])
      setFuture([])
      setItems(latest.nextItems)
      setDirty(true)
      setSelectedIndex(Math.max(0, latest.nextItems.findIndex(item => item.id === right.id)))
      setBoundaryFocusKey(boundaryKey)
      setBoundaryContinuityOutcomes(current => {
        const next = { ...current }
        delete next[boundaryKey]
        return next
      })
      setNotice(`已把 ${left.label} → ${right.label} 的切点${latest.deltaMs < 0 ? '前移' : '后移'} ${timecode(Math.abs(latest.deltaMs), outputFps)}；本次拖动只记录一次撤销，正在自动试听。`)
      setPendingBoundaryPreviewKey(boundaryKey)
    }
    const onCancel = () => {
      cleanup()
      setBoundaryRollMonitor(null)
      advancingPlaybackRef.current = false
      setItems(originalItems)
      setPlayheadMs(initial.nextBoundaryMs)
      setSelectedIndex(originalSelectedIndex)
      setNotice('切点拖动已取消，时间线和撤销历史保持不变。')
    }
    const onKeyDown = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key !== 'Escape') return
      keyEvent.preventDefault()
      onCancel()
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    window.addEventListener('keydown', onKeyDown)
  }

  const slipBoundaryItem = (item: TimelineItem, requestedDeltaMs: number, focusTimelineMs: number, previewBoundaryKey: string) => {
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
    setBoundaryContinuityOutcomes(current => Object.fromEntries(
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
    setPendingBoundaryPreviewKey(previewBoundaryKey)
  }

  const slipBoundaryPair = (
    left: TimelineItem,
    right: TimelineItem,
    requestedLeftDeltaMs: number,
    requestedRightDeltaMs: number,
    previewBoundaryKey: string,
  ) => {
    if (blockMainTrackEdit(left)) return
    const completeSourceItem = (item: TimelineItem) => Boolean(
      item.asset_id
      && item.source_in_ms != null
      && item.source_out_ms != null
      && item.asset_duration_ms != null,
    )
    if (!completeSourceItem(left) || !completeSourceItem(right)) {
      setNotice('前镜和后镜都必须具备完整源区间与素材时长，才能一次应用双方相位。')
      return
    }
    const clampDelta = (item: TimelineItem, requestedDeltaMs: number) => Math.max(
      -item.source_in_ms!,
      Math.min(item.asset_duration_ms! - item.source_out_ms!, requestedDeltaMs),
    )
    const leftDeltaMs = clampDelta(left, requestedLeftDeltaMs)
    const rightDeltaMs = clampDelta(right, requestedRightDeltaMs)
    if (!leftDeltaMs || !rightDeltaMs) {
      setNotice('双方都产生合法相位变化后，才能作为一个组合一次应用。')
      return
    }
    const affectedBoundaryKeys = new Set<string>()
    for (const item of [left, right]) {
      const itemPosition = mainItems.findIndex(row => row.id === item.id)
      if (itemPosition > 0) affectedBoundaryKeys.add(`${mainItems[itemPosition - 1].id}-${item.id}`)
      if (itemPosition >= 0 && itemPosition < mainItems.length - 1) {
        affectedBoundaryKeys.add(`${item.id}-${mainItems[itemPosition + 1].id}`)
      }
    }
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryContinuityOutcomes(current => Object.fromEntries(
      Object.entries(current).filter(([key]) => !affectedBoundaryKeys.has(key)),
    ))
    setPlayheadMs(right.timeline_in_ms)
    commitItems(
      items.map(row => row.id === left.id
        ? {
          ...row,
          source_in_ms: left.source_in_ms! + leftDeltaMs,
          source_out_ms: left.source_out_ms! + leftDeltaMs,
        }
        : row.id === right.id
        ? {
          ...row,
          source_in_ms: right.source_in_ms! + rightDeltaMs,
          source_out_ms: right.source_out_ms! + rightDeltaMs,
        }
        : row),
      `已把 ${left.label} 与 ${right.label} 的试调相位作为一个组合应用；成片位置和时长不变，本次只记录一次撤销，正在自动试听。`,
      right.id,
    )
    setBoundaryFocusKey(previewBoundaryKey)
    setPendingBoundaryPreviewKey(previewBoundaryKey)
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
    setBoundaryContinuityOutcomes(current => Object.fromEntries(
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
    setPendingBoundaryReview({ keys: [...affectedBoundaryKeys], scope: 'slide' })
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

  const queueTrimBoundaryReview = (
    item: TimelineItem,
    edge: 'start' | 'end',
    baseItems: TimelineItem[],
  ) => {
    const baseMainItems = baseItems.filter(row => row.track_type === 'main_video')
    const itemPosition = baseMainItems.findIndex(row => row.id === item.id)
    if (itemPosition < 0) return
    const previous = itemPosition > 0 ? baseMainItems[itemPosition - 1] : null
    const next = itemPosition < baseMainItems.length - 1 ? baseMainItems[itemPosition + 1] : null
    const affectedBoundaryKeys = [
      edge === 'start' && previous ? `${previous.id}-${item.id}` : null,
      next ? `${item.id}-${next.id}` : null,
    ].filter((key): key is string => Boolean(key))
    setBoundaryContinuityOutcomes(current => Object.fromEntries(
      Object.entries(current).filter(([key]) => !affectedBoundaryKeys.includes(key)),
    ))
    const reviewableBoundaryKeys = [
      edge === 'start' && previous?.asset_id && item.asset_id ? `${previous.id}-${item.id}` : null,
      item.asset_id && next?.asset_id ? `${item.id}-${next.id}` : null,
    ].filter((key): key is string => Boolean(key))
    if (reviewableBoundaryKeys.length === 1) {
      setPendingBoundaryPreviewKey(reviewableBoundaryKeys[0])
    } else if (reviewableBoundaryKeys.length > 1) {
      setPendingBoundaryReview({ keys: reviewableBoundaryKeys, scope: 'trim' })
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
    setPendingBoundaryPreviewKey(null)
    setPendingBoundaryReview(null)
    commitItems(
      result.items,
      `已用键盘把${edge === 'start' ? '左' : '右'}侧裁切到源时点 ${timecode(edge === 'start' ? result.sourceIn : result.sourceOut, outputFps)}。`,
      item.id,
    )
    queueTrimBoundaryReview(item, edge, items)
  }

  const beginTrim = (event: ReactPointerEvent<HTMLElement>, item: TimelineItem, edge: 'start' | 'end') => {
    event.currentTarget.focus()
    event.preventDefault()
    event.stopPropagation()
    if (!item.asset_id || blockMainTrackEdit(item)) return
    setPlaying(false)
    setBoundaryPreviewEndMs(null)
    setBoundaryPreviewLoop(null)
    setBoundaryReviewSession(null)
    setPendingBoundaryPreviewKey(null)
    setPendingBoundaryReview(null)
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
      setHistory(rows => [...rows.slice(-49), historySnapshot(originalItems)])
      setFuture([])
      setDirty(true)
      setNotice(`已拖动${edge === 'start' ? '左' : '右'}边缘裁切片段，后续片段自动波纹对齐。`)
      queueTrimBoundaryReview(item, edge, originalItems)
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
      const observedCount = timelinePlaybackObservationRef.current?.boundaries
        .filter(boundary => boundary.status === 'recorded').length ?? 0
      timelinePlaybackObservationRef.current = null
      setPlayheadMs(durationMs)
      setPlaying(false)
      setNotice(observedCount > 0
        ? `时间线预览播放完成；已记录 ${observedCount} 个切点的 1× 完整上下文顺序观察，人工连续性检查仍需逐项确认。`
        : '时间线预览播放完成。')
      return
    }
    selectItem(next, true)
    if (!next.asset_id) {
      const observedCount = timelinePlaybackObservationRef.current?.boundaries
        .filter(boundary => boundary.status === 'recorded').length ?? 0
      timelinePlaybackObservationRef.current = null
      setPlaying(false)
      setNotice(observedCount > 0
        ? `播放到缺口：此前已记录 ${observedCount} 个切点的 1× 完整上下文顺序观察；请先选择一种补齐方式。`
        : '播放到缺口：需要先选择一种补齐方式。')
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
      if (boundaryPreviewEndMs == null && timelinePlaybackObservationRef.current) {
        for (const candidate of timelinePlaybackObservationRef.current.boundaries) {
          if (candidate.status !== 'pending') continue
          if (timelinePosition < candidate.boundaryMs + candidate.evidence.right_context_ms - boundaryLeadMs) continue
          const currentBoundary = mainBoundaries.find(boundary => boundary.key === candidate.boundaryKey)
          if (
            !currentBoundary
            || continuityBoundaryFingerprint(currentBoundary.left, currentBoundary.right) !== candidate.boundaryFingerprint
          ) {
            candidate.status = 'invalid'
            continue
          }
          candidate.status = 'recorded'
          recordBoundaryContinuityReadyEvidence(
            JSON.stringify([candidate.boundaryKey, candidate.boundaryFingerprint]),
            'action-sequence-realtime-context',
            candidate.evidence,
          )
          const recordedCount = timelinePlaybackObservationRef.current.boundaries
            .filter(boundary => boundary.status === 'recorded').length
          setNotice(`时间线预览进行中：已记录 ${recordedCount}/${timelinePlaybackObservationRef.current.boundaries.length} 个切点的 1× 完整上下文顺序观察。`)
        }
      }
      if (boundaryPreviewEndMs != null && timelinePosition >= boundaryPreviewEndMs - boundaryLeadMs) {
        if (boundaryPreviewLoop) {
          const completedBoundary = mainBoundaries.find(boundary => (
            boundary.key === boundaryPreviewLoop.boundaryKey
            && continuityBoundaryFingerprint(boundary.left, boundary.right) === boundaryPreviewLoop.boundaryFingerprint
          ))
          if (!completedBoundary) {
            video.pause()
            advancingPlaybackRef.current = true
            setPlaying(false)
            setBoundaryPreviewEndMs(null)
            setBoundaryPreviewLoop(null)
            setNotice('循环预览期间切点关系已变化，旧循环已停止且没有登记观察。')
            return
          }
          const completedSequentialEvidence = boundaryPreviewLoop.observationRecorded
            ? null
            : boundarySequentialObservationEvidence(
              completedBoundary.left,
              completedBoundary.right,
              boundaryPreviewLoop.beforeMs,
              boundaryPreviewLoop.afterMs,
              boundaryPreviewLoop.playbackRate,
            )
          if (completedSequentialEvidence) {
            recordBoundaryContinuityReadyEvidence(
              JSON.stringify([completedBoundary.key, boundaryPreviewLoop.boundaryFingerprint]),
              'action-sequence-realtime-context',
              completedSequentialEvidence,
            )
          }
          video.pause()
          advancingPlaybackRef.current = true
          setPlaying(false)
          setBoundaryPreviewEndMs(null)
          setBoundaryPreviewLoop({
            ...boundaryPreviewLoop,
            iteration: boundaryPreviewLoop.iteration + 1,
            observationRecorded: boundaryPreviewLoop.observationRecorded || Boolean(completedSequentialEvidence),
          })
          return
        }
        if (boundaryReviewSession) {
          const completedBoundary = mainBoundaries[boundaryReviewSession.boundaryIndexes[boundaryReviewSession.position]]
          const completedSequentialEvidence = completedBoundary
            ? boundarySequentialObservationEvidence(
              completedBoundary.left,
              completedBoundary.right,
              boundaryReviewSession.beforeMs,
              boundaryReviewSession.afterMs,
              boundaryReviewSession.playbackRate,
            )
            : null
          if (completedBoundary && completedSequentialEvidence) {
            recordBoundaryContinuityReadyEvidence(
              JSON.stringify([completedBoundary.key, continuityBoundaryFingerprint(completedBoundary.left, completedBoundary.right)]),
              'action-sequence-realtime-context',
              completedSequentialEvidence,
            )
          }
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
          const sequentialObservationCount = boundaryReviewSession.boundaryIndexes.filter(index => {
            const boundary = mainBoundaries[index]
            return boundary && boundarySequentialObservationEvidence(
              boundary.left,
              boundary.right,
              boundaryReviewSession.beforeMs,
              boundaryReviewSession.afterMs,
              boundaryReviewSession.playbackRate,
            )
          }).length
          const sequentialObservationSummary = sequentialObservationCount === boundaryReviewSession.boundaryIndexes.length
            ? `已记录 ${sequentialObservationCount} 个切点的 1× 完整上下文顺序观察；`
            : sequentialObservationCount > 0
              ? `其中 ${sequentialObservationCount}/${boundaryReviewSession.boundaryIndexes.length} 个切点已记录 1× 完整上下文顺序观察；`
              : '本次速度或窗口不足以登记 1× 完整上下文顺序观察；'
          setNotice(boundaryReviewSession.scope === 'slide'
            ? `片段滑动后的前后切点试听完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个受影响切点；${sequentialObservationSummary}人工连续性检查仍需逐项确认。`
            : boundaryReviewSession.scope === 'trim'
            ? `片段裁切后的受影响切点试听完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个受影响切点；${sequentialObservationSummary}人工连续性检查仍需逐项确认。`
            : boundaryReviewSession.scope === 'repair'
            ? `组合修复后的新切点试听完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个切点；${sequentialObservationSummary}人工连续性检查仍需逐项确认。`
            : boundaryReviewSession.scope === 'asset'
            ? `素材填入后的新切点试听完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个切点；${sequentialObservationSummary}人工连续性检查仍需逐项确认。`
            : boundaryReviewSession.scope === 'structure'
            ? `镜头顺序交换后的新切点试听完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个切点；${sequentialObservationSummary}人工连续性检查仍需逐项确认。`
            : boundaryReviewSession.scope === 'history'
            ? `撤销/重做后的受影响切点试听完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个切点；${sequentialObservationSummary}恢复的人工连续性结果仍需按当前画面确认。`
            : `全时间线切点连续巡检播放完成：已播放 ${boundaryReviewSession.boundaryIndexes.length} 个可用切点${boundaryReviewSession.skippedCount ? `，跳过 ${boundaryReviewSession.skippedCount} 个含缺口边界` : ''}；${sequentialObservationSummary}人工检查项仍需逐项确认。`)
          return
        }
        setPlayheadMs(boundaryPreviewEndMs)
        setPlaying(false)
        setBoundaryPreviewEndMs(null)
        setBoundaryPreviewLoop(null)
        const completedBoundary = boundaryPreviewSession
          ? mainBoundaries.find(boundary => (
            boundary.key === boundaryPreviewSession.boundaryKey
            && continuityBoundaryFingerprint(boundary.left, boundary.right) === boundaryPreviewSession.boundaryFingerprint
          ))
          : null
        const completedSequentialEvidence = completedBoundary && boundaryPreviewSession
          ? boundarySequentialObservationEvidence(
            completedBoundary.left,
            completedBoundary.right,
            boundaryPreviewSession.beforeMs,
            boundaryPreviewSession.afterMs,
            boundaryPreviewSession.playbackRate,
          )
          : null
        if (completedBoundary && completedSequentialEvidence) {
          recordBoundaryContinuityReadyEvidence(
            JSON.stringify([completedBoundary.key, boundaryPreviewSession!.boundaryFingerprint]),
            'action-sequence-realtime-context',
            completedSequentialEvidence,
          )
        }
        setBoundaryPreviewSession(null)
        setNotice(completedSequentialEvidence
          ? `切点预览完成：已记录 1× 完整上下文顺序观察；人工连续性检查仍需逐项确认。`
          : `切点预览完成：本次速度、窗口或边界状态不足以登记 1× 完整上下文顺序观察；可继续用于定位和分析。`)
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
    boundaryPreviewSession,
    boundaryReviewSession,
    boundaryPreviewAfterMs,
    boundaryPreviewBeforeMs,
    items,
    mainBoundaries,
    outputFps,
    playing,
    recordBoundaryContinuityReadyEvidence,
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
          if (dirty && nextUnresolvedBoundaryContinuityReview) {
            focusIncompleteBoundaryContinuityReviewAt(nextUnresolvedBoundaryContinuityReview.index)
          }
          else if (dirty) setConfirmSaveOpen(true)
          else if (validationErrors.length || unresolvedCount) setValidationOpen(true)
          else setNotice('当前版本已经通过检查，可以进入确认阶段。')
        }}>
          {dirty && continuityReviewIssueCount > 0 ? <AlertTriangle /> : dirty ? <CheckCircle2 /> : unresolvedCount || validationErrors.length ? <AlertTriangle /> : <CheckCircle2 />}
          {saveAndValidate.isPending
            ? '正在生成…'
            : dirty && continuityReviewIssueCount > 0
              ? `处理 ${continuityReviewIssueCount} 个衔接检查`
              : dirty
                ? '生成可导出版本'
                : unresolvedCount || validationErrors.length
                  ? `处理 ${Math.max(unresolvedCount, validationErrors.length)} 个问题`
                  : '版本已通过'}
        </button>
      </div>
    </header>

    <section className={styles.statusbar} data-warning={continuityReviewIssueCount > 0 || unresolvedCount > 0 || validationErrors.length > 0 || Boolean(autoSaveDraft.error) || Boolean(saveAndValidate.error) || Boolean(renderPreview.error) || Boolean(reviewPreview.error) || Boolean(confirmTimeline.error) || Boolean(authorizeDelivery.error) || Boolean(uploadDelivery.error) || Boolean(verifyDelivery.error)}>
      {autoSaveDraft.isPending ? <Cloud /> : autoSaveDraft.error ? <CloudOff /> : continuityReviewIssueCount > 0 || unresolvedCount || validationErrors.length || saveAndValidate.error || renderPreview.error || reviewPreview.error || confirmTimeline.error || authorizeDelivery.error || uploadDelivery.error || verifyDelivery.error ? <AlertTriangle /> : <CheckCircle2 />}
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
      {continuityReviewIssueCount > 0 && nextUnresolvedBoundaryContinuityReview && <button onClick={() => focusIncompleteBoundaryContinuityReviewAt(nextUnresolvedBoundaryContinuityReview.index)}>处理 {continuityReviewIssueCount} 个衔接检查</button>}
      <code>{lastAutoSavedAt ? `自动保存 ${new Date(lastAutoSavedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} · ` : ''}{workspace.data.aspect_ratio} · {outputFps}fps · {seconds(durationMs)}</code>
    </section>

    <section className={styles.editingArea}>
      <aside className={styles.assetPanel} data-gap-selection={gapAssetSelection} data-boundary-replacement={Boolean(boundaryAssetReplacementTarget)} data-search={assetSearchOpen}>
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
        {gapAssetSelection && <div className={styles.assetSelectionBanner}><strong>为 {seconds(selectedGapDurationMs)} 缺口选择素材</strong><span>仅显示未用于主画面的已批准视频；点击后按缺口裁切并自动试听新切点</span><button onClick={() => setGapAssetSelection(false)}>退出</button></div>}
        {boundaryAssetReplacementTarget && <div className={styles.assetSelectionBanner}>
          <strong>替换 {boundaryAssetReplacementTarget.label}</strong>
          <span>只显示未用于主画面的已批准视频；点击后按当前片段时长替换并自动复检相邻切点</span>
          <button onClick={() => setBoundaryAssetReplacementTargetId(null)}>退出</button>
        </div>}
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
          {boundaryAssetReplacementTarget && visibleAssets.length === 0 && <div className={styles.assetEmpty}><AlertTriangle /><strong>没有可替换视频</strong><span>当前没有未用于主画面的已批准视频；请生成补充镜头或退出后调整结构。</span></div>}
          {!gapAssetSelection && !boundaryAssetReplacementTarget && normalizedAssetSearch && visibleAssets.length === 0 && <div className={styles.assetEmpty}><Search /><strong>没有匹配素材</strong><span>可更换名称、角色或类型关键词。</span></div>}
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
              if (boundaryAssetReplacementTarget && asset.asset_type === 'video') {
                dropAssetOnItem(boundaryAssetReplacementTarget, asset.id)
                return
              }
              const item = items.find(row => row.asset_id === asset.id)
              if (item) selectItem(item)
              else if (asset.asset_type === 'audio' || asset.asset_type === 'subtitle') addAssetToTrack(asset.asset_type, asset.id)
              else setNotice(`${asset.node_key ?? asset.role} 尚未加入当前时间线，请拖到目标画面位置。`)
          }}>
            <i>{asset.asset_type === 'video' ? <Film /> : asset.asset_type === 'audio' ? <Music2 /> : <Subtitles />}</i>
            <span><strong>{asset.node_key ?? asset.role}</strong><small>{gapAssetSelection && asset.asset_type === 'video' && asset.duration_ms && selectedGapDurationMs > 0
              ? asset.duration_ms >= selectedGapDurationMs
                ? `完整覆盖 ${seconds(selectedGapDurationMs)}${asset.duration_ms > selectedGapDurationMs ? ` · 裁切 ${seconds(asset.duration_ms - selectedGapDurationMs)}` : ''}`
                : `覆盖 ${seconds(asset.duration_ms)} · 仍缺 ${seconds(selectedGapDurationMs - asset.duration_ms)}`
              : `${seconds(asset.duration_ms)} · ${asset.width && asset.height ? `${asset.width}×${asset.height}` : '已批准'}`}</small></span>
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
              event.currentTarget.playbackRate = boundaryPreviewEndMs == null
                ? 1
                : boundaryReviewSession?.playbackRate
                  ?? boundaryPreviewSession?.playbackRate
                  ?? boundaryPreviewLoop?.playbackRate
                  ?? boundaryPreviewRate
              if (playing) void event.currentTarget.play().catch(() => setNotice('浏览器阻止了时间线视频播放，请再次点击播放。'))
            }}
            onTimeUpdate={handleTimeUpdate}
            onEnded={handleEnded}
          />}
          {boundaryRollMonitor && !videoTrackHidden && <BoundaryRollTrimMonitor
            projectId={projectId}
            left={boundaryRollMonitor.left}
            right={boundaryRollMonitor.right}
            leftSourceTimeMs={boundaryRollMonitor.leftSourceTimeMs}
            rightSourceTimeMs={boundaryRollMonitor.rightSourceTimeMs}
            deltaMs={boundaryRollMonitor.deltaMs}
            fps={outputFps}
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

      <aside className={styles.inspector} data-mode={boundaryInspectorOpen ? 'boundary' : 'clip'}>
        <header>
          <div className={styles.inspectorTitle}><span>INSPECTOR</span><strong>{boundaryInspectorOpen ? '衔接检查' : selectedItem?.asset_id ? '片段属性' : '缺口处理'}</strong></div>
          {boundaryInspectorOpen && <button className={styles.inspectorBack} onClick={() => {
            setBoundaryInspectorOpen(false)
            setBoundaryFrameComparisonKey(null)
            setBoundaryFrameOverlayKey(null)
            setBoundaryFrameStripKey(null)
            setBoundaryActionComparisonKey(null)
            setBoundaryCandidateGuidanceRequest(null)
          }}><ChevronLeft />返回片段</button>}
        </header>
        {selectedItem?.asset_id ? <>
          {!boundaryInspectorOpen && <>
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
          {selectedItem.track_type === 'main_video' && (previousMainItem || nextMainItem) && <section className={styles.boundaryInspectorEntry}>
            <div>
              <span>镜头衔接</span>
              <strong>{activeBoundaryContinuityReview
                ? activeBoundaryContinuityReview.unresolvedCount > 0
                  ? `${activeBoundaryContinuityReview.unresolvedCount} 项待检查`
                  : `${activeBoundaryContinuityReview.passedCount}/${activeBoundaryContinuityReview.requiredCount} 项已通过`
                : '当前切点含画面缺口'}</strong>
              <small>需要时再进入切点预览、连续性判断和修复工具。</small>
            </div>
            <button onClick={() => {
              setBoundaryInspectorOpen(true)
              focusBoundaryAt(activeBoundaryIndex)
            }}>检查当前切点<ChevronRight /></button>
          </section>}
          </>}
          {boundaryInspectorOpen && selectedItem.track_type === 'main_video' && (previousMainItem || nextMainItem) && <section>
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
            {boundaryContinuityReviewProgress.length > 0 && <div
              className={styles.boundaryContinuityReviewQueue}
              aria-label="全时间线人工连续性检查进度"
            >
              <span>
                <strong>人工连续性 {passedBoundaryContinuityReviewCount}/{boundaryContinuityReviewProgress.length} 个切点通过</strong>
                <small>{nextUnresolvedBoundaryContinuityReview
                  ? `未检查 ${unreviewedBoundaryContinuityCheckCount} · 待调整 ${needsAdjustmentBoundaryContinuityCheckCount} · 待复检 ${recheckBoundaryContinuityCheckCount} · 按时间线顺序循环`
                  : '当前所有可播放切点均已逐项通过'}</small>
              </span>
              <button
                type="button"
                disabled={!nextUnresolvedBoundaryContinuityReview}
                title={nextUnresolvedBoundaryContinuityReview
                  ? '定位后优先打开待调整或待复检原问题的观察工具；普通未检查项使用并排。不会改变播放头或写入草稿。'
                  : '当前所有可播放切点的人工连续性检查均已通过。'}
                onClick={() => nextUnresolvedBoundaryContinuityReview
                  && focusIncompleteBoundaryContinuityReviewAt(nextUnresolvedBoundaryContinuityReview.index)}
              >{nextUnresolvedBoundaryContinuityReview ? '下一个待处理' : '已全部通过'}</button>
            </div>}
            {candidateReviewFollowUpBoundaries.length > 0 && nextCandidateReviewFollowUpBoundary && <div
              className={styles.boundaryCandidateReviewQueue}
              aria-label="全时间线候选审核待办"
            >
              <span>
                <strong>{candidateReviewFollowUpCount} 项候选审核待办 · {candidateReviewFollowUpBoundaries.length} 个切点</strong>
                <small>按时间线顺序循环，不包含已失效边界</small>
              </span>
              <button
                type="button"
                title="定位后只展开同步动作；不会恢复扫描侧、设置 B 或启动播放。"
                onClick={() => focusCandidateReviewFollowUpAt(nextCandidateReviewFollowUpBoundary.index)}
              >下一个待办</button>
            </div>}
            <button
              className={styles.boundaryReviewRun}
              aria-pressed={Boolean(boundaryReviewSession)}
              disabled={!reviewableBoundaryIndexes.length}
              onClick={toggleBoundaryReview}
            ><Repeat2 />{boundaryReviewSession
                ? `${boundaryReviewSession.scope === 'slide'
                  ? '停止前后切点试听'
                  : boundaryReviewSession.scope === 'trim'
                  ? '停止裁切切点试听'
                  : boundaryReviewSession.scope === 'asset'
                  ? '停止素材填入切点试听'
                  : boundaryReviewSession.scope === 'repair'
                  ? '停止组合修复切点试听'
                  : boundaryReviewSession.scope === 'structure'
                  ? '停止顺序交换切点试听'
                  : boundaryReviewSession.scope === 'history'
                  ? '停止撤销/重做切点试听'
                  : '停止连续巡检'}（${boundaryReviewSession.position + 1}/${boundaryReviewSession.boundaryIndexes.length}）`
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
                const boundaryIndex = mainBoundaries.findIndex(boundary => boundary.key === boundaryKey)
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
                  const currentContinuityOutcomes = Object.fromEntries(
                    Object.entries(boundaryContinuityOutcomes[boundaryKey] ?? {})
                      .filter(([checkId]) => continuityChecks.some(check => check.id === checkId)),
                  ) as Record<string, BoundaryContinuityCheckOutcome>
                  const passedContinuityCheckCount = continuityChecks
                    .filter(check => currentContinuityOutcomes[check.id] === 'passed').length
                  const needsAdjustmentContinuityCheckCount = continuityChecks
                    .filter(check => currentContinuityOutcomes[check.id] === 'needs_adjustment').length
                  const continuityIssueContexts = (boundaryContinuityIssueContexts[boundaryKey] ?? [])
                    .filter(context => continuityChecks.some(check => check.id === context.checkId))
                  const handlingContinuityIssueCount = continuityIssueContexts.filter(
                    context => currentContinuityOutcomes[context.checkId] === 'needs_adjustment',
                  ).length
                  const recheckContinuityIssueCount = continuityIssueContexts.length - handlingContinuityIssueCount
                  const unreviewedContinuityCheckCount = continuityChecks.length
                    - passedContinuityCheckCount
                    - needsAdjustmentContinuityCheckCount
                    - recheckContinuityIssueCount
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
                 const actionComparison = boundaryActionComparisonKey === boundaryKey
                 const boundaryFingerprint = continuityBoundaryFingerprint(left, right)
                 const continuityObservationKey = JSON.stringify([boundaryKey, boundaryFingerprint])
                 const candidateReviewSessionKey = boundaryCandidateReviewSessionKey(projectId, left, right, frameStepMs, outputFps)
                  const candidateReviewSession = boundaryCandidateReviewSessions[candidateReviewSessionKey]
                    ?? EMPTY_BOUNDARY_CANDIDATE_REVIEW_SESSION
                  const candidateReviewOutcomes = Object.values(candidateReviewSession.comparisonOutcomes)
                  const candidateReviewKeptCount = candidateReviewOutcomes.filter(outcome => outcome === 'kept_baseline').length
                  const candidateReviewUndecidedCount = candidateReviewOutcomes.filter(outcome => outcome === 'completed').length
                  const candidateReviewShortlistedCount = candidateReviewOutcomes.filter(outcome => outcome === 'shortlisted').length
                  const candidateReviewMeasuredOnlyCount = Object.keys(candidateReviewSession.measuredMotionEvidence)
                    .filter(sourceKey => !candidateReviewSession.comparisonOutcomes[sourceKey]).length
                  const hasCandidateReviewMemory = candidateReviewOutcomes.length > 0 || candidateReviewMeasuredOnlyCount > 0
                  const hasCandidateReviewFollowUp = candidateReviewUndecidedCount > 0
                    || candidateReviewShortlistedCount > 0
                    || candidateReviewMeasuredOnlyCount > 0
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
                       <em>{passedContinuityCheckCount}/{continuityChecks.length} 通过 · 待调整 {needsAdjustmentContinuityCheckCount}</em>
                     </div>
                     <p>{continuityCopy.summary}</p>
                   </div> : <div className={styles.continuityUnavailable}>
                     <span>{leftShotSequence == null || rightShotSequence == null
                       ? '边界含补充素材，正式分镜没有声明这组衔接关系，请完整人工检查。'
                       : '当前两镜不是正式相邻分镜，不能套用原连续性关系，请按当前叙事人工判断。'}</span>
                     <em>{passedContinuityCheckCount}/{continuityChecks.length} 通过 · 待调整 {needsAdjustmentContinuityCheckCount}</em>
                   </div>}
                   <div className={styles.boundaryActions}>
                    <button
                      disabled={!left.asset_id || !right.asset_id}
                      onClick={() => previewBoundary(left, right)}
                    ><Play />预览切点</button>
                    <button
                      aria-label={`${left.label} 到 ${right.label} 无损对比衔接方式`}
                      disabled={!left.asset_id || !right.asset_id}
                      onClick={() => {
                        setBoundaryFrameComparisonKey(boundaryKey)
                        setBoundaryFrameOverlayKey(null)
                        setBoundaryFrameStripKey(null)
                        setBoundaryActionComparisonKey(boundaryKey)
                      }}
                    ><Layers3 />转场 A/B · {presetValue === 'mixed' ? '两侧不一致' : pairedFade ? `${seconds(durationMs)} 淡出淡入` : '直接切换'}</button>
                    </div>
                   <div className={styles.boundaryPreviewTools}>
                     <label>
                       <span>切前</span>
                       <select
                         aria-label={`${left.label} 到 ${right.label} 的切前预览窗口`}
                         disabled={!left.asset_id || !right.asset_id || Boolean(boundaryReviewSession) || Boolean(boundaryPreviewSession) || Boolean(boundaryPreviewLoop)}
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
                         disabled={!left.asset_id || !right.asset_id || Boolean(boundaryReviewSession) || Boolean(boundaryPreviewSession) || Boolean(boundaryPreviewLoop)}
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
                         disabled={!left.asset_id || !right.asset_id || Boolean(boundaryReviewSession) || Boolean(boundaryPreviewSession) || Boolean(boundaryPreviewLoop)}
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
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口前移 1 秒`} disabled={videoTrackLocked || !row.earlier} onClick={() => slipBoundaryItem(row.item, -1000, row.focusMs, boundaryKey)}>−1s</button>
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口前移 1 帧`} disabled={videoTrackLocked || !row.earlier} onClick={() => slipBoundaryItem(row.item, -frameStepMs, row.focusMs, boundaryKey)}>−1帧</button>
                       <code>{timecode(row.item.source_in_ms ?? 0, outputFps)}–{timecode(row.item.source_out_ms ?? 0, outputFps)}</code>
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口后移 1 帧`} disabled={videoTrackLocked || !row.later} onClick={() => slipBoundaryItem(row.item, frameStepMs, row.focusMs, boundaryKey)}>+1帧</button>
                       <button aria-label={`${left.label} 到 ${right.label} ${row.role}源窗口后移 1 秒`} disabled={videoTrackLocked || !row.later} onClick={() => slipBoundaryItem(row.item, 1000, row.focusMs, boundaryKey)}>+1s</button>
                     </div>)}
                   </div>
                   <button
                    className={styles.boundaryFrameToggle}
                    disabled={!left.asset_id || !right.asset_id}
                    aria-expanded={framesOpen}
                    aria-label={`${left.label} 到 ${right.label} ${framesOpen ? '收起切点定格' : '对比末帧 / 首帧'}`}
                    onClick={() => {
                      setBoundaryFrameComparisonKey(value => value === boundaryKey ? null : boundaryKey)
                      if (framesOpen) setBoundaryActionComparisonKey(null)
                    }}
                   ><Layers3 />{framesOpen ? '收起切点定格' : '对比末帧 / 首帧'}</button>
                    {!framesOpen && hasCandidateReviewFollowUp && <section
                      className={styles.boundaryCandidateReviewReminder}
                      aria-label={`${left.label} 到 ${right.label} 的候选审核待办`}
                    >
                      <span>
                        <strong>候选审核未完成</strong>
                        <small>
                          待决定 {candidateReviewUndecidedCount} · 待复看 {candidateReviewShortlistedCount}
                          {candidateReviewMeasuredOnlyCount > 0 ? ` · 已实测未对照 ${candidateReviewMeasuredOnlyCount}` : ''}
                        </small>
                      </span>
                      <button
                        type="button"
                        title="展开同步动作；不会恢复扫描侧、设置 B 或启动播放。"
                        onClick={() => {
                          setBoundaryFrameComparisonKey(boundaryKey)
                          setBoundaryFrameOverlayKey(null)
                          setBoundaryFrameStripKey(null)
                          setBoundaryActionComparisonKey(boundaryKey)
                          setBoundaryCandidateGuidanceRequest({
                            boundaryKey,
                            checkId: 'candidate-review-follow-up',
                            checkLabel: '候选审核待办',
                            requestToken: Date.now(),
                            intent: 'resume',
                          })
                        }}
                      >继续审核</button>
                    </section>}
                    {framesOpen && left.asset_id && right.asset_id && <>
                     <div className={styles.boundaryFrameModes}>
                       <div>
                         <button aria-pressed={!overlayFrames && !stripFrames && !actionComparison} onClick={() => {
                           setBoundaryFrameOverlayKey(null)
                           setBoundaryFrameStripKey(null)
                           setBoundaryActionComparisonKey(null)
                         }}>并排</button>
                         <button aria-pressed={overlayFrames} onClick={() => {
                           setBoundaryFrameOverlayKey(boundaryKey)
                           setBoundaryFrameStripKey(null)
                           setBoundaryActionComparisonKey(null)
                         }}>叠加对齐</button>
                         <button aria-pressed={stripFrames} onClick={() => {
                           setBoundaryFrameOverlayKey(null)
                           setBoundaryFrameStripKey(boundaryKey)
                           setBoundaryActionComparisonKey(null)
                         }}>动作帧带</button>
                         <button aria-pressed={actionComparison} onClick={() => {
                           setBoundaryFrameOverlayKey(null)
                           setBoundaryFrameStripKey(null)
                           setBoundaryActionComparisonKey(boundaryKey)
                         }}>同步动作</button>
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
                        {hasCandidateReviewMemory && <section
                          className={styles.boundaryCandidateReviewSummary}
                          aria-label={`${left.label} 到 ${right.label} 的候选审核记忆`}
                        >
                          <span>
                            <strong>候选审核记忆</strong>
                            <small>
                              待决定 {candidateReviewUndecidedCount} · 待复看 {candidateReviewShortlistedCount} · 保留 A {candidateReviewKeptCount}
                              {candidateReviewMeasuredOnlyCount > 0 ? ` · 已实测未对照 ${candidateReviewMeasuredOnlyCount}` : ''}
                            </small>
                          </span>
                          {!actionComparison && hasCandidateReviewFollowUp && <button
                            type="button"
                            title="只打开同步动作；不会恢复扫描侧、设置 B 或启动播放。"
                            onClick={() => {
                              setBoundaryFrameOverlayKey(null)
                              setBoundaryFrameStripKey(null)
                              setBoundaryActionComparisonKey(boundaryKey)
                              setBoundaryCandidateGuidanceRequest({
                                boundaryKey,
                                checkId: 'candidate-review-follow-up',
                                checkLabel: '候选审核待办',
                                requestToken: Date.now(),
                                intent: 'resume',
                              })
                            }}
                          >继续审核</button>}
                        </section>}
                      </div>
                     {stripFrames
                       ? <div className={styles.boundaryFrameStrip} aria-label={`${left.label} 到 ${right.label} 的动作连续帧带`}>
                         {([
                           { role: '前镜末端', item: left, frames: leftStripFrames, applyLabel: '设为前镜末帧', currentLabel: '当前末帧', observationSide: 'frames-left' },
                           { role: '后镜开头', item: right, frames: rightStripFrames, applyLabel: '设为后镜首帧', currentLabel: '当前首帧', observationSide: 'frames-right' },
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
                                 observationKey={continuityObservationKey}
                                 observationSide={row.observationSide}
                                 onObserved={recordBoundaryContinuityReadyEvidence}
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
                       : actionComparison
                       ? <BoundaryActionComparison
                         projectId={projectId}
                         left={left}
                         right={right}
                         beforeMs={boundaryPreviewBeforeMs}
                         afterMs={boundaryPreviewAfterMs}
                         rate={boundaryPreviewRate}
                         frameStepMs={frameStepMs}
                         fps={outputFps}
                         editLocked={videoTrackLocked}
                         rollMinimumDeltaMs={rollMinimumDelta}
                         rollMaximumDeltaMs={rollMaximumDelta}
                         onBeforePlay={() => {
                           videoRef.current?.pause()
                           advancingPlaybackRef.current = true
                           setPlaying(false)
                           setBoundaryPreviewEndMs(null)
                           setBoundaryPreviewLoop(null)
                           setBoundaryReviewSession(null)
                           setPendingBoundaryPreviewKey(null)
                           setPendingBoundaryReview(null)
                         }}
                          onApplyRoll={deltaMs => applyBoundaryRoll(left, right, deltaMs)}
                          onApplyTransition={(type, transitionDurationMs) => setBoundaryTransition(left, right, type, transitionDurationMs)}
                          onApplyLeftPhase={deltaMs => slipBoundaryItem(left, deltaMs, Math.max(left.timeline_in_ms, left.timeline_out_ms - frameStepMs), boundaryKey)}
                         onApplyRightPhase={deltaMs => slipBoundaryItem(right, deltaMs, right.timeline_in_ms, boundaryKey)}
                         onApplyPhasePair={(leftDeltaMs, rightDeltaMs) => slipBoundaryPair(left, right, leftDeltaMs, rightDeltaMs, boundaryKey)}
                         candidateReviewSessionKey={candidateReviewSessionKey}
                         candidateReviewSession={candidateReviewSession}
                         guidedScanRequest={boundaryCandidateGuidanceRequest?.boundaryKey === boundaryKey
                           ? {
                             requestToken: boundaryCandidateGuidanceRequest.requestToken,
                             issueLabel: boundaryCandidateGuidanceRequest.checkLabel,
                             intent: boundaryCandidateGuidanceRequest.intent,
                           }
                           : null}
                         onConsumeGuidedScanRequest={requestToken => setBoundaryCandidateGuidanceRequest(current => (
                           current?.requestToken === requestToken ? null : current
                         ))}
                         onRememberCandidateMotionEvidence={rememberBoundaryCandidateMotionEvidence}
                         onRememberCandidateComparisonOutcome={rememberBoundaryCandidateComparisonOutcome}
                         onRememberAlternativeOutcome={rememberBoundaryAlternativeOutcome}
                         onReplaceLeftAsset={() => startBoundaryAssetReplacement(left, '前镜')}
                         onReplaceRightAsset={() => startBoundaryAssetReplacement(right, '后镜')}
                         formalOrderSwapAvailable={orderWarning}
                         onSwapToFormalOrder={() => swapBoundaryToFormalOrder(left, right)}
                         onAdjustStructure={() => {
                           selectItem(left)
                           setBoundaryActionComparisonKey(null)
                           setBoundaryCandidateGuidanceRequest(null)
                           setBoundaryAssetReplacementTargetId(null)
                           setNotice(`已回到 ${left.label} → ${right.label} 的时间线结构；可移动、拖拽或按正式分镜整理。系统尚未自动重排或写入草稿。`)
                         }}
                         onNotice={setNotice}
                         observationKey={continuityObservationKey}
                         onObserved={recordBoundaryContinuityReadyEvidence}
                       />
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
                         observationKey={continuityObservationKey}
                         onObserved={recordBoundaryContinuityReadyEvidence}
                       />
                       : <div className={styles.boundaryFrames}>
                         <BoundaryFrameStill
                           projectId={projectId}
                           item={left}
                           sourceTimeMs={leftFrameSourceMs}
                           label={`${left.label} 末帧`}
                           fps={outputFps}
                           onActivate={() => seekTimeline(Math.max(left.timeline_in_ms, left.timeline_out_ms - frameStepMs))}
                           observationKey={continuityObservationKey}
                           observationSide="frames-left"
                           onObserved={recordBoundaryContinuityReadyEvidence}
                         />
                         <BoundaryFrameStill
                           projectId={projectId}
                           item={right}
                           sourceTimeMs={rightFrameSourceMs}
                           label={`${right.label} 首帧`}
                           fps={outputFps}
                           onActivate={() => seekTimeline(right.timeline_in_ms)}
                           observationKey={continuityObservationKey}
                           observationSide="frames-right"
                           onObserved={recordBoundaryContinuityReadyEvidence}
                         />
                       </div>}
                   </>}
                   <div className={styles.continuityChecklist} aria-label={`${left.label} 到 ${right.label} 的人工连续性检查`}>
                     {continuityChecks.map(check => {
                       const outcome = currentContinuityOutcomes[check.id]
                       const status: BoundaryContinuityCheckOutcome | 'unreviewed' = outcome ?? 'unreviewed'
                       const observationMode = continuityReviewModeForCheckId(check.id)
                       const currentObservation = boundaryContinuityObservations[boundaryKey]?.[observationMode]
                       const requiredLeftContextMs = Math.min(1000, Math.max(0, (left.source_out_ms ?? 0) - (left.source_in_ms ?? 0)))
                       const requiredRightContextMs = Math.min(1000, Math.max(0, (right.source_out_ms ?? 0) - (right.source_in_ms ?? 0)))
                       const persistedActionSequenceEvidence = currentObservation?.action_sequence_evidence
                       const persistedActionSequenceCurrent = observationMode !== 'action'
                         ? persistedActionSequenceEvidence == null
                         : persistedActionSequenceEvidence?.playback_rate === 1
                           && persistedActionSequenceEvidence.left_context_ms >= requiredLeftContextMs
                           && persistedActionSequenceEvidence.left_context_ms <= (left.source_out_ms ?? 0) - (left.source_in_ms ?? 0)
                           && persistedActionSequenceEvidence.right_context_ms >= requiredRightContextMs
                           && persistedActionSequenceEvidence.right_context_ms <= (right.source_out_ms ?? 0) - (right.source_in_ms ?? 0)
                       const requiredCompletedSteps = observationMode === 'frames'
                         ? ['left_frame', 'right_frame'] as const
                         : observationMode === 'overlay'
                           ? ['overlay'] as const
                           : ['synchronous_action', 'sequential_cut_realtime_context'] as const
                       const persistedObservationCurrent = currentObservation?.boundary_fingerprint === boundaryFingerprint
                         && currentObservation.completed_steps.length === requiredCompletedSteps.length
                         && currentObservation.completed_steps.every((step, index) => step === requiredCompletedSteps[index])
                         && persistedActionSequenceCurrent
                       const readyEvidence = boundaryContinuityReadyEvidence[continuityObservationKey] ?? {}
                       const actionSynchronousReady = Boolean(readyEvidence['action-synchronous'])
                       const actionSequenceReady = Boolean(readyEvidence['action-sequence-realtime-context'])
                       const observationReady = observationMode === 'frames'
                         ? Boolean(readyEvidence['frames-left'] && readyEvidence['frames-right'])
                         : observationMode === 'overlay'
                           ? Boolean(readyEvidence.overlay)
                           : Boolean(actionSynchronousReady && actionSequenceReady)
                       const canPass = persistedObservationCurrent || observationReady
                       const observationActionLabel = observationMode !== 'action'
                         ? continuityReviewModeLabel(observationMode)
                         : actionSequenceReady && !actionSynchronousReady
                           ? '同步动作（1× 顺序已完成）'
                           : actionSynchronousReady && !actionSequenceReady
                             ? '1× 完整上下文切点（同步动作已完成）'
                             : continuityReviewModeLabel(observationMode)
                       const setOutcome = (nextOutcome: BoundaryContinuityCheckOutcome | null) => {
                         if (nextOutcome === 'passed' && !canPass) {
                           setNotice(`请先完成${continuityReviewModeLabel(observationMode)}，再把“${check.label}”标为通过。`)
                           return
                         }
                         setDirty(true)
                         if (nextOutcome === 'passed' && !persistedObservationCurrent) {
                           setBoundaryContinuityObservations(current => ({
                             ...current,
                             [boundaryKey]: {
                               ...(current[boundaryKey] ?? {}),
                               [observationMode]: {
                                 boundary_fingerprint: boundaryFingerprint,
                                 observed_at: new Date().toISOString(),
                                 completed_steps: [...requiredCompletedSteps],
                                 action_sequence_evidence: observationMode === 'action'
                                   ? readyEvidence['action-sequence-realtime-context'] ?? null
                                   : null,
                               },
                             },
                           }))
                         }
                         if (nextOutcome === 'passed' && continuityIssueContexts.some(context => context.checkId === check.id)) {
                           setBoundaryContinuityIssueContexts(current => {
                             const retained = (current[boundaryKey] ?? []).filter(context => context.checkId !== check.id)
                             if (retained.length > 0) return { ...current, [boundaryKey]: retained }
                             const next = { ...current }
                             delete next[boundaryKey]
                             return next
                           })
                         }
                         setBoundaryContinuityOutcomes(current => {
                           const nextBoundaryOutcomes = { ...(current[boundaryKey] ?? {}) }
                           if (nextOutcome) nextBoundaryOutcomes[check.id] = nextOutcome
                           else delete nextBoundaryOutcomes[check.id]
                           if (Object.keys(nextBoundaryOutcomes).length === 0) {
                             const next = { ...current }
                             delete next[boundaryKey]
                             return next
                           }
                           return { ...current, [boundaryKey]: nextBoundaryOutcomes }
                         })
                       }
                       return <div className={styles.continuityCheckRow} data-status={status} key={check.id}>
                         <div>
                           <span className={styles.continuityCheckIcon} aria-hidden="true">
                             {status === 'passed' ? <CheckCircle2 /> : status === 'needs_adjustment' ? <AlertTriangle /> : null}
                           </span>
                           <span className={styles.continuityCheckLabel}>{check.label}</span>
                           <em>{status === 'passed' ? '通过' : status === 'needs_adjustment' ? '需调整' : '未检查'}</em>
                         </div>
                         <div className={styles.continuityOutcomeButtons} role="group" aria-label={`${check.label}的检查结果`}>
                           <button type="button" aria-pressed={!outcome} onClick={() => setOutcome(null)}>未检查</button>
                           <button
                             type="button"
                             aria-pressed={status === 'passed'}
                             disabled={!canPass}
                             title={canPass
                               ? `已完成当前切点的${continuityReviewModeLabel(observationMode)}。`
                               : `先完成当前切点的${continuityReviewModeLabel(observationMode)}，才能标为通过。`}
                             onClick={() => setOutcome('passed')}
                           >通过</button>
                           <button type="button" aria-pressed={status === 'needs_adjustment'} onClick={() => setOutcome('needs_adjustment')}>需调整</button>
                         </div>
                         {status !== 'passed' && !canPass && <button
                           type="button"
                           className={styles.continuityObservationAction}
                           disabled={boundaryIndex < 0 || !left.asset_id || !right.asset_id}
                           title={`打开当前切点的${observationActionLabel}；观察完成前不能标为通过。`}
                           onClick={() => {
                             const target = focusBoundaryForReviewAt(boundaryIndex, observationMode)
                             if (target) setNotice(`正在观察“${check.label}”：完成${continuityReviewModeLabel(observationMode)}后才可标为通过。`)
                           }}
                         >观察：{observationActionLabel}</button>}
                         {status === 'needs_adjustment' && <button
                           type="button"
                           className={styles.continuityAdjustmentAction}
                           disabled={boundaryIndex < 0 || !left.asset_id || !right.asset_id}
                           title={left.asset_id && right.asset_id
                             ? `只打开${continuityReviewModeLabel(continuityReviewModeForCheckId(check.id))}；不会自动修改素材、切点或转场。`
                             : '需要先补齐切点两侧画面。'}
                           onClick={() => openBoundaryContinuityAdjustmentAt(boundaryIndex, check.id, check.label)}
                         >处理：{continuityReviewModeLabel(continuityReviewModeForCheckId(check.id))}</button>}
                       </div>
                     })}
                     {continuityIssueContexts.length > 0 && <section
                       className={styles.continuityIssueContext}
                       data-state={recheckContinuityIssueCount > 0 ? 'recheck' : 'handling'}
                       aria-label={`${left.label} 到 ${right.label} 的连续性问题处理上下文`}
                     >
                       <header>
                         <strong>{recheckContinuityIssueCount > 0 ? '原问题待复检' : '正在处理连续性问题'}</strong>
                         <small>处理中 {handlingContinuityIssueCount} · 待复检 {recheckContinuityIssueCount}</small>
                       </header>
                       <div>
                         {continuityIssueContexts.map(context => {
                           const handling = currentContinuityOutcomes[context.checkId] === 'needs_adjustment'
                           return <div data-state={handling ? 'handling' : 'recheck'} key={context.checkId}>
                             <span>
                               <b>{context.checkLabel}</b>
                               <small>{continuityReviewModeLabel(context.mode)} · {handling ? '正在处理' : '待重新判断'}</small>
                             </span>
                             <button
                               type="button"
                               aria-label={`${handling ? '继续处理' : '重新复检'}：${context.checkLabel}`}
                               disabled={boundaryIndex < 0 || !left.asset_id || !right.asset_id}
                               onClick={() => openBoundaryContinuityAdjustmentAt(
                                 boundaryIndex,
                                 context.checkId,
                                 context.checkLabel,
                               )}
                             >{handling ? '继续' : '复检'}</button>
                           </div>
                         })}
                       </div>
                     </section>}
                     <small>本切点：通过 {passedContinuityCheckCount} · 未检查 {unreviewedContinuityCheckCount} · 待调整 {needsAdjustmentContinuityCheckCount} · 待复检 {recheckContinuityIssueCount}。结果与待复检原问题会自动保存到项目草稿；“需调整”不会自动修改素材、切点或转场。</small>
                   </div>
                 </div>
              })}
            </div>
            <div className={styles.trimHint}>淡出淡入会同时设置前镜淡出和后镜淡入，并作为一个撤销步骤写入草稿；它不是交叉叠化。正式预览和导出使用同一冻结参数。</div>
          </section>}
          {boundaryInspectorOpen && selectedItem.track_type === 'main_video' && <section>
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
          {selectedGapPrecedingExtension && selectedGapFormalRecommendation && selectedGapCombinedRepair && selectedGapCompleteRepair && <button
            disabled={videoTrackLocked}
            onClick={() => applySelectedGapCombinedRepair(true)}
          ><WandSparkles /><span>
            <strong>{videoTrackLocked ? '解锁后一次完整修复' : '一次完整修复正式顺序与时长'}</strong>
            <small>延长 {selectedGapPrecedingExtension.item.label} {seconds(selectedGapPrecedingExtension.extensionMs)} + 补入 {selectedGapFormalRecommendation.shot.shot_code} {seconds(selectedGapCombinedRepair.insertedDurationMs)} + {selectedGapCompleteRepair.asset.node_key ?? selectedGapCompleteRepair.asset.role} 覆盖 {seconds(selectedGapCompleteRepair.insertedDurationMs)}{selectedGapCompleteRepair.trimmedDurationMs > 0 ? `（裁切 ${seconds(selectedGapCompleteRepair.trimmedDurationMs)}）` : ''}；补齐到 {seconds(durationMs)} 并试听全部新切点</small>
          </span></button>}
          {selectedGapPrecedingExtension && selectedGapFormalRecommendation && selectedGapCombinedRepair && <button
            disabled={videoTrackLocked}
            onClick={() => applySelectedGapCombinedRepair()}
          ><WandSparkles /><span>
            <strong>{videoTrackLocked ? '解锁后应用组合修复' : '组合修复正式顺序与缺口'}</strong>
            <small>延长 {selectedGapPrecedingExtension.item.label} {seconds(selectedGapPrecedingExtension.extensionMs)} + 补入 {selectedGapFormalRecommendation.shot.shot_code} {seconds(selectedGapCombinedRepair.insertedDurationMs)} + 正式排序；预计剩余 {seconds(selectedGapCombinedRepair.remainingGapMs)} 并自动试听新切点</small>
          </span></button>}
          {selectedGapPrecedingExtension && <button
            disabled={videoTrackLocked}
            onClick={extendPrecedingItemIntoSelectedGap}
          ><Clock3 /><span>
            <strong>{videoTrackLocked ? '解锁后延长前一镜' : `延长前一镜 ${selectedGapPrecedingExtension.item.label}`}</strong>
            <small>可使用剩余源尾 {seconds(selectedGapPrecedingExtension.extensionMs)}，缺口将{selectedGapPrecedingExtension.remainingGapMs > 0 ? `缩短为 ${seconds(selectedGapPrecedingExtension.remainingGapMs)}` : '完全补齐'}；会改变镜头时长并要求复检</small>
          </span></button>}
          {selectedItem && selectedGapFormalRecommendation && <button
            disabled={videoTrackLocked}
            onClick={() => {
              if (selectedGapFormalRecommendation.assets.length === 1) {
                dropAssetOnItem(selectedItem, selectedGapFormalRecommendation.assets[0].id)
                return
              }
              startGapAssetSelection(selectedGapFormalRecommendation.shot.shot_code)
            }}
          ><Sparkles /><span>
            <strong>{videoTrackLocked
              ? '解锁后补入正式缺失分镜'
              : selectedGapFormalRecommendation.assets.length === 1
                ? `补入正式分镜 ${selectedGapFormalRecommendation.shot.shot_code}`
                : `选择 ${selectedGapFormalRecommendation.shot.shot_code} 的素材`}</strong>
            <small>{selectedGapFormalRecommendation.assets.length === 1
              ? `${selectedGapFormalRecommendation.assets[0].node_key ?? selectedGapFormalRecommendation.assets[0].role} · 可覆盖 ${seconds(Math.min(selectedGapFormalRecommendation.assets[0].duration_ms ?? 0, selectedItem.timeline_out_ms - selectedItem.timeline_in_ms))}`
              : `${selectedGapFormalRecommendation.assets.length} 个已批准且未使用的精确 Shot 候选，不自动替你选择`}</small>
          </span></button>}
          <Link to={`/projects/${projectId}/decision-impact`}><Clock3 /><span><strong>分析缩短成片</strong><small>先评估修改 15 秒目标的下游影响</small></span></Link>
          <Link to={`/production?project=${projectId}`}><WandSparkles /><span><strong>前往生成补充镜头</strong><small>在生产流程登记新镜头，授权后才可能产生费用</small></span></Link>
          <button disabled={videoTrackLocked} onClick={() => startGapAssetSelection()}><Plus /><span><strong>{videoTrackLocked ? '先解锁画面轨' : '选择其他素材'}</strong><small>{videoTrackLocked ? '锁定期间不会替换缺口' : '只列出未用于主画面的已批准视频'}</small></span></button>
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
          if ((event.target as HTMLElement).closest('button, [data-timeline-control]')) return
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
            {mainBoundaries.map(({ key, left, right }) => {
              const roll = buildRolledBoundaryItems(items, left, right, 0)
              if (!roll) return null
              const disabled = videoTrackLocked || (roll.minimumDelta === 0 && roll.maximumDelta === 0)
              return <i
                key={key}
                role="slider"
                data-timeline-control
                className={styles.boundaryRollHandle}
                aria-label={`${left.label} 到 ${right.label} 的滚动剪辑切点拖动把手`}
                aria-disabled={disabled}
                aria-valuemin={left.timeline_out_ms + roll.minimumDelta}
                aria-valuemax={left.timeline_out_ms + roll.maximumDelta}
                aria-valuenow={left.timeline_out_ms}
                aria-valuetext={timecode(left.timeline_out_ms, outputFps)}
                tabIndex={disabled ? -1 : 0}
                style={{ left: `${(left.timeline_out_ms / durationMs) * 100}%` }}
                onClick={event => event.stopPropagation()}
                onPointerEnter={() => previewBoundaryRollMonitor(left, right)}
                onPointerLeave={() => setBoundaryRollMonitor(current => (
                  current?.boundaryKey === key && !current.active ? null : current
                ))}
                onFocus={() => previewBoundaryRollMonitor(left, right)}
                onBlur={() => setBoundaryRollMonitor(current => (
                  current?.boundaryKey === key && !current.active ? null : current
                ))}
                onPointerDown={event => beginBoundaryRoll(event, left, right)}
                onKeyDown={event => {
                  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
                  event.preventDefault()
                  event.stopPropagation()
                  setBoundaryRollMonitor(null)
                  const stepMs = event.shiftKey ? 1000 : snapEnabled ? snapIntervalMs : frameStepMs
                  applyBoundaryRoll(left, right, event.key === 'ArrowRight' ? stepMs : -stepMs)
                }}
              />
            })}
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
              <div><dt>连续性审核</dt><dd>{timeline.continuity_review_hash ? `${timeline.continuity_review.boundary_count ?? 0} 个切点 · ${timeline.continuity_review_hash.slice(0, 12)}` : '未冻结'}</dd></div>
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
        <div><dt>连续性审核</dt><dd>{passedBoundaryContinuityReviewCount}/{boundaryContinuityReviewProgress.length} 个切点通过</dd></div>
        <div><dt>基线版本</dt><dd>v{sourceTimeline?.version_number} · row {sourceTimeline?.row_version}</dd></div>
      </dl>
      {unresolvedCount > 0 && <div className={styles.modalWarning}><AlertTriangle /><span>允许保存含空位的候选，但检查不会通过；保存后会精确定位这些问题。</span></div>}
      {continuityReviewIssueCount > 0 && <div className={styles.modalWarning}><AlertTriangle /><span>仍有 {continuityReviewIssueCount} 项镜头连续性检查未通过，必须先逐项处理。</span></div>}
      <footer><button onClick={() => setConfirmSaveOpen(false)}>继续调整</button><button className={styles.confirmButton} disabled={saveAndValidate.isPending || continuityReviewIssueCount > 0} onClick={() => saveAndValidate.mutate()}>{saveAndValidate.isPending ? '正在生成并检查…' : '生成可导出版本'}</button></footer>
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
              onTimeUpdate={handleRenderedPreviewTimeUpdate}
              onSeeked={event => setPreviewCompareMs(Math.round(event.currentTarget.currentTime * 1000))}
              onSeeking={() => {
                if (previewWatchSessionRef.current) {
                  invalidatePreviewWatchAttempt('低清预览播放位置已跳转；请回到开头并以 1× 重新完整播放。')
                }
              }}
              onRateChange={event => {
                if (event.currentTarget.playbackRate !== 1 && previewWatchSessionRef.current) {
                  invalidatePreviewWatchAttempt('低清预览倍速已变化；完整观看只接受 1×，请从头重新播放。')
                }
              }}
              onPlay={handleRenderedPreviewPlay}
              onPause={() => sourceCompareRef.current?.pause()}
              onEnded={handleRenderedPreviewEnded}
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
          <div className={styles.previewWatchGate} data-complete={previewWatchComplete}>
            {previewWatchComplete ? <CheckCircle2 /> : <Clock3 />}
            <span><strong>{previewWatchComplete ? '1× 完整观看已完成' : '先从头完整播放当前预览'}</strong><small>{previewWatchComplete
              ? '观看证据绑定当前预览文件；现在可以逐项确认。'
              : `连续观看 ${timecode(previewWatchProgressMs, lastPreview.fps)} / ${timecode(lastPreview.duration_ms, lastPreview.fps)}；暂停可继续，跳转或倍速会归零。`}</small></span>
          </div>
          <label><input type="checkbox" disabled={!previewWatchComplete} checked={previewReviewChecks.visualContinuity} onChange={event => setPreviewReviewChecks(value => ({ ...value, visualContinuity: event.target.checked }))} /><span><strong>画面连续性</strong><small>已完整观看镜头衔接、主体一致性、动作连续性和异常闪跳。</small></span></label>
          <label><input type="checkbox" disabled={!previewWatchComplete} checked={previewReviewChecks.subjectiveSync} onChange={event => setPreviewReviewChecks(value => ({ ...value, subjectiveSync: event.target.checked }))} /><span><strong>主观音画同步</strong><small>已检查旁白、音乐、字幕与画面节奏是否符合预期。</small></span></label>
          {sourceTimeline?.track_config.subtitle_enabled && <label><input type="checkbox" disabled={!previewWatchComplete} checked={previewReviewChecks.subtitleReadability} onChange={event => setPreviewReviewChecks(value => ({ ...value, subtitleReadability: event.target.checked }))} /><span><strong>字幕可读性</strong><small>已检查文字、换行、遮挡和画面安全区。</small></span></label>}
          {lastPreview.quality_report.checks.some(check => check.state === 'warning') && <label><input type="checkbox" disabled={!previewWatchComplete} checked={previewReviewChecks.warnings} onChange={event => setPreviewReviewChecks(value => ({ ...value, warnings: event.target.checked }))} /><span><strong>警告项已逐项确认</strong><small>已确认检测到的黑画面或其他警告均为有意效果或可接受结果。</small></span></label>}
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
            || !previewWatchComplete
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
