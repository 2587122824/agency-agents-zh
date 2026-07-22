import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, CheckCircle2, Clock3, FileCheck2, Images, Image, Pencil, RefreshCw, RotateCcw, ShieldAlert, Sparkles, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { ProductionAsset } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './ReviewPage.module.css'

type ReviewChoice = { asset: ProductionAsset; action: 'approve' | 'reject' }
type RevisionChoice = { asset: ProductionAsset; scope: 'storyboard' | 'production' | 'editing' }

const stateLabels: Record<string, string> = { created: '待验证', verified: '待审核', review_required: '待人工确认', approved: '已批准', archived: '已归档' }
const categoryLabels: Record<string, string> = { identity: '人物一致性', continuity: '连续性', semantic_match: '内容匹配', composition: '构图', visible_text: '画面文字', motion: '动态', audio_content: '音频内容' }

function mediaUrl(asset: ProductionAsset) {
  return `/api/v1/projects/${asset.project_id}/assets/${asset.id}/content`
}

export function ReviewPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const client = useQueryClient()
  const [projectId, setProjectId] = useState(() => searchParams.get('project') ?? '')
  const [reviewChoice, setReviewChoice] = useState<ReviewChoice | null>(null)
  const [retryChoice, setRetryChoice] = useState<ProductionAsset | null>(null)
  const [revisionChoice, setRevisionChoice] = useState<RevisionChoice | null>(null)
  const [rationale, setRationale] = useState('')
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.projects(), refetchInterval: 5000 })
  const quality = useQuery({ queryKey: ['quality-review', projectId], queryFn: () => api.qualityReview(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const refresh = () => client.invalidateQueries({ queryKey: ['quality-review', projectId] })
  const verify = useMutation({ mutationFn: (asset: ProductionAsset) => api.verifyAsset(projectId, asset), onSuccess: refresh })
  const runQC = useMutation({ mutationFn: (asset: ProductionAsset) => api.runAssetQC(projectId, asset), onSuccess: refresh })
  const retryQC = useMutation({ mutationFn: (asset: ProductionAsset) => api.retryAssetQC(projectId, asset), onSuccess: async () => { setRetryChoice(null); await refresh() } })
  const review = useMutation({
    mutationFn: () => api.reviewAsset(projectId, reviewChoice!.asset, reviewChoice!.action, rationale),
    onSuccess: async () => { setReviewChoice(null); setRationale(''); await refresh() },
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
  const reviewProjects = projects.data?.filter(project => ['producing', 'quality_review', 'blocked'].includes(project.status)) ?? []
  const error = quality.error || verify.error || runQC.error || retryQC.error || review.error || requestRevision.error
  return <>
    <PageHeader eyebrow="QUALITY REVIEW" title="素材审核" description="文件事实、QC 证据和人工结论分别持久化，不把检测器意见伪装成用户决策。" actions={<button className="secondaryButton" onClick={() => { client.invalidateQueries({ queryKey: ['projects'] }); refresh() }}><RefreshCw size={14} />刷新</button>} />
    <main className={styles.layout}>
      <aside className={styles.projects}>
        <header><span>ACTIVE PROJECTS</span><h2>待审核项目</h2><b>{reviewProjects.length}</b></header>
        {reviewProjects.map(project => <button key={project.id} data-selected={projectId === project.id} onClick={() => setProjectId(project.id)}><i>{project.status === 'blocked' ? <AlertTriangle /> : <Clock3 />}</i><span><strong>{project.title}</strong><small>{project.status}</small></span></button>)}
        {!reviewProjects.length && <p>暂无进入生产或审核阶段的项目</p>}
      </aside>

      <section className={styles.workspace}>
        {!projectId && <div className={styles.empty}><FileCheck2 /><strong>选择一个项目开始审核</strong><span>素材必须先完成真实文件验证，才能生成 QC 报告。</span></div>}
        {quality.isPending && projectId && <div className={styles.empty}>正在读取质量事实…</div>}
        {quality.data && <>
          <div className={styles.summary}><div><span>当前步骤</span><h2>{quality.data.next_action.label}</h2><Link className="secondaryButton" to={`/projects/${projectId}/contact-sheet`}><Images size={14} />素材联络表</Link></div>{Object.entries(quality.data.counts).map(([state, count]) => <div key={state}><strong>{count}</strong><small>{stateLabels[state] ?? state}</small></div>)}</div>
          {quality.data.output_gaps.map(gap => <article className={styles.gap} key={gap.dag_node_id}><ShieldAlert /><div><strong>{gap.node_key}</strong><span>{gap.code} · {gap.message}</span></div></article>)}
          <div className={styles.assetGrid}>{quality.data.assets.map(asset => <article className={styles.assetCard} key={asset.id} data-state={asset.state}>
            <div className={styles.preview}>{asset.content_hash && asset.asset_type === 'image' ? <img src={mediaUrl(asset)} alt={asset.node_key ?? asset.role} /> : asset.content_hash && asset.asset_type === 'video' ? <video src={mediaUrl(asset)} controls preload="metadata" /> : <Image />}</div>
            <header><div><strong>{asset.node_key ?? asset.role}</strong><span>{asset.asset_type} · {asset.width && asset.height ? `${asset.width}×${asset.height}` : '未探测规格'}</span></div><em>{stateLabels[asset.state] ?? asset.state}</em></header>
            <div className={styles.meta}><code>{asset.content_hash?.slice(0, 18) ?? '等待文件验证'}</code><span>{asset.byte_size ? `${(asset.byte_size / 1024).toFixed(1)} KB` : asset.storage_backend}</span></div>
            {asset.latest_qc_report && <section className={styles.report}><div><strong>{asset.latest_qc_report.status}</strong><small>{asset.latest_qc_report.ruleset_version}</small></div>{asset.latest_qc_report.findings.map(finding => <p key={finding.id}><AlertTriangle /><span><b>{finding.code}</b>{finding.disposition}</span></p>)}</section>}
            {asset.latest_qc_candidate && <section className={styles.agentReport}><header><div><Sparkles /><strong>质量审核智能体建议</strong></div><small>{asset.latest_qc_candidate.analyzer_version}</small></header>{asset.latest_qc_candidate.findings.length === 0 ? <p className={styles.noFinding}>未发现可可靠定位的问题，仍需你确认素材是否采用。</p> : asset.latest_qc_candidate.findings.map(finding => <article key={finding.finding_code}><div><b>{categoryLabels[finding.category] ?? finding.category}</b><em>{Math.round(finding.confidence * 100)}% 置信度</em></div><p>{finding.summary}</p><small>建议：{finding.suggested_review_action}</small></article>)}</section>}
            {asset.latest_qc_agent_run?.status === 'failed' && <section className={styles.agentError}><AlertTriangle /><div><strong>智能审核失败</strong><span>{asset.latest_qc_agent_run.error_detail ?? asset.latest_qc_agent_run.error_code}</span></div></section>}
            {asset.affected_downstream_node_keys.length > 0 && <p className={styles.impact}>影响下游：{asset.affected_downstream_node_keys.join('、')}</p>}
            {asset.revision_requests[0] && <p className={styles.revisionRecorded}>已登记：{asset.revision_requests[0].issue_scope === 'storyboard' ? '分镜调整' : asset.revision_requests[0].issue_scope === 'production' ? '重新制作' : '剪辑调整'} · {asset.revision_requests[0].status}</p>}
            <footer>
              {asset.state === 'created' && <button className="primaryButton" disabled={verify.isPending} onClick={() => verify.mutate(asset)}><FileCheck2 size={14} />验证文件</button>}
              {asset.state === 'verified' && asset.latest_qc_agent_run?.status !== 'failed' && <button className="primaryButton" disabled={runQC.isPending} onClick={() => runQC.mutate(asset)}>{asset.asset_type === 'image' ? <Sparkles size={14} /> : <ShieldAlert size={14} />}{asset.asset_type === 'image' ? '智能审核' : '进入人工审核'}</button>}
              {asset.state === 'verified' && asset.latest_qc_agent_run?.status === 'failed' && <button className="primaryButton" onClick={() => setRetryChoice(asset)}><RotateCcw size={14} />重跑本次审核</button>}
              {asset.state === 'review_required' && <><button className="secondaryButton" onClick={() => { setRevisionChoice({ asset, scope: 'storyboard' }); setRationale('') }}><Pencil size={14} />需要调整</button><button className="secondaryButton" onClick={() => setReviewChoice({ asset, action: 'reject' })}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => setReviewChoice({ asset, action: 'approve' })}><Check size={14} />批准</button></>}
              {asset.state === 'approved' && <span className={styles.approved}><CheckCircle2 />已批准进入剪辑候选</span>}
            </footer>
          </article>)}</div>
          {!quality.data.assets.length && !quality.data.output_gaps.length && <div className={styles.empty}>当前项目没有已登记素材</div>}
        </>}
        {error && <div className={styles.error}>{error.message}</div>}
      </section>
    </main>
    {reviewChoice && <div className={styles.modal}><section><header>{reviewChoice.action === 'approve' ? <CheckCircle2 /> : <AlertTriangle />}<div><span>人工审核决定</span><h2>{reviewChoice.action === 'approve' ? '批准素材' : '拒绝并归档素材'}</h2></div></header><p>{reviewChoice.asset.node_key} · {reviewChoice.asset.latest_qc_candidate?.id ?? reviewChoice.asset.latest_qc_report?.id}</p><label>审核依据<textarea value={rationale} onChange={event => setRationale(event.target.value)} placeholder="记录你判断通过或拒绝的具体依据" /></label><small>本操作只记录审核结论，不会重试、重新生成或产生费用。</small><footer><button className="secondaryButton" onClick={() => { setReviewChoice(null); setRationale('') }}>取消</button><button className="primaryButton" disabled={!rationale.trim() || review.isPending} onClick={() => review.mutate()}>{reviewChoice.action === 'approve' ? '确认批准' : '确认拒绝'}</button></footer></section></div>}
    {revisionChoice && <div className={styles.modal}><section><header><Pencil /><div><span>素材调整</span><h2>这个问题应该在哪里处理？</h2></div></header><p>{revisionChoice.asset.node_key ?? revisionChoice.asset.role}</p><div className={styles.scopeChoices}>
      <button type="button" data-selected={revisionChoice.scope === 'storyboard'} onClick={() => setRevisionChoice({ ...revisionChoice, scope: 'storyboard' })}><strong>分镜需要调整</strong><span>画面内容、动作、构图或人物设定本身需要改</span></button>
      <button type="button" data-selected={revisionChoice.scope === 'production'} onClick={() => setRevisionChoice({ ...revisionChoice, scope: 'production' })}><strong>生成效果需要重做</strong><span>分镜没问题，但这次模型生成结果不满意</span></button>
      <button type="button" data-selected={revisionChoice.scope === 'editing'} onClick={() => setRevisionChoice({ ...revisionChoice, scope: 'editing' })}><strong>剪辑取舍需要调整</strong><span>素材可以保留，在成片中调整选用、时长或顺序</span></button>
    </div><label>具体问题<textarea value={rationale} onChange={event => setRationale(event.target.value)} placeholder="说明哪里不符合预期，以及希望如何调整" /></label><small>系统只登记你选择的问题类型，不会自动重做素材、改分镜或产生费用。</small><footer><button className="secondaryButton" onClick={() => { setRevisionChoice(null); setRationale('') }}>取消</button><button className="primaryButton" disabled={!rationale.trim() || requestRevision.isPending} onClick={() => requestRevision.mutate()}>登记并前往处理</button></footer></section></div>}
    {retryChoice && <div className={styles.modal}><section><header><RotateCcw /><div><span>精确重跑</span><h2>重新运行质量审核</h2></div></header><p>{retryChoice.node_key ?? retryChoice.role}</p><small>将复用上次失败时的素材、模型和合同，并再次产生模型调用费用。不会切换模型或修改输入。</small><footer><button className="secondaryButton" onClick={() => setRetryChoice(null)}>取消</button><button className="primaryButton" disabled={retryQC.isPending} onClick={() => retryQC.mutate(retryChoice)}>确认费用并重跑</button></footer></section></div>}
  </>
}
