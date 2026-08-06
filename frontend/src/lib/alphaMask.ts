// LUMINANCE-MASK FALLBACK (iOS/WebKit). Region masks are grayscale PNGs
// (white = region, black = not) with NO alpha channel; the composer tints
// regions via CSS `mask-mode: luminance`. WebKit ignores luminance mode for
// raster masks and always reads ALPHA — a fully-opaque mask, so the ENTIRE
// photo gets tinted (the "solid blue wash" bug). On browsers without
// luminance support we convert each mask to an alpha mask in-browser:
// draw to canvas, alpha := luma, white RGB (white × alpha == alpha, so the
// data URL renders identically under BOTH mask modes). Decorative overlay ⇒
// downscale to ≤512 on the long edge; 1536-frame fidelity isn't needed.

/** True when CSS luminance mask-mode actually works here.
 *  Testing hook: `localStorage.setItem('wada:forceAlphaMask','1')` + reload
 *  forces the conversion path on any browser. */
export const luminanceMaskSupported = (): boolean => {
  try {
    if (localStorage.getItem('wada:forceAlphaMask')) return false
  } catch { /* storage blocked — fall through to real detection */ }
  return typeof CSS !== 'undefined'
    && CSS.supports('mask-mode', 'luminance')
    && !/\b(iPad|iPhone|iPod)\b/.test(navigator.userAgent)
    // iPadOS 13+ masquerades as macOS Safari; real macs have no touch points
    && !(/\bMac/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
}

const MAX_EDGE = 512

// keyed by region id: presigned mask URLs rotate, the pixels behind them
// don't — one conversion per region per session, promises dedupe re-entry
const cache = new Map<string, Promise<string>>()

/** Convert a region's grayscale mask PNG into an alpha-mask data URL. */
export const toAlphaMask = (regionId: string, maskUrl: string): Promise<string> => {
  const hit = cache.get(regionId)
  if (hit) return hit
  const p = (async () => {
    const img = new Image()
    img.crossOrigin = 'anonymous' // getImageData needs a CORS-clean bitmap
    // onload, NOT img.decode(): Chromium can defer decode() indefinitely in
    // occluded/background tabs — the promise simply never settles there
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error(`mask fetch failed: ${maskUrl.slice(0, 120)}`))
      img.src = maskUrl
    })
    const scale = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight))
    const w = Math.max(1, Math.round(img.naturalWidth * scale))
    const h = Math.max(1, Math.round(img.naturalHeight * scale))
    const canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) throw new Error('canvas 2d unavailable')
    ctx.drawImage(img, 0, 0, w, h)
    const data = ctx.getImageData(0, 0, w, h)
    const px = data.data
    for (let i = 0; i < px.length; i += 4) {
      // Rec.601 luma → alpha; RGB → white so luminance(px) == alpha(px)
      px[i + 3] = (px[i] * 77 + px[i + 1] * 150 + px[i + 2] * 29) >> 8
      px[i] = px[i + 1] = px[i + 2] = 255
    }
    ctx.putImageData(data, 0, 0)
    return canvas.toDataURL('image/png')
  })()
  p.catch(() => cache.delete(regionId)) // failures retry on next render
  cache.set(regionId, p)
  return p
}
