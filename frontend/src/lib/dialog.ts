import { useLayoutEffect, useRef } from 'react'

/** Shared keyboard ownership for sheets. No synthetic clicks: Escape calls
 * the same close handler as the visible close control. */
export function useDialog(id: string, open: boolean, close: () => void) {
  const closer = useRef(close)
  useLayoutEffect(() => { closer.current = close })
  useLayoutEffect(() => {
    if (!open) return
    const root = document.getElementById(id)
    if (!root) return
    const previous = document.activeElement as HTMLElement | null
    const focusable = () => Array.from(root.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]')).filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
    const timer = window.setTimeout(() => {
      if (!root.contains(document.activeElement)) focusable()[0]?.focus()
    }, 100)
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); closer.current() }
      if (e.key === 'Tab') {
        const els = focusable()
        const first = els[0], last = els[els.length - 1]
        if (e.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) { e.preventDefault(); last?.focus() }
        else if (!e.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) { e.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', key)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('keydown', key)
      previous?.focus({ preventScroll: true })
    }
  }, [id, open])
}
