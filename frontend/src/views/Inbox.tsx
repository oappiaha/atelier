import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  api, fetchInbox, triageMedia,
  PHASE_LABELS, type Design, type Media, type Phase, type Project,
} from '../lib/api'
import { toast } from '../lib/store'

/** Phases an inbox capture can be triaged onto (PRD A5: "into any design").
 *  Images get the visual phases (share-target lands as Inspiration → default);
 *  audio captures are voice notes. */
const IMAGE_PHASES: Phase[] = ['inspiration', 'moodboard', 'sketch', 'manufacturing', 'editorial', 'final']
const AUDIO_PHASES: Phase[] = ['voice']

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

interface DestGroup {
  project: Project
  designs: Design[]
}

export default function Inbox() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: items, isLoading } = useQuery({ queryKey: ['inbox'], queryFn: fetchInbox })

  const [sel, setSel] = useState<Media | null>(null)
  const [designId, setDesignId] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>('inspiration')
  const [busy, setBusy] = useState(false)

  // destinations: every design in every project, grouped (small data, one fetch)
  const { data: dests } = useQuery({
    queryKey: ['triage-dests'],
    queryFn: async (): Promise<DestGroup[]> => {
      const projects = await api<Project[]>('/projects')
      return Promise.all(
        projects.map(async project => ({
          project,
          designs: await api<Design[]>(`/projects/${project.id}/designs`),
        })),
      )
    },
  })

  const allDesigns = dests?.flatMap(g => g.designs) ?? []
  const destDesign = allDesigns.find(d => d.id === designId) ?? null

  const openTriage = (m: Media) => {
    setSel(m)
    setDesignId(allDesigns.length === 1 ? allDesigns[0].id : null)
    setPhase(m.kind === 'audio' ? 'voice' : 'inspiration')
  }
  const closeTriage = () => {
    if (!busy) setSel(null)
  }

  const confirm = async () => {
    if (!sel || !destDesign || busy) return
    setBusy(true)
    try {
      await triageMedia(sel.id, destDesign.id, phase)
      qc.invalidateQueries({ queryKey: ['inbox'] })
      qc.invalidateQueries({ queryKey: ['design', destDesign.id] })
      qc.invalidateQueries({ queryKey: ['timeline', destDesign.id] })
      qc.invalidateQueries({ queryKey: ['media', destDesign.id] })
      qc.invalidateQueries({ queryKey: ['designs'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      toast(`Sorted → ${destDesign.name}`)
      setSel(null)
    } catch (e) {
      toast(e instanceof Error && e.message ? `Failed: ${e.message.slice(0, 80)}` : 'Triage failed')
    } finally {
      setBusy(false)
    }
  }

  const phases = sel?.kind === 'audio' ? AUDIO_PHASES : IMAGE_PHASES

  return (
    <div className="view">
      <div className="content">
        <button className="back-inline press rise" onClick={() => navigate('/')}>
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
            <path d="M9 3L5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Home
        </button>
        <header className="hdr rise">
          <div>
            <div className="eyebrow">INBOX</div>
            <div className="syne" style={{ fontSize: 26, fontWeight: 800, marginTop: 4 }}>Unsorted</div>
          </div>
          {!!items?.length && (
            <div className="mono" style={{ fontSize: 9.5, color: 'var(--faint)' }}>
              {items.length} item{items.length === 1 ? '' : 's'}
            </div>
          )}
        </header>

        {isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading…
          </div>
        ) : !items?.length ? (
          <div className="panel rise" id="inbox-empty" style={{ padding: 24 }}>
            <div className="syne" style={{ fontSize: 16, fontWeight: 700 }}>All sorted ✨</div>
            <p style={{ fontSize: 12.5, color: 'var(--fog)', marginTop: 6, lineHeight: 1.6 }}>
              Nothing waiting. Share from Instagram or Safari — or capture without picking
              a design — and it lands here to triage later.
            </p>
          </div>
        ) : (
          <div className="ib-list">
            {items.map((m, i) => (
              <button
                key={m.id}
                className="ib-card panel press rise"
                style={{ animationDelay: `${i * 0.06}s` }}
                onClick={() => openTriage(m)}
              >
                <img src={m.thumb_url ?? m.url} alt={m.caption ?? 'Unsorted capture'} />
                <div className="ib-txt">
                  <div style={{ fontSize: 12.5, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {m.caption ?? (m.kind === 'audio' ? 'Voice capture' : 'Untitled capture')}
                  </div>
                  <div className="ib-src">
                    {m.source_app ?? (m.source_url ? 'web' : 'capture')} · {fmtDate(m.created_at)}
                  </div>
                </div>
                <span className="mono" style={{ fontSize: 9, letterSpacing: '.1em', color: 'var(--fog)', flexShrink: 0 }}>
                  SORT →
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* triage sheet — mock sheet idiom */}
      <div className={`sheet-wrap${sel ? ' open' : ''}`} id="sheet-triage">
        <div className="backdrop" onClick={closeTriage} />
        <div className="sheet">
          <div className="grabber" />
          {sel && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <div className="syne" style={{ fontSize: 18, fontWeight: 700 }}>Sort into a design</div>
                <button
                  className="press mono"
                  id="triage-cancel"
                  style={{ fontSize: 9.5, letterSpacing: '.1em', color: 'var(--fog)' }}
                  onClick={closeTriage}
                >
                  CANCEL
                </button>
              </div>

              {sel.kind === 'image' ? (
                <img className="tri-hero" src={sel.thumb_url ?? sel.url} alt={sel.caption ?? ''} />
              ) : (
                <div className="tri-hero" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26 }}>
                  🎙
                </div>
              )}
              {(sel.caption || sel.source_url) && (
                <div style={{ marginTop: 8, minWidth: 0 }}>
                  {sel.caption && <div style={{ fontSize: 12, color: 'var(--fog)' }}>{sel.caption}</div>}
                  {sel.source_url && (
                    <div className="ib-src">↗ {sel.source_app ?? 'source'} · {sel.source_url}</div>
                  )}
                </div>
              )}

              <div className="eyebrow" style={{ marginTop: 14 }}>DESIGN</div>
              <div className="tri-designs">
                {!dests ? (
                  <div className="mono" style={{ fontSize: 9.5, color: 'var(--faint)', padding: '8px 2px' }}>
                    Loading designs…
                  </div>
                ) : !allDesigns.length ? (
                  <div className="mono" style={{ fontSize: 9.5, color: 'var(--warn)', padding: '8px 2px', lineHeight: 1.7 }}>
                    No designs yet — create one first, then triage.
                  </div>
                ) : (
                  dests.map(g =>
                    g.designs.map(d => (
                      <button
                        key={d.id}
                        className={`tri-design${designId === d.id ? ' on' : ''}`}
                        onClick={() => setDesignId(d.id)}
                      >
                        <span className="t">{d.name}</span>
                        <span className="mono" style={{ fontSize: 8.5, color: 'var(--faint)', flexShrink: 0 }}>
                          {g.project.name.toUpperCase()}
                        </span>
                        <span className="tri-check">✓</span>
                      </button>
                    )),
                  )
                )}
              </div>

              <div className="eyebrow" style={{ marginTop: 12 }}>PHASE</div>
              <div className="chips" style={{ marginTop: 6 }}>
                {phases.map(ph => (
                  <button key={ph} className={`chip${phase === ph ? ' on' : ''}`} onClick={() => setPhase(ph)}>
                    {PHASE_LABELS[ph]}
                  </button>
                ))}
              </div>

              <button
                className="primary-btn press"
                id="triage-confirm"
                disabled={busy || !destDesign}
                style={!destDesign ? { opacity: 0.5 } : undefined}
                onClick={confirm}
              >
                {busy ? 'Sorting…' : destDesign ? `Sort → ${destDesign.name}` : 'Pick a design'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
