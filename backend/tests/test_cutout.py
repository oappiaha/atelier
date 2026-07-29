"""WADA-CUT: PhotoRoom cutout preprocessing (product decision 2026-07-26).

Covers: migration 0002 presence, the wada.cutout task (skip/idempotency,
HTTP-boundary mock, failure -> never blocks), the segmentation wiring
(cutout ensured BEFORE Gemini, Gemini receives cutout-derived pixels, alpha
constrains persisted masks, PhotoRoom failure degrades to the original), and
cutout_url in media responses. No test makes a network call to PhotoRoom:
conftest blanks PHOTOROOM_API_KEY and these tests mock either httpx.post
(the HTTP boundary) or cutout.call_photoroom (the module boundary).
"""

import io
import json
import os
import uuid

import httpx
import numpy as np
import psycopg
import pytest
from PIL import Image

from app.config import get_settings
from app.workers.cutout import cutout_key_for, ensure_cutout, generate_cutout
from tests.util import WS, sha256

pytestmark = pytest.mark.usefixtures("isolated_infra")


def _real_png(w: int, h: int, rgb=(40, 80, 200)) -> bytes:
    """A real decodable PNG; a few random pixels keep the sha unique."""
    img = Image.new("RGB", (w, h), rgb)
    px = img.load()
    for i, b in enumerate(os.urandom(6)):
        px[i, 0] = (b, b, b)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rgba_png(w: int, h: int, *, split: bool = False) -> bytes:
    """RGBA PNG: fully opaque green, or (split=True) left half transparent."""
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 1] = 200  # green
    arr[..., 3] = 255
    if split:
        arr[:, : w // 2, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _fake_response(status: int, content: bytes = b"") -> httpx.Response:
    return httpx.Response(
        status, content=content,
        request=httpx.Request("POST", "https://sdk.photoroom.com/v1/segment"),
    )


# ── migration 0002 ───────────────────────────────────────────────────────────


def test_migration_adds_nullable_cutout_key_column():
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
            WHERE table_name = 'media' AND column_name = 'cutout_key'
            """
        ).fetchone()
    assert row == ("text", "YES")


# ── wada.cutout task, HTTP boundary mocked ───────────────────────────────────


async def test_generate_cutout_end_to_end(authed, upload_media, monkeypatch):
    data = _real_png(640, 480)
    sha = sha256(data)
    m = await upload_media(data)
    assert m["cutout_url"] is None  # additive field, null until the task runs

    monkeypatch.setattr(get_settings(), "photoroom_api_key", "test-key")
    calls: list[dict] = []

    def fake_post(url, *, headers, files, data, timeout):
        calls.append({"url": url, "headers": headers, "data": data})
        # deliberately WRONG dimensions: the task must coerce back to the
        # original geometry (masks live in the frame derived from it)
        return _fake_response(200, _rgba_png(320, 240))

    monkeypatch.setattr("app.workers.cutout.httpx.post", fake_post)

    key = cutout_key_for(WS, sha)
    assert generate_cutout(m["id"]) == f"generated:{key}"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://sdk.photoroom.com/v1/segment"
    assert calls[0]["headers"]["x-api-key"] == "sandbox_test-key"  # sandbox default
    assert calls[0]["data"] == {"format": "png"}

    # stored object: RGBA PNG at the ORIGINAL dimensions
    from app.storage import get_s3

    png = get_s3().get_object(Bucket=os.environ["S3_BUCKET"], Key=key)["Body"].read()
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.mode == "RGBA" and img.size == (640, 480)

    # authoritative read path: the API serves a fetchable cutout_url
    r = await authed.get("/inbox")
    row = next(x for x in r.json() if x["id"] == m["id"])
    assert row["cutout_url"] is not None
    async with httpx.AsyncClient() as raw:
        cr = await raw.get(row["cutout_url"])
    assert cr.status_code == 200
    assert Image.open(io.BytesIO(cr.content)).mode == "RGBA"

    # idempotent: second run (task or ensure helper) never re-calls PhotoRoom
    assert generate_cutout(m["id"]) == f"already:{key}"
    assert ensure_cutout(m["id"]) == key
    assert len(calls) == 1


async def test_generate_cutout_skips(authed, upload_media, monkeypatch):
    def boom(*a, **k):  # any HTTP call in this test is a bug
        raise AssertionError("PhotoRoom must not be called")

    monkeypatch.setattr("app.workers.cutout.httpx.post", boom)

    assert generate_cutout(str(uuid.uuid4())) == "skipped:missing"

    # no key configured (conftest blanks it) -> clean skip, cutout stays null
    m = await upload_media(_real_png(64, 48))
    assert generate_cutout(m["id"]) == "skipped:no-photoroom-key"
    assert ensure_cutout(m["id"]) is None

    # audio media -> kind skip
    data = b"ID3cutout-audio" + uuid.uuid4().bytes
    digest = sha256(data)
    r = await authed.post(
        "/media/upload-url",
        json={"sha256": digest, "content_type": "audio/mpeg", "kind": "audio"},
    )
    u = r.json()
    async with httpx.AsyncClient() as raw:
        pr = await raw.put(u["upload_url"], content=data, headers={"Content-Type": "audio/mpeg"})
        assert pr.status_code == 200
    r = await authed.post(
        "/media/commit", json={"sha256": digest, "r2_key": u["r2_key"], "kind": "audio"}
    )
    assert r.status_code == 201, r.text
    assert generate_cutout(r.json()["id"]) == "skipped:kind=audio"


async def test_photoroom_failure_never_blocks(authed, upload_media, monkeypatch):
    """Negative: an HTTP failure is a logged skip — cutout_key stays null and
    ensure_cutout hands callers the fallback signal instead of raising."""
    m = await upload_media(_real_png(64, 48))
    monkeypatch.setattr(get_settings(), "photoroom_api_key", "test-key")
    monkeypatch.setattr(
        "app.workers.cutout.httpx.post", lambda *a, **k: _fake_response(402, b"payment")
    )
    assert generate_cutout(m["id"]) == "skipped:photoroom-error"
    assert ensure_cutout(m["id"]) is None
    r = await authed.get("/inbox")
    assert next(x for x in r.json() if x["id"] == m["id"])["cutout_url"] is None


async def test_commit_does_not_enqueue_cutout(upload_media, monkeypatch):
    """Preservation: cutouts run when an image enters Wada Studio (segment
    path) — plain uploads must not spend PhotoRoom credits."""
    calls: list[str] = []
    monkeypatch.setattr(generate_cutout, "delay", lambda mid: calls.append(mid))
    await upload_media(_real_png(64, 48))
    assert calls == []


# ── segmentation wiring: cutout BEFORE Gemini, on cutout pixels ──────────────

RAW_FULL = json.dumps(
    [
        {
            "box_2d": [0, 0, 1000, 1000],
            "mask": [[0, 0], [0, 1000], [1000, 1000], [1000, 0]],
            "label": "body",
            "confidence": 0.99,
        }
    ]
)

GEMINI_META = {
    "model": "gemini-3.5-flash", "latency_s": 0.0,
    "input_tokens": 1, "output_tokens": 1, "finish_reason": "STOP",
}


async def test_segment_runs_on_cutout(authed, upload_media, monkeypatch):
    """Order + payload proof: PhotoRoom first, then Gemini receives the
    flattened CUTOUT pixels (not the blue original), and the persisted mask
    is constrained by the cutout's alpha (left half transparent -> empty)."""
    from app.wada import segmentation
    from app.workers import cutout as cutout_worker
    from app.workers import segment as segment_worker

    m = await upload_media(_real_png(480, 360, rgb=(40, 80, 200)))  # blue original
    events: list[str] = []
    seen: dict = {}

    def fake_photoroom(image_bytes: bytes) -> bytes:
        events.append("photoroom")
        return _rgba_png(480, 360, split=True)  # left transparent, right green

    def fake_gemini(image, api_key):
        events.append("gemini")
        seen["left"] = image.getpixel((120, 180))
        seen["right"] = image.getpixel((360, 180))
        return RAW_FULL, GEMINI_META

    monkeypatch.setattr(get_settings(), "photoroom_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(cutout_worker, "call_photoroom", fake_photoroom)
    monkeypatch.setattr(segmentation, "call_gemini", fake_gemini)

    assert segment_worker.segment_media(m["id"]) == "segmented:1"
    assert events == ["photoroom", "gemini"]
    # Gemini saw the cutout: transparent left flattened to white, green right
    assert seen["left"] == (255, 255, 255)
    assert seen["right"] == (0, 200, 0)

    # persisted mask: nothing outside the cutout's alpha
    r = await authed.get(f"/media/{m['id']}/regions")
    (region,) = r.json()
    from app.storage import get_s3

    png = get_s3().get_object(Bucket=os.environ["S3_BUCKET"], Key=region["mask_key"])
    mask = np.asarray(Image.open(io.BytesIO(png["Body"].read()))) > 127
    assert not mask[:, :240].any()  # transparent half excluded
    assert mask[:, 240:].mean() > 0.9  # opaque half kept
    assert region["area_fraction"] == pytest.approx(0.5, abs=0.05)

    # cutout_key landed and is served
    r = await authed.get("/inbox")
    assert next(x for x in r.json() if x["id"] == m["id"])["cutout_url"] is not None

    # idempotency: re-run is a cache hit — no second PhotoRoom or Gemini call
    assert segment_worker.segment_media(m["id"]).startswith("already:")
    assert events == ["photoroom", "gemini"]


async def test_segment_falls_back_to_original_when_photoroom_fails(
    authed, upload_media, monkeypatch
):
    """Negative: PhotoRoom down -> segmentation still completes, on the
    ORIGINAL pixels, and no cutout_key is recorded."""
    from app.wada import segmentation
    from app.workers import cutout as cutout_worker
    from app.workers import segment as segment_worker

    m = await upload_media(_real_png(480, 360, rgb=(40, 80, 200)))
    seen: dict = {}

    def fake_photoroom(image_bytes: bytes) -> bytes:
        raise httpx.ConnectError("photoroom unreachable")

    def fake_gemini(image, api_key):
        seen["center"] = image.getpixel((240, 180))
        return RAW_FULL, GEMINI_META

    monkeypatch.setattr(get_settings(), "photoroom_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(cutout_worker, "call_photoroom", fake_photoroom)
    monkeypatch.setattr(segmentation, "call_gemini", fake_gemini)

    assert segment_worker.segment_media(m["id"]) == "segmented:1"
    assert seen["center"] == (40, 80, 200)  # the blue original reached Gemini
    r = await authed.get("/inbox")
    assert next(x for x in r.json() if x["id"] == m["id"])["cutout_url"] is None
