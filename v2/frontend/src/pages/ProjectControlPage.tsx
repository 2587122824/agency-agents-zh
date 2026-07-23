import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { AlertTriangle, ArrowRight, BookOpen, CheckCircle2, CircleDollarSign, Clapperboard, Clock3, FileCheck2, GitBranch, MessageSquareText, Network, ReceiptText, RefreshCw, RotateCcw, Route, Workflow, X } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { ProjectControl } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { actorTypeLabel, aggregateTypeLabel, attemptStateLabel, blockerPresentation, eventPresentation, projectStatusLabel, snapshotStatusLabel, stateTriggerLabel, workStatusLabel } from '../presentation/projectFacts'
import styles from './ProjectControlPage.module.css'

function timestamp(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '--'
}

function count(rows: Record<string, number>, key: string) {
  return rows[key] ?? 0
}

const requirementFieldLabels: Record<string, string> = {
  title: '项目名称', core_topic: '核心主题', duration_seconds: '目标时长', aspect_ratio: '画幅', audio_mode: '音频模式',
  creative_direction: '创作方向', content_goal: '内容目标', platform: '发布平台', target_audience: '目标受众',
  visual_style: '视觉风格', tone: '情绪基调', content_structure: '内容结构', call_to_action: '结尾行动', creative_constraints: '创作限制',
}

function requirementValue(key: string, value: unknown) {
  if (key === 'duration_seconds') return `${String(value)} 秒`
  if (key === 'audio_mode') return value === 'off' ? '关闭音频' : value === 'voiceover' ? '使用旁白' : String(value)
  if (Array.isArray(value)) return value.map(String).join('、')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function ProductionBasis({ basis, projectId }: { basis: NonNullable<ProjectControl['production_basis']>; projectId: string }) {
  const [tab, setTab] = useState<'requirement' | 'brief' | 'shots'>('requirement')
  const requirementFields = Object.entries(basis.requirement.fields).filter(([, value]) => value !== null && value !== '')
  return <section className={styles.basis}>
    <header><div><BookOpen /><span><small>本次生产依据</small><h3>已确认创作内容</h3></span></div><p>需求 v{basis.requirement.version_number} · 创作方案 v{basis.plan.version_number} · {basis.plan.shot_count} 个镜头</p><Link className="secondaryButton" to={`/projects/${projectId}/plan`}>查看完整创作方案<ArrowRight size={13} /></Link></header>
    <nav aria-label="生产依据视图">
      <button type="button" data-active={tab === 'requirement'} onClick={() => setTab('requirement')}><MessageSquareText size={14} />创作需求</button>
      <button type="button" data-active={tab === 'brief'} onClick={() => setTab('brief')}><BookOpen size={14} />内容策划</button>
      <button type="button" data-active={tab === 'shots'} onClick={() => setTab('shots')}><Clapperboard size={14} />分镜方案</button>
    </nav>
    {tab === 'requirement' && <div className={styles.requirementBasis}>{requirementFields.map(([key, value]) => <div key={key}><span>{requirementFieldLabels[key] ?? key}</span><strong>{requirementValue(key, value)}</strong></div>)}</div>}
    {tab === 'brief' && <div className={styles.briefBasis}>
      <div className={styles.briefLead}><span>方案名称</span><h4>{basis.creative_brief.title}</h4><p>{basis.creative_brief.content_promise}</p></div>
      <dl><div><dt>观众收获</dt><dd>{basis.creative_brief.audience_takeaway}</dd></div><div><dt>开场设计</dt><dd>{basis.creative_brief.hook.content}</dd></div><div><dt>语气与节奏</dt><dd>{basis.creative_brief.tone} · {basis.creative_brief.pacing}</dd></div></dl>
      <div className={styles.beatBasis}>{basis.creative_brief.narrative_beats.map(beat => <article key={beat.beat_code}><b>{beat.beat_code.replace('BEAT_', '')}</b><div><strong>{beat.purpose}</strong><span>{beat.summary}</span></div><time>{(beat.target_duration_ms / 1000).toFixed(1)}s</time></article>)}</div>
    </div>}
    {tab === 'shots' && <div className={styles.shotBasis}><Clapperboard /><div><span>已确认分镜合同</span><strong>{basis.plan.shot_count} 个镜头 · {basis.plan.contract_schema_version}</strong><small>确认于 {timestamp(basis.plan.confirmed_at)}，生产快照只使用这一版方案。</small></div><Link className="secondaryButton" to={`/projects/${projectId}/plan`}>查看分镜详情</Link></div>}
    <footer><span>以上内容来自当前创作方案的精确版本，不读取后续聊天或未确认候选。</span><Link to={`/projects/${projectId}`}>查看创作记录</Link></footer>
  </section>
}

export function ProjectControlPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [confirmCloseBlocked, setConfirmCloseBlocked] = useState(false)
  const control = useQuery({ queryKey: ['project-control', projectId], queryFn: () => api.projectControl(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['project-control', projectId] })
    await client.invalidateQueries({ queryKey: ['project-controls'] })
  }
  const data = control.data
  const closeBlockedProduction = useMutation({
    mutationFn: () => api.closeBlockedProduction(projectId, data!.active_snapshot!),
    onSuccess: async () => {
      setConfirmCloseBlocked(false)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['project-control', projectId] }),
        client.invalidateQueries({ queryKey: ['project-controls'] }),
        client.invalidateQueries({ queryKey: ['projects'] }),
        client.invalidateQueries({ queryKey: ['production-execution', projectId] }),
        client.invalidateQueries({ queryKey: ['planning-center', projectId] }),
      ])
      navigate(`/projects/${projectId}/plan`)
    },
  })
  const canCloseBlockedProduction = Boolean(
    data && !data.archived_at && data.persisted_status === 'blocked'
    && data.active_snapshot?.status === 'execution_blocked',
  )

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

        {canCloseBlockedProduction && <section className={styles.recovery} data-confirming={confirmCloseBlocked}>
          <RotateCcw />
          <div><span>制作已暂停</span><strong>{confirmCloseBlocked ? '确认结束本次失败制作？' : '修正后返回制作准备'}</strong><p>{confirmCloseBlocked ? '旧任务、失败原因和费用估算会完整保留；尚未开始的步骤将取消。' : '结束当前失败快照后回到制作准备，重新选择当前配置。系统不会自动重跑。'}</p></div>
          {confirmCloseBlocked ? <div className={styles.recoveryButtons}><button className="secondaryButton" disabled={closeBlockedProduction.isPending} onClick={() => setConfirmCloseBlocked(false)}><X size={14} />取消</button><button className="primaryButton" disabled={closeBlockedProduction.isPending} onClick={() => closeBlockedProduction.mutate()}>{closeBlockedProduction.isPending ? '正在结束…' : '确认结束并返回'}</button></div> : <button className="primaryButton" onClick={() => setConfirmCloseBlocked(true)}><RotateCcw size={14} />返回制作准备</button>}
        </section>}
        {closeBlockedProduction.error && <div className={styles.recoveryError}><AlertTriangle size={16} /><div><strong>返回制作准备失败</strong><span>{closeBlockedProduction.error.message}</span></div></div>}

        {data.production_basis && <ProductionBasis basis={data.production_basis} projectId={projectId} />}

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
