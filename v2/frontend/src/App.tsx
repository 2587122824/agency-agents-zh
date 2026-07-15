import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { ProductionPage } from './pages/ProductionPage'
import { ProjectPage } from './pages/ProjectPage'
import { PlanPage } from './pages/PlanPage'

export function App() {
  return <Routes><Route element={<AppShell />}><Route index element={<DashboardPage />} /><Route path="projects/:projectId" element={<ProjectPage />} /><Route path="projects/:projectId/plan" element={<PlanPage />} /><Route path="production" element={<ProductionPage />} /><Route path="review" element={<PlaceholderPage eyebrow="REVIEW" title="素材审核" description="质量结论和用户选择将在这里汇合。" boundary="QC 只报告 passed、review_required、blocked；重试必须由用户选中素材后明确提交。" />} /><Route path="editor" element={<PlaceholderPage eyebrow="EDITOR" title="剪辑台" description="只读取审核通过的素材和精确引用。" boundary="时间线合同不会猜测素材 ID，也不会自动替换缺失片段。" />} /><Route path="assets" element={<PlaceholderPage eyebrow="ENTITY REGISTRY" title="资产库" description="人物、服装、场景和声音将使用类型化实体管理。" boundary="每个实体具有稳定 ID、版本和来源，镜头合同只传实体引用。" />} /><Route path="settings" element={<PlaceholderPage eyebrow="SYSTEM AUTHORITY" title="系统配置" description="供应商和工作流配置独立于项目合同。" boundary="后续接入凭据时只保存在后端，不进入浏览器缓存或项目快照。" />} /></Route></Routes>
}
