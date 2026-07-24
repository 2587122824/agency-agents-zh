import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, BadgeCheck, Calculator, Check, CircleAlert, Clapperboard, FileImage, GitBranch, Layers3, LockKeyhole, Network, Pencil, ShieldCheck, Sparkles, Users, X } from 'lucide-react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { BriefOpenQuestion, CreativeBriefCandidate, ProductionImpactAnalysis, ShotContract, ShotPlanCandidate } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { ShotPlanRevisionEditor } from '../components/ShotPlanRevisionEditor'
import { assetRevisionSummary } from '../presentation/assetRevision'
import styles from './PlanPage.module.css'

const snapshotStatusLabels: Record<string, string> = {
  preparing: '等待确认', locked: '费用已确认', active: '可以开始制作', submitted: '已提交制作',
  execution_blocked: '制作受阻', execution_completed: '制作完成', superseded: '已结束',
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
  REFERENCE_IMAGE_CAPABILITY_MISSING: '所选图片生成方案不支持明确传入参考图。',
  REQUIRED_REFERENCE_IMAGE_MISSING: '有镜头声明必须使用参考图，但尚未绑定可用主参考图。',
  MULTI_FRAME_CAPABILITY_MISSING: '有镜头需要多帧输入，但所选视频生成方案未声明该能力。',
  IDENTITY_CONSISTENCY_CAPABILITY_MISSING: '有镜头需要人物身份一致性，但当前参考图或图片生成方案不满足要求。',
  PRECISE_TEXT_CAPABILITY_MISSING: '有镜头要求精确画面文字，但所选图片生成方案未声明该能力。',
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

function plannerFailureMessage(errorCode?: string | null, detail?: string | null) {
  const messages: Record<string, string> = {
    CONTENT_PLANNER_RESPONSE_CONTENT_MISSING: '模型响应中没有可用的文本内容。请求证据已经保留，可以使用当前合同重新生成。',
    CONTENT_PLANNER_OUTPUT_JSON_INVALID: '模型返回的文本不是有效 JSON。原始文本和结束原因已经保留。',
    CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID: '模型返回的数据结构不符合内容方案合同。',
    CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID: '模型方案与当前项目的时长、音频、平台或实体约束冲突。',
  }
  return (errorCode && messages[errorCode]) || detail || '模型没有返回符合内容方案合同的结果。'
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

function DismissibleErrorAlert({ stage, message }: { stage: string; message: string }) {
  const [closing, setClosing] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    setClosing(false)
    setDismissed(false)
  }, [stage, message])

  useEffect(() => {
    if (!closing) return
    const timer = window.setTimeout(() => setDismissed(true), 280)
    return () => window.clearTimeout(timer)
  }, [closing])

  if (dismissed) return null
  return <div className={styles.stickyAlert} data-closing={closing} role="alert">
    <CircleAlert size={17} />
    <div><strong>{stage}</strong><span>{message}</span></div>
    <button type="button" title="关闭错误提示" aria-label="关闭错误提示" onClick={() => setClosing(true)}><X size={15} /></button>
  </div>
}

function attachmentDisplayName(filename: string) {
  return filename.replace(/\.[^.]+$/, '').trim() || '未命名人物参考'
}

type CharacterUploadStatus = {
  phase: 'uploading' | 'uploaded' | 'binding' | 'succeeded' | 'failed_after_upload'
  filename: string
  attachmentId?: string
  message?: string
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

function additionCategoryLabel(category: string) {
  return ({ narrative_structure: '叙事结构', hook: '开场设计', expression: '表达方式', example: '内容示例', visual_strategy: '视觉策略', call_to_action: '行动引导' } as Record<string, string>)[category] ?? category
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
    {brief.creative_additions.length > 0 && <section className={`${styles.briefSection} ${styles.additionsSection}`}>
      <header><div><span>策划拓展</span><strong>智能体主动补充的创意</strong></div><small>{brief.creative_additions.length} 项</small></header>
      <div className={styles.additionList}>{brief.creative_additions.map(addition => <article key={addition.addition_code}><em>{additionCategoryLabel(addition.category)}</em><div><strong>{addition.content}</strong><span>{addition.purpose}</span></div></article>)}</div>
    </section>}
    {brief.facts_requiring_confirmation.length > 0 && <section className={styles.pendingFacts}>
      <header><CircleAlert size={17} /><div><strong>这些事实还没有得到确认</strong><span>它们不会被当作正式创作依据，请在下方逐项选择。</span></div></header>
      <div>{brief.facts_requiring_confirmation.map(fact => <article key={fact.fact_code}><strong>{fact.statement}</strong><span>{fact.reason}</span></article>)}</div>
    </section>}
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
}: {
  candidate: CreativeBriefCandidate
  answers: Record<string, string>
  busy: boolean
  onAnswer: (questionCode: string, answer: string) => void
  onResolve: (questions: BriefOpenQuestion[]) => void
}) {
  const questions = candidate.brief.open_questions
  const complete = questions.length > 0 && questions.every(question => Boolean(answers[question.question_code]?.trim()))
  return <section className={styles.questionResolver}>
    <header><div><span>需要你的选择</span><h3>逐项确认内容方案</h3></div><b>{Object.values(answers).filter(Boolean).length}/{questions.length} 已选择</b></header>
    <div className={styles.questionList}>{questions.map(question => {
      const predefinedAnswers = new Set(question.options.map(option => option.answer))
      const currentAnswer = answers[question.question_code] ?? ''
      const customAnswer = predefinedAnswers.has(currentAnswer) ? '' : currentAnswer
      const pendingFact = candidate.brief.facts_requiring_confirmation.find(fact => fact.resolution_question_code === question.question_code)
      return <article key={question.question_code}>
        <div className={styles.questionTitle}><b>{question.question_code.replace('QUESTION_', '')}</b><p><strong>{question.prompt}</strong><span>{question.reason}</span></p></div>
        {pendingFact && <div className={styles.questionFact}><span>本题将确认</span><strong>{pendingFact.statement}</strong></div>}
        <div className={styles.questionOptions}>{question.options.map(option => <button type="button" key={option.option_code} data-selected={answers[question.question_code] === option.answer} onClick={() => onAnswer(question.question_code, option.answer)}><strong>{option.label}</strong><span>{option.description}</span></button>)}</div>
        <label><span>或者自行回答</span><input value={customAnswer} onChange={event => onAnswer(question.question_code, event.target.value)} placeholder="输入你的具体要求" /></label>
      </article>
    })}</div>
    <footer><p><strong>全部选择后生成方案修订版</strong><span>你的答案只用于调整当前内容方案，不会修改已确认的基础需求。</span></p><button className="primaryButton" disabled={!complete || busy} onClick={() => onResolve(questions)}>{busy ? '正在更新方案…' : '按选择更新方案'}</button></footer>
  </section>
}

function ShotFrameContract({ shot, compact = false }: { shot: ShotContract; compact?: boolean }) {
  const multiFrame = shot.generation_requirements.multi_frame_required
  const framePrompts = shot.guide_frame_prompts

  return <div className={styles.frameContract} data-compact={compact} data-multi-frame={multiFrame}>
    <div className={styles.frameMode}>
      <b>{multiFrame ? '首中尾三画面' : '单画面'}</b>
      <span>{multiFrame ? '3 张独立关键帧' : '1 张关键帧'}</span>
    </div>
    {multiFrame ? <div className={styles.frameSequence}>
      {([['start', '首帧'], ['middle', '中帧'], ['end', '尾帧']] as const).map(([role, label]) => <div key={role}>
        <span>{label}</span>
        <p data-missing={!framePrompts?.[role]}>{framePrompts?.[role] || '未填写画面描述'}</p>
      </div>)}
    </div> : <p className={styles.singleFramePrompt}>{shot.visual_prompt ?? '缺少画面生成描述'}</p>}
    {!compact && <small>视频运动描述：{shot.action}</small>}
  </div>
}

