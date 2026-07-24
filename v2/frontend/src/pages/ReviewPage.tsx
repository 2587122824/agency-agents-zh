import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Check, CheckCircle2, ChevronLeft, ChevronRight, FileCheck2,
  Images, Maximize2, Pencil, RefreshCw, RotateCcw, Scaling, Search, Undo2, X, ZoomIn, ZoomOut,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { ProductionAsset } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './ReviewPage.module.css'

type ReviewChoice = { asset: ProductionAsset; action: 'reject' }
type RevisionChoice = { asset: ProductionAsset; scope: 'storyboard' | 'production' | 'editing' }
type AssetFilter = 'pending' | 'all' | 'approved' | 'archived'

const APPROVAL_RATIONALE = '人工确认画面符合分镜合同'
const stateLabels: Record<string, string> = {
  created: '待验证', verified: '待审核', review_required: '待人工确认', approved: '已批准', archived: '已归档',
}
const filterLabels: Record<AssetFilter, string> = { pending: '待审核', all: '全部', approved: '已批准', archived: '已归档' }
const faceLabels: Record<string, string> = { required: '需要清晰人脸', optional: '人脸可见或不可见', not_visible: '人脸不可见' }
const textLabels: Record<string, string> = { forbidden: '画面不得出现文字', optional: '文字可选', required: '需要画面文字' }

function mediaUrl(asset: ProductionAsset) {
  return `/api/v1/projects/${asset.project_id}/assets/${asset.id}/content`
}

function isReviewable(asset: ProductionAsset) {
  return asset.state === 'verified' || asset.state === 'review_required'
}

function assetLabel(asset: ProductionAsset) {
  return asset.review_context.shot.shot_code as string || asset.node_key || asset.role
}

function sortAssets(left: ProductionAsset, right: ProductionAsset) {
  const leftSequence = Number(left.review_context.shot.sequence_number ?? Number.MAX_SAFE_INTEGER)
  const rightSequence = Number(right.review_context.shot.sequence_number ?? Number.MAX_SAFE_INTEGER)
  return leftSequence - rightSequence || assetLabel(left).localeCompare(assetLabel(right))
}

