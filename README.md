# Atelier

A design archive, and the Wada colorway studio inside it.

The build contract is `atelier_PRD_v2.docx` + `atelier_TDD_v1.docx`. The visual
and interaction spec is `atelier_light_v3 (3).html` — its token block and
tactile key layer are lifted directly into `frontend/src/styles/`.

## Stack

- **frontend/** — React 19 + Vite PWA. TanStack Query (server state), Zustand
  (studio state), plain CSS with custom properties. No Tailwind.
- **backend/** — FastAPI (async), Postgres 16, Celery + Redis, S3-compatible
  storage (Cloudflare R2 in prod, MinIO locally).

## Local development

```bash
# 1. infra: Postgres :5433, Redis :6380, MinIO :9000 (console :9001)
docker compose up -d

# 2. backend
cd backend
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000

# 3. frontend (proxies /api -> :8000)
cd frontend
pnpm install
pnpm dev
```

Sign-in is by magic link. With no `RESEND_API_KEY` set, the link is printed to
the API log — copy it into the browser.

## Milestones (TDD §11)

M0 bake-off → M1 foundation → M2 archive core → M3 gallery/share →
M4 PWA/inbox → **ship V1** → M5–M8 Wada Studio → **ship V1.5**.
