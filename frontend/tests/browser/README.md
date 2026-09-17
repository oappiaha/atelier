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
