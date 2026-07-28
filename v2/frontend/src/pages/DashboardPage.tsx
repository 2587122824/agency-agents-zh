import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Archive, ArchiveRestore, ArrowRight, CheckCircle2, CircleDot, FileVideo2, Plus, X } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import type { ProjectControlSummary, ProjectCreate } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { projectStatusLabel } from '../presentation/projectFacts'
import styles from './DashboardPage.module.css'

type ProjectDraft = Omit<ProjectCreate, 'production_profile'> & {
  production_profile: ProjectCreate['production_profile'] | null
}

const emptyForm: ProjectDraft = {
  title: '',
  core_topic: '',
  duration_seconds: 45,
  aspect_ratio: '9:16',
  audio_mode: 'off',
  production_profile: null,
}

function total(values: Record<string, number>) {
  return Object.values(values).reduce((sum, value) => sum + value, 0)
}

export function DashboardPage() {
  const [showForm, setShowForm] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [archiveTarget, setArchiveTarget] = useState<ProjectControlSummary | null>(null)
  const [form, setForm] = useState<ProjectDraft>(emptyForm)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const profileOptions = useQuery({ queryKey: ['project-production-profile-options'], queryFn: api.projectProductionProfileOptions })
  const controls = useQuery({ queryKey: ['project-controls', 'all'], queryFn: () => api.projectControls(true), refetchInterval: 5000 })
  const create = useMutation({
    mutationFn: api.createProject,
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ['project-controls'] })
      navigate(`/projects/${project.id}`)
    },
  })
  const archive = useMutation({
    mutationFn: (project: ProjectControlSummary) => project.archived_at
      ? api.restoreProject(project.project_id, project.state_row_version)
      : api.archiveProject(project.project_id, project.state_row_version),
    onSuccess: async () => {
      setArchiveTarget(null)
      await queryClient.invalidateQueries({ queryKey: ['project-controls'] })
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!form.production_profile) return
    create.mutate({ ...form, production_profile: form.production_profile })
  }

  const allProjects = controls.data ?? []
  const activeProjects = allProjects.filter(project => !project.archived_at)
  const archivedProjects = allProjects.filter(project => project.archived_at)
  const projects = showArchived ? archivedProjects : activeProjects
  const activeCount = activeProjects.filter(project => project.evaluated_stage !== 'completed').length
  const blockerCount = activeProjects.reduce((sum, project) => sum + project.blocker_count, 0)
  const reviewCount = activeProjects.filter(project => project.evaluated_stage === 'quality_review').length

  return <>
    <PageHeader eyebrow="PROJECT CONTROL" title="项目总控台" description="从持久化合同、工作项和交付记录计算当前阶段，只读展示生产事实。" actions={<button className="primaryButton" onClick={() => setShowForm(value => !value)}><Plus size={15} />新建项目</button>} />
    <main className={styles.content}>
      <section className={styles.overview}>
        <div className={styles.service}><i data-ok={health.data?.status === 'ok'}></i><span>V2 服务</span><strong>{health.isPending ? '连接中' : health.isError ? '未连接' : '运行正常'}</strong><small>{health.data ? `v${health.data.version}` : 'API 状态未知'}</small></div>
        <dl>
          <div><dt>进行中项目</dt><dd>{activeCount}</dd></div>
          <div><dt>待质量审核</dt><dd>{reviewCount}</dd></div>
          <div data-alert={blockerCount > 0}><dt>确定性阻断</dt><dd>{blockerCount}</dd></div>
          <div><dt>项目总数</dt><dd>{allProjects.length}</dd></div>
        </dl>
      </section>

      {showForm && <form className={styles.createForm} onSubmit={submit}>
        <div className={styles.formTitle}><div><span>NEW PROJECT</span><h2>建立需求草稿</h2></div><button type="button" className="iconButton" title="关闭" onClick={() => setShowForm(false)}><X size={16} /></button></div>
        <label>项目名称<input required value={form.title} onChange={event => setForm({ ...form, title: event.target.value })} placeholder="例如：田径训练日记" /></label>
        <label className={styles.wide}>核心主题<textarea required value={form.core_topic} onChange={event => setForm({ ...form, core_topic: event.target.value })} placeholder="准确描述本次创作主题，后端不会改写" /></label>
        <label>时长（秒）<input required type="number" min="5" max="3600" value={form.duration_seconds} onChange={event => setForm({ ...form, duration_seconds: Number(event.target.value) })} /></label>
        <label>画幅<select value={form.aspect_ratio} onChange={event => setForm({ ...form, aspect_ratio: event.target.value as ProjectCreate['aspect_ratio'] })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
        <label>音频模式<select value={form.audio_mode} onChange={event => setForm({ ...form, audio_mode: event.target.value as ProjectCreate['audio_mode'] })}><option value="off">关闭</option><option value="voiceover">旁白</option></select></label>
        <fieldset className={styles.profileFieldset}>
          <legend>视频运动控制</legend>
          <p>该选择会在首次智能体调用前冻结，并约束内容策划、分镜、制作规划、审核和生产编译。</p>
          <div className={styles.profileOptions}>
            {profileOptions.data?.video_motion_strategies.map(option => <button
              key={option.key}
              type="button"
              disabled={!option.available}
              data-selected={form.production_profile?.video_motion_strategy === option.key}
              onClick={() => option.available && setForm({
                ...form,
                production_profile: {
                  video_motion_strategy: option.key,
                  keyframe_strategy: form.production_profile?.keyframe_strategy ?? 'adaptive',
                  enforcement: 'required',
                },
              })}
            ><span>{option.display_name}{option.recommended && <b>推荐</b>}</span><small>{option.description}</small></button>)}
          </div>
        </fieldset>
        <fieldset className={styles.profileFieldset}>
          <legend>关键帧参考方式</legend>
          <p>参考方式决定三张控制图如何使用人物、风格或未来的多参考输入。</p>
          <div className={styles.profileOptions}>
            {profileOptions.data?.keyframe_strategies.map(option => <button
              key={option.key}
              type="button"
              disabled={!option.available}
              data-selected={form.production_profile?.keyframe_strategy === option.key}
              onClick={() => option.available && setForm({
                ...form,
                production_profile: {
                  video_motion_strategy: form.production_profile?.video_motion_strategy ?? 'three_frame',
                  keyframe_strategy: option.key,
                  enforcement: 'required',
                },
              })}
            ><span>{option.display_name}{option.recommended && <b>推荐</b>}</span><small>{option.description}</small></button>)}
          </div>
        </fieldset>
        <div className={styles.formActions}>{profileOptions.error && <span>{profileOptions.error.message}</span>}{create.error && <span>{create.error.message}</span>}<button className="primaryButton" disabled={create.isPending || !form.production_profile || profileOptions.isPending}>{create.isPending ? '创建中...' : '确认生产模式并创建项目'}<ArrowRight size={14} /></button></div>
      </form>}

      <section className={styles.projects}>
        <header><div><span>{showArchived ? '项目档案' : '项目列表'}</span><h2>{showArchived ? '已归档项目' : '生产项目'}</h2></div><div className={styles.projectHeaderActions}><button className="secondaryButton" onClick={() => setShowArchived(value => !value)}>{showArchived ? <ArchiveRestore size={14} /> : <Archive size={14} />}{showArchived ? `当前项目 ${activeProjects.length}` : `已归档 ${archivedProjects.length}`}</button><b>{projects.length}</b></div></header>
        {controls.isPending && <div className={styles.empty}>正在读取项目状态...</div>}
        {controls.error && <div className={styles.error}>{controls.error.message}</div>}
        {!controls.isPending && !controls.error && !projects.length && <div className={styles.empty}><CheckCircle2 size={22} /><strong>{showArchived ? '还没有归档项目' : '还没有 V2 项目'}</strong><span>{showArchived ? '归档后的项目会保留生产、费用和事件记录并显示在这里。' : '新建项目后，需求、方案、执行和交付状态会在这里汇总。'}</span></div>}
        {projects.length > 0 && <div className={styles.projectList}>{projects.map(project => {
          const workTotal = total(project.work_counts)
          const completed = project.work_counts.completed ?? 0
          const activeWork = (project.work_counts.queued ?? 0) + (project.work_counts.in_progress ?? 0)
          const archiveDisabled = !project.archived_at && activeWork > 0
          return <article key={project.project_id} data-archived={Boolean(project.archived_at)}><Link className={styles.projectLink} to={`/projects/${project.project_id}/control`}>
            <div className={styles.identity}><i data-stage={project.evaluated_stage}><FileVideo2 size={17} /></i><div><strong>{project.title}</strong><p>{project.core_topic}</p><small>{project.duration_seconds}s · {project.aspect_ratio} · 音频{project.audio_mode === 'off' ? '关闭' : '旁白'} · {project.video_motion_strategy === 'three_frame' ? '首中尾三帧' : '按镜头匹配'}</small></div></div>
            <div className={styles.stage}><span>当前阶段</span><strong>{project.stage_label}</strong><small>项目状态：{projectStatusLabel(project.persisted_status)}</small></div>
            <div className={styles.progress}><span>执行进度</span><strong>{workTotal ? `${completed} / ${workTotal}` : '尚未执行'}</strong><small>方案 {project.active_plan_version ? `v${project.active_plan_version}` : '--'} · 快照 {project.active_snapshot_number ? `#${project.active_snapshot_number}` : '--'}</small></div>
            <div className={styles.blocker} data-alert={project.blocker_count > 0}>{project.blocker_count > 0 ? <AlertTriangle size={15} /> : <CircleDot size={15} />}<div><span>阻断</span><strong>{project.blocker_count}</strong></div></div>
            <div className={styles.action}><span>下一步</span><strong>{project.archived_at ? '恢复项目后继续' : project.next_action.label}</strong><small>{project.archived_at ? `归档于 ${new Date(project.archived_at).toLocaleString('zh-CN', { hour12: false })}` : `${project.next_action.confirmation_level === 'high' ? '需要强确认' : project.next_action.confirmation_level === 'normal' ? '需要确认' : '无需确认'} · 生产费用${project.next_action.incurs_production_cost ? '是' : '否'}`}</small></div>
            <ArrowRight className={styles.arrow} size={16} />
          </Link><button className={styles.archiveButton} disabled={archiveDisabled} title={archiveDisabled ? '项目仍有排队或执行中的任务，需先取消后才能归档' : project.archived_at ? '恢复项目' : '归档项目'} onClick={() => { archive.reset(); setArchiveTarget(project) }}>{project.archived_at ? <ArchiveRestore size={16} /> : <Archive size={16} />}</button></article>
        })}</div>}
      </section>
    </main>
    {archiveTarget && <div className={styles.modal}><section><header>{archiveTarget.archived_at ? <ArchiveRestore /> : <Archive />}<div><span>{archiveTarget.archived_at ? '恢复项目' : '归档项目'}</span><h2>{archiveTarget.title}</h2></div></header><p>{archiveTarget.archived_at ? '恢复后，项目会重新出现在默认项目列表中，原制作状态保持不变。' : '归档后，项目会从默认列表隐藏；制作方案、素材、费用和审计事件均会完整保留。'}</p>{archive.error && <div className={styles.modalError}>{archive.error.message}</div>}<footer><button className="secondaryButton" onClick={() => { archive.reset(); setArchiveTarget(null) }}>取消</button><button className="primaryButton" disabled={archive.isPending} onClick={() => archive.mutate(archiveTarget)}>{archive.isPending ? '正在处理…' : archiveTarget.archived_at ? '确认恢复' : '确认归档'}</button></footer></section></div>}
  </>
}
