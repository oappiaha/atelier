# Atelier — Build Progress

> Companion to `atelier_PRD_v2.docx` + `atelier_TDD_v1.docx` (the contract) and
> `atelier_light_v3 (3).html` (the visual spec). Update this file at the end of
> every working session.

**Last updated:** 2026-07-15 · session 1

---

## Milestone status (TDD §11)

| M | Scope | Status |
|---|-------|--------|
| M0 | Seedream vs Nano Banana bake-off | **Blocked on keys** — needs fal.ai + Gemini API keys and 3 real rei by Rei photos. ~1 day, <$10. Decides the primary adapter; nothing else blocked by it. |
| M1 | Foundation | **DONE** — committed as `M1 foundation` (first commit on main). |
| M2 | Archive core | **IN PROGRESS** — backend ~done, unverified. Frontend not started. |
| M3 | Gallery + share link | Not started. |
| M4 | PWA + inbox triage UI | Not started (inbox API exists; share-target manifest is in). |
| M5–M8 | Wada Studio | Not started. Schema already migrated. |

## What exists and is verified working

- **Infra** — `docker compose up -d`: Postgres 16 on **:5433**, Redis 7 on
  **:6380**, MinIO on **:9000** (console :9001, atelier/atelier-local). All
  ports non-default to avoid clashes.
- **Schema** — migration `0001` = the complete TDD §2 DDL (18 tables incl. all
  Wada tables), the `media.phase` sync triggers, workspace seed
  `00000000-…-0001 / rei`. Applied and inspected.
- **Auth** — magic link → 30-day JWT (`sub`, `ws` claims). With no
  `RESEND_API_KEY`, the link prints to the API log. Exercised end-to-end.
- **Storage** — S3 adapter (R2-compatible); `atelier` bucket auto-creates on
  API startup. Presigned PUT/GET helpers in `backend/app/storage.py`.
- **Projects API** — GET/POST `/projects` verified; project "rei by Rei"
  exists in the local DB.
- **Frontend shell** — React 19 + Vite PWA at :5173, `/api` proxy to :8000.
  `tokens.css` + `tactile.css` lifted verbatim from the mock. Login flow +
  Home (projects list) real; Project/Design/Gallery/Studio are placeholders.
  TypeScript clean, `pnpm build` green in CI config.

## M2 state — exactly where we stopped

Backend routers written, registered in `main.py`, ruff-clean, API boots:

- `routers/designs.py` — POST `/designs` (auto `index_no`), GET
  `/projects/{id}/designs` (?status=), GET/PATCH `/designs/{id}`.
- `routers/media.py` — POST `/media/upload-url` (presigned PUT + sha256
  dedupe), POST `/media/commit`, GET `/designs/{id}/media` (?phase=, the hot
  query), GET `/inbox`, POST `/inbox/{id}/triage`.
- `routers/entries.py` — GET `/designs/{id}/timeline` (?phase=), POST
  `/entries` (+ attach media ids), PATCH `/entries/{id}`.

**NOT yet done for M2:**

1. ~~Route smoke test~~ (was mid-verification when session paused — run the
   API and hit each endpoint once).
2. **pytest suite** for the API surface (task #7).
3. **Seed Barrel Bag 001** through the real API: create design, upload real
   bytes via presigned PUT, entries across phases, verify timeline/media
   filters + inbox triage (PRD §7 DoD).
4. **Frontend M2** (task #6): project design grid with status filter chips,
   design detail (hero, Timeline/Media seg toggle, phase chips, timeline
   cards, masonry + lightbox), capture sheet (5 modalities, dest pill →
   design or Inbox). Rendering discipline per TDD §10.2: build once, mutate
   in place, stable keys, entrance animations on mount only.
5. Thumbnails: `thumb_key` is in the schema but nothing generates thumbs yet —
   needs a resize step (worker or on-upload) before the grid gets heavy.

## How to resume

```bash
docker compose up -d                          # infra
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && pnpm dev                       # :5173, magic link prints to API log
```

## Decisions made this session (beyond the TDD)

- Vite 8 (current) instead of the TDD's pinned Vite 6 — no API differences
  that affect us; vite-plugin-pwa is compatible.
- Python 3.13 (Homebrew) instead of 3.12 — no pinned dep conflicts.
- Local dev uses MinIO + console-printed magic links so zero external
  accounts are needed until deploy (Fly.io, Resend, R2, Sentry all pending).
- `pydantic[email]` added for EmailStr validation.

## Needs from Beezy

- fal.ai + Gemini API keys, 3 real product photos → unblocks M0.
- Resend API key + domain → real magic-link emails (deploy time).
- Fly.io + Cloudflare R2 accounts → deploy (M1's "real URL" DoD is unmet
  until then; local-only today).
