import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, CheckCircle2, Clock3, Play, RefreshCw } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import styles from './StagePage.module.css'

export function ProductionPage() {
  const queryClient = useQueryClient()
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects, refetchInterval: 3000 })
  const queue = useMutation({ mutationFn: api.queueValidation, onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects'] }) })
  return <><PageHeader eyebrow="PRODUCTION" title="生产队列" description="这里只提交明确工作项；执行由独立 Worker 负责。" actions={<button className="secondaryButton" onClick={() => queryClient.invalidateQueries()}><RefreshCw size={14} />刷新</button>} /><div className={styles.content}><div className={styles.stageBar}><div className={styles.active}><span>1</span><b>合同验证</b><small>已接入</small></div><ArrowRight /><div><span>2</span><b>DAG 编译</b><small>待设计</small></div><ArrowRight /><div><span>3</span><b>素材生产</b><small>未连接</small></div><ArrowRight /><div><span>4</span><b>人工审核</b><small>边界已建立</small></div></div><section className={styles.list}><header><div><span>DATABASE QUEUE</span><h2>项目工作项</h2></div><b>{projects.data?.length ?? 0}</b></header>{projects.data?.map(project => <article key={project.id}><span className={styles.state}>{project.status === 'confirmed' ? <Play /> : project.status === 'review_required' ? <CheckCircle2 /> : <Clock3 />}</span><div><strong>{project.title}</strong><small>{project.id}</small></div><em>{project.status}</em>{project.status === 'confirmed' ? <button className="primaryButton" disabled={queue.isPending} onClick={() => queue.mutate(project.id)}>提交合同验证</button> : <Link className="secondaryButton" to={`/projects/${project.id}`}>查看合同</Link>}</article>) || <div className={styles.empty}>暂无项目</div>}</section><aside className={styles.notice}><strong>执行边界</strong><p>当前 Worker 只登记了 `contract_validation`。没有执行器的任务会明确阻断，不会切换到其他流程。</p></aside></div></>
}
