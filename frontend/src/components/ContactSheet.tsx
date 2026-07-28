// WADA STUDIO — Contact Sheet (M7-T3, TDD §8.13.2 + PRD §4).
// The organising view for generated output: colorway cards with fingerprint
// chips in canonical slot order, live status while the trie executor runs
// (poll ~3s — §8.13's SSE is unimplemented; polling is the M7 contract),
// ghost cards for deferred permutations ("$0.27 at a time"), pin winners.
// Pins persist client-side until §9's POST /colorways/{id}/pin exists (M8).
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  apiErrorDetail, fetchColorways, generateOne, generateStudy, loadPins,
  savePins, CENTS_PER_CALL, type Colorway, type ColorwaysOut,
} from '../lib/api'
import { toast } from '../lib/store'

const cents = (n: number) => `$${(n / 100).toFixed(2)}`

/** Conservative per-ghost price tag: one call per chain step (= K slots),
 *  no cache discount — the same shape as the backend's enqueue gate. */
const ghostCents = (cw: Colorway) => Math.round(cw.mapping.length * CENTS_PER_CALL)

export default function ContactSheet({ studyId }: { studyId: string }) {
  const qc = useQueryClient()
  const sheetQ = useQuery({
    queryKey: ['colorways', studyId],
    queryFn: () => fetchColorways(studyId),
    refetchInterval: q => {
      const d = q.state.data as ColorwaysOut | undefined
      if (!d) return false
      const busy = d.study_status === 'generating'
        || d.colorways.some(c => c.status === 'generating')
      return busy ? 3000 : false
    },
  })

  const [pins, setPins] = useState<string[]>(() => loadPins(studyId))
  useEffect(() => setPins(loadPins(studyId)), [studyId])
  const togglePin = (id: string) => {
    setPins(prev => {
      const next = prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id]
      savePins(studyId, next)
      return next
    })
  }

  const [confirmRemaining, setConfirmRemaining] = useState(false)
  const [detail, setDetail] = useState<Colorway | null>(null)

  const refetchSoon = () => {
    qc.invalidateQueries({ queryKey: ['colorways', studyId] })
    qc.invalidateQueries({ queryKey: ['study', studyId] })
  }

  const genRemaining = useMutation({
    mutationFn: () => generateStudy(studyId, { all: true }),
    onSuccess: out => {
      setConfirmRemaining(false)
      toast(out.requested.length
        ? `Generating ${out.requested.length} colorways · ≤${cents(out.estimated_cents)}`
        : 'Nothing left to generate')
      refetchSoon()
    },
    onError: e => {
      setConfirmRemaining(false)
      toast(apiErrorDetail(e, 'Could not start generation'))
    },
  })

  const genOne = useMutation({
    mutationFn: (colorwayId: string) => generateOne(studyId, colorwayId),
    onSuccess: out => {
      toast(`Generating this one · ≤${cents(out.estimated_cents)}`)
      refetchSoon()
    },
    onError: e => toast(apiErrorDetail(e, 'Could not generate this colorway')),
  })

  const d = sheetQ.data
  const cws = useMemo(() => d?.colorways ?? [], [d])
  const remaining = useMemo(
    () => cws.filter(c => c.status === 'planned' || c.status === 'failed'),
    [cws],
  )
  const busy = !!d && (d.study_status === 'generating' || cws.some(c => c.status === 'generating'))
  const remainingMax = remaining.reduce((s, c) => s + ghostCents(c), 0)

  if (sheetQ.isError) {
    return (
      <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
        Could not load the contact sheet.
      </div>
    )
  }
  if (!d) {
    return (
      <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
        Loading contact sheet…
      </div>
    )
  }

  return (
    <div className="cw-sheet rise">
      <div className="cw-head">
        <div className="mono" id="cw-counts" style={{ fontSize: 9.5, letterSpacing: '.1em', color: 'var(--fog)' }}>
          {d.ready}/{d.planned} READY · SPENT {cents(d.actual_cost_cents)}
          {busy && <span className="cw-live" id="cw-live"> · GENERATING…</span>}
        </div>
        {remaining.length > 0 && !busy && (
          confirmRemaining ? (
            <div className="gen-confirm" id="gen-confirm-remaining">
              <span className="mono" style={{ fontSize: 9.5 }}>
                {remaining.length} colorway{remaining.length === 1 ? '' : 's'} · ≤{cents(remainingMax)} before cache
              </span>
              <button className="kbtn gc-cancel" id="gen-remaining-cancel" onClick={() => setConfirmRemaining(false)}>
                CANCEL
              </button>
              <button className="kbtn gc-go" id="gen-remaining-go" disabled={genRemaining.isPending} onClick={() => genRemaining.mutate()}>
                {genRemaining.isPending ? '…' : `GENERATE · ≤${cents(remainingMax)}`}
              </button>
            </div>
          ) : (
            <button className="kbtn gc-open press" id="gen-remaining" onClick={() => setConfirmRemaining(true)}>
              GENERATE REMAINING {remaining.length}
            </button>
          )
        )}
      </div>

      <div className="cw-grid" id="cw-grid">
        {cws.map(cw => {
          const pinned = pins.includes(cw.id)
          const img = cw.thumb_url ?? cw.image_url
          const isGhost = cw.status === 'planned'
          return (
            <div
              key={cw.id}
              className={`cw-card${isGhost ? ' ghost' : ''}${pinned ? ' pinned' : ''}`}
              data-cw={cw.permutation_idx}
              data-cw-status={cw.status}
            >
              {isGhost ? (
                <button
                  className="cw-ghostbody press"
                  data-cw-gen={cw.permutation_idx}
                  disabled={busy || genOne.isPending}
                  onClick={() => genOne.mutate(cw.id)}
                  title="Generate just this permutation"
                >
                  <span className="mono" style={{ fontSize: 8.5, letterSpacing: '.1em', color: 'var(--faint)' }}>
                    NOT GENERATED
                  </span>
                  <span className="mono" style={{ fontSize: 10.5, marginTop: 6 }}>
                    ~{cents(ghostCents(cw))}
                  </span>
                  <span className="cw-ghostcta mono">
                    {busy ? 'WAITING…' : 'GENERATE THIS ONE'}
                  </span>
                </button>
              ) : (
                <button
                  className="cw-imgwrap press"
                  data-cw-open={cw.permutation_idx}
                  onClick={() => (cw.status === 'ready' || cw.status === 'pinned') && setDetail(cw)}
                >
                  {img && (cw.status === 'ready' || cw.status === 'pinned') ? (
                    <img className="cw-img" src={img} alt={`colorway ${cw.permutation_idx}`} loading="lazy" />
                  ) : (
                    <span className={`cw-holding${cw.status === 'generating' ? ' pulse' : ''} mono`}>
                      {cw.status === 'generating' ? 'PAINTING…' : cw.status === 'failed' ? (cw.error ?? 'FAILED') : cw.status.toUpperCase()}
                    </span>
                  )}
                </button>
              )}
              {/* the fingerprint — ALWAYS canonical slot order (§8.13.2) */}
              <div className="cw-fp" data-cw-fp={cw.permutation_idx}>
                {cw.mapping.map(chip => (
                  <span
                    key={chip.slot_idx}
                    className="cw-chip"
                    style={{ background: chip.hex }}
                    title={`${chip.slot_label} = ${chip.name}`}
                  />
                ))}
              </div>
              <div className="cw-meta">
                <span className={`cw-st ${cw.status}`} data-cw-st={cw.permutation_idx}>
                  {pinned ? 'PINNED' : cw.status.toUpperCase()}
                </span>
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--faint)' }}>
                  #{cw.permutation_idx}{cw.status === 'ready' || cw.status === 'pinned'
                    ? ` · ${cents(cw.cost_cents)}${cw.cache_hits ? ` · ${cw.cache_hits}⚡` : ''}`
                    : ''}
                </span>
                {(cw.status === 'ready' || cw.status === 'pinned') && (
                  <button
                    className={`cw-pin press${pinned ? ' on' : ''}`}
                    data-cw-pin={cw.permutation_idx}
                    aria-label={pinned ? 'Unpin' : 'Pin'}
                    onClick={() => togglePin(cw.id)}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill={pinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2">
                      <path d="M12 17l-5.878 3.09 1.123-6.545L2.489 9.91l6.572-.955L12 3l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {detail && (
        <div className="cwlb" id="cw-lightbox" onClick={() => setDetail(null)}>
          <div className="cwlb-body" onClick={e => e.stopPropagation()}>
            {detail.image_url && <img className="cwlb-img" src={detail.image_url} alt={`colorway ${detail.permutation_idx}`} />}
            <div className="cwlb-meta">
              <div className="cw-fp" style={{ padding: 0 }}>
                {detail.mapping.map(chip => (
                  <span key={chip.slot_idx} className="cw-chip lg" style={{ background: chip.hex }} title={`${chip.slot_label} = ${chip.name}`} />
                ))}
              </div>
              <div style={{ fontSize: 12.5, marginTop: 8, lineHeight: 1.7 }}>
                {detail.mapping.map(chip => (
                  <span key={chip.slot_idx} style={{ display: 'block' }}>
                    <span className="mono" style={{ color: 'var(--faint)', fontSize: 9 }}>{chip.slot_label.toUpperCase()}</span>
                    {' '}{chip.name}
                  </span>
                ))}
              </div>
              <div className="mono" id="cwlb-info" style={{ fontSize: 9, color: 'var(--faint)', marginTop: 10, lineHeight: 1.8, letterSpacing: '.05em' }}>
                #{detail.permutation_idx} · {cents(detail.cost_cents)} · {detail.cache_hits} cache hit{detail.cache_hits === 1 ? '' : 's'}
                <br />
                boundary lock {detail.lock_verified ? 'VERIFIED — untouched regions byte-identical' : detail.lock_verified === false ? 'FAILED' : '—'}
                {detail.latency_ms != null && <><br />{(detail.latency_ms / 1000).toFixed(1)}s to paint</>}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <button
                  className={`kbtn gc-open${pins.includes(detail.id) ? ' latched' : ''}`}
                  id="cwlb-pin"
                  onClick={() => togglePin(detail.id)}
                >
                  {pins.includes(detail.id) ? '★ PINNED' : '☆ PIN'}
                </button>
                <button className="kbtn gc-cancel" id="cwlb-close" onClick={() => setDetail(null)}>CLOSE</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
