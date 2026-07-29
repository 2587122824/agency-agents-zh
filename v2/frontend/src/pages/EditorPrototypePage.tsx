import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Film,
  Eye, EyeOff, Layers3, Lock, Maximize2, Music2, Pause, Play, Plus, Redo2,
  Scissors, Search, Sparkles, Subtitles, Undo2, Unlock, Volume2, VolumeX,
  WandSparkles,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { TimelineItem } from '../api/types'
import styles from './EditorPrototypePage.module.css'

const DEFAULT_PROJECT_ID = 'project_9cd1c4e1fe5c4c8e88466acef2913e72'

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

export function EditorPrototypePage() {
  const [params] = useSearchParams()
  const projectId = params.get('project') ?? DEFAULT_PROJECT_ID
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
  const videoRef = useRef<HTMLVideoElement | null>(null)

  const sourceTimeline = workspace.data?.timelines[0] ?? null
  useEffect(() => {
    if (!sourceTimeline) return
    setItems(sourceTimeline.items)
    setHistory([])
    setFuture([])
    setSelectedIndex(0)
    setPlayheadMs(0)
  }, [sourceTimeline?.id])

  const durationMs = workspace.data?.duration_ms ?? 15000
  const selectedItem = items[selectedIndex] ?? null
  const selectedAsset = workspace.data?.available_assets.find(asset => asset.id === selectedItem?.asset_id) ?? null
  const visibleAssets = workspace.data?.available_assets.filter(asset => assetFilter === 'all' || asset.asset_type === assetFilter) ?? []
  const mainItems = useMemo(() => items.filter(item => item.track_type === 'main_video'), [items])
  const audioItems = useMemo(() => items.filter(item => item.track_type === 'audio'), [items])
  const subtitleItems = useMemo(() => items.filter(item => item.track_type === 'subtitle'), [items])
  const unresolvedCount = mainItems.filter(item => !item.asset_id).length
  const timelineWidth = Math.max(900, (durationMs / 1000) * timelineZoom)

  useEffect(() => {
    const currentIndex = items.findIndex(item => item.track_type === 'main_video' && playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms)
    if (currentIndex >= 0 && currentIndex !== selectedIndex) setSelectedIndex(currentIndex)
  }, [playheadMs, items, selectedIndex])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !selectedItem?.asset_id) return
    const expectedTime = (selectedItem.source_in_ms ?? 0) / 1000
    if (Math.abs(video.currentTime - expectedTime) > .05) video.currentTime = expectedTime
    if (playing) void video.play()
    else video.pause()
  }, [playing, selectedItem?.id, selectedItem?.asset_id, selectedItem?.source_in_ms])

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
    setSelectedIndex(index => Math.min(index, Math.max(0, previous.length - 1)))
    setNotice('已撤销上一步本地剪辑操作。')
  }

  const redo = () => {
    const next = future[0]
    if (!next) return
    setHistory(rows => [...rows.slice(-49), items])
    setFuture(rows => rows.slice(1))
    setItems(next)
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
    if (!selectedItem || selectedItem.track_type !== 'main_video' || videoTrackLocked) return
    const rows = mainItems.filter(item => item.id !== selectedItem.id)
    const normalized = normalizeMainTrack(rows, durationMs)
    const nextItems = replaceMainTrack(items, normalized)
    commitItems(nextItems, `已删除 ${selectedItem.label}，后续片段已波纹前移。`, normalized[0]?.id ?? null)
    setPlayheadMs(Math.min(playheadMs, durationMs))
  }

  const beginTrim = (event: React.PointerEvent, item: TimelineItem, edge: 'start' | 'end') => {
    event.stopPropagation()
    if (!item.asset_id || videoTrackLocked) return
    const startX = event.clientX
    const original = { sourceIn: item.source_in_ms ?? 0, sourceOut: item.source_out_ms ?? item.asset_duration_ms ?? 0 }
    setHistory(rows => [...rows.slice(-49), items])
    setFuture([])
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

  if (workspace.isPending) return <main className={styles.loading}><Film /><strong>正在装载新版剪辑台原型…</strong></main>
  if (!workspace.data || workspace.error) return <main className={styles.loading}><AlertTriangle /><strong>原型无法读取当前剪辑项目</strong></main>

  return <main className={styles.prototype}>
    <header className={styles.topbar}>
      <Link to={`/editor?project=${projectId}`} title="返回现有剪辑台"><ArrowLeft /></Link>
      <div className={styles.projectTitle}>
        <span>剪辑台新版原型</span>
        <strong>{workspace.data.project_title}</strong>
      </div>
      <button className={styles.versionButton}>时间线 v{sourceTimeline?.version_number ?? '--'} <small>本地草稿</small></button>
      <div className={styles.topActions}>
        <button title="撤销" disabled={!history.length} onClick={undo}><Undo2 /></button>
        <button title="重做" disabled={!future.length} onClick={redo}><Redo2 /></button>
        <button className={styles.primaryAction} onClick={() => setNotice(unresolvedCount ? `仍有 ${unresolvedCount} 个画面缺口，请先处理。` : '草稿检查通过，可以保存为新版本。')}>
          {unresolvedCount ? <AlertTriangle /> : <CheckCircle2 />}
          {unresolvedCount ? `处理 ${unresolvedCount} 个问题` : '保存并检查'}
        </button>
      </div>
    </header>

    <section className={styles.statusbar} data-warning={unresolvedCount > 0}>
      {unresolvedCount ? <AlertTriangle /> : <CheckCircle2 />}
      <span>{notice}</span>
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
            draggable={asset.asset_type === 'video'}
            data-selected={selectedAsset?.id === asset.id}
            onDragStart={() => { setDraggedAssetId(asset.id); setDraggedItemId(null); setNotice(`正在拖动 ${asset.node_key ?? asset.role}，可投放到画面轨。`) }}
            onDragEnd={() => setDraggedAssetId(null)}
            onClick={() => {
            const item = items.find(row => row.asset_id === asset.id)
            if (item) selectItem(item)
            else setNotice(`${asset.node_key ?? asset.role} 尚未加入当前时间线。`)
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
          <section className={styles.clipIdentity}><i><Film /></i><div><strong>{selectedItem.label}</strong><span>对应分镜 {selectedItem.label.split('.')[0]}</span></div></section>
          <section>
            <h3>素材范围</h3>
            <div className={styles.rangeLabels}><span>{seconds(selectedItem.source_in_ms)}</span><b>{seconds((selectedItem.source_out_ms ?? 0) - (selectedItem.source_in_ms ?? 0))}</b><span>{seconds(selectedItem.source_out_ms)}</span></div>
            <div className={styles.trimHint}>直接拖动时间线片段两侧把手裁切</div>
          </section>
          <section>
            <h3>片段操作</h3>
            <div className={styles.actionGrid}>
              <button onClick={splitSelected}><Scissors />播放头分割</button>
              <button onClick={() => shiftItem(-1)}><ChevronLeft />向前移动</button>
              <button onClick={() => shiftItem(1)}><ChevronRight />向后移动</button>
              <button onClick={deleteSelected}>移除片段</button>
            </div>
          </section>
          <section>
            <h3>转场</h3>
            <label>入场<select defaultValue="cut"><option value="cut">直接切换</option><option value="fade">淡入</option></select></label>
            <label>出场<select defaultValue="cut"><option value="cut">直接切换</option><option value="fade">淡出</option></select></label>
          </section>
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
        <button>磁吸 100ms</button>
        <label>缩放<input aria-label="时间线缩放" type="range" min="40" max="180" value={timelineZoom} onChange={event => setTimelineZoom(Number(event.target.value))} /></label>
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
          <div className={styles.trackLane} data-empty={!audioItems.length}>{audioItems.length ? audioItems.map(item => <button key={item.id} className={styles.audioClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }}>{item.label}</button>) : <span>尚未启用配音</span>}</div>
        </div>
        <div className={styles.trackRow} data-track-hidden={subtitleTrackHidden}>
          <label><Subtitles /><span>字幕</span><button title={subtitleTrackHidden ? '显示字幕轨' : '隐藏字幕轨'} onClick={() => setSubtitleTrackHidden(value => !value)}>{subtitleTrackHidden ? <EyeOff /> : <Eye />}</button></label>
          <div className={styles.trackLane} data-empty={!subtitleItems.length}>{subtitleItems.length ? subtitleItems.map(item => <button key={item.id} className={styles.subtitleClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }}>{item.label}</button>) : <span>尚未启用字幕</span>}</div>
        </div>
        <i className={styles.playhead} style={{ left: `${84 + timelineWidth * (playheadMs / durationMs)}px` }}><b /></i>
        </div>
      </div>
      <footer><span><Sparkles />AI 初剪依据和版本证据已收进右侧抽屉</span><span>Space 播放 · S 分割 · Delete 删除 · Ctrl+Z 撤销</span></footer>
    </section>
  </main>
}
