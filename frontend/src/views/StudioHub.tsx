// WADA STUDIO HUB — the per-product studio space at /d/:designId/studio.
// "Explore colorways" lands HERE (it used to silently create a draft study,
// which minted a duplicate draft on every device without the localStorage
// memo — 22 empty drafts by 2026-08). The hub shows every past study for
// this product and one deliberate entry point: NEW STUDY resumes the newest
// empty draft when one exists, and only creates when there's nothing to
// resume. Draft cards carry a delete key (drafts are free; anything
// generated is a spend record and the API 409s).
import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api, apiErrorDetail, createStudy, deleteStudy, fetchRegions, fetchStudies,
  rememberStudy, segmentMedia, DEFAULT_PALETTE_ID,
  type Design, type Media, type StudyGalleryItem,
} from '../lib/api'
import { toast } from '../lib/store'
import { StudyCard } from './Studies'

export default function StudioHub() {
  const { designId } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const design = useQuery({
    queryKey: ['design', designId],
    queryFn: () => api<Design>(`/designs/${designId}`),
    enabled: !!designId,
  })
  const media = useQuery({
    queryKey: ['media', designId],
    queryFn: () => api<Media[]>(`/designs/${designId}/media`),
    enabled: !!designId,
  })
  const studiesQ = useQuery({ queryKey: ['studies'], queryFn: fetchStudies })

  const mine = useMemo(
    () => (studiesQ.data ?? []).filter(s => s.design_id === designId),
    [studiesQ.data, designId],
  )
  const emptyDraft = useMemo(
    () => mine.find(s => s.status === 'draft' && s.ready === 0 && s.planned === 0) ?? null,
    [mine],
  )

  const [opening, setOpening] = useState(false)
  const [picking, setPicking] = useState(false)
  // the finding-regions overlay: which photo is being scanned + step text.
  // hidden=true keeps the loop running without the overlay ("keep browsing")
  const [finding, setFinding] = useState<{ photo: Media; step: string; hidden: boolean } | null>(null)
  const openStudy = (s: StudyGalleryItem) => navigate(`/d/${designId}/study/${s.id}`)

  // study bases: real photos AND photoroom studio shots (clean, centered —
  // the pipeline makes its own cutout of whatever base is chosen). Only wada
  // colorway OUTPUTS are excluded: recoloring a recolor muddies provenance.
  const photos = useMemo(
    () => (media.data ?? []).filter(m => m.kind === 'image' && m.source_app !== 'wada'),
    [media.data],
  )

  /** Create a draft on a CHOSEN base photo — segments it first if needed. */
  const startOn = async (base: Media) => {
    if (!designId || opening) return
    setPicking(false)
    setOpening(true)
    try {
      if (!(await fetchRegions(base.id)).length) {
        setFinding({ photo: base, step: 'Cutting the product out of the background…', hidden: false })
        let ok = false
        for (let i = 0; i < 20; i++) {
          const out = await segmentMedia(base.id)
          if (out.status === 'complete' && out.regions.length) { ok = true; break }
          setFinding(f => f && {
            ...f,
            step: i < 2
              ? 'Cutting the product out of the background…'
              : i < 6
                ? 'Reading the product — panels, straps, hardware…'
                : 'Refining region edges to the pixel…',
          })
          await new Promise(r => setTimeout(r, 3000))
        }
        if (!ok) {
          setFinding(null)
          toast('Segmentation is still running — try again in a minute')
          return
        }
      }
      setFinding(f => f && { ...f, step: 'Regions ready — opening the composer…' })
      const s = await createStudy({
        design_id: designId, base_media_id: base.id, palette_id: DEFAULT_PALETTE_ID,
      })
      rememberStudy(designId, s.id)
      qc.invalidateQueries({ queryKey: ['studies'] })
      navigate(`/d/${designId}/study/${s.id}`)
    } catch (e) {
      toast(apiErrorDetail(e, 'Could not open the studio'))
    } finally {
      setFinding(null)
      setOpening(false)
    }
  }

  /** NEW STUDY: resume the newest empty draft; else pick a base photo
   *  (multi-photo designs) or go straight in (single photo). */
  const newStudy = () => {
    if (!designId || opening) return
    if (emptyDraft) {
      toast('Resuming your open draft')
      rememberStudy(designId, emptyDraft.id)
      navigate(`/d/${designId}/study/${emptyDraft.id}`)
      return
    }
    if (!photos.length) {
      toast('Add a product photo first')
      return
    }
    if (photos.length === 1) void startOn(photos[0])
    else setPicking(true)
  }

  const deleteM = useMutation({
    mutationFn: (id: string) => deleteStudy(id),
    onSuccess: () => {
      toast('Draft deleted')
      qc.invalidateQueries({ queryKey: ['studies'] })
    },
    onError: e => toast(apiErrorDetail(e, 'Could not delete the draft')),
  })

  const d = design.data
  const generated = mine.filter(s => s.status !== 'draft')
  const drafts = mine.filter(s => s.status === 'draft')

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
          <div className="rise" style={{ animationDelay: '.06s' }}>
            <button
              className="kbtn gc-open press dup-btn"
              id="hub-new-study"
              disabled={opening}
              onClick={newStudy}
            >
              {opening ? 'OPENING…' : emptyDraft ? '▸ RESUME DRAFT' : '＋ NEW STUDY'}
            </button>
          </div>
        </div>

        {studiesQ.isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading studies…
          </div>
        ) : !mine.length ? (
          <div className="panel rise" style={{ padding: 24, marginTop: 10 }}>
            <div className="syne" style={{ fontSize: 16, fontWeight: 700 }}>No studies yet</div>
            <p style={{ fontSize: 12.5, color: 'var(--fog)', marginTop: 6, lineHeight: 1.6 }}>
              Start one — pick regions on a product photo, paint them into slots,
              and generate colorways.
            </p>
          </div>
        ) : (
          <>
            {generated.length > 0 && (
              <section className="rise" style={{ marginTop: 4 }}>
                <div className="eyebrow" style={{ margin: '10px 0 8px' }}>
                  STUDIES · {generated.length}
                </div>
                <div className="cw-grid sg-grid">
                  {generated.map((s, i) => (
                    <StudyCard key={s.id} s={s} i={i} onOpen={() => openStudy(s)} />
                  ))}
                </div>
              </section>
            )}
            {drafts.length > 0 && (
              <section className="rise" style={{ marginTop: 22, animationDelay: '.08s' }}>
                <div className="eyebrow" style={{ margin: '10px 0 8px' }}>
                  DRAFTS · {drafts.length}
                </div>
                <div className="cw-grid sg-grid">
                  {drafts.map((s, i) => (
                    <div key={s.id} className="sg-wrap">
                      <StudyCard s={s} i={i} onOpen={() => openStudy(s)} />
                      <button
                        className="sg-del press"
                        aria-label="Delete draft"
                        title="Delete this draft (free — nothing was generated)"
                        disabled={deleteM.isPending}
                        onClick={() => deleteM.mutate(s.id)}
                      >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                          <path d="M6 6l12 12M18 6L6 18" />
                        </svg>
                      </button>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>

      {/* base-photo picker: which photo does the new study paint on? Portaled
          to <body> — never trust a fixed overlay inside .rise ancestors. */}
      {picking && createPortal(
        <div className="cwlb" id="base-picker" onClick={() => setPicking(false)}>
          <div className="cwlb-body pald-body" onClick={e => e.stopPropagation()}>
            <button className="cwlb-close press" aria-label="Close" onClick={() => setPicking(false)}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
            <div style={{ padding: '20px 18px' }}>
              <div className="eyebrow" style={{ marginBottom: 2 }}>NEW STUDY</div>
              <div className="syne" style={{ fontSize: 19, fontWeight: 800 }}>Which photo?</div>
              <p style={{ fontSize: 12, color: 'var(--fog)', margin: '6px 0 12px', lineHeight: 1.6 }}>
                The study paints on this photo — regions, slots and colorways
                all live in its frame.
              </p>
              <div className="base-pick-grid">
                {photos.map(m => (
                  <button key={m.id} className="base-pick press" onClick={() => startOn(m)}>
                    <img src={m.thumb_url ?? m.url} alt="" loading="lazy" />
                    <span className="mono">
                      {m.source_app === 'photoroom' ? 'STUDIO SHOT' : m.phase?.toUpperCase() ?? 'PHOTO'}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* finding-regions: the wait made visible — scan sweep over the chosen
          photo + honest step text. Dismissible: segmentation runs server-side
          and its result is cached, so leaving is always safe. */}
      {finding && !finding.hidden && createPortal(
        <div className="cwlb" id="finding-regions">
          <div className="cwlb-body" onClick={e => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div style={{ padding: '20px 18px', textAlign: 'center' }}>
              <div className="scan-frame">
                <img src={finding.photo.thumb_url ?? finding.photo.url} alt="" />
                <div className="scan-beam" />
              </div>
              <div className="syne" style={{ fontSize: 17, fontWeight: 800, marginTop: 14 }}>
                Finding regions
              </div>
              <div className="mono scan-step" style={{ fontSize: 9.5, letterSpacing: '.08em', color: 'var(--fog)', marginTop: 6, minHeight: 14 }}>
                {finding.step}
              </div>
              <div className="mono" style={{ fontSize: 8.5, color: 'var(--faint)', marginTop: 10, lineHeight: 1.7 }}>
                USUALLY 10–20 SECONDS · SAFE TO KEEP BROWSING —<br />REGIONS ARE CACHED THE MOMENT THEY'RE READY
              </div>
              <button
                className="kbtn gc-open press"
                id="finding-hide"
                style={{ width: 'auto', padding: '8px 14px', marginTop: 12 }}
                onClick={() => setFinding(f => f && { ...f, hidden: true })}
              >
                KEEP BROWSING
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
