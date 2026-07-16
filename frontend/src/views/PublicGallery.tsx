import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ApiError, fetchPublicProjection, PHASE_LABELS, STATUS_CLASS, STATUS_LABELS, publicPath,
  type PublicDesign, type PublicMedia, type PublicNote,
} from '../lib/api'

/* PUBLIC /s/:slug (TDD §10.1) — no app shell, no auth, no login redirect.
   Renders the backend's public projection as-is: it contains no internal ids;
   media are opaque API-relative paths that 307-redirect to storage.
   PRD §7 DoD: this page must look correct on a phone. */

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })

function MediaTile({ m, delay }: { m: PublicMedia; delay: number }) {
  if (m.kind === 'audio') {
    return (
      <div className="pub-tile" style={{ animationDelay: `${delay}s`, padding: '14px 15px' }}>
        <div className="mono" style={{ fontSize: 9.5, color: 'var(--fog)', letterSpacing: '.1em' }}>
          VOICE NOTE{m.duration_ms ? ` · 0:${String(Math.round(m.duration_ms / 1000)).padStart(2, '0')}` : ''}
        </div>
        {m.caption && <div style={{ fontSize: 12.5, marginTop: 6 }}>{m.caption}</div>}
      </div>
    )
  }
  return (
    <figure className="pub-tile" style={{ animationDelay: `${delay}s` }}>
      <img
        src={publicPath(m.thumb_url ?? m.url)}
        alt={m.caption ?? m.phase ?? 'photo'}
        loading="lazy"
        width={m.width ?? undefined}
        height={m.height ?? undefined}
      />
      <div className="m-tag">{m.phase ? PHASE_LABELS[m.phase] : 'Photo'}</div>
    </figure>
  )
}

/* full scope: media + notes merged into one chronological timeline per design */
function FullTimeline({ design }: { design: PublicDesign }) {
  const rows: ({ t: 'media'; at: string; m: PublicMedia } | { t: 'note'; at: string; n: PublicNote })[] = [
    ...design.media.map(m => ({ t: 'media' as const, at: m.created_at, m })),
    ...design.notes.map(n => ({ t: 'note' as const, at: n.occurred_at, n })),
  ].sort((a, b) => a.at.localeCompare(b.at))

  return (
    <div className="tl" style={{ marginTop: 16 }}>
      {rows.map((r, i) => (
        <div className="tl-item" key={r.t === 'media' ? `m${r.m.index}` : `n${i}`} style={{ opacity: 1 }}>
          <div className="tl-head">
            <span className="phase-tag">
              {r.t === 'media'
                ? (r.m.phase ? PHASE_LABELS[r.m.phase] : 'Photo')
                : PHASE_LABELS[r.n.phase]}
            </span>
            <span className="tl-date">{fmtDate(r.at)}</span>
          </div>
          {r.t === 'note' ? (
            <div className="tl-note">{r.n.body}</div>
          ) : r.m.kind === 'audio' ? (
            <div className="tl-note mono" style={{ fontSize: 10.5, color: 'var(--fog)' }}>
              VOICE NOTE{r.m.duration_ms ? ` · 0:${String(Math.round(r.m.duration_ms / 1000)).padStart(2, '0')}` : ''}
            </div>
          ) : (
            <div className="tl-card">
              <img
                src={publicPath(r.m.thumb_url ?? r.m.url)}
                alt={r.m.caption ?? r.m.phase ?? 'photo'}
                loading="lazy"
                style={{ display: 'block', width: '100%', height: 'auto' }}
              />
              {r.m.caption && <div className="tl-cap">{r.m.caption}</div>}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function NotFound() {
  return (
    <div className="pub-center">
      <div className="panel rise" style={{ width: 'min(380px, calc(100vw - 32px))', padding: 28, textAlign: 'center' }}>
        <div className="eyebrow">ATELIER</div>
        <div className="syne" style={{ fontSize: 20, fontWeight: 800, marginTop: 8 }}>
          This gallery isn't live
        </div>
        <p style={{ fontSize: 12.5, color: 'var(--fog)', marginTop: 8, lineHeight: 1.6 }}>
          The link may have been mistyped, or its owner switched it off.
        </p>
      </div>
    </div>
  )
}

export default function PublicGallery() {
  const { slug } = useParams<{ slug: string }>()
  const { data, error, isLoading } = useQuery({
    queryKey: ['public', slug],
    queryFn: () => fetchPublicProjection(slug!),
    enabled: !!slug,
    retry: false,
    staleTime: Infinity, // every refetch costs public rate-limit budget
  })

  if (isLoading) {
    return (
      <div className="pub-center">
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)' }}>Loading…</div>
      </div>
    )
  }

  if (error || !data) {
    const status = error instanceof ApiError ? error.status : 0
    if (status === 429) {
      return (
        <div className="pub-center">
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', textAlign: 'center', lineHeight: 2 }}>
            A little too popular right now.<br />Try again in a minute.
          </div>
        </div>
      )
    }
    return <NotFound />
  }

  const mediaCount = data.designs.reduce((n, d) => n + d.media.length, 0)

  return (
    <div className="pub">
      <div className="pub-content">
        <header className="pub-hdr rise">
          <div className="eyebrow" style={{ marginBottom: 6 }}>
            {data.project?.kicker ?? 'SHARED GALLERY'}
          </div>
          <div className="syne" style={{ fontSize: 'clamp(26px, 7vw, 34px)', fontWeight: 800 }}>
            {data.project?.name ?? 'Atelier'}
          </div>
          <div className="mono" style={{ fontSize: 9.5, letterSpacing: '.14em', color: 'var(--faint)', marginTop: 8 }}>
            {data.scope === 'finals' ? 'FINALS' : 'FULL TIMELINE'} · {mediaCount} PIECE{mediaCount === 1 ? '' : 'S'} · VIEW ONLY
          </div>
        </header>

        {data.designs.map(d => (
          <section className="pub-design" key={`${d.index_no}-${d.name}`}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
              <span className="mono" style={{ fontSize: 9.5, color: 'var(--faint)', letterSpacing: '.1em' }}>
                {String(d.index_no).padStart(3, '0')}
              </span>
              <span className="syne" style={{ fontSize: 19, fontWeight: 700 }}>{d.name}</span>
              <span className={`dstatus ${STATUS_CLASS[d.status]}`}>
                <span className="dot" />
                {STATUS_LABELS[d.status]}
              </span>
            </div>
            {data.scope === 'full' ? (
              <FullTimeline design={d} />
            ) : (
              <div className="pub-grid">
                {d.media.map((m, k) => (
                  <MediaTile key={m.index} m={m} delay={k * 0.05} />
                ))}
              </div>
            )}
          </section>
        ))}

        <footer className="pub-foot">
          <div className="eyebrow">MADE WITH ATELIER</div>
        </footer>
      </div>
    </div>
  )
}
