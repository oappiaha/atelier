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

export const useCapture = create<CaptureState>((set, get) => ({
  open: false,
  designCtx: null,
  dest: 'inbox',
  openCapture: () => set({ open: true, dest: get().designCtx ? 'design' : 'inbox' }),
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
