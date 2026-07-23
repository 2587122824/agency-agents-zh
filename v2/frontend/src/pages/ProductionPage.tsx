import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Clock3, GitBranch, Layers3, RefreshCw, RotateCcw, ShieldCheck, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { api } from '../api/client'
import type { ProductionExecution } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { blockerPresentation } from '../presentation/projectFacts'
import styles from './StagePage.module.css'

const activelyUpdating = new Set(['queued', 'running'])

const statusLabels: Record<string, string> = {
  production_ready: '等待开始制作', producing: '正在制作', quality_review: '等待素材审核', blocked: '制作已暂停',
  queued: '等待制作', running: '正在制作', completed: '已完成', review_required: '需要审核', cancelled: '已取消', skipped: '已跳过',
  waiting_phase: '等待图片确认',
  created: '已创建', claimed: '准备执行', submitting: '正在提交', submitted: '已提交', polling: '生成中', succeeded: '已完成', failed: '未完成', reconciliation_required: '需要核对',
}

const kindLabels: Record<string, string> = {
  generate_keyframe: '生成分镜图片', generate_i2v_clip: '生成视频片段', generate_three_frame_i2v_clip: '生成首中尾帧视频', generate_t2v_clip: '生成纯文本视频', generate_tts_audio: '生成配音', assemble_timeline_contract: '整理剪辑时间线', contract_validation: '检查制作合同',
}

const phaseStatusLabels: Record<string, string> = {
  not_required: '无需图片阶段', producing: '正在生成', review_required: '等待图片审核', ready_to_release: '图片已审核，等待确认',
  approved: '已确认', waiting_image_approval: '等待图片确认', completed: '已完成', blocked: '已暂停',
}

const label = (value: string | null | undefined, labels: Record<string, string>, fallback: string) => value ? labels[value] ?? fallback : fallback

type Blocker = ProductionExecution['blockers'][number]

function groupBlockers(blockers: Blocker[]) {
  const groups = new Map<string, { errorCode: string | null; blockers: Blocker[] }>()
  blockers.forEach(blocker => {
    const groupKey = blocker.error_code ?? `work_item:${blocker.work_item_id}`
    const existing = groups.get(groupKey)
    if (existing) existing.blockers.push(blocker)
    else groups.set(groupKey, { errorCode: blocker.error_code, blockers: [blocker] })
  })
  return [...groups.values()]
}

function PhaseWorkList({ items, phaseLabel }: { items: ProductionExecution['work_items']; phaseLabel: string }) {
  return <div className={styles.workList}>{items.map((item, index) => {
    const attempt = item.attempts.at(-1)
    return <article key={item.id} data-status={item.status}>
      <span className={styles.nodeState}>{item.status === 'completed' ? <CheckCircle2 /> : item.status === 'blocked' ? <AlertTriangle /> : <Clock3 />}</span>
      <div className={styles.nodeMain}><strong>{phaseLabel} {index + 1} · {label(item.kind, kindLabels, '制作素材')}</strong><span>{item.status === 'blocked' ? blockerPresentation(attempt?.error_code ?? '').title : label(attempt?.state === 'created' && item.status === 'waiting_phase' ? item.status : attempt?.state ?? item.status, statusLabels, '等待更新')}</span><details><summary>技术详情</summary><code>{item.node_key} · {item.kind} · {item.request_fingerprint.slice(0, 16)} · {attempt?.provider ?? 'NO_PROVIDER'}</code></details></div>
      <div className={styles.attempt}><small>第 {attempt?.attempt_number ?? 0} 次执行</small><strong>{label(attempt?.state === 'created' && item.status === 'waiting_phase' ? item.status : attempt?.state ?? item.status, statusLabels, '等待更新')}</strong><span>{attempt?.provider ? '已指定生成服务' : '尚未指定服务'}</span></div>
      <em>{label(item.status, statusLabels, '等待更新')}</em>
    </article>
  })}</div>
}

