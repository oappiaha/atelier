import { useEffect } from 'react'
import { create } from 'zustand'
import type { DesignStatus } from './api'

interface BrowseState {
  layout: 'grid' | 'list'
  search: string
  sort: 'name' | 'recent' | 'number'
  status: 'all' | DesignStatus
  category: string
  catOpen: boolean
}
const defaults: BrowseState = { layout: 'grid', search: '', sort: 'number', status: 'all', category: 'all', catOpen: false }
// Bounded to visited archive routes for this session; never mix projects' filters.
const useBrowseStore = create<{ routes: Record<string, BrowseState> }>(() => ({ routes: {} }))
const positions = new Map<string, number>()
export function useBrowse(key: string, ready: boolean) {
  const state = useBrowseStore(s => s.routes[key] ?? defaults)
  const set = (patch: Partial<BrowseState>) => useBrowseStore.setState(s => ({ routes: { ...s.routes, [key]: { ...(s.routes[key] ?? defaults), ...patch } } }))
  useEffect(() => {
    if (!ready) return
    let armed = false
    const frame = requestAnimationFrame(() => {
      window.scrollTo(0, positions.get(key) ?? 0)
      armed = true
    })
    const remember = () => { if (armed) positions.set(key, window.scrollY) }
    window.addEventListener('scroll', remember)
    return () => { cancelAnimationFrame(frame); window.removeEventListener('scroll', remember) }
  }, [key, ready])
  return { ...state, set }
}
