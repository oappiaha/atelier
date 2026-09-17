import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  api, PHASE_LABELS,
  type Design, type Media, type Phase, type Project,
} from '../lib/api'
import { createPortal } from 'react-dom'
import { useDialog } from '../lib/dialog'
import { usePanel, useJourney } from '../lib/panel'
import { useShare } from '../lib/store'

/* PRD A7: the Gallery is the cross-project view of Finals AND Editorial media —
   "the answer to 'what do you make?'" — in Stack (fanned carousel) and Ring modes. */

const GALLERY_PHASES: Phase[] = ['final', 'editorial']
type GalFilter = 'all' | Phase

// One bounded session record, retained across route unmounts.
const browsing: { mode: 'stack' | 'ring'; filter: GalFilter; mediaId: string | null; scroll: number } = { mode: 'stack', filter: 'all', mediaId: null, scroll: 0 }

interface GalItem {
  media: Media
  design: Design
  project: Project
}

/** One fan-out fetch: projects → designs → media, kept as flat gallery items. */
function useGalleryItems() {
  return useQuery({
    queryKey: ['gallery'],
    queryFn: async (): Promise<{ items: GalItem[]; projects: Project[] }> => {
      const projects = await api<Project[]>('/projects')
      const designLists = await Promise.all(
        projects.map(p => api<Design[]>(`/projects/${p.id}/designs`)),
      )
      const designs = designLists.flatMap((ds, i) => ds.map(d => ({ d, p: projects[i] })))
      const mediaLists = await Promise.all(
        designs.map(({ d }) => api<Media[]>(`/designs/${d.id}/media`)),
      )
      const items = designs.flatMap(({ d, p }, i) =>
        mediaLists[i]
          .filter(m => m.kind === 'image' && m.phase && GALLERY_PHASES.includes(m.phase))
          .map(media => ({ media, design: d, project: p })),
      )
      // finals lead, then editorial; newest first within a phase
      items.sort(
        (a, b) =>
          GALLERY_PHASES.indexOf(a.media.phase!) - GALLERY_PHASES.indexOf(b.media.phase!)
          || b.media.created_at.localeCompare(a.media.created_at),
      )
      return { items, projects }
    },
  })
}

const bg = (m: Media) => `url(${JSON.stringify(m.thumb_url ?? m.url)})`

/* ── Stack: the mock's fan — absolute cards, transforms move, DOM stays ── */
function FanStack({
  items, idx, setIdx, onOpen,
}: {
  items: GalItem[]
  idx: number
  setIdx: (i: number) => void
  onOpen: (i: GalItem) => void
}) {
  const downX = useRef<number | null>(null)
  const cur = Math.min(idx, items.length - 1)
  const go = (d: number) => setIdx(Math.max(0, Math.min(items.length - 1, cur + d)))

  return (
    <div id="gal-stack">
      <div
        className="fan-stage"
        id="fan-stage"
        onPointerDown={e => { downX.current = e.clientX }}
        onPointerUp={e => {
          if (downX.current === null) return
          const dx = e.clientX - downX.current
          if (Math.abs(dx) > 44) go(dx < 0 ? 1 : -1)
          downX.current = null
        }}
      >
        {items.map((it, i) => {
          const off = i - cur
          const a = Math.abs(off)
          return (
            <button
              key={it.media.id}
              tabIndex={a > 2 ? -1 : 0}
              aria-label={`${i === cur ? 'Open' : 'Focus'} ${it.design.name}`}
              className={`fan-card${a === 0 ? ' center' : ''}`}
              style={{
                transform: `translateX(${off * 42}%) scale(${Math.max(0.72, 1 - a * 0.12)}) rotateY(${off * -10}deg)`,
                opacity: a > 2 ? 0 : 1 - a * 0.26,
                zIndex: 100 - a,
                filter: a === 0 ? 'none' : 'saturate(.8) brightness(.96)',
                pointerEvents: a > 2 ? 'none' : 'auto',
              }}
              onClick={() => (i === cur ? onOpen(it) : setIdx(i))}
            >
              <div className="fan-art" style={{ backgroundImage: bg(it.media) }} />
              <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(180deg,transparent 55%,rgba(20,14,8,.55))' }} />
              <div className="fan-label">
                <div className="mono" style={{ fontSize: 8.5, letterSpacing: '.16em', opacity: 0.8 }}>
                  {String(it.design.index_no).padStart(3, '0')} · {it.media.phase ? PHASE_LABELS[it.media.phase].toUpperCase() : ''}
                </div>
                <div className="syne" style={{ fontSize: 16, fontWeight: 700, marginTop: 2 }}>
                  {it.design.name}
                </div>
              </div>
            </button>
          )
        })}
      </div>
      <div className="fan-nav">
        <button className="glassbtn arrow press" aria-label="Previous" onClick={() => go(-1)}>
          <svg width="14" height="14" viewBox="0 0 15 15" fill="none">
            <path d="M9.5 3.5L5.5 7.5l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <div className="fan-count">
          <span id="fan-idx">{String(cur + 1).padStart(2, '0')}</span> · <span id="fan-name">{items[cur]?.design.name}</span>
        </div>
        <button className="glassbtn arrow press" aria-label="Next" onClick={() => go(1)}>
          <svg width="14" height="14" viewBox="0 0 15 15" fill="none">
            <path d="M5.5 3.5l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
    </div>
  )
}