export function ProductionPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [projectId, setProjectId] = useState(() => searchParams.get('project') ?? '')
  const [confirmCloseBlocked, setConfirmCloseBlocked] = useState(false)
  const revisionRequestId = searchParams.get('revisionRequest') ?? ''
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.projects(), refetchInterval: 5000 })
  const execution = useQuery({
    queryKey: ['production-execution', projectId],
    queryFn: () => api.productionExecution(projectId),
    enabled: Boolean(projectId),
    refetchInterval: query => query.state.data?.work_items.some(item => activelyUpdating.has(item.status)) ? 2000 : false,
  })
  const revision = useQuery({ queryKey: ['asset-revision-request', projectId, revisionRequestId], queryFn: () => api.assetRevisionRequest(projectId, revisionRequestId), enabled: Boolean(projectId && revisionRequestId) })
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['projects'] })
    if (projectId) client.invalidateQueries({ queryKey: ['production-execution', projectId] })
  }
  const approveImagePhase = useMutation({
    mutationFn: () => api.approveImagePhase(projectId, execution.data!),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['projects'] }),
        client.invalidateQueries({ queryKey: ['production-execution', projectId] }),
        client.invalidateQueries({ queryKey: ['quality-review', projectId] }),
      ])
    },
  })
  const closeBlockedProduction = useMutation({
    mutationFn: () => api.closeBlockedProduction(projectId, execution.data!.snapshot!),
    onSuccess: async () => {
      setConfirmCloseBlocked(false)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['projects'] }),
        client.invalidateQueries({ queryKey: ['production-execution', projectId] }),
        client.invalidateQueries({ queryKey: ['planning-center', projectId] }),
      ])
      navigate(`/projects/${projectId}/plan`)
    },
  })
  const productionProjects = projects.data?.filter(project => ['production_ready', 'producing', 'quality_review', 'blocked'].includes(project.status)) ?? []
  return <>
    <PageHeader eyebrow="PRODUCTION" title="素材制作" description="查看每项素材的制作进度，以及当前需要处理的问题。" actions={<button className="secondaryButton" onClick={refresh}><RefreshCw size={14} />刷新</button>} />
    <div className={styles.content}>
      <div className={styles.stageBar}>
        <div className={styles.active}><span>1</span><b>确认制作方案</b><small>内容和费用已锁定</small></div>
        <GitBranch />
        <div className={styles.active}><span>2</span><b>准备制作</b><small>检查全部制作条件</small></div>
        <GitBranch />
        <div className={styles.active}><span>3</span><b>分阶段生成</b><small>先确认图片，再制作视频</small></div>
        <GitBranch />
        <div><span>4</span><b>审核素材</b><small>确认后进入剪辑</small></div>
      </div>

      <div className={styles.executionLayout}>
        <section className={styles.list}>
          <header><div><span>当前项目</span><h2>制作任务</h2></div><b>{productionProjects.length}</b></header>
          {productionProjects.map(project => <button className={styles.projectRow} data-selected={projectId === project.id} key={project.id} onClick={() => setProjectId(project.id)}>
            <span className={styles.state}>{project.status === 'blocked' ? <AlertTriangle /> : project.status === 'quality_review' ? <CheckCircle2 /> : <Clock3 />}</span>
            <div><strong>{project.title}</strong><small>制作方案已选定</small></div><em>{label(project.status, statusLabels, '状态待确认')}</em>
          </button>)}
          {!productionProjects.length && <div className={styles.empty}>没有已激活或已提交的生产快照</div>}
        </section>

        <section className={styles.executionPanel}>
          {revision.data && <div className={styles.revisionContext}><AlertTriangle size={17} /><div><strong>已登记生成效果问题</strong><span>{revision.data.rationale}</span><small>{revision.data.shot_code ? `对应分镜 ${revision.data.shot_code} · ` : ''}系统没有自动重做素材，请由你决定后续生产操作。</small></div></div>}
          {!projectId && <div className={styles.executionEmpty}><Layers3 size={24} /><strong>选择一个制作任务</strong><span>这里会显示每项素材的制作进度和需要处理的问题。</span></div>}
          {projectId && execution.isPending && <div className={styles.executionEmpty}>正在读取执行状态…</div>}
          {execution.error && <div className={styles.executionEmpty}><AlertTriangle size={22} /><strong>读取失败</strong><span>{execution.error.message}</span></div>}
          {approveImagePhase.error && <div className={styles.blocker}><AlertTriangle size={16} /><div><strong>图片阶段确认失败</strong><span>{approveImagePhase.error.message}</span></div></div>}
          {closeBlockedProduction.error && <div className={styles.blocker}><AlertTriangle size={16} /><div><strong>返回制作准备失败</strong><span>{closeBlockedProduction.error.message}</span></div></div>}
          {execution.data && <>
            <header className={styles.executionHeader}><div><span>制作方案 {execution.data.snapshot?.snapshot_number ?? '-'}</span><h2>{label(execution.data.project_status, statusLabels, '制作状态待确认')}</h2></div><div><strong>{execution.data.work_items.filter(item => item.status === 'completed').length}/{execution.data.work_items.length}</strong><small>已完成步骤</small></div></header>
            {execution.data.project_status === 'blocked' && execution.data.snapshot?.status === 'execution_blocked' && <div className={styles.recoveryAction} data-confirming={confirmCloseBlocked}>
              <RotateCcw size={18} />
              <div><strong>{confirmCloseBlocked ? '确认结束本次失败制作？' : '需要修正配置或制作方案'}</strong><span>{confirmCloseBlocked ? '旧任务、失败原因和费用估算会完整保留；尚未开始的步骤将取消。' : '结束当前失败快照后，返回制作准备并使用新配置创建新方案。系统不会自动重跑。'}</span></div>
              {confirmCloseBlocked ? <><button className="secondaryButton" disabled={closeBlockedProduction.isPending} onClick={() => setConfirmCloseBlocked(false)}><X size={14} />取消</button><button className="primaryButton" disabled={closeBlockedProduction.isPending} onClick={() => closeBlockedProduction.mutate()}>{closeBlockedProduction.isPending ? '正在结束…' : '确认结束并返回'}</button></> : <button className="secondaryButton" onClick={() => setConfirmCloseBlocked(true)}><RotateCcw size={14} />返回制作准备</button>}
            </div>}
            {groupBlockers(execution.data.blockers).map((group, index) => {
              const presentation = blockerPresentation(group.errorCode ?? '')
              const countLabel = group.blockers.length > 1 ? `，影响 ${group.blockers.length} 个步骤` : ''
              return <div className={styles.blocker} key={group.errorCode ?? group.blockers[0].work_item_id}><AlertTriangle size={16} /><div><strong>{presentation.title}{countLabel || (group.errorCode ? '' : ` ${index + 1}`)}</strong><span>{presentation.description}</span><details><summary>技术详情（{group.blockers.length}）</summary><div className={styles.blockerDetails}>{group.blockers.map(blocker => <code key={blocker.work_item_id}>{blocker.error_code ?? 'NO_ERROR_CODE'} · {blocker.node_key} · {blocker.error ?? '没有错误说明'}</code>)}</div></details></div></div>
            })}
            {execution.data.phases.map(phase => {
              const imagePhase = phase.phase === 'images'
              const items = execution.data.work_items.filter(item => imagePhase ? item.kind === 'generate_keyframe' : item.kind !== 'generate_keyframe')
              return <section className={styles.productionPhase} key={phase.phase} data-status={phase.status}>
                <header><div><span>{imagePhase ? '阶段 1' : '阶段 2'}</span><strong>{imagePhase ? '分镜图片' : '视频与后续制作'}</strong><small>{imagePhase ? '所有关键帧通过审核后，才会放行视频。' : '按分镜顺序制作视频，再继续音频和剪辑合同。'}</small></div><p><b>{phase.completed_count}/{phase.total_count}</b><em>{phaseStatusLabels[phase.status] ?? phase.status}</em></p></header>
                {imagePhase && phase.status === 'review_required' && <div className={styles.phaseAction}><div><strong>图片已生成，等待逐项审核</strong><span>请验证文件、运行质量检查，并明确批准每个关键帧。</span></div><Link className="primaryButton" to={`/review?project=${projectId}`}>前往审核图片</Link></div>}
                {imagePhase && phase.status === 'ready_to_release' && <div className={styles.phaseAction}><div><strong>{phase.approved_count} 张关键帧已全部批准</strong><span>确认后才会把视频和后续步骤放入生产队列，本操作不会修改工作流。</span></div><button className="primaryButton" disabled={approveImagePhase.isPending} onClick={() => approveImagePhase.mutate()}>{approveImagePhase.isPending ? '正在确认…' : '确认并开始视频制作'}</button></div>}
                {imagePhase && phase.status === 'not_required' && <div className={styles.phaseNote}>当前方案全部为纯文本生视频，不需要图片审核门禁。</div>}
                <PhaseWorkList items={items} phaseLabel={imagePhase ? '图片' : '后续步骤'} />
              </section>
            })}
            {!execution.data.work_items.length && <div className={styles.executionEmpty}><ShieldCheck size={22} /><strong>快照已激活，尚未提交</strong><span>返回方案页进行独立的高风险生产提交确认。</span></div>}
          </>}
        </section>
      </div>

      <aside className={styles.notice}><strong>执行边界</strong><p>系统只使用制作方案中已确认的工作流。图片审核不会自动放行视频，也不会自动重做、替换生成方案或重复调用付费服务。</p></aside>
    </div>
  </>
}
