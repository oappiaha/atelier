const TOKEN_KEY = 'atelier.jwt'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (jwt: string) => localStorage.setItem(TOKEN_KEY, jwt)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const jwt = getToken()
  if (jwt) headers.set('Authorization', `Bearer ${jwt}`)

  const res = await fetch(`/api${path}`, { ...init, headers })
  if (res.status === 401) {
    clearToken()
    if (location.pathname !== '/login') location.assign('/login')
  }
  if (!res.ok) throw new ApiError(res.status, await res.text())
  return res.json() as Promise<T>
}

// ── domain vocabulary (TDD §2, PRD §"Fixed vocabulary") ─────────────────────

export const PHASES = [
  'moodboard', 'inspiration', 'sketch', 'note', 'voice',
  'manufacturing', 'editorial', 'final', 'study',
] as const
export type Phase = (typeof PHASES)[number]

export const PHASE_LABELS: Record<Phase, string> = {
  moodboard: 'Mood board',
  inspiration: 'Inspiration',
  sketch: 'Sketch',
  note: 'Note',
  voice: 'Voice note',
  manufacturing: 'Manufacturing',
  editorial: 'Editorial',
  final: 'Final product',
  study: 'Colorway study',
}

export const STATUSES = ['developing', 'in_production', 'final', 'archive'] as const
export type DesignStatus = (typeof STATUSES)[number]

export const STATUS_LABELS: Record<DesignStatus, string> = {
  developing: 'Developing',
  in_production: 'In production',
  final: 'Final',
  archive: 'Archive',
}

/* status → mock's colour classes (.st-final/.st-dev/.st-mfg/.st-arch) */
export const STATUS_CLASS: Record<DesignStatus, string> = {
  developing: 'st-dev',
  in_production: 'st-mfg',
  final: 'st-final',
  archive: 'st-arch',
}

// ── API shapes (verified against the running backend, T1 evidence) ──────────

export interface Project {
  id: string
  name: string
  kicker: string | null
  design_count: number
}

export interface Design {
  id: string
  project_id: string
  name: string
  status: DesignStatus
  index_no: number
  materials: string | null
  cover_media_id: string | null
  cover_url: string | null
  entry_count: number
  media_count: number
  created_at: string
}

export interface Media {
  id: string
  design_id: string | null
  entry_id: string | null
  kind: 'image' | 'audio'
  phase: Phase | null
  url: string
  thumb_url: string | null
  width: number | null
  height: number | null
  duration_ms: number | null
  caption: string | null
  source_url: string | null
  source_app: string | null
  created_at: string
}

export interface Entry {
  id: string
  design_id: string
  phase: Phase
  body: string | null
  study_id: string | null
  occurred_at: string
  is_open: boolean
  media: Media[]
}

// ── share links (M3, TDD §4) ────────────────────────────────────────────────

export const SCOPES = ['finals', 'full'] as const
export type ShareScope = (typeof SCOPES)[number]
export const SCOPE_LABELS: Record<ShareScope, string> = {
  finals: 'Finals',
  full: 'Full timeline',
}

export interface ShareLink {
  id: string
  project_id: string | null
  design_id: string | null
  slug: string
  scope: ShareScope
  url: string // API-relative: /s/{slug}
  revoked_at: string | null
  view_count: number
  created_at: string
}

/** Mint (or fetch the existing live) view-only link. Idempotent per target+scope. */
export const mintShare = (target: { project_id?: string; design_id?: string }, scope: ShareScope) =>
  api<ShareLink>('/share', { method: 'POST', body: JSON.stringify({ ...target, scope }) })

/* The public projection contains NO internal ids; media are addressed by
   opaque API-relative paths (/s/{slug}/m/{i}) that 307-redirect to storage.
   Everything public rides the same /api origin prefix the rest of the app
   uses (vite dev proxy now; one reverse proxy in prod). */
export const publicPath = (apiRelative: string) => `/api${apiRelative}`

export interface PublicMedia {
  index: number
  kind: 'image' | 'audio'
  phase: Phase | null
  url: string
  thumb_url: string | null
  width: number | null
  height: number | null
  duration_ms: number | null
  caption: string | null
  created_at: string
}

export interface PublicNote {
  phase: Phase
  body: string
  occurred_at: string
}

export interface PublicDesign {
  name: string
  index_no: number
  status: DesignStatus
  media: PublicMedia[]
  notes: PublicNote[]
}

export interface PublicProjection {
  slug: string
  scope: ShareScope
  target: 'project' | 'design'
  project: { name: string; kicker: string | null } | null
  designs: PublicDesign[]
}

/** Unauthenticated fetch for the public gallery — NEVER attaches the JWT and
 *  never redirects to /login (recipients have no account). */
export async function fetchPublicProjection(slug: string): Promise<PublicProjection> {
  const res = await fetch(publicPath(`/s/${slug}`))
  if (!res.ok) throw new ApiError(res.status, await res.text())
  return res.json() as Promise<PublicProjection>
}

interface UploadUrlOut {
  upload_url: string | null
  r2_key: string
  duplicate_of: string | null
}

// ── upload pipeline (TDD §9: upload-url → direct PUT to storage → commit) ───

const UPLOADABLE = new Set([
  'image/jpeg', 'image/png', 'image/webp', 'image/heic',
  'audio/mp4', 'audio/m4a', 'audio/webm', 'audio/mpeg',
])

async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('')
}

function imageDims(blob: Blob): Promise<{ width: number; height: number } | null> {
  return createImageBitmap(blob)
    .then(bmp => {
      const d = { width: bmp.width, height: bmp.height }
      bmp.close()
      return d
    })
    .catch(() => null)
}

export interface UploadOpts {
  kind: 'image' | 'audio'
  caption?: string
  source_url?: string
  source_app?: string
  entry_id?: string
  duration_ms?: number
}

/** Full capture pipeline for one file. Returns the committed media row.
 *  entry_id omitted → the capture lands in the Inbox. */
export async function uploadMedia(file: Blob, opts: UploadOpts): Promise<Media> {
  const contentType = file.type || 'image/jpeg'
  if (!UPLOADABLE.has(contentType)) {
    throw new ApiError(422, `Unsupported file type ${contentType || '(unknown)'}`)
  }
  const buf = await file.arrayBuffer()
  const sha256 = await sha256Hex(buf)

  const u = await api<UploadUrlOut>('/media/upload-url', {
    method: 'POST',
    body: JSON.stringify({ sha256, content_type: contentType, kind: opts.kind }),
  })

  // duplicate_of set → the bytes are already in the archive; skip the PUT.
  if (u.upload_url) {
    const put = await fetch(u.upload_url, {
      method: 'PUT',
      body: file,
      headers: { 'Content-Type': contentType },
    })
    if (!put.ok) throw new ApiError(put.status, 'Storage upload failed')
  }

  const dims = opts.kind === 'image' ? await imageDims(file) : null
  return api<Media>('/media/commit', {
    method: 'POST',
    body: JSON.stringify({
      sha256,
      r2_key: u.r2_key,
      kind: opts.kind,
      width: dims?.width ?? null,
      height: dims?.height ?? null,
      duration_ms: opts.duration_ms ?? null,
      caption: opts.caption || null,
      source_url: opts.source_url || null,
      source_app: opts.source_app || null,
      entry_id: opts.entry_id ?? null,
    }),
  })
}
