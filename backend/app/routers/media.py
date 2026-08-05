"""Media upload + the two hot read paths (timeline, media view) + inbox.

Upload is two-step (TDD §9): POST /media/upload-url returns a presigned PUT,
the client uploads bytes DIRECTLY to storage, then POST /media/commit registers
the object. sha256 is computed client-side and verified as the dedupe key.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx
from app.db import get_session
from app.routers.designs import PHASES
from app.storage import object_exists, presign_get, presign_put
from app.workers.thumbs import enqueue_thumbs

router = APIRouter(tags=["media"])

MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/webm": "webm",
    "audio/mpeg": "mp3",
}


class UploadUrlIn(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    kind: str = Field(pattern="^(image|audio)$")


class UploadUrlOut(BaseModel):
    upload_url: str | None
    r2_key: str
    duplicate_of: uuid.UUID | None = None


class CommitIn(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    r2_key: str
    kind: str = Field(pattern="^(image|audio)$")
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    caption: str | None = None
    source_url: str | None = None
    source_app: str | None = None
    entry_id: uuid.UUID | None = None  # None -> Inbox


class MediaOut(BaseModel):
    id: uuid.UUID
    design_id: uuid.UUID | None
    entry_id: uuid.UUID | None
    kind: str
    phase: str | None
    url: str
    thumb_url: str | None
    cutout_url: str | None = None  # PhotoRoom cutout (additive, WADA-CUT)
    width: int | None
    height: int | None
    duration_ms: int | None
    caption: str | None
    source_url: str | None
    source_app: str | None
    created_at: datetime


def _media_out(r) -> MediaOut:
    return MediaOut(
        id=r.id, design_id=r.design_id, entry_id=r.entry_id, kind=r.kind, phase=r.phase,
        url=presign_get(r.r2_key),
        thumb_url=presign_get(r.thumb_key) if r.thumb_key else None,
        cutout_url=presign_get(r.cutout_key) if r.cutout_key else None,
        width=r.width, height=r.height, duration_ms=r.duration_ms,
        caption=r.caption, source_url=r.source_url, source_app=r.source_app,
        created_at=r.created_at,
    )


@router.post("/media/upload-url", response_model=UploadUrlOut)
async def upload_url(
    body: UploadUrlIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> UploadUrlOut:
    ext = MIME_EXT.get(body.content_type)
    if ext is None:
        raise HTTPException(422, f"unsupported content type {body.content_type}")
    # dedupe by content hash (TDD §2.2 unique index)
    dup = (
        await session.execute(
            text("SELECT id, r2_key FROM media WHERE workspace_id = :ws AND sha256 = :sha"),
            {"ws": str(ctx.workspace_id), "sha": body.sha256},
        )
    ).one_or_none()
    if dup is not None:
        return UploadUrlOut(upload_url=None, r2_key=dup.r2_key, duplicate_of=dup.id)
    prefix = "audio" if body.kind == "audio" else "src"
    key = f"{prefix}/{ctx.workspace_id}/{body.sha256}.{ext}"
    return UploadUrlOut(upload_url=presign_put(key, body.content_type), r2_key=key)


@router.post("/media/commit", response_model=MediaOut, status_code=201)
async def commit(
    body: CommitIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> MediaOut:
    # the key is derived from (workspace, sha) at upload-url time; a commit whose
    # key doesn't embed the claimed sha would corrupt the dedupe index
    prefix = "audio" if body.kind == "audio" else "src"
    if not body.r2_key.startswith(f"{prefix}/{ctx.workspace_id}/{body.sha256}."):
        raise HTTPException(422, "r2_key does not match sha256/kind for this workspace")
    # commit registers an object — the bytes must actually be in storage
    if not object_exists(body.r2_key):
        raise HTTPException(409, "object not found in storage; upload it before committing")
    row = (
        await session.execute(
            text(
                """
                INSERT INTO media (workspace_id, entry_id, kind, r2_key, sha256,
                                   width, height, duration_ms, caption, source_url, source_app)
                VALUES (:ws, :entry_id, :kind, :r2_key, :sha, :w, :h, :dur, :cap, :src_url, :src_app)
                ON CONFLICT (workspace_id, sha256) DO UPDATE SET sha256 = EXCLUDED.sha256
                RETURNING *
                """
            ),
            {
                "ws": str(ctx.workspace_id),
                "entry_id": str(body.entry_id) if body.entry_id else None,
                "kind": body.kind,
                "r2_key": body.r2_key,
                "sha": body.sha256,
                "w": body.width, "h": body.height, "dur": body.duration_ms,
                "cap": body.caption, "src_url": body.source_url, "src_app": body.source_app,
            },
        )
    ).one()
    await session.commit()
    # thumbnails are generated out-of-band by the Celery resize worker (TDD §3);
    # best-effort enqueue — never blocks or fails the commit. Covers the dedupe
    # path too: an existing row that still lacks a thumb gets re-enqueued.
    if row.kind == "image" and row.thumb_key is None:
        enqueue_thumbs(str(row.id))
    return _media_out(row)


@router.get("/designs/{design_id}/media", response_model=list[MediaOut])
async def design_media(
    design_id: uuid.UUID,
    phase: str | None = None,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[MediaOut]:
    """The hot query (TDD §9). Filters on media.phase — no join through entries."""
    if phase is not None and phase not in PHASES:
        raise HTTPException(422, f"phase must be one of {PHASES}")
    rows = await session.execute(
        text(
            f"""
            SELECT * FROM media
            WHERE workspace_id = :ws AND design_id = :did
              {"AND phase = :phase" if phase else ""}
            ORDER BY created_at DESC, sort_index
            """
        ),
        {"ws": str(ctx.workspace_id), "did": str(design_id)}
        | ({"phase": phase} if phase else {}),
    )
    return [_media_out(r) for r in rows]


@router.get("/inbox", response_model=list[MediaOut])
async def inbox(
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[MediaOut]:
    rows = await session.execute(
        text(
            """
            SELECT * FROM media
            WHERE workspace_id = :ws AND design_id IS NULL
            ORDER BY created_at DESC
            """
        ),
        {"ws": str(ctx.workspace_id)},
    )
    return [_media_out(r) for r in rows]


class TriageIn(BaseModel):
    design_id: uuid.UUID
    phase: str


@router.post("/inbox/{media_id}/triage", response_model=MediaOut)
async def triage(
    media_id: uuid.UUID,
    body: TriageIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> MediaOut:
    """Assign an untriaged capture to a design + phase. Creates the entry."""
    if body.phase not in PHASES:
        raise HTTPException(422, f"phase must be one of {PHASES}")
    media_row = (
        await session.execute(
            text(
                """
                SELECT id, caption FROM media
                WHERE workspace_id = :ws AND id = :id AND design_id IS NULL
                """
            ),
            {"ws": str(ctx.workspace_id), "id": str(media_id)},
        )
    ).one_or_none()
    if media_row is None:
        raise HTTPException(404, "media not found in inbox")
    entry = (
        await session.execute(
            text(
                """
                INSERT INTO entries (workspace_id, design_id, phase, body)
                VALUES (:ws, :did, :phase, :body)
                RETURNING id
                """
            ),
            {
                "ws": str(ctx.workspace_id),
                "did": str(body.design_id),
                "phase": body.phase,
                "body": media_row.caption,
            },
        )
    ).one()
    row = (
        await session.execute(
            text("UPDATE media SET entry_id = :eid WHERE id = :id RETURNING *"),
            {"eid": str(entry.id), "id": str(media_id)},
        )
    ).one()
    await session.commit()
    return _media_out(row)


# ── Studio Shot (2026-08-04, Beezy): PRESENTATION derivative on demand ───────
# Background removed + AI soft shadow + white backdrop via PhotoRoom's edit
# API. Registered as real archive media on an editorial entry, so it can be
# pinned as cover, shared, exported. Distinct from the Wada pipeline cutout
# (clean alpha, no styling) — that one stays automatic and untouched.

class StudioShotOut(BaseModel):
    entry_id: uuid.UUID
    media: MediaOut
    created: bool  # False = these exact bytes were already in the archive


def _studio_shot_bytes(key: str) -> tuple[bytes, str, int, int]:
    """Original bytes → PhotoRoom studio edit → (png, sha, w, h). Sync: runs
    in the threadpool; PhotoRoom latency is seconds, bounded by its timeout."""
    import hashlib
    import io

    from PIL import Image

    from app.config import get_settings
    from app.storage import get_s3
    from app.workers.cutout import call_photoroom_studio

    s3 = get_s3()
    original = s3.get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"].read()
    png = call_photoroom_studio(original)
    sha = hashlib.sha256(png).hexdigest()
    with Image.open(io.BytesIO(png)) as img:
        width, height = img.size
    return png, sha, width, height


def _put_object(key: str, png: bytes) -> None:
    from app.config import get_settings
    from app.storage import get_s3

    get_s3().put_object(
        Bucket=get_settings().s3_bucket, Key=key, Body=png, ContentType="image/png",
    )


@router.post("/media/{media_id}/studio-shot", response_model=StudioShotOut, status_code=201)
async def studio_shot(
    media_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> StudioShotOut:
    import httpx as _httpx
    from starlette.concurrency import run_in_threadpool

    from app.config import get_settings

    if not get_settings().photoroom_api_key:
        raise HTTPException(503, "PhotoRoom is not configured")
    src = (
        await session.execute(
            text(
                "SELECT id, design_id, kind, r2_key FROM media "
                "WHERE id = :id AND workspace_id = :ws"
            ),
            {"id": str(media_id), "ws": str(ctx.workspace_id)},
        )
    ).one_or_none()
    if src is None:
        raise HTTPException(404, "media not found")
    if src.kind != "image":
        raise HTTPException(422, "studio shot needs an image")
    if src.design_id is None:
        raise HTTPException(422, "triage this photo to a design first")

    try:
        png, sha, width, height = await run_in_threadpool(_studio_shot_bytes, src.r2_key)
    except _httpx.HTTPError as e:
        raise HTTPException(502, f"PhotoRoom edit failed: {e}") from e

    ws = str(ctx.workspace_id)
    existing = (
        await session.execute(
            text("SELECT * FROM media WHERE workspace_id = :ws AND sha256 = :sha"),
            {"ws": ws, "sha": sha},
        )
    ).one_or_none()
    if existing is not None and existing.entry_id is not None:
        # identical bytes already live on an entry — the studio shot exists
        return StudioShotOut(entry_id=existing.entry_id, media=_media_out(existing), created=False)

    entry = (
        await session.execute(
            text(
                "INSERT INTO entries (workspace_id, design_id, phase, body) "
                "VALUES (:ws, :did, 'editorial', :body) RETURNING id"
            ),
            {"ws": ws, "did": str(src.design_id),
             "body": "Studio shot — background removed, soft shadow"},
        )
    ).one()
    if existing is not None:  # orphaned bytes — attach
        row = (
            await session.execute(
                text("UPDATE media SET entry_id = :eid WHERE id = :id RETURNING *"),
                {"eid": str(entry.id), "id": str(existing.id)},
            )
        ).one()
    else:
        media_key = f"src/{ws}/{sha}.png"
        await run_in_threadpool(_put_object, media_key, png)
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO media (workspace_id, entry_id, kind, r2_key, sha256,
                                       width, height, source_url, source_app)
                    VALUES (:ws, :eid, 'image', :key, :sha, :w, :h, :src_url, 'photoroom')
                    RETURNING *
                    """
                ),
                {"ws": ws, "eid": str(entry.id), "key": media_key, "sha": sha,
                 "w": width, "h": height, "src_url": f"media:{media_id}"},
            )
        ).one()
    await session.commit()
    if row.thumb_key is None:
        enqueue_thumbs(str(row.id))
    return StudioShotOut(entry_id=entry.id, media=_media_out(row), created=True)
