"""Colorway actions (TDD §9, PRD W8/W11 — M8).

POST /colorways/{id}/pin      Pin. Physically COPIES the object from
                              cw/{ws}/{id}.png to cw/{ws}/pinned/{id}.png
                              (§3: pinned objects never expire; a status flag
                              alone would let the 30-day lifecycle delete the
                              one thing the user said they cared about).
                              Idempotent: pinning a pinned colorway is a 200.
                              Pin → timeline bridge (Beezy 2026-07): a pin also
                              registers the image as archive media and writes a
                              phase='study' entry on the design, so the pick
                              shows up in the design's development timeline.
                              Idempotent by content hash across pin/unpin/pin;
                              unpin leaves the entry alone — it's history.
POST /colorways/{id}/unpin    Reverse (additive — §9 defines pin + reject but
                              no unpin; a pin the user cannot take back is a
                              trap, documented deviation). Copies the pinned
                              object back to the working key (the original may
                              have been lifecycle-evicted), deletes the pinned
                              copy, status → ready. Idempotent on ready.
POST /colorways/{id}/reject   §9's reject: hide a colorway from the working
                              sheet (status → rejected). Only planned/ready
                              can be rejected; a PINNED colorway is 422 —
                              pin and reject are contradictory intents, so
                              the user must unpin first (SHIP-1 decision).
                              Nothing is deleted: the image/thumb objects and
                              spend records stay (a rejected colorway is
                              still a spend record). Idempotent on rejected.
POST /colorways/{id}/unreject Reverse (additive, same deviation logic as
                              unpin: §9 defines reject but no way back, and
                              a hide the user cannot undo is a trap). Status
                              returns to ready when an image exists, else to
                              planned (a rejected ghost). Idempotent on
                              ready/planned.
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

import hashlib
import io
import json
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
from app.workers.thumbs import enqueue_thumbs

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


# ── pin → timeline bridge ────────────────────────────────────────────────────
# A pin is a design decision, so it also lands on the design's development
# timeline: the pinned PNG is registered as archive media (source_app='wada',
# source_url = the study route) attached to a phase='study' entry. The media
# bytes live at the canonical src/{ws}/{sha}.png key — NOT at cw/pinned/ —
# because unpin deletes the pinned copy and the timeline is history: the entry
# (and its image) must survive an unpin. Idempotent by content hash: the
# media unique index (workspace_id, sha256) makes pin/unpin/pin find the
# first pin's row and stop before creating a second entry.


def _fetch_pin_bytes(image_key: str) -> tuple[bytes, str, int, int]:
    """Download the just-pinned PNG; return (bytes, sha256, width, height)."""
    s3, bucket = get_s3(), get_settings().s3_bucket
    png = s3.get_object(Bucket=bucket, Key=image_key)["Body"].read()
    sha = hashlib.sha256(png).hexdigest()
    with Image.open(io.BytesIO(png)) as img:
        width, height = img.size
    return png, sha, width, height


def _put_media_object(key: str, png: bytes) -> None:
    s3, bucket = get_s3(), get_settings().s3_bucket
    s3.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")


async def _pin_timeline_entry(cw, pinned_key: str, ctx: Ctx, session: AsyncSession) -> None:
    """Create the timeline entry + media for a freshly pinned colorway.

    Runs inside the pin's transaction (committed by the caller). Only the
    thumbnail enqueue is best-effort — the entry itself is part of the pin."""
    png, sha, width, height = await run_in_threadpool(_fetch_pin_bytes, pinned_key)
    ws = str(ctx.workspace_id)

    existing = (
        await session.execute(
            text("SELECT id, entry_id, thumb_key FROM media WHERE workspace_id = :ws AND sha256 = :sha"),
            {"ws": ws, "sha": sha},
        )
    ).one_or_none()
    if existing is not None and existing.entry_id is not None:
        # re-pin after unpin (or the bytes already live on an entry) — done;
        # still chase a missing thumb, mirroring /media/commit's dedupe path
        if existing.thumb_key is None:
            enqueue_thumbs(str(existing.id))
        return

    info = (
        await session.execute(
            text(
                """
                SELECT s.id AS study_id, s.design_id, pal.name AS palette_name,
                       p.idx AS perm_idx, p.mapping
                FROM studies s
                JOIN palettes pal ON pal.id = s.palette_id
                JOIN permutations p ON p.study_id = s.id
                WHERE s.id = :study_id AND p.id = :perm_id
                """
            ),
            {"study_id": str(cw.study_id), "perm_id": str(cw.permutation_id)},
        )
    ).one()

    # "Wada colorway #N — {palette} ({slot: colour, …})" — #N is the contact
    # sheet's permutation idx, fingerprint in canonical slot order
    mapping = info.mapping if isinstance(info.mapping, dict) else json.loads(info.mapping)
    mapping = {int(k): v for k, v in mapping.items()}
    labels = {
        r.idx: r.label
        for r in await session.execute(
            text("SELECT idx, label FROM slots WHERE study_id = :id"),
            {"id": str(cw.study_id)},
        )
    }
    names = {
        r.id: r.name
        for r in await session.execute(
            text("SELECT id, name FROM sanzo_colors WHERE id = ANY(CAST(:ids AS int[]))"),
            {"ids": list(mapping.values())},
        )
    }
    fingerprint = ", ".join(
        f"{labels.get(idx, f'slot {idx}')}: {names.get(color_id, color_id)}"
        for idx, color_id in sorted(mapping.items())
    )
    body = f"Wada colorway #{info.perm_idx} — {info.palette_name} ({fingerprint})"

    entry = (
        await session.execute(
            text(
                """
                INSERT INTO entries (workspace_id, design_id, phase, body, study_id)
                VALUES (:ws, :did, 'study', :body, :study_id)
                RETURNING id
                """
            ),
            {"ws": ws, "did": str(info.design_id), "body": body,
             "study_id": str(cw.study_id)},
        )
    ).one()

    media_key = f"src/{ws}/{sha}.png"  # canonical media key — survives unpin
    if existing is None:
        await run_in_threadpool(_put_media_object, media_key, png)
        media_row = (
            await session.execute(
                text(
                    """
                    INSERT INTO media (workspace_id, entry_id, kind, r2_key, sha256,
                                       width, height, source_url, source_app)
                    VALUES (:ws, :eid, 'image', :key, :sha, :w, :h, :src_url, 'wada')
                    RETURNING id
                    """
                ),
                {"ws": ws, "eid": str(entry.id), "key": media_key, "sha": sha,
                 "w": width, "h": height,
                 "src_url": f"/d/{info.design_id}/study/{cw.study_id}"},
            )
        ).one()
        media_id = media_row.id
    else:  # sha known but orphaned — attach it (trigger denormalises phase)
        await session.execute(
            text("UPDATE media SET entry_id = :eid WHERE id = :id"),
            {"eid": str(entry.id), "id": str(existing.id)},
        )
        media_id = existing.id

    # thumbnails are eventually consistent — a dead broker never fails the pin
    enqueue_thumbs(str(media_id))


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
    await _pin_timeline_entry(cw, pinned_key, ctx, session)
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


class RejectOut(BaseModel):
    """Reject/unreject echo — image keys are optional (a planned ghost has
    none), unlike PinOut whose contract requires a stored object."""

    id: uuid.UUID
    status: str
    image_url: str | None
    thumb_url: str | None


def _reject_out(row) -> RejectOut:
    return RejectOut(
        id=row.id, status=row.status,
        image_url=presign_get(row.image_key) if row.image_key else None,
        thumb_url=presign_get(row.thumb_key) if row.thumb_key else None,
    )


@router.post("/colorways/{colorway_id}/reject", response_model=RejectOut)
async def reject_colorway(
    colorway_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> RejectOut:
    cw = await _colorway_or_404(colorway_id, ctx, session)
    if cw.status == "rejected":
        return _reject_out(cw)  # idempotent
    if cw.status == "pinned":
        raise HTTPException(
            422, "a pinned colorway cannot be rejected — unpin it first"
        )
    if cw.status not in ("planned", "ready"):
        raise HTTPException(
            409, f"only a planned or ready colorway can be rejected (this one is {cw.status})"
        )
    row = (
        await session.execute(
            text("UPDATE colorways SET status = 'rejected' WHERE id = :id RETURNING *"),
            {"id": str(cw.id)},
        )
    ).one()
    await session.commit()
    return _reject_out(row)


@router.post("/colorways/{colorway_id}/unreject", response_model=RejectOut)
async def unreject_colorway(
    colorway_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> RejectOut:
    cw = await _colorway_or_404(colorway_id, ctx, session)
    if cw.status in ("ready", "planned"):
        return _reject_out(cw)  # idempotent
    if cw.status != "rejected":
        raise HTTPException(409, f"colorway is not rejected (it is {cw.status})")
    # a generated reject returns to ready; a rejected ghost returns to planned
    restored = "ready" if cw.image_key else "planned"
    row = (
        await session.execute(
            text("UPDATE colorways SET status = :st WHERE id = :id RETURNING *"),
            {"st": restored, "id": str(cw.id)},
        )
    ).one()
    await session.commit()
    return _reject_out(row)


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
