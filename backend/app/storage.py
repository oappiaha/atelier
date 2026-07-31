"""S3-compatible object storage: Cloudflare R2 in prod, MinIO locally.

Key layout per TDD §3 (src/, proc/, thumb/, mask/, node/, cw/, export/, audio/).
"""

import boto3
from botocore.client import Config

from app.config import get_settings


def get_s3():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def ensure_bucket() -> None:
    s = get_settings()
    s3 = get_s3()
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if s.s3_bucket not in existing:
        s3.create_bucket(Bucket=s.s3_bucket)


def object_exists(key: str) -> bool:
    """True if the object is present in the bucket (used by POST /media/commit)."""
    from botocore.exceptions import ClientError

    try:
        get_s3().head_object(Bucket=get_settings().s3_bucket, Key=key)
        return True
    except ClientError:
        return False


def presign_put(key: str, content_type: str, expires: int = 900) -> str:
    """Presigned PUT for direct client upload (POST /media/upload-url)."""
    return get_s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


# Read URLs are minted per response, so a fresh signature every call means a
# fresh URL every refetch — the browser can never cache an image, and a URL
# held by a long-lived page dies at expiry (the "images vanish after an hour"
# bug). Sign for the SigV4 maximum (7 days) and memoize per key for most of
# that window: the same URL comes back across requests, browser caching works,
# and only a tab left open for ~6 days straight can watch a URL expire.
_GET_URL_TTL = 7 * 24 * 3600  # SigV4 hard maximum
_GET_URL_REUSE = 6 * 24 * 3600
_get_url_cache: dict[str, tuple[str, float]] = {}


def presign_get(key: str, expires: int = _GET_URL_TTL) -> str:
    import time

    now = time.monotonic()
    bucket = get_settings().s3_bucket
    cache_key = f"{bucket}/{key}"
    hit = _get_url_cache.get(cache_key) if expires == _GET_URL_TTL else None
    if hit and hit[1] > now:
        return hit[0]
    url = get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )
    if expires == _GET_URL_TTL:
        if len(_get_url_cache) > 8192:  # unbounded-growth backstop
            _get_url_cache.clear()
        _get_url_cache[cache_key] = (url, now + _GET_URL_REUSE)
    return url


def presign_download(key: str, filename: str, expires: int = 3600) -> str:
    """Presigned GET that forces a browser download with a friendly filename
    (2K exports — POST /colorways/{id}/export)."""
    return get_s3().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": get_settings().s3_bucket,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=expires,
    )


def copy_object(src_key: str, dst_key: str) -> None:
    """Server-side object copy within the bucket (pin: cw/ → cw/pinned/)."""
    s = get_settings()
    get_s3().copy_object(
        Bucket=s.s3_bucket,
        Key=dst_key,
        CopySource={"Bucket": s.s3_bucket, "Key": src_key},
    )
