// WADA STUDIO — Contact Sheet (M7-T3 → M8, TDD §8.13.2 + PRD §4).
// The organising view for generated output: colorway cards with fingerprint
// chips in canonical slot order, live status while the trie executor runs
// (poll ~3s — §8.13's SSE is unimplemented; polling is the M7 contract),
// ghost cards for deferred permutations ("$0.27 at a time").
// M8: pins are SERVER state (POST /colorways/{id}/pin copies the object to
// cw/pinned/ — lifecycle-protected, cross-device); T3's localStorage pins
// migrate to the API once on load. 2K export lives in the lightbox: free
// local upscale or paid Seedream re-paint, costs shown before confirming.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  apiErrorDetail, clearLegacyPins, exportColorway, fetchColorways,
  generateOne, generateStudy, pinColorway, readLegacyPins,
  rejectColorway, unpinColorway, unrejectColorway,
  CENTS_PER_CALL, EXPORT_REGEN_DOLLARS,
  type Colorway, type ColorwaysOut,
} from '../lib/api'
import { toast } from '../lib/store'

const cents = (n: number) => `$${(n / 100).toFixed(2)}`

/** Conservative per-ghost price tag: one call per chain step (= K slots),
 *  no cache discount — the same shape as the backend's enqueue gate. */
const ghostCents = (cw: Colorway) => Math.round(cw.mapping.length * CENTS_PER_CALL)

/** Presigned export URL carries content-disposition: attachment — navigating
 *  it downloads the PNG without leaving the app. */
