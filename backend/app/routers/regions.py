"""Wada segmentation surface (TDD §9):

POST /media/{id}/segment  — run detection. Idempotent: returns cached regions
                            if they exist; otherwise enqueues the wada worker
                            task and returns 202 {status: "pending"}.
GET  /media/{id}/regions  — regions with confidence (+ presigned mask URLs).

Execution model (documented decision): the model call runs in the Celery
worker on the dedicated "wada" queue — TDD §1.2 makes the worker the only
thing that talks to model providers, and the 10–13s Gemini latency (M0) plus
refinement doesn't belong in a request handler. The route is a cache read +
enqueue; clients poll (re-POST or GET) until regions exist. A redis in-flight
lock (TTL 180s) keeps duplicate POSTs from double-spending.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx, get_redis
from app.db import get_session
from app.storage import presign_get
from app.workers.segment import SEG_LOCK_TTL, seg_lock_key, segment_media

router = APIRouter(tags=["regions"])


class RegionOut(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    label: str
    label_source: str
    confidence: float
    mask_url: str
    mask_key: str
    area_fraction: float
    bbox: list[int]  # [x0, y0, x1, y1] in mask (1536-long-edge working frame) pixels
    mean_lab: list[float]
    is_cosmetic: bool
    created_at: datetime


class SegmentOut(BaseModel):
    status: str  # "complete" | "pending"
    regions: list[RegionOut] = []


def _region_out(r) -> RegionOut:
    return RegionOut(
        id=r.id, media_id=r.media_id, label=r.label, label_source=r.label_source,
        confidence=r.confidence, mask_url=presign_get(r.mask_key), mask_key=r.mask_key,
        area_fraction=r.area_fraction, bbox=list(r.bbox), mean_lab=list(r.mean_lab),
        is_cosmetic=r.is_cosmetic, created_at=r.created_at,
    )


async def _media_or_error(media_id: uuid.UUID, ctx: Ctx, session: AsyncSession):
    row = (
        await session.execute(
            text("SELECT id, kind FROM media WHERE workspace_id = :ws AND id = :id"),
            {"ws": str(ctx.workspace_id), "id": str(media_id)},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "media not found")
    return row


async def _regions_for(media_id: uuid.UUID, ctx: Ctx, session: AsyncSession) -> list[RegionOut]:
    rows = await session.execute(
        text(
            """
            SELECT * FROM regions
            WHERE workspace_id = :ws AND media_id = :id
            ORDER BY area_fraction DESC, created_at
            """
        ),
        {"ws": str(ctx.workspace_id), "id": str(media_id)},
    )
    return [_region_out(r) for r in rows]


@router.post("/media/{media_id}/segment", response_model=SegmentOut, status_code=200)
async def segment(
    media_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
):
    from fastapi.responses import JSONResponse

    media = await _media_or_error(media_id, ctx, session)
    if media.kind != "image":
        raise HTTPException(422, f"segmentation runs on images, not {media.kind}")

    cached = await _regions_for(media_id, ctx, session)
    if cached:  # TDD §9: idempotent — cached regions, no model call, ever again
        return SegmentOut(status="complete", regions=cached)

    pending = SegmentOut(status="pending").model_dump(mode="json")
    # in-flight lock: first POST enqueues, concurrent/repeat POSTs just poll
    acquired = await get_redis().set(seg_lock_key(str(media_id)), "1", nx=True, ex=SEG_LOCK_TTL)
    if acquired:
        segment_media.delay(str(media_id))
    return JSONResponse(status_code=202, content=pending)


@router.get("/media/{media_id}/regions", response_model=list[RegionOut])
async def regions(
    media_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[RegionOut]:
    await _media_or_error(media_id, ctx, session)
    return await _regions_for(media_id, ctx, session)