/* ── Ring: items on a slow-spinning circle; centre holds the share action ── */
function Ring({
  items, onPick, onShare,
}: {
  items: GalItem[]
  onPick: (idx: number) => void
  onShare: () => void
}) {
  const stageRef = useRef<HTMLDivElement>(null)
  // fill the circle even when the archive is young — repeat, mock-style
  const N = Math.max(Math.min(16, items.length * 4), items.length)
  const w = stageRef.current?.clientWidth ?? (typeof window !== 'undefined' ? window.innerWidth : 390)
  const R = Math.min(215, (Math.min(w, window.innerWidth) - 125) / 2)

  return (
    <div className="ring-stage" id="gal-ring" ref={stageRef}>
      <div className="ring" id="ring" style={{ width: R * 2 + 52, height: R * 2 + 52 }}>
        {Array.from({ length: N }, (_, i) => {
          const it = items[i % items.length]
          const angle = (i / N) * Math.PI * 2
          return (
            <button
              key={`${it.media.id}-${i}`}
              aria-label={`Browse ${it.design.name}`}
              className="ring-item"
              style={{ transform: `translate(${Math.cos(angle) * R}px,${Math.sin(angle) * R}px)` }}
              onClick={() => onPick(i % items.length)}
            >
              <div style={{ backgroundImage: bg(it.media) }} />
            </button>
          )
        })}
      </div>
      <div className="ring-center">
        <div className="eyebrow" style={{ marginBottom: 5 }}>Atelier</div>
        <div className="syne" style={{ fontSize: 20, fontWeight: 700 }}>
          {items.length} piece{items.length === 1 ? '' : 's'}.<br />One archive.
        </div>
        <button
          className="press"
          id="ring-share"
          style={{
            marginTop: 12, fontFamily: "'DM Mono'", fontSize: 9.5, letterSpacing: '.12em',
            padding: '9px 15px', borderRadius: 999, background: 'var(--ink)', color: '#FFF',
          }}
          onClick={onShare}
        >
          Share project…
        </button>
      </div>
    </div>
  )
}

