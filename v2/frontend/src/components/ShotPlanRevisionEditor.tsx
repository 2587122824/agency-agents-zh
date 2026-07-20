import { Check, ChevronLeft, ChevronRight, RotateCcw, Save, Search, Sparkles, Undo2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { ShotContract, ShotPlanCandidate } from '../api/types'
import styles from './ShotPlanRevisionEditor.module.css'

type EntityOption = {
  id: string
  entity_type: string
  display_name: string
  version_number: number
  source_attachment_id: string | null
  source_mime_type: string | null
  source_attachment_verified: boolean
}

type ShotPatch = { target_shot_code: string; changes: Partial<ShotContract> }
type ShotFilter = 'all' | 'modified'

const fields: Array<keyof ShotContract> = [
  'shot_code', 'sequence_number', 'duration_ms', 'narrative_beat_code', 'brief_segment_codes', 'continuity_group_id', 'continuity_relation',
  'shot_purpose', 'framing', 'camera_angle', 'camera_motion', 'subject_motion', 'scene_entity_version_id',
  'character_entity_version_ids', 'outfit_entity_version_ids', 'product_entity_version_ids',
  'primary_reference_entity_version_id', 'face_visibility', 'face_subject_entity_version_ids', 'text_policy', 'required_on_screen_text', 'audio_requirement',
  'composition', 'action', 'visual_prompt', 'negative_prompt', 'new_information', 'generation_requirements',
]

function cloneShot(shot: ShotContract): ShotContract {
  return {
    ...shot,
    brief_segment_codes: [...shot.brief_segment_codes],
    character_entity_version_ids: [...shot.character_entity_version_ids],
    outfit_entity_version_ids: [...shot.outfit_entity_version_ids],
    product_entity_version_ids: [...shot.product_entity_version_ids],
    face_subject_entity_version_ids: [...shot.face_subject_entity_version_ids],
    generation_requirements: { ...shot.generation_requirements },
  }
}

function cloneShots(shots: ShotContract[]): ShotContract[] {
  return shots.map(cloneShot)
}

function equalValue(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function shotChanges(original: ShotContract, draft: ShotContract) {
  const changes: Partial<ShotContract> = {}
  for (const field of fields) {
    if (!equalValue(original[field], draft[field])) {
      Object.assign(changes, { [field]: draft[field] })
    }
  }
  return changes
}

export function ShotPlanRevisionEditor({
  candidate,
  projectId,
  entities,
  scriptSegments,
  saving,
  aiSaving,
  onCancel,
  onSubmit,
  onAIRevision,
}: {
  candidate: ShotPlanCandidate
  projectId: string
  entities: EntityOption[]
  scriptSegments: Array<{ segment_code: string; beat_code: string; kind: string; spoken_text: string | null; on_screen_text: string | null }>
  saving: boolean
  aiSaving: boolean
  onCancel: () => void
  onSubmit: (patches: ShotPatch[]) => void
  onAIRevision: (selectedShotCodes: string[], instruction: string) => void
}) {
  const [drafts, setDrafts] = useState(() => cloneShots(candidate.shots))
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<ShotFilter>('all')
  const [aiSelected, setAiSelected] = useState<string[]>([])
  const [aiInstruction, setAiInstruction] = useState('')

  const changesByIndex = useMemo(
    () => candidate.shots.map((original, index) => shotChanges(original, drafts[index])),
    [candidate.shots, drafts],
  )
  const modifiedIndices = useMemo(
    () => new Set(changesByIndex.flatMap((changes, index) => Object.keys(changes).length ? [index] : [])),
    [changesByIndex],
  )
  const patches = useMemo(() => changesByIndex.flatMap((changes, index) => Object.keys(changes).length ? [{
    target_shot_code: candidate.shots[index].shot_code,
    changes,
  }] : []), [candidate.shots, changesByIndex])
  const visibleIndices = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase()
    return drafts.flatMap((shot, index) => {
      if (filter === 'modified' && !modifiedIndices.has(index)) return []
      const searchable = `${shot.shot_code} ${shot.shot_purpose} ${shot.action} ${shot.narrative_beat_code ?? ''}`.toLocaleLowerCase()
      return !normalizedQuery || searchable.includes(normalizedQuery) ? [index] : []
    })
  }, [drafts, filter, modifiedIndices, query])

  useEffect(() => {
    if (visibleIndices.length && !visibleIndices.includes(selectedIndex)) {
      setSelectedIndex(visibleIndices[0])
    }
  }, [selectedIndex, visibleIndices])

  const update = <K extends keyof ShotContract>(index: number, field: K, value: ShotContract[K]) => {
    setDrafts(current => current.map((shot, shotIndex) => shotIndex === index ? { ...shot, [field]: value } : shot))
  }
  const resetShot = (index: number) => {
    setDrafts(current => current.map((shot, shotIndex) => shotIndex === index ? cloneShot(candidate.shots[index]) : shot))
  }
  const toggleEntity = (index: number, field: 'character_entity_version_ids' | 'outfit_entity_version_ids' | 'product_entity_version_ids', id: string) => {
    const current = drafts[index][field]
    const removing = current.includes(id)
    setDrafts(items => items.map((shot, shotIndex) => shotIndex !== index ? shot : {
      ...shot,
      [field]: removing ? current.filter(value => value !== id) : [...current, id],
      primary_reference_entity_version_id: removing && shot.primary_reference_entity_version_id === id
        ? null
        : shot.primary_reference_entity_version_id,
      face_subject_entity_version_ids: field === 'character_entity_version_ids' && removing
        ? shot.face_subject_entity_version_ids.filter(value => value !== id)
        : shot.face_subject_entity_version_ids,
    }))
  }

  const characters = entities.filter(item => item.entity_type === 'character')
  const outfits = entities.filter(item => item.entity_type === 'outfit')
  const scenes = entities.filter(item => item.entity_type === 'scene')
  const products = entities.filter(item => item.entity_type === 'product')
  const shot = drafts[selectedIndex]
  const isSelectedModified = modifiedIndices.has(selectedIndex)
  const declaredIds = new Set([
    ...(shot.scene_entity_version_id ? [shot.scene_entity_version_id] : []),
    ...shot.character_entity_version_ids,
    ...shot.outfit_entity_version_ids,
    ...shot.product_entity_version_ids,
  ])
  const referenceOptions = entities.filter(entity => declaredIds.has(entity.id) && entity.source_attachment_verified && entity.source_attachment_id && entity.source_mime_type?.startsWith('image/'))
  const selectedReference = referenceOptions.find(entity => entity.id === shot.primary_reference_entity_version_id)

  return <section className={styles.editor}>
    <header>
      <div><span>STRUCTURED REVISION</span><h3>分镜候选修订 v{candidate.revision_number + 1}</h3></div>
      <strong>{patches.length} 个镜头已修改</strong>
    </header>
    <div className={styles.workspace}>
      <aside className={styles.navigator} style={{ minHeight: 0 }}>
        <div className={styles.navigatorTitle}><div><strong>镜头列表</strong><span>{candidate.shots.length} 个镜头</span></div></div>
        <label className={styles.searchBox}><Search size={14} /><input value={query} placeholder="搜索编号、类型或内容" onChange={event => setQuery(event.target.value)} /></label>
        <div className={styles.filters} aria-label="镜头筛选">
          <button type="button" data-active={filter === 'all'} onClick={() => setFilter('all')}>全部</button>
          <button type="button" data-active={filter === 'modified'} onClick={() => setFilter('modified')}>已修改 {modifiedIndices.size}</button>
        </div>
        <div className={styles.navigationList}>
          {visibleIndices.map(index => {
            const item = drafts[index]
            return <div key={`${candidate.id}:${index}`} className={styles.navigationItem} data-active={index === selectedIndex}>
              <button className={styles.navigationButton} type="button" onClick={() => setSelectedIndex(index)}>
                <span className={styles.shotNumber}>{String(index + 1).padStart(2, '0')}</span>
                <span className={styles.shotSummary}><strong>{item.shot_code || `镜头 ${index + 1}`}</strong><small>{item.action || item.shot_purpose}</small><em>{item.shot_purpose} · {(item.duration_ms / 1000).toFixed(1)} 秒</em></span>
                {modifiedIndices.has(index) && <span className={styles.modifiedDot}>已修改</span>}
              </button>
              <label className={styles.aiShotToggle} title="选择此镜头用于 AI 调整">
                <input type="checkbox" aria-label={`选择 ${item.shot_code} 用于 AI 调整`} checked={aiSelected.includes(item.shot_code)} onChange={() => setAiSelected(current => current.includes(item.shot_code) ? current.filter(code => code !== item.shot_code) : [...current, item.shot_code])} />
              </label>
            </div>
          })}
          {!visibleIndices.length && <div className={styles.emptyList}><strong>没有匹配的镜头</strong><span>调整搜索词或切换筛选条件。</span></div>}
        </div>
      </aside>

      <div className={styles.detail}>
        <div className={styles.shotHeading}>
          <div><span>正在编辑</span><b>{shot.shot_code || `镜头 ${selectedIndex + 1}`}</b>{isSelectedModified && <em>未保存修改</em>}</div>
          <div className={styles.shotActions}>
            <button type="button" title="上一个镜头" disabled={selectedIndex === 0} onClick={() => setSelectedIndex(index => index - 1)}><ChevronLeft size={15} /></button>
            <span>{selectedIndex + 1} / {drafts.length}</span>
            <button type="button" title="下一个镜头" disabled={selectedIndex === drafts.length - 1} onClick={() => setSelectedIndex(index => index + 1)}><ChevronRight size={15} /></button>
            <button type="button" className={styles.resetButton} disabled={!isSelectedModified} onClick={() => resetShot(selectedIndex)}><Undo2 size={14} />重置此镜头</button>
          </div>
        </div>
        <div className={styles.formScroll}>
          <section className={styles.formSection}>
            <div className={styles.sectionTitle}><strong>镜头信息</strong><span>编号、顺序与内容节拍</span></div>
            <div className={styles.primaryGrid}>
              <label>镜头编号<input value={shot.shot_code} maxLength={32} onChange={event => update(selectedIndex, 'shot_code', event.target.value)} /></label>
              <label>顺序<input type="number" min={1} value={shot.sequence_number} onChange={event => update(selectedIndex, 'sequence_number', Number(event.target.value))} /></label>
              <label>时长（毫秒）<input type="number" min={1} value={shot.duration_ms} onChange={event => update(selectedIndex, 'duration_ms', Number(event.target.value))} /></label>
              <label>内容节拍<input value={shot.narrative_beat_code ?? ''} maxLength={32} onChange={event => update(selectedIndex, 'narrative_beat_code', event.target.value)} /></label>
              <label>连续组<input value={shot.continuity_group_id ?? ''} maxLength={32} placeholder="不连续则留空" onChange={event => update(selectedIndex, 'continuity_group_id', event.target.value || null)} /></label>
              <label>连续关系<select value={shot.continuity_relation} onChange={event => update(selectedIndex, 'continuity_relation', event.target.value as ShotContract['continuity_relation'])}><option value="same_moment">同一时刻</option><option value="time_jump">时间跳转</option><option value="location_change">地点变化</option><option value="outfit_change">换装</option></select></label>
              <label>镜头目的<select value={shot.shot_purpose} onChange={event => update(selectedIndex, 'shot_purpose', event.target.value as ShotContract['shot_purpose'])}><option value="establish">建立信息</option><option value="develop">推进内容</option><option value="demonstrate">演示</option><option value="contrast">对比</option><option value="transition">过渡</option><option value="resolve">收束</option></select></label>
              <label>景别<select value={shot.framing} onChange={event => update(selectedIndex, 'framing', event.target.value as ShotContract['framing'])}><option value="extreme_close_up">大特写</option><option value="close_up">特写</option><option value="medium">中景</option><option value="full">全身</option><option value="wide">远景</option></select></label>
              <label>机位角度<select value={shot.camera_angle} onChange={event => update(selectedIndex, 'camera_angle', event.target.value as ShotContract['camera_angle'])}><option value="eye_level">平视</option><option value="high">俯拍</option><option value="low">仰拍</option><option value="top_down">顶视</option><option value="over_shoulder">过肩</option></select></label>
              <label>镜头运动<select value={shot.camera_motion} onChange={event => update(selectedIndex, 'camera_motion', event.target.value as ShotContract['camera_motion'])}><option value="locked">固定</option><option value="pan">横摇</option><option value="tilt">纵摇</option><option value="dolly">推拉</option><option value="tracking">跟拍</option><option value="handheld">手持</option></select></label>
            </div>
            <fieldset className={styles.inlineChecks}><legend>覆盖的脚本段</legend>{scriptSegments.filter(segment => segment.beat_code === shot.narrative_beat_code).map(segment => <label key={segment.segment_code}><input type="checkbox" checked={shot.brief_segment_codes.includes(segment.segment_code)} onChange={() => update(selectedIndex, 'brief_segment_codes', shot.brief_segment_codes.includes(segment.segment_code) ? shot.brief_segment_codes.filter(code => code !== segment.segment_code) : [...shot.brief_segment_codes, segment.segment_code])} /><span>{segment.segment_code} · {segment.spoken_text || segment.on_screen_text || segment.kind}</span></label>)}</fieldset>
          </section>
          <section className={styles.formSection}>
            <div className={styles.sectionTitle}><strong>画面内容</strong><span>描述该镜头的动作、构图与生成要求</span></div>
            <div className={styles.textGrid}>
              <label>动作描述<textarea value={shot.action} maxLength={1000} onChange={event => update(selectedIndex, 'action', event.target.value)} /></label>
              <label>构图描述<textarea value={shot.composition} maxLength={500} onChange={event => update(selectedIndex, 'composition', event.target.value)} /></label>
            </div>
            <div className={styles.generationGrid}>
              <label>画面生成描述<textarea required value={shot.visual_prompt ?? ''} maxLength={4000} onChange={event => update(selectedIndex, 'visual_prompt', event.target.value)} /></label>
              <label>避免内容（可不设置）<textarea value={shot.negative_prompt ?? ''} maxLength={2000} placeholder="未设置" onChange={event => update(selectedIndex, 'negative_prompt', event.target.value || null)} /></label>
            </div>
            <label className={styles.fullText}>相对前一镜头新增的信息<textarea value={shot.new_information} maxLength={1000} onChange={event => update(selectedIndex, 'new_information', event.target.value)} /></label>
          </section>
          <section className={styles.formSection}>
            <div className={styles.sectionTitle}><strong>生成约束</strong><span>明确可见性、动态、声音与场景</span></div>
            <div className={styles.constraintGrid}>
              <label>人脸可见性<select value={shot.face_visibility} onChange={event => update(selectedIndex, 'face_visibility', event.target.value)}><option value="required">必须可见</option><option value="optional">可选</option><option value="not_visible">不可见</option></select></label>
              <label>画面文字<select value={shot.text_policy} onChange={event => update(selectedIndex, 'text_policy', event.target.value)}><option value="forbidden">禁止</option><option value="allowed">允许</option><option value="required">必须出现</option></select></label>
              <label>主体运动<select value={shot.subject_motion} onChange={event => update(selectedIndex, 'subject_motion', event.target.value as ShotContract['subject_motion'])}><option value="none">无</option><option value="subtle">轻微</option><option value="moderate">中等</option><option value="significant">明显</option></select></label>
              <label>声音要求<select value={shot.audio_requirement} onChange={event => update(selectedIndex, 'audio_requirement', event.target.value as ShotContract['audio_requirement'])}><option value="off">无声音依赖</option><option value="lip_motion_only">仅说话动作</option><option value="configured">使用项目声音配置</option></select></label>
              <label>场景版本<select value={shot.scene_entity_version_id ?? ''} onChange={event => { const next = event.target.value || null; setDrafts(items => items.map((item, shotIndex) => shotIndex !== selectedIndex ? item : { ...item, scene_entity_version_id: next, primary_reference_entity_version_id: item.primary_reference_entity_version_id === item.scene_entity_version_id ? null : item.primary_reference_entity_version_id })) }}><option value="">未绑定</option>{scenes.map(entity => <option key={entity.id} value={entity.id}>{entity.display_name} · v{entity.version_number}</option>)}</select></label>
              <label>画面精确文字<input disabled={shot.text_policy === 'forbidden'} value={shot.required_on_screen_text ?? ''} onChange={event => update(selectedIndex, 'required_on_screen_text', event.target.value || null)} placeholder={shot.text_policy === 'required' ? '必须填写准确文字' : '没有则留空'} /></label>
            </div>
          </section>
          <section className={styles.formSection}>
            <div className={styles.sectionTitle}><strong>实体与参考</strong><span>只选择该镜头实际使用的版本</span></div>
            <div className={styles.entityGrid}>
              <fieldset><legend>人物版本</legend>{characters.length ? characters.map(entity => <label key={entity.id}><input type="checkbox" checked={shot.character_entity_version_ids.includes(entity.id)} onChange={() => toggleEntity(selectedIndex, 'character_entity_version_ids', entity.id)} /><span>{entity.display_name} · v{entity.version_number}</span><Check size={13} /></label>) : <em>无可用人物版本</em>}</fieldset>
              <fieldset><legend>服装版本</legend>{outfits.length ? outfits.map(entity => <label key={entity.id}><input type="checkbox" checked={shot.outfit_entity_version_ids.includes(entity.id)} onChange={() => toggleEntity(selectedIndex, 'outfit_entity_version_ids', entity.id)} /><span>{entity.display_name} · v{entity.version_number}</span><Check size={13} /></label>) : <em>无可用服装版本</em>}</fieldset>
              <fieldset><legend>产品版本</legend>{products.length ? products.map(entity => <label key={entity.id}><input type="checkbox" checked={shot.product_entity_version_ids.includes(entity.id)} onChange={() => toggleEntity(selectedIndex, 'product_entity_version_ids', entity.id)} /><span>{entity.display_name} · v{entity.version_number}</span><Check size={13} /></label>) : <em>无可用产品版本</em>}</fieldset>
            </div>
            <div className={styles.contractChecks}>
              <fieldset><legend>必须看清人脸的主体</legend>{characters.filter(entity => shot.character_entity_version_ids.includes(entity.id)).map(entity => <label key={entity.id}><input type="checkbox" disabled={shot.face_visibility !== 'required'} checked={shot.face_subject_entity_version_ids.includes(entity.id)} onChange={() => update(selectedIndex, 'face_subject_entity_version_ids', shot.face_subject_entity_version_ids.includes(entity.id) ? shot.face_subject_entity_version_ids.filter(id => id !== entity.id) : [...shot.face_subject_entity_version_ids, entity.id])} />{entity.display_name} · v{entity.version_number}</label>)}</fieldset>
              <fieldset><legend>制作能力要求</legend>{([['reference_image_required', '需要参考图'], ['multi_frame_required', '需要多帧输入'], ['identity_consistency_required', '需要身份一致性'], ['precise_text_required', '需要精确文字']] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={shot.generation_requirements[key]} onChange={() => update(selectedIndex, 'generation_requirements', { ...shot.generation_requirements, [key]: !shot.generation_requirements[key] })} />{label}</label>)}</fieldset>
            </div>
            <div className={styles.referencePicker}>
              <label>主参考图<select value={shot.primary_reference_entity_version_id ?? ''} onChange={event => update(selectedIndex, 'primary_reference_entity_version_id', event.target.value || null)}><option value="">无主参考图</option>{referenceOptions.map(entity => <option key={entity.id} value={entity.id}>{entity.display_name} · v{entity.version_number}</option>)}</select></label>
              {selectedReference?.source_attachment_id ? <figure><img src={`/api/v1/projects/${projectId}/attachments/${selectedReference.source_attachment_id}/content`} alt={selectedReference.display_name} /><figcaption>{selectedReference.display_name} · v{selectedReference.version_number}</figcaption></figure> : <div><strong>未选择主参考图</strong><span>需要参考图的生成方案会明确阻止制作。</span></div>}
            </div>
          </section>
        </div>
      </div>
    </div>
    <section className={styles.aiRevision}>
      <div><Sparkles size={17} /><p><strong>AI 调整选中镜头</strong><span>只授权分镜导演修改勾选镜头；其他镜头必须保持完全一致。本操作会调用一次当前模型。</span></p></div>
      <textarea value={aiInstruction} onChange={event => setAiInstruction(event.target.value)} placeholder="明确说明选中镜头需要怎样调整" />
      <button className="secondaryButton" type="button" disabled={!aiSelected.length || !aiInstruction.trim() || aiSaving} onClick={() => onAIRevision(aiSelected, aiInstruction.trim())}><Sparkles size={14} />{aiSaving ? '正在调整…' : `确认费用并调整 ${aiSelected.length} 个镜头`}</button>
    </section>
    <footer>
      <span>基于候选 v{candidate.revision_number} · row {candidate.row_version}</span>
      <button className="secondaryButton" type="button" onClick={onCancel}><RotateCcw size={14} />取消</button>
      <button className="primaryButton" type="button" disabled={!patches.length || saving} onClick={() => onSubmit(patches)}><Save size={14} />{saving ? '正在创建…' : `创建修订候选${patches.length ? `（${patches.length}）` : ''}`}</button>
    </footer>
  </section>
}
