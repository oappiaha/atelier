// WADA STUDIO HUB — the per-product studio space at /d/:designId/studio.
// "Explore colorways" lands HERE (it used to silently create a draft study,
// which minted a duplicate draft on every device without the localStorage
// memo — 22 empty drafts by 2026-08). The hub shows every past study for
// this product and one deliberate entry point: NEW STUDY resumes the newest
// empty draft when one exists, and only creates when there's nothing to
// resume. Draft cards carry a delete key (drafts are free; anything
// generated is a spend record and the API 409s).
import { useMemo, useState } from 'react'
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
  const openStudy = (s: StudyGalleryItem) => navigate(`/d/${designId}/study/${s.id}`)

  /** NEW STUDY: resume the newest empty draft, else segment + create. */
  const newStudy = async () => {
    if (!designId || opening) return
    if (emptyDraft) {
      toast('Resuming your open draft')
      rememberStudy(designId, emptyDraft.id)
      navigate(`/d/${designId}/study/${emptyDraft.id}`)
      return
    }
    setOpening(true)
    try {
      const imgs = (media.data ?? []).filter(m => m.kind === 'image')
      if (!imgs.length) {
        toast('Add a product photo first')
        return
      }
      let base: Media | null = null
      for (const m of imgs) {
        if ((await fetchRegions(m.id)).length) { base = m; break }
      }
      if (!base) {
        toast('Finding regions…')
        for (let i = 0; i < 20; i++) {
          const out = await segmentMedia(imgs[0].id)
          if (out.status === 'complete' && out.regions.length) { base = imgs[0]; break }
          await new Promise(r => setTimeout(r, 3000))
        }
        if (!base) {
          toast('Segmentation is still running — try again in a minute')
          return
        }
      }
      const s = await createStudy({
        design_id: designId, base_media_id: base.id, palette_id: DEFAULT_PALETTE_ID,
      })
      rememberStudy(designId, s.id)
      qc.invalidateQueries({ queryKey: ['studies'] })
      navigate(`/d/${designId}/study/${s.id}`)
    } catch (e) {
      toast(apiErrorDetail(e, 'Could not open the studio'))
    } finally {
      setOpening(false)
    }
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
    </div>
  )
}
