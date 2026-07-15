import { Construction } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'

export function PlaceholderPage({ eyebrow, title, description, boundary }: { eyebrow: string; title: string; description: string; boundary: string }) {
  return <><PageHeader eyebrow={eyebrow} title={title} description={description} /><div className="placeholder"><Construction size={28} /><span>BOUNDARY READY</span><h2>模块边界已经预留</h2><p>{boundary}</p><div><b>当前状态</b><em>未接入业务实现</em></div></div></>
}
