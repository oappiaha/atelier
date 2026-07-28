// WADA STUDIO — Palette detail (M8, PRD W3 "browsable" + TDD §9
// GET /palettes/{id} "Detail + similar"). Full swatch anatomy (name, hex,
// CIELAB, hue family, chroma), palette-level facts (temperature, mean L*,
// mean chroma, ΔE spread, Wada volume/plate), the 6 nearest same-count
// palettes by mean-Lab distance, and USE IN STUDY (PATCHes the draft in
// place — the same seam the composer rail uses).
// Shortlist/favourites: §2 has no palette-favourite table and §9 no route —
// deliberately NOT invented here (documented M8 skip).
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchPaletteDetail, type Palette } from '../lib/api'

export default function PaletteDetail({
  paletteId, activeId, frozen, busy, onUse, onClose,
}: {
  paletteId: string
  activeId: string | undefined // the study's current palette
  frozen: boolean
  busy: boolean
  onUse: (id: string) => void
  onClose: () => void
}) {
  // similar-palette taps navigate WITHIN the sheet
  const [shownId, setShownId] = useState(paletteId)
  const q = useQuery({
    queryKey: ['palette-detail', shownId],
    queryFn: () => fetchPaletteDetail(shownId),
  })
  const p = q.data
  const isActive = shownId === activeId

  return (
    <div className="cwlb" id="pal-detail" onClick={onClose}>
      <div className="cwlb-body pald-body" onClick={e => e.stopPropagation()}>
        {!p ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: 24 }}>
            {q.isError ? 'Could not load this palette.' : 'Loading palette…'}
          </div>
        ) : (
          <div className="pald-meta">
            <div className="eyebrow" style={{ marginBottom: 2 }}>
              SANZO WADA · #{p.id}{p.volume != null && ` · VOL ${p.volume}`}{p.plate != null && ` · PLATE ${p.plate}`}
            </div>
            <div className="syne" id="pald-name" style={{ fontSize: 21, fontWeight: 800 }}>{p.name}</div>
            <div className="mono" id="pald-facts" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--fog)', marginTop: 6, lineHeight: 1.9 }}>
              {p.color_count}-COLOUR · {p.temperature.toUpperCase()} · MEAN L* {p.mean_lab_l.toFixed(0)} · MEAN CHROMA {p.mean_chroma.toFixed(0)}
              <br />ΔE SPREAD {p.min_delta_e.toFixed(1)}–{p.max_delta_e.toFixed(1)}
            </div>

            {/* full swatches — the anatomy the rail's 4 blocks can't show */}
            <div className="pald-swatches" id="pald-swatches">
              {p.colors.map(c => (
                <div key={c.id} className="pald-sw" data-sw={c.id}>
                  <span className="pald-chip" style={{ background: c.hex }} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block', fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {c.name}
                    </span>
                    <span className="mono" style={{ display: 'block', fontSize: 8.5, color: 'var(--faint)', marginTop: 1 }}>
                      {c.hex.toUpperCase()} · {c.hue_family.toUpperCase()}
                    </span>
                  </span>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--fog)', textAlign: 'right', lineHeight: 1.6, flexShrink: 0 }}>
                    L* {c.lab_l.toFixed(0)} a {c.lab_a.toFixed(0)} b {c.lab_b.toFixed(0)}
                    <br />C {c.chroma.toFixed(0)} · H {c.hue_deg.toFixed(0)}°
                  </span>
                </div>
              ))}
            </div>

            {/* similar: nearest mean-Lab, same colour count (§9) */}
            <div className="eyebrow" style={{ margin: '14px 0 6px' }}>SIMILAR PALETTES</div>
            <div className="pald-similar" id="pald-similar">
              {p.similar.map(s => (
                <SimilarCard key={s.palette.id} p={s.palette} distance={s.distance} onOpen={() => setShownId(s.palette.id)} />
              ))}
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
              <button
                className={`kbtn gc-open${isActive ? ' latched' : ''}`}
                id="pald-use"
                disabled={busy || isActive}
                onClick={() => {
                  if (frozen || isActive) return
                  onUse(p.id)
                  onClose()
                }}
              >
                {isActive ? '✓ IN USE' : frozen ? 'STUDY FROZEN' : busy ? '…' : 'USE IN STUDY'}
              </button>
              <button className="kbtn gc-cancel" id="pald-close" onClick={onClose}>CLOSE</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function SimilarCard({ p, distance, onOpen }: { p: Palette; distance: number; onOpen: () => void }) {
  return (
    <button className="pald-simcard press" data-sim={p.id} onClick={onOpen}>
      <span className="pal-blocks" style={{ height: 26 }}>
        {p.colors.map(c => (
          <span key={c.id} style={{ background: c.hex, flex: 1 }} />
        ))}
      </span>
      <span className="pal-name" style={{ display: 'block' }}>{p.name}</span>
      <span className="pal-id" style={{ display: 'block' }}>#{p.id} · ΔLab {distance.toFixed(1)}</span>
    </button>
  )
}
