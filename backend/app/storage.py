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


def presign_put(key: str, content_type: str, expires: int = 900) -> str:
    """Presigned PUT for direct client upload (POST /media/upload-url)."""
    return get_s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presign_get(key: str, expires: int = 3600) -> str:
    return get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=expires,
    )
