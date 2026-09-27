import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { usePanelState, type PanelAction } from '../lib/panel'
import { useDialog } from '../lib/dialog'


// Match the original navigation's 19px, rounded 1.5px outline icons.
function ActionIcon({ label }: { label: string }) {
  const d = /back|all projects|archive/i.test(label) ? 'M12 4l-6 6 6 6M6 10h11'
    : /share/i.test(label) ? 'M10 13V3M6 7l4-4 4 4M4 11v6h12v-6'
    : /new|add|capture/i.test(label) ? 'M10 4v12M4 10h12'
    : /inbox|sort/i.test(label) ? 'M5 4h10l3 9v4H2v-4l3-9zM2 13h5l1 2h4l1-2h5'
    : /filter/i.test(label) ? 'M3 5h14M6 10h8M8 15h4'
    : /studio|studies/i.test(label) ? 'M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6z'
    : /more/i.test(label) ? 'M4 9v2M10 9v2M16 9v2'
    : 'M3 5h14v11H3zM3 12l4-3 4 3 4-4 2 2'
  return <svg aria-hidden="true" width="19" height="19" viewBox="0 0 20 20" fill="none">
    <path d={d} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
}

export default function ContextPanel() {
  const location = useLocation()
  const navigate = useNavigate()
  const { route, actions } = usePanelState()
  const [menu, setMenu] = useState<PanelAction | null>(null)
  const [editing, setEditing] = useState(false)
  useEffect(() => { setMenu(null) }, [location.key])
  useEffect(() => {
    const focus = () => setEditing(!!document.activeElement?.matches('input,textarea,[contenteditable="true"]'))
    document.addEventListener('focusin', focus)
    document.addEventListener('focusout', focus)
    return () => { document.removeEventListener('focusin', focus); document.removeEventListener('focusout', focus) }
  }, [])
  useDialog('panel-menu', !!menu, () => setMenu(null))
  const section = (location.pathname.startsWith('/gallery') || (/^\/d\/[^/]+$/.test(location.pathname) && location.state?.origin?.to?.startsWith('/gallery'))) ? 'Gallery'
    : location.pathname === '/studies' || /\/stud(?:io|y\/)/.test(location.pathname) ? 'Studies' : 'Archive'
  const execute = (action: PanelAction) => {
    if (action.children) setMenu(action)
    else { setMenu(null); action.run?.() }
  }
  return <>
    <div className={`context-panel${editing ? ' editing' : ''}`} aria-label="Contextual panel">
      <div className="panel-actions" aria-label="Screen actions">
        {route === location.key && actions.map(a => <button key={a.label} className={`panel-key press${a.primary ? ' primary' : ''}`} title={a.label} disabled={a.disabled} onClick={() => execute(a)}><ActionIcon label={a.label} /><span className="tab-label">{a.label}</span></button>)}
      </div>
      <nav className="tabbar" aria-label="Main navigation">
        {([['Archive', '/'], ['Gallery', '/gallery'], ['Studies', '/studies']] as const).map(([label, to]) =>
          <button key={label} className={`tab${section === label ? ' on' : ''}`} aria-current={section === label ? 'page' : undefined} onClick={() => navigate(to)}>
            <svg aria-hidden="true" width="19" height="19" viewBox="0 0 20 20" fill="none"><path d={label === 'Archive' ? 'M3 9.5 10 3l7 6.5M5 8.5V17h10V8.5' : label === 'Gallery' ? 'M3 5.5h14v11H3zM3 12l4-3 4 3.5L15 9l2 1.5' : 'M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6z'} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg><span className="tab-label">{label}</span>
          </button>)}
      </nav>
    </div>
    {menu && createPortal(<div className="sheet-wrap open" id="panel-menu" role="dialog" aria-modal="true" aria-label={menu.label}>
      <div className="backdrop" onClick={() => setMenu(null)} />
      <div className="sheet"><div className="grabber" /><div className="dialog-heading"><h2 className="syne">{menu.label}</h2><button className="chip" onClick={() => setMenu(null)}>Close</button></div>
        <div className="panel-menu-items">{menu.children?.map(a => <button key={a.label} className={`panel-key press${a.primary ? ' primary' : ''}`} title={a.label} disabled={a.disabled} onClick={() => execute(a)}>{a.label}</button>)}</div>
      </div>
    </div>, document.body)}
  </>
}
