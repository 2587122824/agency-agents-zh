import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, BadgeCheck, Check, CircleAlert, Clapperboard, GitBranch, Layers3, LockKeyhole, Sparkles, Users, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { ShotContract } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './PlanPage.module.css'

const briefLabels: Record<string, string> = {
  core_intent: '核心意图', duration_seconds: '目标时长', aspect_ratio: '画幅', audio_mode: '音频模式',
  narrative_structure: '叙事结构', visual_style: '视觉风格', character_refs: '人物版本', outfit_refs: '服装版本', scene_refs: '场景版本', voice_refs: '声音版本',
}

function displayValue(key: string, value: unknown) {
  if (value === null || value === undefined || value === '') return '未指定'
  if (Array.isArray(value)) return value.length ? value.join(' / ') : '未绑定'
  if (key === 'duration_seconds') return `${String(value)} 秒`
  if (key === 'audio_mode') return value === 'off' ? '关闭' : String(value)
  return String(value)
}

function ShotTable({ shots, locked }: { shots: ShotContract[]; locked: boolean }) {
  return <div className={styles.shotTableWrap}><div className={styles.tableTitle}><div><Clapperboard size={17} /><h3>分镜合同</h3></div><span>{shots.length} 个镜头 · {(shots.reduce((sum, shot) => sum + shot.duration_ms, 0) / 1000).toFixed(1)} 秒</span></div><div className={styles.tableScroll}><table><thead><tr><th>镜头</th><th>内容与构图</th><th>实体引用</th><th>约束</th><th>时长</th><th>状态</th></tr></thead><tbody>{shots.map(shot => <tr key={shot.shot_code}><td><strong>{shot.shot_code}</strong><small>{shot.shot_type}</small></td><td><strong>{shot.action}</strong><small>{shot.composition} · {shot.motion_requirement}</small></td><td><span>{shot.character_entity_version_ids.length ? shot.character_entity_version_ids.join(', ') : '人物未绑定'}</span><small>{shot.scene_entity_version_id ?? '场景未绑定'}</small></td><td><span>人脸 {shot.face_visibility}</span><small>文字 {shot.text_policy}</small></td><td>{(shot.duration_ms / 1000).toFixed(1)}s</td><td><em data-locked={locked}>{locked ? '已锁定' : '待确认'}</em></td></tr>)}</tbody></table></div></div>
}

