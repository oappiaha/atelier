# Context panel browser regression checks

These checks drive the actual React UI with Chromium. All `/api/**` calls are intercepted with isolated, stateful sample data; unknown calls fail. They verify frontend behavior and request scope, not backend integration or a physical phone's software keyboard.

Prerequisites: frontend dependencies, Python 3.9+ with `playwright`, and its Chromium browser. Python browser tooling is separate from the frontend production dependencies.

Start the app from `frontend`:

```sh
pnpm dev --host 127.0.0.1 --port 5178 --strictPort
```

Then run from the same directory:

```sh
python3 tests/browser/context_panel.py
python3 tests/browser/context_actions.py
python3 tests/browser/gallery_sharing.py
python3 tests/browser/busy_sheet.py
python3 tests/browser/navigation_edges.py
```

`ATELIER_TEST_URL` can select another isolated loopback port. `ATELIER_TEST_EVIDENCE` selects the screenshot/request-log directory (default `/tmp/atelier-context-panel-evidence`). Never point these tests at production. Browser service workers are blocked, contexts are fresh, and no real account credentials are used.

Coverage: contextual actions and navigation across seven screens at three viewport sizes; Gallery origin/filter/card/mode/scroll restoration; explicit multi-project sharing and scope reset; project-scoped creation; Inbox triage read-back; nested Studio capture context; empty/busy states; keyboard dismissal/focus; long labels; and signed-out routing. The suite reports page errors and unknown fixture requests.

Archive Grid/List regression: `python3 tests/browser/archive_browse.py` verifies search, no matches, sorting, project-specific state, Home and design return scroll, and responsive layouts with a larger fixture archive.

Collection header/wordmark regression: `python3 tests/browser/wordmark.py` covers compact headers, image conversion/preview/save/reload/remove, invalid input and retained More sharing at phone/desktop widths.

Performance checks (run against a production build, not Vite dev modules):

- `pnpm build`, then `python3 tests/browser/load_benchmark.py`: starts and stops its own local static server; records 24 runs with zero/150ms simulated API delay. Output defaults to /tmp/atelier-performance.json; override ATELIER_PERF_REPORT.
- `python3 tests/browser/gallery_loading.py`: use ATELIER_TEST_URL pointing to a local production preview. Checks one initial Gallery request, three images, full 100-item traversal, filters, sharing, route chunk loading, no Studio prewarm, and retry.
- `python3 tests/browser/pwa_cache.py`: starts its own local production server and verifies service-worker shell caching/offline reload without downloading unvisited Gallery/Studio code.

Fixtures now provide paginated /gallery responses. Fixture cursors are test-only offsets; real API cursors use phase/date/UUID keysets. Real API and database proof is backend/scripts/check_gallery.py against a disposable, migrated gallery_perf_test database. It seeds and cleans its own workspace rows, uses signed test tokens and real presigning, and makes no S3 requests.
