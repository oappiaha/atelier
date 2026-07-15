from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import auth
from app.config import get_settings
from app.db import engine
from app.routers import designs, entries, media, projects


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn)
    try:
        from app.storage import ensure_bucket

        ensure_bucket()
    except Exception as e:  # storage being down shouldn't kill the API
        print(f"storage unavailable at startup: {e}", flush=True)
    yield
    await engine.dispose()


app = FastAPI(title="Atelier", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().app_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(designs.router)
app.include_router(entries.router)
app.include_router(media.router)


@app.get("/health")
async def health() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"ok": True}
