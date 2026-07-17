import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, CircleDot, GitBranch, Network, RefreshCw, ShieldAlert } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { DecisionImpactNode } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { DecisionChangeAnalysisPanel } from '../components/DecisionChangeAnalysisPanel'
import styles from './DecisionImpactPage.module.css'

const typeLabels: Record<string, string> = {
  entity: '实体', entity_version: '实体版本',
  manifest: '输入清单', agent_run: '智能体运行', requirement_candidate: '需求候选',
  requirement_version: '需求版本', creative_brief: '创意方案', shot_plan: '分镜候选',
  plan: '方案版本', shot: '分镜', snapshot: '生产快照', dag_node: 'DAG 节点',
  work_item: '工作项', asset: '素材', timeline: '时间线', timeline_item: '剪辑条目',
}

const typeOrder = Object.keys(typeLabels)

function shortId(value: string) {
  return value.length > 22 ? `${value.slice(0, 12)}...${value.slice(-6)}` : value
}

function NodeRow({ node, relations }: { node: DecisionImpactNode; relations: string[] }) {
  return <article className={styles.nodeRow} data-authority={node.authority}>
    <i><CircleDot size={14} /></i>
    <div><strong>{node.label}</strong><code>{shortId(node.record_id)}</code></div>
    <span>{node.status}</span>
    <small>{relations.join(' / ') || 'recorded'}</small>
  </article>
}

export function DecisionImpactPage() {
  const { projectId = '' } = useParams()
  const client = useQueryClient()
  const graph = useQuery({ queryKey: ['decision-impact', projectId], queryFn: () => api.decisionImpactGraph(projectId), enabled: Boolean(projectId), refetchInterval: 10000 })
  const changeImpacts = useQuery({ queryKey: ['decision-change-impacts', projectId], queryFn: () => api.decisionChangeImpacts(projectId), enabled: Boolean(projectId), refetchInterval: 10000 })
  const [selectedId, setSelectedId] = useState('')
  useEffect(() => {
    if (graph.data?.decisions[0] && !graph.data.decisions.some(item => item.decision_id === selectedId)) {
      setSelectedId(graph.data.decisions[0].decision_id)
    }
  }, [graph.data, selectedId])
  const selected = graph.data?.decisions.find(item => item.decision_id === selectedId) ?? graph.data?.decisions[0]
  const visibleIds = useMemo(() => new Set(selected ? [`decision:${selected.decision_id}`, ...selected.downstream_node_ids] : []), [selected])
  const visibleNodes = useMemo(() => (graph.data?.nodes ?? []).filter(node => visibleIds.has(node.node_id)), [graph.data, visibleIds])
  const inbound = useMemo(() => {
    const result = new Map<string, string[]>()
    for (const edge of graph.data?.edges ?? []) {
      if (!visibleIds.has(edge.source_node_id) || !visibleIds.has(edge.target_node_id)) continue
      result.set(edge.target_node_id, [...(result.get(edge.target_node_id) ?? []), edge.relation])
    }
    return result
  }, [graph.data, visibleIds])
  const analyzeChange = useMutation({ mutationFn: (value: unknown) => api.analyzeDecisionChange(projectId, selected!.decision_id, value), onSuccess: () => client.invalidateQueries({ queryKey: ['decision-change-impacts', projectId] }) })
  const selectedAnalyses = (changeImpacts.data?.analyses ?? []).filter(item => item.decision_id === selected?.decision_id)

  return <>
    <PageHeader eyebrow="DECISION LINEAGE" title={graph.data?.project_title ?? '决策影响'} description="已观测决策传播证据" actions={<><Link className="secondaryButton" to={`/projects/${projectId}/control`}><ArrowLeft size={14} />返回控制台</Link><button className="secondaryButton" onClick={() => client.invalidateQueries({ queryKey: ['decision-impact', projectId] })}><RefreshCw size={14} />刷新</button></>} />
    <main className={styles.page}>
      {graph.isPending && <div className={styles.state}>正在读取决策传播证据...</div>}
      {graph.error && <div className={styles.error}>{graph.error.message}</div>}
      {graph.data && !graph.data.decisions.length && <div className={styles.empty}><Network /><strong>当前项目没有决策记录</strong></div>}
      {graph.data && graph.data.decisions.length > 0 && <>
        <section className={styles.metrics}>
          <div><span>决策</span><strong>{graph.data.decisions.length}</strong></div>
          <div><span>已观测</span><strong>{graph.data.decisions.filter(item => item.observation_status === 'observed').length}</strong></div>
          <div><span>证据节点</span><strong>{graph.data.nodes.length}</strong></div>
          <div><span>精确关系</span><strong>{graph.data.edges.length}</strong></div>
        </section>
        <div className={styles.layout}>
          <aside className={styles.decisions}>
            <header><span>DECISIONS</span><h2>决策账本</h2></header>
            {graph.data.decisions.map(item => <button key={item.decision_id} data-selected={item.decision_id === selected?.decision_id} onClick={() => setSelectedId(item.decision_id)}>
              <i>{item.observation_status === 'observed' ? <CheckCircle2 /> : <ShieldAlert />}</i>
              <span><strong>{item.label}</strong><code>{item.key}</code></span>
              <b>{item.downstream_node_ids.length}</b>
            </button>)}
          </aside>
          <section className={styles.lineage}>
            {selected && <header className={styles.selection}>
              <div><span>{selected.observation_status === 'observed' ? 'OBSERVED' : 'NOT OBSERVED'}</span><h2>{selected.label}</h2><code>{selected.key}</code></div>
              <dl><div><dt>输入清单</dt><dd>{selected.direct_manifest_ids.length}</dd></div><div><dt>下游证据</dt><dd>{selected.downstream_node_ids.length}</dd></div><div><dt>活动记录</dt><dd>{selected.active_downstream_count}</dd></div></dl>
            </header>}
            {selected?.observation_status === 'not_observed' && <div className={styles.notObserved}><ShieldAlert /><div><strong>没有清单冻结过该决策</strong><p>当前证据不足以声明下游影响。</p></div></div>}
            {selected?.observation_status === 'observed' && <div className={styles.groups}>
              {typeOrder.map(type => {
                const rows = visibleNodes.filter(node => node.record_type === type)
                if (!rows.length) return null
                return <section key={type}><header><GitBranch /><span>{typeLabels[type]}</span><b>{rows.length}</b></header>{rows.map(node => <NodeRow key={node.node_id} node={node} relations={inbound.get(node.node_id) ?? []} />)}</section>
              })}
            </div>}
          </section>
        </div>
        {selected && <DecisionChangeAnalysisPanel key={selected.decision_id} decision={selected} analyses={selectedAnalyses} pending={analyzeChange.isPending} error={analyzeChange.error?.message ?? changeImpacts.error?.message ?? null} onAnalyze={value => analyzeChange.mutate(value)} />}
        <footer className={styles.boundary}>{graph.data.boundary}</footer>
      </>}
    </main>
  </>
}
