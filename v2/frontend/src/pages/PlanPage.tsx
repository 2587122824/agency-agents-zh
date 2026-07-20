import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, BadgeCheck, Calculator, Check, CircleAlert, Clapperboard, GitBranch, Layers3, LockKeyhole, Network, Pencil, ShieldCheck, Sparkles, Users, X } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useState } from 'react'

import { api } from '../api/client'
import type { BriefOpenQuestion, CreativeBriefCandidate, ProductionImpactAnalysis, ShotContract, ShotPlanCandidate } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { ShotPlanRevisionEditor } from '../components/ShotPlanRevisionEditor'
import styles from './PlanPage.module.css'

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
  SHOT_PLAN_SCHEMA_UNSUPPORTED: '当前是旧版分镜方案，请重新生成并确认包含画面生成描述的新方案。',
  VISUAL_PROMPT_REQUIRED: '有镜头缺少画面生成描述，请修订分镜。',
  RUNNINGHUB_VISUAL_PROMPT_BINDING_REQUIRED: '图片生成方案没有绑定“画面生成描述”，请修订系统配置。',
  REQUIRED_NEGATIVE_PROMPT_MISSING: '图片生成方案要求填写避免内容，但有镜头尚未设置。',
  REQUIRED_PRIMARY_REFERENCE_MISSING: '图片生成方案要求主参考图，但有镜头尚未明确选择。',
  PRIMARY_REFERENCE_ATTACHMENT_INVALID: '所选主参考图不可用，请检查实体来源附件。',
  REFERENCE_IMAGE_HASH_MISMATCH: '主参考图文件与登记内容不一致，请重新上传并确认。',
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

type ValidationIssue = ProductionImpactAnalysis['validation_errors'][number]

function groupValidationIssues(items: ValidationIssue[], shotCodes: string[]) {
  const groups = new Map<string, { code: string; items: ValidationIssue[]; shotCodes: string[] }>()
  items.forEach(item => {
    const group = groups.get(item.code) ?? { code: item.code, items: [], shotCodes: [] }
    group.items.push(item)
    const shotCode = shotCodes.find(code => item.path?.startsWith(`shots.${code}.`))
    if (shotCode && !group.shotCodes.includes(shotCode)) group.shotCodes.push(shotCode)
    groups.set(item.code, group)
  })
  return Array.from(groups.values())
}

function scriptKindLabel(kind: string) {
  return ({ visual_only: '纯画面', voiceover: '旁白', dialogue: '对白', on_screen_text: '画面文字' } as Record<string, string>)[kind] ?? kind
}

function BriefView({ candidate }: { candidate: CreativeBriefCandidate }) {
  const brief = candidate.brief
  return <div className={styles.briefContent}>
    <section className={styles.briefSummary}>
      <div><span>方案名称</span><strong>{brief.title}</strong></div>
      <div><span>内容承诺</span><strong>{brief.content_promise}</strong></div>
      <div><span>观众收获</span><strong>{brief.audience_takeaway}</strong></div>
    </section>
    <section className={styles.hookBand}><span>开场设计</span><strong>{brief.hook.content}</strong><small>{brief.hook.kind} · 内容策划建议</small></section>
    <section className={styles.briefSection}>
      <header><div><span>内容节拍</span><strong>{brief.narrative_beats.length} 个段落</strong></div><small>总时长 {brief.duration_seconds} 秒</small></header>
      <div className={styles.beatList}>{brief.narrative_beats.map(beat => <article key={beat.beat_code}><b>{beat.beat_code.replace('BEAT_', '')}</b><div><strong>{beat.purpose}</strong><span>{beat.summary}</span></div><time>{(beat.target_duration_ms / 1000).toFixed(1)}s</time></article>)}</div>
    </section>
    <section className={styles.briefSection}>
      <header><div><span>脚本结构</span><strong>{brief.audio_mode === 'off' ? '无音频方案' : '包含声音内容'}</strong></div><small>{brief.script_segments.length} 个脚本段</small></header>
      <div className={styles.scriptList}>{brief.script_segments.map(segment => <article key={segment.segment_code}><div><b>{segment.segment_code.replace('SEG_', '')}</b><em>{scriptKindLabel(segment.kind)}</em></div><strong>{segment.spoken_text ?? segment.on_screen_text ?? '通过画面完成这一段内容'}</strong><small>对应 {segment.beat_code}</small></article>)}</div>
    </section>
    <footer className={styles.briefFacts}>
      <span>语气 <b>{brief.tone}</b></span><span>节奏 <b>{brief.pacing}</b></span><span>平台 <b>{brief.platform_adaptation ?? '未指定，不做平台适配'}</b></span><span>实体 <b>{brief.entity_version_ids.length ? `${brief.entity_version_ids.length} 个已确认版本` : '未绑定'}</b></span>
    </footer>
  </div>
}

