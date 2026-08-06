import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api, STATUSES, STATUS_LABELS,
  type Design, type DesignStatus, type Project,
} from '../lib/api'
import { toast, useNewDesign } from '../lib/store'

/** New-design sheet: name (required) + optional materials, category and status.
 *  POST /designs assigns index_no automatically and defaults to 'developing';
 *  a non-default status is applied with a follow-up PATCH.
 *  Opened with a preselected project (Project view / Home with one project),
 *  or with none (Home with several) — then a project picker row appears. */
export default function NewDesignSheet() {
  const { open, project: preselected, closeNewDesign } = useNewDesign()
  const qc = useQueryClient()

  const [name, setName] = useState('')
  const [materials, setMaterials] = useState('')
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState<DesignStatus>('developing')
  const [pickedId, setPickedId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const nameRef = useRef<HTMLInputElement>(null)

  // picker data — only fetched when the sheet actually needs to offer a choice
  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api<Project[]>('/projects'),
    enabled: open && !preselected,
  })
  // preselected wins; else the explicit pick; else a lone project picks itself
  const picked =
    projects?.find(p => p.id === pickedId) ??
    (projects?.length === 1 ? projects[0] : undefined)
  const project = preselected ?? picked ?? null

  // reset on every open; focus once the slide-up transition has started
  useEffect(() => {
    if (open) {
      setName('')
      setMaterials('')
      setCategory('')
      setStatus('developing')
      setPickedId(null)
      setBusy(false)
      const t = setTimeout(() => nameRef.current?.focus(), 80)
      return () => clearTimeout(t)
    }
  }, [open])

  const create = async () => {
    if (!project || !name.trim() || busy) return
    setBusy(true)
    try {
      let d = await api<Design>('/designs', {
        method: 'POST',
        body: JSON.stringify({
          project_id: project.id,
          name: name.trim(),
          materials: materials.trim() || null,
          ...(category.trim() ? { category: category.trim() } : {}),
        }),
      })
      if (status !== 'developing') {
        d = await api<Design>(`/designs/${d.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ status }),
        })
      }
      qc.invalidateQueries({ queryKey: ['designs', project.id] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      toast(`${d.name} · No. ${String(d.index_no).padStart(3, '0')}`)
      closeNewDesign()
    } catch (e) {
      toast(e instanceof Error && e.message ? `Failed: ${e.message.slice(0, 80)}` : 'Create failed')
      setBusy(false)
    }
  }

  // portaled to <body> so a transformed ancestor can't trap the fixed sheet
  return createPortal(
    <div className={`sheet-wrap${open ? ' open' : ''}`} id="sheet-newdesign">
      <div className="backdrop" onClick={closeNewDesign} />
      <div className="sheet">
        <div className="grabber" />
        <div className="syne" style={{ fontSize: 18, fontWeight: 700, marginBottom: 3 }}>
          New design
        </div>
        <div style={{ fontSize: 12, color: 'var(--fog)', marginBottom: 12 }}>
          {project
            ? `Joins the ${project.name} index — `
            : preselected === null && (projects?.length ?? 0) > 1
              ? 'Pick a project — '
              : ''}
          the index number is assigned automatically.
        </div>

        {!preselected && (projects?.length ?? 0) > 1 && (
          <div className="chips" style={{ marginBottom: 10 }} id="nd-projects">
            {projects!.map(p => (
              <button
                key={p.id}
                type="button"
                className={`chip${pickedId === p.id ? ' on' : ''}`}
                onClick={() => setPickedId(p.id)}
              >
                {p.name}
              </button>
            ))}
          </div>
        )}

        <form
          onSubmit={e => {
            e.preventDefault()
            create()
          }}
        >
          <input
            ref={nameRef}
            className="field"
            id="nd-name"
            placeholder="Name — e.g. Saddle Tote"
            value={name}
            onChange={e => setName(e.target.value)}
          />
          <textarea
            className="field"
            id="nd-materials"
            placeholder="Materials (optional) — e.g. vegetable-tanned leather, brass"
            value={materials}
            onChange={e => setMaterials(e.target.value)}
            style={{ marginTop: 9, minHeight: 64 }}
          />
          <input
            className="field"
            id="nd-category"
            placeholder="Category (optional) — e.g. Bags"
            value={category}
            onChange={e => setCategory(e.target.value)}
            style={{ marginTop: 9 }}
          />

          <div className="chips" style={{ marginTop: 10 }}>
            {STATUSES.map(s => (
              <button
                key={s}
                type="button"
                className={`chip${status === s ? ' on' : ''}`}
                onClick={() => setStatus(s)}
              >
                {STATUS_LABELS[s]}
              </button>
            ))}
          </div>

          <button
            type="submit"
            className="primary-btn press"
            id="nd-create"
            disabled={busy || !name.trim() || !project}
            style={!name.trim() || !project ? { opacity: 0.5 } : undefined}
          >
            {busy ? 'Creating…' : project ? 'Create design' : 'Pick a project first'}
          </button>
        </form>
      </div>
    </div>,
    document.body,
  )
}
