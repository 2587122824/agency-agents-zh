import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Bot, Check, ChevronDown, Clock3, FileAudio, FileImage, History, Link2, MessageSquareText, Paperclip, Send, ShieldCheck, Sparkles, X } from 'lucide-react'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { CreationAttachment } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { useWorkspace } from '../store/workspace'
import styles from './ProjectPage.module.css'

const sourceLabels: Record<string, string> = {
  user: '用户输入',
  agent_proposal: 'Agent 建议',
  declared_default: '已声明默认值',
  template: '模板',
}

const fieldLabels: Record<string, string> = {
  title: '项目名称', core_topic: '核心主题', duration_seconds: '目标时长',
  aspect_ratio: '画幅', audio_mode: '音频模式', creative_direction: '创作方向',
}

function commandError(...mutations: Array<{ error: Error | null }>) {
  return mutations.find(item => item.error)?.error?.message
}

function AttachmentRow({ item, onBind, busy }: { item: CreationAttachment; onBind: (type: 'identity_reference' | 'voice_sample' | 'inspiration_only') => void; busy: boolean }) {
  const binding = item.bindings.at(-1)
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
  const [showHistory, setShowHistory] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const setCurrentProjectId = useWorkspace(state => state.setCurrentProjectId)
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId), enabled: Boolean(projectId) })
  const center = useQuery({ queryKey: ['creation-center', projectId], queryFn: () => api.creationCenter(projectId), enabled: Boolean(projectId), refetchInterval: 5000 })
  useEffect(() => { setCurrentProjectId(projectId); return () => setCurrentProjectId(null) }, [projectId, setCurrentProjectId])
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['creation-center', projectId] })
  const addMessage = useMutation({ mutationFn: (content: string) => api.addMessage(projectId, content), onSuccess: async () => { setMessage(''); await refresh() } })
  const generate = useMutation({ mutationFn: () => api.generateRequirementCandidate(projectId, center.data!.active_requirement.id), onSuccess: refresh })
  const accept = useMutation({ mutationFn: () => api.acceptRequirementCandidate(projectId, center.data!.current_candidate!.id, center.data!.active_requirement.id), onSuccess: refresh })
  const reject = useMutation({ mutationFn: () => api.rejectRequirementCandidate(projectId, center.data!.current_candidate!.id, '用户认为当前候选不符合创作方向'), onSuccess: refresh })
  const register = useMutation({ mutationFn: (file: File) => api.registerAttachment(projectId, file), onSuccess: refresh })
  const bind = useMutation({ mutationFn: ({ attachmentId, type }: { attachmentId: string; type: 'identity_reference' | 'voice_sample' | 'inspiration_only' }) => api.bindAttachment(projectId, attachmentId, type, type === 'identity_reference' ? 'char_main' : type === 'voice_sample' ? 'voice_main' : undefined), onSuccess: refresh })

  function submit(event: FormEvent) { event.preventDefault(); if (message.trim()) addMessage.mutate(message.trim()) }
  if (project.isPending || center.isPending) return <div className={styles.loading}>正在读取创作中心…</div>
  if (project.error || center.error || !project.data || !center.data) return <div className={styles.loading}>创作中心读取失败：{project.error?.message || center.error?.message}</div>
  const data = project.data
  const creation = center.data
  const candidate = creation.current_candidate
  const error = commandError(addMessage, generate, accept, reject, register, bind)

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
        <div className={styles.panelHeading}><div><MessageSquareText size={18} /><div><span>需求对话</span><h2>创作输入</h2></div></div><b>{creation.messages.length} 条消息</b></div>
        <div className={styles.messages}>
          {creation.messages.length ? creation.messages.map(item => <article key={item.id}><span>你</span><div><p>{item.content}</p><small>{new Date(item.created_at).toLocaleString('zh-CN')}</small></div></article>) : <div className={styles.emptyMessages}><MessageSquareText size={25} /><strong>从明确的创作需求开始</strong><p>描述内容、受众或风格。未说出的可选信息会保持未指定。</p></div>}
        </div>
        <form className={styles.composer} onSubmit={submit}>
          <textarea value={message} onChange={event => setMessage(event.target.value)} placeholder="继续补充创作要求…" rows={3} />
          <div><button type="button" className="secondaryButton" onClick={() => fileInput.current?.click()} disabled={register.isPending}><Paperclip size={15} />{register.isPending ? '上传中…' : '上传附件'}</button><input ref={fileInput} hidden type="file" accept="image/png,image/jpeg,image/webp,audio/wav,audio/mpeg,video/mp4" onChange={event => { const file = event.target.files?.[0]; if (file) register.mutate(file); event.target.value = '' }} /><span>本阶段使用 Mock Agent，不产生模型或生产费用</span><button className="primaryButton" disabled={!message.trim() || addMessage.isPending}>发送<Send size={15} /></button></div>
        </form>
      </section>

      <section className={styles.requirements}>
        <div className={styles.panelHeading}><div><Bot size={18} /><div><span>结构化状态</span><h2>{candidate ? '需求候选' : '正式需求'}</h2></div></div><b className={candidate ? styles.pendingTag : styles.confirmedTag}>{candidate ? '待确认' : '已生效'}</b></div>
        <div className={styles.fieldList}>
          {Object.entries(candidate?.fields ?? creation.active_requirement.fields).map(([key, value]) => {
            const source = (candidate?.field_sources ?? creation.active_requirement.field_sources)[key]
            return <div className={styles.field} key={key}><span>{fieldLabels[key] ?? key}</span><p>{key === 'duration_seconds' ? `${String(value)} 秒` : key === 'audio_mode' ? value === 'off' ? '关闭' : '旁白' : String(value)}</p><small data-agent={source?.type === 'agent_proposal'}>{sourceLabels[source?.type ?? 'user'] ?? source?.type ?? '用户输入'}</small></div>
          })}
        </div>
        {candidate ? <div className={styles.candidateActions}><div><strong>候选不会自动生效</strong><span>确认后将创建 requirement_v{creation.active_requirement.version_number + 1}，旧版本保留。</span></div><button className="secondaryButton" onClick={() => reject.mutate()} disabled={reject.isPending}><X size={15} />拒绝</button><button className="primaryButton" onClick={() => accept.mutate()} disabled={accept.isPending}><Check size={15} />确认并创建新版本</button></div> : <div className={styles.generateBox}><div><Sparkles size={18} /><p><strong>整理最新消息为候选</strong><span>只读取活动版本、消息和已确认附件绑定。</span></p></div><button className="primaryButton" onClick={() => generate.mutate()} disabled={!creation.messages.length || generate.isPending}>{generate.isPending ? '正在生成…' : '运行 Mock Agent'}</button></div>}
      </section>

      <aside className={styles.side}>
        <section className={styles.nextAction}><span>唯一下一步</span><h3>{creation.next_action.label}</h3><p>模型费用：否 · 生产费用：否</p></section>
        <section className={styles.attachments}>
          <div className={styles.sideHeading}><div><Paperclip size={17} /><h3>附件与绑定</h3></div><b>{creation.attachments.length}</b></div>
          {creation.attachments.length ? creation.attachments.map(item => <AttachmentRow key={item.id} item={item} busy={bind.isPending} onBind={type => bind.mutate({ attachmentId: item.id, type })} />) : <div className={styles.sideEmpty}>附件上传和用途绑定是两个独立状态。</div>}
        </section>
        <section className={styles.history}>
          <button onClick={() => setShowHistory(value => !value)}><div><History size={17} /><span><strong>运行与候选历史</strong><small>{creation.agent_runs.length} 次运行 · {creation.candidate_history.length} 个候选</small></span></div><ChevronDown size={16} data-open={showHistory} /></button>
          {showHistory && <div className={styles.historyList}>{creation.agent_runs.length ? creation.agent_runs.map(run => <article key={run.id}><span data-status={run.status}><Clock3 size={13} />{run.status}</span><strong>{run.model_name}</strong><small>{run.id.slice(0, 18)}</small></article>) : <div className={styles.sideEmpty}>还没有 AgentRun。</div>}</div>}
        </section>
        {error && <div className={styles.error}>{error}</div>}
      </aside>
    </main>
  </>
}
