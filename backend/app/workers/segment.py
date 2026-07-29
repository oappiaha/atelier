"""Wada segmentation worker (TDD §8.1 SEGMENT + M5 mask refinement).

The worker is the only thing that talks to Gemini (TDD §1.2). One model call
per image, EVER: the API route checks the regions cache before enqueueing, a
redis in-flight lock stops duplicate enqueues, and the task itself re-checks
under a pg advisory lock before spending — three layers, because the spend is
external and unrollbackable.

Task route: queue "wada" (celery_app.conf.task_routes) so a thumbs-only worker
never picks up model-calling work and vice versa.

Per kept region the task persists:
- a refined binary mask PNG at  mask/{ws}/{media_sha}/{idx}-{label-slug}.png
  (working frame = 1536px long edge, the generation frame), plus the raw
  Gemini response at mask/{ws}/{media_sha}/segmentation-raw.json for debugging,
- a `regions` row: label, confidence, mask_key/sha, area_fraction (pixel-true,
  from the refined mask), bbox [x0,y0,x1,y1] in mask space, mean_lab of the
  pixels under the mask.

Every model call is logged as "MODEL-CALL ..." — that line is the call ledger.
"""

import hashlib
import io
import json
import logging

import numpy as np
import psycopg
from PIL import Image

from app.config import get_settings
from app.storage import get_s3
from app.wada import refine, segmentation
from app.workers import cutout
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

SEG_LOCK_TTL = 180  # seconds; covers model latency + refinement comfortably

# SHIP-1 hardening: Gemini occasionally emits malformed/truncated JSON (a
# transient — observed once on prod, deploy/redeploy-m7m8-evidence.md). A
# parse failure is re-requested up to this many times; each retry is a real,
# separately-ledgered model call.
SEG_PARSE_RETRIES = 2


def seg_lock_key(media_id: str) -> str:
    return f"seg:inflight:{media_id}"


def _sync_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


def mask_key_for(workspace_id: str, media_sha: str, idx: int, label: str) -> str:
    return f"mask/{workspace_id}/{media_sha}/{idx}-{segmentation.slugify(label)}.png"


