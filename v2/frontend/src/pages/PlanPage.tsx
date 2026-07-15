import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, BadgeCheck, Calculator, Check, CircleAlert, Clapperboard, GitBranch, Layers3, LockKeyhole, Network, ShieldCheck, Sparkles, Users, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { useState } from 'react'

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
  const preparation = useQuery({ queryKey: ['production-preparation', projectId], queryFn: () => api.productionPreparation(projectId), enabled: Boolean(projectId) })
  const [configId, setConfigId] = useState('')
  const [videoSpecId, setVideoSpecId] = useState('')
  const [keyframeSlotId, setKeyframeSlotId] = useState('')
  const [videoSlotId, setVideoSlotId] = useState('')
  const [ttsSlotId, setTtsSlotId] = useState('')
  const [pricingCatalogId, setPricingCatalogId] = useState('')
  const [confirmCost, setConfirmCost] = useState(false)
  const latestSnapshot = preparation.data?.snapshots[0]
  const refresh = () => client.invalidateQueries({ queryKey: ['planning-center', projectId] })
  const generateBrief = useMutation({ mutationFn: () => api.generateCreativeBrief(projectId, planning.data!.active_requirement.id), onSuccess: refresh })
  const decideBrief = useMutation({ mutationFn: (accept: boolean) => api.decideCreativeBrief(projectId, planning.data!.current_brief_candidate!.id, planning.data!.active_requirement.id, accept), onSuccess: refresh })
  const generateShots = useMutation({ mutationFn: () => api.generateShotPlan(projectId, planning.data!.active_requirement.id, planning.data!.accepted_brief_candidate!.id), onSuccess: refresh })
  const decideShots = useMutation({ mutationFn: (accept: boolean) => api.decideShotPlan(projectId, planning.data!.current_shot_candidate!.id, planning.data!.active_requirement.id, accept), onSuccess: refresh })
  const analyzeImpact = useMutation({ mutationFn: () => api.analyzeProductionImpact(projectId, { plan_version_id: planning.data!.active_plan!.id, production_config_version_id: configId, video_spec_version_id: videoSpecId, keyframe_workflow_slot_version_id: keyframeSlotId, video_workflow_slot_version_id: videoSlotId, tts_workflow_slot_version_id: ttsSlotId || null, pricing_catalog_version_id: pricingCatalogId || null }) })
  const createSnapshot = useMutation({ mutationFn: () => api.createProductionSnapshot(projectId, analyzeImpact.data!), onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) })
  const lockSnapshot = useMutation({ mutationFn: () => api.lockProductionSnapshot(projectId, latestSnapshot!), onSuccess: async () => { setConfirmCost(false); await client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) } })
  if (project.isPending || planning.isPending) return <div className={styles.loading}>正在读取方案合同…</div>
  if (!project.data || !planning.data || project.error || planning.error) return <div className={styles.loading}>方案读取失败：{project.error?.message || planning.error?.message}</div>
  const data = planning.data
  const brief = data.current_brief_candidate ?? data.accepted_brief_candidate
  const shots = data.active_plan?.shots ?? data.current_shot_candidate?.shots ?? []
  const error = generateBrief.error || decideBrief.error || generateShots.error || decideShots.error || preparation.error || analyzeImpact.error || createSnapshot.error || lockSnapshot.error
  const selectedConfig = preparation.data?.published_configurations.find(item => item.id === configId)
  const keyframeSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'image_generation') ?? []
  const videoSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'video_generation') ?? []
  const ttsSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'tts') ?? []
  const impact = analyzeImpact.data
  const canAnalyze = Boolean(data.active_plan && configId && videoSpecId && keyframeSlotId && videoSlotId && (preparation.data?.audio_mode !== 'voiceover' || ttsSlotId))
  const nextAction = data.active_plan && preparation.data ? preparation.data.next_action : data.next_action
  function chooseConfig(value: string) { setConfigId(value); setVideoSpecId(''); setKeyframeSlotId(''); setVideoSlotId(''); setTtsSlotId(''); setPricingCatalogId(''); analyzeImpact.reset() }
  return <>
    <PageHeader eyebrow="PLAN REVIEW" title={`${project.data.title} · 方案确认`} description="Creative 与 Director 只提交候选，用户确认后才创建不可变 plan 版本。" actions={<Link className="secondaryButton" to={`/projects/${projectId}`}><ArrowLeft size={15} />返回创作中心</Link>} />
    <div className={styles.versionBar}><span><GitBranch size={15} />requirement_v{data.active_requirement.version_number}</span><i></i><span data-active={Boolean(brief)}><Sparkles size={15} />{brief ? 'creative_brief' : 'brief 待生成'}</span><i></i><span data-active={Boolean(data.active_plan)}><LockKeyhole size={15} />{data.active_plan ? `plan_v${data.active_plan.version_number}` : 'plan 尚未创建'}</span><b>{nextAction.label}</b></div>
    <main className={styles.layout}>
      <section className={styles.main}>
        <div className={styles.briefPanel}>
          <div className={styles.panelHeading}><div><Layers3 size={18} /><span><small>CREATIVE BRIEF</small><h2>{brief ? '创意方案候选' : '基于已确认需求生成方案'}</h2></span></div>{brief && <em data-accepted={brief.status === 'accepted'}>{brief.status === 'accepted' ? '已接受' : '尚未生效'}</em>}</div>
          {brief ? <div className={styles.briefGrid}>{Object.entries(brief.brief).filter(([key]) => key !== 'assumptions').map(([key, value]) => <div key={key}><span>{briefLabels[key] ?? key}</span><strong>{displayValue(key, value)}</strong><small>{brief.field_sources[key]?.type === 'agent_proposal' ? 'Agent 建议' : brief.field_sources[key]?.type === 'unspecified' ? '未指定' : '已确认来源'}</small></div>)}</div> : <div className={styles.empty}><Sparkles size={24} /><strong>当前需求可以进入方案规划</strong><span>运行 Mock Creative Agent 不产生模型或生产费用。</span><button className="primaryButton" disabled={generateBrief.isPending} onClick={() => generateBrief.mutate()}>{generateBrief.isPending ? '正在生成…' : '生成创意方案候选'}</button></div>}
          {data.current_brief_candidate && <div className={styles.reviewBar}><p><strong>候选尚未生效</strong><span>接受后 Director 才能读取这份 Creative Brief。</span></p><button className="secondaryButton" onClick={() => decideBrief.mutate(false)} disabled={decideBrief.isPending}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideBrief.mutate(true)} disabled={decideBrief.isPending}><Check size={14} />接受方案</button></div>}
        </div>
        {shots.length ? <><ShotTable shots={shots} locked={Boolean(data.active_plan)} />{data.current_shot_candidate && <div className={styles.reviewBar}><p><strong>分镜候选尚未生效</strong><span>确认后创建不可变 plan_v{data.plan_history.length + 1}。</span></p><button className="secondaryButton" onClick={() => decideShots.mutate(false)} disabled={decideShots.isPending}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideShots.mutate(true)} disabled={decideShots.isPending}><Check size={14} />确认分镜合同</button></div>}</> : data.accepted_brief_candidate && <div className={styles.generateShots}><Clapperboard size={22} /><div><strong>Creative Brief 已接受</strong><span>Director 将生成结构化分镜候选，不选择供应商或工作流。</span></div><button className="primaryButton" onClick={() => generateShots.mutate()} disabled={generateShots.isPending}>{generateShots.isPending ? '正在生成…' : '生成分镜候选'}</button></div>}
        {data.active_plan && <section className={styles.productionPrep}>
          <div className={styles.panelHeading}><div><Network size={18} /><span><small>PRODUCTION PREPARATION</small><h2>生产影响与快照</h2></span></div><em data-accepted={Boolean(preparation.data?.snapshots.length)}>{preparation.data?.snapshots.length ? `snapshot_${preparation.data.snapshots[0].snapshot_number}` : '尚未创建'}</em></div>
          {preparation.data?.published_configurations.length ? <>
            <div className={styles.routeGrid}>
              <label>已发布生产配置<select value={configId} onChange={event => chooseConfig(event.target.value)}><option value="">请选择精确版本</option>{preparation.data.published_configurations.map(item => <option key={item.id} value={item.id}>{item.display_name} · v{item.version_number}</option>)}</select></label>
              <label>视频规格<select disabled={!selectedConfig} value={videoSpecId} onChange={event => { setVideoSpecId(event.target.value); analyzeImpact.reset() }}><option value="">请选择</option>{selectedConfig?.video_specs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.width}×{item.height} · {item.fps}fps</option>)}</select></label>
              <label>关键帧工作流<select disabled={!selectedConfig} value={keyframeSlotId} onChange={event => { setKeyframeSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择 image_generation</option>{keyframeSlots.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.key}</option>)}</select></label>
              <label>首帧视频工作流<select disabled={!selectedConfig} value={videoSlotId} onChange={event => { setVideoSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择 video_generation</option>{videoSlots.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.key}</option>)}</select></label>
              {preparation.data.audio_mode === 'voiceover' && <label>TTS 工作流<select disabled={!selectedConfig} value={ttsSlotId} onChange={event => { setTtsSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择 tts</option>{ttsSlots.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.key}</option>)}</select></label>}
              <label>价格目录<select disabled={!selectedConfig} value={pricingCatalogId} onChange={event => { setPricingCatalogId(event.target.value); analyzeImpact.reset() }}><option value="">不选择（快照无法锁定）</option>{selectedConfig?.pricing_catalogs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.currency}</option>)}</select></label>
            </div>
            <div className={styles.analysisAction}><div><ShieldCheck size={17} /><p><strong>所有路由都由你显式选择</strong><span>系统不猜测槽位、不替换工作流，也不会在此步骤调用供应商。</span></p></div><button className="primaryButton" disabled={!canAnalyze || analyzeImpact.isPending} onClick={() => analyzeImpact.mutate()}>{analyzeImpact.isPending ? '正在分析…' : '分析生产影响'}</button></div>
          </> : <div className={styles.noConfig}><CircleAlert size={20} /><div><strong>没有已发布生产配置</strong><span>请先到系统配置创建、校验并发布精确版本。</span></div><Link className="secondaryButton" to="/settings">前往系统配置</Link></div>}
          {impact && <div className={styles.impactResult} data-blocked={impact.status === 'blocked'}>
            <header><Calculator size={17} /><div><strong>{impact.status === 'blocked' ? '生产合同存在阻断' : '影响分析等待确认'}</strong><span>{impact.manifest.shots.length} 个镜头 · {impact.estimated_call_count} 次计划调用 · {impact.estimated_cost === null ? '费用未核算' : `预计 ${impact.currency} ${impact.estimated_cost.toFixed(6)}`}</span></div><code>{impact.analysis_hash.slice(0, 12)}</code></header>
            {impact.validation_errors.map(item => <article key={`${item.code}:${item.path}`}><b>{item.code}</b><span>{item.message ?? item.path}</span></article>)}
            {impact.execution_blockers.map(item => <article className={styles.costBlocker} key={item.code}><b>{item.code}</b><span>{item.message}</span></article>)}
            {impact.status === 'awaiting_confirmation' && <footer><p><strong>确认后创建不可修改的 preparing 快照</strong><span>价格目录尚未实现，因此快照不会激活、不会生成 WorkItem。</span></p><button className="primaryButton" disabled={createSnapshot.isPending} onClick={() => createSnapshot.mutate()}><LockKeyhole size={14} />确认精确范围并创建快照</button></footer>}
          </div>}
          {preparation.data?.snapshots.map(snapshot => <div className={styles.snapshotRow} key={snapshot.id}><LockKeyhole size={17} /><div><strong>snapshot_{snapshot.snapshot_number} · {snapshot.status}</strong><span>{snapshot.nodes.length} 个 DAG 节点 · {snapshot.edges.length} 条依赖 · {snapshot.estimated_cost === null ? '费用未核算' : `${snapshot.currency} ${snapshot.estimated_cost.toFixed(6)}`}</span><code>{snapshot.contract_hash}</code></div>{snapshot.status === 'preparing' && snapshot.cost_status === 'estimated' ? <button className="primaryButton" onClick={() => setConfirmCost(true)}>确认费用并锁定</button> : <em>{snapshot.status === 'locked' ? '费用已确认' : '等待成本核算'}</em>}</div>)}
        </section>}
      </section>
      <aside className={styles.aside}>
        <section className={styles.next}><span>唯一下一步</span><h3>{nextAction.label}</h3><p>模型费用：否 · 生产费用：{nextAction.incurs_production_cost ? '是' : '否'}</p></section>
        <section className={styles.entities}><div className={styles.asideTitle}><div><Users size={17} /><h3>已确认实体版本</h3></div><b>{data.entity_versions.length}</b></div>{data.entity_versions.length ? data.entity_versions.map(entity => <article key={entity.id}><BadgeCheck size={16} /><div><strong>{entity.display_name}</strong><span>{entity.entity_type} · v{entity.version_number}</span><small>{entity.id}</small></div></article>) : <div className={styles.asideEmpty}>当前没有实体绑定；分镜会明确显示未绑定，不创建隐式人物或场景。</div>}</section>
        <section className={styles.boundary}><CircleAlert size={17} /><div><strong>确认边界</strong><p>{data.active_plan ? 'plan 已锁定。后续修改必须创建新需求和新方案版本。' : '当前操作只创建候选或方案版本，不创建生产快照。'}</p></div></section>
        {data.active_plan && <section className={styles.planState}><LockKeyhole size={18} /><div><strong>{latestSnapshot ? `snapshot_${latestSnapshot.snapshot_number} · ${latestSnapshot.status}` : `plan_v${data.active_plan.version_number}`}</strong><span>{latestSnapshot ? `${latestSnapshot.nodes.length} 个 DAG 节点 · ${latestSnapshot.cost_status}` : `${data.active_plan.shots.length} 个镜头 · 生产快照尚未创建`}</span></div></section>}
        {error && <div className={styles.error}>{error.message}</div>}
      </aside>
    </main>
    {confirmCost && latestSnapshot && <div className={styles.costModal}><section><header><Calculator size={20} /><div><span>HIGH RISK COST CONFIRMATION</span><h2>确认 snapshot_{latestSnapshot.snapshot_number} 预计费用</h2></div></header><div className={styles.costAmount}><small>预计生产费用</small><strong>{latestSnapshot.currency} {latestSnapshot.estimated_cost?.toFixed(6)}</strong><span>{latestSnapshot.estimated_call_count} 次计划供应商调用</span></div><p>确认将锁定合同哈希、精确价格目录和每个 DAG 节点的费用明细。本步骤不创建 WorkItem、不调用供应商，也不会实际扣费。</p><code>{latestSnapshot.contract_hash}</code><footer><button className="secondaryButton" onClick={() => setConfirmCost(false)}>取消</button><button className="primaryButton" disabled={lockSnapshot.isPending} onClick={() => lockSnapshot.mutate()}><LockKeyhole size={14} />确认金额并锁定</button></footer></section></div>}
  </>
}
