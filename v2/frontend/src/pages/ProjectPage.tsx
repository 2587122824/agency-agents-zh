import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, CircleAlert, LockKeyhole, Plus } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { useWorkspace } from '../store/workspace'
import styles from './ProjectPage.module.css'

export function ProjectPage() {
  const { projectId = '' } = useParams()
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const queryClient = useQueryClient()
  const setCurrentProjectId = useWorkspace(state => state.setCurrentProjectId)
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId), enabled: Boolean(projectId), refetchInterval: 3000 })
  useEffect(() => { setCurrentProjectId(projectId); return () => setCurrentProjectId(null) }, [projectId, setCurrentProjectId])
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['project', projectId] })
  const addDecision = useMutation({ mutationFn: () => api.addDecision(projectId, { key, label, status: 'pending' }), onSuccess: async () => { setKey(''); setLabel(''); await refresh() } })
  const resolve = useMutation({ mutationFn: ({ id, value }: { id: string; value: unknown }) => api.resolveDecision(projectId, id, value), onSuccess: refresh })
  const confirm = useMutation({ mutationFn: () => api.confirmProject(projectId), onSuccess: refresh })

  function submit(event: FormEvent) { event.preventDefault(); addDecision.mutate() }
  if (project.isPending) return <div className={styles.loading}>正在读取项目合同…</div>
  if (project.error || !project.data) return <div className={styles.loading}>项目读取失败：{project.error?.message}</div>
  const data = project.data
  const pending = data.decisions.filter(item => item.status === 'pending')

  return <>
    <PageHeader eyebrow="PROJECT CONTRACT" title={data.title} description="这里显示数据库中的真实项目合同和用户决策账本。" actions={<Link className="secondaryButton" to="/"><ArrowLeft size={14} />项目列表</Link>} />
    <div className={styles.layout}>
      <section className={styles.main}>
        <div className={styles.contract}>
          <div className={styles.sectionHeading}><div><span>只读合同</span><h2>明确需求</h2></div><em>{data.status}</em></div>
          <dl><div><dt>核心主题</dt><dd>{data.core_topic}</dd></div><div><dt>时长</dt><dd>{data.duration_seconds} 秒</dd></div><div><dt>画幅</dt><dd>{data.aspect_ratio}</dd></div><div><dt>音频</dt><dd>{data.audio_mode === 'off' ? '关闭，不创建 TTS' : '旁白'}</dd></div></dl>
        </div>
        <div className={styles.ledger}>
          <div className={styles.sectionHeading}><div><span>DECISION LEDGER</span><h2>决策账本</h2></div><b>{pending.length} 项待确认</b></div>
          {data.decisions.length ? <div className={styles.decisions}>{data.decisions.map(decision => <article key={decision.id}><span className={decision.status === 'resolved' ? styles.resolved : styles.pending}>{decision.status === 'resolved' ? <Check size={13} /> : <CircleAlert size={13} />}</span><div><strong>{decision.label}</strong><small>{decision.key} · 来源：{decision.source}</small>{decision.status === 'resolved' && <p>{JSON.stringify(decision.value)}</p>}</div>{decision.status === 'pending' && <button className="secondaryButton" onClick={() => { const value = window.prompt(`请输入“${decision.label}”的明确值`); if (value !== null && value !== '') resolve.mutate({ id: decision.id, value }) }}>确认</button>}</article>)}</div> : <div className={styles.empty}>还没有待确认决策。需要选择时，先登记决策再确认项目。</div>}
          {data.status === 'draft' && <form className={styles.decisionForm} onSubmit={submit}><input required pattern="[a-z][a-z0-9_]{1,119}" value={key} onChange={event => setKey(event.target.value)} placeholder="decision_key" /><input required value={label} onChange={event => setLabel(event.target.value)} placeholder="决策名称" /><button className="secondaryButton"><Plus size={14} />登记待确认项</button></form>}
        </div>
      </section>
      <aside className={styles.aside}>
        <div className={styles.gate}><LockKeyhole size={20} /><h3>确认门禁</h3><p>只有全部决策明确解决后，项目才能进入生产合同验证。</p><ul><li>待确认决策 <b>{pending.length}</b></li><li>已登记工作项 <b>{data.work_items.length}</b></li><li>自动重试 <b>0</b></li><li>路由替换 <b>0</b></li></ul>{confirm.error && <div className={styles.error}>{confirm.error.message}</div>}<button className="primaryButton" disabled={data.status !== 'draft' || pending.length > 0 || confirm.isPending} onClick={() => confirm.mutate()}>{data.status === 'draft' ? '确认项目合同' : '合同已离开草稿状态'}<Check size={14} /></button></div>
        <div className={styles.next}><span>下一步</span><h3>合同验证</h3><p>确认后前往生产队列，由独立 Worker 执行结构验证。</p><Link to="/production">打开生产队列</Link></div>
      </aside>
    </div>
  </>
}
