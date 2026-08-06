// WADA STUDIO — Slot Composer + Contact Sheet (M5-T3 → M7-T3, PRD §4.2 +
// TDD §8.13/§10.1). Paint the model-found regions into slots, pick a Sanzo
// palette, read the live estimate — then the generate key is REAL (M7):
// confirm with cost → POST /generate → the contact sheet fills live.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api, apiErrorDetail, duplicateStudy, estimateStudy, fetchPalette, fetchPalettes,
  fetchRegions, fetchStudy, generateStudy, patchStudy, putSlots,
  rememberStudy, rescanRegions, segmentMedia, MAX_PAINT_SLOTS,
  type Design as DesignT, type Estimate, type Media, type Palette,
  type Region, type SlotIn, type Study,
} from '../lib/api'
import { toast } from '../lib/store'
import { luminanceMaskSupported, toAlphaMask } from '../lib/alphaMask'
import ContactSheet from '../components/ContactSheet'
import PaletteDetail from '../components/PaletteDetail'
import ScanOverlay from '../components/ScanOverlay'

interface LocalSlot {
  label: string
  regionIds: string[]
  anchorColorId: number | null
}

const dedupeLabel = (regionIds: string[], regions: Region[], fallback: string): string => {
  const seen: string[] = []
  for (const id of regionIds) {
    const l = regions.find(r => r.id === id)?.label
    if (l && !seen.includes(l)) seen.push(l)
  }
  return (seen.join('+') || fallback).slice(0, 80)
}

