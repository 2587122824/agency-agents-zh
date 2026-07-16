import { Calculator, CircleAlert, FileSearch, History, ShieldCheck } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { DecisionChangeImpactAnalysis, DecisionImpactSummary } from '../api/types'
import styles from './DecisionChangeAnalysisPanel.module.css'

const targetLabels: Record<string, string> = {
  entity: '实体', entity_version: '实体版本', requirement_candidate: '需求候选',
  requirement_version: '需求版本', creative_brief: '创意方案', shot_plan: '分镜候选',
  plan: '方案', shot: '镜头', snapshot: '快照', dag_node: 'DAG 节点',
  work_item: '工作项', asset: '素材', timeline: '时间线', timeline_item: '剪辑条目',
}

function valueText(value: unknown) {
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2) ?? 'null'
}

function sameValue(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function ProposalInput({ value, onChange }: { value: unknown; onChange: (value: unknown, valid: boolean) => void }) {
  const kind = value === null ? 'json' : typeof value
  const [raw, setRaw] = useState(() => valueText(value))
  const updateJson = (next: string) => {
    setRaw(next)
    try { onChange(JSON.parse(next), true) } catch { onChange(undefined, false) }
  }
  if (kind === 'boolean') return <div className={styles.booleanInput}><button type="button" data-selected={raw === 'true'} onClick={() => { setRaw('true'); onChange(true, true) }}>是</button><button type="button" data-selected={raw === 'false'} onClick={() => { setRaw('false'); onChange(false, true) }}>否</button></div>
  if (kind === 'number') return <input type="number" value={raw} onChange={event => { const next = event.target.value; setRaw(next); onChange(Number(next), next !== '' && Number.isFinite(Number(next))) }} />
  if (kind === 'string') return <input value={raw} onChange={event => { setRaw(event.target.value); onChange(event.target.value, true) }} />
  return <textarea value={raw} onChange={event => updateJson(event.target.value)} spellCheck={false} />
}

function CostValue({ report }: { report: DecisionChangeImpactAnalysis }) {
  if (report.cost_status === 'estimated') return <strong>{report.currency} {report.estimated_cost?.toFixed(6)}</strong>
  if (report.cost_status === 'not_configured') return <strong>价格证据不完整</strong>
  if (report.cost_status === 'mixed_currency') return <strong>存在多个币种</strong>
  return <strong>当前无可计价工作</strong>
}

export function DecisionChangeAnalysisPanel({
  decision,
  analyses,
  pending,
  error,
  onAnalyze,
}: {
  decision: DecisionImpactSummary
  analyses: DecisionChangeImpactAnalysis[]
  pending: boolean
  error: string | null
  onAnalyze: (value: unknown) => void
}) {
  const [proposed, setProposed] = useState<unknown>(decision.current_value)
  const [valid, setValid] = useState(true)
  const [selectedAnalysisId, setSelectedAnalysisId] = useState(analyses[0]?.id ?? '')
  useEffect(() => { if (analyses[0]?.id) setSelectedAnalysisId(analyses[0].id) }, [analyses[0]?.id])
  const latest = analyses.find(item => item.id === selectedAnalysisId) ?? analyses[0]
  const changed = valid && !sameValue(decision.current_value, proposed)
  const groupedTargets = useMemo(() => {
    const groups = new Map<string, DecisionChangeImpactAnalysis['targets']>()
    for (const target of latest?.targets ?? []) groups.set(target.record_type, [...(groups.get(target.record_type) ?? []), target])
    return [...groups.entries()]
  }, [latest])

  return <section className={styles.panel}>
    <header><div><FileSearch size={18} /><span><small>PROSPECTIVE IMPACT</small><h2>决策变更影响分析</h2></span></div><em>{analyses.length} 份报告</em></header>
    <div className={styles.proposal}>
      <div className={styles.valueBlock}><span>当前值</span><code>{valueText(decision.current_value)}</code></div>
      <label><span>提议值</span><ProposalInput value={decision.current_value} onChange={(value, isValid) => { setProposed(value); setValid(isValid) }} />{!valid && <small>JSON 格式无效</small>}</label>
      <button className="primaryButton" type="button" disabled={!changed || pending || decision.status !== 'resolved'} onClick={() => onAnalyze(proposed)}><Calculator size={14} />{pending ? '正在分析…' : '生成分析'}</button>
    </div>
    {error && <div className={styles.error}><CircleAlert size={15} />{error}</div>}
    {!latest && <div className={styles.empty}><History size={20} /><span>尚无该决策的变更分析</span></div>}
    {analyses.length > 1 && <div className={styles.history}><span><History size={14} />报告历史</span>{analyses.map(item => <button type="button" key={item.id} data-selected={item.id === latest?.id} onClick={() => setSelectedAnalysisId(item.id)}><strong>{new Date(item.created_at).toLocaleString()}</strong><small>{valueText(item.proposed_value)}</small><code>{item.analysis_hash.slice(0, 8)}</code></button>)}</div>}
    {latest && <div className={styles.report}>
      <div className={styles.reportHeading}><div><ShieldCheck size={18} /><span><small>{latest.status === 'completed' ? 'ANALYSIS COMPLETE' : 'INSUFFICIENT EVIDENCE'}</small><strong>{new Date(latest.created_at).toLocaleString()}</strong></span></div><code>{latest.analysis_hash.slice(0, 12)}</code></div>
      <div className={styles.reportMetrics}><div><span>候选影响目标</span><strong>{latest.targets.length}</strong></div><div><span>潜在工作单元</span><strong>{latest.estimated_work_count}</strong></div><div><span>活动快照费用</span><CostValue report={latest} /></div></div>
      {groupedTargets.length > 0 && <div className={styles.targetGroups}>{groupedTargets.map(([type, targets]) => <section key={type}><header><span>{targetLabels[type] ?? type}</span><b>{targets.length}</b></header>{targets.map(target => <article key={target.id} data-estimated={target.included_in_estimate}><div><strong>{target.label}</strong><code>{target.record_id}</code></div><span>{target.authority} · {target.record_status}</span>{target.included_in_estimate ? <em>{target.currency && target.estimated_cost !== null ? `${target.currency} ${target.estimated_cost.toFixed(6)}` : '价格未配置'}</em> : <small>仅审核</small>}</article>)}</section>)}</div>}
      <footer>{valueText(latest.current_value)} → {valueText(latest.proposed_value)}</footer>
    </div>}
  </section>
}
