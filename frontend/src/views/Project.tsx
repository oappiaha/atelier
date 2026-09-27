import { useState } from 'react'
import WordmarkSheet from '../components/WordmarkSheet'
import BrowseControls from '../components/BrowseControls'
import { useBrowse } from '../lib/browse'
import { useNavigate, useParams } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  api, STATUSES, STATUS_CLASS, STATUS_LABELS,
  type Design, type DesignStatus, type Project as ProjectT,
} from '../lib/api'
import { usePanel, useJourney } from '../lib/panel'
import { useCapture, useNewDesign, useShare } from '../lib/store'

const FALLBACK_ART = 'linear-gradient(150deg,#DCE4EE,#B9C6D8 55%,#8FA2BC)'

/* categories are free text (vault folder names) — display-normalize once */
const titleCase = (s: string) =>
  s.toLowerCase().replace(/(^|\s)\S/g, c => c.toUpperCase())

export default function Project() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const [wordmarkOpen, setWordmarkOpen] = useState(false)
  const journey = useJourney()
  const openShare = useShare(s => s.openShare)
  const openNewDesign = useNewDesign(s => s.openNewDesign)

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api<ProjectT[]>('/projects'),
  })
  const project = projects?.find(p => p.id === projectId)

  // unfiltered list: chip counts + the "All" grid
  const all = useQuery({
    queryKey: ['designs', projectId, 'all'],
    queryFn: () => api<Design[]>(`/projects/${projectId}/designs`),
  })
  const browse = useBrowse(`/p/${projectId}`, !all.isLoading)
  const { status, category, catOpen } = browse
  const setStatus = (status: 'all' | DesignStatus) => browse.set({ status })
  const setCategory = (category: string) => browse.set({ category })
  // status chips drive a real ?status= query (the API filter).
  // NB: the key must NOT collide with the 'all' query above — when status==='all'
  // a shared key would let this (disabled) queryFn win a refetch after
  // invalidation and fire ?status=all → 422.
  const filtered = useQuery({
    queryKey: ['designs', projectId, 'status', status],
    queryFn: () => api<Design[]>(`/projects/${projectId}/designs?status=${status}`),
    enabled: status !== 'all',
    placeholderData: keepPreviousData,
  })

  // category chips: built from the loaded designs, shown only when the index
  // actually spans >=2 categories; filtering is client-side ON TOP of the
  // status filter (which stays a real ?status= API query)
  const categories = Array.from(
    new Set((all.data ?? []).flatMap(d => (d.category ? [titleCase(d.category)] : []))),
  ).sort()
  const catActive = category !== 'all' && categories.includes(category)
  const byStatus = status === 'all' ? all.data : filtered.data
  const categorized = catActive
    ? byStatus?.filter(d => d.category && titleCase(d.category) === category)
    : byStatus
  const designs = categorized?.filter(d => `${d.name} ${d.index_no} ${d.materials ?? ''}`.toLowerCase().includes(browse.search.trim().toLowerCase())).slice().sort((a, b) =>
    browse.sort === 'name' ? a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }) : browse.sort === 'recent' ? b.created_at.localeCompare(a.created_at) : a.index_no - b.index_no)
  const count = (s: DesignStatus) => all.data?.filter(d => d.status === s).length ?? 0

  usePanel([
    { label: 'Projects', run: () => navigate('/') },
    { primary: true, label: 'New design', disabled: !project, run: () => project && openNewDesign(project) },
    { label: 'More', children: [
      { label: 'Wordmark', disabled: !project, run: () => setWordmarkOpen(true) },
      { label: 'Top', run: () => window.scrollTo({ top: 0, behavior: 'smooth' }) },
      { label: 'Share', disabled: !project, run: () => project && openShare({ kind: 'project', ...project }) },
      { label: 'Capture', run: useCapture.getState().openCapture },
    ] },
  ])

  return (
    <div className="view">
      {wordmarkOpen && project && <WordmarkSheet key={project.id} project={project} onClose={() => setWordmarkOpen(false)} />}
      <div className="content">
        <button className="back-inline press" onClick={() => navigate('/')}>
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
            <path d="M9 3L5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          All projects
        </button>
        <header className="collection-header">
          <h1 className="syne collection-title">
            {project?.wordmark
              ? <img className="collection-wordmark" src={project.wordmark} alt={project.name} />
              : project?.name ?? 'Loading…'}
          </h1>
        </header>

        <BrowseControls {...browse} onLayout={layout => browse.set({ layout })} onSearch={search => browse.set({ search })} onSort={sort => browse.set({ sort })} />

        {/* desktop: category row over status row, as before. Mobile (≤899px):
           the status row leads and the category row hides behind the CATEGORY
           toggle chip — one chip row by default, never two pushing the grid
           below the fold. An active category keeps its row visible. */}
        <div className="filter-rows">
          {categories.length >= 2 && (
            <div
              className={`chips cat-row${catOpen || catActive ? ' open' : ''}`}
              id="category-chips"
            >
              <button
                className={`chip${catActive ? '' : ' on'}`}
                data-cat="all"
                onClick={() => setCategory('all')}
              >
                All
              </button>
              {categories.map(c => (
                <button
                  key={c}
                  className={`chip${category === c ? ' on' : ''}`}
                  data-cat={c}
                  onClick={() => setCategory(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          <div className="chips" id="status-chips">
            {categories.length >= 2 && (
              <button
                className={`chip cat-toggle${catActive ? ' on' : ''}`}
                id="cat-toggle"
                onClick={() => browse.set({ catOpen: !catOpen })}
              >
                {catActive ? category : 'Category'}
                <svg width="9" height="9" viewBox="0 0 10 10" fill="none">
                  <path d="M2.5 4l2.5 2.5L7.5 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            )}
            <button
              className={`chip${status === 'all' ? ' on' : ''}`}
              data-f="all"
              onClick={() => setStatus('all')}
            >
              All · {all.data?.length ?? 0}
            </button>
            {STATUSES.map(s => (
              <button
                key={s}
                className={`chip${status === s ? ' on' : ''}`}
                data-f={s}
                onClick={() => setStatus(s)}
              >
                {STATUS_LABELS[s]} · {count(s)}
              </button>
            ))}
          </div>
        </div>

        {all.isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading…
          </div>
        ) : (
          <>
            {!designs?.length && (
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
                {browse.search || status !== 'all' || catActive ? 'No matching designs. Try another search or filter.' : 'Nothing here yet.'}
              </div>
            )}
            {/* key={filter}: the filter changed the CONTENT, so the grid re-animates —
               correct per TDD §10.2. Within a filter, cards keep stable uuid keys. */}
            <div className={`dgrid${browse.layout === 'list' ? ' archive-list' : ''}`} id="dgrid" key={`${status}:${catActive ? category : 'all'}`}>
              {designs?.map((d, k) => (
                <button
                  key={d.id}
                  className="dcard"
                  // cap the stagger: with 100+ designs an uncapped k*0.06s left
                  // the last card invisible for 6+ seconds
                  style={{ animation: 'tileIn .55s var(--ease) backwards', animationDelay: `${Math.min(k, 11) * 0.05}s` }}
                  onClick={() => journey.openDesign(d.id, project?.name ?? 'Project')}
                >
                  <div className="card-idx">{String(d.index_no).padStart(3, '0')}</div>
                  <div className="art" style={d.cover_url ? undefined : { background: FALLBACK_ART }}>
                    {d.cover_url && (
                      /* real <img>: lazy-loads offscreen covers — a bg-image div
                         fetched all 100+ eagerly */
                      <img className="art-img" src={d.cover_url} alt="" loading="lazy" decoding="async" />
                    )}
                  </div>
                  <div className="label">
                    <div className="dname">{d.name}</div>
                    <div className={`dstatus ${STATUS_CLASS[d.status]}`}>
                      <span className="dot" />
                      {STATUS_LABELS[d.status]}
                    </div>
                  </div>
                </button>
              ))}
              {project && (
                <button
                  className="dcard dcard-new press"
                  id="dcard-new"
                  style={{ animation: 'tileIn .55s var(--ease) both', animationDelay: `${Math.min(designs?.length ?? 0, 11) * 0.06}s` }}
                  onClick={() => openNewDesign({ id: project.id, name: project.name })}
                >
                  <div className="plus">+</div>
                  <div className="t">New design</div>
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