function BriefQuestionResolver({
  candidate,
  answers,
  busy,
  onAnswer,
  onResolve,
  onUpgradeLegacy,
}: {
  candidate: CreativeBriefCandidate
  answers: Record<string, string>
  busy: boolean
  onAnswer: (questionCode: string, answer: string) => void
  onResolve: (questions: BriefOpenQuestion[]) => void
  onUpgradeLegacy: () => void
}) {
  const legacyQuestions = candidate.brief.open_questions.filter((item): item is string => typeof item === 'string')
  const questions = candidate.brief.open_questions.filter((item): item is BriefOpenQuestion => typeof item !== 'string')
  if (legacyQuestions.length > 0) return <section className={styles.legacyQuestions}>
    <div><CircleAlert size={18} /><p><strong>这份方案的确认项还没有选项</strong><span>它由旧版合同生成。确认后调用一次当前内容策划模型，只把这些问题整理成可选择的答案，不会改变基础需求。</span></p></div>
    <ul>{legacyQuestions.map(question => <li key={question}>{question}</li>)}</ul>
    <button className="primaryButton" disabled={busy} onClick={onUpgradeLegacy}>{busy ? '正在生成可选项…' : '确认模型调用并生成可选项'}</button>
  </section>
  const complete = questions.length > 0 && questions.every(question => Boolean(answers[question.question_code]?.trim()))
  return <section className={styles.questionResolver}>
    <header><div><span>需要你的选择</span><h3>逐项确认内容方案</h3></div><b>{Object.values(answers).filter(Boolean).length}/{questions.length} 已选择</b></header>
    <div className={styles.questionList}>{questions.map(question => {
      const predefinedAnswers = new Set(question.options.map(option => option.answer))
      const currentAnswer = answers[question.question_code] ?? ''
      const customAnswer = predefinedAnswers.has(currentAnswer) ? '' : currentAnswer
      return <article key={question.question_code}>
        <div className={styles.questionTitle}><b>{question.question_code.replace('QUESTION_', '')}</b><p><strong>{question.prompt}</strong><span>{question.reason}</span></p></div>
        <div className={styles.questionOptions}>{question.options.map(option => <button type="button" key={option.option_code} data-selected={answers[question.question_code] === option.answer} onClick={() => onAnswer(question.question_code, option.answer)}><strong>{option.label}</strong><span>{option.description}</span></button>)}</div>
        <label><span>或者自行回答</span><input value={customAnswer} onChange={event => onAnswer(question.question_code, event.target.value)} placeholder="输入你的具体要求" /></label>
      </article>
    })}</div>
    <footer><p><strong>全部选择后生成方案修订版</strong><span>你的答案只用于调整当前内容方案，不会修改已确认的基础需求。</span></p><button className="primaryButton" disabled={!complete || busy} onClick={() => onResolve(questions)}>{busy ? '正在更新方案…' : '按选择更新方案'}</button></footer>
  </section>
}

function ShotTable({ shots, locked }: { shots: ShotContract[]; locked: boolean }) {
  return <div className={styles.shotTableWrap}><div className={styles.tableTitle}><div><Clapperboard size={17} /><h3>分镜合同</h3></div><span>{shots.length} 个镜头 · {(shots.reduce((sum, shot) => sum + shot.duration_ms, 0) / 1000).toFixed(1)} 秒</span></div><div className={styles.tableScroll}><table><thead><tr><th>镜头</th><th>画面生成描述</th><th>实体与主参考</th><th>约束</th><th>时长</th><th>状态</th></tr></thead><tbody>{shots.map(shot => <tr key={shot.shot_code}><td><strong>{shot.shot_code}</strong><small>{shot.narrative_beat_code ?? '历史镜头'} · {shot.shot_type}</small></td><td><strong>{shot.visual_prompt ?? '缺少画面生成描述'}</strong><small>{shot.negative_prompt ? `避免：${shot.negative_prompt}` : '避免内容未设置'} · {shot.composition}</small></td><td><span>{shot.character_entity_version_ids.length ? shot.character_entity_version_ids.join(', ') : '人物未绑定'}</span><small>{shot.primary_reference_entity_version_id ? `主参考 ${shot.primary_reference_entity_version_id}` : '无主参考图'} · {shot.continuity_group_id ?? '非连续组'}</small></td><td><span>人脸 {shot.face_visibility}</span><small>文字 {shot.text_policy} · 动态 {shot.motion_requirement} · 声音 {shot.audio_requirement}</small></td><td>{(shot.duration_ms / 1000).toFixed(1)}s</td><td><em data-locked={locked}>{locked ? '已锁定' : '待确认'}</em></td></tr>)}</tbody></table></div></div>
}