function ShotTable({ shots, locked }: { shots: ShotContract[]; locked: boolean }) {
  return <div className={styles.shotTableWrap}><div className={styles.tableTitle}><div><Clapperboard size={17} /><h3>分镜合同</h3></div><span>{shots.length} 个镜头 · {(shots.reduce((sum, shot) => sum + shot.duration_ms, 0) / 1000).toFixed(1)} 秒</span></div><div className={styles.tableScroll}><table><thead><tr><th>镜头</th><th>画面结构与生成描述</th><th>实体与主参考</th><th>约束</th><th>时长</th><th>状态</th></tr></thead><tbody>{shots.map(shot => <tr key={shot.shot_code}><td><strong>{shot.shot_code}</strong><small>{shot.narrative_beat_code ?? '未绑定节拍'} · {shot.shot_purpose} · {shot.framing}</small></td><td><ShotFrameContract shot={shot} /><small>{shot.new_information} · {shot.composition}</small></td><td><span>{shot.character_entity_version_ids.length ? shot.character_entity_version_ids.join(', ') : '人物未绑定'}</span><small>{shot.primary_reference_entity_version_id ? `主参考 ${shot.primary_reference_entity_version_id}` : '无主参考图'} · {shot.continuity_relation}</small></td><td><span>人脸 {shot.face_visibility}</span><small>文字 {shot.text_policy} · 主体运动 {shot.subject_motion} · 声音 {shot.audio_requirement}</small></td><td>{(shot.duration_ms / 1000).toFixed(1)}s</td><td><em data-locked={locked}>{locked ? '已锁定' : '待确认'}</em></td></tr>)}</tbody></table></div></div>
}

function CoverageView({ brief, shots }: { brief: CreativeBriefCandidate['brief']; shots: ShotContract[] }) {
  return <section className={styles.coverage}><header><div><span>内容覆盖</span><h3>节拍 → 脚本段 → 镜头</h3></div><b>{brief.script_segments.filter(segment => !shots.some(shot => shot.brief_segment_codes.includes(segment.segment_code))).length} 个缺口</b></header>{brief.narrative_beats.map(beat => { const segments = brief.script_segments.filter(segment => segment.beat_code === beat.beat_code); const beatShots = shots.filter(shot => shot.narrative_beat_code === beat.beat_code); return <article key={beat.beat_code}><div className={styles.coverageBeat}><strong>{beat.beat_code} · {beat.purpose}</strong><span>{beat.summary}</span><time>{(beat.target_duration_ms / 1000).toFixed(1)} 秒</time></div><div className={styles.coverageSegments}>{segments.map(segment => { const linked = beatShots.filter(shot => shot.brief_segment_codes.includes(segment.segment_code)); return <div key={segment.segment_code} data-gap={!linked.length}><p><b>{segment.segment_code}</b><span>{segment.spoken_text || segment.on_screen_text || scriptKindLabel(segment.kind)}</span></p><p>{linked.length ? linked.map(shot => <em key={shot.shot_code}>{shot.shot_code} · {shot.new_information}</em>) : <strong>尚无镜头覆盖</strong>}</p></div> })}</div></article>})}</section>
}