export function ReviewPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const client = useQueryClient()
  const previewRef = useRef<HTMLDivElement>(null)
  const [projectId, setProjectId] = useState(() => searchParams.get('project') ?? '')
  const [selectedAssetId, setSelectedAssetId] = useState(() => searchParams.get('asset') ?? '')
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([])
  const [filter, setFilter] = useState<AssetFilter>('pending')
  const [zoom, setZoom] = useState<number | null>(null)
  const [reviewChoice, setReviewChoice] = useState<ReviewChoice | null>(null)
  const [revokeChoice, setRevokeChoice] = useState<ProductionAsset | null>(null)
  const [batchConfirmOpen, setBatchConfirmOpen] = useState(false)
  const [retryChoice, setRetryChoice] = useState<ProductionAsset | null>(null)
  const [revisionChoice, setRevisionChoice] = useState<RevisionChoice | null>(null)
  const [rationale, setRationale] = useState('')

  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.projects(), refetchInterval: 5000 })
  const quality = useQuery({
    queryKey: ['quality-review', projectId],
    queryFn: () => api.qualityReview(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 5000,
  })
  const refresh = () => client.invalidateQueries({ queryKey: ['quality-review', projectId] })

  const activeAssets = useMemo(() => {
    if (!quality.data?.active_snapshot_id) return []
    return quality.data.assets
      .filter(asset => asset.snapshot_id === quality.data.active_snapshot_id && ['image', 'video', 'audio'].includes(asset.asset_type))
      .sort(sortAssets)
  }, [quality.data])
  const visibleAssets = useMemo(() => activeAssets.filter(asset => {
    if (filter === 'pending') return isReviewable(asset) || asset.state === 'created'
    if (filter === 'approved') return asset.state === 'approved'
    if (filter === 'archived') return asset.state === 'archived'
    return asset.state !== 'archived'
  }), [activeAssets, filter])
  const selectedAsset = visibleAssets.find(asset => asset.id === selectedAssetId) ?? visibleAssets[0] ?? null
  const reviewableAssets = activeAssets.filter(isReviewable)
  const reviewedCount = activeAssets.filter(asset => ['approved', 'archived'].includes(asset.state)).length

  useEffect(() => {
    if (!selectedAsset && visibleAssets[0]) setSelectedAssetId(visibleAssets[0].id)
  }, [selectedAsset, visibleAssets])

  useEffect(() => {
    if (!projectId) return
    const next = new URLSearchParams(searchParams)
    next.set('project', projectId)
    if (selectedAsset?.id) next.set('asset', selectedAsset.id)
    else next.delete('asset')
    setSearchParams(next, { replace: true })
  }, [projectId, selectedAsset?.id])

  const moveSelection = (direction: -1 | 1, fromId = selectedAsset?.id) => {
    if (!visibleAssets.length) return
    const currentIndex = Math.max(0, visibleAssets.findIndex(asset => asset.id === fromId))
    const nextIndex = Math.min(visibleAssets.length - 1, Math.max(0, currentIndex + direction))
    setSelectedAssetId(visibleAssets[nextIndex].id)
    setZoom(null)
  }

  const moveToNextPending = (completedId: string) => {
    const remaining = reviewableAssets.filter(asset => asset.id !== completedId)
    const currentIndex = reviewableAssets.findIndex(asset => asset.id === completedId)
    const next = remaining.find((_, index) => index >= currentIndex) ?? remaining[0]
    if (next) setSelectedAssetId(next.id)
  }

  const verify = useMutation({
    mutationFn: (asset: ProductionAsset) => api.verifyAsset(projectId, asset),
    onSuccess: refresh,
  })
  const directReview = useMutation({
    mutationFn: ({ asset, action, reason }: { asset: ProductionAsset; action: 'approve' | 'reject'; reason: string }) =>
      api.reviewAsset(projectId, asset, action, reason),
    onSuccess: async (_, variables) => {
      setReviewChoice(null)
      setRationale('')
      moveToNextPending(variables.asset.id)
      await refresh()
    },
  })
  const batchApprove = useMutation({
    mutationFn: async () => {
      const assets = selectedAssetIds
        .map(id => activeAssets.find(asset => asset.id === id))
        .filter((asset): asset is ProductionAsset => Boolean(asset && isReviewable(asset)))
      for (const asset of assets) await api.reviewAsset(projectId, asset, 'approve', APPROVAL_RATIONALE)
      return assets
    },
    onSuccess: async assets => {
      setBatchConfirmOpen(false)
      setSelectedAssetIds([])
      if (assets[0]) moveToNextPending(assets[0].id)
      await refresh()
    },
  })
  const revokeApproval = useMutation({
    mutationFn: (asset: ProductionAsset) => api.revokeAssetApproval(projectId, asset),
    onSuccess: async asset => {
      setRevokeChoice(null)
      setFilter('pending')
      setSelectedAssetId(asset.id)
      await refresh()
    },
  })
  const retryQC = useMutation({
    mutationFn: (asset: ProductionAsset) => api.retryAssetQC(projectId, asset),
    onSuccess: async () => { setRetryChoice(null); await refresh() },
  })
  const requestRevision = useMutation({
    mutationFn: () => api.requestAssetRevision(projectId, revisionChoice!.asset, revisionChoice!.scope, rationale),
    onSuccess: async result => {
      setRevisionChoice(null)
      setRationale('')
      await refresh()
      navigate(result.next_action.path)
    },
  })

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.matches('input, textarea, select') || reviewChoice || revokeChoice || revisionChoice || retryChoice || batchConfirmOpen) return
      if (event.key === 'ArrowLeft') { event.preventDefault(); moveSelection(-1) }
      if (event.key === 'ArrowRight') { event.preventDefault(); moveSelection(1) }
      if (event.key.toLowerCase() === 'a' && selectedAsset && isReviewable(selectedAsset)) {
        event.preventDefault()
        directReview.mutate({ asset: selectedAsset, action: 'approve', reason: APPROVAL_RATIONALE })
      }
      if (event.key.toLowerCase() === 'r' && selectedAsset && isReviewable(selectedAsset)) {
        event.preventDefault()
        setReviewChoice({ asset: selectedAsset, action: 'reject' })
        setRationale('')
      }
      if (event.key === ' ') { event.preventDefault(); setZoom(current => current === null ? 1 : null) }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [selectedAsset, visibleAssets, reviewChoice, revokeChoice, revisionChoice, retryChoice, batchConfirmOpen])

  const reviewProjects = projects.data?.filter(project => ['producing', 'quality_review', 'blocked'].includes(project.status)) ?? []
  const error = quality.error || verify.error || directReview.error || batchApprove.error || revokeApproval.error || retryQC.error || requestRevision.error
  const shot = selectedAsset?.review_context.shot ?? {}
  const selectedReviewableCount = selectedAssetIds.filter(id => reviewableAssets.some(asset => asset.id === id)).length

  return <>
    <PageHeader
      eyebrow="QUALITY REVIEW"
      title="素材审核"
      description="连续检查当前生产快照中的素材，人工结论与生产证据分别记录。"
      actions={<button className="secondaryButton" onClick={() => { client.invalidateQueries({ queryKey: ['projects'] }); refresh() }}><RefreshCw size={14} />刷新</button>}
    />
    <main className={styles.page}>
      <section className={styles.topbar}>
        <label>
          <span>审核项目</span>
          <select value={projectId} onChange={event => { setProjectId(event.target.value); setSelectedAssetId(''); setSelectedAssetIds([]) }}>
            <option value="">选择待审核项目</option>
            {reviewProjects.map(project => <option key={project.id} value={project.id}>{project.title}</option>)}
          </select>
        </label>
        {quality.data && <div className={styles.progress}>
          <div><strong>{reviewedCount}/{activeAssets.length}</strong><span>已处理</span></div>
          <i><b style={{ width: `${activeAssets.length ? reviewedCount / activeAssets.length * 100 : 0}%` }} /></i>
          <Link className="secondaryButton" to={`/projects/${projectId}/contact-sheet`}><Images size={14} />联络表</Link>
        </div>}
      </section>

      {!projectId && <section className={styles.empty}><FileCheck2 /><strong>选择一个项目开始审核</strong><span>这里会按分镜顺序展示当前生产快照的素材。</span></section>}
      {quality.isPending && projectId && <section className={styles.empty}>正在读取素材与分镜合同…</section>}
      {quality.data && <section className={styles.reviewShell}>
        <aside className={styles.queue}>
          <header>
            <div><span>SHOT QUEUE</span><strong>素材队列</strong></div>
            <b>{visibleAssets.length}</b>
          </header>
          <nav className={styles.filters}>
            {(Object.keys(filterLabels) as AssetFilter[]).map(item => <button key={item} data-selected={filter === item} onClick={() => setFilter(item)}>{filterLabels[item]}</button>)}
          </nav>
          <div className={styles.batchBar}>
            <span>已选 {selectedReviewableCount}</span>
            <button disabled={!selectedReviewableCount} onClick={() => setBatchConfirmOpen(true)}><Check size={13} />批量通过</button>
          </div>
          <div className={styles.assetList}>
            {visibleAssets.map(asset => <article key={asset.id} data-selected={selectedAsset?.id === asset.id}>
              <label className={styles.checkbox} title="选择用于批量通过">
                <input
                  type="checkbox"
                  checked={selectedAssetIds.includes(asset.id)}
                  disabled={!isReviewable(asset)}
                  onChange={event => setSelectedAssetIds(current => event.target.checked ? [...current, asset.id] : current.filter(id => id !== asset.id))}
                />
              </label>
              <button onClick={() => { setSelectedAssetId(asset.id); setZoom(null) }}>
                <span className={styles.thumbnail}>
                  {asset.content_hash && asset.asset_type === 'image' ? <img src={mediaUrl(asset)} alt="" /> : <Search />}
                </span>
                <span className={styles.assetName}><strong>{assetLabel(asset)}</strong><small>{asset.width && asset.height ? `${asset.width}×${asset.height}` : asset.asset_type}</small></span>
                <em data-state={asset.state}>{stateLabels[asset.state] ?? asset.state}</em>
              </button>
            </article>)}
            {!visibleAssets.length && <p>当前筛选条件下没有素材</p>}
          </div>
        </aside>

        <section className={styles.viewer}>
          {selectedAsset ? <>
            <header>
              <button title="上一张" onClick={() => moveSelection(-1)}><ChevronLeft /></button>
              <div><span>{assetLabel(selectedAsset)}</span><strong>{stateLabels[selectedAsset.state] ?? selectedAsset.state}</strong></div>
              <button title="下一张" onClick={() => moveSelection(1)}><ChevronRight /></button>
            </header>
            <div className={styles.canvas} ref={previewRef} data-fit={zoom === null}>
              {selectedAsset.content_hash && selectedAsset.asset_type === 'image' && <img
                src={mediaUrl(selectedAsset)}
                alt={assetLabel(selectedAsset)}
                data-fit={zoom === null}
                style={zoom === null ? undefined : { width: `${selectedAsset.width ? selectedAsset.width * zoom : 480 * zoom}px` }}
              />}
              {selectedAsset.content_hash && selectedAsset.asset_type === 'video' && <video src={mediaUrl(selectedAsset)} controls preload="metadata" />}
              {!selectedAsset.content_hash && <div className={styles.noPreview}><Search /><span>文件尚未完成验证</span></div>}
            </div>
            <footer className={styles.viewerTools}>
              <button title="缩小" onClick={() => setZoom(current => Math.max(.25, (current ?? 1) - .25))}><ZoomOut /></button>
              <button title="适应窗口" data-active={zoom === null} onClick={() => setZoom(null)}><Scaling /></button>
              <button title="原始大小" data-active={zoom === 1} onClick={() => setZoom(1)}>100%</button>
              <button title="放大" onClick={() => setZoom(current => Math.min(3, (current ?? 1) + .25))}><ZoomIn /></button>
              <button title="全屏预览" onClick={() => previewRef.current?.requestFullscreen()}><Maximize2 /></button>
            </footer>
          </> : <div className={styles.noPreview}><Search /><span>选择左侧素材查看大图</span></div>}
        </section>

        <aside className={styles.inspector}>
          {selectedAsset && <>
            <header><span>REVIEW EVIDENCE</span><h2>审核依据</h2></header>
            <section>
              <h3>分镜要求</h3>
              <dl>
                <div><dt>动作</dt><dd>{String(shot.action ?? '未指定')}</dd></div>
                <div><dt>构图</dt><dd>{String(shot.composition ?? '未指定')}</dd></div>
                <div><dt>景别</dt><dd>{String(shot.framing ?? '未指定')}</dd></div>
                <div><dt>人脸</dt><dd>{faceLabels[String(shot.face_visibility)] ?? String(shot.face_visibility ?? '未指定')}</dd></div>
                <div><dt>文字</dt><dd>{textLabels[String(shot.text_policy)] ?? String(shot.text_policy ?? '未指定')}</dd></div>
                {shot.required_on_screen_text != null && <div><dt>指定文字</dt><dd>{String(shot.required_on_screen_text)}</dd></div>}
              </dl>
            </section>
            <section>
              <h3>文件事实</h3>
              <dl>
                <div><dt>类型</dt><dd>{selectedAsset.mime_type ?? selectedAsset.asset_type}</dd></div>
                <div><dt>尺寸</dt><dd>{selectedAsset.width && selectedAsset.height ? `${selectedAsset.width} × ${selectedAsset.height}` : '未检测'}</dd></div>
                <div><dt>大小</dt><dd>{selectedAsset.byte_size ? `${(selectedAsset.byte_size / 1024).toFixed(1)} KB` : '未检测'}</dd></div>
                <div><dt>哈希</dt><dd><code>{selectedAsset.content_hash?.slice(0, 16) ?? '待验证'}</code></dd></div>
              </dl>
            </section>
            {selectedAsset.latest_qc_candidate && <section>
              <h3>智能审核建议</h3>
              {selectedAsset.latest_qc_candidate.findings.length
                ? selectedAsset.latest_qc_candidate.findings.map(finding => <p className={styles.finding} key={finding.finding_code}><AlertTriangle />{finding.summary}</p>)
                : <p className={styles.muted}>没有定位到具体问题，最终仍由人工决定。</p>}
            </section>}
            {selectedAsset.latest_qc_agent_run?.status === 'failed' && <section className={styles.agentError}>
              <AlertTriangle /><div><strong>智能审核失败</strong><span>{selectedAsset.latest_qc_agent_run.error_detail ?? selectedAsset.latest_qc_agent_run.error_code}</span></div>
              <button onClick={() => setRetryChoice(selectedAsset)}><RotateCcw size={13} />重跑</button>
            </section>}
            {selectedAsset.revision_requests[0] && <p className={styles.revisionRecorded}>已登记调整：{selectedAsset.revision_requests[0].status}</p>}
            <footer className={styles.actions}>
              {selectedAsset.state === 'created' && <button className="primaryButton" disabled={verify.isPending} onClick={() => verify.mutate(selectedAsset)}><FileCheck2 size={14} />验证文件</button>}
              {isReviewable(selectedAsset) && <>
                <button className="secondaryButton" onClick={() => { setRevisionChoice({ asset: selectedAsset, scope: 'storyboard' }); setRationale('') }}><Pencil size={14} />需要调整</button>
                <button className="secondaryButton" onClick={() => { setReviewChoice({ asset: selectedAsset, action: 'reject' }); setRationale('') }}><X size={14} />拒绝</button>
                <button className="primaryButton" disabled={directReview.isPending} onClick={() => directReview.mutate({ asset: selectedAsset, action: 'approve', reason: APPROVAL_RATIONALE })}><Check size={14} />通过</button>
              </>}
              {selectedAsset.state === 'approved' && selectedAsset.approval_revocation.allowed && <>
                <span className={styles.approved}><CheckCircle2 />已批准进入后续流程</span>
                <button className="secondaryButton" onClick={() => setRevokeChoice(selectedAsset)}><Undo2 size={14} />撤销通过</button>
              </>}
              {selectedAsset.state === 'approved' && !selectedAsset.approval_revocation.allowed && <span className={styles.approvedBlocked}><CheckCircle2 /><span><b>已批准进入后续流程</b><small>{selectedAsset.approval_revocation.message}</small></span></span>}
              {selectedAsset.state === 'archived' && <span className={styles.archived}>该素材已归档</span>}
            </footer>
          </>}
        </aside>
      </section>}

      {quality.data?.output_gaps.map(gap => <article className={styles.gap} key={gap.dag_node_id}><AlertTriangle /><div><strong>{gap.node_key}</strong><span>{gap.message}</span></div></article>)}
      {error && <div className={styles.error}>{error.message}</div>}
    </main>

    {reviewChoice && <div className={styles.modal}><section>
      <header><AlertTriangle /><div><span>人工审核决定</span><h2>拒绝并归档素材</h2></div></header>
      <p>{assetLabel(reviewChoice.asset)}</p>
      <label>拒绝原因<textarea value={rationale} onChange={event => setRationale(event.target.value)} placeholder="说明画面哪里不符合分镜要求" /></label>
      <small>本操作只记录审核结论，不会重试、重新生成或产生费用。</small>
      <footer><button className="secondaryButton" onClick={() => { setReviewChoice(null); setRationale('') }}>取消</button><button className="primaryButton" disabled={!rationale.trim() || directReview.isPending} onClick={() => directReview.mutate({ asset: reviewChoice.asset, action: 'reject', reason: rationale })}>确认拒绝</button></footer>
    </section></div>}

    {batchConfirmOpen && <div className={styles.modal}><section>
      <header><CheckCircle2 /><div><span>批量审核</span><h2>通过选中的 {selectedReviewableCount} 个素材</h2></div></header>
      <div className={styles.batchList}>{selectedAssetIds.map(id => activeAssets.find(asset => asset.id === id)).filter((asset): asset is ProductionAsset => Boolean(asset && isReviewable(asset))).map(asset => <code key={asset.id}>{assetLabel(asset)}</code>)}</div>
      <small>只处理上面明确列出的素材，并逐项记录“{APPROVAL_RATIONALE}”。</small>
      <footer><button className="secondaryButton" onClick={() => setBatchConfirmOpen(false)}>取消</button><button className="primaryButton" disabled={batchApprove.isPending} onClick={() => batchApprove.mutate()}>{batchApprove.isPending ? '正在处理…' : '确认批量通过'}</button></footer>
    </section></div>}

    {revokeChoice && <div className={styles.modal}><section>
      <header><Undo2 /><div><span>撤销审核结论</span><h2>让素材重新进入待审核</h2></div></header>
      <p>{assetLabel(revokeChoice)}</p>
      <small>原审核报告和通过记录会继续保留，并新增一条撤销记录。本操作不会取消任务、重跑素材或产生费用。</small>
      <footer><button className="secondaryButton" onClick={() => setRevokeChoice(null)}>取消</button><button className="primaryButton" disabled={revokeApproval.isPending} onClick={() => revokeApproval.mutate(revokeChoice)}>{revokeApproval.isPending ? '正在撤销…' : '确认撤销通过'}</button></footer>
    </section></div>}

    {revisionChoice && <div className={styles.modal}><section>
      <header><Pencil /><div><span>素材调整</span><h2>这个问题应该在哪里处理？</h2></div></header>
      <p>{assetLabel(revisionChoice.asset)}</p>
      <div className={styles.scopeChoices}>
        <button type="button" data-selected={revisionChoice.scope === 'storyboard'} onClick={() => setRevisionChoice({ ...revisionChoice, scope: 'storyboard' })}><strong>分镜需要调整</strong><span>画面内容、动作、构图或人物设定本身需要改</span></button>
        <button type="button" data-selected={revisionChoice.scope === 'production'} onClick={() => setRevisionChoice({ ...revisionChoice, scope: 'production' })}><strong>生成效果需要重做</strong><span>分镜没问题，但这次模型生成结果不满意</span></button>
        <button type="button" data-selected={revisionChoice.scope === 'editing'} onClick={() => setRevisionChoice({ ...revisionChoice, scope: 'editing' })}><strong>剪辑取舍需要调整</strong><span>素材可以保留，在成片中调整选用、时长或顺序</span></button>
      </div>
      <label>具体问题<textarea value={rationale} onChange={event => setRationale(event.target.value)} placeholder="说明哪里不符合预期，以及希望如何调整" /></label>
      <small>系统只登记你选择的问题类型，不会自动重做素材、改分镜或产生费用。</small>
      <footer><button className="secondaryButton" onClick={() => { setRevisionChoice(null); setRationale('') }}>取消</button><button className="primaryButton" disabled={!rationale.trim() || requestRevision.isPending} onClick={() => requestRevision.mutate()}>登记并前往处理</button></footer>
    </section></div>}

    {retryChoice && <div className={styles.modal}><section>
      <header><RotateCcw /><div><span>精确重跑</span><h2>重新运行质量审核</h2></div></header>
      <p>{assetLabel(retryChoice)}</p>
      <small>将复用上次失败时的素材、模型和合同，并再次产生模型调用费用，不会切换模型或修改输入。</small>
      <footer><button className="secondaryButton" onClick={() => setRetryChoice(null)}>取消</button><button className="primaryButton" disabled={retryQC.isPending} onClick={() => retryQC.mutate(retryChoice)}>确认费用并重跑</button></footer>
    </section></div>}
  </>
}