const triggerDownload = (url: string) => {
  const a = document.createElement('a')
  a.href = url
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

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

  const [confirmRemaining, setConfirmRemaining] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [exportAsk, setExportAsk] = useState(false)
  const [showHidden, setShowHidden] = useState(false)

  const refetchSoon = () => {
    qc.invalidateQueries({ queryKey: ['colorways', studyId] })
    qc.invalidateQueries({ queryKey: ['study', studyId] })
  }

  // ── pins: server state (M8) ────────────────────────────────────────────────
  // Optimistic: the star flips the cache instantly (the POST also builds the
  // pin→timeline entry server-side, which takes seconds — the UI must not
  // wait on it). Rolled back on error; settled state comes from the refetch.
  const flipStatus = (id: string, status: Colorway['status']) => {
    qc.setQueryData<ColorwaysOut>(['colorways', studyId], old =>
      old && {
        ...old,
        colorways: old.colorways.map(c => (c.id === id ? { ...c, status } : c)),
      })
  }
  const pinM = useMutation({
    mutationFn: (cw: Colorway) =>
      cw.status === 'pinned' ? unpinColorway(cw.id) : pinColorway(cw.id),
    onMutate: async (cw: Colorway) => {
      await qc.cancelQueries({ queryKey: ['colorways', studyId] })
      const prev = qc.getQueryData<ColorwaysOut>(['colorways', studyId])
      flipStatus(cw.id, cw.status === 'pinned' ? 'ready' : 'pinned')
      return { prev }
    },
    onSuccess: out => {
      toast(out.status === 'pinned'
        ? 'Pinned — saved forever, added to the design timeline'
        : 'Unpinned')
    },
    onError: (e, _cw, ctx) => {
      if (ctx?.prev) qc.setQueryData(['colorways', studyId], ctx.prev)
      toast(apiErrorDetail(e, 'Could not update the pin'))
    },
    onSettled: () => refetchSoon(),
  })

  // one-time migration: T3's localStorage pins → the API (then clear the key)
  const migratedFor = useRef<string | null>(null)
  const d = sheetQ.data
  useEffect(() => {
    if (!d || migratedFor.current === studyId) return
    migratedFor.current = studyId
    const legacy = readLegacyPins(studyId)
    if (!legacy.length) return
    const targets = d.colorways.filter(
      c => legacy.includes(c.id) && c.status === 'ready',
    )
    if (!targets.length) {
      clearLegacyPins(studyId) // stale/already-migrated ids — nothing to sync
      return
    }
    Promise.allSettled(targets.map(c => pinColorway(c.id))).then(results => {
      if (results.every(r => r.status === 'fulfilled')) {
        clearLegacyPins(studyId)
        toast(`Migrated ${targets.length} pin${targets.length === 1 ? '' : 's'} to your account`)
      } else {
        toast('Some pins could not be migrated — will retry next visit')
      }
      qc.invalidateQueries({ queryKey: ['colorways', studyId] })
    })
  }, [d, studyId, qc])

  // ── reject / hide (SHIP-1): rejected cards collapse into the hidden
  //    section; unreject brings them back (ready, or planned for a ghost).
  //    A pinned colorway 422s server-side — unpin is the way back. ──────────
  const rejectM = useMutation({
    mutationFn: (cw: Colorway) =>
      cw.status === 'rejected' ? unrejectColorway(cw.id) : rejectColorway(cw.id),
    onMutate: async (cw: Colorway) => {
      await qc.cancelQueries({ queryKey: ['colorways', studyId] })
      const prev = qc.getQueryData<ColorwaysOut>(['colorways', studyId])
      flipStatus(cw.id, cw.status === 'rejected' ? 'ready' : 'rejected')
      if (cw.status !== 'rejected' && detailId === cw.id) {
        setDetailId(null)
        setExportAsk(false)
      }
      return { prev }
    },
    onSuccess: out => {
      toast(out.status === 'rejected' ? 'Hidden — moved to the shelf below' : 'Restored to the sheet')
    },
    onError: (e, _cw, ctx) => {
      if (ctx?.prev) qc.setQueryData(['colorways', studyId], ctx.prev)
      toast(apiErrorDetail(e, 'Could not update the colorway'))
    },
    onSettled: () => refetchSoon(),
  })

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

  // ── 2K export (M8, PRD W11): $0 upscale | ~$0.135 re-paint ────────────────
  const exportM = useMutation({
    mutationFn: ({ id, regenerate }: { id: string; regenerate: boolean }) =>
      exportColorway(id, regenerate),
    onSuccess: out => {
      setExportAsk(false)
      triggerDownload(out.download_url)
      toast(`2K PNG ready — ${out.width}×${out.height} · ${out.method === 'upscale' ? 'free' : cents(out.cost_cents)}`)
    },
    onError: e => toast(apiErrorDetail(e, '2K export failed')),
  })

  const cws = useMemo(() => d?.colorways ?? [], [d])
  // rejected colorways leave the working grid and collapse into "N hidden"
  const visible = useMemo(() => cws.filter(c => c.status !== 'rejected'), [cws])
  const hidden = useMemo(() => cws.filter(c => c.status === 'rejected'), [cws])
  const remaining = useMemo(
    () => cws.filter(c => c.status === 'planned' || c.status === 'failed'),
    [cws],
  )
  const busy = !!d && (d.study_status === 'generating' || cws.some(c => c.status === 'generating'))
  const remainingMax = remaining.reduce((s, c) => s + ghostCents(c), 0)
  // the lightbox always renders the LIVE row (pin/export update underneath it)
  const detail = detailId ? cws.find(c => c.id === detailId) ?? null : null

  // Esc closes the lightbox (backdrop click already does)
  useEffect(() => {
    if (!detailId) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDetailId(null)
        setExportAsk(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [detailId])

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
        {visible.map(cw => {
          const pinned = cw.status === 'pinned'
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
                  onClick={() => (cw.status === 'ready' || cw.status === 'pinned') && setDetailId(cw.id)}
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
                  {cw.status.toUpperCase()}
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
                    onClick={() => pinM.mutate(cw)}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill={pinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2">
                      <path d="M12 17l-5.878 3.09 1.123-6.545L2.489 9.91l6.572-.955L12 3l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z" />
                    </svg>
                  </button>
                )}
                {cw.status === 'ready' && (
                  <button
                    className="cw-pin cw-hide press"
                    data-cw-hide={cw.permutation_idx}
                    aria-label="Hide (reject)"
                    title="Hide this colorway"
                    onClick={() => rejectM.mutate(cw)}
                  >
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <path d="M6 6l12 12M18 6L6 18" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* ── hidden shelf: rejected colorways, collapsed to one row ── */}
      {hidden.length > 0 && (
        <div className="cw-hidden" id="cw-hidden">
          <button
            className="cw-hidden-toggle press mono"
            id="cw-hidden-toggle"
            onClick={() => setShowHidden(h => !h)}
          >
            <span>{hidden.length} HIDDEN</span>
            <svg
              width="10" height="10" viewBox="0 0 10 10" fill="none"
              style={{ transform: showHidden ? 'rotate(180deg)' : 'none', transition: 'transform .2s ease' }}
            >
              <path d="M2.5 4l2.5 2.5L7.5 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          {showHidden && (
            <div className="cw-grid cw-grid-hidden" id="cw-hidden-grid">
              {hidden.map(cw => {
                const img = cw.thumb_url ?? cw.image_url
                return (
                  <div key={cw.id} className="cw-card rejected" data-cw={cw.permutation_idx} data-cw-status="rejected">
                    <div className="cw-imgwrap">
                      {img ? (
                        <img className="cw-img" src={img} alt={`colorway ${cw.permutation_idx}`} loading="lazy" />
                      ) : (
                        <span className="cw-holding mono">NEVER GENERATED</span>
                      )}
                    </div>
                    <div className="cw-fp" data-cw-fp={cw.permutation_idx}>
                      {cw.mapping.map(chip => (
                        <span key={chip.slot_idx} className="cw-chip" style={{ background: chip.hex }} title={`${chip.slot_label} = ${chip.name}`} />
                      ))}
                    </div>
                    <div className="cw-meta">
                      <span className="cw-st rejected" data-cw-st={cw.permutation_idx}>REJECTED</span>
                      <button
                        className="kbtn gc-open cw-unreject press"
                        data-cw-unreject={cw.permutation_idx}
                        disabled={rejectM.isPending}
                        onClick={() => rejectM.mutate(cw)}
                      >
                        {rejectM.isPending ? '…' : 'UN-REJECT'}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* portaled to <body>: any transformed ancestor (e.g. a .rise entry
          animation) would otherwise become the containing block for this
          fixed overlay and pin it inside the sheet instead of the viewport */}
      {detail && createPortal(
        <div className="cwlb" id="cw-lightbox" onClick={() => { setDetailId(null); setExportAsk(false) }}>
          <div className="cwlb-body" onClick={e => e.stopPropagation()}>
            <button
              className="cwlb-close press"
              id="cwlb-close"
              aria-label="Close"
              title="Close"
              onClick={() => { setDetailId(null); setExportAsk(false) }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
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
                {detail.status === 'pinned' && <><br />PINNED — protected from the 30-day expiry</>}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                <button
                  className={`kbtn gc-open${detail.status === 'pinned' ? ' latched' : ''}`}
                  id="cwlb-pin"
                  onClick={() => pinM.mutate(detail)}
                >
                  {detail.status === 'pinned' ? '★ PINNED' : '☆ PIN'}
                </button>
                <button
                  className="kbtn gc-open"
                  id="cwlb-export"
                  disabled={exportM.isPending}
                  onClick={() => setExportAsk(a => !a)}
                >
                  EXPORT 2K
                </button>
                {detail.status === 'ready' && (
                  <button
                    className="iconbtn press"
                    id="cwlb-hide"
                    aria-label="Hide colorway"
                    title="Hide from the sheet (reversible)"
                    disabled={rejectM.isPending}
                    onClick={() => rejectM.mutate(detail)}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M17.94 17.94A10.4 10.4 0 0 1 12 19c-7 0-10.5-7-10.5-7a19.8 19.8 0 0 1 4.62-5.56M9.9 5.24A9.3 9.3 0 0 1 12 5c7 0 10.5 7 10.5 7a19.9 19.9 0 0 1-2.85 3.95" />
                      <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
                      <path d="M2 2l20 20" />
                    </svg>
                  </button>
                )}
              </div>
              {(exportAsk || exportM.isPending) && (
                <div className="export-ask" id="export-ask">
                  {exportM.isPending ? (
                    <div className="mono export-busy" id="export-busy" style={{ fontSize: 9.5, letterSpacing: '.08em' }}>
                      {exportM.variables?.regenerate ? 'RE-PAINTING AT 2048 — ONE MODEL CALL, ~40S…' : 'UPSCALING TO 2048…'}
                    </div>
                  ) : (
                    <>
                      <div className="mono" style={{ fontSize: 8.5, letterSpacing: '.1em', color: 'var(--faint)' }}>
                        2048PX PNG — PICK A PATH
                      </div>
                      <button
                        className="export-opt press"
                        id="export-upscale"
                        onClick={() => exportM.mutate({ id: detail.id, regenerate: false })}
                      >
                        <span>Local upscale <span className="mono" style={{ fontSize: 8.5, color: 'var(--faint)' }}>LANCZOS FROM 1536</span></span>
                        <b className="mono">FREE · $0</b>
                      </button>
                      <button
                        className="export-opt press"
                        id="export-regen"
                        onClick={() => exportM.mutate({ id: detail.id, regenerate: true })}
                      >
                        <span>Re-paint at 2048 <span className="mono" style={{ fontSize: 8.5, color: 'var(--faint)' }}>ONE SEEDREAM CALL</span></span>
                        <b className="mono">~${EXPORT_REGEN_DOLLARS}</b>
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
