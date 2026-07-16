# Atelier — Build Progress

> Companion to `atelier_PRD_v2.docx` + `atelier_TDD_v1.docx` (the contract) and
> `atelier_light_v3 (3).html` (the visual spec). Update this file at the end of
> every working session.

**Last updated:** 2026-07-16 · session 2

---

## Milestone status (TDD §11)

| M | Scope | Status |
|---|-------|--------|
| M0 | Seedream vs Nano Banana bake-off | **Blocked on keys** — needs fal.ai + Gemini API keys and 3 real rei by Rei photos. ~1 day, <$10. Decides the primary adapter; nothing else blocked by it. |
| M1 | Foundation | **DONE** (committed; "real URL" DoD still pending deploy accounts). |
| M2 | Archive core | **DONE + verified** — backend proven end-to-end, 67-test pytest suite, Barrel Bag 001 seeded via the real API, frontend built and browser-verified, thumbnails live. |
| M3 | Gallery + share link | Not started. |
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
- **Dev data:** project "rei by Rei" → design **Barrel Bag 001**
  (`f28f5b7e-c23d-44f4-950c-d5832084e7bb`, in_production): 8 entries, 11 design
  media across 5 phases + 3 untriaged inbox items, all thumbed. Reusable seed
  flow lives in the session-2 scratch artifacts (`seed_barrel_bag.py`).

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
  generic; R2 (TDD default, zero egress) vs Tigris (Fly-native, zero egress,
  single-vendor deploy) — decide at deploy time, env-var change only.

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

- fal.ai + Gemini API keys, 3 real product photos → unblocks M0.
- Resend API key + domain → real magic-link emails (deploy time).
- Fly.io account → deploy (M1's "real URL" DoD; the Fly setup needs a Celery
  worker process alongside the API for thumbs/Wada). Storage: R2 or Tigris —
  Cloudflare optional, see "Added scope".
- PhotoRoom API key (sandbox tier fine for dev) → background-removal pipeline.
- Product calls on the dedupe/share-target semantics above before M4, and on
  whether PhotoRoom runs on all uploads or only final/editorial + on-demand.
