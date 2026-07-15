import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AudioLines, BadgeCheck, Boxes, FileSearch, History, Link2, MapPinned, Package, RefreshCw, Search, Shirt, UserRound } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { RegistryEntity, RegistryEntityVersion } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import styles from './AssetLibraryPage.module.css'

const kinds = [
  { key: 'all', label: '全部', icon: Boxes },
  { key: 'character', label: '人物', icon: UserRound },
  { key: 'outfit', label: '服装', icon: Shirt },
  { key: 'scene', label: '场景', icon: MapPinned },
  { key: 'product', label: '产品', icon: Package },
  { key: 'voice', label: '声音', icon: AudioLines },
] as const

const iconByKind = { character: UserRound, outfit: Shirt, scene: MapPinned, product: Package, voice: AudioLines }

function sourceUrl(entity: RegistryEntity, version: RegistryEntityVersion) {
  return version.source_attachment ? `/api/v1/projects/${entity.project_id}/attachments/${version.source_attachment.id}/content` : ''
}

function SourcePreview({ entity, version }: { entity: RegistryEntity; version: RegistryEntityVersion }) {
  const [previewFailed, setPreviewFailed] = useState(false)
  const source = version.source_attachment
  if (!source) return <div className={styles.noSource}><FileSearch /><span>此版本没有来源附件</span></div>
  if (previewFailed) return <div className={styles.previewFailed}><FileSearch /><strong>来源附件无法预览</strong><span>保留文件哈希与引用证据，不自动修复内容。</span></div>
  const url = sourceUrl(entity, version)
  if (source.mime_type.startsWith('image/')) return <img src={url} alt={source.original_filename} onError={() => setPreviewFailed(true)} />
  if (source.mime_type.startsWith('audio/')) return <div className={styles.audio}><AudioLines /><audio controls preload="metadata" src={url} /></div>
  if (source.mime_type.startsWith('video/')) return <video controls preload="metadata" src={url} onError={() => setPreviewFailed(true)} />
  return <div className={styles.noSource}><FileSearch /><span>{source.original_filename}</span></div>
}

