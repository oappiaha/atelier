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