export function PlanPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId), enabled: Boolean(projectId), refetchOnMount: 'always' })
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
  const [editingBrief, setEditingBrief] = useState(false)
  const [briefRevisionInstruction, setBriefRevisionInstruction] = useState('')
  const [briefQuestionAnswers, setBriefQuestionAnswers] = useState<Record<string, string>>({})
  const latestSnapshot = preparation.data?.snapshots[0]
  const refresh = () => client.invalidateQueries({ queryKey: ['planning-center', projectId] })
  const generateBrief = useMutation({ mutationFn: () => api.generateCreativeBrief(projectId, planning.data!.active_requirement.id), onSuccess: refresh })
  const retryBrief = useMutation({ mutationFn: () => api.retryCreativeBrief(projectId, planning.data!.latest_planner_run!.id, planning.data!.active_requirement.id), onMutate: () => generateBrief.reset(), onSuccess: refresh })
  const decideBrief = useMutation({ mutationFn: (accept: boolean) => api.decideCreativeBrief(projectId, planning.data!.current_brief_candidate!.id, planning.data!.active_requirement.id, accept), onSuccess: async () => { await Promise.all([refresh(), client.invalidateQueries({ queryKey: ['project', projectId] }), client.invalidateQueries({ queryKey: ['creation-center', projectId] })]) } })
  const reviseBrief = useMutation({ mutationFn: ({ candidateId, instruction }: { candidateId: string; instruction: string }) => api.reviseCreativeBrief(projectId, candidateId, planning.data!.active_requirement.id, instruction), onSuccess: async () => { setEditingBrief(false); setBriefRevisionInstruction(''); setBriefQuestionAnswers({}); await refresh() } })
  const reviseRequirement = useMutation({ mutationFn: (candidateId: string) => api.decideCreativeBrief(projectId, candidateId, planning.data!.active_requirement.id, false, '用户选择修改基础创作需求'), onSuccess: async () => { await Promise.all([refresh(), client.invalidateQueries({ queryKey: ['project', projectId] }), client.invalidateQueries({ queryKey: ['creation-center', projectId] })]); navigate(`/projects/${projectId}`) } })
  const generateShots = useMutation({ mutationFn: () => api.generateShotPlan(projectId, planning.data!.active_requirement.id, planning.data!.accepted_brief_candidate!.id), onSuccess: refresh })
  const retryShots = useMutation({ mutationFn: () => api.retryShotPlan(projectId, planning.data!.latest_director_run!.id, planning.data!.active_requirement.id), onSuccess: refresh })
  const revisableShotCandidate = (): ShotPlanCandidate | null => planning.data?.current_shot_candidate
    ?? planning.data?.shot_plan_history.find(item => item.requirement_version_id === planning.data?.active_requirement.id && item.status === 'rejected')
    ?? null
  const reviseShots = useMutation({ mutationFn: (patches: Array<{ target_shot_code: string; changes: Partial<ShotContract> }>) => {
    const candidate = revisableShotCandidate()
    if (!candidate) throw new Error('没有可调整的分镜方案，请刷新页面后重试。')
    return api.reviseShotPlan(projectId, candidate.id, planning.data!.active_requirement.id, candidate.row_version, patches)
  }, onSuccess: async () => { setEditingShots(false); await refresh() } })
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
  const rejectedBrief = data.brief_history.find(item => item.requirement_version_id === data.active_requirement.id && item.status === 'rejected') ?? null
  const revisableBrief = data.current_brief_candidate ?? rejectedBrief
  const hasOpenBriefQuestions = Boolean(data.current_brief_candidate?.brief.open_questions.length)
  const rejectedShot = data.shot_plan_history.find(item => item.requirement_version_id === data.active_requirement.id && item.status === 'rejected') ?? null
  const editableShot = data.current_shot_candidate ?? rejectedShot
  const shots = data.active_plan?.shots ?? editableShot?.shots ?? []
  const error = generateBrief.error || retryBrief.error || reviseBrief.error || reviseRequirement.error || decideBrief.error || generateShots.error || retryShots.error || reviseShots.error || decideShots.error || preparation.error || analyzeImpact.error || createSnapshot.error || lockSnapshot.error || activateSnapshot.error || submitProduction.error
  const selectedConfig = preparation.data?.published_configurations.find(item => item.id === configId)
  const keyframeSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'image_generation') ?? []
  const videoSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'video_generation') ?? []
  const ttsSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'tts') ?? []
  const selectedVideoSpec = selectedConfig?.video_specs.find(item => item.id === videoSpecId)
  const selectedKeyframeSlot = keyframeSlots.find(item => item.id === keyframeSlotId)
  const selectedVideoSlot = videoSlots.find(item => item.id === videoSlotId)
  const selectedTtsSlot = ttsSlots.find(item => item.id === ttsSlotId)
  const impact = analyzeImpact.data
  const impactShotCodes = impact?.manifest.shots.map(shot => shot.shot_code) ?? []
  const groupedValidationIssues = impact ? groupValidationIssues(impact.validation_errors, impactShotCodes) : []
  const canAnalyze = Boolean(data.active_plan && configId && videoSpecId && keyframeSlotId && videoSlotId && (preparation.data?.audio_mode !== 'voiceover' || ttsSlotId))
  const nextAction = data.active_plan && preparation.data ? preparation.data.next_action : data.next_action
  const nextActionLabel = productionActionLabels[nextAction.code] ?? nextAction.label
  function chooseConfig(value: string) { setConfigId(value); setVideoSpecId(''); setKeyframeSlotId(''); setVideoSlotId(''); setTtsSlotId(''); setPricingCatalogId(''); analyzeImpact.reset() }
  return <>
    <PageHeader eyebrow="PLAN REVIEW" title={`${project.data.title} · 方案确认`} description="内容策划智能体与分镜导演智能体只提交候选，用户确认后才创建不可变方案版本。" actions={<Link className="secondaryButton" to={`/projects/${projectId}`}><ArrowLeft size={15} />返回创作中心</Link>} />
    <div className={styles.versionBar}><span><GitBranch size={15} />需求 v{data.active_requirement.version_number}</span><i></i><span data-active={Boolean(brief)}><Sparkles size={15} />{brief ? '内容方案' : '内容方案待生成'}</span><i></i><span data-active={Boolean(data.active_plan)}><LockKeyhole size={15} />{data.active_plan ? `创作方案 v${data.active_plan.version_number}` : '创作方案尚未创建'}</span><b>{nextActionLabel}</b></div>
    <main className={styles.layout}>
      <section className={styles.main}>
        <div className={styles.briefPanel}>
          <div className={styles.panelHeading}><div><Layers3 size={18} /><span><small>CREATIVE BRIEF</small><h2>{brief ? '内容方案候选' : '基于已确认需求生成方案'}</h2></span></div>{brief && <em data-accepted={brief.status === 'accepted'}>{brief.status === 'accepted' ? '已接受' : '尚未生效'}</em>}</div>
          {brief ? <BriefView candidate={brief} /> : data.next_action.code === 'RETRY_FAILED_CREATIVE_BRIEF' && data.latest_planner_run ? <div className={styles.empty}><CircleAlert size={24} /><strong>本次内容策划失败</strong><span>{data.latest_planner_run.error_detail || '模型没有返回符合内容方案合同的结果。'} 系统没有自动重试，也没有更换模型。</span><button className="primaryButton" disabled={retryBrief.isPending} onClick={() => retryBrief.mutate()}>{retryBrief.isPending ? '正在重跑…' : '确认模型调用并重跑'}</button></div> : data.next_action.code === 'WAIT_FOR_CREATIVE_BRIEF' ? <div className={styles.empty}><Sparkles size={24} /><strong>内容策划正在生成方案</strong><span>正在等待当前模型调用返回，期间不会再次提交相同需求。</span><button className="primaryButton" disabled>正在生成…</button></div> : data.next_action.code === 'REVISE_REQUIREMENT_FOR_NEW_BRIEF' ? <div className={styles.empty}><CircleAlert size={24} /><strong>当前内容方案已被拒绝</strong><span>小改内容结构可以直接生成方案修订版；受众、时长、音频或整体方向变化再修改创作需求。</span><div className={styles.emptyActions}><button className="primaryButton" onClick={() => setEditingBrief(true)} disabled={!rejectedBrief}><Pencil size={14} />微调当前方案</button><Link className="secondaryButton" to={`/projects/${projectId}`}>修改创作需求</Link></div></div> : <div className={styles.empty}><Sparkles size={24} /><strong>当前需求可以进入内容策划</strong><span>内容策划智能体将读取已确认需求并生成一份待审核方案，本次操作会调用已配置模型。</span><button className="primaryButton" disabled={generateBrief.isPending} onClick={() => { retryBrief.reset(); generateBrief.mutate() }}>{generateBrief.isPending ? '正在策划…' : '生成内容方案候选'}</button></div>}
          {data.current_brief_candidate?.brief.open_questions.length ? <BriefQuestionResolver candidate={data.current_brief_candidate} answers={briefQuestionAnswers} busy={reviseBrief.isPending} onAnswer={(questionCode, answer) => setBriefQuestionAnswers(current => ({ ...current, [questionCode]: answer }))} onUpgradeLegacy={() => reviseBrief.mutate({ candidateId: data.current_brief_candidate!.id, instruction: '保持当前内容方案和全部已确认基础需求不变。请将当前所有待确认问题整理为结构化问题，并为每个问题提供 2 到 3 个互斥、可直接执行的答案选项，供用户逐项选择。' })} onResolve={questions => reviseBrief.mutate({ candidateId: data.current_brief_candidate!.id, instruction: ['请根据以下用户对待确认项的逐项回答修订当前内容方案。已回答的问题不再保留为未确认项；如果回答仍不足，只保留确实无法执行的剩余问题。不要改变已确认的基础需求。', ...questions.map(question => `${question.question_code} ${question.prompt}\n用户回答：${briefQuestionAnswers[question.question_code].trim()}`)].join('\n\n') })} /> : null}
          {editingBrief && revisableBrief && <form className={styles.briefRevision} onSubmit={event => { event.preventDefault(); reviseBrief.mutate({ candidateId: revisableBrief.id, instruction: briefRevisionInstruction.trim() }) }}><div><strong>调整内容方案 v{revisableBrief.revision_number}</strong><span>只描述希望改变的内容结构、开场、节奏或文案重点。基础需求不会改变，本次会调用一次当前内容策划模型。</span></div><textarea autoFocus rows={4} value={briefRevisionInstruction} onChange={event => setBriefRevisionInstruction(event.target.value)} placeholder="例如：开头更快进入结果，减少过程说明，重点突出第一天和第七天的变化。" /><footer><button type="button" className="secondaryButton" onClick={() => { setEditingBrief(false); setBriefRevisionInstruction('') }}>取消</button><button className="primaryButton" disabled={!briefRevisionInstruction.trim() || reviseBrief.isPending}>{reviseBrief.isPending ? '正在生成修订版…' : '确认模型调用并生成修订版'}</button></footer></form>}
          {brief && data.next_action.code === 'RETRY_FAILED_CREATIVE_BRIEF' && data.latest_planner_run && <div className={styles.revisionFailure}><CircleAlert size={17} /><div><strong>方案调整没有成功</strong><span>{data.latest_planner_run.error_detail || '模型没有返回符合合同的修订版。'} 原方案仍然保留。</span></div><button className="primaryButton" disabled={retryBrief.isPending} onClick={() => retryBrief.mutate()}>{retryBrief.isPending ? '正在重跑…' : '确认费用并精确重跑'}</button></div>}
          {data.current_brief_candidate && <div className={styles.reviewBar}><p><strong>内容方案 v{data.current_brief_candidate.revision_number} 尚未生效</strong><span>{hasOpenBriefQuestions ? '请先完成上方待确认项，再接受方案。' : '可以微调方案；接受后分镜导演才能读取。修改基础需求会放弃当前方案。'}</span></p><button className="secondaryButton" onClick={() => setEditingBrief(value => !value)} disabled={reviseBrief.isPending}><Pencil size={14} />{editingBrief ? '收起调整' : '调整方案'}</button><button className="secondaryButton" onClick={() => reviseRequirement.mutate(data.current_brief_candidate!.id)} disabled={reviseRequirement.isPending}>修改创作需求</button><button className="secondaryButton" onClick={() => decideBrief.mutate(false)} disabled={decideBrief.isPending}><X size={14} />放弃方案</button><button className="primaryButton" onClick={() => decideBrief.mutate(true)} disabled={decideBrief.isPending || hasOpenBriefQuestions}><Check size={14} />接受方案</button></div>}
        </div>
        {shots.length ? <>
          <ShotTable shots={shots} locked={Boolean(data.active_plan)} />
          {editableShot && editingShots && <ShotPlanRevisionEditor projectId={projectId} candidate={editableShot} entities={data.entity_versions} saving={reviseShots.isPending} onCancel={() => setEditingShots(false)} onSubmit={patches => reviseShots.mutate(patches)} />}
          {data.current_shot_candidate && <div className={styles.reviewBar}><p><strong>分镜候选 v{data.current_shot_candidate.revision_number} 尚未生效</strong><span>确认后创建不可变 plan_v{data.plan_history.length + 1}。</span></p><button className="secondaryButton" onClick={() => setEditingShots(value => !value)} disabled={reviseShots.isPending}><Pencil size={14} />{editingShots ? '收起编辑' : '结构化修订'}</button><button className="secondaryButton" onClick={() => decideShots.mutate(false)} disabled={decideShots.isPending || editingShots}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideShots.mutate(true)} disabled={decideShots.isPending || editingShots}><Check size={14} />确认分镜合同</button></div>}
          {rejectedShot && <div className={styles.reviewBar}><p><strong>分镜方案已拒绝</strong><span>原方案仍保留供查看。选择具体镜头调整后会创建新的待确认版本，不会再次调用模型。</span></p><button className="primaryButton" onClick={() => setEditingShots(value => !value)} disabled={reviseShots.isPending}><Pencil size={14} />{editingShots ? '收起调整' : '调整具体分镜'}</button></div>}
        </> : data.accepted_brief_candidate && <div className={styles.generateShots}><Clapperboard size={22} /><div><strong>{data.next_action.code === 'RETRY_FAILED_SHOT_PLAN' ? '本次分镜生成失败' : '内容方案已接受'}</strong><span>{data.next_action.code === 'RETRY_FAILED_SHOT_PLAN' ? `${data.latest_director_run?.error_detail || '模型没有返回符合分镜合同的结果。'} 系统没有自动重试，也没有更换模型。` : '分镜导演将生成结构化分镜候选，不选择服务供应商或工作流。'}</span></div>{data.next_action.code === 'RETRY_FAILED_SHOT_PLAN' ? <button className="primaryButton" onClick={() => retryShots.mutate()} disabled={retryShots.isPending}>{retryShots.isPending ? '正在重跑…' : '确认模型调用并重跑'}</button> : data.next_action.code === 'WAIT_FOR_SHOT_PLAN' ? <button className="primaryButton" disabled>正在生成…</button> : <button className="primaryButton" onClick={() => generateShots.mutate()} disabled={generateShots.isPending}>{generateShots.isPending ? '正在生成…' : '生成分镜候选'}</button>}</div>}
        {data.active_plan && <section className={styles.productionPrep}>
          <div className={styles.panelHeading}><div><Network size={18} /><span><small>制作准备</small><h2>制作设置与费用预估</h2></span></div><em data-accepted={Boolean(preparation.data?.snapshots.length)}>{preparation.data?.snapshots.length ? `制作方案 ${preparation.data.snapshots[0].snapshot_number}` : '尚未保存'}</em></div>
          {preparation.data?.published_configurations.length ? <>
            <div className={styles.routeGrid}>
              <label>制作配置<select value={configId} onChange={event => chooseConfig(event.target.value)}><option value="">请选择配置</option>{preparation.data.published_configurations.map(item => <option key={item.id} value={item.id}>{item.display_name} · v{item.version_number}</option>)}</select></label>
              <label>画面规格<select disabled={!selectedConfig} value={videoSpecId} onChange={event => { setVideoSpecId(event.target.value); analyzeImpact.reset() }}><option value="">请选择画面规格</option>{selectedConfig?.video_specs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.width}×{item.height} · {item.fps}fps</option>)}</select></label>
              <label>图片生成方案<select id="keyframe-workflow-select" disabled={!selectedConfig} value={keyframeSlotId} onChange={event => { setKeyframeSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择图片生成方案</option>{keyframeSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
              <label>视频生成方案<select disabled={!selectedConfig} value={videoSlotId} onChange={event => { setVideoSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择视频生成方案</option>{videoSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
              {preparation.data.audio_mode === 'voiceover' && <label>配音生成方案<select disabled={!selectedConfig} value={ttsSlotId} onChange={event => { setTtsSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择配音生成方案</option>{ttsSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>}
              <label>计费方案<select disabled={!selectedConfig} value={pricingCatalogId} onChange={event => { setPricingCatalogId(event.target.value); analyzeImpact.reset() }}><option value="">暂不选择（只能预览，不能开始制作）</option>{selectedConfig?.pricing_catalogs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.currency}</option>)}</select></label>
            </div>
            <div className={styles.analysisAction}><div><ShieldCheck size={17} /><p><strong>所有生成方式都由你选择</strong><span>系统只按当前选择计算制作步骤，不会自动更换生成方案，也不会在这里开始生成。</span></p></div><button className="primaryButton" disabled={!canAnalyze || analyzeImpact.isPending} onClick={() => analyzeImpact.mutate()}>{analyzeImpact.isPending ? '正在计算…' : '查看制作计划'}</button></div>
            {selectedConfig && <details className={styles.technicalDetails}><summary>查看当前选择的技术详情</summary><dl><div><dt>配置版本</dt><dd><code>{selectedConfig.id}</code></dd></div>{selectedVideoSpec && <div><dt>画面规格</dt><dd><code>{selectedVideoSpec.id}</code></dd></div>}{selectedKeyframeSlot && <div><dt>图片生成</dt><dd><code>{selectedKeyframeSlot.key}</code></dd></div>}{selectedVideoSlot && <div><dt>视频生成</dt><dd><code>{selectedVideoSlot.key}</code></dd></div>}{selectedTtsSlot && <div><dt>配音生成</dt><dd><code>{selectedTtsSlot.key}</code></dd></div>}</dl></details>}
          </> : <div className={styles.noConfig}><CircleAlert size={20} /><div><strong>没有已发布生产配置</strong><span>请先到系统配置创建、校验并发布精确版本。</span></div><Link className="secondaryButton" to="/settings">前往系统配置</Link></div>}
          {impact && <div className={styles.impactResult} data-blocked={impact.status === 'blocked'}>
            <header><Calculator size={17} /><div><strong>{impact.status === 'blocked' ? '制作计划需要调整' : '制作计划已生成，等待确认'}</strong><span>{impact.manifest.shots.length} 个镜头 · 预计调用生成服务 {impact.estimated_call_count} 次 · {costLabel(impact.estimated_cost, impact.currency)}</span></div></header>
            {(() => {
              const referenceNodes = impact.manifest.dag.nodes.filter(node => node.kind === 'generate_keyframe').map(node => {
                const input = node.input_contract as { shot?: { shot_code?: string }; reference_image?: { attachment_id: string; content_hash: string; mime_type: string } | null }
                return { key: node.node_key, shotCode: input.shot?.shot_code ?? node.node_key, reference: input.reference_image }
              })
              const selectedCount = referenceNodes.filter(item => item.reference).length
              return <details className={styles.referenceSummary}>
                <summary><div><span>主参考图</span><strong>{selectedCount} / {referenceNodes.length} 个镜头已选择</strong></div><small>查看逐镜头状态</small></summary>
                <div className={styles.referenceFacts}>{referenceNodes.map(item => <div key={item.key}><span>{item.shotCode}</span><strong>{item.reference ? '使用主参考图' : '未选择主参考图'}</strong><small>{item.reference ? `${item.reference.mime_type} · ${item.reference.content_hash.slice(0, 12)}` : '系统不会自行选择图片'}</small></div>)}</div>
              </details>
            })()}
            {groupedValidationIssues.length > 0 && <section className={styles.issueSummary}>
              <div className={styles.issueSummaryHeading}><div><CircleAlert size={16} /><p><strong>{groupedValidationIssues.length} 项制作要求尚未满足</strong><span>相同原因已合并；受影响镜头仍按后端校验结果精确列出。</span></p></div><button type="button" className="secondaryButton" onClick={() => document.getElementById('keyframe-workflow-select')?.focus()}>重新选择图片生成方案</button></div>
              {groupedValidationIssues.map(group => <article key={group.code}><div><b>{userIssue(group.code)}</b><span>{group.shotCodes.length ? `影响 ${group.shotCodes.length} 个镜头` : `共 ${group.items.length} 条校验结果`}</span></div>{group.shotCodes.length > 0 && <p>{group.shotCodes.map(code => <em key={code}>{code}</em>)}</p>}</article>)}
            </section>}
            {impact.execution_blockers.map(item => <article className={styles.costBlocker} key={item.code}><b>还不能开始制作</b><span>{userIssue(item.code)}</span></article>)}
            <details className={styles.technicalDetails}><summary>查看本次计算的技术详情</summary><dl><div><dt>分析编号</dt><dd><code>{impact.analysis_hash}</code></dd></div><div><dt>内部状态</dt><dd><code>{impact.status}</code></dd></div>{[...impact.validation_errors, ...impact.execution_blockers].map(item => <div key={`technical:${item.code}:${'path' in item ? item.path : ''}`}><dt>{item.code}</dt><dd>{item.message ?? ('path' in item ? item.path : '')}</dd></div>)}</dl></details>
            {impact.status === 'awaiting_confirmation' && <footer><p><strong>确认后保存一份不可修改的制作方案</strong><span>这里只保存本次选择，不会开始生成，也不会产生费用。</span></p><button className="primaryButton" disabled={createSnapshot.isPending} onClick={() => createSnapshot.mutate()}><LockKeyhole size={14} />保存本次制作方案</button></footer>}
          </div>}
          {preparation.data?.snapshots.map(snapshot => <div className={styles.snapshotRow} key={snapshot.id}><LockKeyhole size={17} /><div><strong>制作方案 {snapshot.snapshot_number} · {snapshotStatusLabel(snapshot.status, snapshot.cost_status)}</strong><span>{snapshot.nodes.length} 个制作步骤 · 预计调用生成服务 {snapshot.estimated_call_count} 次 · {costLabel(snapshot.estimated_cost, snapshot.currency)}</span><details className={styles.technicalDetails}><summary>技术详情</summary><dl><div><dt>内部状态</dt><dd><code>{snapshot.status}</code></dd></div><div><dt>步骤与依赖</dt><dd>{snapshot.nodes.length} 个节点 · {snapshot.edges.length} 条依赖</dd></div><div><dt>合同校验码</dt><dd><code>{snapshot.contract_hash}</code></dd></div></dl></details></div>{snapshot.status === 'preparing' && snapshot.cost_status === 'estimated' ? <button className="primaryButton" onClick={() => setConfirmCost(true)}>确认费用并锁定方案</button> : snapshot.status === 'locked' ? <button className="primaryButton" disabled={activateSnapshot.isPending} onClick={() => activateSnapshot.mutate()}>设为当前制作方案</button> : snapshot.status === 'active' ? <button className="primaryButton" onClick={() => setConfirmSubmit(true)}>开始制作</button> : snapshot.status === 'submitted' || snapshot.status.startsWith('execution_') ? <Link className="secondaryButton" to="/production">查看制作进度</Link> : <em>{snapshotStatusLabel(snapshot.status, snapshot.cost_status)}</em>}</div>)}
        </section>}
      </section>
      <aside className={styles.aside}>
        <section className={styles.next}><span>下一步</span><h3>{nextActionLabel}</h3><p>模型调用：{'incurs_model_cost' in nextAction && nextAction.incurs_model_cost ? '是' : '否'} · 制作费用：{nextAction.incurs_production_cost ? '是' : '否'}</p></section>
        <section className={styles.entities}><div className={styles.asideTitle}><div><Users size={17} /><h3>已确认实体版本</h3></div><b>{data.entity_versions.length}</b></div>{data.entity_versions.length ? data.entity_versions.map(entity => <article key={entity.id}><BadgeCheck size={16} /><div><strong>{entity.display_name}</strong><span>{entity.entity_type} · v{entity.version_number}</span><small>{entity.id}</small></div></article>) : <div className={styles.asideEmpty}>当前没有实体绑定；分镜会明确显示未绑定，不创建隐式人物或场景。</div>}</section>
        {data.brief_history.length > 0 && <section className={styles.candidateHistory}><div className={styles.asideTitle}><div><GitBranch size={17} /><h3>内容方案版本</h3></div><b>{data.brief_history.length}</b></div>{data.brief_history.map(candidate => <article key={candidate.id} data-current={candidate.id === data.current_brief_candidate?.id}><div><strong>方案 v{candidate.revision_number}</strong><span>{candidate.source === 'planner_revision' ? '按意见调整' : '首次策划'} · {candidate.status}</span></div><small>{candidate.id.slice(-10)}</small></article>)}</section>}
        {data.shot_plan_history.length > 0 && <section className={styles.candidateHistory}><div className={styles.asideTitle}><div><GitBranch size={17} /><h3>分镜候选版本</h3></div><b>{data.shot_plan_history.length}</b></div>{data.shot_plan_history.map(candidate => <article key={candidate.id} data-current={candidate.id === data.current_shot_candidate?.id}><div><strong>候选 v{candidate.revision_number}</strong><span>{candidate.source === 'user_revision' ? '用户修订' : '分镜导演智能体生成'} · {candidate.status}</span></div><small>{candidate.id.slice(-10)}</small></article>)}</section>}
        <section className={styles.boundary}><CircleAlert size={17} /><div><strong>确认边界</strong><p>{data.active_plan ? '当前创作方案已确认。后续修改需要创建新的需求和方案版本。' : '当前操作只确认创作方案，不会创建制作任务。'}</p></div></section>
        {data.active_plan && <section className={styles.planState}><LockKeyhole size={18} /><div><strong>{latestSnapshot ? `制作方案 ${latestSnapshot.snapshot_number} · ${snapshotStatusLabel(latestSnapshot.status, latestSnapshot.cost_status)}` : `创作方案 v${data.active_plan.version_number}`}</strong><span>{latestSnapshot ? `${latestSnapshot.nodes.length} 个制作步骤 · ${costLabel(latestSnapshot.estimated_cost, latestSnapshot.currency)}` : `${data.active_plan.shots.length} 个镜头 · 尚未保存制作方案`}</span></div></section>}
        {error && <div className={styles.error}>{error.message}</div>}
      </aside>
    </main>
    {confirmCost && latestSnapshot && <div className={styles.costModal}><section><header><Calculator size={20} /><div><span>费用确认</span><h2>确认制作方案 {latestSnapshot.snapshot_number} 的预计费用</h2></div></header><div className={styles.costAmount}><small>预计制作费用</small><strong>{latestSnapshot.currency} {latestSnapshot.estimated_cost?.toFixed(6)}</strong><span>预计调用生成服务 {latestSnapshot.estimated_call_count} 次</span></div><p>确认后将锁定本次制作内容、计费方案和各步骤费用。本操作不会开始生成，也不会实际扣费。</p><details className={styles.technicalDetails}><summary>查看技术详情</summary><code>{latestSnapshot.contract_hash}</code></details><footer><button className="secondaryButton" onClick={() => setConfirmCost(false)}>取消</button><button className="primaryButton" disabled={lockSnapshot.isPending} onClick={() => lockSnapshot.mutate()}><LockKeyhole size={14} />确认费用并锁定方案</button></footer></section></div>}
    {confirmSubmit && latestSnapshot && <div className={styles.costModal}><section><header><Network size={20} /><div><span>开始制作确认</span><h2>开始制作方案 {latestSnapshot.snapshot_number}</h2></div></header><div className={styles.costAmount}><small>已确认预计费用</small><strong>{latestSnapshot.currency} {latestSnapshot.estimated_cost?.toFixed(6)}</strong><span>{latestSnapshot.nodes.length} 个制作步骤 · 预计调用生成服务 {latestSnapshot.estimated_call_count} 次</span></div><p>确认后系统将按当前方案创建制作任务并进入队列。系统不会补步骤、更换生成方案或自动重试。</p><details className={styles.technicalDetails}><summary>查看技术详情</summary><code>{latestSnapshot.contract_hash}</code></details><footer><button className="secondaryButton" onClick={() => setConfirmSubmit(false)}>取消</button><button className="primaryButton" disabled={submitProduction.isPending} onClick={() => submitProduction.mutate()}><ShieldCheck size={14} />确认并开始制作</button></footer></section></div>}
  </>
}