export function AssetLibraryPage() {
  const client = useQueryClient()
  const registry = useQuery({ queryKey: ['entity-registry'], queryFn: api.entityRegistry, refetchInterval: 10000 })
  const [kind, setKind] = useState('all')
  const [projectId, setProjectId] = useState('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const entities = useMemo(() => (registry.data?.entities ?? []).filter(entity => {
    if (kind !== 'all' && entity.entity_type !== kind) return false
    if (projectId !== 'all' && entity.project_id !== projectId) return false
    const term = query.trim().toLocaleLowerCase()
    return !term || `${entity.display_name} ${entity.id} ${entity.project_title}`.toLocaleLowerCase().includes(term)
  }), [registry.data, kind, projectId, query])
  const selected = entities.find(entity => entity.id === selectedId) ?? null

  return <>
    <PageHeader eyebrow="ENTITY REGISTRY" title="资产库" description="查看人物、服装、场景、产品和声音的版本、来源与真实引用，不从描述推断实体关系。" actions={<button className="secondaryButton" onClick={() => client.invalidateQueries({ queryKey: ['entity-registry'] })}><RefreshCw size={14} />刷新</button>} />
    <main className={styles.page}>
      <section className={styles.metrics}>{kinds.slice(1).map(item => { const Icon = item.icon; return <div key={item.key}><Icon /><span>{item.label}</span><strong>{registry.data?.counts[item.key] ?? 0}</strong></div> })}</section>
      <section className={styles.toolbar}>
        <div className={styles.tabs}>{kinds.map(item => { const Icon = item.icon; return <button key={item.key} data-active={kind === item.key} onClick={() => { setKind(item.key); setSelectedId('') }}><Icon size={14} />{item.label}</button> })}</div>
        <label><Search size={14} /><input value={query} onChange={event => { setQuery(event.target.value); setSelectedId('') }} placeholder="搜索名称、ID 或项目" /></label>
        <select aria-label="项目筛选" value={projectId} onChange={event => { setProjectId(event.target.value); setSelectedId('') }}><option value="all">全部项目</option>{registry.data?.projects.map(project => <option key={project.id} value={project.id}>{project.title}</option>)}</select>
      </section>

      <div className={styles.workspace}>
        <aside className={styles.entityList}>
          <header><div><span>ENTITIES</span><h2>实体</h2></div><b>{entities.length}</b></header>
          {registry.isPending && <p>正在读取实体注册表...</p>}
          {registry.error && <p className={styles.error}>{registry.error.message}</p>}
          {!registry.isPending && !entities.length && <div className={styles.listEmpty}><FileSearch /><span>当前筛选下没有已确认实体</span></div>}
          {entities.map(entity => { const Icon = iconByKind[entity.entity_type]; const active = entity.versions.find(item => item.id === entity.active_version_id); return <button key={entity.id} data-selected={selectedId === entity.id} onClick={() => setSelectedId(entity.id)}><i><Icon /></i><span><strong>{entity.display_name}</strong><small>{entity.project_title}</small><em>{entity.id}</em></span><b>{active ? `v${active.version_number}` : '--'}</b></button> })}
        </aside>

        <section className={styles.detail}>
          {!selected && <div className={styles.empty}><History /><strong>选择一个实体查看版本证据</strong><span>查看行为不会选中生产实体，也不会修改任何项目合同。</span></div>}
          {selected && <>
            <header className={styles.entityHeader}>{(() => { const Icon = iconByKind[selected.entity_type]; return <i><Icon /></i> })()}<div><span>{selected.entity_type}</span><h2>{selected.display_name}</h2><code>{selected.id}</code></div><div><Link to={`/projects/${selected.project_id}`}>{selected.project_title}</Link><em>{selected.status}</em><small>{selected.versions.length} 个不可变版本</small></div></header>
            <div className={styles.versions}>{selected.versions.map(version => <article key={version.id} data-active={version.is_active}>
              <header><div><History /><span><strong>版本 {version.version_number}</strong><small>{version.id}</small></span></div><div>{version.is_active && <em><BadgeCheck />活动版本</em>}<time>{new Date(version.created_at).toLocaleString('zh-CN', { hour12: false })}</time></div></header>
              <div className={styles.versionGrid}>
                <section className={styles.source}><SourcePreview entity={selected} version={version} />{version.source_attachment && <div><strong>{version.source_attachment.original_filename}</strong><small>{version.source_attachment.mime_type} · {(version.source_attachment.byte_size / 1024).toFixed(1)} KB</small><code>{version.source_attachment.content_hash.slice(0, 24)}</code></div>}</section>
                <section className={styles.facts}><h3>版本事实</h3><dl><div><dt>状态</dt><dd>{version.status}</dd></div><div><dt>创建者</dt><dd>{version.created_by}</dd></div><div><dt>绑定</dt><dd>{version.bindings.length || 0}</dd></div><div><dt>快照引用</dt><dd>{version.snapshot_references.length}</dd></div><div><dt>分镜引用</dt><dd>{version.shot_references.length}</dd></div></dl><details><summary>结构化属性</summary><pre>{JSON.stringify(version.attributes, null, 2)}</pre></details></section>
              </div>
              <div className={styles.references}>
                <section><h3><Link2 />确认绑定</h3>{version.bindings.length ? version.bindings.map(item => <p key={item.id}><strong>{item.binding_type}</strong><span>{item.confirmed_by} · {item.status}</span></p>) : <p><span>没有绑定记录</span></p>}</section>
                <section><h3><Boxes />生产快照</h3>{version.snapshot_references.length ? version.snapshot_references.map(item => <p key={`${item.snapshot_id}-${item.role}`}><strong>#{item.snapshot_number} · {item.role}</strong><span>{item.snapshot_status}</span></p>) : <p><span>尚未冻结进生产快照</span></p>}</section>
                <section><h3><FileSearch />分镜合同</h3>{version.shot_references.length ? version.shot_references.map(item => <p key={`${item.shot_id}-${item.role}`}><strong>{item.shot_code} · {item.role}</strong><span>plan v{item.plan_version_number}</span></p>) : <p><span>尚未被分镜引用</span></p>}</section>
              </div>
            </article>)}</div>
          </>}
        </section>
      </div>
    </main>
  </>
}
