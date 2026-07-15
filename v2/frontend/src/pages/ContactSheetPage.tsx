import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, AudioLines, Boxes, FileQuestion, Fingerprint, Image, Link2, RefreshCw, Route, ShieldCheck, UserRound, Video } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { ContactSheetEntry, ContactSheetEntityReference, ProductionAsset } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './ContactSheetPage.module.css'

function assetUrl(asset: ProductionAsset) {
  return `/api/v1/projects/${asset.project_id}/assets/${asset.id}/content`
}

function sourceUrl(projectId: string, reference: ContactSheetEntityReference) {
  return `/api/v1/projects/${projectId}/attachments/${reference.source_attachment_id}/content`
}

function AssetPreview({ asset }: { asset: ProductionAsset }) {
  const [failed, setFailed] = useState(false)
  if (!asset.content_hash) return <div className={styles.previewEmpty}><FileQuestion /><strong>文件尚未验证</strong><span>当前只保留登记信息。</span></div>
  if (failed) return <div className={styles.previewFailed}><AlertTriangle /><strong>素材无法预览</strong><span>保留文件与哈希证据，不自动转码、修复或替换。</span></div>
  if (asset.asset_type === 'image') return <img src={assetUrl(asset)} alt={asset.node_key ?? asset.role} onError={() => setFailed(true)} />
  if (asset.asset_type === 'video') return <video src={assetUrl(asset)} controls preload="metadata" onError={() => setFailed(true)} />
  if (asset.asset_type === 'audio') return <div className={styles.audioPreview}><AudioLines /><audio src={assetUrl(asset)} controls preload="metadata" onError={() => setFailed(true)} /></div>
  return <div className={styles.previewEmpty}><FileQuestion /><strong>{asset.asset_type}</strong><span>此类型没有内嵌预览。</span></div>
}

function ReferencePreview({ projectId, reference }: { projectId: string; reference: ContactSheetEntityReference }) {
  const [failed, setFailed] = useState(false)
  if (!reference.source_attachment_id || !reference.source_mime_type) return <div className={styles.referencePlaceholder}><UserRound /></div>
  if (failed) return <div className={styles.referencePlaceholder} title="来源附件无法预览"><AlertTriangle /></div>
  const url = sourceUrl(projectId, reference)
  if (reference.source_mime_type.startsWith('image/')) return <img src={url} alt={reference.source_filename ?? reference.entity_name} onError={() => setFailed(true)} />
  if (reference.source_mime_type.startsWith('video/')) return <video src={url} preload="metadata" onError={() => setFailed(true)} />
  return <div className={styles.referencePlaceholder}><AudioLines /></div>
}

function labelForType(assetType: string) {
  if (assetType === 'video') return <Video />
  if (assetType === 'audio') return <AudioLines />
  return <Image />
}

function ContactCard({ projectId, entry }: { projectId: string; entry: ContactSheetEntry }) {
  const asset = entry.asset
  return <article className={styles.card} data-state={asset.state}>
    <div className={styles.visual}>
      <span className={styles.number}>{String(entry.number).padStart(2, '0')}</span>
      <AssetPreview asset={asset} />
    </div>
    <header className={styles.cardHeader}>
      <i>{labelForType(asset.asset_type)}</i>
      <div><span>{entry.node_kind ?? 'UNBOUND ASSET'}</span><h2>{entry.node_key ?? asset.role}</h2><code>{asset.id}</code></div>
      <em>{asset.state}</em>
    </header>
    <dl className={styles.fileFacts}>
      <div><dt>类型</dt><dd>{asset.asset_type} · {asset.role}</dd></div>
      <div><dt>规格</dt><dd>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : '未检测'}{asset.duration_ms ? ` · ${(asset.duration_ms / 1000).toFixed(2)}s` : ''}</dd></div>
      <div><dt>文件</dt><dd>{asset.byte_size ? `${(asset.byte_size / 1024).toFixed(1)} KB` : asset.storage_backend}</dd></div>
      <div><dt>哈希</dt><dd><code>{asset.content_hash?.slice(0, 18) ?? '未验证'}</code></dd></div>
    </dl>

    <section className={styles.evidence}>
      <h3><ShieldCheck />QC 与审核</h3>
      {asset.latest_qc_report ? <><p><strong>{asset.latest_qc_report.status}</strong><span>{asset.latest_qc_report.ruleset_version}</span></p>{asset.latest_qc_report.findings.map(finding => <p key={finding.id}><b>{finding.code}</b><span>{finding.disposition}</span></p>)}</> : <p><span>没有 QC 报告</span></p>}
      {asset.review_decisions.map(decision => <p key={decision.id}><b>{decision.decision}</b><span>{decision.actor_id} · {decision.rationale}</span></p>)}
    </section>

    <section className={styles.evidence}>
      <h3><Boxes />分镜合同</h3>
      {entry.shot ? <><p><strong>{entry.shot.shot_code} · {entry.shot.shot_type}</strong><span>{(entry.shot.duration_ms / 1000).toFixed(2)}s</span></p><p><b>人物可见性</b><span>{entry.shot.face_visibility}</span></p><p><b>文字策略</b><span>{entry.shot.text_policy}</span></p><p><b>动态要求</b><span>{entry.shot.motion_requirement}</span></p><p className={styles.longFact}><b>构图</b><span>{entry.shot.composition}</span></p><p className={styles.longFact}><b>动作</b><span>{entry.shot.action}</span></p></> : <p><span>素材未绑定分镜。</span></p>}
    </section>

    <section className={styles.evidence}>
      <h3><Route />实际执行路由</h3>
      {entry.route ? <><p><strong>{entry.route.provider}</strong><span>{entry.route.adapter_kind ?? 'adapter 未声明'}</span></p><p><b>工作流</b><span>{entry.route.provider_workflow_id ?? '未声明'}</span></p><p><b>尝试</b><span>#{entry.route.attempt_number} · {entry.route.attempt_state}</span></p><p><b>供应商任务</b><span>{entry.route.provider_task_id ?? '未创建'}</span></p><p className={styles.longFact}><b>请求指纹</b><code>{entry.route.request_fingerprint}</code></p></> : <p><span>没有可追溯的 WorkAttempt；系统不补写路由。</span></p>}
    </section>

    <section className={styles.evidence}>
      <h3><Link2 />声明依赖</h3>
      {entry.dependencies.length ? entry.dependencies.map(dependency => <div className={styles.dependency} key={dependency.edge_id}><p><strong>{dependency.parent_node_key}</strong><span>{dependency.dependency_type} · {dependency.input_slot ?? '未命名槽位'}</span></p>{dependency.registered_assets.length ? dependency.registered_assets.map(input => <p key={input.id}><code>{input.id}</code><span>{input.asset_type} · {input.state}</span></p>) : <p><span>父节点没有登记输出。</span></p>}</div>) : <p><span>此节点没有声明上游依赖。</span></p>}
    </section>

    <section className={styles.references}>
      <h3><Fingerprint />实体版本与来源</h3>
      {entry.entity_references.length ? entry.entity_references.map(reference => <div key={`${reference.role}-${reference.entity_version_id}`}><figure><ReferencePreview projectId={projectId} reference={reference} /></figure><p><strong>{reference.entity_name}</strong><span>{reference.role} · v{reference.version_number}</span><code>{reference.entity_version_id}</code><small>{reference.source_filename ?? '没有来源附件'}</small></p></div>) : <p>分镜没有声明人物、场景或服装实体版本。</p>}
    </section>
  </article>
}

