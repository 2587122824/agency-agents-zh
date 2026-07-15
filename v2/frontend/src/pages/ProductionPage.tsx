import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Clock3, GitBranch, Layers3, RefreshCw, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

import { api } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import styles from './StagePage.module.css'

const terminal = new Set(['completed', 'blocked'])

export function ProductionPage() {
  const client = useQueryClient()
  const [projectId, setProjectId] = useState('')
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects, refetchInterval: 5000 })
  const execution = useQuery({
    queryKey: ['production-execution', projectId],
    queryFn: () => api.productionExecution(projectId),
    enabled: Boolean(projectId),
    refetchInterval: query => query.state.data?.work_items.some(item => !terminal.has(item.status)) ? 2000 : false,
  })
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['projects'] })
    if (projectId) client.invalidateQueries({ queryKey: ['production-execution', projectId] })
  }
  const productionProjects = projects.data?.filter(project => ['production_ready', 'producing', 'quality_review', 'blocked'].includes(project.status)) ?? []
  return <>
    <PageHeader eyebrow="PRODUCTION" title="生产执行" description="快照激活、生产提交与 Worker 执行保持为三道独立边界。" actions={<button className="secondaryButton" onClick={refresh}><RefreshCw size={14} />刷新</button>} />
    <div className={styles.content}>
      <div className={styles.stageBar}>
        <div className={styles.active}><span>1</span><b>快照锁定</b><small>合同与费用不可变</small></div>
        <GitBranch />
        <div className={styles.active}><span>2</span><b>显式激活</b><small>尚未创建任务</small></div>
        <GitBranch />
        <div className={styles.active}><span>3</span><b>提交生产</b><small>精确编译 WorkItem</small></div>
        <GitBranch />
        <div><span>4</span><b>质量审核</b><small>等待真实素材能力接入</small></div>
      </div>

      <div className={styles.executionLayout}>
        <section className={styles.list}>
          <header><div><span>ACTIVE SNAPSHOTS</span><h2>生产项目</h2></div><b>{productionProjects.length}</b></header>
          {productionProjects.map(project => <button className={styles.projectRow} data-selected={projectId === project.id} key={project.id} onClick={() => setProjectId(project.id)}>
            <span className={styles.state}>{project.status === 'blocked' ? <AlertTriangle /> : project.status === 'quality_review' ? <CheckCircle2 /> : <Clock3 />}</span>
            <div><strong>{project.title}</strong><small>{project.id}</small></div><em>{project.status}</em>
          </button>)}
          {!productionProjects.length && <div className={styles.empty}>没有已激活或已提交的生产快照</div>}
        </section>

        <section className={styles.executionPanel}>
          {!projectId && <div className={styles.executionEmpty}><Layers3 size={24} /><strong>选择一个生产项目</strong><span>这里会显示快照、DAG 节点、执行尝试和阻断原因。</span></div>}
          {projectId && execution.isPending && <div className={styles.executionEmpty}>正在读取执行状态…</div>}
          {execution.error && <div className={styles.executionEmpty}><AlertTriangle size={22} /><strong>读取失败</strong><span>{execution.error.message}</span></div>}
          {execution.data && <>
            <header className={styles.executionHeader}><div><span>SNAPSHOT_{execution.data.snapshot?.snapshot_number}</span><h2>{execution.data.project_status}</h2></div><div><strong>{execution.data.work_items.filter(item => item.status === 'completed').length}/{execution.data.work_items.length}</strong><small>完成节点</small></div></header>
            {execution.data.blockers.map(blocker => <div className={styles.blocker} key={blocker.work_item_id}><AlertTriangle size={16} /><div><strong>{blocker.node_key}</strong><span>{blocker.error}</span></div></div>)}
            <div className={styles.workList}>{execution.data.work_items.map(item => {
              const attempt = item.attempts.at(-1)
              return <article key={item.id} data-status={item.status}>
                <span className={styles.nodeState}>{item.status === 'completed' ? <CheckCircle2 /> : item.status === 'blocked' ? <AlertTriangle /> : <Clock3 />}</span>
                <div className={styles.nodeMain}><strong>{item.node_key}</strong><span>{item.kind}</span><code>{item.request_fingerprint.slice(0, 16)}</code></div>
                <div className={styles.attempt}><small>ATTEMPT {attempt?.attempt_number ?? 0}</small><strong>{attempt?.state ?? item.status}</strong><span>{attempt?.provider ?? '未创建'}</span></div>
                <em>{item.status}</em>
              </article>
            })}</div>
            {!execution.data.work_items.length && <div className={styles.executionEmpty}><ShieldCheck size={22} /><strong>快照已激活，尚未提交</strong><span>返回方案页进行独立的高风险生产提交确认。</span></div>}
          </>}
        </section>
      </div>

      <aside className={styles.notice}><strong>当前执行边界</strong><p>只有显式配置为 mock 的供应商节点和本地时间线合同节点可以执行。未接入的真实适配器会明确阻断，不发送网络请求、不替换路由、不重试。</p></aside>
    </div>
  </>
}