def _mask_png(mask: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


@celery_app.task(name="wada.segment")
def segment_media(media_id: str) -> str:
    """Segment one image media row -> refined `regions` rows. Idempotent."""
    settings = get_settings()
    try:
        return _segment(media_id, settings)
    finally:
        # release the API's in-flight marker so a failed run can be retried
        # before the TTL expires
        try:
            import redis as redis_sync

            r = redis_sync.from_url(settings.redis_url)
            r.delete(seg_lock_key(media_id))
            r.close()
        except Exception:
            logger.warning("could not clear segment lock for %s", media_id, exc_info=True)


def _segment(media_id: str, settings) -> str:
    with psycopg.connect(_sync_dsn()) as conn:
        row = conn.execute(
            "SELECT workspace_id, kind, r2_key, sha256 FROM media WHERE id = %s",
            (media_id,),
        ).fetchone()
        if row is None:
            return "skipped:missing"
        ws, kind, r2_key, sha = row
        if kind != "image":
            return f"skipped:kind={kind}"

        # cache check #2 (the route already checked once, pre-enqueue)
        (n_existing,) = conn.execute(
            "SELECT COUNT(*) FROM regions WHERE media_id = %s", (media_id,)
        ).fetchone()
        if n_existing:
            return f"already:{n_existing}"

    if not settings.gemini_api_key:
        return "skipped:no-gemini-key"

    s3 = get_s3()
    bucket = settings.s3_bucket

    # Product decision (PhotoRoom × Wada, 2026-07-26): segmentation runs on
    # the background-removal CUTOUT, not the original — this neutralizes the
    # busy-background risk by design. ensure_cutout is synchronous here (we
    # are already inside a Celery task) and best-effort: a PhotoRoom failure
    # degrades to the original image instead of blocking segmentation.
    # Existing regions are never touched — the cache checks above make this
    # a new-segmentations-only behavior change.
    cutout_key = cutout.ensure_cutout(media_id)
    source_key = cutout_key or r2_key
    source = s3.get_object(Bucket=bucket, Key=source_key)["Body"].read()
    img, alpha = segmentation.working_image_and_alpha(source)
    w, h = img.size
    logger.info("media=%s segmenting from %s (alpha=%s)", media_id, source_key,
                alpha is not None)

    regions = None
    last_err: ValueError | None = None
    for attempt in range(1 + SEG_PARSE_RETRIES):
        raw_text, meta = segmentation.call_gemini(img, settings.gemini_api_key)
        logger.info(
            "MODEL-CALL gemini segmentation media=%s model=%s latency=%.1fs "
            "tokens_in=%s tokens_out=%s finish=%s attempt=%d",
            media_id, meta["model"], meta["latency_s"],
            meta["input_tokens"], meta["output_tokens"], meta["finish_reason"],
            attempt + 1,
        )
        try:  # json.JSONDecodeError is a ValueError subclass
            regions = segmentation.parse_regions(raw_text)
            break
        except ValueError as e:
            last_err = e
            logger.warning(
                "media=%s segmentation JSON parse failed (attempt %d/%d): %s",
                media_id, attempt + 1, 1 + SEG_PARSE_RETRIES, e,
            )
    if regions is None:
        raise ValueError(
            f"segmentation response unparseable after {1 + SEG_PARSE_RETRIES} "
            f"attempts: {last_err}"
        )
    kept, dropped = segmentation.apply_drop_rules(regions)
    logger.info(
        "media=%s regions: %d returned, %d kept, %d dropped (conf<%.2f or area<%.3f)",
        media_id, len(regions), len(kept), len(dropped),
        segmentation.CONF_MIN, segmentation.AREA_FRAC_MIN,
    )

    # keep the raw response next to the masks — free debuggability, and the
    # coarse polygons remain reconstructable after refinement
    s3.put_object(
        Bucket=bucket,
        Key=f"mask/{ws}/{sha}/segmentation-raw.json",
        Body=json.dumps({"model": meta, "text": raw_text}).encode(),
        ContentType="application/json",
    )

    image_arr = np.asarray(img)
    lab = refine.srgb_to_lab(image_arr)
    prepared = []
    for idx, region in enumerate(kept):
        coarse = segmentation.polygon_to_mask(region.polygon, w, h)
        refined, info = refine.refine_mask(image_arr, coarse)
        if alpha is not None:
            # exploit the cutout's alpha: PhotoRoom already decided what is
            # background, so no region pixel may fall outside the product.
            # (GrabCut seeding itself is left unchanged — the intersection is
            # the provable win; reseeding showed no measurable gain on the
            # near-clean fixtures and risks perturbing locked behavior.)
            refined &= alpha
        if not refined.any():
            logger.warning("media=%s region %d (%s): empty mask, skipped", media_id, idx,
                           region.label)
            continue
        png = _mask_png(refined)
        key = mask_key_for(str(ws), sha, idx, region.label)
        s3.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
        prepared.append(
            {
                "label": region.label,
                "confidence": region.confidence,
                "mask_key": key,
                "mask_sha256": hashlib.sha256(png).hexdigest(),
                "area_fraction": float(refined.sum() / refined.size),
                "bbox": refine.mask_bbox(refined),
                "mean_lab": [round(float(v), 3) for v in lab[refined].mean(axis=0)],
            }
        )
        logger.info(
            "media=%s region %d '%s' conf=%.2f refined: iou_vs_coarse=%.3f fallback=%s "
            "area=%.4f",
            media_id, idx, region.label, region.confidence,
            info.iou_vs_coarse, info.used_fallback, prepared[-1]["area_fraction"],
        )

    with psycopg.connect(_sync_dsn()) as conn:
        # advisory lock: two racing tasks can't both insert (spend is already
        # gone by now, but the cache must stay unique per image)
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (media_id,))
        (n_existing,) = conn.execute(
            "SELECT COUNT(*) FROM regions WHERE media_id = %s", (media_id,)
        ).fetchone()
        if n_existing:
            return f"already:{n_existing}"
        for p in prepared:
            conn.execute(
                """
                INSERT INTO regions (workspace_id, media_id, label, confidence,
                                     mask_key, mask_sha256, area_fraction, bbox, mean_lab)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ws, media_id, p["label"], p["confidence"], p["mask_key"],
                    p["mask_sha256"], p["area_fraction"], p["bbox"], p["mean_lab"],
                ),
            )
        conn.commit()
    return f"segmented:{len(prepared)}"
