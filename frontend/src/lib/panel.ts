import { useLayoutEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { create } from 'zustand'

export interface PanelAction {
  primary?: boolean
  label: string
  run?: () => void
  disabled?: boolean
  children?: PanelAction[]
}
export const usePanelState = create<{ route: string; actions: PanelAction[] }>(() => ({ route: '', actions: [] }))

// Views own their handlers and loading state; only the panel subscribes to this
// registration, so local edits cannot cause a shell/view render loop.
export function usePanel(actions: PanelAction[]) {
  const location = useLocation()
  useLayoutEffect(() => {
    usePanelState.setState({ route: location.key, actions })
  })
}

export function useJourney() {
  const location = useLocation()
  const navigate = useNavigate()
  const origin = location.state?.origin as { to: string; label: string; scroll: number } | undefined
  const valid = origin && /^\/(gallery(?:\?|$)|p\/[^/?]+$|studies$|inbox$)/.test(origin.to)
  return {
    state: location.state,
    openDesign: (id: string, label: string) => navigate(`/d/${id}`, {
      state: { origin: { to: location.pathname + location.search, label, scroll: window.scrollY } },
    }),
    backLabel: valid ? origin.label : null,
    back: (projectId?: string) => valid
      ? navigate(origin.to, { state: { restoreScroll: origin.scroll } })
      : navigate(projectId ? `/p/${projectId}` : '/'),
  }
}
