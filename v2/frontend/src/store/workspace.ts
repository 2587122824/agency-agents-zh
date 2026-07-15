import { create } from 'zustand'

interface WorkspaceState {
  currentProjectId: string | null
  setCurrentProjectId: (id: string | null) => void
}

export const useWorkspace = create<WorkspaceState>((set) => ({
  currentProjectId: null,
  setCurrentProjectId: (currentProjectId) => set({ currentProjectId }),
}))
