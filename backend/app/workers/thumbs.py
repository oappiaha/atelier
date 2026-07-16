"""Thumbnail generation (TDD §3): `thumb/{ws}/{sha}/{w}.webp` at widths
200/400/800, produced by the Celery resize worker ("R2 + resize worker
(thumbnails)", TDD §1.1). Eventually consistent: the API enqueues on
POST /media/commit and never blocks on generation; until the task lands,
`thumb_key` stays NULL and clients fall back to the full-size URL.

The single `media.thumb_key` column points at the 400px variant — the design
grid's working size. The 200/800 variants are still written at their canonical
keys for future srcset use.

Idempotent by content hash (TDD §1.2): a row that already has a thumb_key is
skipped, and keys are derived from (workspace, sha256), so a retry costs a
re-render of the same objects at the same keys.
"""

import io
import logging

import psycopg
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings
from app.storage import get_s3
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

THUMB_WIDTHS = (200, 400, 800)
GRID_WIDTH = 400  # which variant media.thumb_key points at
WEBP_QUALITY = 80


def thumb_key_for(workspace_id: str, sha256: str, width: int) -> str:
    return f"thumb/{workspace_id}/{sha256}/{width}.webp"


def _sync_dsn() -> str:
    """The app's DATABASE_URL is asyncpg-flavoured; psycopg wants plain."""
    return get_settings().database_url.replace("+asyncpg", "")


def _render_thumbs(original: bytes, workspace_id: str, sha256: str) -> str:
    """Render + upload all THUMB_WIDTHS variants; return the grid thumb key.

    Never upscales: an original narrower than the target width is re-encoded
    at its native size so every canonical key exists.
    """
    img = Image.open(io.BytesIO(original))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "transparency" in img.info or "A" in img.mode else "RGB")

    s3 = get_s3()
    bucket = get_settings().s3_bucket
    for w in THUMB_WIDTHS:
        variant = img.copy()
        if variant.width > w:
            variant = variant.resize(
                (w, max(1, round(variant.height * w / variant.width))),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        variant.save(buf, format="WEBP", quality=WEBP_QUALITY, method=4)
        s3.put_object(
            Bucket=bucket,
            Key=thumb_key_for(workspace_id, sha256, w),
            Body=buf.getvalue(),
            ContentType="image/webp",
        )
    return thumb_key_for(workspace_id, sha256, GRID_WIDTH)


@celery_app.task(name="thumbs.generate")
def generate_thumbs(media_id: str) -> str:
    """Generate thumbnails for one media row and populate thumb_key.

    Returns a short status string (useful in worker logs / tests):
    "generated:<key>", "already:<key>", "skipped:kind=<kind>",
    "skipped:missing", "skipped:unreadable".
    """
    with psycopg.connect(_sync_dsn()) as conn:
        row = conn.execute(
            "SELECT workspace_id, kind, r2_key, sha256, thumb_key FROM media WHERE id = %s",
            (media_id,),
        ).fetchone()
        if row is None:
            return "skipped:missing"
        ws, kind, r2_key, sha, existing = row
        if kind != "image":
            return f"skipped:kind={kind}"
        if existing is not None:
            return f"already:{existing}"

        s3 = get_s3()
        original = s3.get_object(Bucket=get_settings().s3_bucket, Key=r2_key)["Body"].read()
        try:
            key = _render_thumbs(original, str(ws), sha)
        except UnidentifiedImageError:
            logger.warning("media %s: bytes at %s are not a decodable image", media_id, r2_key)
            return "skipped:unreadable"

        conn.execute("UPDATE media SET thumb_key = %s WHERE id = %s", (key, media_id))
        conn.commit()
        return f"generated:{key}"


def enqueue_thumbs(media_id: str) -> None:
    """Best-effort enqueue from the API. Thumbnails are eventually consistent —
    a dead broker must never fail the commit (contract negative case)."""
    try:
        generate_thumbs.delay(media_id)
    except Exception:
        logger.warning("could not enqueue thumbnail task for media %s", media_id, exc_info=True)