export default function Gallery() {
  const navigate = useNavigate()
  const location = useLocation()
  const navigationType = useNavigationType()
  const journey = useJourney()
  const openShare = useShare(s => s.openShare)
  const [mode, setMode] = useState<'stack' | 'ring'>(browsing.mode)
  const [filter, setFilter] = useState<GalFilter>(browsing.filter)
  const [mediaId, setMediaId] = useState<string | null>(browsing.mediaId)

  const { data, isLoading } = useGalleryItems()
  const all = useMemo(() => data?.items ?? [], [data])
  const items = useMemo(
    () => (filter === 'all' ? all : all.filter(it => it.media.phase === filter)),
    [all, filter],
  )
  const count = (p: Phase) => all.filter(it => it.media.phase === p).length
  const fanIdx = Math.max(0, items.findIndex(it => it.media.id === mediaId))
  const setFanIdx = (i: number) => setMediaId(items[i]?.media.id ?? null)
  const focused = items[fanIdx]
  const openDesign = (it: GalItem) => journey.openDesign(it.design.id, 'Gallery')
  const shareProjects = (data?.projects ?? []).map(project => ({
    label: `Share project: ${project.name}`,
    run: () => openShare({ kind: 'project' as const, id: project.id, name: project.name }),
  }))
  const [choosingProject, setChoosingProject] = useState(false)
  const restored = useRef(false)
  useEffect(() => {
    browsing.mode = mode; browsing.filter = filter; browsing.mediaId = mediaId
  }, [mode, filter, mediaId])
  useEffect(() => {
    if (isLoading) return
    const frame = requestAnimationFrame(() => {
      if (!restored.current) {
        // Browser back/forward belongs to ScrollRestoration. An older
        // explicit-return position in history state must not override it.
        if (navigationType !== 'POP') window.scrollTo(0, location.state?.restoreScroll ?? browsing.scroll)
        restored.current = true
      }
    })
    const remember = () => { if (restored.current) browsing.scroll = window.scrollY }
    window.addEventListener('scroll', remember)
    return () => { cancelAnimationFrame(frame); window.removeEventListener('scroll', remember) }
  }, [isLoading, location.state, navigationType])
  usePanel([
    ...(!focused && !isLoading ? [{ primary: true, label: 'Choose a design', run: () => navigate('/') }]
      : mode === 'ring' ? [
        { primary: true, label: 'Browse pieces', disabled: !focused, run: () => setMode('stack') },
        { label: 'Share project…', disabled: !shareProjects.length || !focused, run: () => setChoosingProject(true) },
      ] : [
        { primary: true, label: 'Open design', disabled: !focused, run: () => focused && openDesign(focused) },
        { label: 'Share design', disabled: !focused, run: () => focused && openShare({ kind: 'design', id: focused.design.id, name: focused.design.name }) },
      ]),
    { label: 'View / filter', children: [
      { label: 'Stack', run: () => setMode('stack') },
      { label: 'Ring', run: () => setMode('ring') },
      { label: 'Everything', run: () => { setFilter('all'); setMediaId(null) } },
      ...GALLERY_PHASES.map(p => ({ label: PHASE_LABELS[p], run: () => { setFilter(p); setMediaId(null) } })),
      { label: 'Share project…', disabled: !shareProjects.length, children: shareProjects },
      { label: 'Choose a design', run: () => navigate('/') },
    ] },
  ])
  useDialog('gallery-project-picker', choosingProject, () => setChoosingProject(false))

  return (
    <div className="view">
      <div className="content">
        <div className="hdr">
          <div className="rise">
            {/* short enough that the Stack/Ring toggle keeps the top-right
               slot on a 390px phone instead of wrapping under the title */}
            <div className="eyebrow" style={{ marginBottom: 4 }}>"What do you make?"</div>
            <div className="syne" style={{ fontSize: 28, fontWeight: 800 }}>Gallery</div>
          </div>
          <div className="gal-toggle rise" style={{ animationDelay: '.06s' }}>
            <button className={`gal-mode${mode === 'stack' ? ' on' : ''}`} id="gm-stack" onClick={() => setMode('stack')}>
              Stack
            </button>
            <button className={`gal-mode${mode === 'ring' ? ' on' : ''}`} id="gm-ring" onClick={() => setMode('ring')}>
              Ring
            </button>
          </div>
        </div>

        <div className="chips">
          <button className={`chip${filter === 'all' ? ' on' : ''}`} onClick={() => { setFilter('all'); setMediaId(null) }}>
            Everything · {all.length}
          </button>
          {GALLERY_PHASES.map(p => (
            <button key={p} className={`chip${filter === p ? ' on' : ''}`} onClick={() => { setFilter(p); setMediaId(null) }}>
              {PHASE_LABELS[p]} · {count(p)}
            </button>
          ))}
        </div>

        {isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading…
          </div>
        ) : !items.length ? (
          <div className="panel rise" style={{ padding: 24, marginTop: 10 }}>
            <div className="syne" style={{ fontSize: 16, fontWeight: 700 }}>{filter === 'all' ? 'No gallery photos yet' : `No ${PHASE_LABELS[filter].toLowerCase()} photos yet`}</div>
            <p style={{ fontSize: 12.5, color: 'var(--fog)', marginTop: 6, lineHeight: 1.6 }}>
              Choose a design and add a Final product or Editorial photo to see it here.
            </p>
            <button className="primary-btn press" onClick={() => navigate('/')}>Choose a design</button>
          </div>
        ) : mode === 'stack' ? (
          /* key={filter}: filter changes the CONTENT → rebuild + re-animate (TDD §10.2) */
          <FanStack key={filter} items={items} idx={fanIdx} setIdx={setFanIdx} onOpen={openDesign} />
        ) : (
          /* mock behaviour: tapping a ring item lands on that card in the stack */
          <Ring
            key={filter}
            items={items}
            onPick={i => { setFanIdx(i); setMode('stack') }}
            onShare={() => setChoosingProject(true)}
          />
        )}
      </div>
      {choosingProject && createPortal(<div className="sheet-wrap open" id="gallery-project-picker" role="dialog" aria-modal="true" aria-label="Choose a project to share">
        <div className="backdrop" onClick={() => setChoosingProject(false)} />
        <div className="sheet"><div className="grabber" /><div className="dialog-heading"><h2 className="syne">Share a project</h2><button className="chip" onClick={() => setChoosingProject(false)}>Close</button></div>
          <p>Choose the collection this link will include.</p>
          <div className="panel-menu-items">{shareProjects.map(a => <button key={a.label} className="panel-key press" onClick={() => { setChoosingProject(false); a.run() }}>{a.label}</button>)}</div>
        </div>
      </div>, document.body)}
    </div>
  )
}
