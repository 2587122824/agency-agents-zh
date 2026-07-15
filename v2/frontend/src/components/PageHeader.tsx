import type { ReactNode } from 'react'
import styles from './PageHeader.module.css'

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: ReactNode }) {
  return <header className={styles.header}><div><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{actions && <aside>{actions}</aside>}</header>
}
