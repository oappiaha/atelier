"""Colorway actions (TDD §9, PRD W8/W11 — M8).

POST /colorways/{id}/pin      Pin. Physically COPIES the object from
                              cw/{ws}/{id}.png to cw/{ws}/pinned/{id}.png
                              (§3: pinned objects never expire; a status flag
                              alone would let the 30-day lifecycle delete the
                              one thing the user said they cared about).
                              Idempotent: pinning a pinned colorway is a 200.
POST /colorways/{id}/unpin    Reverse (additive — §9 defines pin + reject but
                              no unpin; a pin the user cannot take back is a
                              trap, documented deviation). Copies the pinned
                              object back to the working key (the original may
                              have been lifecycle-evicted), deletes the pinned
                              copy, status → ready. Idempotent on ready.
POST /colorways/{id}/export   2K PNG (PRD W11 + Beezy 2026-07-18 decision):
                              default = FREE local Pillow upscale of the
                              stored colorway to a 2048 long edge (LANCZOS,
                              lossless PNG); {"regenerate": true} = ONE fresh
                              Seedream call at 2048 (~$0.135) through the same
                              fal boundary the executor uses. Key per §3:
                              export/{ws}/{colorway_id}.png (7d lifecycle,
                              regenerable from the colorway; a re-export
                              overwrites — the export is a derivative).

Documented decisions (the TDD/§9 rows are one-liners):
- Pin also copies the .400.webp thumb next to the pinned PNG so the contact
  sheet keeps rendering after the working thumb expires (same M7-T2
  deviation that put cw thumbs under cw/ in the first place).
- The paid re-generation is ledgered in spend_ledger (kind='export',
  §2's third kind) at 14¢ = round_half_up($0.135) and gated by all three
  §8.11 tiers; it does NOT touch studies.actual_cost_cents — that
  column reconciles generation spend against the estimate the user approved,
  and an export is a separate purchase.
- Both export paths run synchronously (threadpool): the upscale is ~1s; the
  regeneration is one model call (~40s) — acceptable for an explicit,
  confirmed purchase, and it keeps §9's route surface (no polling routes
  §9 doesn't define).
"""

import io
import uuid

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth import Ctx, get_ctx
from app.config import get_settings
from app.db import get_session
from app.routers.generation import _budget_gate
from app.storage import copy_object, get_s3, presign_download, presign_get

router = APIRouter(tags=["colorways"])

EXPORT_LONG_EDGE = 2048
# Seedream at 2048 ≈ $0.135/edit (M0 pricing, PROGRESS "Wada 2K export"
# decision). Ledgered as integer cents, round half up.
EXPORT_REGEN_CENTS = 14

EXPORT_REGEN_PROMPT = (
    "Reproduce this exact product photograph at higher resolution.\n"
    "Change nothing: identical colours, materials, texture, stitching, "
    "lighting, shadows, framing and background.\n"
    "Photorealistic product photography. No stylisation."
)


class PinOut(BaseModel):
    id: uuid.UUID
    status: str
    image_key: str
    thumb_key: str | None
    image_url: str
    thumb_url: str | None


class ExportIn(BaseModel):
    regenerate: bool = False


class ExportOut(BaseModel):
    colorway_id: uuid.UUID
    method: str  # 'upscale' (free) | 'regenerate' (paid)
    width: int
    height: int
    key: str
    download_url: str
    cost_cents: int


