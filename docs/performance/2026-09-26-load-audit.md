# Load audit — 2026-09-26

Measured the current production build locally in Chromium, 390×844 viewport, fresh browser context for each cold run, three runs per case. Service workers blocked. External font CSS replaced with an empty response to exclude font-network variability. API responses used isolated fixtures: one project, 10 or 100 designs, one image per design. Each image had a unique URL serving the same sample JPEG. Async request interception introduced either zero or 150ms delay per API request, concurrently. No real backend/database/CDN or production authentication was exercised. No bandwidth or CPU throttling. These are diagnostic timings, not production Web Vitals.

Readiness means the first populated project/design/card became visible, not all images decoded or the full screen became interactive. Request counts recorded after another 800ms. Gallery warm return measured actual Next → Archive → Gallery clicks.

| Screen / designs | 0ms API delay median | 150ms API delay median | Cold API requests | Image requests |
|---|---:|---:|---:|---:|
| Home / 100 | 57ms | 232ms | 2 | 0 |
| Project / 100 | 62ms | 314ms | 2 | 34 |
| Gallery / 10 | 96ms | 836ms | 12 | 10 |
| Gallery / 100 | 362ms | 843ms | 102 | 100 |

Gallery warm return: median54ms (100 designs, zero API delay),66ms (100 designs,150ms delay). All measured runs had no page errors or unhandled API fixtures.

Build: JS451.66kB raw /134.52kB gzip; CSS60.90kB raw /12.18kB gzip. All routes are statically imported in main.tsx. Build generated a service-worker precache containing the application bundle. Source inspection: Gallery requests projects, then designs per project, then media per design in three dependent stages; waits for all results. Every fan-card uses an eager CSS background including opacity-zero cards. Project covers and Studies images already use native lazy loading. Design media tiles do not. StudioHub automatically starts segmentation for up to six photos on entry.

Recommended order:
1. Consolidated paginated Gallery endpoint returning eligible final/editorial media and associated display metadata; eliminate per-design media requests and display the first page promptly.
2. Gallery loads only focused/nearby card images; prefetch next neighbors. Ring should have a bounded thumbnail set, not scale its mounted items to the entire archive.
3. Route-level lazy imports for Wada/composer, public gallery and secondary screens; lazy-load image/editor/share dialogs when opened. Coordinate service-worker precaching so splitting does not just fetch all chunks again in the background.
4. Lazy-load below-fold Design media/timeline content. Preserve eager loading for the visible hero. Ensure small thumbnails, explicit dimensions and defer full originals until opening/downloading.
5. Revisit six-photo Studio segmentation prewarm: prioritize selected base, cap/defer remaining jobs. This is a behavior/cost change and should be measured separately.
6. Optional background prefetch on navigation intent; retain existing query caching.

Next production profiling should measure authenticated API TTFB, image transfer sizes/CDN cache behavior, LCP/INP/CLS on a representative phone/network, and actual Studio job timing. Local fixture timing does not establish those values.

Evidence: /tmp/atelier-perf/run.py (reproduction), results.json (24 observations), run.log. Temporary HTTP server/browser were closed. No application changes, backend calls, generated jobs, deployment, or production mutations.
