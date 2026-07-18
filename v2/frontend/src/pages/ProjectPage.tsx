import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Bot, Check, CheckCircle2, ChevronDown, CircleAlert, Clock3, FileAudio, FileImage, History, Link2, MessageSquarePlus, MessageSquareText, Paperclip, Send, ShieldCheck, Sparkles, X } from 'lucide-react'
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { AgentRun, CreationAttachment } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { useWorkspace } from '../store/workspace'
import styles from './ProjectPage.module.css'

const sourceLabels: Record<string, string> = {
  user: '用户输入',
  agent_proposal: '创作助手建议',
  user_selection: '你选择的建议',
  declared_default: '已声明默认值',
  template: '模板',
}

const fieldLabels: Record<string, string> = {
  title: '项目名称', core_topic: '核心主题', duration_seconds: '目标时长',
  aspect_ratio: '画幅', audio_mode: '音频模式', creative_direction: '创作方向',
  content_goal: '内容目标', platform: '发布平台', target_audience: '目标受众',
  visual_style: '视觉风格', tone: '情绪基调', content_structure: '内容结构',
  call_to_action: '结尾行动', creative_constraints: '创作限制',
}

const agentRoleLabels: Record<string, string> = {
  creative: '创作制片人',
  planner: '内容策划',
  director: '分镜导演',
  qc: '质量审核',
  editor: '剪辑助理',
}

const runStatusLabels: Record<string, string> = {
  created: '等待运行',
  running: '运行中',
  completed: '已完成',
  succeeded: '已完成',
  validation_failed: '候选校验失败',
  failed: '运行失败',
  blocked: '已阻断',
  cancelled: '已取消',
  stale: '已过期',
}

function runDuration(run: AgentRun) {
  if (!run.started_at) return '尚未开始'
  if (!run.finished_at) return '进行中'
  const milliseconds = Math.max(0, new Date(run.finished_at).getTime() - new Date(run.started_at).getTime())
  return milliseconds < 1000 ? `${milliseconds} 毫秒` : `${(milliseconds / 1000).toFixed(1)} 秒`
}

function commandError(...mutations: Array<{ error: Error | null }>) {
  return mutations.find(item => item.error)?.error?.message
}

function AttachmentRow({ item, onBind, busy }: { item: CreationAttachment; onBind: (type: 'identity_reference' | 'voice_sample' | 'inspiration_only') => void; busy: boolean }) {
  const binding = [...item.bindings].reverse().find(value => value.status === 'confirmed')
  const isAudio = item.mime_type.startsWith('audio/')
  return <article className={styles.attachmentRow}>
    <span className={styles.fileIcon}>{isAudio ? <FileAudio size={18} /> : <FileImage size={18} />}</span>
    <div><strong>{item.original_filename}</strong><small>{(item.byte_size / 1024).toFixed(1)} KB · {item.verification_status === 'verified' ? '已验证' : item.verification_status}</small></div>
    {binding ? <span className={styles.bound}><Link2 size={13} />{binding.binding_type === 'identity_reference' ? '人物参考' : binding.binding_type === 'voice_sample' ? '声音样本' : '仅作灵感'}</span> : <div className={styles.bindActions}>
      <button disabled={busy} onClick={() => onBind(isAudio ? 'voice_sample' : 'identity_reference')}>{isAudio ? '绑定声音' : '绑定人物'}</button>
      <button disabled={busy} onClick={() => onBind('inspiration_only')}>仅作灵感</button>
    </div>}
  </article>
}

