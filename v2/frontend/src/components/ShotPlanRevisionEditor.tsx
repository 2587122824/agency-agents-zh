import { Check, RotateCcw, Save } from 'lucide-react'
import { useMemo, useState } from 'react'

import type { ShotContract, ShotPlanCandidate } from '../api/types'
import styles from './ShotPlanRevisionEditor.module.css'

type EntityOption = {
  id: string
  entity_type: string
  display_name: string
  version_number: number
}

type ShotPatch = { target_shot_code: string; changes: Partial<ShotContract> }

const fields: Array<keyof ShotContract> = [
  'shot_code', 'sequence_number', 'duration_ms', 'shot_type', 'scene_entity_version_id',
  'character_entity_version_ids', 'outfit_entity_version_ids', 'face_visibility',
  'text_policy', 'motion_requirement', 'composition', 'action',
]

function cloneShots(shots: ShotContract[]): ShotContract[] {
  return shots.map(shot => ({
    ...shot,
    character_entity_version_ids: [...shot.character_entity_version_ids],
    outfit_entity_version_ids: [...shot.outfit_entity_version_ids],
  }))
}

function equalValue(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function ShotPlanRevisionEditor({
  candidate,
  entities,
  saving,
  onCancel,
  onSubmit,
}: {
  candidate: ShotPlanCandidate
  entities: EntityOption[]
  saving: boolean
  onCancel: () => void
  onSubmit: (patches: ShotPatch[]) => void
}) {
  const [drafts, setDrafts] = useState(() => cloneShots(candidate.shots))
  const patches = useMemo(() => candidate.shots.flatMap((original, index) => {
    const changes: Partial<ShotContract> = {}
    for (const field of fields) {
      if (!equalValue(original[field], drafts[index][field])) {
        Object.assign(changes, { [field]: drafts[index][field] })
      }
    }
    return Object.keys(changes).length ? [{ target_shot_code: original.shot_code, changes }] : []
  }), [candidate.shots, drafts])

  const update = <K extends keyof ShotContract>(index: number, field: K, value: ShotContract[K]) => {
    setDrafts(current => current.map((shot, shotIndex) => shotIndex === index ? { ...shot, [field]: value } : shot))
  }
  const toggleEntity = (index: number, field: 'character_entity_version_ids' | 'outfit_entity_version_ids', id: string) => {
    const current = drafts[index][field]
    update(index, field, current.includes(id) ? current.filter(value => value !== id) : [...current, id])
  }
  const characters = entities.filter(item => item.entity_type === 'character')
  const outfits = entities.filter(item => item.entity_type === 'outfit')
  const scenes = entities.filter(item => item.entity_type === 'scene')

  return <section className={styles.editor}>
    <header>
      <div><span>STRUCTURED REVISION</span><h3>分镜候选修订 v{candidate.revision_number + 1}</h3></div>
      <strong>{patches.length} 个镜头已修改</strong>
    </header>
    <div className={styles.shotList}>{drafts.map((shot, index) => <article key={`${candidate.id}:${index}`}>
      <div className={styles.shotHeading}><b>{shot.shot_code || `镜头 ${index + 1}`}</b><span>{(shot.duration_ms / 1000).toFixed(1)} 秒</span></div>
      <div className={styles.primaryGrid}>
        <label>镜头编号<input value={shot.shot_code} maxLength={32} onChange={event => update(index, 'shot_code', event.target.value)} /></label>
        <label>顺序<input type="number" min={1} value={shot.sequence_number} onChange={event => update(index, 'sequence_number', Number(event.target.value))} /></label>
        <label>时长（毫秒）<input type="number" min={1} value={shot.duration_ms} onChange={event => update(index, 'duration_ms', Number(event.target.value))} /></label>
        <label>镜头类型<input value={shot.shot_type} maxLength={40} onChange={event => update(index, 'shot_type', event.target.value)} /></label>
      </div>
      <div className={styles.textGrid}>
        <label>动作描述<textarea value={shot.action} maxLength={1000} onChange={event => update(index, 'action', event.target.value)} /></label>
        <label>构图描述<textarea value={shot.composition} maxLength={500} onChange={event => update(index, 'composition', event.target.value)} /></label>
      </div>
      <div className={styles.constraintGrid}>
        <label>人脸可见性<select value={shot.face_visibility} onChange={event => update(index, 'face_visibility', event.target.value)}><option value="required">必须可见</option><option value="optional">可选</option><option value="not_visible">不可见</option></select></label>
        <label>画面文字<select value={shot.text_policy} onChange={event => update(index, 'text_policy', event.target.value)}><option value="forbidden">禁止</option><option value="allowed">允许</option><option value="required">必须出现</option></select></label>
        <label>动态要求<select value={shot.motion_requirement} onChange={event => update(index, 'motion_requirement', event.target.value)}><option value="static">静态</option><option value="moderate">中等</option><option value="significant">明显运动</option></select></label>
        <label>场景版本<select value={shot.scene_entity_version_id ?? ''} onChange={event => update(index, 'scene_entity_version_id', event.target.value || null)}><option value="">未绑定</option>{scenes.map(entity => <option key={entity.id} value={entity.id}>{entity.display_name} · v{entity.version_number}</option>)}</select></label>
      </div>
      <div className={styles.entityGrid}>
        <fieldset><legend>人物版本</legend>{characters.length ? characters.map(entity => <label key={entity.id}><input type="checkbox" checked={shot.character_entity_version_ids.includes(entity.id)} onChange={() => toggleEntity(index, 'character_entity_version_ids', entity.id)} /><span>{entity.display_name} · v{entity.version_number}</span><Check size={13} /></label>) : <em>无可用人物版本</em>}</fieldset>
        <fieldset><legend>服装版本</legend>{outfits.length ? outfits.map(entity => <label key={entity.id}><input type="checkbox" checked={shot.outfit_entity_version_ids.includes(entity.id)} onChange={() => toggleEntity(index, 'outfit_entity_version_ids', entity.id)} /><span>{entity.display_name} · v{entity.version_number}</span><Check size={13} /></label>) : <em>无可用服装版本</em>}</fieldset>
      </div>
    </article>)}</div>
    <footer>
      <span>基于候选 v{candidate.revision_number} · row {candidate.row_version}</span>
      <button className="secondaryButton" type="button" onClick={onCancel}><RotateCcw size={14} />取消</button>
      <button className="primaryButton" type="button" disabled={!patches.length || saving} onClick={() => onSubmit(patches)}><Save size={14} />{saving ? '正在创建…' : '创建修订候选'}</button>
    </footer>
  </section>
}
