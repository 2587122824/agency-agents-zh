import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { ProductionPage } from './pages/ProductionPage'
import { ProjectPage } from './pages/ProjectPage'
import { PlanPage } from './pages/PlanPage'
import { SettingsPage } from './pages/SettingsPage'
import { ReviewPage } from './pages/ReviewPage'
import { EditorPage } from './pages/EditorPage'

export function App() {
  return <Routes><Route element={<AppShell />}><Route index element={<DashboardPage />} /><Route path="projects/:projectId" element={<ProjectPage />} /><Route path="projects/:projectId/plan" element={<PlanPage />} /><Route path="production" element={<ProductionPage />} /><Route path="review" element={<ReviewPage />} /><Route path="editor" element={<EditorPage />} /><Route path="assets" element={<PlaceholderPage eyebrow="ENTITY REGISTRY" title="资产库" description="人物、服装、场景和声音将使用类型化实体管理。" boundary="每个实体具有稳定 ID、版本和来源，镜头合同只传实体引用。" />} /><Route path="settings" element={<SettingsPage />} /></Route></Routes>
}