export function PlanPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const client = useQueryClient()
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId), enabled: Boolean(projectId), refetchOnMount: 'always' })
  const planning = useQuery({ queryKey: ['planning-center', projectId], queryFn: () => api.planningCenter(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  const creation = useQuery({ queryKey: ['creation-center', projectId], queryFn: () => api.creationCenter(projectId), enabled: Boolean(projectId) })
  const preparation = useQuery({ queryKey: ['production-preparation', projectId], queryFn: () => api.productionPreparation(projectId), enabled: Boolean(projectId) })
  const [configId, setConfigId] = useState('')
  const [videoSpecId, setVideoSpecId] = useState('')
  const [keyframeSlotId, setKeyframeSlotId] = useState('')
  const [videoSlotId, setVideoSlotId] = useState('')
  const [shotWorkflowAssignments, setShotWorkflowAssignments] = useState<Record<string, { keyframe: string; video: string }>>({})
  const [ttsSlotId, setTtsSlotId] = useState('')
  const [pricingCatalogId, setPricingCatalogId] = useState('')
  const [confirmCost, setConfirmCost] = useState(false)
  const [confirmSubmit, setConfirmSubmit] = useState(false)
  const [confirmCancelRevision, setConfirmCancelRevision] = useState(false)
  const [editingShots, setEditingShots] = useState(false)
  const [editingBrief, setEditingBrief] = useState(false)
  const [briefRevisionInstruction, setBriefRevisionInstruction] = useState('')
  const [briefQuestionAnswers, setBriefQuestionAnswers] = useState<Record<string, string>>({})
  const [characterUploadStatus, setCharacterUploadStatus] = useState<CharacterUploadStatus | null>(null)
  const characterFileInput = useRef<HTMLInputElement>(null)
  const currentSnapshot = preparation.data?.current_snapshot
  const refresh = () => client.invalidateQueries({ queryKey: ['planning-center', projectId] })
  const generateBrief = useMutation({ mutationFn: () => api.generateCreativeBrief(projectId, planning.data!.active_requirement.id), onSuccess: refresh })
  const retryBrief = useMutation({ mutationFn: () => api.retryCreativeBrief(projectId, planning.data!.latest_planner_run!.id, planning.data!.active_requirement.id), onMutate: () => generateBrief.reset(), onSuccess: refresh })
  const regenerateBrief = useMutation({ mutationFn: () => api.regenerateCreativeBriefWithCurrentContract(projectId, planning.data!.latest_planner_run!.id, planning.data!.active_requirement.id), onMutate: () => { generateBrief.reset(); retryBrief.reset() }, onSuccess: refresh })
  const decideBrief = useMutation({ mutationFn: (accept: boolean) => api.decideCreativeBrief(projectId, planning.data!.current_brief_candidate!.id, planning.data!.active_requirement.id, accept), onSuccess: async () => { await Promise.all([refresh(), client.invalidateQueries({ queryKey: ['project', projectId] }), client.invalidateQueries({ queryKey: ['creation-center', projectId] })]) } })
  const reviseBrief = useMutation({ mutationFn: ({ candidateId, instruction }: { candidateId: string; instruction: string }) => api.reviseCreativeBrief(projectId, candidateId, planning.data!.active_requirement.id, instruction), onSuccess: async () => { setEditingBrief(false); setBriefRevisionInstruction(''); setBriefQuestionAnswers({}); await refresh() } })
  const reviseRequirement = useMutation({ mutationFn: (candidateId: string) => api.decideCreativeBrief(projectId, candidateId, planning.data!.active_requirement.id, false, '用户选择修改基础创作需求'), onSuccess: async () => { await Promise.all([refresh(), client.invalidateQueries({ queryKey: ['project', projectId] }), client.invalidateQueries({ queryKey: ['creation-center', projectId] })]); navigate(`/projects/${projectId}`) } })
  const generateShots = useMutation({ mutationFn: () => api.generateShotPlan(projectId, planning.data!.active_requirement.id, planning.data!.accepted_brief_candidate!.id), onSuccess: refresh })
  const retryShots = useMutation({ mutationFn: () => api.retryShotPlan(projectId, planning.data!.latest_director_run!.id, planning.data!.active_requirement.id), onSuccess: refresh })
  const uploadCharacter = useMutation({
    mutationFn: async (file: File) => {
      setCharacterUploadStatus({ phase: 'uploading', filename: file.name })
      const attachment = await api.registerAttachment(projectId, file)
      setCharacterUploadStatus({ phase: 'uploaded', filename: file.name, attachmentId: attachment.id })
      setCharacterUploadStatus({ phase: 'binding', filename: file.name, attachmentId: attachment.id })
      try {
        const binding = await api.bindAttachment(projectId, attachment.id, 'identity_reference', { createNew: true, displayName: attachmentDisplayName(file.name) })
        return { attachment, binding }
      } catch (error) {
        setCharacterUploadStatus({
          phase: 'failed_after_upload', filename: file.name, attachmentId: attachment.id,
          message: error instanceof Error ? error.message : '人物登记失败',
        })
        throw error
      }
    },
    onSuccess: async ({ attachment }) => {
      setCharacterUploadStatus({ phase: 'succeeded', filename: attachment.original_filename, attachmentId: attachment.id })
      await Promise.all([refresh(), client.invalidateQueries({ queryKey: ['creation-center', projectId] })])
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['creation-center', projectId] }),
  })
  const registerExistingCharacter = useMutation({
    mutationFn: (attachment: { id: string; original_filename: string }) => api.bindAttachment(
      projectId,
      attachment.id,
      'identity_reference',
      { createNew: true, displayName: attachmentDisplayName(attachment.original_filename) },
    ),
    onMutate: attachment => setCharacterUploadStatus({ phase: 'binding', filename: attachment.original_filename, attachmentId: attachment.id }),
    onSuccess: async (_binding, attachment) => {
      setCharacterUploadStatus({ phase: 'succeeded', filename: attachment.original_filename, attachmentId: attachment.id })
      await Promise.all([refresh(), client.invalidateQueries({ queryKey: ['creation-center', projectId] })])
    },
    onError: (error, attachment) => setCharacterUploadStatus({
      phase: 'failed_after_upload', filename: attachment.original_filename, attachmentId: attachment.id,
      message: error instanceof Error ? error.message : '人物登记失败',
    }),
  })
  const startShotRevision = useMutation({ mutationFn: () => api.startShotPlanRevision(projectId, planning.data!.active_plan!.id), onSuccess: async () => { setEditingShots(true); await refresh() } })
  const revisableShotCandidate = (): ShotPlanCandidate | null => planning.data?.current_shot_candidate
    ?? planning.data?.revision_draft
    ?? planning.data?.shot_plan_history.find(item => item.requirement_version_id === planning.data?.active_requirement.id && item.status === 'rejected')
    ?? null
  const reviseShots = useMutation({ mutationFn: (patches: Array<{ target_shot_code: string; changes: Partial<ShotContract> }>) => {
    const candidate = revisableShotCandidate()
    if (!candidate) throw new Error('没有可调整的分镜方案，请刷新页面后重试。')
    return api.reviseShotPlan(projectId, candidate.id, planning.data!.active_requirement.id, candidate.row_version, patches)
  }, onSuccess: async () => { setEditingShots(false); await refresh() } })
  const reviseShotsWithDirector = useMutation({ mutationFn: ({ selected, instruction }: { selected: string[]; instruction: string }) => {
    const candidate = revisableShotCandidate()
    if (!candidate) throw new Error('没有可调整的分镜方案，请刷新页面后重试。')
    return api.reviseShotPlanWithDirector(projectId, candidate.id, planning.data!.active_requirement.id, candidate.row_version, selected, instruction)
  }, onSuccess: async () => { setEditingShots(false); await refresh() } })
  const decideShots = useMutation({ mutationFn: (accept: boolean) => api.decideShotPlan(projectId, planning.data!.current_shot_candidate!.id, planning.data!.active_requirement.id, planning.data!.current_shot_candidate!.row_version, accept), onSuccess: refresh })
  const cancelRevision = useMutation({ mutationFn: () => api.cancelAssetRevisionRequest(projectId, planning.data!.revision_context!.id, '用户明确放弃本次成品回改'), onSuccess: async () => { setConfirmCancelRevision(false); setEditingShots(false); await refresh() } })
  const cancelManualRevision = useMutation({ mutationFn: () => api.cancelShotPlanRevision(projectId, planning.data!.revision_draft!.id, planning.data!.revision_draft!.row_version), onSuccess: async () => { setEditingShots(false); await refresh() } })
  const generateProductionPlan = useMutation({
    mutationFn: () => api.generateProductionPlan(projectId, planning.data!.active_plan!.id, configId, videoSpecId),
    onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }),
  })
  const retryProductionPlan = useMutation({
    mutationFn: () => api.retryProductionPlan(projectId, preparation.data!.latest_production_planner_run!.id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }),
  })
  const decideProductionPlan = useMutation({
    mutationFn: ({ candidateId, accept }: { candidateId: string; accept: boolean }) => {
      const candidate = preparation.data!.production_plan_candidates.find(item => item.id === candidateId)!
      const assignments = planning.data!.active_plan!.shots.map(shot => ({
        shot_code: shot.shot_code,
        keyframe_workflow_slot_version_id: shotWorkflowAssignments[shot.shot_code]?.keyframe || null,
        video_workflow_slot_version_id: shotWorkflowAssignments[shot.shot_code]?.video || '',
      }))
      return api.decideProductionPlan(projectId, candidate, accept, assignments)
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }),
  })
  const analyzeImpact = useMutation({ mutationFn: () => api.analyzeProductionImpact(projectId, { plan_version_id: planning.data!.active_plan!.id, production_config_version_id: configId, video_spec_version_id: videoSpecId, shot_workflow_assignments: planning.data!.active_plan!.shots.map(shot => ({ shot_code: shot.shot_code, keyframe_workflow_slot_version_id: shotWorkflowAssignments[shot.shot_code]?.keyframe || null, video_workflow_slot_version_id: shotWorkflowAssignments[shot.shot_code]?.video || '' })), tts_workflow_slot_version_id: ttsSlotId || null, pricing_catalog_version_id: pricingCatalogId || null }) })
  const createSnapshot = useMutation({ mutationFn: () => api.createProductionSnapshot(projectId, analyzeImpact.data!), onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) })
  const lockSnapshot = useMutation({ mutationFn: () => api.lockProductionSnapshot(projectId, currentSnapshot!), onSuccess: async () => { setConfirmCost(false); await client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) } })
  const activateSnapshot = useMutation({ mutationFn: () => api.activateProductionSnapshot(projectId, currentSnapshot!), onSuccess: () => client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) })
  const submitProduction = useMutation({ mutationFn: () => api.submitProduction(projectId, currentSnapshot!), onSuccess: async () => { setConfirmSubmit(false); await client.invalidateQueries({ queryKey: ['production-preparation', projectId] }) } })
  useEffect(() => {
    if (searchParams.get('revisionRequest') && planning.data?.revision_draft) setEditingShots(true)
  }, [planning.data?.revision_draft, searchParams])
  useEffect(() => {
    setShotWorkflowAssignments({})
    analyzeImpact.reset()
  }, [planning.data?.active_plan?.id])
  useEffect(() => {
    const configurations = preparation.data?.published_configurations ?? []
    if (!configId && configurations.length === 1) {
      setConfigId(configurations[0].id)
    }
  }, [configId, preparation.data?.published_configurations])
  useEffect(() => {
    const config = preparation.data?.published_configurations.find(item => item.id === configId)
    if (!config) return
    const keyframeOptions = config.workflow_slots.filter(item => item.operation_kind === 'image_generation')
    const videoOptions = config.workflow_slots.filter(item => ['video_generation', 'multi_frame_video_generation', 'text_to_video_generation'].includes(item.operation_kind))
    const ttsOptions = config.workflow_slots.filter(item => item.operation_kind === 'tts')
    if (!videoSpecId && config.video_specs.length === 1) setVideoSpecId(config.video_specs[0].id)
    if (!pricingCatalogId && config.pricing_catalogs.length === 1) setPricingCatalogId(config.pricing_catalogs[0].id)
    if (!videoSlotId && videoOptions.length === 1) setVideoSlotId(videoOptions[0].id)
    if (
      !keyframeSlotId
      && keyframeOptions.length === 1
      && !(videoOptions.length === 1 && videoOptions[0].operation_kind === 'text_to_video_generation')
    ) {
      setKeyframeSlotId(keyframeOptions[0].id)
    }
    if (preparation.data?.audio_mode === 'voiceover' && !ttsSlotId && ttsOptions.length === 1) {
      setTtsSlotId(ttsOptions[0].id)
    }
  }, [
    configId,
    keyframeSlotId,
    preparation.data?.audio_mode,
    preparation.data?.published_configurations,
    pricingCatalogId,
    ttsSlotId,
    videoSlotId,
    videoSpecId,
  ])
  useEffect(() => {
    if (!configId || !videoSpecId) return
    const candidate = preparation.data?.production_plan_candidates.find(item =>
      item.production_config_version_id === configId
      && item.video_spec_version_id === videoSpecId
      && ['awaiting_review', 'accepted'].includes(item.status),
    )
    const assignments = candidate?.confirmed_assignments ?? candidate?.proposed_assignments
    if (!assignments) return
    setShotWorkflowAssignments(Object.fromEntries(assignments.map(item => [item.shot_code, {
      keyframe: item.keyframe_workflow_slot_version_id ?? '',
      video: item.video_workflow_slot_version_id,
    }])))
    analyzeImpact.reset()
  }, [configId, videoSpecId, preparation.data?.production_plan_candidates])
  if (project.isPending || planning.isPending) return <div className={styles.loading}>正在读取方案合同…</div>
  if (!project.data || !planning.data || project.error || planning.error) return <div className={styles.loading}>方案读取失败：{project.error?.message || planning.error?.message}</div>
  const data = planning.data
  const brief = data.current_brief_candidate ?? data.accepted_brief_candidate
  const rejectedBrief = data.brief_history.find(item => item.requirement_version_id === data.active_requirement.id && item.status === 'rejected') ?? null
  const revisableBrief = data.current_brief_candidate ?? rejectedBrief
  const hasOpenBriefQuestions = Boolean(
    data.current_brief_candidate
    && (data.current_brief_candidate.brief.open_questions.length > 0
      || data.current_brief_candidate.brief.facts_requiring_confirmation.length > 0),
  )
  const rejectedShot = data.shot_plan_history.find(item => item.requirement_version_id === data.active_requirement.id && item.status === 'rejected') ?? null
  const editableShot = data.current_shot_candidate ?? data.revision_draft ?? rejectedShot
  const shots = editableShot?.shots ?? data.active_plan?.shots ?? []
  const characterVersions = data.entity_versions.filter(item => item.entity_type === 'character')
  const error = generateBrief.error || retryBrief.error || reviseBrief.error || reviseRequirement.error || decideBrief.error || generateShots.error || retryShots.error || uploadCharacter.error || registerExistingCharacter.error || startShotRevision.error || reviseShots.error || reviseShotsWithDirector.error || decideShots.error || cancelRevision.error || cancelManualRevision.error || generateProductionPlan.error || retryProductionPlan.error || decideProductionPlan.error || preparation.error || analyzeImpact.error || createSnapshot.error || lockSnapshot.error || activateSnapshot.error || submitProduction.error
  const errorStage = generateProductionPlan.error || retryProductionPlan.error || decideProductionPlan.error
    ? '制作规划失败'
    : generateBrief.error || retryBrief.error || reviseBrief.error || reviseRequirement.error || decideBrief.error
      ? '内容策划操作失败'
      : generateShots.error || retryShots.error || startShotRevision.error || reviseShots.error || reviseShotsWithDirector.error || decideShots.error || cancelRevision.error || cancelManualRevision.error
        ? '分镜操作失败'
        : uploadCharacter.error || registerExistingCharacter.error
          ? '人物参考登记失败'
          : '制作设置操作失败'
  const selectedConfig = preparation.data?.published_configurations.find(item => item.id === configId)
  const keyframeSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'image_generation') ?? []
  const videoSlots = selectedConfig?.workflow_slots.filter(item => ['video_generation', 'multi_frame_video_generation', 'text_to_video_generation'].includes(item.operation_kind)) ?? []
  const ttsSlots = selectedConfig?.workflow_slots.filter(item => item.operation_kind === 'tts') ?? []
  const selectedVideoSpec = selectedConfig?.video_specs.find(item => item.id === videoSpecId)
  const selectedTtsSlot = ttsSlots.find(item => item.id === ttsSlotId)
  const productionPlanCandidate = preparation.data?.production_plan_candidates.find(item =>
    item.production_config_version_id === configId
    && item.video_spec_version_id === videoSpecId
    && ['awaiting_review', 'accepted'].includes(item.status),
  )
  const productionPlannerFailed = preparation.data?.latest_production_planner_run?.status === 'failed'
    && preparation.data.latest_production_planner_run.production_config_version_id === configId
  const impact = analyzeImpact.data
  const savedMatchingSnapshot = impact
    ? preparation.data?.snapshots.find(snapshot =>
        snapshot.status !== 'superseded'
        && snapshot.contract_hash === impact.snapshot_contract_hash
      )
    : undefined
  const impactShotCodes = impact?.manifest.shots.map(shot => shot.shot_code) ?? []
  const groupedValidationIssues = impact ? groupValidationIssues(impact.validation_errors, impactShotCodes) : []
  const allShotsAssigned = Boolean(data.active_plan?.shots.length) && data.active_plan!.shots.every(shot => {
    const assignment = shotWorkflowAssignments[shot.shot_code]
    const video = videoSlots.find(item => item.id === assignment?.video)
    return Boolean(video && (video.operation_kind === 'text_to_video_generation' || assignment?.keyframe))
  })
  const canAnalyze = Boolean(data.active_plan && configId && videoSpecId && allShotsAssigned && (preparation.data?.audio_mode !== 'voiceover' || ttsSlotId))
  const nextAction = data.active_plan && preparation.data ? preparation.data.next_action : data.next_action
  const nextActionLabel = productionActionLabels[nextAction.code] ?? nextAction.label
  function chooseConfig(value: string) { setConfigId(value); setVideoSpecId(''); setKeyframeSlotId(''); setVideoSlotId(''); setShotWorkflowAssignments({}); setTtsSlotId(''); setPricingCatalogId(''); generateProductionPlan.reset(); retryProductionPlan.reset(); analyzeImpact.reset() }
  function assignShot(shotCode: string, field: 'keyframe' | 'video', value: string) {
    setShotWorkflowAssignments(current => {
      const next = { ...(current[shotCode] ?? { keyframe: '', video: '' }), [field]: value }
      const video = videoSlots.find(item => item.id === next.video)
      if (video?.operation_kind === 'text_to_video_generation') next.keyframe = ''
      return { ...current, [shotCode]: next }
    })
    analyzeImpact.reset()
  }
  function applyWorkflowPairToAllShots() {
    const video = videoSlots.find(item => item.id === videoSlotId)
    if (!video || (video.operation_kind !== 'text_to_video_generation' && !keyframeSlotId)) return
    setShotWorkflowAssignments(Object.fromEntries(data.active_plan!.shots.map(shot => [shot.shot_code, {
      keyframe: video.operation_kind === 'text_to_video_generation' ? '' : keyframeSlotId,
      video: videoSlotId,
    }])))
    analyzeImpact.reset()
  }
  return <>
    <PageHeader eyebrow="PLAN REVIEW" title={`${project.data.title} · 方案确认`} description="内容策划智能体与分镜导演智能体只提交候选，用户确认后才创建不可变方案版本。" actions={<Link className="secondaryButton" to={`/projects/${projectId}`}><ArrowLeft size={15} />返回创作中心</Link>} />
    <div className={styles.versionBar}><span><GitBranch size={15} />需求 v{data.active_requirement.version_number}</span><i></i><span data-active={Boolean(brief)}><Sparkles size={15} />{brief ? '内容方案' : '内容方案待生成'}</span><i></i><span data-active={Boolean(data.active_plan)}><LockKeyhole size={15} />{data.active_plan ? `创作方案 v${data.active_plan.version_number}` : '创作方案尚未创建'}</span><b>{nextActionLabel}</b></div>
    {error && <DismissibleErrorAlert stage={errorStage} message={error.message} />}
    <main className={styles.layout} data-editing-shots={editingShots}>
      <section className={styles.main}>
        <div className={styles.briefPanel}>
          <div className={styles.panelHeading}><div><Layers3 size={18} /><span><small>CREATIVE BRIEF</small><h2>{brief ? '内容方案候选' : '基于已确认需求生成方案'}</h2></span></div>{brief && <em data-accepted={brief.status === 'accepted'}>{brief.status === 'accepted' ? '已接受' : '尚未生效'}</em>}</div>
          {brief ? <BriefView candidate={brief} /> : ['RETRY_FAILED_CREATIVE_BRIEF', 'REGENERATE_CREATIVE_BRIEF_WITH_CURRENT_CONTRACT'].includes(data.next_action.code) && data.latest_planner_run ? <div className={styles.empty}><CircleAlert size={24} /><strong>本次内容策划失败</strong><span>{plannerFailureMessage(data.latest_planner_run.error_code, data.latest_planner_run.error_detail)} 系统没有自动重试、修补输出或更换模型。</span>{data.next_action.code === 'REGENERATE_CREATIVE_BRIEF_WITH_CURRENT_CONTRACT' ? <button className="primaryButton" disabled={regenerateBrief.isPending} onClick={() => regenerateBrief.mutate()}>{regenerateBrief.isPending ? '正在重新生成…' : '确认费用并使用当前合同重新生成'}</button> : <button className="primaryButton" disabled={retryBrief.isPending} onClick={() => retryBrief.mutate()}>{retryBrief.isPending ? '正在重跑…' : '确认模型调用并精确重跑'}</button>}</div> : data.next_action.code === 'WAIT_FOR_CREATIVE_BRIEF' ? <div className={styles.empty}><Sparkles size={24} /><strong>内容策划正在生成方案</strong><span>正在等待当前模型调用返回，期间不会再次提交相同需求。</span><button className="primaryButton" disabled>正在生成…</button></div> : data.next_action.code === 'REVISE_REQUIREMENT_FOR_NEW_BRIEF' ? <div className={styles.empty}><CircleAlert size={24} /><strong>当前内容方案已被拒绝</strong><span>小改内容结构可以直接生成方案修订版；受众、时长、音频或整体方向变化再修改创作需求。</span><div className={styles.emptyActions}><button className="primaryButton" onClick={() => setEditingBrief(true)} disabled={!rejectedBrief}><Pencil size={14} />微调当前方案</button><Link className="secondaryButton" to={`/projects/${projectId}`}>修改创作需求</Link></div></div> : <div className={styles.empty}><Sparkles size={24} /><strong>当前需求可以进入内容策划</strong><span>内容策划智能体将读取已确认需求并生成一份待审核方案，本次操作会调用已配置模型。</span><button className="primaryButton" disabled={generateBrief.isPending} onClick={() => { retryBrief.reset(); generateBrief.mutate() }}>{generateBrief.isPending ? '正在策划…' : '生成内容方案候选'}</button></div>}
          {data.current_brief_candidate?.brief.open_questions.length ? <BriefQuestionResolver candidate={data.current_brief_candidate} answers={briefQuestionAnswers} busy={reviseBrief.isPending} onAnswer={(questionCode, answer) => setBriefQuestionAnswers(current => ({ ...current, [questionCode]: answer }))} onResolve={questions => reviseBrief.mutate({ candidateId: data.current_brief_candidate!.id, instruction: ['请根据以下用户对待确认项的逐项回答修订当前内容方案。已回答的问题及其关联的待确认事实不再保留；如果回答仍不足，只保留确实无法执行的剩余问题和事实。不要改变已确认的基础需求。', ...questions.map(question => `${question.question_code} ${question.prompt}\n用户回答：${briefQuestionAnswers[question.question_code].trim()}`)].join('\n\n') })} /> : null}
          {editingBrief && revisableBrief && <form className={styles.briefRevision} onSubmit={event => { event.preventDefault(); reviseBrief.mutate({ candidateId: revisableBrief.id, instruction: briefRevisionInstruction.trim() }) }}><div><strong>调整内容方案 v{revisableBrief.revision_number}</strong><span>只描述希望改变的内容结构、开场、节奏或文案重点。基础需求不会改变，本次会调用一次当前内容策划模型。</span></div><textarea autoFocus rows={4} value={briefRevisionInstruction} onChange={event => setBriefRevisionInstruction(event.target.value)} placeholder="例如：开头更快进入结果，减少过程说明，重点突出第一天和第七天的变化。" /><footer><button type="button" className="secondaryButton" onClick={() => { setEditingBrief(false); setBriefRevisionInstruction('') }}>取消</button><button className="primaryButton" disabled={!briefRevisionInstruction.trim() || reviseBrief.isPending}>{reviseBrief.isPending ? '正在生成修订版…' : '确认模型调用并生成修订版'}</button></footer></form>}
          {brief && ['RETRY_FAILED_CREATIVE_BRIEF', 'REGENERATE_CREATIVE_BRIEF_WITH_CURRENT_CONTRACT'].includes(data.next_action.code) && data.latest_planner_run && <div className={styles.revisionFailure}><CircleAlert size={17} /><div><strong>方案调整没有成功</strong><span>{plannerFailureMessage(data.latest_planner_run.error_code, data.latest_planner_run.error_detail)} 原方案仍然保留。</span></div>{data.next_action.code === 'REGENERATE_CREATIVE_BRIEF_WITH_CURRENT_CONTRACT' ? <button className="primaryButton" disabled={regenerateBrief.isPending} onClick={() => regenerateBrief.mutate()}>{regenerateBrief.isPending ? '正在重新生成…' : '确认费用并使用当前合同重新生成'}</button> : <button className="primaryButton" disabled={retryBrief.isPending} onClick={() => retryBrief.mutate()}>{retryBrief.isPending ? '正在重跑…' : '确认费用并精确重跑'}</button>}</div>}
          {data.current_brief_candidate && <div className={styles.reviewBar}><p><strong>内容方案 v{data.current_brief_candidate.revision_number} 尚未生效</strong><span>{hasOpenBriefQuestions ? '请先完成上方待确认项，再接受方案。' : '可以微调方案；接受后分镜导演才能读取。修改基础需求会放弃当前方案。'}</span></p><button className="secondaryButton" onClick={() => setEditingBrief(value => !value)} disabled={reviseBrief.isPending}><Pencil size={14} />{editingBrief ? '收起调整' : '调整方案'}</button><button className="secondaryButton" onClick={() => reviseRequirement.mutate(data.current_brief_candidate!.id)} disabled={reviseRequirement.isPending}>修改创作需求</button><button className="secondaryButton" onClick={() => decideBrief.mutate(false)} disabled={decideBrief.isPending}><X size={14} />放弃方案</button><button className="primaryButton" onClick={() => decideBrief.mutate(true)} disabled={decideBrief.isPending || hasOpenBriefQuestions}><Check size={14} />接受方案</button></div>}
        </div>
        {shots.length ? <>
          {brief && <CoverageView brief={brief.brief} shots={shots} />}
          <ShotTable shots={shots} locked={Boolean(data.active_plan)} />
          {data.active_plan && !data.current_shot_candidate && !data.revision_draft && <section className={styles.characterBinding}>
            <div className={styles.characterBindingIntro}><Users size={19} /><div><span>人物与镜头是两步确认</span><strong>{characterVersions.length ? `已有 ${characterVersions.length} 个人物版本可用` : '先上传一张清晰的人物参考图'}</strong><p>上传只会登记人物版本；进入调整后，再由你逐个镜头选择人物、主参考图和人脸要求。系统不会自动套用到全部镜头。</p></div></div>
            <div className={styles.characterBindingActions}>
              <button type="button" className="secondaryButton" disabled={uploadCharacter.isPending || registerExistingCharacter.isPending || startShotRevision.isPending} onClick={() => characterFileInput.current?.click()}><FileImage size={15} />{uploadCharacter.isPending ? (characterUploadStatus?.phase === 'uploading' ? '正在上传…' : '正在登记人物…') : characterVersions.length ? '上传新的人物参考图' : '上传人物参考图'}</button>
              <input ref={characterFileInput} hidden type="file" accept="image/png,image/jpeg,image/webp" onChange={event => { const file = event.target.files?.[0]; if (file) uploadCharacter.mutate(file); event.target.value = '' }} />
              <button type="button" className="primaryButton" disabled={!characterVersions.length || startShotRevision.isPending || uploadCharacter.isPending || registerExistingCharacter.isPending} onClick={() => startShotRevision.mutate()}><Pencil size={15} />{startShotRevision.isPending ? '正在创建调整草稿…' : '绑定人物到具体镜头'}</button>
            </div>
            {characterUploadStatus && <div className={styles.characterBindingStatus} data-error={characterUploadStatus.phase === 'failed_after_upload'}>
              <strong>{characterUploadStatus.phase === 'uploading' ? `正在上传：${characterUploadStatus.filename}` : characterUploadStatus.phase === 'uploaded' ? `文件已上传：${characterUploadStatus.filename}` : characterUploadStatus.phase === 'binding' ? `正在登记人物：${characterUploadStatus.filename}` : characterUploadStatus.phase === 'succeeded' ? `已上传并登记人物参考：${characterUploadStatus.filename}` : `文件已上传，但人物登记失败：${characterUploadStatus.filename}`}</strong>
              {characterUploadStatus.message && <span>{characterUploadStatus.message}</span>}
            </div>}
            {creation.data?.attachments.filter(attachment => attachment.verification_status === 'verified' && attachment.mime_type.startsWith('image/') && !attachment.bindings.some(binding => binding.status === 'confirmed')).map(attachment => <div className={styles.unboundCharacter} key={attachment.id}>
              <div><span>已上传，尚未登记</span><strong>{attachment.original_filename}</strong></div>
              <button type="button" className="secondaryButton" disabled={uploadCharacter.isPending || registerExistingCharacter.isPending} onClick={() => registerExistingCharacter.mutate(attachment)}>{registerExistingCharacter.isPending && characterUploadStatus?.attachmentId === attachment.id ? '正在登记…' : '登记为新人物'}</button>
            </div>)}
          </section>}
          {data.revision_context && <section className={styles.assetRevisionContext}><CircleAlert size={18} /><div><span>成品反馈返回分镜</span><h3>{data.revision_context.shot_code} 需要调整</h3><p>{assetRevisionSummary(data.revision_context)}</p><small>来源素材 {data.revision_context.asset_id.slice(-10)} · 原方案 v{data.plan_history.find(plan => plan.id === data.revision_context?.plan_version_id)?.version_number ?? ''} · 下游影响 {data.revision_context.affected_downstream_node_keys.length} 项</small></div></section>}
          {editableShot && editingShots && <ShotPlanRevisionEditor projectId={projectId} candidate={editableShot} entities={data.entity_versions} scriptSegments={brief?.brief.script_segments ?? []} saving={reviseShots.isPending} aiSaving={reviseShotsWithDirector.isPending} initialShotCode={data.revision_context?.shot_code} onCancel={() => setEditingShots(false)} onSubmit={patches => reviseShots.mutate(patches)} onAIRevision={(selected, instruction) => reviseShotsWithDirector.mutate({ selected, instruction })} />}
          {data.revision_draft && <div className={styles.reviewBar}><p><strong>这是分镜调整草稿，尚不能确认</strong><span>{data.revision_context ? '请调整反馈对应的镜头。' : '逐镜头选择人物、主参考图和人脸要求；旧方案不会被改动。'}</span></p><button className="secondaryButton" disabled={cancelManualRevision.isPending} onClick={() => data.revision_context ? setConfirmCancelRevision(true) : cancelManualRevision.mutate()}>{data.revision_context ? '放弃本次回改' : '放弃调整草稿'}</button><button className="primaryButton" onClick={() => setEditingShots(true)} disabled={editingShots}><Pencil size={14} />{data.revision_context ? '调整对应分镜' : '继续绑定人物'}</button></div>}
          {data.current_shot_candidate && <div className={styles.reviewBar}><p><strong>分镜候选 v{data.current_shot_candidate.revision_number} 尚未生效</strong><span>确认后创建不可变 plan_v{data.plan_history.length + 1}。</span></p><button className="secondaryButton" onClick={() => setEditingShots(value => !value)} disabled={reviseShots.isPending}><Pencil size={14} />{editingShots ? '收起编辑' : '结构化修订'}</button><button className="secondaryButton" onClick={() => decideShots.mutate(false)} disabled={decideShots.isPending || editingShots}><X size={14} />拒绝</button><button className="primaryButton" onClick={() => decideShots.mutate(true)} disabled={decideShots.isPending || editingShots}><Check size={14} />确认分镜合同</button></div>}
          {rejectedShot && <div className={styles.reviewBar}><p><strong>分镜方案已拒绝</strong><span>原方案仍保留供查看。选择具体镜头调整后会创建新的待确认版本，不会再次调用模型。</span></p><button className="primaryButton" onClick={() => setEditingShots(value => !value)} disabled={reviseShots.isPending}><Pencil size={14} />{editingShots ? '收起调整' : '调整具体分镜'}</button></div>}
        </> : data.accepted_brief_candidate && <div className={styles.generateShots}><Clapperboard size={22} /><div><strong>{data.next_action.code === 'RETRY_FAILED_SHOT_PLAN' ? '本次分镜生成失败' : '内容方案已接受'}</strong><span>{data.next_action.code === 'RETRY_FAILED_SHOT_PLAN' ? `${data.latest_director_run?.error_detail || '模型没有返回符合分镜合同的结果。'} 系统没有自动重试，也没有更换模型。` : '分镜导演将生成结构化分镜候选，不选择服务供应商或工作流。'}</span></div>{data.next_action.code === 'RETRY_FAILED_SHOT_PLAN' ? <button className="primaryButton" onClick={() => retryShots.mutate()} disabled={retryShots.isPending}>{retryShots.isPending ? '正在重跑…' : '确认模型调用并重跑'}</button> : data.next_action.code === 'WAIT_FOR_SHOT_PLAN' ? <button className="primaryButton" disabled>正在生成…</button> : <button className="primaryButton" onClick={() => generateShots.mutate()} disabled={generateShots.isPending}>{generateShots.isPending ? '正在生成…' : '生成分镜候选'}</button>}</div>}
        {data.active_plan && !data.revision_context && !data.revision_draft && !data.current_shot_candidate && <section className={styles.productionPrep}>
          <div className={styles.panelHeading}><div><Network size={18} /><span><small>制作准备</small><h2>制作设置与费用预估</h2></span></div><em data-accepted={Boolean(preparation.data?.snapshots.length)}>{preparation.data?.snapshots.length ? `制作方案 ${preparation.data.snapshots[0].snapshot_number}` : '尚未保存'}</em></div>
          {preparation.data?.published_configurations.length ? <>
            <div className={styles.routeGrid}>
              <label>制作配置<select value={configId} onChange={event => chooseConfig(event.target.value)}><option value="">请选择配置</option>{preparation.data.published_configurations.map(item => <option key={item.id} value={item.id}>{item.display_name} · v{item.version_number}</option>)}</select></label>
              <label>画面规格<select disabled={!selectedConfig} value={videoSpecId} onChange={event => { setVideoSpecId(event.target.value); setShotWorkflowAssignments({}); generateProductionPlan.reset(); retryProductionPlan.reset(); analyzeImpact.reset() }}><option value="">请选择画面规格</option>{selectedConfig?.video_specs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.width}×{item.height} · {item.fps}fps</option>)}</select></label>
              {preparation.data.audio_mode === 'voiceover' && <label>配音生成方案<select disabled={!selectedConfig} value={ttsSlotId} onChange={event => { setTtsSlotId(event.target.value); analyzeImpact.reset() }}><option value="">请选择配音生成方案</option>{ttsSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>}
              <label>计费方案<select disabled={!selectedConfig} value={pricingCatalogId} onChange={event => { setPricingCatalogId(event.target.value); analyzeImpact.reset() }}><option value="">暂不选择（只能预览，不能开始制作）</option>{selectedConfig?.pricing_catalogs.map(item => <option key={item.id} value={item.id}>{item.display_name} · {item.currency}</option>)}</select></label>
            </div>
            <section className={styles.productionPlanner}>
              <header><div><Sparkles size={18} /><span><small>制作规划智能体</small><strong>{productionPlanCandidate?.status === 'accepted' ? '当前路线已采用' : productionPlanCandidate ? '逐镜头路线等待确认' : productionPlannerFailed ? '本次制作规划失败' : '让智能体匹配现有工作流'}</strong></span></div>{productionPlanCandidate && <em data-accepted={productionPlanCandidate.status === 'accepted'}>{productionPlanCandidate.status === 'accepted' ? '已采用' : '待确认'}</em>}</header>
              {productionPlanCandidate ? <>
                <p>{productionPlanCandidate.status === 'accepted' ? '已采用的选择已填入下方。你仍可手动调整；最终以费用预估时提交的路线为准。' : '智能体只提出候选。请检查理由并按需要修改下方选择，再明确采用。'}</p>
                {productionPlanCandidate.status === 'awaiting_review' && <footer><button className="secondaryButton" disabled={decideProductionPlan.isPending} onClick={() => decideProductionPlan.mutate({ candidateId: productionPlanCandidate.id, accept: false })}><X size={14} />拒绝候选</button><button className="primaryButton" disabled={!allShotsAssigned || decideProductionPlan.isPending} onClick={() => decideProductionPlan.mutate({ candidateId: productionPlanCandidate.id, accept: true })}><Check size={14} />采用当前逐镜头路线</button></footer>}
              </> : productionPlannerFailed ? <><p>{preparation.data?.latest_production_planner_run?.error_detail || '模型没有返回符合制作路线合同的结果。'} 系统没有自动重试，也没有更换模型或工作流。</p><footer><button className="primaryButton" disabled={retryProductionPlan.isPending} onClick={() => retryProductionPlan.mutate()}>{retryProductionPlan.isPending ? '正在重跑…' : '确认模型调用并精确重跑'}</button></footer></> : <><p>先选择制作配置和画面规格。智能体会读取每个镜头的参考图、一致性、文字与运动要求，在当前工作流库中提出路线。</p><footer><button className="primaryButton" disabled={!configId || !videoSpecId || generateProductionPlan.isPending} onClick={() => generateProductionPlan.mutate()}><Sparkles size={14} />{generateProductionPlan.isPending ? '正在规划…' : '智能规划制作路线'}</button></footer></>}
            </section>
            <section className={styles.workflowAssignment} id="shot-workflow-assignment">
              <header><div><strong>逐镜头制作方案</strong><span>每个镜头都必须明确选择。系统不会根据文字猜测、自动替换或沿用其他镜头。</span></div><b>{Object.values(shotWorkflowAssignments).filter(item => item.video).length} / {data.active_plan.shots.length}</b></header>
              <div className={styles.batchAssignment}>
                <label>批量图片方案<select disabled={!selectedConfig || videoSlots.find(item => item.id === videoSlotId)?.operation_kind === 'text_to_video_generation'} value={keyframeSlotId} onChange={event => setKeyframeSlotId(event.target.value)}><option value="">请选择</option>{keyframeSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
                <label>批量视频方案<select disabled={!selectedConfig} value={videoSlotId} onChange={event => setVideoSlotId(event.target.value)}><option value="">请选择</option>{videoSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
                <button type="button" className="secondaryButton" disabled={!videoSlotId || (videoSlots.find(item => item.id === videoSlotId)?.operation_kind !== 'text_to_video_generation' && !keyframeSlotId)} onClick={applyWorkflowPairToAllShots}>明确应用到全部镜头</button>
              </div>
              <div className={styles.assignmentList}>{data.active_plan.shots.map(shot => {
                const assignment = shotWorkflowAssignments[shot.shot_code] ?? { keyframe: '', video: '' }
                const selectedVideo = videoSlots.find(item => item.id === assignment.video)
                const textToVideo = selectedVideo?.operation_kind === 'text_to_video_generation'
                const proposal = productionPlanCandidate?.proposed_assignments.find(item => item.shot_code === shot.shot_code)
                return <article key={shot.shot_code}>
                  <div><b>{shot.shot_code}</b><ShotFrameContract shot={shot} compact /><small>{proposal?.reason ?? `${shot.duration_ms / 1000} 秒 · ${shot.generation_requirements.identity_consistency_required ? '需要人物一致性' : '普通镜头'}${shot.generation_requirements.reference_image_required ? ' · 需要参考图' : ''}`}</small></div>
                  <label>图片方案<select value={assignment.keyframe} disabled={!selectedConfig || textToVideo} onChange={event => assignShot(shot.shot_code, 'keyframe', event.target.value)}><option value="">{textToVideo ? '纯文本视频不使用关键帧' : '请选择图片方案'}</option>{keyframeSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
                  <label>视频方案<select value={assignment.video} disabled={!selectedConfig} onChange={event => assignShot(shot.shot_code, 'video', event.target.value)}><option value="">请选择视频方案</option>{videoSlots.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
                </article>
              })}</div>
            </section>
            <div className={styles.analysisAction}><div><ShieldCheck size={17} /><p><strong>所有生成方式都由你选择</strong><span>系统只按当前选择计算制作步骤，不会自动更换生成方案，也不会在这里开始生成。</span></p></div><button className="primaryButton" disabled={!canAnalyze || analyzeImpact.isPending} onClick={() => analyzeImpact.mutate()}>{analyzeImpact.isPending ? '正在计算…' : '查看制作计划'}</button></div>
            {selectedConfig && <details className={styles.technicalDetails}><summary>查看当前选择的技术详情</summary><dl><div><dt>配置版本</dt><dd><code>{selectedConfig.id}</code></dd></div>{selectedVideoSpec && <div><dt>画面规格</dt><dd><code>{selectedVideoSpec.id}</code></dd></div>}<div><dt>逐镜头路由</dt><dd>{Object.values(shotWorkflowAssignments).filter(item => item.video).length} 个镜头已明确选择</dd></div>{selectedTtsSlot && <div><dt>配音生成</dt><dd><code>{selectedTtsSlot.key}</code></dd></div>}</dl></details>}
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
              <div className={styles.issueSummaryHeading}><div><CircleAlert size={16} /><p><strong>{groupedValidationIssues.length} 项制作要求尚未满足</strong><span>相同原因已合并；受影响镜头仍按后端校验结果精确列出。</span></p></div><button type="button" className="secondaryButton" onClick={() => document.getElementById('shot-workflow-assignment')?.scrollIntoView({ behavior: 'smooth' })}>检查逐镜头方案</button></div>
              {groupedValidationIssues.map(group => <article key={group.code}><div><b>{userIssue(group.code)}</b><span>{group.shotCodes.length ? `影响 ${group.shotCodes.length} 个镜头` : `共 ${group.items.length} 条校验结果`}</span></div>{group.shotCodes.length > 0 && <p>{group.shotCodes.map(code => <em key={code}>{code}</em>)}</p>}</article>)}
            </section>}
            {impact.execution_blockers.map(item => <article className={styles.costBlocker} key={item.code}><b>还不能开始制作</b><span>{userIssue(item.code)}</span></article>)}
            <details className={styles.technicalDetails}><summary>查看本次计算的技术详情</summary><dl><div><dt>分析编号</dt><dd><code>{impact.analysis_hash}</code></dd></div><div><dt>内部状态</dt><dd><code>{impact.status}</code></dd></div>{[...impact.validation_errors, ...impact.execution_blockers].map(item => <div key={`technical:${item.code}:${'path' in item ? item.path : ''}`}><dt>{item.code}</dt><dd>{item.message ?? ('path' in item ? item.path : '')}</dd></div>)}</dl></details>
            {impact.status === 'awaiting_confirmation' && <footer>{savedMatchingSnapshot ? <p><strong>相同制作方案已保存</strong><span>当前计划与制作方案 {savedMatchingSnapshot.snapshot_number} 完全相同，无需重复保存。</span></p> : <><p><strong>确认后保存一份不可修改的制作方案</strong><span>这里只保存本次选择，不会开始生成，也不会产生费用。</span></p><button className="primaryButton" disabled={createSnapshot.isPending} onClick={() => createSnapshot.mutate()}><LockKeyhole size={14} />保存本次制作方案</button></>}</footer>}
          </div>}
          {preparation.data?.snapshots.map(snapshot => {
            const isCurrent = snapshot.id === currentSnapshot?.id
            return <div className={styles.snapshotRow} key={snapshot.id}><LockKeyhole size={17} /><div><strong>制作方案 {snapshot.snapshot_number} · {snapshotStatusLabel(snapshot.status, snapshot.cost_status)}</strong><span>{snapshot.nodes.length} 个制作步骤 · 预计调用生成服务 {snapshot.estimated_call_count} 次 · {costLabel(snapshot.estimated_cost, snapshot.currency)}</span><details className={styles.technicalDetails}><summary>技术详情</summary><dl><div><dt>内部状态</dt><dd><code>{snapshot.status}</code></dd></div><div><dt>步骤与依赖</dt><dd>{snapshot.nodes.length} 个节点 · {snapshot.edges.length} 条依赖</dd></div><div><dt>合同校验码</dt><dd><code>{snapshot.contract_hash}</code></dd></div></dl></details></div>{isCurrent && snapshot.status === 'preparing' && snapshot.cost_status === 'estimated' ? <button className="primaryButton" onClick={() => setConfirmCost(true)}>确认费用并锁定方案</button> : isCurrent && snapshot.status === 'locked' ? <button className="primaryButton" disabled={activateSnapshot.isPending} onClick={() => activateSnapshot.mutate()}>设为当前制作方案</button> : isCurrent && snapshot.status === 'active' ? <button className="primaryButton" onClick={() => setConfirmSubmit(true)}>开始制作</button> : isCurrent && (snapshot.status === 'submitted' || snapshot.status.startsWith('execution_')) ? <Link className="secondaryButton" to={`/production?project=${projectId}`}>查看制作进度</Link> : <em>{snapshotStatusLabel(snapshot.status, snapshot.cost_status)}</em>}</div>
          })}
        </section>}
      </section>
      <aside className={styles.aside}>
        <section className={styles.next}><span>下一步</span><h3>{nextActionLabel}</h3><p>模型调用：{'incurs_model_cost' in nextAction && nextAction.incurs_model_cost ? '是' : '否'} · 制作费用：{nextAction.incurs_production_cost ? '是' : '否'}</p></section>
        <section className={styles.entities}><div className={styles.asideTitle}><div><Users size={17} /><h3>已确认实体版本</h3></div><b>{data.entity_versions.length}</b></div>{data.entity_versions.length ? data.entity_versions.map(entity => <article key={entity.id}><BadgeCheck size={16} /><div><strong>{entity.display_name}</strong><span>{entity.entity_type} · v{entity.version_number}</span><small>{entity.id}</small></div></article>) : <div className={styles.asideEmpty}>当前没有实体绑定；分镜会明确显示未绑定，不创建隐式人物或场景。</div>}</section>
        {data.brief_history.length > 0 && <section className={styles.candidateHistory}><div className={styles.asideTitle}><div><GitBranch size={17} /><h3>内容方案版本</h3></div><b>{data.brief_history.length}</b></div>{data.brief_history.map(candidate => <article key={candidate.id} data-current={candidate.id === data.current_brief_candidate?.id}><div><strong>方案 v{candidate.revision_number}</strong><span>{candidate.source === 'planner_revision' ? '按意见调整' : '首次策划'} · {candidate.status}</span></div><small>{candidate.id.slice(-10)}</small></article>)}</section>}
        {data.shot_plan_history.length > 0 && <section className={styles.candidateHistory}><div className={styles.asideTitle}><div><GitBranch size={17} /><h3>分镜候选版本</h3></div><b>{data.shot_plan_history.length}</b></div>{data.shot_plan_history.map(candidate => <article key={candidate.id} data-current={candidate.id === data.current_shot_candidate?.id || candidate.id === data.revision_draft?.id}><div><strong>候选 v{candidate.revision_number}</strong><span>{candidate.source === 'manual_revision_draft' ? '手动调整草稿' : candidate.source === 'asset_feedback_draft' ? '成品反馈草稿' : candidate.source === 'user_revision' ? '用户修订' : '分镜导演智能体生成'} · {candidate.status}</span></div><small>{candidate.id.slice(-10)}</small></article>)}</section>}
        <section className={styles.boundary}><CircleAlert size={17} /><div><strong>确认边界</strong><p>{data.active_plan ? '当前创作方案已确认。后续修改需要创建新的需求和方案版本。' : '当前操作只确认创作方案，不会创建制作任务。'}</p></div></section>
        {data.active_plan && <section className={styles.planState}><LockKeyhole size={18} /><div><strong>{currentSnapshot ? `制作方案 ${currentSnapshot.snapshot_number} · ${snapshotStatusLabel(currentSnapshot.status, currentSnapshot.cost_status)}` : `创作方案 v${data.active_plan.version_number}`}</strong><span>{currentSnapshot ? `${currentSnapshot.nodes.length} 个制作步骤 · ${costLabel(currentSnapshot.estimated_cost, currentSnapshot.currency)}` : `${data.active_plan.shots.length} 个镜头 · 尚未保存制作方案`}</span></div></section>}
      </aside>
    </main>
    {confirmCost && currentSnapshot && <div className={styles.costModal}><section><header><Calculator size={20} /><div><span>费用确认</span><h2>确认制作方案 {currentSnapshot.snapshot_number} 的预计费用</h2></div></header><div className={styles.costAmount}><small>预计制作费用</small><strong>{currentSnapshot.currency} {currentSnapshot.estimated_cost?.toFixed(6)}</strong><span>预计调用生成服务 {currentSnapshot.estimated_call_count} 次</span></div><p>确认后将锁定本次制作内容、计费方案和各步骤费用。本操作不会开始生成，也不会实际扣费。</p><details className={styles.technicalDetails}><summary>查看技术详情</summary><code>{currentSnapshot.contract_hash}</code></details><footer><button className="secondaryButton" onClick={() => setConfirmCost(false)}>取消</button><button className="primaryButton" disabled={lockSnapshot.isPending} onClick={() => lockSnapshot.mutate()}><LockKeyhole size={14} />确认费用并锁定方案</button></footer></section></div>}
    {confirmSubmit && currentSnapshot && <div className={styles.costModal}><section><header><Network size={20} /><div><span>开始制作确认</span><h2>开始制作方案 {currentSnapshot.snapshot_number}</h2></div></header><div className={styles.costAmount}><small>已确认预计费用</small><strong>{currentSnapshot.currency} {currentSnapshot.estimated_cost?.toFixed(6)}</strong><span>{currentSnapshot.nodes.length} 个制作步骤 · 预计调用生成服务 {currentSnapshot.estimated_call_count} 次</span></div><p>确认后系统将按当前方案创建制作任务并进入队列。系统不会补步骤、更换生成方案或自动重试。</p><details className={styles.technicalDetails}><summary>查看技术详情</summary><code>{currentSnapshot.contract_hash}</code></details><footer><button className="secondaryButton" onClick={() => setConfirmSubmit(false)}>取消</button><button className="primaryButton" disabled={submitProduction.isPending} onClick={() => submitProduction.mutate()}><ShieldCheck size={14} />确认并开始制作</button></footer></section></div>}
    {confirmCancelRevision && data.revision_context && <div className={styles.costModal}><section><header><CircleAlert size={20} /><div><span>放弃回改确认</span><h2>放弃 {data.revision_context.shot_code} 的本次调整</h2></div></header><p>本次回改草稿或待审候选将标记为已取消。当前正式方案、制作快照和已有素材都不会改变。</p><footer><button className="secondaryButton" onClick={() => setConfirmCancelRevision(false)}>继续调整</button><button className="primaryButton" disabled={cancelRevision.isPending} onClick={() => cancelRevision.mutate()}>确认放弃</button></footer></section></div>}
  </>
}
