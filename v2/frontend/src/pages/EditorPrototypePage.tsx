import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Film,
  Layers3, Maximize2, Music2, Pause, Play, Plus, Redo2, Scissors, Search, Sparkles,
  Subtitles, Undo2, Volume2, WandSparkles,
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
  const videoRef = useRef<HTMLVideoElement | null>(null)

  const sourceTimeline = workspace.data?.timelines[0] ?? null
  useEffect(() => {
    if (!sourceTimeline) return
    setItems(sourceTimeline.items)
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

  useEffect(() => {
    const currentIndex = items.findIndex(item => item.track_type === 'main_video' && playheadMs >= item.timeline_in_ms && playheadMs < item.timeline_out_ms)
    if (currentIndex >= 0 && currentIndex !== selectedIndex) setSelectedIndex(currentIndex)
  }, [playheadMs, items, selectedIndex])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !selectedItem?.asset_id) return
    if (playing) void video.play()
    else video.pause()
  }, [playing, selectedItem?.asset_id])

  const selectItem = (item: TimelineItem) => {
    const index = items.indexOf(item)
    setSelectedIndex(index)
    setPlayheadMs(item.timeline_in_ms)
    setPlaying(false)
  }

  const shiftItem = (direction: -1 | 1) => {
    if (!selectedItem || selectedItem.track_type !== 'main_video') return
    const trackIndexes = items.map((item, index) => ({ item, index })).filter(row => row.item.track_type === 'main_video')
    const position = trackIndexes.findIndex(row => row.index === selectedIndex)
    const target = position + direction
    if (target < 0 || target >= trackIndexes.length) return
    const reordered = [...trackIndexes.map(row => row.item)]
    ;[reordered[position], reordered[target]] = [reordered[target], reordered[position]]
    let cursor = 0
    const normalized = reordered.map((item, index) => {
      const duration = item.timeline_out_ms - item.timeline_in_ms
      const next = { ...item, sequence_number: index + 1, timeline_in_ms: cursor, timeline_out_ms: cursor + duration }
      cursor += duration
      return next
    })
    setItems(current => current.map(item => item.track_type === 'main_video' ? normalized.shift()! : item))
    setSelectedIndex(trackIndexes[target].index)
    setNotice('已在原型草稿中调整片段顺序。')
  }

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
        <button title="撤销" onClick={() => setNotice('原型：撤销历史将在正式开发时接入。')}><Undo2 /></button>
        <button title="重做" onClick={() => setNotice('原型：重做历史将在正式开发时接入。')}><Redo2 /></button>
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
          {visibleAssets.map(asset => <button key={asset.id} data-selected={selectedAsset?.id === asset.id} onClick={() => {
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
          <button onClick={() => setPlayheadMs(0)}><ChevronLeft /></button>
          <button className={styles.playButton} onClick={() => setPlaying(value => !value)}>{playing ? <Pause /> : <Play />}</button>
          <button onClick={() => setPlayheadMs(durationMs)}><ChevronRight /></button>
          <code>{timecode(playheadMs)} <span>/ {timecode(durationMs)}</span></code>
          <div className={styles.previewScrubber} onClick={event => {
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
            <input type="range" min="0" max={selectedItem.asset_duration_ms ?? 5000} value={selectedItem.source_out_ms ?? 0} readOnly />
          </section>
          <section>
            <h3>片段操作</h3>
            <div className={styles.actionGrid}>
              <button onClick={() => setNotice(`将在 ${timecode(playheadMs)} 分割片段（原型演示）。`)}><Scissors />播放头分割</button>
              <button onClick={() => shiftItem(-1)}><ChevronLeft />向前移动</button>
              <button onClick={() => shiftItem(1)}><ChevronRight />向后移动</button>
              <button onClick={() => setNotice('已标记删除，保存前可以撤销。')}>移除片段</button>
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
        <button><Scissors />分割</button>
        <button>磁吸 100ms</button>
        <label>缩放<input type="range" min="40" max="140" defaultValue="82" /></label>
        <code>{timecode(playheadMs)}</code>
      </header>
      <div className={styles.timelineViewport} onClick={event => {
        if ((event.target as HTMLElement).closest('button')) return
        const rect = event.currentTarget.getBoundingClientRect()
        const labelWidth = 84
        const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - labelWidth) / (rect.width - labelWidth)))
        setPlayheadMs(Math.round(ratio * durationMs))
        setPlaying(false)
      }}>
        <div className={styles.ruler}><span /><div>{[0, 3, 6, 9, 12, 15].map(value => <i key={value} style={{ left: `${(value / 15) * 100}%` }}>{`00:${String(value).padStart(2, '0')}`}</i>)}</div></div>
        <div className={styles.trackRow}>
          <label><Film />画面</label>
          <div className={styles.trackLane}>
            {mainItems.map(item => <button
              key={item.id}
              className={item.asset_id ? styles.videoClip : styles.gapClip}
              data-selected={selectedItem?.id === item.id}
              style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }}
              onClick={() => selectItem(item)}
            >{item.asset_id ? <><Film /><span><strong>{item.label}</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></> : <><AlertTriangle /><span><strong>缺少画面</strong><small>{seconds(item.timeline_out_ms - item.timeline_in_ms)}</small></span></>}</button>)}
          </div>
        </div>
        <div className={styles.trackRow}>
          <label><Music2 />声音</label>
          <div className={styles.trackLane} data-empty={!audioItems.length}>{audioItems.length ? audioItems.map(item => <button key={item.id} className={styles.audioClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }}>{item.label}</button>) : <span>尚未启用配音</span>}</div>
        </div>
        <div className={styles.trackRow}>
          <label><Subtitles />字幕</label>
          <div className={styles.trackLane} data-empty={!subtitleItems.length}>{subtitleItems.length ? subtitleItems.map(item => <button key={item.id} className={styles.subtitleClip} style={{ left: `${(item.timeline_in_ms / durationMs) * 100}%`, width: `${((item.timeline_out_ms - item.timeline_in_ms) / durationMs) * 100}%` }}>{item.label}</button>) : <span>尚未启用字幕</span>}</div>
        </div>
        <i className={styles.playhead} style={{ left: `calc(84px + (100% - 84px) * ${playheadMs / durationMs})` }}><b /></i>
      </div>
      <footer><span><Sparkles />AI 初剪依据和版本证据已收进右侧抽屉</span><span>Space 播放 · S 分割 · Delete 删除 · Ctrl+Z 撤销</span></footer>
    </section>
  </main>
}
