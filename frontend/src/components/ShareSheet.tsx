import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { mintShare, SCOPE_LABELS, SCOPES, type ShareScope } from '../lib/api'
import { toast, useShare } from '../lib/store'

/** Share sheet per the mock's #sheet-share: a view-only public link + Copy.
 *  Minting is idempotent per target+scope (backend returns the live link),
 *  so re-opening the sheet always shows the same URL. */
export default function ShareSheet() {
  const { open, target, closeShare } = useShare()
  const [scope, setScope] = useState<ShareScope>('finals')

  // reset to the PRD A8 default ("Finals only") on every open
  useEffect(() => {
    if (open) setScope('finals')
  }, [open])

  const link = useQuery({
    queryKey: ['share', target?.kind, target?.id, scope],
    queryFn: () =>
      mintShare(
        target!.kind === 'project' ? { project_id: target!.id } : { design_id: target!.id },
        scope,
      ),
    enabled: open && !!target,
    staleTime: Infinity,
  })

  const shareUrl = link.data ? `${location.origin}${link.data.url}` : null

  const copy = async () => {
    if (!shareUrl) return
    try {
      await navigator.clipboard.writeText(shareUrl)
      toast('Link copied')
    } catch {
      toast('Copy failed — long-press the link instead')
    }
  }

  // portaled to <body> so a transformed ancestor can't trap the fixed sheet
  return createPortal(
    <div className={`sheet-wrap${open ? ' open' : ''}`} id="sheet-share">
      <div className="backdrop" onClick={closeShare} />
      <div className="sheet">
        <div className="grabber" />
        <div className="syne" style={{ fontSize: 18, fontWeight: 700, marginBottom: 3 }}>
          Share gallery
        </div>
        <div style={{ fontSize: 12, color: 'var(--fog)' }}>
          A view-only link{target ? ` for ${target.name}` : ''}. No login for whoever you send it to.
        </div>

        <div className="seg" style={{ marginTop: 12 }}>
          {SCOPES.map(s => (
            <button key={s} className={scope === s ? 'on' : ''} onClick={() => setScope(s)}>
              {SCOPE_LABELS[s]}
            </button>
          ))}
        </div>

        <div className="share-link">
          <div
            className="mono"
            id="share-url"
            style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {link.isError ? 'Could not mint a link — try again' : shareUrl ?? 'Minting…'}
          </div>
          <button
            className="press"
            id="share-copy"
            disabled={!shareUrl}
            style={{
              padding: '8px 13px', borderRadius: 11, background: 'var(--ink)', color: '#FFF',
              fontSize: 11.5, fontWeight: 600, flexShrink: 0, opacity: shareUrl ? 1 : 0.5,
            }}
            onClick={copy}
          >
            Copy
          </button>
        </div>
        <div className="mono" style={{ fontSize: 9, color: 'var(--faint)', marginTop: 10, lineHeight: 1.7 }}>
          {scope === 'finals'
            ? 'FINALS · the finished + editorial photos only'
            : 'FULL · every phase, notes included'}
        </div>
      </div>
    </div>,
    document.body,
  )
}
