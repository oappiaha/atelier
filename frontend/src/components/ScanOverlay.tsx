// FINDING-REGIONS SCAN OVERLAY — the segmentation wait made visible: scan
// sweep over the photo + honest step text. Extracted verbatim from StudioHub
// so the composer's RE-SCAN shows the same viewfinder. Dismissible: the scan
// runs server-side and its result is cached, so leaving is always safe.
// Portaled to <body> — never trust a fixed overlay inside .rise ancestors.
import { createPortal } from 'react-dom'

export default function ScanOverlay({
  photoUrl, step, onHide,
}: {
  photoUrl: string
  step: string
  onHide: () => void
}) {
  return createPortal(
    <div className="cwlb" id="finding-regions">
      <div className="cwlb-body" onClick={e => e.stopPropagation()} style={{ maxWidth: 420 }}>
        <div style={{ padding: '20px 18px', textAlign: 'center' }}>
          <div className="scan-frame">
            <img src={photoUrl} alt="" />
            <div className="scan-grid" />
            <div className="scan-beam" />
            <div className="scan-dot" />
            <div className="scan-dot d2" />
            <div className="scan-dot d3" />
            <div className="scan-corner tl" /><div className="scan-corner tr" />
            <div className="scan-corner bl" /><div className="scan-corner br" />
          </div>
          <div className="syne" style={{ fontSize: 17, fontWeight: 800, marginTop: 14 }}>
            Finding regions
          </div>
          <div className="mono scan-step" style={{ fontSize: 9.5, letterSpacing: '.08em', color: 'var(--fog)', marginTop: 6, minHeight: 14 }}>
            {step}
          </div>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--faint)', marginTop: 10, lineHeight: 1.7 }}>
            USUALLY 10–20 SECONDS · SAFE TO KEEP BROWSING —<br />REGIONS ARE CACHED THE MOMENT THEY'RE READY
          </div>
          <button
            className="kbtn gc-open press"
            id="finding-hide"
            style={{ width: 'auto', padding: '8px 14px', marginTop: 12 }}
            onClick={onHide}
          >
            KEEP BROWSING
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
