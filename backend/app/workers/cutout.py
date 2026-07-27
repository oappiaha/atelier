"""PhotoRoom background-removal cutout at `cutout/{ws}/{sha}.png`.

Product decision (PhotoRoom × Wada, 2026-07-26): every image entering Wada
Studio gets a background-removal cutout first — segmentation (and M7
generation) run on the cutout, not the original, which neutralizes the
busy-background segmentation risk by design. One PhotoRoom call per image,
EVER: cached by sha via `media.cutout_key` (migration 0002, mirrors
thumb_key), and the task re-checks the column before spending.

Same shape as thumbs.generate: best-effort, idempotent, canonical key derived
from (workspace, sha256). A PhotoRoom failure is a logged skip — never an
exception into the caller — so segmentation degrades to the original image
instead of blocking.

The cutout is coerced to the ORIGINAL pixel dimensions (post-EXIF-transpose).
This is load-bearing: region masks live in the 1536-long-edge working frame
derived from the original geometry, so the cutout must share it exactly.

Every PhotoRoom call is logged as "MODEL-CALL photoroom ..." — the ledger.
Sandbox keys (photoroom_sandbox=True) are free but watermark the output.
"""

import io
import logging
import time

import httpx
import psycopg
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.storage import get_s3
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

PHOTOROOM_URL = "https://sdk.photoroom.com/v1/segment"
PHOTOROOM_TIMEOUT = 60.0


def cutout_key_for(workspace_id: str, sha256: str) -> str:
    return f"cutout/{workspace_id}/{sha256}.png"


def _sync_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


def call_photoroom(image_bytes: bytes) -> bytes:
    """ONE background-removal call (the HTTP boundary tests mock). Returns
    transparent-background PNG bytes; raises httpx.HTTPError on failure."""
    settings = get_settings()
    key = settings.photoroom_api_key or ""
    if settings.photoroom_sandbox and not key.startswith("sandbox_"):
        key = f"sandbox_{key}"  # per PhotoRoom docs: sandbox_ prefix = free+watermark
    t0 = time.time()
    resp = httpx.post(
        PHOTOROOM_URL,
        headers={"x-api-key": key},
        files={"image_file": ("image", image_bytes)},
        data={"format": "png"},
        timeout=PHOTOROOM_TIMEOUT,
    )
    resp.raise_for_status()
    logger.info(
        "MODEL-CALL photoroom remove-bg sandbox=%s latency=%.2fs bytes_in=%d bytes_out=%d",
        settings.photoroom_sandbox, time.time() - t0, len(image_bytes), len(resp.content),
    )
    return resp.content


def _cutout_png(original: bytes) -> bytes:
    """PhotoRoom cutout as RGBA PNG at the original's (EXIF-corrected) pixel
    dimensions — the geometry the 1536 working frame is derived from."""
    src = ImageOps.exif_transpose(Image.open(io.BytesIO(original)))
    out = Image.open(io.BytesIO(call_photoroom(original)))
    if out.mode != "RGBA":
        out = out.convert("RGBA")
    if out.size != src.size:
        logger.info("cutout resized from %s back to original %s", out.size, src.size)
        out = out.resize(src.size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


@celery_app.task(name="wada.cutout")
def generate_cutout(media_id: str) -> str:
    """Produce the cutout for one media row and populate cutout_key.

    Status strings mirror thumbs.generate: "generated:<key>", "already:<key>",
    "skipped:missing", "skipped:kind=<kind>", "skipped:no-photoroom-key",
    "skipped:unreadable", "skipped:photoroom-error".
    """
    with psycopg.connect(_sync_dsn()) as conn:
        row = conn.execute(
            "SELECT workspace_id, kind, r2_key, sha256, cutout_key FROM media WHERE id = %s",
            (media_id,),
        ).fetchone()
        if row is None:
            return "skipped:missing"
        ws, kind, r2_key, sha, existing = row
        if kind != "image":
            return f"skipped:kind={kind}"
        if existing is not None:
            return f"already:{existing}"
        if not get_settings().photoroom_api_key:
            return "skipped:no-photoroom-key"

        s3 = get_s3()
        bucket = get_settings().s3_bucket
        original = s3.get_object(Bucket=bucket, Key=r2_key)["Body"].read()
        try:
            png = _cutout_png(original)
        except UnidentifiedImageError:
            logger.warning("media %s: bytes at %s are not a decodable image", media_id, r2_key)
            return "skipped:unreadable"
        except httpx.HTTPError:
            logger.warning("media %s: PhotoRoom call failed", media_id, exc_info=True)
            return "skipped:photoroom-error"

        key = cutout_key_for(str(ws), sha)
        s3.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
        conn.execute("UPDATE media SET cutout_key = %s WHERE id = %s", (key, media_id))
        conn.commit()
        return f"generated:{key}"


def ensure_cutout(media_id: str) -> str | None:
    """Synchronous ensure for the segmentation path (which already runs inside
    a Celery task). Returns the cutout_key, or None when no cutout can be
    produced — callers fall back to the original image. Never raises."""
    try:
        result = generate_cutout(media_id)
    except Exception:
        logger.warning("ensure_cutout failed for media %s", media_id, exc_info=True)
        return None
    if result.startswith(("generated:", "already:")):
        return result.split(":", 1)[1]
    logger.info("media %s: no cutout (%s), falling back to original", media_id, result)
    return None
