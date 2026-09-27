# Performance improvements — local implementation

## Measured result

Production build, 390×844 Chromium viewport, fresh contexts, three runs per case, one project/100 designs, simulated150ms delay per API request. Same harness and sample images as the load audit, with a fixture for the new endpoint. No bandwidth/CPU throttling, no external fonts, no service workers in timing runs. This measures visible content readiness, not full image decode or production Web Vitals.

| Metric | Before | After |
|---|---:|---:|
| Gallery content-ready median | 843ms | 230ms |
| Cold Gallery API requests | 102 | 1 |
| Initial Gallery image requests | 100 | 3 |
| Initial JS, gzip (entry + preload dependencies) | 134.52KB | 113.66KB |
| Home content-ready median | 232ms | 227ms |
| Project content-ready median | 314ms | 297ms |

Gallery readiness improved approximately73% in this controlled simulation. Home/Project differences are small enough not to claim a material latency improvement. Runtime/backend/CDN production timing is still unmeasured.

## Implemented

- GET /gallery returns24 items/page (maximum60), phase counts, total and compact project identifiers for sharing. Three SQL reads per page replace per-design HTTP fan-out. Stable cursor ordering uses phase, timestamp and UUID; workspace constraints cover all joins. Only page thumbnails are presigned; full media payloads/wordmark data are not included.
- Gallery fetches another page near the loaded boundary, with explicit retry/load-more fallback. Server phase filtering preserves global counts. Stack mounts at most5 neighboring cards (3 at the first position); Ring is a bounded16-thumbnail preview, with full traversal through Stack.
- Secondary screens, including Studio and its hub, are lazy route chunks. The service worker precaches the entry/shared shell scripts and caches other code when visited, rather than precaching every lazy chunk. Unvisited secondary routes need a first online load.
- Design timeline and media-grid thumbnails use native image lazy loading; hero loading is unchanged.
- StudioHub no longer starts up to6 speculative segmentation jobs on entry. New study retains processing for the chosen base photo. First use of an unprocessed photo may wait longer after selection; generation behavior/cost settings are unchanged.
- Capture, triage, design deletion and Studio Shot invalidate paginated Gallery queries.

## Verification

- Frontend lint/typecheck/build and backend ruff passed.
- Production browser regression suite: gallery_loading, context_panel, context_actions, gallery_sharing, navigation_edges, archive_browse, busy_sheet passed. Includes all100 items, pagination boundary, server filtering/counts, selected-card restoration, share target, bounded image requests, code loading, no automatic segment request, initial-error recovery, 320/390/1440 layouts and existing user flows.
- Actual project Gallery router through HTTPX ASGI + disposable Postgres17: all migrations applied;63 eligible rows traversed in3 pages with tied timestamps and no duplicates. Verified filtering/counts, thumbnails, invalid cursor/limit/filter422s, deletion between page requests, missing-auth401, cross-workspace isolation. Reproducer backend/scripts/check_gallery.py. No storage or paid model requests.
- Actual service worker: shell scripts cached, unvisited Gallery/Studio scripts absent, offline login shell reload succeeded. Reproducer frontend/tests/browser/pwa_cache.py.
- Full backend pytest suite was not run; its MinIO/Redis infrastructure is unavailable. Scoped API/database proof above covers the changed endpoint.
- Evidence: /tmp/deez/performance/{before,after}.json, benchmark.log, browser-final.log, pwa.json. Benchmark checked in at frontend/tests/browser/load_benchmark.py.

No deployment, production mutations or commit in this round. No new database migration for performance. Deploy backend /gallery support before the frontend that consumes it. Earlier uncommitted UI/wordmark changes are preserved.
