import { useInfiniteQuery } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, ChevronDown, CircleAlert, CircleDollarSign, Clock3, GitBranch, ReceiptText } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { AuditCostEvent } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { eventPresentation } from '../presentation/projectFacts'
import styles from './AuditLedgerPage.module.css'

const costKindLabels: Record<string, string> = {
  estimated: '预计费用',
  charged: '实际扣费',
  adjusted: '费用调整',
  refunded: '退款',
}

const costStatusLabels: Record<string, string> = {
  pending: '待对账',
  confirmed: '已确认',
  disputed: '有争议',
}

const actorTypeLabels: Record<string, string> = {
  user: '用户',
  system: '系统',
  worker: '执行器',
  agent: '智能体',
}

function timestamp(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function amount(event: AuditCostEvent) {
  const exact = event.amount.toFixed(6).replace(/\.?0+$/, '')
  return `${event.currency} ${exact}`
}

export function AuditLedgerPage() {
  const { projectId = '' } = useParams()
  const ledger = useInfiniteQuery({
    queryKey: ['project-audit-ledger', projectId],
    queryFn: ({ pageParam }) => api.projectAuditLedger(projectId, pageParam as number | null),
    initialPageParam: null as number | null,
    getNextPageParam: page => page.has_more_events ? page.next_before_sequence : undefined,
    enabled: Boolean(projectId),
  })
  const firstPage = ledger.data?.pages[0]
  const events = ledger.data?.pages.flatMap(page => page.events) ?? []
  const costs = firstPage?.cost_events ?? []
  const pendingCosts = costs.filter(event => event.status !== 'confirmed').length

  return <>
    <PageHeader eyebrow="AUDIT LEDGER" title={firstPage?.project_title ?? '费用与事件'} description="只读查看每次事件与费用事实；记录不会在此页面被修复、合并或重试。" actions={<Link className="secondaryButton" to={`/projects/${projectId}/control`}><ArrowLeft size={15} />返回项目控制台</Link>} />
    <main className={styles.page}>
      {ledger.isPending && <div className={styles.empty}>正在读取审计账本...</div>}
      {ledger.error && <div className={styles.error}>{ledger.error.message}</div>}
      {firstPage && <>
        <section className={styles.metrics}>
          <article><GitBranch /><span>已加载事件</span><strong>{events.length}</strong><small>{firstPage.has_more_events ? '还有更早记录' : '已到最早记录'}</small></article>
          <article><ReceiptText /><span>费用记录</span><strong>{costs.length}</strong><small>逐笔保留，不覆盖历史</small></article>
          <article data-alert={pendingCosts > 0}><CircleAlert /><span>待处理费用</span><strong>{pendingCosts}</strong><small>{pendingCosts ? '待对账或有争议' : '当前全部已确认'}</small></article>
          <article><CircleDollarSign /><span>记录币种</span><strong>{firstPage.cost_summaries.length}</strong><small>不同币种不自动换算</small></article>
        </section>

        <div className={styles.layout}>
          <section className={styles.events}>
            <header><div><span>PROJECT EVENTS</span><h2>项目事件</h2></div><b>按项目序号倒序</b></header>
            {!events.length && <div className={styles.empty}><Clock3 /><span>尚无项目事件</span></div>}
            {events.map(event => { const presentation = eventPresentation(event.event_type); return <article key={event.event_id}>
              <i><GitBranch size={16} /></i>
              <div className={styles.eventBody}><div className={styles.eventHeading}><span>#{event.sequence}</span><time>{timestamp(event.created_at)}</time></div><h3>{presentation.title}</h3><p>{presentation.description}</p><small>{actorTypeLabels[event.actor_type] ?? event.actor_type} · {event.actor_id}</small>
                <details><summary>查看事件证据</summary><dl><div><dt>事件类型</dt><dd>{event.event_type}</dd></div><div><dt>原始说明</dt><dd>{event.message}</dd></div><div><dt>聚合对象</dt><dd>{event.aggregate_type} · {event.aggregate_id}</dd></div><div><dt>事件 ID</dt><dd>{event.event_id}</dd></div><div><dt>关联 ID</dt><dd>{event.correlation_id}</dd></div><div><dt>原因事件</dt><dd>{event.causation_id ?? '无'}</dd></div><div><dt>生产快照</dt><dd>{event.snapshot_id ?? '无'}</dd></div><div><dt>合同版本</dt><dd>v{event.schema_version}</dd></div></dl><pre>{JSON.stringify(event.data, null, 2)}</pre></details>
              </div>
            </article> })}
            {ledger.hasNextPage && <button className={styles.loadMore} disabled={ledger.isFetchingNextPage} onClick={() => ledger.fetchNextPage()}>{ledger.isFetchingNextPage ? '正在读取...' : '读取更早事件'}<ChevronDown size={15} /></button>}
          </section>

          <aside className={styles.costColumn}>
            <section className={styles.summary}>
              <header><div><span>COST SUMMARY</span><h2>费用汇总</h2></div><b>{firstPage.cost_summaries.length}</b></header>
              {!firstPage.cost_summaries.length && <div className={styles.empty}>尚无费用记录</div>}
              {firstPage.cost_summaries.map(row => <article key={row.currency}><strong>{row.currency}</strong><dl><div><dt>预计</dt><dd>{row.estimated_confirmed.toFixed(6)}</dd></div><div><dt>扣费</dt><dd>{row.charged_confirmed.toFixed(6)}</dd></div><div><dt>调整</dt><dd>{row.adjusted_confirmed.toFixed(6)}</dd></div><div><dt>退款</dt><dd>{row.refunded_confirmed.toFixed(6)}</dd></div></dl>{row.pending_event_count > 0 ? <small>{row.pending_event_count} 条待处理</small> : <em><CheckCircle2 size={12} />均已确认</em>}</article>)}
            </section>

            <section className={styles.costs}>
              <header><div><span>COST EVENTS</span><h2>逐笔费用</h2></div><b>{costs.length}</b></header>
              {!costs.length && <div className={styles.empty}>尚无逐笔费用事实</div>}
              {costs.map(event => <article key={event.id}><div className={styles.costHeading}><span>{costKindLabels[event.kind] ?? event.kind}</span><b data-status={event.status}>{costStatusLabels[event.status] ?? event.status}</b></div><strong>{amount(event)}</strong><p>{event.provider} · {event.provider_operation}</p><time>{timestamp(event.occurred_at)}</time><details><summary>查看费用证据</summary><dl><div><dt>生产快照</dt><dd>{event.snapshot_id}</dd></div><div><dt>制作尝试</dt><dd>{event.work_attempt_id ?? '未绑定'}</dd></div><div><dt>供应商凭据</dt><dd>{event.provider_reference ?? '未提供'}</dd></div><div><dt>费用 ID</dt><dd>{event.id}</dd></div></dl></details></article>)}
            </section>
          </aside>
        </div>
      </>}
    </main>
  </>
}
