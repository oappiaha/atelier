import { create } from 'zustand'

// ── toast ────────────────────────────────────────────────────────────────────

interface ToastState {
  msg: string
  visible: boolean
  toast: (msg: string) => void
  _timer: ReturnType<typeof setTimeout> | null
}

export const useToast = create<ToastState>((set, get) => ({
  msg: '',
  visible: false,
  _timer: null,
  toast: (msg: string) => {
    const t = get()._timer
    if (t) clearTimeout(t)
    set({
      msg,
      visible: true,
      _timer: setTimeout(() => set({ visible: false }), 1900),
    })
  },
}))

export const toast = (msg: string) => useToast.getState().toast(msg)

// ── sheet stacking guard ─────────────────────────────────────────────────────
// Only one bottom sheet may be open at a time: every open* first closes the
// others (M5 backlog one-liner). Sheets self-register their closer here.

const sheetClosers = new Map<string, () => void>()
export const registerSheet = (name: string, close: () => void) => {
  sheetClosers.set(name, close)
}
const closeOtherSheets = (except: string) => {
  for (const [name, close] of sheetClosers) if (name !== except) close()
}

// ── capture sheet ────────────────────────────────────────────────────────────

export interface DesignCtx {
  id: string
  name: string
}

interface CaptureState {
  open: boolean
  /** design the user is currently looking at (set by the Design view) */
  designCtx: DesignCtx | null
  /** where captures land: the current design, or the Inbox */
  dest: 'design' | 'inbox'
  openCapture: () => void
  closeCapture: () => void
  toggleDest: () => void
  setDesignCtx: (ctx: DesignCtx | null) => void
}

// ── share sheet (M3) ─────────────────────────────────────────────────────────

export interface ShareTarget {
  kind: 'project' | 'design'
  id: string
  name: string
}

interface ShareState {
  open: boolean
  target: ShareTarget | null
  openShare: (target: ShareTarget) => void
  closeShare: () => void
}

export const useShare = create<ShareState>(set => ({
  open: false,
  target: null,
  openShare: target => {
    closeOtherSheets('share')
    set({ open: true, target })
  },
  closeShare: () => set({ open: false }),
}))
registerSheet('share', () => useShare.getState().closeShare())

// ── new-design sheet (M4) ────────────────────────────────────────────────────

export interface ProjectCtx {
  id: string
  name: string
}

interface NewDesignState {
  open: boolean
  /** project the design will be created in (set by the Project view) */
  project: ProjectCtx | null
  openNewDesign: (project: ProjectCtx) => void
  closeNewDesign: () => void
}

export const useNewDesign = create<NewDesignState>(set => ({
  open: false,
  project: null,
  openNewDesign: project => {
    closeOtherSheets('newDesign')
    set({ open: true, project })
  },
  closeNewDesign: () => set({ open: false }),
}))
registerSheet('newDesign', () => useNewDesign.getState().closeNewDesign())

export const useCapture = create<CaptureState>((set, get) => ({
  open: false,
  designCtx: null,
  dest: 'inbox',
  openCapture: () => {
    closeOtherSheets('capture')
    set({ open: true, dest: get().designCtx ? 'design' : 'inbox' })
  },
  closeCapture: () => set({ open: false }),
  toggleDest: () => {
    const { designCtx, dest } = get()
    if (!designCtx) {
      toast('Open a design to capture into it — going to Inbox')
      return
    }
    set({ dest: dest === 'design' ? 'inbox' : 'design' })
  },
  setDesignCtx: (ctx: DesignCtx | null) =>
    set(s => ({ designCtx: ctx, dest: ctx ? s.dest : 'inbox' })),
}))
registerSheet('capture', () => useCapture.getState().closeCapture())