export function ProjectPage() {
  const { projectId = '' } = useParams()
  const [message, setMessage] = useState('')
  const [clarificationValue, setClarificationValue] = useState('')
  const [customSuggestionSetId, setCustomSuggestionSetId] = useState<string | null>(null)
  const [customSuggestion, setCustomSuggestion] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const setCurrentProjectId = useWorkspace(state => state.setCurrentProjectId)
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId), enabled: Boolean(projectId) })
  const center = useQuery({ queryKey: ['creation-center', projectId], queryFn: () => api.creationCenter(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  useEffect(() => { setCurrentProjectId(projectId); return () => setCurrentProjectId(null) }, [projectId, setCurrentProjectId])
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['creation-center', projectId] })
  const generate = useMutation({ mutationFn: () => api.generateRequirementCandidate(projectId, center.data!.active_requirement.id), onSettled: refresh })
  const retryCreativeTurn = useMutation({
    mutationFn: () => api.retryCreativeTurn(projectId, center.data!.latest_agent_run!.id, center.data!.active_requirement.id),
    onSettled: refresh,
  })
  const addMessage = useMutation({ mutationFn: (content: string) => api.addMessage(projectId, content), onSuccess: async () => { setMessage(''); await refresh(); generate.mutate() } })
  const startConversation = useMutation({ mutationFn: () => api.startConversationSession(projectId), onSuccess: async () => { setMessage(''); setCustomSuggestion(''); setCustomSuggestionSetId(null); await refresh() } })
  const selectSuggestion = useMutation({
    mutationFn: ({ proposalId, suggestionSetId, optionId }: { proposalId: string; suggestionSetId: string; optionId: string }) => api.selectCreativeSuggestion(
      projectId, proposalId, center.data!.active_requirement.id, suggestionSetId, optionId,
    ),
    onSuccess: async () => { setCustomSuggestionSetId(null); setCustomSuggestion(''); await refresh() },
  })
  const accept = useMutation({ mutationFn: () => api.acceptRequirementCandidate(projectId, center.data!.current_candidate!.id, center.data!.active_requirement.id), onSuccess: refresh })
  const reject = useMutation({ mutationFn: () => api.rejectRequirementCandidate(projectId, center.data!.current_candidate!.id, '用户认为当前候选不符合创作方向'), onSuccess: refresh })
  const resolveClarification = useMutation({ mutationFn: (value: unknown) => {
    const clarification = center.data!.pending_clarifications[0]
    return api.resolveClarification(projectId, clarification.id, center.data!.active_requirement.id, value)
  }, onSuccess: async () => { setClarificationValue(''); await refresh() } })
  const register = useMutation({ mutationFn: (file: File) => api.registerAttachment(projectId, file), onSuccess: refresh })
  const bind = useMutation({ mutationFn: ({ attachmentId, type }: { attachmentId: string; type: 'identity_reference' | 'voice_sample' | 'inspiration_only' }) => api.bindAttachment(projectId, attachmentId, type, type === 'identity_reference' ? 'char_main' : type === 'voice_sample' ? 'voice_main' : undefined), onSuccess: refresh })

  useEffect(() => {
    const element = messagesRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [center.data?.messages.length, generate.isPending, generate.error])

  function sendMessage() {
    const content = message.trim()
    if (!content || addMessage.isPending) return
    addMessage.mutate(content)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    sendMessage()
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    sendMessage()
  }
  if (project.isPending || center.isPending) return <div className={styles.loading}>正在读取创作中心…</div>
  if (project.error || center.error || !project.data || !center.data) return <div className={styles.loading}>创作中心读取失败：{project.error?.message || center.error?.message}</div>
  const data = project.data
  const creation = center.data
  const candidate = creation.current_candidate
  const clarification = creation.pending_clarifications[0]
  const error = commandError(addMessage, generate, retryCreativeTurn, startConversation, selectSuggestion, accept, reject, resolveClarification, register, bind)
  const activeProposal = creation.active_creative_proposal

  return <>
    <PageHeader eyebrow="CREATION CENTER" title={data.title} description="对话负责提出需求，候选经过明确确认后才成为正式版本。" actions={<Link className="secondaryButton" to="/"><ArrowLeft size={15} />项目列表</Link>} />
    <div className={styles.versionBar}>
      <div><ShieldCheck size={16} /><span>当前权威版本</span><strong>requirement_v{creation.active_requirement.version_number}</strong></div>
      <i></i>
      <div className={candidate ? styles.candidateActive : ''}><Sparkles size={16} /><span>AI 候选</span><strong>{candidate ? candidate.id.slice(0, 16) : '尚未生成'}</strong></div>
      <div className={styles.versionState}>{candidate ? '尚未生效，等待你审核' : creation.next_action.label}</div>
    </div>

    <main className={styles.layout}>
      <section className={styles.conversation}>
        <div className={styles.panelHeading}><div><MessageSquareText size={18} /><div><span>需求对话</span><h2>创作输入</h2></div></div><div className={styles.conversationTools}><b>{creation.messages.length} 条消息</b><button type="button" title="开启新对话，保留已确认项目需求" disabled={startConversation.isPending || generate.isPending || retryCreativeTurn.isPending} onClick={() => startConversation.mutate()}><MessageSquarePlus size={15} /></button></div></div>
        <div className={styles.messages} ref={messagesRef}>
          {creation.messages.length ? creation.messages.map(item => <article key={item.id} data-role={item.role}><span>{item.role === 'assistant' ? 'AI' : item.role === 'system' ? '系统' : '你'}</span><div><p>{item.content}</p><small>{item.role === 'assistant' ? '创作制片人 · ' : ''}{new Date(item.created_at).toLocaleString('zh-CN')}</small></div></article>) : <div className={styles.emptyMessages}><MessageSquareText size={25} /><strong>从明确的创作需求开始</strong><p>描述内容、受众或风格。未说出的可选信息会保持未指定。</p></div>}
          {activeProposal?.suggestion_sets.map(suggestionSet => {
            const selected = activeProposal.selections.find(item => item.suggestion_set_id === suggestionSet.id)
            return <section className={styles.suggestionSet} key={suggestionSet.id}>
              <header><span>创作建议</span><strong>{suggestionSet.title}</strong></header>
              <div className={styles.suggestionOptions}>{suggestionSet.options.map((option, index) => <button
                type="button"
                key={option.id}
                data-selected={selected?.option_id === option.id}
                disabled={Boolean(selected) || selectSuggestion.isPending}
                onClick={() => selectSuggestion.mutate({ proposalId: activeProposal.id, suggestionSetId: suggestionSet.id, optionId: option.id })}
              >
                <span>{option.label}{index === 0 && <b>推荐</b>}{selected?.option_id === option.id && <Check size={14} />}</span>
                <small>{option.summary}</small>
              </button>)}</div>
              {!selected && (customSuggestionSetId === suggestionSet.id ? <form className={styles.customSuggestion} onSubmit={event => { event.preventDefault(); const content = customSuggestion.trim(); if (!content) return; addMessage.mutate(content); setCustomSuggestion(''); setCustomSuggestionSetId(null) }}>
                <input autoFocus value={customSuggestion} onChange={event => setCustomSuggestion(event.target.value)} placeholder="输入你的想法" />
                <button className="primaryButton" disabled={!customSuggestion.trim() || addMessage.isPending}>发送</button>
              </form> : <button type="button" className={styles.otherSuggestion} onClick={() => setCustomSuggestionSetId(suggestionSet.id)}>其他想法</button>)}
            </section>
          })}
          {activeProposal?.clarifying_question?.prompt && <section className={styles.agentQuestion}>
            <CircleAlert size={17} />
            <div><span>创作制片人需要你补充</span><strong>{activeProposal.clarifying_question.prompt}</strong><small>直接在下方输入回答，本轮问题不会自动修改正式需求。</small></div>
          </section>}
          {(generate.isPending || retryCreativeTurn.isPending) && <article data-role="assistant" data-pending><span>AI</span><div><p>{retryCreativeTurn.isPending ? '正在按你的确认重跑本轮…' : '正在理解本轮需求…'}</p><small>只运行当前配置的创作模型</small></div></article>}
          {(generate.error || retryCreativeTurn.error) && <article data-role="system" data-error><span>系统</span><div><p>本轮智能体没有返回回复：{(generate.error || retryCreativeTurn.error)?.message}</p><small>没有自动重试，也没有切换模型</small></div></article>}
        </div>
        <form className={styles.composer} onSubmit={submit}>
          <textarea value={message} onChange={event => setMessage(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder="继续补充创作要求…" rows={3} />
          <div><button type="button" className="secondaryButton" onClick={() => fileInput.current?.click()} disabled={register.isPending}><Paperclip size={15} />{register.isPending ? '上传中…' : '上传附件'}</button><input ref={fileInput} hidden type="file" accept="image/png,image/jpeg,image/webp,audio/wav,audio/mpeg,video/mp4" onChange={event => { const file = event.target.files?.[0]; if (file) register.mutate(file); event.target.value = '' }} /><span>发送后调用当前配置的创作模型；不会启动图片或视频生产</span><button className="primaryButton" disabled={!message.trim() || addMessage.isPending || generate.isPending || retryCreativeTurn.isPending}>发送<Send size={15} /></button></div>
        </form>
      </section>

      <section className={styles.requirements}>
        <div className={styles.panelHeading}><div><Bot size={18} /><div><span>结构化状态</span><h2>{clarification ? '需要你的决策' : candidate ? '需求候选' : '正式需求'}</h2></div></div><b className={clarification || candidate ? styles.pendingTag : styles.confirmedTag}>{clarification ? '阻断' : candidate ? '待确认' : '已生效'}</b></div>
        {clarification && <div className={styles.clarification}>
          <div className={styles.clarificationTitle}><CircleAlert size={19} /><div><span>{clarification.risk_level === 'high' ? '高风险确认' : '中风险确认'}</span><h3>{clarification.question}</h3></div></div>
          <p>此回答只更新 <code>{clarification.field_key}</code>，不会推断或修改其他需求。</p>
          {clarification.options.length ? <div className={styles.optionList}>{clarification.options.map(option => <button key={String(option.value)} disabled={resolveClarification.isPending} onClick={() => resolveClarification.mutate(option.value)}>{option.label}</button>)}</div> : <form className={styles.clarificationForm} onSubmit={event => { event.preventDefault(); const value = clarification.field_key === 'duration_seconds' ? Number(clarificationValue) : clarificationValue; resolveClarification.mutate(value) }}><input required type={clarification.field_key === 'duration_seconds' ? 'number' : 'text'} min={clarification.field_key === 'duration_seconds' ? 5 : undefined} value={clarificationValue} onChange={event => setClarificationValue(event.target.value)} /><button className="primaryButton" disabled={resolveClarification.isPending}>确认</button></form>}
        </div>}
        <div className={styles.fieldList}>
          {Object.entries(candidate?.fields ?? creation.active_requirement.fields).map(([key, value]) => {
            const source = (candidate?.field_sources ?? creation.active_requirement.field_sources)[key]
            return <div className={styles.field} key={key}><span>{fieldLabels[key] ?? key}</span><p>{key === 'duration_seconds' ? `${String(value)} 秒` : key === 'audio_mode' ? value === 'off' ? '关闭' : '旁白' : String(value)}</p><small data-agent={source?.type === 'agent_proposal'}>{sourceLabels[source?.type ?? 'user'] ?? source?.type ?? '用户输入'}</small></div>
          })}
        </div>
        {candidate ? <div className={styles.candidateActions}><div><strong>候选不会自动生效</strong><span>确认后将创建 requirement_v{creation.active_requirement.version_number + 1}，旧版本保留。</span></div><button className="secondaryButton" onClick={() => reject.mutate()} disabled={reject.isPending}><X size={15} />拒绝</button><button className="primaryButton" onClick={() => accept.mutate()} disabled={accept.isPending}><Check size={15} />确认并创建新版本</button></div> : creation.next_action.code === 'GENERATE_REQUIREMENT_CANDIDATE' ? <div className={styles.generateBox}><div><Sparkles size={18} /><p><strong>整理最新消息为候选</strong><span>只读取活动版本、未消费消息和已确认附件绑定。</span></p></div><button className="primaryButton" onClick={() => generate.mutate()} disabled={generate.isPending}>{generate.isPending ? '正在生成…' : '运行需求整理智能体'}</button></div> : creation.next_action.code === 'RETRY_FAILED_CREATIVE_TURN' && creation.latest_agent_run ? <div className={styles.retryBox}><div><CircleAlert size={18} /><p><strong>本轮创作智能体运行失败</strong><span>{creation.latest_agent_run.error_detail || '模型没有返回有效候选。'} 系统不会自动重试。</span></p></div><button className="primaryButton" onClick={() => retryCreativeTurn.mutate()} disabled={retryCreativeTurn.isPending}>{retryCreativeTurn.isPending ? '正在重跑…' : '确认模型调用并重跑本轮'}</button></div> : creation.next_action.code === 'REQUIREMENT_READY_FOR_PLANNING' ? <div className={styles.readyBox}><CheckCircle2 size={19} /><p><strong>已具备最低策划条件</strong><span>未填写的可选信息保持未指定，下一阶段将基于当前正式版本生成方案候选。</span></p><Link className="primaryButton" to={`/projects/${projectId}/plan`}>进入方案确认</Link></div> : null}
      </section>

      <aside className={styles.side}>
        <section className={styles.nextAction}><span>唯一下一步</span><h3>{creation.next_action.label}</h3><p>模型调用：{creation.next_action.incurs_model_cost ? '是' : '否'} · 生产费用：{creation.next_action.incurs_production_cost ? '是' : '否'}</p></section>
        <section className={styles.attachments}>
          <div className={styles.sideHeading}><div><Paperclip size={17} /><h3>附件与绑定</h3></div><b>{creation.attachments.length}</b></div>
          <p className={styles.attachmentNotice}>当前创作模型只读取文件名、类型和用途绑定，不读取图片、音频或视频内容。</p>
          {creation.attachments.length ? creation.attachments.map(item => <AttachmentRow key={item.id} item={item} busy={bind.isPending} onBind={type => bind.mutate({ attachmentId: item.id, type })} />) : <div className={styles.sideEmpty}>附件上传和用途绑定是两个独立状态。</div>}
        </section>
        <section className={styles.history}>
          <button onClick={() => setShowHistory(value => !value)}><div><History size={17} /><span><strong>运行与候选历史</strong><small>{creation.agent_runs.length} 次运行 · {creation.candidate_history.length} 个候选</small></span></div><ChevronDown size={16} data-open={showHistory} /></button>
          {showHistory && <div className={styles.historyList}>{creation.agent_runs.length ? creation.agent_runs.map(run => {
            const manifest = run.input_manifest
            return <article key={run.id} className={styles.runCard}>
              <div className={styles.runHeading}>
                <div><span>{agentRoleLabels[run.agent_role] ?? run.agent_role}</span><strong>{run.model_name}</strong></div>
                <b data-status={run.status}><Clock3 size={13} />{runStatusLabels[run.status] ?? run.status}</b>
              </div>
              <div className={styles.runFacts}>
                <span>耗时<strong>{runDuration(run)}</strong></span>
                <span>消息<strong>{manifest?.message_ids.length ?? 0}</strong></span>
                <span>决策<strong>{manifest?.decision_ids.length ?? 0}</strong></span>
                <span>附件绑定<strong>{manifest?.attachment_binding_ids.length ?? 0}</strong></span>
              </div>
              <p className={run.parsed_candidate_id ? styles.runResultOk : styles.runResultEmpty}>{run.parsed_candidate_id ? '已登记候选，等待独立审核' : '没有登记候选'}</p>
              {(run.error_code || run.error_detail) && <div className={styles.runFailure}><CircleAlert size={14} /><div><strong>{run.error_code ?? '未提供错误代码'}</strong><span>{run.error_detail ?? '未提供错误说明'}</span></div></div>}
              <details className={styles.runDetails}>
                <summary>查看审计详情</summary>
                <dl>
                  <div><dt>模型供应商</dt><dd>{run.model_provider}</dd></div>
                  <div><dt>提示词合同</dt><dd>{run.prompt_contract_version}</dd></div>
                  <div><dt>输出合同</dt><dd>{run.output_schema_version}</dd></div>
                  <div><dt>模型配置版本</dt><dd>{run.model_config_version_id ?? '未记录'}</dd></div>
                  <div><dt>供应商配置版本</dt><dd>{run.provider_config_version_id ?? '未记录'}</dd></div>
                  <div><dt>供应商请求 ID</dt><dd>{run.provider_request_id ?? '未返回'}</dd></div>
                  <div><dt>Token 用量</dt><dd>{Object.keys(run.token_usage).length ? JSON.stringify(run.token_usage) : '未返回'}</dd></div>
                  <div><dt>系统配置</dt><dd>{manifest?.system_config_version ?? '清单缺失'}</dd></div>
                  <div><dt>基础需求版本</dt><dd>{manifest?.base_requirement_version_id ?? '清单缺失'}</dd></div>
                  <div><dt>输入哈希</dt><dd>{manifest?.input_hash ?? '清单缺失'}</dd></div>
                  <div><dt>运行 ID</dt><dd>{run.id}</dd></div>
                </dl>
              </details>
            </article>
          }) : <div className={styles.sideEmpty}>还没有智能体运行记录。</div>}</div>}
        </section>
        {error && <div className={styles.error}>{error}</div>}
      </aside>
    </main>
  </>
}
