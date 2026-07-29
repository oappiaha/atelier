"""Thumbnail pipeline (T4): Celery task renders thumb/{ws}/{sha}/{w}.webp at
200/400/800 (TDD §3), thumb_key -> 400 variant, enqueued on /media/commit.

The task function is executed synchronously in-process here (direct call =
Celery's local execution path) against the ISOLATED test DB + bucket; the
live-worker path is verified out-of-band on the dev stack (T4 evidence)."""

import io
import os
import uuid

import httpx
from PIL import Image

from app.workers.thumbs import THUMB_WIDTHS, generate_thumbs, thumb_key_for
from tests.util import WS, sha256


def make_real_png(w: int = 1000, h: int = 750) -> bytes:
    """A real decodable PNG at a grid-relevant size (unique bytes per call)."""
    img = Image.new("RGB", (w, h), tuple(os.urandom(3)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _fetch(url: str) -> httpx.Response:
    async with httpx.AsyncClient() as raw:
        return await raw.get(url)


async def test_generate_thumbs_end_to_end(authed, upload_media):
    data = make_real_png(1000, 750)
    sha = sha256(data)
    m = await upload_media(data)
    assert m["thumb_url"] is None  # eventually consistent: not there at commit time

    status = generate_thumbs(m["id"])
    assert status == f"generated:{thumb_key_for(WS, sha, 400)}"

    # authoritative read path: the API now serves thumb_url
    r = await authed.get("/inbox")
    row = next(x for x in r.json() if x["id"] == m["id"])
    assert row["thumb_url"] is not None

    # thumb bytes are a real webp at the grid size, aspect preserved
    tr = await _fetch(row["thumb_url"])
    assert tr.status_code == 200
    thumb = Image.open(io.BytesIO(tr.content))
    assert thumb.format == "WEBP"
    assert (thumb.width, thumb.height) == (400, 300)

    # all three canonical variants exist in the ISOLATED bucket
    from app.storage import get_s3

    s3 = get_s3()
    for w, expected in ((200, 150), (400, 300), (800, 600)):
        obj = s3.get_object(Bucket=os.environ["S3_BUCKET"], Key=thumb_key_for(WS, sha, w))
        v = Image.open(io.BytesIO(obj["Body"].read()))
        assert (v.width, v.height) == (w, expected)

    # preservation: the original object is untouched
    head = s3.head_object(Bucket=os.environ["S3_BUCKET"], Key=f"src/{WS}/{sha}.png")
    assert head["ContentLength"] == len(data)


async def test_generate_thumbs_never_upscales(upload_media):
    data = make_real_png(120, 90)
    sha = sha256(data)
    m = await upload_media(data)
    assert generate_thumbs(m["id"]).startswith("generated:")

    from app.storage import get_s3

    for w in THUMB_WIDTHS:
        obj = get_s3().get_object(Bucket=os.environ["S3_BUCKET"], Key=thumb_key_for(WS, sha, w))
        v = Image.open(io.BytesIO(obj["Body"].read()))
        assert (v.width, v.height) == (120, 90)  # re-encoded, never upscaled


async def test_generate_thumbs_idempotent(upload_media):
    m = await upload_media(make_real_png(500, 500))
    first = generate_thumbs(m["id"])
    assert first.startswith("generated:")
    key = first.split(":", 1)[1]
    assert generate_thumbs(m["id"]) == f"already:{key}"


async def test_non_image_media_skipped(authed):
    """Negative: audio media passes through the task without error or thumb."""
    data = b"ID3\x03\x00" + os.urandom(64)  # fake-but-unique mp3 bytes
    digest = sha256(data)
    r = await authed.post(
        "/media/upload-url",
        json={"sha256": digest, "content_type": "audio/mpeg", "kind": "audio"},
    )
    assert r.status_code == 200, r.text
    u = r.json()
    async with httpx.AsyncClient() as raw:
        pr = await raw.put(u["upload_url"], content=data, headers={"Content-Type": "audio/mpeg"})
        assert pr.status_code == 200
    r = await authed.post(
        "/media/commit",
        json={"sha256": digest, "r2_key": u["r2_key"], "kind": "audio", "duration_ms": 1000},
    )
    assert r.status_code == 201, r.text
    m = r.json()

    assert generate_thumbs(m["id"]) == "skipped:kind=audio"
    r = await authed.get("/inbox")
    assert next(x for x in r.json() if x["id"] == m["id"])["thumb_url"] is None


async def test_generate_thumbs_unknown_media_noop():
    assert generate_thumbs(str(uuid.uuid4())) == "skipped:missing"


async def test_commit_enqueues_thumb_task(authed, upload_media, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(generate_thumbs, "delay", lambda media_id: calls.append(media_id))
    m = await upload_media(make_real_png(300, 200))
    assert calls == [m["id"]]


async def test_commit_survives_broker_down(authed, upload_media, monkeypatch):
    """Negative/preservation: thumbnails are eventually consistent — a dead
    broker (enqueue raising) must not fail the commit."""

    def boom(media_id: str):
        raise ConnectionError("redis broker unreachable")

    monkeypatch.setattr(generate_thumbs, "delay", boom)
    m = await upload_media(make_real_png(300, 200))  # asserts 201 internally
    assert m["thumb_url"] is None
