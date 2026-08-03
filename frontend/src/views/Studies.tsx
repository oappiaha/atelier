// STUDIES — the workspace-wide gallery of every Wada study ever made,
// organised per product (GET /studies): one section per design — name,
// count + spend — with that design's study cards in a grid under it.
// Tapping a card opens the Studio it belongs to (/d/{design}/study/{id}).
import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiErrorDetail, deleteStudy, fetchStudies, type StudyGalleryItem } from '../lib/api'
import { toast } from '../lib/store'

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })

const cents = (n: number) => `$${(n / 100).toFixed(2)}`

/* study_status (TDD §2.4) → the contact sheet's .cw-st colour language:
   complete reads done (green), anything mid-flight reads live (amber pulse),
   failed/cancelled read error, draft/partial stay neutral. */
const ST_CLASS: Record<string, string> = {
  draft: '',
  estimating: 'generating',
  generating: 'generating',
  partial: 'partial',
  complete: 'ready',
  failed: 'failed',
  cancelled: 'failed',
}

export function StudyCard({ s, i, onOpen }: { s: StudyGalleryItem; i: number; onOpen: () => void }) {
  const hero = s.hero_thumb_url
  return (
    <button
      className="cw-card sg-card press"
      style={{ animationDelay: `${Math.min(i, 11) * 0.04}s` }}
      onClick={onOpen}
    >
      <div className="cw-imgwrap">
        {hero ? (
          <img className="cw-img" src={hero} alt={`${s.design_name} colorway`} loading="lazy" />
        ) : s.base_thumb_url ? (
          /* nothing generated yet — the base photo, muted, so the card still
             reads as a real piece without pretending a colorway exists */
          <>
            <img className="cw-img sg-base" src={s.base_thumb_url} alt={s.design_name} loading="lazy" />
            <span className="cw-holding sg-holding mono">NOT GENERATED</span>
          </>
        ) : (
          /* no photo either: the palette itself is the placeholder */
          <div className="sg-blocks">
            {s.palette_hexes.map((h, k) => (
              <div key={k} style={{ background: h }} />
            ))}
          </div>
        )}
      </div>

      <div className="cw-fp">
        {s.palette_hexes.map((h, k) => (
          <span key={k} className="cw-chip" style={{ background: h }} />
        ))}
      </div>

      <div className="sg-label">
        <div className="dname">{s.design_name}</div>
        <div className="sg-pal mono">
          {s.palette_name} · {s.palette_id.toUpperCase()}
        </div>
        <div className="sg-foot">
          <span className={`cw-st ${ST_CLASS[s.status] ?? ''}`}>{s.status.toUpperCase()}</span>
          <span className="sg-nums mono">
            {s.ready}/{s.planned} ready · {cents(s.actual_cost_cents)}
          </span>
        </div>
        <div className="sg-date mono">{fmtDate(s.created_at)}</div>
      </div>
    </button>
  )
}

interface DesignGroup {
  design_id: string
  design_name: string
  items: StudyGalleryItem[]
}

export default function Studies() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['studies'], queryFn: fetchStudies })
  const studies = data ?? []

  // drafts are free to delete; generated studies are spend records (API 409s)
  const deleteM = useMutation({
    mutationFn: (id: string) => deleteStudy(id),
    onSuccess: () => {
      toast('Draft deleted')
      qc.invalidateQueries({ queryKey: ['studies'] })
    },
    onError: e => toast(apiErrorDetail(e, 'Could not delete the draft')),
  })

  // group per design. Rows arrive newest-study-first, so first-appearance
  // order puts the most recently worked-on design at the top.
  const groups = useMemo<DesignGroup[]>(() => {
    const byDesign = new Map<string, DesignGroup>()
    for (const s of data ?? []) {
      let g = byDesign.get(s.design_id)
      if (!g) {
        g = { design_id: s.design_id, design_name: s.design_name, items: [] }
        byDesign.set(s.design_id, g)
      }
      g.items.push(s)
    }
    return [...byDesign.values()]
  }, [data])

  return (
    <div className="view">
      <div className="content">
        <div className="hdr">
          <div className="rise">
            <div className="eyebrow" style={{ marginBottom: 4 }}>Every colorway study, one place</div>
            <div className="syne" style={{ fontSize: 28, fontWeight: 800 }}>Studies</div>
          </div>
          {!isLoading && studies.length > 0 && (
            <div className="mono rise" style={{ fontSize: 10, letterSpacing: '.12em', color: 'var(--faint)', animationDelay: '.06s' }}>
              {studies.length} STUD{studies.length === 1 ? 'Y' : 'IES'}
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading…
          </div>
        ) : !studies.length ? (
          <div className="panel rise" style={{ padding: 24, marginTop: 10 }}>
            <div className="syne" style={{ fontSize: 16, fontWeight: 700 }}>No studies yet</div>
            <p style={{ fontSize: 12.5, color: 'var(--fog)', marginTop: 6, lineHeight: 1.6 }}>
              Open a design, pick a final photo and explore colorways — every study you
              compose shows up here.
            </p>
          </div>
        ) : (
          groups.map((g, gi) => {
            const spent = g.items.reduce((n, s) => n + s.actual_cost_cents, 0)
            return (
              <section key={g.design_id} className="rise" style={{ marginTop: gi === 0 ? 4 : 28, animationDelay: `${Math.min(gi, 6) * 0.05}s` }}>
                <div
                  style={{
                    display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
                    gap: 10, paddingBottom: 7, borderBottom: '1px solid var(--stroke)',
                  }}
                >
                  <div className="syne" style={{ fontSize: 17, fontWeight: 700, minWidth: 0 }}>
                    {g.design_name}
                  </div>
                  <div className="mono" style={{ fontSize: 9, letterSpacing: '.12em', color: 'var(--faint)', flexShrink: 0 }}>
                    {g.items.length} STUD{g.items.length === 1 ? 'Y' : 'IES'} · {cents(spent)}
                  </div>
                </div>
                <div className="cw-grid sg-grid">
                  {g.items.map((s, i) => (
                    <div key={s.id} className="sg-wrap">
                      <StudyCard
                        s={s}
                        i={i}
                        onOpen={() => navigate(`/d/${s.design_id}/study/${s.id}`)}
                      />
                      {s.status === 'draft' && (
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
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )
          })
        )}
      </div>
    </div>
  )
}
