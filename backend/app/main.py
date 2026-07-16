from contextlib import asynccontextmanager

from asyncpg.exceptions import ForeignKeyViolationError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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

# A FK violation on a write means the referenced row doesn't exist — a client
# error (404), not a server bug (500). asyncpg surfaces it as
# IntegrityError -> adapted dbapi error -> ForeignKeyViolationError. Constraints
# follow PG default naming {table}_{column}_fkey, except the cover_media_id FKs
# which the 0001 schema names {table}_cover_fk (they reference media).
_FK_SUFFIX_ENTITY = {
    "_project_id_fkey": "project",
    "_design_id_fkey": "design",
    "_entry_id_fkey": "entry",
    "_media_id_fkey": "media",
    "_cover_fk": "media",
}


@app.exception_handler(IntegrityError)
async def fk_violation_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    cause = getattr(exc.orig, "__cause__", None)
    if not isinstance(cause, ForeignKeyViolationError):
        raise exc  # not a FK problem — let it surface as a 500
    constraint = cause.constraint_name or ""
    entity = next(
        (name for suffix, name in _FK_SUFFIX_ENTITY.items() if constraint.endswith(suffix)),
        "referenced resource",
    )
    return JSONResponse(status_code=404, content={"detail": f"{entity} not found"})


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
