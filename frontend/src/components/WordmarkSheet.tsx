import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api, type Project } from '../lib/api'
import { useDialog } from '../lib/dialog'

async function readWordmark(file: File): Promise<string> {
  if (!['image/png', 'image/webp', 'image/svg+xml', 'image/jpeg'].includes(file.type)) throw new Error('Choose a PNG, WebP, SVG or JPEG image.')
  if (file.size > 5 * 1024 * 1024) throw new Error('Choose an image under 5 MB.')
  const url = URL.createObjectURL(file)
  try {
    const img = new Image()
    img.src = url
    await img.decode()
    const scale = Math.min(1, 1200 / img.naturalWidth, 320 / img.naturalHeight)
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Image conversion is unavailable.')
    context.drawImage(img, 0, 0, canvas.width, canvas.height)
    const data = canvas.toDataURL('image/png')
    if (data.length > 350_000) throw new Error('This image is too detailed. Choose a simpler or smaller wordmark.')
    return data
  } finally { URL.revokeObjectURL(url) }
}

export default function WordmarkSheet({ project, onClose }: { project: Project; onClose: () => void }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState(project.wordmark ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const close = () => { if (!busy) onClose() }
  useDialog('wordmark-sheet', true, close)
  const save = async () => {
    setBusy(true); setError('')
    try {
      await api(`/projects/${project.id}/wordmark`, { method: 'PATCH', body: JSON.stringify({ wordmark: draft }) })
      qc.setQueryData<Project[]>(['projects'], old => old?.map(p => p.id === project.id ? { ...p, wordmark: draft } : p))
      onClose()
    } catch { setError('Could not save the wordmark. Please try again.') }
    finally { setBusy(false) }
  }
  return createPortal(<div className="sheet-wrap open" id="wordmark-sheet" role="dialog" aria-modal="true" aria-label="Wordmark">
    <div className="backdrop" onClick={close} />
    <div className="sheet">
      <div className="dialog-heading"><h2 className="syne">Wordmark</h2><button className="chip" disabled={busy} onClick={close}>Close</button></div>
      <p className="wordmark-help">Use a logo in place of the title. Your collection name stays the same.</p>
      <div className="wordmark-preview">{draft ? <img src={draft} alt={project.name} /> : <span className="syne">{project.name}</span>}</div>
      <label className="wordmark-upload">Choose image
        <input type="file" accept="image/png,image/webp,image/svg+xml,image/jpeg" disabled={busy} onChange={async e => {
          const file = e.target.files?.[0]; e.target.value = ''
          if (!file) return
          setBusy(true); setError('')
          try { setDraft(await readWordmark(file)) }
          catch (err) { setError(err instanceof Error ? err.message : 'Could not read the image.') }
          finally { setBusy(false) }
        }} />
      </label>
      <p className="wordmark-help">PNG, WebP, SVG or JPEG · up to 5 MB. Transparent backgrounds work best.</p>
      {error && <p role="alert">{error}</p>}
      <div className="wordmark-actions">
        <button className="chip" disabled={busy || !draft} onClick={() => setDraft(null)}>Use text</button>
        <button className="primary-btn" disabled={busy || draft === (project.wordmark ?? null)} onClick={save}>{busy ? 'Saving' : 'Save'}</button>
      </div>
    </div>
  </div>, document.body)
}