async def _colorway_or_404(colorway_id: uuid.UUID, ctx: Ctx, session: AsyncSession):
    row = (
        await session.execute(
            text(
                "SELECT * FROM colorways WHERE workspace_id = :ws AND id = :id"
            ),
            {"ws": str(ctx.workspace_id), "id": str(colorway_id)},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "colorway not found")
    return row


def _pin_out(row) -> PinOut:
    return PinOut(
        id=row.id, status=row.status, image_key=row.image_key,
        thumb_key=row.thumb_key, image_url=presign_get(row.image_key),
        thumb_url=presign_get(row.thumb_key) if row.thumb_key else None,
    )


async def _set_keys(
    session: AsyncSession, cw_id, status: str, image_key: str, thumb_key: str | None
):
    return (
        await session.execute(
            text(
                "UPDATE colorways SET status = :st, image_key = :ik, "
                "thumb_key = :tk WHERE id = :id RETURNING *"
            ),
            {"st": status, "ik": image_key, "tk": thumb_key, "id": str(cw_id)},
        )
    ).one()


@router.post("/colorways/{colorway_id}/pin", response_model=PinOut)
async def pin_colorway(
    colorway_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> PinOut:
    cw = await _colorway_or_404(colorway_id, ctx, session)
    if cw.status == "pinned":
        return _pin_out(cw)  # idempotent
    if cw.status != "ready" or not cw.image_key:
        raise HTTPException(409, f"only a ready colorway can be pinned (this one is {cw.status})")

    ws = str(ctx.workspace_id)
    pinned_key = f"cw/{ws}/pinned/{cw.id}.png"  # §3, never expires
    pinned_thumb = f"cw/{ws}/pinned/{cw.id}.400.webp" if cw.thumb_key else None
    await run_in_threadpool(copy_object, cw.image_key, pinned_key)
    if pinned_thumb:
        await run_in_threadpool(copy_object, cw.thumb_key, pinned_thumb)

    row = await _set_keys(session, cw.id, "pinned", pinned_key, pinned_thumb)
    await session.commit()
    return _pin_out(row)


@router.post("/colorways/{colorway_id}/unpin", response_model=PinOut)
async def unpin_colorway(
    colorway_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> PinOut:
    cw = await _colorway_or_404(colorway_id, ctx, session)
    if cw.status == "ready":
        return _pin_out(cw)  # idempotent
    if cw.status != "pinned":
        raise HTTPException(409, f"colorway is not pinned (it is {cw.status})")

    ws = str(ctx.workspace_id)
    work_key = f"cw/{ws}/{cw.id}.png"
    work_thumb = f"cw/{ws}/{cw.id}.400.webp" if cw.thumb_key else None

    def restore() -> None:
        # the working object may have been lifecycle-evicted while pinned —
        # copy back first, THEN delete the protected copy
        s3, bucket = get_s3(), get_settings().s3_bucket
        copy_object(cw.image_key, work_key)
        if work_thumb:
            copy_object(cw.thumb_key, work_thumb)
        s3.delete_object(Bucket=bucket, Key=cw.image_key)
        if cw.thumb_key:
            s3.delete_object(Bucket=bucket, Key=cw.thumb_key)

    await run_in_threadpool(restore)
    row = await _set_keys(session, cw.id, "ready", work_key, work_thumb)
    await session.commit()
    return _pin_out(row)


def _upscale_export(image_key: str, export_key: str) -> tuple[int, int]:
    """FREE path: LANCZOS upscale of the stored colorway to a 2048 long edge,
    lossless PNG (PNG has no quality knob to lose)."""
    s3, bucket = get_s3(), get_settings().s3_bucket
    src = s3.get_object(Bucket=bucket, Key=image_key)["Body"].read()
    img = Image.open(io.BytesIO(src)).convert("RGB")
    scale = EXPORT_LONG_EDGE / max(img.size)
    size = (round(img.width * scale), round(img.height * scale))
    out = img.resize(size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    s3.put_object(Bucket=bucket, Key=export_key, Body=buf.getvalue(),
                  ContentType="image/png")
    return size


def _regen_export(image_key: str, export_key: str) -> tuple[int, int]:
    """PAID path: ONE fresh Seedream call at 2048 through the executor's fal
    boundary (same endpoint/retries the trie executor uses). The finished
    colorway is the input; the mask is full-frame (nothing is locked — the
    ask is a faithful re-render, not an edit)."""
    import numpy as np

    from app.workers import generate as W

    s3, bucket = get_s3(), get_settings().s3_bucket
    src = s3.get_object(Bucket=bucket, Key=image_key)["Body"].read()
    img = Image.open(io.BytesIO(src)).convert("RGB")
    scale = EXPORT_LONG_EDGE / max(img.size)
    frame = (round(img.width * scale), round(img.height * scale))
    mask = Image.fromarray(
        np.full((img.height, img.width), 255, np.uint8), "L"
    )
    try:
        out, _latency = W.call_seedream(img, mask, EXPORT_REGEN_PROMPT, frame)
    except W.NodeFailed as e:
        raise HTTPException(502, f"2K re-generation failed: {e}") from e
    if W.G.unusable(out):
        raise HTTPException(502, "2K re-generation returned an unusable frame")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    s3.put_object(Bucket=bucket, Key=export_key, Body=buf.getvalue(),
                  ContentType="image/png")
    return frame


@router.post("/colorways/{colorway_id}/export", response_model=ExportOut)
async def export_colorway(
    colorway_id: uuid.UUID,
    body: ExportIn | None = None,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> ExportOut:
    body = body or ExportIn()
    cw = await _colorway_or_404(colorway_id, ctx, session)
    if cw.status not in ("ready", "pinned") or not cw.image_key:
        raise HTTPException(
            409, f"only a generated colorway can be exported (this one is {cw.status})"
        )
    ws = str(ctx.workspace_id)
    export_key = f"export/{ws}/{cw.id}.png"  # §3: 7d, regenerable

    if body.regenerate:
        study_actual = (
            await session.execute(
                text("SELECT actual_cost_cents FROM studies WHERE id = :id"),
                {"id": str(cw.study_id)},
            )
        ).scalar_one()
        await _budget_gate(session, ws, study_actual, EXPORT_REGEN_CENTS)
        width, height = await run_in_threadpool(_regen_export, cw.image_key, export_key)
        from app.wada.generation import MODEL_ID

        await session.execute(
            text(
                "INSERT INTO spend_ledger (workspace_id, study_id, kind, "
                "model_id, cost_cents, cache_hit) "
                "VALUES (:ws, :study_id, 'export', :model, :cents, false)"
            ),
            {"ws": ws, "study_id": str(cw.study_id), "model": MODEL_ID,
             "cents": EXPORT_REGEN_CENTS},
        )
        await session.commit()
        method, cost = "regenerate", EXPORT_REGEN_CENTS
    else:
        width, height = await run_in_threadpool(_upscale_export, cw.image_key, export_key)
        method, cost = "upscale", 0

    return ExportOut(
        colorway_id=cw.id, method=method, width=width, height=height,
        key=export_key,
        download_url=presign_download(export_key, f"colorway-{cw.id}-2k.png"),
        cost_cents=cost,
    )
