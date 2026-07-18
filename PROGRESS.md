# Atelier — Build Progress

> Companion to `atelier_PRD_v2.docx` + `atelier_TDD_v1.docx` (the contract) and
> `atelier_light_v3 (3).html` (the visual spec). Update this file at the end of
> every working session.

**Last updated:** 2026-07-16 · session 2 (M2 + M3 + long-term needs)

---

## Milestone status (TDD §11)

| M | Scope | Status |
|---|-------|--------|
| M0 | Seedream vs Nano Banana bake-off | **DONE 2026-07-18** — ran on 2 real photos (3rd pending, non-blocking), $1.37 spent of $10 cap. **Primary adapter: Seedream 5.0 Pro** (`bytedance/seedream/v5/pro/edit` — no `fal-ai/` prefix); NB2 is a real fallback (3x faster @12.5s → eager/preview drafts + vendor insurance). Full report + 14 outputs in session scratch `atelier-m0/t1/bakeoff-report.md`. |
| M1 | Foundation | **DONE** (committed; "real URL" DoD still pending deploy accounts). |
| M2 | Archive core | **DONE + verified** — backend proven end-to-end, 67-test pytest suite, Barrel Bag 001 seeded via the real API, frontend built and browser-verified, thumbnails live. |
| M3 | Gallery + share link | **DONE + verified** — 84-test suite, public page proven in a logged-out browser at 390px. DoD ("link opens for someone with no account") met locally; needs the M1 deploy for a real public URL. |
| M4 | PWA + inbox triage UI | Not started (inbox API exists + 3 real untriaged fixtures; share-target manifest is in). |
| M5–M8 | Wada Studio | Not started. Schema already migrated. |

## What exists and is verified working

- **Infra** — `docker compose up -d`: Postgres 16 on **:5433**, Redis 7 on
  **:6380**, MinIO on **:9000** (console :9001, atelier/atelier-local).
- **Schema** — migration `0001` = complete TDD §2 DDL (18 tables), phase sync
  triggers, workspace seed `00000000-…-0001 / rei`.
- **Auth** — magic link → 30-day JWT. Without `RESEND_API_KEY` the link prints
  to the API log.
- **Backend M2 (all routes exercised live with real auth):**
  - designs: POST (auto `index_no`), list `?status=`, GET, PATCH (incl. cover).
  - media: upload-url (presigned PUT + sha256 dedupe via `duplicate_of`),
    commit (rejects key/sha mismatch 422, missing object 409), per-design
    `?phase=` listing, inbox + triage.
  - entries: timeline `?phase=`, POST with media attach, PATCH (phase change
    re-syncs media via trigger).
  - FK violations on all write paths → clean 404 (global handler in `main.py`),
    not 500.
  - **Thumbnails (TDD §3):** `thumbs.generate` Celery task renders
    200/400/800 WEBP at `thumb/{ws}/{sha}/{w}.webp` on commit (best-effort
    enqueue — commit never blocks/fails if broker or worker is down);
    `thumb_key` → 400 variant; `thumb_url` served everywhere; backfill via
    `python -m app.workers.backfill_thumbs` (idempotent, already run on dev).
    Run the worker with `celery -A app.workers.celery_app worker`.
- **pytest suite — the regression gate:** `cd backend && .venv/bin/python -m
  pytest` → **67 passed** (~2.5s). Self-provisions an isolated `atelier_test`
  DB + `atelier-test` bucket (real Postgres/MinIO/Redis, in-process app); has
  a dev-data preservation guard. Run it before accepting any backend change.
- **Frontend M2 (verified in real chromium, 32 scripted checks, 0 console
  errors):** project design grid with status chips (real `?status=` queries),
  design detail (hero crossfade + rail, Timeline/Media toggle, phase chips
  filtering without remounts per TDD §10.2, entry card variants, masonry,
  lightbox), capture sheet (5 modalities, dest pill design↔Inbox, real
  presigned upload → commit → entry). `pnpm build` + oxlint green. Grid reads
  `thumb_url ?? url` (srcset upgrade possible later — 200/800 already exist).
- **Share links (M3, TDD §4/§9):** POST `/share` (auth, idempotent — one live
  URL per target+scope per PRD A8, 200 reuse vs 201 new); public `GET /s/{slug}`
  on a separate no-auth router, Redis rate limit 60/min/IP (counts media
  redirects — revisit before M4), view_count, revoked→404 (revoke = SQL
  `UPDATE share_links SET revoked_at=now()`). The projection contains ZERO
  internal uuids: media are addressed by opaque `/s/{slug}/m/{i}[/thumb]`
  paths that 307-redirect to presigned storage GETs. `finals` scope = phases
  (final, editorial) per PRD A7; `full` adds text-note entries.
- **Gallery + public page (M3 frontend, verified in chromium 31/31 × my rerun):**
  `/gallery` Stack (fanned carousel) + Ring modes over cross-project
  final+editorial media, phase chips; ShareSheet (scope toggle, copy+toast)
  from design header, project header, ring centre; `/s/:slug` public page with
  no app shell (finals grid or full timeline, not-found/429 states), zero
  authed requests when logged out, no horizontal overflow at 390px.
  **Prod routing is load-bearing:** same-origin `/api/* → backend` (prefix
  stripped) — the public page fetches `/api/s/{slug}`; `publicPath()` in
  api.ts is the seam if the API base ever changes.
- **Dev data:** project "rei by Rei" → design **Barrel Bag 001**
  (`f28f5b7e-c23d-44f4-950c-d5832084e7bb`, in_production): 10 entries, 14 design
  media across 5 phases (incl. 2 editorial + 1 extra final seeded for the
  gallery) + 3 untriaged inbox items, all thumbed. Live share links:
  `/s/reibyrei-4139` (project finals), `/s/reibyrei-mupd` (project full),
  `/s/barrelbag001-tax7` + `-c8d9` (design finals/full); `barrelbag001-j9wj`
  revoked (404 fixture). Reusable seed flow in session-2 scratch artifacts.
