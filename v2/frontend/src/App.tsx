import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { ProductionPage } from './pages/ProductionPage'
import { ProjectPage } from './pages/ProjectPage'
import { PlanPage } from './pages/PlanPage'
import { SettingsPage } from './pages/SettingsPage'
import { ReviewPage } from './pages/ReviewPage'
import { ContactSheetPage } from './pages/ContactSheetPage'
import { EditorPage } from './pages/EditorPage'
import { ProjectControlPage } from './pages/ProjectControlPage'
import { AssetLibraryPage } from './pages/AssetLibraryPage'

export function App() {
  return <Routes><Route element={<AppShell />}><Route index element={<DashboardPage />} /><Route path="projects/:projectId" element={<ProjectPage />} /><Route path="projects/:projectId/control" element={<ProjectControlPage />} /><Route path="projects/:projectId/plan" element={<PlanPage />} /><Route path="projects/:projectId/contact-sheet" element={<ContactSheetPage />} /><Route path="production" element={<ProductionPage />} /><Route path="review" element={<ReviewPage />} /><Route path="editor" element={<EditorPage />} /><Route path="library" element={<AssetLibraryPage />} /><Route path="settings" element={<SettingsPage />} /></Route></Routes>
}