export function ContactSheetPage() {
  const { projectId = '' } = useParams()
  const client = useQueryClient()
  const sheet = useQuery({ queryKey: ['contact-sheet', projectId], queryFn: () => api.contactSheet(projectId), enabled: Boolean(projectId), refetchInterval: 10000 })
  const data = sheet.data

  return <>
    <PageHeader eyebrow="MATERIAL CONTACT SHEET" title="素材联络表" description="并排核对当前活动快照的素材、分镜、路由、依赖、实体来源与 QC 证据。" actions={<><Link className="secondaryButton" to="/review"><ArrowLeft size={14} />返回审核</Link><button className="secondaryButton" onClick={() => client.invalidateQueries({ queryKey: ['contact-sheet', projectId] })}><RefreshCw size={14} />刷新</button></>} />
    <main className={styles.page}>
      {sheet.isPending && <div className={styles.empty}><RefreshCw /><strong>正在读取联络表</strong></div>}
      {sheet.error && <div className={styles.error}><AlertTriangle /><strong>联络表读取失败</strong><span>{sheet.error.message}</span></div>}
      {data && <>
        <section className={styles.overview}>
          <div><span>PROJECT</span><h2>{data.project_title}</h2><small>{data.project_status}</small></div>
          <div><span>ACTIVE SNAPSHOT</span><strong>{data.snapshot ? `#${data.snapshot.snapshot_number}` : '--'}</strong><small>{data.snapshot?.status ?? '未激活'}</small></div>
          <div><span>REGISTERED ASSETS</span><strong>{data.entries.length}</strong><small>{Object.entries(data.counts).map(([state, count]) => `${state} ${count}`).join(' · ') || '没有登记素材'}</small></div>
          <div><span>OUTPUT GAPS</span><strong>{data.output_gaps.length}</strong><small>只展示确定性缺口</small></div>
        </section>
        <aside className={styles.boundary}><ShieldCheck /><div><strong>只读证据视图</strong><span>{data.boundary}</span></div></aside>
        {data.output_gaps.length > 0 && <section className={styles.gaps}>{data.output_gaps.map(gap => <article key={gap.dag_node_id}><AlertTriangle /><div><strong>{gap.node_key}</strong><span>{gap.code} · {gap.message}</span></div></article>)}</section>}
        {!data.snapshot && <div className={styles.empty}><Boxes /><strong>没有活动生产快照</strong><span>联络表不会从历史记录中挑选替代快照。</span></div>}
        {data.snapshot && !data.entries.length && <div className={styles.empty}><FileQuestion /><strong>当前快照尚无登记素材</strong><span>上方仍会列出确定性的输出缺口。</span></div>}
        {data.entries.length > 0 && <section className={styles.grid}>{data.entries.map(entry => <ContactCard key={entry.asset.id} projectId={data.project_id} entry={entry} />)}</section>}
      </>}
    </main>
  </>
}
