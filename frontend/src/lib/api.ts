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

// ── inbox triage (M4, PRD A5) ───────────────────────────────────────────────

/** Untriaged captures (design_id IS NULL), newest first. */
export const fetchInbox = () => api<Media[]>('/inbox')

/** Assign an inbox capture to a design + phase. The backend creates the
 *  carrying entry; a re-triage of the same media is a 404 (no longer in inbox). */
export const triageMedia = (mediaId: string, design_id: string, phase: Phase) =>
  api<Media>(`/inbox/${mediaId}/triage`, {
    method: 'POST',
    body: JSON.stringify({ design_id, phase }),
  })

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

// ── Wada Studio (M5, TDD §8/§9): regions, palettes, studies ─────────────────

export interface Region {
  id: string
  media_id: string
  label: string
  label_source: string
  confidence: number
  mask_url: string
  mask_key: string
  area_fraction: number
  bbox: [number, number, number, number] // mask-frame px (1536 long edge)
  mean_lab: number[]
  is_cosmetic: boolean
  created_at: string
}

export interface SegmentOut {
  status: 'complete' | 'pending'
  regions: Region[]
}

export interface SanzoColor {
  id: number
  name: string
  hex: string
  lab_l: number
  lab_a: number
  lab_b: number
  chroma: number
  hue_deg: number
  hue_family: string
}

export interface Palette {
  id: string
  name: string
  color_count: number
  color_ids: number[]
  colors: SanzoColor[]
  mean_lab_l: number
  mean_chroma: number
  temperature: string
  max_delta_e: number
  min_delta_e: number
  volume: number | null
  plate: number | null
}

export interface Slot {
  id: string
  idx: number
  label: string
  kind: 'paint' | 'lock'
  region_ids: string[]
  union_mask_key: string
  union_mask_url: string
  union_sha256: string
  area_fraction: number
  anchor_color_id: number | null
}

export interface Study {
  id: string
  design_id: string
  base_media_id: string
  palette_id: string
  status: string
  policy: Record<string, unknown>
  perm_total: number | null
  perm_planned: number | null
  est_cost_cents: number | null
  actual_cost_cents: number
  model_id: string
  prompt_version: string
  created_at: string
  k: number
  slots: Slot[]
}

export interface SlotIn {
  label: string
  kind: 'paint' | 'lock'
  region_ids: string[]
  anchor_color_id?: number | null
}

/** §8.13.1 readout — every number the estimate panel shows, from the engine. */
export interface Estimate {
  k: number
  c: number
  anchors: number
  regime: 'bijective' | 'injective' | 'surjective'
  perm_total: number
  after_dedupe: number
  planned: number
  steps_per_colorway: number
  naive_calls: number
  naive_cost: string
  trie_calls_full: number
  trie_cost_full: string
  saved_pct: number
  planned_calls: number
  est_cost: string
  est_cost_cents: number
  eager: number
  eta_seconds: number
  cap: number
  cap_exceeded: boolean
  cap_message: string | null
  message: string
}

export const fetchRegions = (mediaId: string) => api<Region[]>(`/media/${mediaId}/regions`)
export const segmentMedia = (mediaId: string) =>
  api<SegmentOut>(`/media/${mediaId}/segment`, { method: 'POST' })
export const fetchStudy = (studyId: string) => api<Study>(`/studies/${studyId}`)
export const createStudy = (body: { design_id: string; base_media_id: string; palette_id: string }) =>
  api<Study>('/studies', { method: 'POST', body: JSON.stringify(body) })
export const putSlots = (studyId: string, slots: SlotIn[]) =>
  api<Study>(`/studies/${studyId}/slots`, { method: 'PUT', body: JSON.stringify(slots) })
export const estimateStudy = (studyId: string) =>
  api<Estimate>(`/studies/${studyId}/estimate`, { method: 'POST' })
export const fetchPalette = (id: string) => api<Palette>(`/palettes/${id}`)
export const fetchPalettes = (facets: { count?: number; q?: string }) => {
  const p = new URLSearchParams()
  if (facets.count) p.set('count', String(facets.count))
  if (facets.q) p.set('q', facets.q)
  const qs = p.toString()
  return api<Palette[]>(`/palettes${qs ? `?${qs}` : ''}`)
}

/** §8: at most 8 paint slots (backend MAX_PAINT_SLOTS). */
export const MAX_PAINT_SLOTS = 8
/** Palette a fresh study opens with; swappable in the composer. */
export const DEFAULT_PALETTE_ID = 'c125'

/* §9 has no per-design study list yet (M5-T2 gap) — remember the last study
   we opened per design so "Explore colorways" reopens it. */
const studyMemoKey = (designId: string) => `atelier.study.${designId}`
export const rememberStudy = (designId: string, studyId: string) =>
  localStorage.setItem(studyMemoKey(designId), studyId)
export const recallStudy = (designId: string) => localStorage.getItem(studyMemoKey(designId))
export const forgetStudy = (designId: string) => localStorage.removeItem(studyMemoKey(designId))

/** FastAPI errors are {"detail": "..."} — pull the human string for toasts. */
export const apiErrorDetail = (e: unknown, fallback: string): string => {
  if (e instanceof ApiError) {
    try {
      const d = (JSON.parse(e.message) as { detail?: unknown }).detail
      if (typeof d === 'string') return d
    } catch { /* not JSON */ }
  }
  return fallback
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
