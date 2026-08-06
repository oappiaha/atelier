import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  api, STATUSES, STATUS_CLASS, STATUS_LABELS,
  type Design, type DesignStatus, type Project as ProjectT,
} from '../lib/api'
import { useNewDesign, useShare } from '../lib/store'

const FALLBACK_ART = 'linear-gradient(150deg,#DCE4EE,#B9C6D8 55%,#8FA2BC)'

export default function Project() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const openShare = useShare(s => s.openShare)
  const openNewDesign = useNewDesign(s => s.openNewDesign)
  const [status, setStatus] = useState<'all' | DesignStatus>('all')

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

  const designs = status === 'all' ? all.data : filtered.data
  const count = (s: DesignStatus) => all.data?.filter(d => d.status === s).length ?? 0

  return (
    <div className="view">
      <div className="content">
        <button className="back-inline press" onClick={() => navigate('/')}>
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
            <path d="M9 3L5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          All projects
        </button>
        <div className="hdr" style={{ paddingTop: 12 }}>
          <div className="rise">
            <div className="eyebrow" style={{ marginBottom: 4 }}>
              {project?.kicker ?? 'PROJECT'} · index of {all.data?.length ?? project?.design_count ?? '—'}
            </div>
            <div className="syne" style={{ fontSize: 28, fontWeight: 800 }}>
              {project?.name ?? '…'}
            </div>
          </div>
          {project && (
            <button
              className="glassbtn press rise"
              aria-label="Share"
              id="project-share"
              style={{ width: 40, height: 40, animationDelay: '.06s' }}
              onClick={() => openShare({ kind: 'project', id: project.id, name: project.name })}
            >
              <svg width="14" height="15" viewBox="0 0 15 16" fill="none">
                <path d="M7.5 1v9M7.5 1L4 4.5M7.5 1L11 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M2 8v5.5h11V8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>

        <div className="chips" style={{ marginBottom: 14 }}>
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

        {all.isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading…
          </div>
        ) : (
          <>
            {!designs?.length && (
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
                Nothing here yet.
              </div>
            )}
            {/* key={status}: the filter changed the CONTENT, so the grid re-animates —
               correct per TDD §10.2. Within a filter, cards keep stable uuid keys. */}
            <div className="dgrid" id="dgrid" key={status}>
              {designs?.map((d, k) => (
                <div
                  key={d.id}
                  className="dcard"
                  // cap the stagger: with 100+ designs an uncapped k*0.06s left
                  // the last card invisible for 6+ seconds
                  style={{ animation: 'tileIn .55s var(--ease) backwards', animationDelay: `${Math.min(k, 11) * 0.05}s` }}
                  onClick={() => navigate(`/d/${d.id}`)}
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
                </div>
              ))}
              {project && (
                <button
                  className="dcard dcard-new press"
                  id="dcard-new"
                  style={{ animation: 'tileIn .55s var(--ease) both', animationDelay: `${(designs?.length ?? 0) * 0.06}s` }}
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
