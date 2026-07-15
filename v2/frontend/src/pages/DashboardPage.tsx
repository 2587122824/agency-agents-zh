import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, CheckCircle2, CircleDot, FileVideo2, Plus, X } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import type { ProjectCreate } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './DashboardPage.module.css'

const emptyForm: ProjectCreate = { title: '', core_topic: '', duration_seconds: 45, aspect_ratio: '9:16', audio_mode: 'off' }

function total(values: Record<string, number>) {
  return Object.values(values).reduce((sum, value) => sum + value, 0)
}

export function DashboardPage() {
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<ProjectCreate>(emptyForm)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const controls = useQuery({ queryKey: ['project-controls'], queryFn: api.projectControls, refetchInterval: 5000 })
  const create = useMutation({
    mutationFn: api.createProject,
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ['project-controls'] })
      navigate(`/projects/${project.id}`)
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    create.mutate(form)
  }

  const projects = controls.data ?? []
  const activeCount = projects.filter(project => project.evaluated_stage !== 'completed').length
  const blockerCount = projects.reduce((sum, project) => sum + project.blocker_count, 0)
  const reviewCount = projects.filter(project => project.evaluated_stage === 'quality_review').length

  return <>
    <PageHeader eyebrow="PROJECT CONTROL" title="项目总控台" description="从持久化合同、工作项和交付记录计算当前阶段，只读展示生产事实。" actions={<button className="primaryButton" onClick={() => setShowForm(value => !value)}><Plus size={15} />新建项目</button>} />
    <main className={styles.content}>
      <section className={styles.overview}>
        <div className={styles.service}><i data-ok={health.data?.status === 'ok'}></i><span>V2 服务</span><strong>{health.isPending ? '连接中' : health.isError ? '未连接' : '运行正常'}</strong><small>{health.data ? `v${health.data.version}` : 'API 状态未知'}</small></div>
        <dl>
          <div><dt>进行中项目</dt><dd>{activeCount}</dd></div>
          <div><dt>待质量审核</dt><dd>{reviewCount}</dd></div>
          <div data-alert={blockerCount > 0}><dt>确定性阻断</dt><dd>{blockerCount}</dd></div>
          <div><dt>项目总数</dt><dd>{projects.length}</dd></div>
        </dl>
      </section>

      {showForm && <form className={styles.createForm} onSubmit={submit}>
        <div className={styles.formTitle}><div><span>NEW PROJECT</span><h2>建立需求草稿</h2></div><button type="button" className="iconButton" title="关闭" onClick={() => setShowForm(false)}><X size={16} /></button></div>
        <label>项目名称<input required value={form.title} onChange={event => setForm({ ...form, title: event.target.value })} placeholder="例如：田径训练日记" /></label>
        <label className={styles.wide}>核心主题<textarea required value={form.core_topic} onChange={event => setForm({ ...form, core_topic: event.target.value })} placeholder="准确描述本次创作主题，后端不会改写" /></label>
        <label>时长（秒）<input required type="number" min="5" max="3600" value={form.duration_seconds} onChange={event => setForm({ ...form, duration_seconds: Number(event.target.value) })} /></label>
        <label>画幅<select value={form.aspect_ratio} onChange={event => setForm({ ...form, aspect_ratio: event.target.value as ProjectCreate['aspect_ratio'] })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
        <label>音频模式<select value={form.audio_mode} onChange={event => setForm({ ...form, audio_mode: event.target.value as ProjectCreate['audio_mode'] })}><option value="off">关闭</option><option value="voiceover">旁白</option></select></label>
        <div className={styles.formActions}>{create.error && <span>{create.error.message}</span>}<button className="primaryButton" disabled={create.isPending}>{create.isPending ? '创建中...' : '创建需求草稿'}<ArrowRight size={14} /></button></div>
      </form>}

      <section className={styles.projects}>
        <header><div><span>PROJECTS</span><h2>生产项目</h2></div><b>{projects.length}</b></header>
        {controls.isPending && <div className={styles.empty}>正在读取项目状态...</div>}
        {controls.error && <div className={styles.error}>{controls.error.message}</div>}
        {!controls.isPending && !controls.error && !projects.length && <div className={styles.empty}><CheckCircle2 size={22} /><strong>还没有 V2 项目</strong><span>新建项目后，需求、方案、执行和交付状态会在这里汇总。</span></div>}
        {projects.length > 0 && <div className={styles.projectList}>{projects.map(project => {
          const workTotal = total(project.work_counts)
          const completed = project.work_counts.completed ?? 0
          return <Link key={project.project_id} to={`/projects/${project.project_id}/control`}>
            <div className={styles.identity}><i data-stage={project.evaluated_stage}><FileVideo2 size={17} /></i><div><strong>{project.title}</strong><p>{project.core_topic}</p><small>{project.duration_seconds}s · {project.aspect_ratio} · 音频{project.audio_mode === 'off' ? '关闭' : '旁白'}</small></div></div>
            <div className={styles.stage}><span>当前阶段</span><strong>{project.stage_label}</strong><small>持久状态：{project.persisted_status}</small></div>
            <div className={styles.progress}><span>执行进度</span><strong>{workTotal ? `${completed} / ${workTotal}` : '尚未执行'}</strong><small>方案 {project.active_plan_version ? `v${project.active_plan_version}` : '--'} · 快照 {project.active_snapshot_number ? `#${project.active_snapshot_number}` : '--'}</small></div>
            <div className={styles.blocker} data-alert={project.blocker_count > 0}>{project.blocker_count > 0 ? <AlertTriangle size={15} /> : <CircleDot size={15} />}<div><span>阻断</span><strong>{project.blocker_count}</strong></div></div>
            <div className={styles.action}><span>下一步</span><strong>{project.next_action.label}</strong><small>{project.next_action.confirmation_level === 'high' ? '需要强确认' : project.next_action.confirmation_level === 'normal' ? '需要确认' : '无需确认'} · 生产费用{project.next_action.incurs_production_cost ? '是' : '否'}</small></div>
            <ArrowRight className={styles.arrow} size={16} />
          </Link>
        })}</div>}
      </section>
    </main>
  </>
}