- **`LONGTERM-NEEDS.md`** (repo root): full external-dependency / cost /
  setup-order assessment, all prices cited as of 2026-07-16. Headline: 6
  accounts total, ~$9.3/mo fixed (Hetzner+R2), Wada <$2 target holds,
  Seedream price-favored at 1536px.

## M0 findings that amend TDD §8 (load-bearing for M5–M8)

- `gemini-2.5-flash` is 404 ("not available to new users") on our key — segmentation
  runs on **gemini-3.5-flash**. Latency 10–13s per photo (<15s DoD ✓), labels/confidence
  excellent (0.99).
- TDD §8.1's `mask_png_b64` contract is **dead on Gemini 3.x** (requesting PNG masks
  loops to MAX_TOKENS). The real contract is **polygon masks, [y,x]-ordered, 0–1000
  normalized** — but they're coarse (~20 vertices), so **M5 needs a mask-refinement
  stage** (dense polygons / matting / grow-cut) before §8.10's lock guarantee is real.
  This is now the M5 critical path.
- **Neither API has a true mask parameter.** Seedream's `supports_mask` = mask passed
  as reference image + instruction, obeyed ~50% in our sample → treat as advisory.
  §8.10 post-hoc compositing is the correctness guarantee for BOTH adapters.
- Seedream: superb in-mask quality (ΔE 4–9, leather texture flawless) but poor edit
  locality; 39s/edit — trie chains of 2–3 steps won't fit "first colorway <45s", NB2
  eager drafts cover that. Cost figures confirmed ($0.0675@1536 / NB2 $0.101@2K).
- Prompt gotcha: "keep metal hardware silver" turns the embroidered crest into metal
  on both models — reword per-region in M7 templates.

## Product decisions to make (found during verification, deferred)

- Dedupe semantics: capturing byte-identical media to a design **moves** the
  existing media row onto the new entry. Decide before M4's share-target lands
  Instagram duplicates (surface "already in archive"?).
- Dedupe commit returns 201 (not 200) for an existing row — pinned by tests.
- Inspo modality requires an image; a bare link has no backend representation.
- Voice capture implemented (MediaRecorder → webm) but not mic-tested; audio
  media get no thumbs (skipped cleanly). HEIC needs `pillow-heif` before real
  iPhone captures (M4).
- Mobile layout uses the mock's responsive CSS but wasn't browser-driven.

## Added scope (session 2, Beezy)

- **PhotoRoom background removal** for uploaded images: Celery task calling the
  PhotoRoom API (same pattern as `thumbs.generate` — async, best-effort,
  idempotent backfill), result stored as a derivative (needs a `cutout_key`
  migration, mirroring `thumb_key`). Default plan: auto-run for photos in
  `final`/`editorial` phases + on-demand elsewhere (credit control) — confirm
  with Beezy whether it should run on all uploads. Needs a PhotoRoom API key
  (sandbox tier exists for dev). Feeds M3 gallery heroes and M5+ Wada inputs.
- **Storage vendor is open, Cloudflare not required:** the S3 adapter is
  generic; R2 (TDD default, zero egress, 10GB free tier) vs Tigris (Fly-native)
  vs MinIO-on-VPS — decide at deploy time, env-var change only.
- **Hosting assessment (2026-07-16):** Fly.io (TDD default) ≈ $8–25/mo for
  API + worker + PG + Redis; a single Hetzner CX23 (~€4/mo, 2vCPU/4GB, 20TB
  traffic) runs the existing compose stack as-is for ~3–5x less. R2 free tier
  makes storage ~$0 either way. Recommendation: Hetzner VPS + R2 unless Beezy
  prefers Fly's managed deploys. Decide at deploy (M1 "real URL" DoD).
- **PhotoRoom pipeline ON HOLD** per Beezy; API key received and stored in
  `backend/.env` (gitignored — key lives only there, never in git).

## How to resume

```bash
docker compose up -d                          # infra
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
cd backend && .venv/bin/celery -A app.workers.celery_app worker  # thumbs
cd frontend && pnpm dev                       # :5173, magic link prints to API log
cd backend && .venv/bin/python -m pytest      # 67 tests, isolated infra
```

## Decisions made (beyond the TDD)

- Session 1: Vite 8, Python 3.13, MinIO + console magic links locally,
  `pydantic[email]`.
- Session 2: `thumb_key` points at the 400px WEBP (schema has one column; the
  TDD's 200/800 variants exist at canonical keys for future srcset). Pillow
  12.3 added. FK errors mapped centrally (constraint-name suffix → entity) in
  `main.py` rather than per-router.

## Needs from Beezy

- ~~fal.ai + Gemini API keys~~ RECEIVED 2026-07-16 (in `backend/.env`, gitignored;
  rotate all before prod — they transited chat). Resend key also received,
  stored commented-out (activating it switches local auth to real email sends).
- **3 real product photos** → the only remaining M0 blocker.
- Domain + Resend domain verification (SPF/DKIM) → deploy time.
- Fly.io account → deploy (M1's "real URL" DoD; the Fly setup needs a Celery
  worker process alongside the API for thumbs/Wada). Storage: R2 or Tigris —
  Cloudflare optional, see "Added scope".
- PhotoRoom API key (sandbox tier fine for dev) → background-removal pipeline.
- Product calls on the dedupe/share-target semantics above before M4, and on
  whether PhotoRoom runs on all uploads or only final/editorial + on-demand.
