import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, CheckCircle2, CircleDollarSign, Clock3, FileCheck2, GitBranch, Network, ReceiptText, RefreshCw, Route, Workflow } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { actorTypeLabel, aggregateTypeLabel, attemptStateLabel, blockerPresentation, eventPresentation, projectStatusLabel, snapshotStatusLabel, stateTriggerLabel, workStatusLabel } from '../presentation/projectFacts'
import styles from './ProjectControlPage.module.css'

function timestamp(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--'
}

function count(rows: Record<string, number>, key: string) {
  return rows[key] ?? 0
}

export function ProjectControlPage() {
  const { projectId = '' } = useParams()
  const client = useQueryClient()
  const control = useQuery({ queryKey: ['project-control', projectId], queryFn: () => api.projectControl(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['project-control', projectId] })
    await client.invalidateQueries({ queryKey: ['project-controls'] })
  }
  const data = control.data

  return <>
    <PageHeader eyebrow="项目控制" title={data?.title ?? '项目控制台'} description={data?.core_topic ?? '读取项目权威状态与执行证据。'} actions={<><button className="secondaryButton" onClick={refresh}><RefreshCw size={14} />刷新</button>{data && <Link className="secondaryButton" to={`/projects/${projectId}/audit`}><ReceiptText size={14} />费用与事件</Link>}{data && <Link className="secondaryButton" to={`/projects/${projectId}/decision-impact`}><Network size={14} />决策影响</Link>}{data && !data.archived_at && <Link className="primaryButton" to={data.next_action.path}>{data.next_action.label}<ArrowRight size={14} /></Link>}</>} />
    <main className={styles.page}>
      {control.isPending && <div className={styles.empty}>正在读取项目状态...</div>}
      {control.error && <div className={styles.error}>{control.error.message}</div>}
      {data && <>
        <section className={styles.stage} data-stage={data.evaluated_stage}>
          <div><span>当前阶段</span><h2>{data.stage_label}</h2><small>项目状态：{projectStatusLabel(data.persisted_status)}</small></div>
          <dl>
            <div><dt>方案</dt><dd>{data.active_plan_version ? `v${data.active_plan_version}` : '--'}</dd></div>
            <div><dt>快照</dt><dd>{data.active_snapshot_number ? `#${data.active_snapshot_number}` : '--'}</dd></div>
            <div><dt>制作方案状态</dt><dd>{snapshotStatusLabel(data.active_snapshot_status)}</dd></div>
            <div><dt>最近事件</dt><dd>{timestamp(data.latest_event_at)}</dd></div>
            <div><dt>状态版本</dt><dd>v{data.state_row_version}</dd></div>
            <div><dt>状态来源</dt><dd>{stateTriggerLabel(data.state_trigger)}</dd></div>
            <div><dt>操作来源</dt><dd>{actorTypeLabel(data.state_actor_type)}</dd></div>
            <div><dt>状态时间</dt><dd>{timestamp(data.state_changed_at)}</dd></div>
          </dl>
          <aside><strong>{data.archived_at ? '项目已归档' : data.next_action.label}</strong><small>{data.archived_at ? `归档于 ${timestamp(data.archived_at)}，恢复后可继续操作` : `${data.next_action.confirmation_level === 'high' ? '需要重点确认' : data.next_action.confirmation_level === 'normal' ? '需要确认' : '无需确认'} · ${data.next_action.incurs_production_cost ? '会产生制作费用' : '不会产生制作费用'}`}</small>{data.state_reason_code && <div className={styles.stateBlock}><b>{blockerPresentation(data.state_reason_code).title}</b><span>{projectStatusLabel(data.blocked_from_state)} → {projectStatusLabel(data.persisted_status)}</span><small>责任对象：{aggregateTypeLabel(data.blocked_responsible_aggregate_type)}</small></div>}<details className={styles.stageTechnical}><summary>技术详情</summary><code>{data.persisted_status} · {data.active_snapshot_status ?? 'NO_SNAPSHOT'} · {data.next_action.code}</code><p>{data.state_trigger} · {data.state_actor_type}:{data.state_changed_by}</p>{data.archived_at && <p>{data.archived_at} · {data.archived_by}</p>}{data.state_reason_code && <code>{data.state_reason_code} · {data.blocked_from_state ?? 'NO_PREVIOUS_STATE'} → {data.persisted_status} · {data.blocked_responsible_aggregate_type}:{data.blocked_responsible_aggregate_id}</code>}</details></aside>
        </section>

        <section className={styles.metrics}>
          <article><Workflow /><span>工作项</span><strong>{Object.values(data.work_counts).reduce((sum, value) => sum + value, 0)}</strong><small>{count(data.work_counts, 'completed')} 完成 · {count(data.work_counts, 'in_progress')} 执行 · {count(data.work_counts, 'queued')} 排队</small></article>
          <article><FileCheck2 /><span>素材</span><strong>{Object.values(data.asset_counts).reduce((sum, value) => sum + value, 0)}</strong><small>{count(data.asset_counts, 'approved') + count(data.asset_counts, 'used')} 可用 · {count(data.asset_counts, 'review_required')} 待审</small></article>
          <article data-alert={data.blocker_count > 0}><AlertTriangle /><span>阻断</span><strong>{data.blocker_count}</strong><small>{data.blocker_count ? '需要查看确定性证据' : '当前没有阻断记录'}</small></article>
          <article><CircleDollarSign /><span>币种账本</span><strong>{data.costs.length}</strong><small>{data.costs.reduce((sum, row) => sum + row.pending_event_count, 0)} 条待对账</small></article>
        </section>

        <div className={styles.columns}>
          <section className={styles.blockers}>
            <header><div><span>问题摘要</span><h3>当前阻断</h3></div><b>{data.blockers.length}</b></header>
            {!data.blockers.length && <div className={styles.clear}><CheckCircle2 /><span>没有持久化阻断证据</span></div>}
            {data.blockers.map((item, index) => { const presentation = blockerPresentation(item.code); return <article key={`${item.source_type}-${item.source_id}-${item.code}-${index}`}>
              <AlertTriangle /><div><strong>{presentation.title}</strong><p>{presentation.description}</p>{item.affected_node_keys.length > 0 && <em>影响：{item.affected_node_keys.join('、')}</em>}<details><summary>技术详情</summary><code>{item.code} · {item.source_type} · {item.source_id}</code><p>{item.message}</p><pre>{JSON.stringify(item.evidence, null, 2)}</pre></details></div>
            </article> })}
          </section>

          <section className={styles.costs}>
            <header><div><span>费用账本</span><h3>费用事实</h3></div><b>{data.costs.length}</b></header>
            {!data.costs.length && <div className={styles.clear}><CircleDollarSign /><span>尚无成本事件</span></div>}
            {data.costs.map(row => <article key={row.currency}><strong>{row.currency}</strong><dl><div><dt>已确认预计</dt><dd>{row.estimated_confirmed.toFixed(6)}</dd></div><div><dt>实际扣费</dt><dd>{row.charged_confirmed.toFixed(6)}</dd></div><div><dt>调整</dt><dd>{row.adjusted_confirmed.toFixed(6)}</dd></div><div><dt>退款</dt><dd>{row.refunded_confirmed.toFixed(6)}</dd></div></dl>{row.pending_event_count > 0 && <small>{row.pending_event_count} 条待对账</small>}</article>)}
          </section>
        </div>

        <section className={styles.routes}>
          <header><div><span>执行记录</span><h3>实际执行路由</h3></div><b>{data.routes.length}</b></header>
          {!data.routes.length ? <div className={styles.clear}><Route /><span>尚无工作尝试</span></div> : <div className={styles.tableScroll}><table><thead><tr><th>制作步骤</th><th>生成服务</th><th>执行次数</th><th>步骤状态</th><th>执行状态</th><th>详情</th></tr></thead><tbody>{data.routes.map((row, index) => <tr key={row.attempt_id}><td><strong>步骤 {index + 1}</strong></td><td>{row.provider}</td><td>第 {row.attempt_number} 次</td><td>{workStatusLabel(row.work_item_status)}</td><td><em data-state={row.attempt_state}>{attemptStateLabel(row.attempt_state)}</em>{row.error_code && <small>{blockerPresentation(row.error_code).title}</small>}</td><td><details><summary>技术详情</summary><code>{row.node_key ?? 'NO_NODE'} · {row.work_item_id} · {row.attempt_id}</code><p>{row.adapter_kind ?? 'NO_ADAPTER'} · {row.provider_workflow_id ?? 'NO_WORKFLOW'} · {row.provider_task_id ?? 'NO_PROVIDER_TASK'}</p><code>{row.work_item_status} · {row.attempt_state} · {row.error_code ?? 'NO_ERROR_CODE'} · {row.request_fingerprint}</code></details></td></tr>)}</tbody></table></div>}
        </section>

        <section className={styles.events}>
          <header><div><span>项目记录</span><h3>最近事件</h3></div><b>{data.recent_events.length}</b></header>
          {!data.recent_events.length && <div className={styles.clear}><Clock3 /><span>尚无项目事件</span></div>}
          {data.recent_events.map(event => { const presentation = eventPresentation(event.event_type); return <article key={event.sequence}><i><GitBranch /></i><div><strong>{presentation.title}</strong><p>{presentation.description}</p><small>#{event.sequence} · {timestamp(event.created_at)}</small></div><details><summary>技术详情</summary><code>{event.event_type}</code><p>{event.message}</p><pre>{JSON.stringify(event.data, null, 2)}</pre></details></article> })}
        </section>
      </>}
    </main>
  </>
}
