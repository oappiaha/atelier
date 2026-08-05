import { useCallback, useEffect, useState } from 'react'
import type { Media } from '../lib/api'

/** Full-screen media lightbox, per the mock's .lightbox block.
 *  All slides stay mounted (stable keys); navigation only moves transforms. */
export default function Lightbox({
  items,
  index,
  phaseLabel,
  onClose,
  onStudioShot,
  studioShotBusy,
}: {
  items: Media[]
  index: number
  phaseLabel: string
  onClose: () => void
  /** When provided, images offer "STUDIO SHOT" — PhotoRoom presentation
   *  derivative (bg removed + soft shadow), ~$0.02. Hidden on media that IS
   *  already a photoroom derivative. */
  onStudioShot?: (m: Media) => void
  studioShotBusy?: boolean
}) {
  const [idx, setIdx] = useState(Math.min(index, items.length - 1))

  const go = useCallback(
    (d: number) => setIdx(i => Math.max(0, Math.min(items.length - 1, i + d))),
    [items.length],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') go(1)
      if (e.key === 'ArrowLeft') go(-1)
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [go, onClose])

  const cur = items[idx]
  if (!cur) return null

  return (
    <div className="lightbox open" id="lightbox">
      <div className="lb-top">
        <div className="mono" style={{ fontSize: 9.5, letterSpacing: '.14em' }} id="lb-phase">
          {phaseLabel.toUpperCase()}
        </div>
        <button
          className="press"
          aria-label="Close"
          style={{
            width: 36, height: 36, borderRadius: '50%', background: 'rgba(255,255,255,.12)',
            color: '#FFF', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      <div className="lb-stage" id="lb-stage">
        {items.map((m, i) => {
          const off = i - idx
          return (
            <div
              key={m.id}
              className="lb-img"
              style={{
                backgroundImage: `url(${m.url})`,
                transform: `translateX(${off * 104}%) scale(${off === 0 ? 1 : 0.9})`,
                opacity: Math.abs(off) > 1 ? 0 : 1,
              }}
            />
          )
        })}
        <div className="lb-edge l" onClick={() => go(-1)} />
        <div className="lb-edge r" onClick={() => go(1)} />
      </div>
      <div className="lb-meta">
        <div style={{ fontSize: 13 }} id="lb-cap">{cur.caption ?? '—'}</div>
        <div className="lb-src" id="lb-src">
          {cur.source_url ?? cur.source_app ?? cur.kind}
        </div>
        <div className="mono" style={{ fontSize: 9.5, color: 'rgba(255,255,255,.42)', marginTop: 8 }}>
          <span id="lb-idx">{idx + 1}</span> / <span id="lb-total">{items.length}</span> · swipe or tap edges
        </div>
        {onStudioShot && cur.kind === 'image' && cur.source_app !== 'photoroom' && (
          <button
            className="lb-studio mono press"
            id="lb-studio-shot"
            disabled={studioShotBusy}
            title="PhotoRoom: background removed + AI soft shadow, saved to the timeline (~$0.02)"
            onClick={() => onStudioShot(cur)}
          >
            {studioShotBusy ? 'SHOOTING…' : '◐ STUDIO SHOT · ~$0.02'}
          </button>
        )}
      </div>
    </div>
  )
}
