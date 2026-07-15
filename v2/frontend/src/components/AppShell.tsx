import {
  Boxes,
  Clapperboard,
  Library,
  ListVideo,
  MessageSquareText,
  ScanEye,
  Settings2,
  Workflow,
} from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'

import styles from './AppShell.module.css'

const nav = [
  { to: '/', label: '创作台', icon: MessageSquareText },
  { to: '/production', label: '生产队列', icon: Boxes },
  { to: '/review', label: '素材审核', icon: ScanEye },
  { to: '/editor', label: '剪辑台', icon: ListVideo },
  { to: '/library', label: '资产库', icon: Library },
  { to: '/settings', label: '系统配置', icon: Settings2 },
]

export function AppShell() {
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span><Clapperboard size={18} /></span>
          <div><strong>片场</strong><small>模块化 V2</small></div>
        </div>
        <nav>
          <p>工作区</p>
          {nav.slice(0, 4).map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => isActive ? styles.active : undefined}>
              <Icon size={16} /><span>{label}</span>
            </NavLink>
          ))}
          <p>资源</p>
          {nav.slice(4).map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => isActive ? styles.active : undefined}>
              <Icon size={16} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className={styles.boundary}>
          <Workflow size={16} />
          <div><strong>V1 完全隔离</strong><small>未连接生产供应商</small></div>
        </div>
      </aside>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  )
}
