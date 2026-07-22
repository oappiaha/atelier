import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useEffect } from 'react'
import { getToken } from './lib/api'
import { useCapture } from './lib/store'
import CaptureSheet from './components/CaptureSheet'
import NewDesignSheet from './components/NewDesignSheet'
import ShareSheet from './components/ShareSheet'
import Toast from './components/Toast'

function TabIcon({ d }: { d: string }) {
  return (
    <svg width="19" height="19" viewBox="0 0 20 20" fill="none">
      <path d={d} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

const HOME_ICON = 'M3 9.5 10 3l7 6.5M5 8.5V17h10V8.5'
const GALLERY_ICON = 'M3 5.5h14v11H3zM3 12l4-3 4 3.5L15 9l2 1.5'

export default function App() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const openCapture = useCapture(s => s.openCapture)
  const authed = !!getToken()

  useEffect(() => {
    if (!authed) navigate('/login', { replace: true })
  }, [authed, navigate])

  // don't mount views (and fire their queries) until the token exists
  if (!authed) return null

  const section = pathname.startsWith('/gallery')
    ? 'gallery'
    : /\/study\//.test(pathname)
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
          <NavLink to="/" className={({ isActive }) => `snav${isActive && section === 'home' ? ' on' : ''}`} end>
            Home
          </NavLink>
          <NavLink to="/gallery" className={({ isActive }) => `snav${isActive ? ' on' : ''}`}>
            Gallery
          </NavLink>
        </nav>
        <div style={{ marginTop: 'auto' }}>
          <button className="gen-btn press" style={{ padding: '12px 15px' }} onClick={openCapture}>
            <span style={{ fontSize: 13, fontWeight: 500 }}>Capture</span>
            <span style={{ fontSize: 15 }}>+</span>
          </button>
        </div>
      </aside>

      <main className="main">
        <Outlet />
      </main>

      <nav className="tabbar">
        <button className={`tab${section === 'home' ? ' on' : ''}`} onClick={() => navigate('/')}>
          <TabIcon d={HOME_ICON} />
          <span className="tab-label">Archive</span>
        </button>
        <button className="fab" aria-label="Capture" onClick={openCapture}>
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path d="M9 3v12M3 9h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
        <button className={`tab${section === 'gallery' ? ' on' : ''}`} onClick={() => navigate('/gallery')}>
          <TabIcon d={GALLERY_ICON} />
          <span className="tab-label">Gallery</span>
        </button>
      </nav>

      <CaptureSheet />
      <NewDesignSheet />
      <ShareSheet />
      <Toast />
    </div>
  )
}
