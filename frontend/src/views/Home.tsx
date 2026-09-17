import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, fetchInbox, type Project } from '../lib/api'
import { usePanel } from '../lib/panel'
import { useCapture, useNewDesign } from '../lib/store'

export default function Home() {
  const navigate = useNavigate()
  const openNewDesign = useNewDesign(s => s.openNewDesign)
  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api<Project[]>('/projects'),
  })
  // PRD A5 — "Home surfaces the unsorted count"
  const { data: inbox } = useQuery({ queryKey: ['inbox'], queryFn: fetchInbox })

  usePanel([
    { primary: true, label: 'New design', disabled: !projects?.length, run: () => openNewDesign(projects?.length === 1 ? projects[0] : null) },
    { label: `Inbox${inbox ? ` · ${inbox.length}` : ''}`, run: () => navigate('/inbox') },
    { label: 'Capture', run: useCapture.getState().openCapture },
  ])

  return (
    <div className="view">
      <div className="content">
        <header className="hdr rise">
          <div>
            <div className="eyebrow">ARCHIVE</div>
            <div className="syne" style={{ fontSize: 26, fontWeight: 800, marginTop: 4 }}>Projects</div>
          </div>
        </header>

        {inbox && (
          <button className="inbox press rise" id="inbox-row" onClick={() => navigate('/inbox')}>
            <div className="inbox-icon"><span>✨</span></div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 500 }}>Inbox</div>
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--faint)', marginTop: 2 }}>
                {inbox.length
                  ? `${inbox.length} unsorted · tap to triage`
                  : 'all sorted · share from IG & Safari lands here'}
              </div>
            </div>
            {inbox.length > 0 && (
              <>
                <div className="inbox-thumbs">
                  {inbox.slice(0, 3).map(m => (
                    <img key={m.id} src={m.thumb_url ?? m.url} alt="" />
                  ))}
                </div>
                <div className="pulse-dot" />
              </>
            )}
          </button>
        )}

        {isLoading ? (
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--faint)', padding: '18px 4px' }}>
            Loading…
          </div>
        ) : !projects?.length ? (
          <div className="panel rise" style={{ padding: 24 }}>
            <div className="syne" style={{ fontSize: 16, fontWeight: 700 }}>Nothing here yet</div>
            <p style={{ fontSize: 12.5, color: 'var(--fog)', marginTop: 6, lineHeight: 1.6 }}>
              No projects are available for a new design. Capture to Inbox while your archive is being set up.
            </p>
          </div>
        ) : (
          <div className="proj-row">
            {projects.map(p => (
              <button key={p.id} className="panel press" style={{ padding: 20, textAlign: 'left' }} onClick={() => navigate(`/p/${p.id}`)}>
                <div className="eyebrow">{p.kicker ?? 'PROJECT'}</div>
                <div className="syne" style={{ fontSize: 18, fontWeight: 700, marginTop: 4 }}>{p.name}</div>
                <div className="mono" style={{ fontSize: 9.5, color: 'var(--faint)', marginTop: 8 }}>
                  {p.design_count} designs
                </div>
              </button>
            ))}
            {/* new design straight from Home: one project → preselect it;
               several → the sheet shows a project picker */}
            <button
              className="panel press"
              id="home-new-design"
              style={{
                padding: 20, background: 'transparent', boxShadow: 'none',
                border: '1.5px dashed var(--stroke-strong)', color: 'var(--faint)',
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', gap: 6, minHeight: 96, cursor: 'pointer',
              }}
              onClick={() =>
                openNewDesign(
                  projects.length === 1
                    ? { id: projects[0].id, name: projects[0].name }
                    : null,
                )
              }
            >
              <span style={{ fontSize: 22, fontWeight: 300, lineHeight: 1 }}>+</span>
              <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.12em' }}>NEW DESIGN</span>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
