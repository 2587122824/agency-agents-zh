import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, BadgeCheck, Calculator, Check, CircleAlert, Clapperboard, GitBranch, Layers3, LockKeyhole, Network, Pencil, ShieldCheck, Sparkles, Users, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { useState } from 'react'

import { api } from '../api/client'
import type { ShotContract } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { ShotPlanRevisionEditor } from '../components/ShotPlanRevisionEditor'
import styles from './PlanPage.module.css'

const briefLabels: Record<string, string> = {
  core_intent: '核心意图', duration_seconds: '目标时长', aspect_ratio: '画幅', audio_mode: '音频模式',
  narrative_structure: '叙事结构', visual_style: '视觉风格', character_refs: '人物版本', outfit_refs: '服装版本', scene_refs: '场景版本', voice_refs: '声音版本',
}

const snapshotStatusLabels: Record<string, string> = {
  preparing: '等待确认', locked: '费用已确认', active: '可以开始制作', submitted: '已提交制作',
  execution_blocked: '制作受阻', execution_completed: '制作完成',
}

const issueMessages: Record<string, string> = {
  COST_ESTIMATE_REQUIRED: '还没有选择覆盖全部生成步骤的计费方案。你可以先保存制作方案，但补齐价格前不能开始生成。',
  VIDEO_SPEC_ASPECT_RATIO_MISMATCH: '所选画面规格与项目画幅不一致，请重新选择。',
  KEYFRAME_SLOT_KIND_INVALID: '所选图片生成方案不可用于生成关键帧，请重新选择。',
  VIDEO_SLOT_KIND_INVALID: '所选视频生成方案不可用于首帧生视频，请重新选择。',
  WORKFLOW_VIDEO_SPEC_UNSUPPORTED: '所选生成方案不支持当前画面规格，请调整生成方案或画面规格。',
  AUDIO_OFF_HAS_TTS: '项目已关闭音频，不需要选择配音生成方案。',
  VOICEOVER_TTS_REQUIRED: '项目需要旁白，请选择配音生成方案。',
  TTS_SLOT_KIND_INVALID: '所选方案不能生成配音，请重新选择。',
  PLAN_HAS_NO_SHOTS: '当前方案还没有镜头，暂时不能准备制作。',
  ENTITY_VERSION_INVALID: '有镜头引用了不可用的人物、服装或场景版本，请先修正分镜。',
  SHOT_DURATION_UNSUPPORTED: '有镜头时长超出所选画面规格支持的范围。',
  PRICING_NOT_EFFECTIVE: '所选计费方案尚未生效。',
  PRICING_EXPIRED: '所选计费方案已经过期。',
}

const productionActionLabels: Record<string, string> = {
  CONFIRM_PLAN: '先确认创作方案',
  PUBLISH_CONFIGURATION: '先发布一套制作配置',
  CONFIRM_PRODUCTION_COST: '确认预计费用',
  CONFIGURE_PRICING: '补充并发布计费方案后，重新保存制作方案',
  ACTIVATE_SNAPSHOT: '设为当前制作方案',
  SUBMIT_PRODUCTION: '确认并开始制作',
  VIEW_PRODUCTION: '查看制作进度',
  ANALYZE_PRODUCTION_IMPACT: '选择制作设置并查看制作计划',
}

function snapshotStatusLabel(status: string, costStatus: string) {
  if (status === 'preparing' && costStatus !== 'estimated') return '等待补充费用'
  if (status.startsWith('execution_') && !snapshotStatusLabels[status]) return '制作中'
  return snapshotStatusLabels[status] ?? '状态更新中'
}

function costLabel(cost: number | null, currency: string | null) {
  return cost === null ? '费用待补充' : `预计费用 ${currency} ${cost.toFixed(6)}`
}