export function PlanPage() {
  const { projectId = '' } = useParams()
  const client = useQueryClient()
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId), enabled: Boolean(projectId) })
  const planning = useQuery({ queryKey: ['planning-center', projectId], queryFn: () => api.planningCenter(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const refresh = () => client.invalidateQueries({ queryKey: ['planning-center', projectId] })
  const generateBrief = useMutation({ mutationFn: () => api.generateCreativeBrief(projectId, planning.data!.active_requirement.id), onSuccess: refresh })
  const decideBrief = useMutation({ mutationFn: (accept: boolean) => api.decideCreativeBrief(projectId, planning.data!.current_brief_candidate!.id, planning.data!.active_requirement.id, accept), onSuccess: refresh })
  const generateShots = useMutation({ mutationFn: () => api.generateShotPlan(projectId, planning.data!.active_requirement.id, planning.data!.accepted_brief_candidate!.id), onSuccess: refresh })
  const decideShots = useMutation({ mutationFn: (accept: boolean) => api.decideShotPlan(projectId, planning.data!.current_shot_candidate!.id, planning.data!.active_requirement.id, accept), onSuccess: refresh })
  if (project.isPending || planning.isPending) return <div className={styles.loading}>正在读取方案合同…</div>
  if (!project.data || !planning.data || project.error || planning.error) return <div className={styles.loading}>方案读取失败：{project.error?.message || planning.error?.message}</div>
  const data = planning.data
  const brief = data.current_brief_candidate ?? data.accepted_brief_candidate
  const shots = data.active_plan?.shots ?? data.current_shot_candidate?.shots ?? []
  const error = generateBrief.error || decideBrief.error || generateShots.error || decideShots.error
  return <>
    <PageHeader eyebrow="PLAN REVIEW" title={`${project.data.title} · 方案确认`} description="Creative 与 Director 只提交候选，用户确认后才创建不可变 plan 版本。" actions={<Link className="secondaryButton" to={`/projects/${projectId}`}><ArrowLeft size={15} />返回创作中心</Link>} />
    <div className={styles.versionBar}><span><GitBranch size={15} />requirement_v{data.active_requirement.version_number}</span><i></i><span data-active={Boolean(brief)}><Sparkles size={15} />{brief ? 'creative_brief' : 'brief 待生成'}</span><i></i><span data-active={Boolean(data.active_plan)}><LockKeyhole size={15} />{data.active_plan ? `plan_v${data.active_plan.version_number}` : 'plan 尚未创建'}</span><b>{data.next_action.label}</b></div>
    <main className={styles.layout}>
      <section className={styles.main}>
        <div className={styles.briefPanel}>
          <div className={styles.panelHeading}><div><Layers3 size={18} /><span><small>CREATIVE BRIEF</small><h2>{brief ? '创意方案候选' : '基于已确认需求生成方案'}</h2></span></div>{brief && <em data-accepted={brief.status === 'accepted'}>{brief.status === 'accepted' ? '已接受' : '尚未生效'}</em>}</div>
          {brief ? <div className={styles.briefGrid}>{Object.entries(brief.brief).filter(([key]) => key !== 'assumptions').map(([key, value]) => <div key={key}><span>{briefLabels[key] ?? key}</span><strong>{displayValue(key, value)}</strong><small>{brief.field_sources[key]?.type === 'agent_proposal' ? 'Agent 建议' : brief.field_sources[key]?.type === 'unspecified' ? '未指定' : '已确认来源'}</small></div>)}</div> : <div className={styles.empty}><Sparkles size={24} /><strong>当前需求可以进入方案规划</strong><span>运行 Mock Creative Agent 不产生模型或生产费用。</span><button className="primaryButton" disabled={generateBrief.isPending} onClick={() => generateBrief.mutate()}>{generateBrief.isPending ? '正在生成…' : '生成创意方案候选'}</button></div>}
          {data.current_brief_candidate && <div className={styles.reviewBar}><p><strong>候选尚未生效</strong><span>接受后 Director 才能读取这份 Creative Brief。</span></p><button className="secondaryButton" onClick={() => decideBrief.mutate(false)} disabled={decideBrief.isPending}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideBrief.mutate(true)} disabled={decideBrief.isPending}><Check size={14} />接受方案</button></div>}
        </div>
        {shots.length ? <><ShotTable shots={shots} locked={Boolean(data.active_plan)} />{data.current_shot_candidate && <div className={styles.reviewBar}><p><strong>分镜候选尚未生效</strong><span>确认后创建不可变 plan_v{data.plan_history.length + 1}。</span></p><button className="secondaryButton" onClick={() => decideShots.mutate(false)} disabled={decideShots.isPending}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideShots.mutate(true)} disabled={decideShots.isPending}><Check size={14} />确认分镜合同</button></div>}</> : data.accepted_brief_candidate && <div className={styles.generateShots}><Clapperboard size={22} /><div><strong>Creative Brief 已接受</strong><span>Director 将生成结构化分镜候选，不选择供应商或工作流。</span></div><button className="primaryButton" onClick={() => generateShots.mutate()} disabled={generateShots.isPending}>{generateShots.isPending ? '正在生成…' : '生成分镜候选'}</button></div>}
      </section>
      <aside className={styles.aside}>
        <section className={styles.next}><span>唯一下一步</span><h3>{data.next_action.label}</h3><p>模型费用：否 · 生产费用：否</p></section>
        <section className={styles.entities}><div className={styles.asideTitle}><div><Users size={17} /><h3>已确认实体版本</h3></div><b>{data.entity_versions.length}</b></div>{data.entity_versions.length ? data.entity_versions.map(entity => <article key={entity.id}><BadgeCheck size={16} /><div><strong>{entity.display_name}</strong><span>{entity.entity_type} · v{entity.version_number}</span><small>{entity.id}</small></div></article>) : <div className={styles.asideEmpty}>当前没有实体绑定；分镜会明确显示未绑定，不创建隐式人物或场景。</div>}</section>
        <section className={styles.boundary}><CircleAlert size={17} /><div><strong>确认边界</strong><p>{data.active_plan ? 'plan 已锁定。后续修改必须创建新需求和新方案版本。' : '当前操作只创建候选或方案版本，不创建生产快照。'}</p></div></section>
        {data.active_plan && <section className={styles.planState}><LockKeyhole size={18} /><div><strong>plan_v{data.active_plan.version_number}</strong><span>{data.active_plan.shots.length} 个镜头 · 生产快照尚未创建</span></div></section>}
        {error && <div className={styles.error}>{error.message}</div>}
      </aside>
    </main>
  </>
}
