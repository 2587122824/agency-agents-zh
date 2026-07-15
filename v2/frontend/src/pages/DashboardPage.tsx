import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, CheckCircle2, Database, Plus, Radio, Server, Workflow } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import type { ProjectCreate } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './DashboardPage.module.css'

const emptyForm: ProjectCreate = { title: '', core_topic: '', duration_seconds: 45, aspect_ratio: '9:16', audio_mode: 'off' }

export function DashboardPage() {
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<ProjectCreate>(emptyForm)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const create = useMutation({
    mutationFn: api.createProject,
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${project.id}`)
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    create.mutate(form)
  }

  return <>
    <PageHeader eyebrow="V2 FOUNDATION" title="创作系统骨架" description="界面、合同、状态和执行进程已经分开，当前不会调用生产供应商。" actions={<button className="primaryButton" onClick={() => setShowForm(value => !value)}><Plus size={15} />新建项目</button>} />
    <div className={styles.content}>
      <section className={styles.hero}>
        <div><span>FRAMEWORK STATUS</span><h2>从一个可解释的项目状态开始。</h2><p>这不是对旧管理台的继续拆补，而是一套新的模块化边界。每个生产动作都需要已确认合同和明确工作项。</p><div className={styles.health}><i className={health.data?.status === 'ok' ? styles.ok : styles.wait}></i><strong>{health.isPending ? '正在连接 V2 API' : health.isError ? 'V2 API 未连接' : 'V2 API 与 SQLite 正常'}</strong><small>{health.data ? `v${health.data.version}` : ''}</small></div></div>
        <div className={styles.heroImage} aria-label="视频创作工作画面"></div>
      </section>

      {showForm && <form className={styles.createForm} onSubmit={submit}>
        <div className={styles.formTitle}><div><span>新项目合同</span><h3>只填写明确需求</h3></div><button type="button" className="iconButton" onClick={() => setShowForm(false)}>×</button></div>
        <label>项目名称<input required value={form.title} onChange={event => setForm({ ...form, title: event.target.value })} placeholder="例如：田径训练日记" /></label>
        <label className={styles.wide}>核心主题<textarea required value={form.core_topic} onChange={event => setForm({ ...form, core_topic: event.target.value })} placeholder="准确描述这次创作的主题，不由后端改写" /></label>
        <label>时长（秒）<input required type="number" min="5" max="3600" value={form.duration_seconds} onChange={event => setForm({ ...form, duration_seconds: Number(event.target.value) })} /></label>
        <label>画幅<select value={form.aspect_ratio} onChange={event => setForm({ ...form, aspect_ratio: event.target.value as ProjectCreate['aspect_ratio'] })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
        <label>音频模式<select value={form.audio_mode} onChange={event => setForm({ ...form, audio_mode: event.target.value as ProjectCreate['audio_mode'] })}><option value="off">关闭</option><option value="voiceover">旁白</option></select></label>
        <div className={styles.formActions}>{create.error && <span>{create.error.message}</span>}<button className="primaryButton" disabled={create.isPending}>{create.isPending ? '创建中…' : '创建草稿'}<ArrowRight size={14} /></button></div>
      </form>}

      <section className={styles.architecture}>
        <article><Server /><span>HTTP BOUNDARY</span><h3>FastAPI</h3><p>API 路由只负责合同验证和明确命令。</p></article>
        <article><Database /><span>STATE AUTHORITY</span><h3>SQLite + SQLAlchemy</h3><p>项目、决策、工作项和事件都有持久状态。</p></article>
        <article><Workflow /><span>EXECUTION</span><h3>独立 Worker</h3><p>长任务离开请求线程，按登记类型执行。</p></article>
        <article><Radio /><span>OBSERVABILITY</span><h3>SSE 事件流</h3><p>前端只展示数据库中的真实进度事件。</p></article>
      </section>

      <section className={styles.projects}>
        <div className={styles.sectionTitle}><div><span>项目</span><h2>最近项目</h2></div><b>{projects.data?.length ?? 0}</b></div>
        {projects.isPending ? <div className={styles.empty}>正在读取项目…</div> : projects.data?.length ? <div className={styles.projectList}>{projects.data.map(project => <Link key={project.id} to={`/projects/${project.id}`}><span className={styles.projectCover}></span><div><strong>{project.title}</strong><p>{project.core_topic}</p><small>{project.duration_seconds}s · {project.aspect_ratio} · 音频{project.audio_mode === 'off' ? '关闭' : '旁白'}</small></div><em data-status={project.status}>{project.status}</em><ArrowRight size={15} /></Link>)}</div> : <div className={styles.empty}><CheckCircle2 size={22} /><strong>还没有 V2 项目</strong><span>新建项目后，结构化合同会写入独立数据库。</span></div>}
      </section>
    </div>
  </>
}