function userIssue(code: string) {
  return issueMessages[code] ?? '当前制作设置需要调整，请展开技术详情查看具体原因。'
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
  const preparation = useQuery({ queryKey: ['production-preparation', projectId], queryFn: () => api.productionPreparation(projectId), enabled: Boolean(projectId) })
  const [configId, setConfigId] = useState('')
  const [videoSpecId, setVideoSpecId] = useState('')
  const [keyframeSlotId, setKeyframeSlotId] = useState('')
  const [videoSlotId, setVideoSlotId] = useState('')
  const [ttsSlotId, setTtsSlotId] = useState('')
  const [pricingCatalogId, setPricingCatalogId] = useState('')
  const [confirmCost, setConfirmCost] = useState(false)
  const [confirmSubmit, setConfirmSubmit] = useState(false)
  const [editingShots, setEditingShots] = useState(false)
  const latestSnapshot = preparation.data?.snapshots[0]
  const refresh = () => client.invalidateQueries({ queryKey: ['planning-center', projectId] })
  const generateBrief = useMutation({ mutationFn: () => api.generateCreativeBrief(projectId, planning.data!.active_requirement.id), onSuccess: refresh })
  const decideBrief = useMutation({ mutationFn: (accept: boolean) => api.decideCreativeBrief(projectId, planning.data!.current_brief_candidate!.id, planning.data!.active_requirement.id, accept), onSuccess: refresh })
  const generateShots = useMutation({ mutationFn: () => api.generateShotPlan(projectId, planning.data!.active_requirement.id, planning.data!.accepted_brief_candidate!.id), onSuccess: refresh })
  const reviseShots = useMutation({ mutationFn: (patches: Array<{ target_shot_code: string; changes: Partial<ShotContract> }>) => api.reviseShotPlan(projectId, planning.data!.current_shot_candidate!.id, planning.data!.active_requirement.id, planning.data!.current_shot_candidate!.row_version, patches), onSuccess: async () => { setEditingShots(false); await refresh() } })
  const decideShots = useMutation({ mutationFn: (accept: boolean) => api.decideShotPlan(projectId, planning.data!.current_shot_candidate!.id, planning.data!.active_requirement.id, planning.data!.current_shot_candidate!.row_version, accept), onSuccess: refresh })
  const analyzeImpact = useMutation({ mutationFn: () => api.analyzeProductionImpact(projectId, { plan_version_id: planning.data!.active_plan!.id, production_config_version_id: configId, video_spec_version_id: videoSpecId, keyframe_workflow_slot_version_id: keyframeSlotId, video_workflow_slot_version_id: videoSlotId, tts_workflow_slot_version_id: ttsSlotId || null, pricing_catalog_version_id: pricingCatalogId || null }) })
  const createSnapshot = useMutation({ mutationFn: () => api.createProductionSnapshot(projectId, analyzeImpact.data!), onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) })
  const lockSnapshot = useMutation({ mutationFn: () => api.lockProductionSnapshot(projectId, latestSnapshot!), onSuccess: async () => { setConfirmCost(false); await client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) } })
  const activateSnapshot = useMutation({ mutationFn: () => api.activateProductionSnapshot(projectId, latestSnapshot!), onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) })
  const submitProduction = useMutation({ mutationFn: () => api.submitProduction(projectId, latestSnapshot!), onSuccess: async () => { setConfirmSubmit(false); await client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) } })
  if (project.isPending || planning.isPending) return <div className={styles.loading}>正在读取方案合同…</div>
  if (!project.data || !planning.data || project.error || planning.error) return <div className={styles.loading}>方案读取失败：{project.error?.message || planning.error?.message}</div>
  const data = planning.data
  const brief = data.current_brief_candidate ?? data.accepted_brief_candidate
  const shots = data.active_plan?.shots ?? data.current_shot_candidate?.shots ?? []
  const error = generateBrief.error || decideBrief.error || generateShots.error || reviseShots.error || decideShots.error || preparation.error || analyzeImpact.error || createSnapshot.error || lockSnapshot.error || activateSnapshot.error || submitProduction.error
  const selectedConfig = preparation.data?.published_configurations.find(item => item.id === configId)
  const keyframeSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'image_generation') ?? []
  const videoSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'video_generation') ?? []
  const ttsSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'tts') ?? []
  const selectedVideoSpec = selectedConfig?.video_specs.find(item => item.id === videoSpecId)
  const selectedKeyframeSlot = keyframeSlots.find(item => item.id === keyframeSlotId)
  const selectedVideoSlot = videoSlots.find(item => item.id === videoSlotId)
  const selectedTtsSlot = ttsSlots.find(item => item.id === ttsSlotId)
  const impact = analyzeImpact.data
  const canAnalyze = Boolean(data.active_plan && configId && videoSpecId && keyframeSlotId && videoSlotId && (preparation.data?.audio_mode !== 'voiceover' || ttsSlotId))
  const nextAction = data.active_plan && preparation.data ? preparation.data.next_action : data.next_action
  const nextActionLabel = productionActionLabels[nextAction.code] ?? nextAction.label
  function chooseConfig(value: string) { setConfigId(value); setVideoSpecId(''); setKeyframeSlotId(''); setVideoSlotId(''); setTtsSlotId(''); setPricingCatalogId(''); analyzeImpact.reset() }
  return <>
    <PageHeader eyebrow="PLAN REVIEW" title={`${project.data.title} · 方案确认`} description="Creative 与 Director 只提交候选，用户确认后才创建不可变 plan 版本。" actions={<Link className="secondaryButton" to={`/projects/${projectId}`}><ArrowLeft size={15} />返回创作中心</Link>} />
    <div className={styles.versionBar}><span><GitBranch size={15} />需求 v{data.active_requirement.version_number}</span><i></i><span data-active={Boolean(brief)}><Sparkles size={15} />{brief ? '创意方案' : '创意方案待生成'}</span><i></i><span data-active={Boolean(data.active_plan)}><LockKeyhole size={15} />{data.active_plan ? `创作方案 v${data.active_plan.version_number}` : '创作方案尚未创建'}</span><b>{nextActionLabel}</b></div>
    <main className={styles.layout}>
      <section className={styles.main}>
        <div className={styles.briefPanel}>
          <div className={styles.panelHeading}><div><Layers3 size={18} /><span><small>CREATIVE BRIEF</small><h2>{brief ? '创意方案候选' : '基于已确认需求生成方案'}</h2></span></div>{brief && <em data-accepted={brief.status === 'accepted'}>{brief.status === 'accepted' ? '已接受' : '尚未生效'}</em>}</div>
          {brief ? <div className={styles.briefGrid}>{Object.entries(brief.brief).filter(([key]) => key !== 'assumptions').map(([key, value]) => <div key={key}><span>{briefLabels[key] ?? key}</span><strong>{displayValue(key, value)}</strong><small>{brief.field_sources[key]?.type === 'agent_proposal' ? 'Agent 建议' : brief.field_sources[key]?.type === 'unspecified' ? '未指定' : '已确认来源'}</small></div>)}</div> : <div className={styles.empty}><Sparkles size={24} /><strong>当前需求可以进入方案规划</strong><span>运行 Mock Creative Agent 不产生模型或生产费用。</span><button className="primaryButton" disabled={generateBrief.isPending} onClick={() => generateBrief.mutate()}>{generateBrief.isPending ? '正在生成…' : '生成创意方案候选'}</button></div>}
          {data.current_brief_candidate && <div className={styles.reviewBar}><p><strong>候选尚未生效</strong><span>接受后 Director 才能读取这份 Creative Brief。</span></p><button className="secondaryButton" onClick={() => decideBrief.mutate(false)} disabled={decideBrief.isPending}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideBrief.mutate(true)} disabled={decideBrief.isPending}><Check size={14} />接受方案</button></div>}
        </div>
        {shots.length ? <><ShotTable shots={shots} locked={Boolean(data.active_plan)} />{data.current_shot_candidate && editingShots && <ShotPlanRevisionEditor candidate={data.current_shot_candidate} entities={data.entity_versions} saving={reviseShots.isPending} onCancel={() => setEditingShots(false)} onSubmit={patches => reviseShots.mutate(patches)} />}{data.current_shot_candidate && <div className={styles.reviewBar}><p><strong>分镜候选 v{data.current_shot_candidate.revision_number} 尚未生效</strong><span>确认后创建不可变 plan_v{data.plan_history.length + 1}。</span></p><button className="secondaryButton" onClick={() => setEditingShots(value => !value)} disabled={reviseShots.isPending}><Pencil size={14} />{editingShots ? '收起编辑' : '结构化修订'}</button><button className="secondaryButton" onClick={() => decideShots.mutate(false)} disabled={decideShots.isPending || editingShots}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideShots.mutate(true)} disabled={decideShots.isPending || editingShots}><Check size={14} />确认分镜合同</button></div>}</> : data.accepted_brief_candidate && <div className={styles.generateShots}><Clapperboard size={22} /><div><strong>Creative Brief 已接受</strong><span>Director 将生成结构化分镜候选，不选择供应商或工作流。</span></div><button className="primaryButton" onClick={() => generateShots.mutate()} disabled={generateShots.isPending}>{generateShots.isPending ? '正在生成…' : '生成分镜候选'}</button></div>}
        {data.active_plan && <section className={styles.productionPrep}>
          <div className={styles.panelHeading}><div><Network size={18} /><span><small>制作准备</small><h2>制作设置与费用预估</h2></span></div><em data-accepted={Boolean(preparation.data?.snapshots.length)}>{preparation.data?.snapshots.length ? `制作方案 ${preparation.data.snapshots[0].snapshot_number}` : '尚未保存'}</em></div>
          {preparation.data?.published_configurations.length ? <>
            <div className={styles.routeGrid}>
              <label>制作配置<select value={configId} onChange={event => chooseConfig(event.target.value)}><option value="">请选择配置</option>{preparation.data.published_configurations.map(item => <option key={item.id} value={item.id}>{item.display_name} · v{item.version_number}</option>)}</select></label>
              <label>画面规格<select disabled={!selectedConfig} value={videoSpecId} onChange={event => { setVideoSpecId(event.target.value); analyzeImpact.reset() }}><option value="">请选择画面规格</option>{selectedConfig?.video_specs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.width}×{item.height} · {item.fps}fps</option>)}</select></label>
              <label>图片生成方案<select disabled={!selectedConfig} value={keyframeSlotId} onChange={event => { setKeyframeSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择图片生成方案</option>{keyframeSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
              <label>视频生成方案<select disabled={!selectedConfig} value={videoSlotId} onChange={event => { setVideoSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择视频生成方案</option>{videoSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
              {preparation.data.audio_mode === 'voiceover' && <label>配音生成方案<select disabled={!selectedConfig} value={ttsSlotId} onChange={event => { setTtsSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择配音生成方案</option>{ttsSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>}
              <label>计费方案<select disabled={!selectedConfig} value={pricingCatalogId} onChange={event => { setPricingCatalogId(event.target.value); analyzeImpact.reset() }}><option value="">暂不选择（只能预览，不能开始制作）</option>{selectedConfig?.pricing_catalogs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.currency}</option>)}</select></label>
            </div>
            <div className={styles.analysisAction}><div><ShieldCheck size={17} /><p><strong>所有生成方式都由你选择</strong><span>系统只按当前选择计算制作步骤，不会自动更换生成方案，也不会在这里开始生成。</span></p></div><button className="primaryButton" disabled={!canAnalyze || analyzeImpact.isPending} onClick={() => analyzeImpact.mutate()}>{analyzeImpact.isPending ? '正在计算…' : '查看制作计划'}</button></div>
            {selectedConfig && <details className={styles.technicalDetails}><summary>查看当前选择的技术详情</summary><dl><div><dt>配置版本</dt><dd><code>{selectedConfig.id}</code></dd></div>{selectedVideoSpec && <div><dt>画面规格</dt><dd><code>{selectedVideoSpec.id}</code></dd></div>}{selectedKeyframeSlot && <div><dt>图片生成</dt><dd><code>{selectedKeyframeSlot.key}</code></dd></div>}{selectedVideoSlot && <div><dt>视频生成</dt><dd><code>{selectedVideoSlot.key}</code></dd></div>}{selectedTtsSlot && <div><dt>配音生成</dt><dd><code>{selectedTtsSlot.key}</code></dd></div>}</dl></details>}
          </> : <div className={styles.noConfig}><CircleAlert size={20} /><div><strong>没有已发布生产配置</strong><span>请先到系统配置创建、校验并发布精确版本。</span></div><Link className="secondaryButton" to="/settings">前往系统配置</Link></div>}
          {impact && <div className={styles.impactResult} data-blocked={impact.status === 'blocked'}>
            <header><Calculator size={17} /><div><strong>{impact.status === 'blocked' ? '制作计划需要调整' : '制作计划已生成，等待确认'}</strong><span>{impact.manifest.shots.length} 个镜头 · 预计调用生成服务 {impact.estimated_call_count} 次 · {costLabel(impact.estimated_cost, impact.currency)}</span></div></header>
            {impact.validation_errors.map(item => <article key={`${item.code}:${item.path}`}><b>制作设置需要调整</b><span>{userIssue(item.code)}</span></article>)}
            {impact.execution_blockers.map(item => <article className={styles.costBlocker} key={item.code}><b>还不能开始制作</b><span>{userIssue(item.code)}</span></article>)}
            <details className={styles.technicalDetails}><summary>查看本次计算的技术详情</summary><dl><div><dt>分析编号</dt><dd><code>{impact.analysis_hash}</code></dd></div><div><dt>内部状态</dt><dd><code>{impact.status}</code></dd></div>{[...impact.validation_errors, ...impact.execution_blockers].map(item => <div key={`technical:${item.code}:${'path' in item ? item.path : ''}`}><dt>{item.code}</dt><dd>{item.message ?? ('path' in item ? item.path : '')}</dd></div>)}</dl></details>
            {impact.status === 'awaiting_confirmation' && <footer><p><strong>确认后保存一份不可修改的制作方案</strong><span>这里只保存本次选择，不会开始生成，也不会产生费用。</span></p><button className="primaryButton" disabled={createSnapshot.isPending} onClick={() => createSnapshot.mutate()}><LockKeyhole size={14} />保存本次制作方案</button></footer>}
          </div>}
          {preparation.data?.snapshots.map(snapshot => <div className={styles.snapshotRow} key={snapshot.id}><LockKeyhole size={17} /><div><strong>制作方案 {snapshot.snapshot_number} · {snapshotStatusLabel(snapshot.status, snapshot.cost_status)}</strong><span>{snapshot.nodes.length} 个制作步骤 · 预计调用生成服务 {snapshot.estimated_call_count} 次 · {costLabel(snapshot.estimated_cost, snapshot.currency)}</span><details className={styles.technicalDetails}><summary>技术详情</summary><dl><div><dt>内部状态</dt><dd><code>{snapshot.status}</code></dd></div><div><dt>步骤与依赖</dt><dd>{snapshot.nodes.length} 个节点 · {snapshot.edges.length} 条依赖</dd></div><div><dt>合同校验码</dt><dd><code>{snapshot.contract_hash}</code></dd></div></dl></details></div>{snapshot.status === 'preparing' && snapshot.cost_status === 'estimated' ? <button className="primaryButton" onClick={() => setConfirmCost(true)}>确认费用并锁定方案</button> : snapshot.status === 'locked' ? <button className="primaryButton" disabled={activateSnapshot.isPending} onClick={() => activateSnapshot.mutate()}>设为当前制作方案</button> : snapshot.status === 'active' ? <button className="primaryButton" onClick={() => setConfirmSubmit(true)}>开始制作</button> : snapshot.status === 'submitted' || snapshot.status.startsWith('execution_') ? <Link className="secondaryButton" to="/production">查看制作进度</Link> : <em>{snapshotStatusLabel(snapshot.status, snapshot.cost_status)}</em>}</div>)}
        </section>}
      </section>
      <aside className={styles.aside}>
        <section className={styles.next}><span>下一步</span><h3>{nextActionLabel}</h3><p>模型费用：否 · 制作费用：{nextAction.incurs_production_cost ? '是' : '否'}</p></section>
        <section className={styles.entities}><div className={styles.asideTitle}><div><Users size={17} /><h3>已确认实体版本</h3></div><b>{data.entity_versions.length}</b></div>{data.entity_versions.length ? data.entity_versions.map(entity => <article key={entity.id}><BadgeCheck size={16} /><div><strong>{entity.display_name}</strong><span>{entity.entity_type} · v{entity.version_number}</span><small>{entity.id}</small></div></article>) : <div className={styles.asideEmpty}>当前没有实体绑定；分镜会明确显示未绑定，不创建隐式人物或场景。</div>}</section>
        {data.shot_plan_history.length > 0 && <section className={styles.candidateHistory}><div className={styles.asideTitle}><div><GitBranch size={17} /><h3>分镜候选版本</h3></div><b>{data.shot_plan_history.length}</b></div>{data.shot_plan_history.map(candidate => <article key={candidate.id} data-current={candidate.id === data.current_shot_candidate?.id}><div><strong>候选 v{candidate.revision_number}</strong><span>{candidate.source === 'user_revision' ? '用户修订' : 'Director 生成'} · {candidate.status}</span></div><small>{candidate.id.slice(-10)}</small></article>)}</section>}
        <section className={styles.boundary}><CircleAlert size={17} /><div><strong>确认边界</strong><p>{data.active_plan ? '当前创作方案已确认。后续修改需要创建新的需求和方案版本。' : '当前操作只确认创作方案，不会创建制作任务。'}</p></div></section>
        {data.active_plan && <section className={styles.planState}><LockKeyhole size={18} /><div><strong>{latestSnapshot ? `制作方案 ${latestSnapshot.snapshot_number} · ${snapshotStatusLabel(latestSnapshot.status, latestSnapshot.cost_status)}` : `创作方案 v${data.active_plan.version_number}`}</strong><span>{latestSnapshot ? `${latestSnapshot.nodes.length} 个制作步骤 · ${costLabel(latestSnapshot.estimated_cost, latestSnapshot.currency)}` : `${data.active_plan.shots.length} 个镜头 · 尚未保存制作方案`}</span></div></section>}
        {error && <div className={styles.error}>{error.message}</div>}
      </aside>
    </main>
    {confirmCost && latestSnapshot && <div className={styles.costModal}><section><header><Calculator size={20} /><div><span>费用确认</span><h2>确认制作方案 {latestSnapshot.snapshot_number} 的预计费用</h2></div></header><div className={styles.costAmount}><small>预计制作费用</small><strong>{latestSnapshot.currency} {latestSnapshot.estimated_cost?.toFixed(6)}</strong><span>预计调用生成服务 {latestSnapshot.estimated_call_count} 次</span></div><p>确认后将锁定本次制作内容、计费方案和各步骤费用。本操作不会开始生成，也不会实际扣费。</p><details className={styles.technicalDetails}><summary>查看技术详情</summary><code>{latestSnapshot.contract_hash}</code></details><footer><button className="secondaryButton" onClick={() => setConfirmCost(false)}>取消</button><button className="primaryButton" disabled={lockSnapshot.isPending} onClick={() => lockSnapshot.mutate()}><LockKeyhole size={14} />确认费用并锁定方案</button></footer></section></div>}
    {confirmSubmit && latestSnapshot && <div className={styles.costModal}><section><header><Network size={20} /><div><span>开始制作确认</span><h2>开始制作方案 {latestSnapshot.snapshot_number}</h2></div></header><div className={styles.costAmount}><small>已确认预计费用</small><strong>{latestSnapshot.currency} {latestSnapshot.estimated_cost?.toFixed(6)}</strong><span>{latestSnapshot.nodes.length} 个制作步骤 · 预计调用生成服务 {latestSnapshot.estimated_call_count} 次</span></div><p>确认后系统将按当前方案创建制作任务并进入队列。系统不会补步骤、更换生成方案或自动重试。</p><details className={styles.technicalDetails}><summary>查看技术详情</summary><code>{latestSnapshot.contract_hash}</code></details><footer><button className="secondaryButton" onClick={() => setConfirmSubmit(false)}>取消</button><button className="primaryButton" disabled={submitProduction.isPending} onClick={() => submitProduction.mutate()}><ShieldCheck size={14} />确认并开始制作</button></footer></section></div>}
  </>
}
