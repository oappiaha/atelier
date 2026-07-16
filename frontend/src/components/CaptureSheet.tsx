import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, uploadMedia, type Entry, type Media, type Phase, PHASE_LABELS } from '../lib/api'
import { toast, useCapture } from '../lib/store'

type CapMode = 'menu' | 'photo' | 'mood' | 'note' | 'voice' | 'inspo'

/** Phases a plain photo can land on (the rest have their own modality). */
const PHOTO_PHASES: Phase[] = ['moodboard', 'sketch', 'manufacturing', 'editorial', 'final']

export default function CaptureSheet() {
  const { open, designCtx, dest, closeCapture, toggleDest } = useCapture()
  const qc = useQueryClient()

  const [mode, setMode] = useState<CapMode>('menu')
  const [busy, setBusy] = useState(false)
  const [photoPhase, setPhotoPhase] = useState<Phase>('manufacturing')
  const [note, setNote] = useState('')
  const [inspoUrl, setInspoUrl] = useState('')
  const [inspoWhy, setInspoWhy] = useState('')
  const [inspoFile, setInspoFile] = useState<File | null>(null)
  const [moodFiles, setMoodFiles] = useState<{ file: File; url: string; sel: boolean }[]>([])
  const [recState, setRecState] = useState<'idle' | 'recording' | 'done'>('idle')
  const recRef = useRef<{ rec: MediaRecorder; chunks: Blob[]; started: number } | null>(null)
  const [voiceBlob, setVoiceBlob] = useState<{ blob: Blob; duration_ms: number } | null>(null)

  const cameraInput = useRef<HTMLInputElement>(null)
  const libraryInput = useRef<HTMLInputElement>(null)
  const moodInput = useRef<HTMLInputElement>(null)
  const inspoInput = useRef<HTMLInputElement>(null)

  const destName = dest === 'design' && designCtx ? designCtx.name : 'Inbox'

  // reset on every open
  useEffect(() => {
    if (open) {
      setMode('menu')
      setBusy(false)
      setNote('')
      setInspoUrl('')
      setInspoWhy('')
      setInspoFile(null)
      setRecState('idle')
      setVoiceBlob(null)
      setMoodFiles(prev => {
        prev.forEach(m => URL.revokeObjectURL(m.url))
        return []
      })
    }
  }, [open])

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['inbox'] })
    qc.invalidateQueries({ queryKey: ['projects'] })
    if (designCtx) {
      qc.invalidateQueries({ queryKey: ['design', designCtx.id] })
      qc.invalidateQueries({ queryKey: ['timeline', designCtx.id] })
      qc.invalidateQueries({ queryKey: ['media', designCtx.id] })
      qc.invalidateQueries({ queryKey: ['designs'] })
    }
  }

  const done = (label: string) => {
    invalidate()
    toast(`${label} → ${destName}`)
    closeCapture()
  }

  const fail = (e: unknown) => {
    toast(e instanceof Error && e.message ? `Failed: ${e.message.slice(0, 80)}` : 'Capture failed')
    setBusy(false)
  }

  /** Upload images; when the destination is a design, wrap them in one entry. */
  const saveImages = async (
    files: File[],
    phase: Phase,
    label: string,
    extra?: { caption?: string; source_url?: string; source_app?: string },
  ) => {
    if (!files.length) return
    setBusy(true)
    try {
      const medias: Media[] = []
      for (const f of files) medias.push(await uploadMedia(f, { kind: 'image', ...extra }))
      if (dest === 'design' && designCtx) {
        await api<Entry>('/entries', {
          method: 'POST',
          body: JSON.stringify({
            design_id: designCtx.id,
            phase,
            body: extra?.caption || null,
            media_ids: medias.map(m => m.id),
          }),
        })
      }
      done(label)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const saveNote = async () => {
    if (!note.trim() || !designCtx || dest !== 'design') return
    setBusy(true)
    try {
      await api<Entry>('/entries', {
        method: 'POST',
        body: JSON.stringify({ design_id: designCtx.id, phase: 'note', body: note.trim() }),
      })
      done('Note')
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const startStopRec = async () => {
    if (recState === 'recording') {
      recRef.current?.rec.stop()
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      const entry = { rec, chunks: [] as Blob[], started: Date.now() }
      rec.ondataavailable = e => entry.chunks.push(e.data)
      rec.onstop = () => {
        stream.getTracks().forEach(t => t.stop())
        setVoiceBlob({
          blob: new Blob(entry.chunks, { type: 'audio/webm' }),
          duration_ms: Date.now() - entry.started,
        })
        setRecState('done')
      }
      recRef.current = entry
      rec.start()
      setRecState('recording')
    } catch {
      toast('Microphone unavailable')
    }
  }

  const saveVoice = async () => {
    if (!voiceBlob) return
    setBusy(true)
    try {
      const media = await uploadMedia(voiceBlob.blob, {
        kind: 'audio',
        duration_ms: voiceBlob.duration_ms,
      })
      if (dest === 'design' && designCtx) {
        await api<Entry>('/entries', {
          method: 'POST',
          body: JSON.stringify({ design_id: designCtx.id, phase: 'voice', media_ids: [media.id] }),
        })
      }
      done('Voice note')
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const onMoodPick = (files: FileList | null) => {
    if (!files?.length) return
    setMoodFiles(prev => [
      ...prev,
      ...[...files].map(file => ({ file, url: URL.createObjectURL(file), sel: true })),
    ])
  }

  const moodSel = moodFiles.filter(m => m.sel)

  return (
    <div className={`sheet-wrap${open ? ' open' : ''}`} id="sheet-capture">
      <div className="backdrop" onClick={closeCapture} />
      <div className="sheet">
        <div className="grabber" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 3 }}>
          <div className="syne" style={{ fontSize: 18, fontWeight: 700 }}>
            {mode === 'menu' ? 'Capture'
              : mode === 'photo' ? 'Photo'
              : mode === 'mood' ? 'Mood board'
              : mode === 'note' ? 'Note'
              : mode === 'voice' ? 'Voice note'
              : 'Inspiration'}
          </div>
          {mode !== 'menu' && (
            <button
              className="press mono"
              style={{ fontSize: 9.5, letterSpacing: '.1em', color: 'var(--fog)' }}
              onClick={() => setMode('menu')}
            >
              ← BACK
            </button>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: 'var(--fog)' }}>To:</span>
          <button className="dest-pill press" onClick={toggleDest}>
            <span id="dest-name">{destName}</span>
            <svg width="9" height="9" viewBox="0 0 10 10" fill="none">
              <path d="M2.5 4l2.5 2.5L7.5 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>

        {mode === 'menu' && (
          <>
            <div className="cap-row">
              <button className="cap-opt press" onClick={() => setMode('photo')}>
                <div style={{ fontSize: 18 }}>📷</div><div className="t">Photo</div>
              </button>
              <button className="cap-opt press" onClick={() => setMode('mood')}>
                <div style={{ fontSize: 18 }}>🖼</div><div className="t">Mood</div>
              </button>
              <button className="cap-opt press" onClick={() => setMode('note')}>
                <div style={{ fontSize: 18 }}>✏️</div><div className="t">Note</div>
              </button>
              <button className="cap-opt press" onClick={() => setMode('voice')}>
                <div style={{ fontSize: 18 }}>🎙</div><div className="t">Voice</div>
              </button>
              <button className="cap-opt press" onClick={() => setMode('inspo')}>
                <div style={{ fontSize: 18 }}>✨</div><div className="t">Inspo</div>
              </button>
            </div>
            <div className="mono" style={{ fontSize: 9, color: 'var(--faint)', lineHeight: 1.8, marginTop: 14 }}>
              TIP · Share to Atelier from Instagram or Safari<br />and it lands in Inspo automatically.
            </div>
          </>
        )}

        {mode === 'photo' && (
          <>
            {dest === 'design' && (
              <div className="chips" style={{ marginTop: 4 }}>
                {PHOTO_PHASES.map(ph => (
                  <button
                    key={ph}
                    className={`chip${photoPhase === ph ? ' on' : ''}`}
                    onClick={() => setPhotoPhase(ph)}
                  >
                    {PHASE_LABELS[ph]}
                  </button>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: 9, marginTop: 10 }}>
              <button
                className="cap-opt press"
                style={{ flex: 1, padding: '20px 8px' }}
                disabled={busy}
                onClick={() => cameraInput.current?.click()}
              >
                <div style={{ fontSize: 19 }}>📸</div><div className="t">Camera</div>
              </button>
              <button
                className="cap-opt press"
                style={{ flex: 1, padding: '20px 8px' }}
                disabled={busy}
                onClick={() => libraryInput.current?.click()}
              >
                <div style={{ fontSize: 19 }}>🗂</div><div className="t">Library</div>
              </button>
            </div>
            {busy && (
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--faint)', marginTop: 12, textAlign: 'center' }}>
                Uploading…
              </div>
            )}
            <input
              ref={cameraInput} id="cap-file-camera" type="file" accept="image/*" capture="environment"
              style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) saveImages([f], photoPhase, 'Photo') }}
            />
            <input
              ref={libraryInput} id="cap-file-library" type="file" accept="image/*"
              style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) saveImages([f], photoPhase, 'Photo') }}
            />
          </>
        )}

        {mode === 'mood' && (
          <>
            <div style={{ fontSize: 12, color: 'var(--fog)' }}>
              Multi-select. Boards keep growing — add anytime.
            </div>
            <div className="mb-pick">
              {moodFiles.map((m, i) => (
                <div
                  key={m.url}
                  className={`mb-thumb${m.sel ? ' sel' : ''}`}
                  style={{ backgroundImage: `url(${m.url})` }}
                  onClick={() =>
                    setMoodFiles(prev => prev.map((x, k) => (k === i ? { ...x, sel: !x.sel } : x)))
                  }
                >
                  <div className="mb-check">✓</div>
                </div>
              ))}
              <div
                className="mb-thumb"
                style={{ border: '2px dashed var(--stroke-strong)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20, color: 'var(--faint)' }}
                onClick={() => moodInput.current?.click()}
              >
                +
              </div>
            </div>
            <button
              className="primary-btn press"
              id="mb-add"
              disabled={busy || !moodSel.length}
              style={!moodSel.length ? { opacity: 0.5 } : undefined}
              onClick={() => saveImages(moodSel.map(m => m.file), 'moodboard', `Mood board · ${moodSel.length} images`)}
            >
              {busy ? 'Uploading…' : `Add ${moodSel.length} image${moodSel.length === 1 ? '' : 's'} → ${destName}`}
            </button>
            <input
              ref={moodInput} id="cap-file-mood" type="file" accept="image/*" multiple
              style={{ display: 'none' }}
              onChange={e => { onMoodPick(e.target.files); e.target.value = '' }}
            />
          </>
        )}

        {mode === 'note' && (
          <>
            <textarea
              className="field"
              placeholder="Strap drop feels 2cm too long on the sample…"
              value={note}
              onChange={e => setNote(e.target.value)}
            />
            {dest === 'design' ? (
              <button className="primary-btn press" disabled={busy || !note.trim()} onClick={saveNote}>
                {busy ? 'Saving…' : `Save note → ${destName}`}
              </button>
            ) : (
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--warn)', marginTop: 12, lineHeight: 1.7 }}>
                Notes live on a design's timeline — switch the destination to a design.
              </div>
            )}
          </>
        )}

        {mode === 'voice' && (
          <>
            <button className={`rec-circle press${recState === 'recording' ? ' rec' : ''}`} id="rec" onClick={startStopRec}>
              <svg width="20" height="20" viewBox="0 0 22 22" fill="currentColor">
                <rect x="8" y="3" width="6" height="11" rx="3" />
                <path d="M5 11a6 6 0 0012 0M11 17v3" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" />
              </svg>
            </button>
            <div className="mono" style={{ textAlign: 'center', fontSize: 10.5, color: 'var(--fog)' }} id="rec-t">
              {recState === 'recording' ? 'recording… tap to stop'
                : recState === 'done' && voiceBlob ? `${Math.round(voiceBlob.duration_ms / 1000)}s recorded`
                : 'tap to record'}
            </div>
            <button
              className="primary-btn press"
              disabled={busy || !voiceBlob}
              style={!voiceBlob ? { opacity: 0.5 } : undefined}
              onClick={saveVoice}
            >
              {busy ? 'Uploading…' : `Save → ${destName}`}
            </button>
          </>
        )}

        {mode === 'inspo' && (
          <>
            <div style={{ fontSize: 12, color: 'var(--fog)' }}>One image, source kept.</div>
            <button
              className="cap-opt press"
              style={{ width: '100%', padding: '16px 8px', marginTop: 10 }}
              onClick={() => inspoInput.current?.click()}
            >
              <div style={{ fontSize: 18 }}>{inspoFile ? '✓' : '🖼'}</div>
              <div className="t">{inspoFile ? inspoFile.name : 'Pick an image'}</div>
            </button>
            <input
              className="field"
              placeholder="Source link (optional)"
              value={inspoUrl}
              onChange={e => setInspoUrl(e.target.value)}
            />
            <input
              className="field"
              placeholder="Why it matters (optional)"
              value={inspoWhy}
              onChange={e => setInspoWhy(e.target.value)}
            />
            <button
              className="primary-btn press"
              disabled={busy || !inspoFile}
              style={!inspoFile ? { opacity: 0.5 } : undefined}
              onClick={() =>
                inspoFile &&
                saveImages([inspoFile], 'inspiration', 'Inspiration', {
                  caption: inspoWhy.trim() || undefined,
                  source_url: inspoUrl.trim() || undefined,
                  source_app: inspoUrl.trim() ? 'web' : undefined,
                })
              }
            >
              {busy ? 'Uploading…' : `Save → ${destName}`}
            </button>
            <input
              ref={inspoInput} id="cap-file-inspo" type="file" accept="image/*"
              style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) setInspoFile(f) }}
            />
          </>
        )}
      </div>
    </div>
  )
}
