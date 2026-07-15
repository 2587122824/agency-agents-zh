import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, CheckCircle2, Clock3, FileCheck2, Image, RefreshCw, ShieldAlert, X } from 'lucide-react'
import { useState } from 'react'

import { api } from '../api/client'
import type { ProductionAsset } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './ReviewPage.module.css'

type ReviewChoice = { asset: ProductionAsset; action: 'approve' | 'reject' }

function mediaUrl(asset: ProductionAsset) {
  return `/api/v1/projects/${asset.project_id}/assets/${asset.id}/content`
}

export function ReviewPage() {
  const client = useQueryClient()
  const [projectId, setProjectId] = useState('')
  const [reviewChoice, setReviewChoice] = useState<ReviewChoice | null>(null)
  const [rationale, setRationale] = useState('')
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects, refetchInterval: 5000 })
  const quality = useQuery({ queryKey: ['quality-review', projectId], queryFn: () => api.qualityReview(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const refresh = () => client.invalidateQueries({ queryKey: ['quality-review', projectId] })
  const verify = useMutation({ mutationFn: (asset: ProductionAsset) => api.verifyAsset(projectId, asset), onSuccess: refresh })
  const runQC = useMutation({ mutationFn: (asset: ProductionAsset) => api.runAssetQC(projectId, asset), onSuccess: refresh })
  const review = useMutation({
    mutationFn: () => api.reviewAsset(projectId, reviewChoice!.asset, reviewChoice!.action, rationale),
    onSuccess: async () => { setReviewChoice(null); setRationale(''); await refresh() },
  })
  const reviewProjects = projects.data?.filter(project => ['producing', 'quality_review', 'blocked'].includes(project.status)) ?? []
  const error = quality.error || verify.error || runQC.error || review.error
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
          <div className={styles.summary}><div><span>NEXT ACTION</span><h2>{quality.data.next_action.label}</h2></div>{Object.entries(quality.data.counts).map(([state, count]) => <div key={state}><strong>{count}</strong><small>{state}</small></div>)}</div>
          {quality.data.output_gaps.map(gap => <article className={styles.gap} key={gap.dag_node_id}><ShieldAlert /><div><strong>{gap.node_key}</strong><span>{gap.code} · {gap.message}</span></div></article>)}
          <div className={styles.assetGrid}>{quality.data.assets.map(asset => <article className={styles.assetCard} key={asset.id} data-state={asset.state}>
            <div className={styles.preview}>{asset.content_hash && asset.asset_type === 'image' ? <img src={mediaUrl(asset)} alt={asset.node_key ?? asset.role} /> : asset.content_hash && asset.asset_type === 'video' ? <video src={mediaUrl(asset)} controls preload="metadata" /> : <Image />}</div>
            <header><div><strong>{asset.node_key ?? asset.role}</strong><span>{asset.asset_type} · {asset.width && asset.height ? `${asset.width}×${asset.height}` : '未探测规格'}</span></div><em>{asset.state}</em></header>
            <div className={styles.meta}><code>{asset.content_hash?.slice(0, 18) ?? '等待文件验证'}</code><span>{asset.byte_size ? `${(asset.byte_size / 1024).toFixed(1)} KB` : asset.storage_backend}</span></div>
            {asset.latest_qc_report && <section className={styles.report}><div><strong>{asset.latest_qc_report.status}</strong><small>{asset.latest_qc_report.ruleset_version}</small></div>{asset.latest_qc_report.findings.map(finding => <p key={finding.id}><AlertTriangle /><span><b>{finding.code}</b>{finding.disposition}</span></p>)}</section>}
            {asset.affected_downstream_node_keys.length > 0 && <p className={styles.impact}>影响下游：{asset.affected_downstream_node_keys.join('、')}</p>}
            <footer>
              {asset.state === 'created' && <button className="primaryButton" disabled={verify.isPending} onClick={() => verify.mutate(asset)}><FileCheck2 size={14} />验证文件</button>}
              {asset.state === 'verified' && <button className="primaryButton" disabled={runQC.isPending} onClick={() => runQC.mutate(asset)}><ShieldAlert size={14} />运行 QC</button>}
              {asset.state === 'review_required' && <><button className="secondaryButton" onClick={() => setReviewChoice({ asset, action: 'reject' })}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => setReviewChoice({ asset, action: 'approve' })}><Check size={14} />批准</button></>}
              {asset.state === 'approved' && <span className={styles.approved}><CheckCircle2 />已批准进入剪辑候选</span>}
            </footer>
          </article>)}</div>
          {!quality.data.assets.length && !quality.data.output_gaps.length && <div className={styles.empty}>当前项目没有已登记素材</div>}
        </>}
        {error && <div className={styles.error}>{error.message}</div>}
      </section>
    </main>
    {reviewChoice && <div className={styles.modal}><section><header>{reviewChoice.action === 'approve' ? <CheckCircle2 /> : <AlertTriangle />}<div><span>HUMAN REVIEW DECISION</span><h2>{reviewChoice.action === 'approve' ? '批准素材' : '拒绝并归档素材'}</h2></div></header><p>{reviewChoice.asset.node_key} · {reviewChoice.asset.latest_qc_report?.id}</p><label>审核依据<textarea value={rationale} onChange={event => setRationale(event.target.value)} placeholder="记录你判断通过或拒绝的具体依据" /></label><small>本操作只记录审核结论，不会重试、重新生成或产生费用。</small><footer><button className="secondaryButton" onClick={() => { setReviewChoice(null); setRationale('') }}>取消</button><button className="primaryButton" disabled={!rationale.trim() || review.isPending} onClick={() => review.mutate()}>{reviewChoice.action === 'approve' ? '确认批准' : '确认拒绝'}</button></footer></section></div>}
  </>
}
