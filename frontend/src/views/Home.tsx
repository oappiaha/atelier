import { useQuery } from '@tanstack/react-query'
import { api, type Project } from '../lib/api'

export default function Home() {
  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api<Project[]>('/projects'),
  })

  return (
    <div className="view">
      <div className="content">
        <header className="hdr rise">
          <div>
            <div className="eyebrow">ARCHIVE</div>
            <div className="syne" style={{ fontSize: 26, fontWeight: 800, marginTop: 4 }}>Projects</div>
          </div>
        </header>

        {isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading…
          </div>
        ) : !projects?.length ? (
          <div className="panel rise" style={{ padding: 24 }}>
            <div className="syne" style={{ fontSize: 16, fontWeight: 700 }}>Nothing here yet</div>
            <p style={{ fontSize: 12.5, color: 'var(--fog)', marginTop: 6, lineHeight: 1.6 }}>
              Create your first project — a brand or body of work. Designs live inside it.
            </p>
          </div>
        ) : (
          <div className="proj-row">
            {projects.map(p => (
              <div key={p.id} className="panel press" style={{ padding: 20 }}>
                <div className="eyebrow">{p.kicker ?? 'PROJECT'}</div>
                <div className="syne" style={{ fontSize: 18, fontWeight: 700, marginTop: 4 }}>{p.name}</div>
                <div className="mono" style={{ fontSize: 9.5, color: 'var(--faint)', marginTop: 8 }}>
                  {p.design_count} designs
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