const fmtEta = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`)

export default function Studio() {
  const { designId, studyId } = useParams<{ designId: string; studyId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  // ── server state ───────────────────────────────────────────────────────────
  const study = useQuery({
    queryKey: ['study', studyId],
    queryFn: () => fetchStudy(studyId!),
    enabled: !!studyId,
  })
  const design = useQuery({
    queryKey: ['design', designId],
    queryFn: () => api<DesignT>(`/designs/${designId}`),
    enabled: !!designId,
  })
  const media = useQuery({
    queryKey: ['media', designId],
    queryFn: () => api<Media[]>(`/designs/${designId}/media`),
    enabled: !!designId,
  })
  const baseMediaId = study.data?.base_media_id
  const regionsQ = useQuery({
    queryKey: ['regions', baseMediaId],
    queryFn: () => fetchRegions(baseMediaId!),
    enabled: !!baseMediaId,
  })
  const paletteQ = useQuery({
    queryKey: ['palette', study.data?.palette_id],
    queryFn: () => fetchPalette(study.data!.palette_id),
    enabled: !!study.data?.palette_id,
  })

  // palette picker facets
  const [countFacet, setCountFacet] = useState<number | null>(null)
  const [palQ, setPalQ] = useState('')
  const palettesQ = useQuery({
    queryKey: ['palettes', countFacet, palQ],
    queryFn: () => fetchPalettes({ count: countFacet ?? undefined, q: palQ || undefined }),
    // the Sanzo corpus is static (seeded once) — never refetch, keep for the session
    staleTime: Infinity,
    gcTime: 24 * 3600_000,
  })

  const photo = useMemo(
    () => (media.data ?? []).find(m => m.id === baseMediaId) ?? null,
    [media.data, baseMediaId],
  )
  const regions = useMemo(
    () => regionsQ.data ?? [],
    [regionsQ.data],
  )

  // ── local composer state ───────────────────────────────────────────────────
  const [slots, setSlots] = useState<LocalSlot[]>([])
  const [active, setActive] = useState(0)
  const [hoverRg, setHoverRg] = useState<string | null>(null)
  // touch "hover": the ◉ peek control on a region row pins that region's
  // highlight on the photo without painting it (no hover on touch screens)
  const [pinRg, setPinRg] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<Estimate | null>(null)
  const [maskDim, setMaskDim] = useState<{ w: number; h: number } | null>(null)
  const [loadedMasks, setLoadedMasks] = useState(0)

  // iOS/WebKit treats raster masks as ALPHA regardless of mask-mode; the
  // grayscale mask PNGs are fully opaque there ⇒ whole-photo blue wash.
  // Without luminance support, swap in canvas-converted alpha-mask data URLs.
  const lumOk = useMemo(() => luminanceMaskSupported(), [])
  const [alphaMasks, setAlphaMasks] = useState<Record<string, string>>({})
  const alphaStarted = useRef(new Set<string>())
  useEffect(() => {
    if (lumOk) return
    const regs = regionsQ.data ?? []
    for (const r of regs) {
      if (alphaStarted.current.has(r.id)) continue
      alphaStarted.current.add(r.id)
      toAlphaMask(r.id, r.mask_url)
        .then(url => setAlphaMasks(m => ({ ...m, [r.id]: url })))
        .catch(() => alphaStarted.current.delete(r.id)) // overlay is decorative — skip quietly
    }
  }, [lumOk, regionsQ.data])

  const editSeq = useRef(0) // bumps on every local edit
  const errorSeq = useRef(-1) // last edit seq whose save 422'd (don't retry-loop)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const hydratedFor = useRef<string | null>(null)
  const estimatedFor = useRef<string | null>(null)

  // compose ⇄ contact sheet: a generated study opens on its sheet (M7)
  const [mode, setMode] = useState<'compose' | 'sheet'>('compose')
  const [confirming, setConfirming] = useState(false)
  // palette-library detail sheet (M8, PRD W3)
  const [palDetailId, setPalDetailId] = useState<string | null>(null)

  // hydrate local slots from the server exactly once per study id
  useEffect(() => {
    const s = study.data
    if (!s || hydratedFor.current === s.id) return
    hydratedFor.current = s.id
    setSlots(
      s.slots
        .filter(sl => sl.kind === 'paint')
        .sort((a, b) => a.idx - b.idx)
        .map(sl => ({ label: sl.label, regionIds: [...sl.region_ids], anchorColorId: sl.anchor_color_id })),
    )
    setActive(0)
    setDirty(false)
    setEstimate(null)
    setMode(s.status === 'draft' ? 'compose' : 'sheet')
    setConfirming(false)
    if (designId) rememberStudy(designId, s.id)
  }, [study.data, designId])

  // ── estimate (POST — pure, no spend) ───────────────────────────────────────
  const estimateM = useMutation({
    mutationFn: () => estimateStudy(studyId!),
    onSuccess: out => setEstimate(out),
    onError: e => toast(apiErrorDetail(e, 'Estimate failed')),
  })
  const estimateRef = useRef(estimateM.mutate)
  estimateRef.current = estimateM.mutate

  // auto-estimate when a study with slots arrives
  useEffect(() => {
    const s = study.data
    if (!s || s.k === 0 || estimatedFor.current === s.id) return
    estimatedFor.current = s.id
    estimateRef.current()
  }, [study.data])

  // ── persistence: debounced auto-PUT when every slot is non-empty ───────────
  const toSlotIn = (locals: LocalSlot[], serverStudy: Study): SlotIn[] => [
    ...locals.map((sl, i) => ({
      label: sl.label || `slot ${i + 1}`,
      kind: 'paint' as const,
      region_ids: sl.regionIds,
      anchor_color_id: sl.anchorColorId,
    })),
    // carry any lock slots through untouched (composer edits paint slots only)
    ...serverStudy.slots
      .filter(sl => sl.kind === 'lock')
      .map(sl => ({ label: sl.label, kind: 'lock' as const, region_ids: sl.region_ids })),
  ]

  useEffect(() => {
    const s = study.data
    if (!s || s.status !== 'draft' || !dirty || saving) return
    if (errorSeq.current === editSeq.current) return // wait for a new edit after a 422
    if (slots.length === 0 || slots.some(sl => sl.regionIds.length === 0)) return
    const seq = editSeq.current
    const t = setTimeout(() => {
      setSaving(true)
      putSlots(s.id, toSlotIn(slots, s))
        .then(saved => {
          qc.setQueryData(['study', saved.id], saved)
          if (editSeq.current === seq) {
            // no edits while in flight — adopt the canonical (area-desc) order
            setSlots(
              saved.slots
                .filter(sl => sl.kind === 'paint')
                .sort((a, b) => a.idx - b.idx)
                .map(sl => ({ label: sl.label, regionIds: [...sl.region_ids], anchorColorId: sl.anchor_color_id })),
            )
            setDirty(false)
          }
          estimateRef.current() // re-estimate on every material change
        })
        .catch(e => {
          errorSeq.current = seq
          toast(apiErrorDetail(e, 'Could not save slots'))
        })
        .finally(() => setSaving(false))
    }, 700)
    return () => clearTimeout(t)
  }, [slots, dirty, saving, study.data, qc])

  // ── slot painting ──────────────────────────────────────────────────────────
  const slotOfRegion = useMemo(() => {
    const m = new Map<string, number>()
    slots.forEach((sl, i) => sl.regionIds.forEach(id => m.set(id, i)))
    return m
  }, [slots])

  const edit = (next: LocalSlot[]) => {
    editSeq.current += 1
    setSlots(next)
    setDirty(true)
  }

  const tapRegion = (regionId: string) => {
    setPinRg(null) // painting supersedes a pinned touch-preview
    if (study.data && study.data.status !== 'draft') {
      toast(`Slots are frozen — study is ${study.data.status}`)
      return
    }
    if (!slots.length) {
      toast('Add a slot first')
      return
    }
    const cur = slotOfRegion.get(regionId)
    const next = slots.map(sl => ({ ...sl, regionIds: sl.regionIds.filter(id => id !== regionId) }))
    if (cur !== active) next[active].regionIds.push(regionId) // assign (or steal); same slot ⇒ toggle off
    next.forEach((sl, i) => {
      sl.label = dedupeLabel(sl.regionIds, regions, `slot ${i + 1}`)
    })
    edit(next)
  }

  const addSlot = () => {
    if (slots.length >= MAX_PAINT_SLOTS) return
    edit([...slots, { label: `slot ${slots.length + 1}`, regionIds: [], anchorColorId: null }])
    setActive(slots.length)
  }
  const removeSlot = () => {
    if (slots.length <= 1) return
    const next = slots.filter((_, i) => i !== active)
    edit(next)
    setActive(a => Math.min(a, next.length - 1))
  }

  // ── palette switch: PATCH in place (M7-T2 killed mint-on-browse) ───────────
  // Slots and the studio URL survive; the server clears its stored estimate,
  // so re-run /estimate right after.
  const swapPalette = useMutation({
    mutationFn: (pid: string) => patchStudy(studyId!, { palette_id: pid }),
    onSuccess: ns => {
      qc.setQueryData(['study', ns.id], ns)
      toast(`Palette → #${ns.palette_id}`)
      if (ns.k > 0) estimateRef.current()
    },
    onError: e => toast(apiErrorDetail(e, 'Could not switch palette')),
  })

  // ── generate (M7): confirm-with-cost → POST → the sheet fills live ─────────
  // Default scope = the policy's eager-N in diversity order (§8.12); deferred
  // permutations stay browsable as ghost cards on the contact sheet.
  const generateM = useMutation({
    mutationFn: () => generateStudy(studyId!),
    onSuccess: out => {
      setConfirming(false)
      setMode('sheet')
      toast(out.requested.length
        ? `Generating ${out.requested.length} colorway${out.requested.length === 1 ? '' : 's'} · ≤$${(out.estimated_cents / 100).toFixed(2)}`
        : `${out.already_ready} already generated — opening the sheet`)
      qc.invalidateQueries({ queryKey: ['study', studyId] })
      qc.invalidateQueries({ queryKey: ['colorways', studyId] })
    },
    onError: e => {
      setConfirming(false)
      toast(apiErrorDetail(e, 'Could not start generation'))
    },
  })

  // ── duplicate & tweak (SHIP-1): a frozen study forks into a fresh draft —
  //    same base photo, slots (groupings/locks/anchors) and palette carried,
  //    estimate recomputed on arrival. ─────────────────────────────────────────
  // RE-SCAN (2026-08-06): replace a poor segmentation (one-blob monochrome
  // sneaker, fur blobs). Destructive to the draft's slots — reload after.
  const [rescanning, setRescanning] = useState(false)
  // the hub's finding-regions viewfinder, reused: step text + KEEP BROWSING
  const [scanStep, setScanStep] = useState('')
  const [scanHidden, setScanHidden] = useState(false)
  const rescan = async () => {
    if (!baseMediaId || rescanning) return
    setRescanning(true)
    setScanHidden(false)
    setScanStep('Cutting the product out of the background…')
    try {
      await rescanRegions(baseMediaId)
      toast('Re-scanning — regions will be replaced')
      let ok = false
      for (let i = 0; i < 20; i++) {
        setScanStep(i < 2
          ? 'Cutting the product out of the background…'
          : i < 6
            ? 'Reading the product — panels, straps, hardware…'
            : 'Refining region edges to the pixel…')
        await new Promise(r => setTimeout(r, 3000))
        const out = await segmentMedia(baseMediaId)
        if (out.status === 'complete' && out.regions.length) { ok = true; break }
      }
      if (ok) setScanStep('Regions ready — reloading the composer…')
      navigate(0) // fresh regions ⇒ fresh composer state; repaint slots
    } catch (e) {
      toast(apiErrorDetail(e, 'Re-scan failed'))
      setRescanning(false)
    }
  }

  const duplicateM = useMutation({
    mutationFn: () => duplicateStudy(studyId!),
    onSuccess: ns => {
      qc.setQueryData(['study', ns.id], ns)
      qc.invalidateQueries({ queryKey: ['studies', designId] })
      toast('New draft — slots & palette carried over')
      navigate(`/d/${designId}/study/${ns.id}`)
    },
    onError: e => toast(apiErrorDetail(e, 'Could not duplicate the study')),
  })

  // ── derived ────────────────────────────────────────────────────────────────
  const palette = paletteQ.data ?? null
  const slotColor = (i: number) =>
    palette ? palette.colors[i % palette.colors.length] : null
  const emptySlot = slots.findIndex(sl => sl.regionIds.length === 0)
  const frozen = !!study.data && study.data.status !== 'draft'

  const saveStatus = frozen
    ? `FROZEN · ${study.data?.status.toUpperCase()}`
    : saving
      ? 'SAVING…'
      : emptySlot >= 0
        ? `SLOT ${emptySlot + 1} EMPTY — TAP REGIONS`
        : dirty
          ? 'UNSAVED'
          : 'SAVED'

  // hotspots big→small so small regions sit on top and win the tap
  const hotspots = useMemo(
    () => [...regions].sort((a, b) => b.area_fraction - a.area_fraction),
    [regions],
  )
  // mouse hover OR a pinned touch-preview both light a region up
  const focusRg = hoverRg ?? pinRg
  const hovered = focusRg ? regions.find(r => r.id === focusRg) ?? null : null

  const d = design.data
  const est = estimate

  return (
    <div className="view">
      <div className="content">
        <button className="back-inline press" onClick={() => navigate(`/d/${designId}`)}>
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
            <path d="M9 3L5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {d?.name ?? 'Design'}
        </button>

        <div className="hdr" style={{ paddingTop: 12 }}>
          <div className="rise">
            <div className="eyebrow" style={{ marginBottom: 4 }}>
              和田 · SANZO WADA · {d?.name ?? '…'}
            </div>
            <div className="syne" style={{ fontSize: 28, fontWeight: 800 }}>Wada Studio</div>
          </div>
          <div className="rise" style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8, animationDelay: '.06s' }}>
            <div className="mono" id="study-status" style={{ fontSize: 9, letterSpacing: '.12em', color: 'var(--fog)' }}>
              {study.data ? `${study.data.status.toUpperCase()} · #${study.data.palette_id} · ${palette?.name ?? '…'}` : 'LOADING…'}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
              <button
                className="kbtn gc-open press"
                id="study-hub-link"
                style={{ width: 'auto' }}
                title="All studies for this product"
                onClick={() => navigate(`/d/${designId}/studio`)}
              >
                ⊞ ALL STUDIES
              </button>
              {frozen && (
                <button
                  className="kbtn gc-open press dup-btn"
                  id="study-duplicate"
                  disabled={duplicateM.isPending}
                  title="Fork this study into a fresh draft — slots, locks, anchors and palette carried over"
                  onClick={() => duplicateM.mutate()}
                >
                  {duplicateM.isPending ? '…' : '⧉ NEW STUDY'}
                </button>
              )}
            </div>
          </div>
        </div>

        {study.isError ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Study not found.
          </div>
        ) : study.data && study.data.status !== 'draft' && mode === 'sheet' ? (
          <>
            <div className="studio-tabs rise">
              <button className="chip" id="tab-compose" onClick={() => setMode('compose')}>
                COMPOSER
              </button>
              <button className="chip on" id="tab-sheet">CONTACT SHEET</button>
            </div>
            <ContactSheet studyId={study.data.id} />
          </>
        ) : (
          <>
            {study.data && study.data.status !== 'draft' && (
              <div className="studio-tabs rise">
                <button className="chip on" id="tab-compose">COMPOSER</button>
                <button className="chip" id="tab-sheet" onClick={() => setMode('sheet')}>
                  CONTACT SHEET
                </button>
              </div>
            )}
            <div className="studio-grid">
            {/* ── PALETTE PICKER ── */}
            <div className="sg-pal rise" style={{ animationDelay: '.1s' }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>
                SANZO WADA · {palettesQ.data ? `${palettesQ.data.length} COMBINATIONS` : '…'}
              </div>
              <div className="chips" style={{ marginBottom: 6 }}>
                {[null, 2, 3, 4].map(n => (
                  <button
                    key={n ?? 'all'}
                    className={`chip${countFacet === n ? ' on' : ''}`}
                    data-count={n ?? 'all'}
                    onClick={() => setCountFacet(n)}
                  >
                    {n === null ? 'All' : `${n} colours`}
                  </button>
                ))}
              </div>
              <input
                className="field"
                style={{ marginTop: 0, marginBottom: 10, padding: '10px 13px', fontSize: 12 }}
                placeholder="Search palettes…"
                value={palQ}
                onChange={e => setPalQ(e.target.value)}
              />
              <div className="pal-rail">
                {palettesQ.data?.map(p => (
                  <PalCard
                    key={p.id}
                    p={p}
                    on={p.id === study.data?.palette_id}
                    busy={swapPalette.isPending}
                    onPick={() => {
                      if (p.id === study.data?.palette_id || swapPalette.isPending) return
                      if (frozen) { toast('Palette is frozen — study is no longer a draft'); return }
                      swapPalette.mutate(p.id)
                    }}
                    onDetail={() => setPalDetailId(p.id)}
                  />
                ))}
              </div>
            </div>

            {/* ── CANVAS: photo + mask overlays ── */}
            <div className="panel canvas-panel sg-canvas rise">
              <div className="canvas-bg">
                {photo ? (
                  <div className="ov-frame" onMouseLeave={() => setHoverRg(null)}>
                    <img className="ov-photo" src={photo.url} alt={d?.name ?? 'base photo'} />
                    {regions.map(r => {
                      const si = slotOfRegion.get(r.id)
                      const col = si !== undefined ? slotColor(si)?.hex ?? null : null
                      const hot = focusRg === r.id
                      // no luminance support ⇒ only the converted alpha mask is
                      // safe; the raw grayscale PNG would tint the WHOLE photo
                      const maskUrl = lumOk ? r.mask_url : alphaMasks[r.id] ?? null
                      return (
                        <div
                          key={r.id}
                          className={`rg-ov${hot ? ' hot' : col ? ' tint' : ''}`}
                          data-rg-ov={r.id}
                          style={{
                            background: hot ? 'var(--accent)' : col ?? 'transparent',
                            ...(maskUrl
                              ? {
                                  WebkitMaskImage: `url(${JSON.stringify(maskUrl)})`,
                                  maskImage: `url(${JSON.stringify(maskUrl)})`,
                                }
                              : { visibility: 'hidden' as const }),
                          }}
                        />
                      )
                    })}
                    {regions.map(r => (
                      <img
                        key={`p-${r.id}`}
                        className="rg-probe"
                        data-rg-probe={r.id}
                        // CORS-mode fetch keeps the cache entry usable by the CSS
                        // mask-image fetch (also CORS-mode). Without this, R2's
                        // no-Origin response (no ACAO, no Vary: Origin) poisons the
                        // cache and Chromium blocks the mask -> region tints break.
                        // MinIO masked this in dev by sending ACAO:* on everything.
                        crossOrigin="anonymous"
                        src={r.mask_url}
                        alt=""
                        onLoad={e => {
                          setLoadedMasks(n => n + 1)
                          const el = e.currentTarget
                          if (el.naturalWidth) setMaskDim(dim => dim ?? { w: el.naturalWidth, h: el.naturalHeight })
                        }}
                        onError={() => toast('A region mask failed to load')}
                      />
                    ))}
                    {maskDim && hotspots.map(r => (
                      <div
                        key={`h-${r.id}`}
                        className="rg-hot"
                        data-rg-hot={r.id}
                        style={{
                          left: `${(r.bbox[0] / maskDim.w) * 100}%`,
                          top: `${(r.bbox[1] / maskDim.h) * 100}%`,
                          width: `${((r.bbox[2] - r.bbox[0]) / maskDim.w) * 100}%`,
                          height: `${((r.bbox[3] - r.bbox[1]) / maskDim.h) * 100}%`,
                        }}
                        onMouseEnter={() => setHoverRg(r.id)}
                        onClick={() => tapRegion(r.id)}
                      />
                    ))}
                    {hovered && maskDim && (
                      <div
                        className="rg-tip"
                        style={{
                          // clamp so edge regions don't push the tip outside the clipped frame
                          left: `${Math.min(80, Math.max(20, (((hovered.bbox[0] + hovered.bbox[2]) / 2) / maskDim.w) * 100))}%`,
                          top: `${Math.max(6, (hovered.bbox[1] / maskDim.h) * 100)}%`,
                        }}
                      >
                        {hovered.label} · {Math.round(hovered.confidence * 100)}%
                        {slotOfRegion.has(hovered.id) ? ` · S${slotOfRegion.get(hovered.id)! + 1}` : ''}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '48px 12px', textAlign: 'center' }}>
                    Loading photo…
                  </div>
                )}
              </div>
              <div className="legend">
                <span id="mask-count">{loadedMasks}/{regions.length} masks</span>
                <span style={{ color: 'var(--faint)' }}>tap a region to paint it into slot {active + 1}</span>
              </div>
            </div>

            {/* ── RAIL: regions · slots · estimate ── */}
            <div className="sg-rail">
              <div className="panel rise" style={{ padding: 14, animationDelay: '.08s' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, gap: 6, flexWrap: 'wrap' }}>
                  <div className="eyebrow">Regions</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <div className="mono" style={{ fontSize: 8, color: 'var(--faint)', letterSpacing: '.08em' }}>
                      MODEL-FOUND · TAP TO PAINT
                    </div>
                    {!frozen && baseMediaId && (
                      <button
                        className="kbtn"
                        id="rescan-regions"
                        title="Not enough regions? Re-run segmentation with the current model — replaces these regions; painted slots need repainting"
                        disabled={rescanning}
                        style={{ width: 'auto', padding: '0 8px', fontFamily: 'DM Mono', fontSize: 8, letterSpacing: '.08em' }}
                        onClick={rescan}
                      >
                        {rescanning ? 'SCANNING…' : '⟳ RE-SCAN'}
                      </button>
                    )}
                  </div>
                </div>
                {regionsQ.isLoading && (
                  <div className="mono" style={{ fontSize: 10, color: 'var(--faint)' }}>Loading…</div>
                )}
                {regions.map(r => {
                  const si = slotOfRegion.get(r.id)
                  return (
                    // div, not button: the ◉ peek control nests inside (buttons can't nest)
                    <div
                      key={r.id}
                      className={`region-row${si !== undefined ? ' sel' : ''}`}
                      data-region-row={r.id}
                      role="button"
                      tabIndex={0}
                      onMouseEnter={() => setHoverRg(r.id)}
                      onMouseLeave={() => setHoverRg(null)}
                      onClick={() => tapRegion(r.id)}
                      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tapRegion(r.id) } }}
                    >
                      <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                        <span className={`conf ${r.confidence >= 0.8 ? 'hi' : 'mid'}`} />
                        <span style={{ fontSize: 12.5, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {r.label}
                        </span>
                        <span className="mono" style={{ fontSize: 8.5, color: 'var(--faint)', flexShrink: 0 }}>
                          {Math.round(r.confidence * 100)}%
                        </span>
                      </span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                        {/* touch "hover": pin this region's highlight on the photo
                            without painting it; tap again (or paint) to clear */}
                        <button
                          className={`rg-peek${pinRg === r.id ? ' on' : ''}`}
                          data-rg-peek={r.id}
                          aria-label={`Preview ${r.label} on the photo`}
                          aria-pressed={pinRg === r.id}
                          title="Preview this region on the photo"
                          onClick={e => {
                            e.stopPropagation()
                            setPinRg(p => (p === r.id ? null : r.id))
                          }}
                        >
                          ◉
                        </button>
                        <span className={`rr-state ${si !== undefined ? 'sel' : 'none'}`}>
                          {si !== undefined ? `SLOT ${si + 1}` : '—'}
                        </span>
                      </span>
                    </div>
                  )
                })}
                <div className="mono" style={{ fontSize: 9, color: 'var(--faint)', lineHeight: 1.7, marginTop: 8 }}>
                  ● green = high confidence · ● amber = check me
                </div>
              </div>

              <div className="panel slot-list rise" style={{ marginTop: 12, animationDelay: '.12s' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, gap: 6 }}>
                  <div className="eyebrow">Slots · K = {slots.length}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span className="mono" id="save-status" style={{ fontSize: 8, letterSpacing: '.08em', color: saving || dirty ? 'var(--warn)' : 'var(--faint)' }}>
                      {saveStatus}
                    </span>
                    <button className="kbtn" id="slot-minus" aria-label="Remove slot" disabled={slots.length <= 1 || frozen} onClick={removeSlot}>−</button>
                    <button className="kbtn" id="slot-plus" aria-label="Add slot" disabled={slots.length >= MAX_PAINT_SLOTS || frozen} onClick={addSlot}>+</button>
                  </div>
                </div>
                {slots.map((sl, i) => {
                  const c = slotColor(i)
                  return (
                    <button
                      key={i}
                      className={`slot-row${i === active ? ' on' : ''}`}
                      id={`slot-row-${i}`}
                      onClick={() => setActive(i)}
                    >
                      <span className="slot-swatch" style={{ background: c?.hex ?? 'var(--bg-deep)' }} />
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'block', fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {sl.regionIds.length ? sl.label : 'empty — tap regions'}
                        </span>
                        <span className="mono" style={{ display: 'block', fontSize: 9, color: 'var(--faint)', marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {c ? `${c.name} · ${c.hex.toUpperCase()}` : '…'} · {sl.regionIds.length} region{sl.regionIds.length === 1 ? '' : 's'}
                        </span>
                      </span>
                      <span className="mono" style={{ fontSize: 8.5, color: i === active ? 'var(--accent)' : 'var(--faint)', flexShrink: 0 }}>
                        {i === active ? 'PAINTING' : `SLOT ${i + 1}`}
                      </span>
                    </button>
                  )
                })}
              </div>

              {/* ── ESTIMATE (§8.13.1 readout, live from POST /estimate) ── */}
              <div className="panel rise" id="est-panel" style={{ padding: 14, marginTop: 12, animationDelay: '.16s' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6, flexWrap: 'wrap' }}>
                  <div className="eyebrow">Estimate</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {est && (
                      <span className={`regime-pill${est.regime === 'surjective' ? ' amber' : ''}`} id="est-regime">
                        {est.regime} · K{est.k} C{est.c}{est.anchors ? ` · ${est.anchors} anchored` : ''}
                      </span>
                    )}
                    <button
                      className="kbtn"
                      id="est-refresh"
                      aria-label="Re-estimate"
                      onClick={() => estimateM.mutate()}
                      style={{ width: 'auto', padding: '0 9px', fontFamily: 'DM Mono', fontSize: 8.5, letterSpacing: '.08em' }}
                    >
                      {estimateM.isPending ? '…' : 'RE-RUN'}
                    </button>
                  </div>
                </div>
                {est ? (
                  <>
                    {estimateM.isPending && (
                      <div className="est-busy mono" id="est-busy">
                        <span className="spinner" />
                        RE-PRICING THE STUDY…
                      </div>
                    )}
                    <div className="est-nums" style={{ opacity: estimateM.isPending ? 0.35 : 1, transition: 'opacity .25s ease' }}>
                      <div className="est-cell"><div className="n" id="est-perms">{est.perm_total}</div><div className="t">Permutations</div></div>
                      <div className="est-cell"><div className="n" id="est-planned">{est.planned}</div><div className="t">Planned{est.cap_exceeded ? ` (cap ${est.cap})` : ''}</div></div>
                      <div className="est-cell"><div className="n" id="est-calls">{est.planned_calls}</div><div className="t">Model calls</div></div>
                      <div className="est-cell"><div className="n" id="est-cost">${est.est_cost}</div><div className="t">Est. cost</div></div>
                      <div className="est-cell"><div className="n" id="est-saved">{est.saved_pct}%</div><div className="t">Saved vs naive</div></div>
                      <div className="est-cell"><div className="n" id="est-eta">{fmtEta(est.eta_seconds)}</div><div className="t">ETA</div></div>
                    </div>
                    <div className={`est-copy${est.regime === 'surjective' ? ' amber' : ''}`} id="est-msg">
                      {est.message}
                    </div>
                    {est.cap_message && (
                      <div className="est-copy amber" id="est-cap">{est.cap_message}</div>
                    )}
                    <div className="mono" style={{ fontSize: 8.5, color: 'var(--faint)', lineHeight: 1.8, marginTop: 9, letterSpacing: '.04em' }}>
                      naive {est.naive_calls} calls ${est.naive_cost} → trie {est.trie_calls_full} calls ${est.trie_cost_full}
                      {' '}· {est.steps_per_colorway} steps/colorway · eager {est.eager} · cap {est.cap}
                    </div>
                  </>
                ) : (
                  <div className="mono" style={{ fontSize: 10, color: 'var(--faint)', marginTop: 10, display: 'flex', alignItems: 'center', gap: 7 }}>
                    {estimateM.isPending && <span className="spinner" />}
                    {estimateM.isPending ? 'PRICING THE STUDY…' : 'Paint regions into slots to price the study.'}
                  </div>
                )}
                {confirming && est ? (
                  <div className="gen-confirm block" id="gen-confirm">
                    <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                      Generate <b>{est.eager} of {est.planned}</b> planned colorways now
                      (~<b>${est.est_cost}</b> for the full sheet, eager first) —
                      the rest stay browsable as ghost cards, one tap each.
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                      <button className="kbtn gc-cancel" id="gen-cancel" onClick={() => setConfirming(false)}>
                        CANCEL
                      </button>
                      <button className="kbtn gc-go" id="gen-go" disabled={generateM.isPending} onClick={() => generateM.mutate()}>
                        {generateM.isPending ? '…' : `GENERATE · ~$${est.est_cost}`}
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    className="gen-btn press"
                    id="gen-btn"
                    disabled={!est || frozen || generateM.isPending}
                    style={!est || frozen ? { marginTop: 12, opacity: 0.55, cursor: 'default' } : { marginTop: 12 }}
                    onClick={() => est && !frozen && setConfirming(true)}
                  >
                    <span style={{ textAlign: 'left' }}>
                      <span className="syne" style={{ display: 'block', fontSize: 14, fontWeight: 600 }}>Generate colorways</span>
                      <span className="mono" id="gen-sub" style={{ display: 'block', fontSize: 9, opacity: 0.7, marginTop: 2 }}>
                        {frozen
                          ? `GENERATED — SEE THE CONTACT SHEET`
                          : est
                            ? `${est.planned} colorways · ~$${est.est_cost} · EAGER ${est.eager} FIRST`
                            : 'PAINT SLOTS TO PRICE THE STUDY'}
                      </span>
                    </span>
                    <svg width="16" height="16" viewBox="0 0 17 17" fill="none">
                      <path d="M3.5 8.5h10m0 0l-4-4m4 4l-4 4" stroke="#FFF" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
            </div>
          </>
        )}
        {/* RE-SCAN: the hub's finding-regions viewfinder over the base photo.
            Dismissible — the scan keeps polling behind the composer. */}
        {rescanning && !scanHidden && photo && (
          <ScanOverlay
            photoUrl={photo.thumb_url ?? photo.url}
            step={scanStep}
            onHide={() => setScanHidden(true)}
          />
        )}
        {palDetailId && (
          <PaletteDetail
            paletteId={palDetailId}
            activeId={study.data?.palette_id}
            frozen={frozen}
            busy={swapPalette.isPending}
            onUse={id => swapPalette.mutate(id)}
            onClose={() => setPalDetailId(null)}
          />
        )}
      </div>
    </div>
  )
}

function PalCard({
  p, on, busy, onPick, onDetail,
}: {
  p: Palette
  on: boolean
  busy: boolean
  onPick: () => void
  onDetail: () => void
}) {
  // div, not button: the M8 DETAIL affordance nests inside (buttons can't nest)
  return (
    <div
      className={`pal-card${on ? ' on' : ''}`}
      data-pal={p.id}
      role="button"
      tabIndex={0}
      onClick={onPick}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick() } }}
      style={busy ? { opacity: 0.6, cursor: 'pointer' } : { cursor: 'pointer' }}
    >
      <span className="pal-blocks">
        {p.colors.map(c => (
          <span key={c.id} style={{ background: c.hex, flex: 1 }} title={c.name} />
        ))}
      </span>
      <span className="pal-name" style={{ display: 'block' }}>{p.name}</span>
      <span className="pal-id" style={{ display: 'block' }}>#{p.id} · {p.color_count}-colour · {p.temperature}</span>
      <button
        className="pal-detail mono press"
        data-pal-detail={p.id}
        aria-label={`Palette ${p.id} detail`}
        onClick={e => { e.stopPropagation(); onDetail() }}
      >
        DETAIL
      </button>
    </div>
  )
}
