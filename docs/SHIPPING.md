# Shipping — Atelier

## Environment hazards
- Production is https://wada.garden, historically served from /opt/atelier on a remote droplet. Never use production data or secrets for UI verification.
- Do not invoke production endpoints, generation, segmentation or share-link minting during browser tests. Intercept every /api request with isolated fixtures; block service workers.
- Bind local verification servers to 127.0.0.1. Do not stop processes belonging to other sessions.
- Changes in this round are local frontend work. Deployment and pushes are separate operations; no production authorization is inferred from a successful local test.

## Port registry
- Documented development dependencies: API 8000, Postgres 5433, Redis 6380, MinIO 9000/9001. Production API binding: 8010 on the remote host.
- This round reserves frontend 5178 (check availability before launch). Use --strictPort; never reclaim a busy port.

## Verification recipes
- Python Playwright is installed on this Mac. Run through sys_os_shell when native browser/network execution is unavailable.
- Start Vite in frontend: pnpm dev --host 127.0.0.1 --port 5178 --strictPort.
- Use isolated browser contexts with service_workers='block', a fixture-only localStorage token and /api/** route fixtures. No backend mutation or production auth is needed for UI-only tests.
- Baseline audit fixture: /tmp/atelier-panel-audit/explore.py. Evidence for this round: /tmp/deez/atelier-context-panel.
- Verify navigation/actions by clicking the actual UI; include phone widths, short viewport, desktop and a fresh browser context. Assert no unintended share requests, disabled/empty behavior and no console failures.

## Test & build commands
From frontend:
- pnpm install --frozen-lockfile
- pnpm lint
- pnpm build
CI runs the same frontend checks. Backend CI uses Python 3.13, ruff check app tests, and python -m pytest -q with isolated Postgres/Redis/MinIO. Backend checks are required for backend changes.

## Git & migration conventions
- Preserve unrelated local changes. Stage explicit paths only.
- Root owns integration and commits. No migration is needed for the contextual panel.
- For this round serialize repo-writing implementation and verification; no shared concurrent builds. Provisioning for parallel worktrees has not been established.

## Deploy runbook
- Existing configuration: docker-compose.prod.yml and deploy/nginx-wada.garden.conf.
- Production compose command in the config: docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build. API startup applies migrations.
- Current frontend publication procedure and deployment access are unverified. Establish them before an authorized deployment; never guess or copy credentials.

## Post-deploy log
PROGRESS.md records delivery rounds and follow-ups. Clearly distinguish locally verified changes from production deployment.

## Context-panel regression suite
The reusable browser checks now live in frontend/tests/browser. See its README for prerequisites and the five commands. ATELIER_TEST_URL only permits loopback hosts; ATELIER_TEST_EVIDENCE selects the artifact directory. The suite covers 21 screen/viewport combinations plus action/state/keyboard/busy regressions. Run lint/build before it. No production or backend access is required.

## Authorized release procedure (verified 2026-09-26)

SSH access is `root@137.184.211.55`; production `/opt/atelier` is a copied source tree, not a Git checkout. Compare its source before updating. Nginx serves `/var/www/wada.garden` and proxies `/api/` to loopback8010. Production env stays on the server. User explicitly authorized this release's push and deployment.

Before deployment, archive backend/compose and frontend under a private `/opt/atelier/releases/<release>` directory, save a `pg_dump -Fc`, and tag the running API image for rollback. Build the candidate from a Git archive in the release directory; run full CI before activating. API compose image is `atelier-api:latest`. For API-only changes, tag the verified candidate then run `docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --no-deps --no-build api`. Startup applies migrations (this release adds nullable projects.wordmark in0008). Preserve the worker and in-flight jobs.

Check `/health`, authorization rejection and read-only API behavior before publishing frontend. A short-lived smoke credential can be created and consumed entirely inside the API container; never output it or put production credentials into browser fixtures. No production mutation/generation/email/share calls are needed. Publish assets first without deleting old hashes, then atomically replace index.html and sw.js. Smoke the public shell/redirect, API health/auth, and exact asset hashes.

Rollback: restore the archived frontend; restore backend/compose and retag the saved API image as `atelier-api:latest`, then recreate only the API with `--no-build`. Leave additive0008 in place; do not restore the database over newer user writes. Backups are a recovery aid, not permission to discard live data.

CI MinIO Docker Hub and Quay images proved unavailable during this round. The workflow now builds upstream `RELEASE.2025-10-15T17-29-55Z` using Go1.24.8, binds the temporary S3 service to127.0.0.1:9000, and requires its health check before backend pytest.
