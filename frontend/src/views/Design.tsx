import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api,
  PHASES, PHASE_LABELS, STATUS_CLASS, STATUS_LABELS,
  type Design as DesignT, type Entry, type Media, type Phase, type Project as ProjectT,
} from '../lib/api'
import Lightbox from '../components/Lightbox'
import { toast, useCapture, useShare } from '../lib/store'

type PhaseFilter = 'all' | Phase
type LbState = { items: Media[]; index: number; phaseLabel: string } | null

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })

/* ── hero: two stacked layers cross-fade; the DOM is never rebuilt ── */
function useCrossfade(src: string | null) {
  const [state, set] = useState<{ a: string | null; b: string | null; showA: boolean }>({
    a: src, b: null, showA: true,
  })
  useEffect(() => {
    set(s => {
      const cur = s.showA ? s.a : s.b
      if (!src || src === cur) return s
      if (cur === null) return s.showA ? { ...s, a: src } : { ...s, b: src }
      return s.showA ? { ...s, b: src, showA: false } : { ...s, a: src, showA: true }
    })
  }, [src])
  return state
}

const bg = (m: Media) => `url(${JSON.stringify(m.thumb_url ?? m.url)})`

/* ── one timeline item: mounts once, filter toggles the .gone class ── */
function TlItem({
  entry, hidden, delay, onOpen,
}: {
  entry: Entry
  hidden: boolean
  delay: number
  onOpen: (media: Media[], index: number, phaseLabel: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // entrance on MOUNT only (TDD §10.2)
    const t = setTimeout(() => ref.current?.classList.add('in'), 40 + delay)
    return () => clearTimeout(t)
  }, [delay])

  const imgs = entry.media.filter(m => m.kind === 'image')
  const audios = entry.media.filter(m => m.kind === 'audio')
  const label = PHASE_LABELS[entry.phase]
  const open = (i: number) => onOpen(imgs, i, label)

  return (
    <div ref={ref} className={`tl-item reveal${hidden ? ' gone' : ''}`} data-ph={entry.phase}>
      <div className="tl-head">
        <span className="phase-tag">{label}</span>
        <span className="tl-date">{fmtDate(entry.occurred_at)}</span>
        {entry.is_open && (
          <span className="mono" style={{ fontSize: 8.5, color: 'var(--accent)', letterSpacing: '.1em' }}>
            · STILL GROWING
          </span>
        )}
      </div>

      {audios.map(a => (
        <VoiceCard key={a.id} media={a} />
      ))}

      {imgs.length === 0 && !audios.length && entry.body && (
        <div className="tl-note">{entry.body}</div>
      )}

      {imgs.length > 0 && (
        <div className="tl-card">
          {imgs.length === 1 && (
            <div className="tl-media" style={{ height: 150 }} onClick={() => open(0)}>
              <div style={{ backgroundImage: bg(imgs[0]) }} />
            </div>
          )}
          {(imgs.length === 2 || imgs.length === 3) && (
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${imgs.length},1fr)`, gap: 2 }}>
              {imgs.map((m, i) => (
                <div key={m.id} className="tl-media" style={{ height: 88 }} onClick={() => open(i)}>
                  <div style={{ backgroundImage: bg(m) }} />
                </div>
              ))}
            </div>
          )}
          {imgs.length >= 4 && (
            <div className="mood-grid">
              {imgs.map((m, i) => (
                <div key={m.id} style={{ backgroundImage: bg(m) }} onClick={() => open(i)} />
              ))}
            </div>
          )}
          {(entry.body || imgs[0]?.caption) && (
            <div className="tl-cap" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <span>{entry.body ?? imgs[0]?.caption}</span>
              {imgs[0]?.source_url && (
                <span className="mono" style={{ fontSize: 8.5, color: 'var(--faint)', flexShrink: 0 }}>
                  ↗ {imgs[0].source_app ?? 'source'}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const WAVE_BARS = [8, 14, 20, 26, 18, 12, 22, 28, 16, 10, 18, 24, 14, 8, 16, 26, 20, 12, 24, 18, 10, 14, 22, 16]

function VoiceCard({ media }: { media: Media }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const secs = media.duration_ms ? Math.round(media.duration_ms / 1000) : null
  const toggle = () => {
    const a = audioRef.current
    if (!a) return
    if (playing) a.pause()
    else void a.play()
    setPlaying(!playing)
  }
  return (
    <div className={`voice press${playing ? ' playing' : ''}`} onClick={toggle}>
      <div className="play">
        {playing ? (
          <svg width="10" height="10" viewBox="0 0 12 12" fill="currentColor">
            <rect x="2" y="1.5" width="2.8" height="9" rx="1" /><rect x="7.2" y="1.5" width="2.8" height="9" rx="1" />
          </svg>
        ) : (
          <svg width="11" height="11" viewBox="0 0 13 13" fill="currentColor">
            <path d="M3.5 2.2v8.6c0 .5.55.8.97.53l6.4-4.3a.63.63 0 000-1.06l-6.4-4.3a.63.63 0 00-.97.53z" />
          </svg>
        )}
      </div>
      <div className="wave">
        {WAVE_BARS.map((h, i) => (
          <i key={i} style={{ height: h, animationDelay: `${i * 0.045}s` }} />
        ))}
      </div>
      <div className="mono" style={{ fontSize: 10, color: 'var(--fog)', flexShrink: 0 }}>
        {secs !== null ? `0:${String(secs).padStart(2, '0')}` : '· · ·'}
      </div>
      <audio ref={audioRef} src={media.url} onEnded={() => setPlaying(false)} />
    </div>
  )
}

function MTile({
  media, delay, onOpen,
}: {
  media: Media
  delay: number
  onOpen: () => void
}) {
  return (
    <div className="m-tile" style={{ animationDelay: `${delay}s` }} onClick={onOpen}>
      <img className="m-art" src={media.thumb_url ?? media.url} alt={media.caption ?? media.phase ?? 'media'} />
      <div className="m-shine" />
      <div className="m-open">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 8L8 2M8 2H4M8 2v4" stroke="#17181D" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <div className="m-tag">{media.phase ? PHASE_LABELS[media.phase] : media.kind}</div>
      {media.source_app === 'wada' && (
        <div className="m-tag" style={{ left: 'auto', right: 8 }}>WADA</div>
      )}
    </div>
  )
}

export default function Design() {
  const { designId } = useParams<{ designId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const setDesignCtx = useCapture(s => s.setDesignCtx)
  const openShare = useShare(s => s.openShare)

  const design = useQuery({
    queryKey: ['design', designId],
    queryFn: () => api<DesignT>(`/designs/${designId}`),
  })
  const timeline = useQuery({
    queryKey: ['timeline', designId],
    queryFn: () => api<Entry[]>(`/designs/${designId}/timeline`),
  })
  const media = useQuery({
    queryKey: ['media', designId],
    queryFn: () => api<Media[]>(`/designs/${designId}/media`),
  })
  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api<ProjectT[]>('/projects'),
  })
  const projectName = projects?.find(p => p.id === design.data?.project_id)?.name ?? 'Back'

  const [view, setView] = useState<'timeline' | 'media'>('timeline')
  const [phase, setPhase] = useState<PhaseFilter>('all')
  const [heroIdx, setHeroIdx] = useState(0)
  const [lb, setLb] = useState<LbState>(null)

  // register this design as the capture destination
  useEffect(() => {
    if (design.data) setDesignCtx({ id: design.data.id, name: design.data.name })
    return () => setDesignCtx(null)
  }, [design.data, setDesignCtx])

  // hero rail: the CHOSEN cover leads, real photos before wada colorway
  // media (explorations shouldn't hijack the product's face — Beezy
  // 2026-08-04: "I'd rather choose which covers to pin"). Stable within
  // each tier, so recency still orders the rest.
  const coverId = design.data?.cover_media_id
  const images = useMemo(() => {
    const imgs = (media.data ?? []).filter(m => m.kind === 'image')
    const tier = (m: Media) =>
      m.id === coverId ? 0 : m.source_app === 'wada' ? 2 : 1
    return imgs
      .map((m, i) => ({ m, i }))
      .sort((a, b) => tier(a.m) - tier(b.m) || a.i - b.i)
      .map(x => x.m)
  }, [media.data, coverId])
  const heroImages = images.slice(0, 8)
  const heroShown = heroImages[Math.min(heroIdx, Math.max(heroImages.length - 1, 0))] ?? null
  const heroSrc = heroShown?.url ?? design.data?.cover_url ?? null
  const hero = useCrossfade(heroSrc)

  // delete product: destructive + final — entries/media/studies cascade
  const [confirmDelete, setConfirmDelete] = useState(false)
  const deleteDesignM = useMutation({
    mutationFn: () => api<void>(`/designs/${designId}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast('Product deleted')
      qc.invalidateQueries({ queryKey: ['designs'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      qc.invalidateQueries({ queryKey: ['studies'] })
      navigate(design.data ? `/p/${design.data.project_id}` : '/')
    },
    onError: () => toast('Could not delete the product'),
  })

  // Studio Shot: PhotoRoom presentation derivative → editorial timeline entry
  const studioShotM = useMutation({
    mutationFn: (mediaId: string) =>
      api<{ created: boolean }>(`/media/${mediaId}/studio-shot`, { method: 'POST' }),
    onSuccess: out => {
      toast(out.created
        ? 'Studio shot saved to the timeline'
        : 'Already shot — it’s on the timeline')
      qc.invalidateQueries({ queryKey: ['timeline', designId] })
      qc.invalidateQueries({ queryKey: ['media', designId] })
      qc.invalidateQueries({ queryKey: ['design', designId] })
    },
    onError: () => toast('Studio shot failed — PhotoRoom may be unavailable'),
  })

  const coverM = useMutation({
    mutationFn: (mediaId: string) =>
      api<DesignT>(`/designs/${designId}`, {
        method: 'PATCH',
        body: JSON.stringify({ cover_media_id: mediaId }),
      }),
    onSuccess: () => {
      toast('Cover set')
      qc.invalidateQueries({ queryKey: ['design', designId] })
      qc.invalidateQueries({ queryKey: ['designs'] })
    },
    onError: () => toast('Could not set the cover'),
  })

  // phases present in the archive, canonical order
  const presentPhases = useMemo(() => {
    const set = new Set<Phase>()
    timeline.data?.forEach(e => set.add(e.phase))
    media.data?.forEach(m => m.phase && set.add(m.phase))
    return PHASES.filter(p => set.has(p))
  }, [timeline.data, media.data])

  const entries = timeline.data ?? []
  const visibleEntries = entries.filter(e => phase === 'all' || e.phase === phase)
  const filteredMedia = images.filter(m => phase === 'all' || m.phase === phase)

  const openLb = (items: Media[], index: number, phaseLabel: string) => {
    if (items.length) setLb({ items, index, phaseLabel })
  }

  // ── WADA STUDIO entry: reopen the remembered study, or create one on the
  // design's photo. Regions come from the cache; POST /segment + poll is the
  // non-crashing fallback for a photo that was never segmented. ──
  // "Explore colorways" now lands on the per-product studio HUB (past
  // studies + one deliberate NEW STUDY key). The old behaviour — silently
  // creating a draft on every tap without a localStorage memo — minted
  // duplicate empty drafts on every new device.
  const openStudio = () => designId && navigate(`/d/${designId}/studio`)

  const d = design.data

  return (
    <div className="view">
      <div className="content">
        <button
          className="back-inline press"
          onClick={() => (d ? navigate(`/p/${d.project_id}`) : navigate('/'))}
        >
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
            <path d="M9 3L5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {projectName}
        </button>
        <div className="hdr" style={{ paddingTop: 12, alignItems: 'flex-start' }}>
          <div className="rise">
            <div className={`dstatus ${d ? STATUS_CLASS[d.status] : 'st-dev'}`} style={{ marginBottom: 6 }}>
              <span className="dot" />
              {d ? `${STATUS_LABELS[d.status]} · ${d.entry_count} entries · ${d.media_count} media` : '…'}
            </div>
            <div className="syne" style={{ fontSize: 28, fontWeight: 800 }}>{d?.name ?? '…'}</div>
          </div>
          <button
            className="glassbtn press rise"
            aria-label="Share"
            style={{ width: 40, height: 40, animationDelay: '.06s' }}
            onClick={() =>
              d ? openShare({ kind: 'design', id: d.id, name: d.name }) : toast('Still loading…')
            }
          >
            <svg width="14" height="15" viewBox="0 0 15 16" fill="none">
              <path d="M7.5 1v9M7.5 1L4 4.5M7.5 1L11 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M2 8v5.5h11V8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>

        <div className="detail-grid">
          {/* HERO + RAIL */}
          <div className="detail-hero rise" style={{ animationDelay: '.08s' }}>
            <div className="hero-stack">
              <div className="hero-ghost g2" />
              <div className="hero-ghost g1" />
              <div className="hero-img" id="hero">
                <div
                  className={`hero-layer${hero.showA ? '' : ' out'}`}
                  id="hero-a"
                  style={hero.a ? { backgroundImage: `url(${JSON.stringify(hero.a)})` } : undefined}
                />
                <div
                  className={`hero-layer${hero.showA ? ' out' : ''}`}
                  id="hero-b"
                  style={hero.b ? { backgroundImage: `url(${JSON.stringify(hero.b)})` } : undefined}
                />
                <div className="hero-sheen" />
                {heroShown && (
                  <button
                    className={`hero-cover mono press${heroShown.id === coverId ? ' on' : ''}`}
                    id="hero-set-cover"
                    disabled={coverM.isPending || heroShown.id === coverId}
                    title="Use this image as the design's cover"
                    onClick={() => coverM.mutate(heroShown.id)}
                  >
                    {heroShown.id === coverId ? '★ COVER' : '☆ SET AS COVER'}
                  </button>
                )}
              </div>
            </div>
            <div className="rail" id="hero-rail">
              {heroImages.map((m, i) => (
                <div
                  key={m.id}
                  className={`rail-item${i === heroIdx ? ' on' : ''}`}
                  style={{ backgroundImage: bg(m) }}
                  onClick={() => setHeroIdx(i)}
                >
                  <i />
                </div>
              ))}
            </div>
            <div className="actions">
              <button className="action press" id="open-studio" onClick={openStudio}>
                <div className="a-eyebrow" style={{ color: 'var(--accent)' }}>WADA STUDIO</div>
                <div className="a-t">Explore colorways</div>
              </button>
              <button className="action press" onClick={() => toast('3D — future flex')}>
                <div className="a-eyebrow" style={{ color: 'var(--faint)' }}>VIEW</div>
                <div className="a-t">Spin in 3D</div>
              </button>
            </div>
          </div>

          {/* TIMELINE / MEDIA */}
          <div className="rise" style={{ animationDelay: '.14s' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, margin: '4px 0 10px', flexWrap: 'wrap' }}>
              <div className="seg">
                <button
                  className={view === 'timeline' ? 'on' : ''}
                  id="dv-timeline"
                  onClick={() => setView('timeline')}
                >
                  Timeline
                </button>
                <button
                  className={view === 'media' ? 'on' : ''}
                  id="dv-media"
                  onClick={() => setView('media')}
                >
                  Media
                </button>
              </div>
              <div className="mono" style={{ fontSize: 9, color: 'var(--faint)', letterSpacing: '.1em' }} id="dv-count">
                {view === 'media' ? `${filteredMedia.length} media` : `${visibleEntries.length} entries`}
              </div>
            </div>
            <div className="chips" id="phase-chips">
              <button
                className={`chip${phase === 'all' ? ' on' : ''}`}
                data-ph="all"
                onClick={() => setPhase('all')}
              >
                All
              </button>
              {presentPhases.map(p => (
                <button
                  key={p}
                  className={`chip${phase === p ? ' on' : ''}`}
                  data-ph={p}
                  onClick={() => setPhase(p)}
                >
                  {PHASE_LABELS[p]}
                </button>
              ))}
            </div>

            {/* timeline: built once; the phase filter toggles .gone (TDD §10.2) */}
            <div className="tl" id="detail-timeline" style={{ display: view === 'timeline' ? 'block' : 'none' }}>
              {timeline.isLoading ? (
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
                  Loading…
                </div>
              ) : !visibleEntries.length ? (
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
                  Nothing here yet.
                </div>
              ) : null}
              {entries.map((e, k) => (
                <TlItem
                  key={e.id}
                  entry={e}
                  hidden={phase !== 'all' && e.phase !== phase}
                  delay={k * 60}
                  onOpen={openLb}
                />
              ))}
            </div>

            {/* media masonry: filter changes CONTENT → remount + re-animate is correct */}
            <div className="media-wrap" id="detail-media" style={{ display: view === 'media' ? 'block' : 'none' }}>
              {filteredMedia.length ? (
                <div className="masonry" id="masonry" key={phase}>
                  {filteredMedia.map((m, k) => (
                    <MTile
                      key={m.id}
                      media={m}
                      delay={k * 0.05}
                      onOpen={() =>
                        openLb(
                          filteredMedia,
                          k,
                          phase === 'all' ? 'Media' : PHASE_LABELS[phase],
                        )
                      }
                    />
                  ))}
                </div>
              ) : (
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
                  Nothing here yet.
                </div>
              )}
            </div>
          </div>
        </div>

        {/* danger zone: deleting a product takes its WHOLE history with it */}
        <div className="danger-zone rise">
          {confirmDelete ? (
            <div className="gen-confirm" id="design-delete-confirm">
              <span className="mono" style={{ fontSize: 9.5, color: 'var(--warn)' }}>
                DELETES {d?.entry_count ?? 0} ENTRIES · {d?.media_count ?? 0} MEDIA · ALL STUDIES — FOREVER
              </span>
              <button className="kbtn gc-open" id="design-delete-cancel" onClick={() => setConfirmDelete(false)}>
                KEEP IT
              </button>
              <button
                className="kbtn gc-cancel dz-go"
                id="design-delete-go"
                disabled={deleteDesignM.isPending}
                onClick={() => deleteDesignM.mutate()}
              >
                {deleteDesignM.isPending ? '…' : 'DELETE FOREVER'}
              </button>
            </div>
          ) : (
            <button className="dz-open mono press" id="design-delete" onClick={() => setConfirmDelete(true)}>
              DELETE THIS PRODUCT…
            </button>
          )}
        </div>
      </div>

      {lb && (
        <Lightbox
          items={lb.items}
          index={lb.index}
          phaseLabel={lb.phaseLabel}
          onClose={() => setLb(null)}
          onStudioShot={m => studioShotM.mutate(m.id)}
          studioShotBusy={studioShotM.isPending}
        />
      )}
    </div>
  )
}
