import { NavLink, Outlet, ScrollRestoration, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useEffect, useLayoutEffect } from 'react'
import { api, getToken, type Design } from './lib/api'
import { useQuery } from '@tanstack/react-query'
import ContextPanel from './components/ContextPanel'
import { useCapture } from './lib/store'
import CaptureSheet from './components/CaptureSheet'
import NewDesignSheet from './components/NewDesignSheet'
import ShareSheet from './components/ShareSheet'
import Toast from './components/Toast'

export default function App() {
  const navigate = useNavigate()
  const { pathname, state } = useLocation()
  const { designId } = useParams()
  const design = useQuery({ queryKey: ['design', designId], queryFn: () => api<Design>(`/designs/${designId}`), enabled: !!designId && !!getToken() })
  useLayoutEffect(() => {
    useCapture.getState().setDesignCtx(designId && design.data ? { id: design.data.id, name: design.data.name } : null)
  }, [designId, design.data])
  const openCapture = useCapture(s => s.openCapture)
  const authed = !!getToken()

  useEffect(() => {
    if (!authed) navigate('/login', { replace: true })
  }, [authed, navigate])

  // don't mount views (and fire their queries) until the token exists
  if (!authed) return null

  const section = (pathname.startsWith('/gallery') || (!!designId && !/\/stud(?:io|y\/)/.test(pathname) && state?.origin?.to?.startsWith('/gallery')))
    ? 'gallery'
    : pathname === '/studies' || /\/stud(?:io|y\/)/.test(pathname)
      ? 'studio'
      : 'home'

  return (
    <div className="shell">
      <aside className="sidebar">
        <div>
          <div className="syne" style={{ fontSize: 21, fontWeight: 800 }}>Atelier</div>
          <div className="mono" style={{ fontSize: 8.5, letterSpacing: '.2em', color: 'var(--faint)', marginTop: 3 }}>
            DESIGN ARCHIVE
          </div>
        </div>
        <nav className="side-nav">
          <NavLink to="/" className={`snav${section === 'home' ? ' on' : ''}`} end>
            Archive
          </NavLink>
          <NavLink to="/gallery" className={`snav${section === 'gallery' ? ' on' : ''}`}>
            Gallery
          </NavLink>
          <NavLink to="/studies" className={`snav${section === 'studio' ? ' on' : ''}`}>
            Studies
          </NavLink>
        </nav>
        <div style={{ marginTop: 'auto' }}>
          <button className="gen-btn press" style={{ padding: '12px 15px' }} disabled={!!designId && !design.data} onClick={openCapture}>
            <span style={{ fontSize: 13, fontWeight: 500 }}>Capture</span>
            <span style={{ fontSize: 15 }}>+</span>
          </button>
        </div>
      </aside>

      <main className="main">
        <Outlet />
        {/* back/forward restores the scroll position (the 102-design grid
            was snapping to the top on every return); new pushes start at top */}
        <ScrollRestoration />
      </main>

      <ContextPanel />

      <CaptureSheet />
      <NewDesignSheet />
      <ShareSheet />
      <Toast />
    </div>
  )
}
