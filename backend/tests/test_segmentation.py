"""M5 segmentation pipeline: refinement unit tests on synthetic fixtures,
Gemini-response parsing/drop rules, and the §9 endpoint contracts
(POST /media/{id}/segment + GET /media/{id}/regions) with the model call
mocked at the boundary (app.wada.segmentation.call_gemini).

The worker task (app.workers.segment.segment_media) runs for real against the
isolated test DB/bucket — only the Gemini HTTP call is faked.
"""

import io
import json
import uuid

import numpy as np
import pytest
from PIL import Image, ImageDraw

from tests.util import sha256

# ── synthetic refinement fixture ─────────────────────────────────────────────
# A rounded shape in a distinct color on a noisy background, plus a COARSE
# polygon (few vertices, deliberately offset) playing the role of Gemini's
# sloppy mask. Ground truth is known exactly, so IoU improvement is provable.

W = H = 480


def _synthetic():
    rng = np.random.default_rng(7)
    img = rng.normal(210, 6, (H, W, 3)).clip(0, 255).astype(np.uint8)  # light bg
    truth_canvas = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(truth_canvas)
    d.rounded_rectangle([90, 120, 390, 380], radius=70, fill=255)
    truth = np.asarray(truth_canvas) > 127
    fg = rng.normal(0, 7, (H, W, 3)) + np.array([60, 110, 160])  # blue-ish leather
    img[truth] = fg[truth].clip(0, 255).astype(np.uint8)
    # coarse polygon: 10 vertices around the shape, offset by -18..+22px
    cx, cy = 240, 250
    pts = []
    for k in range(10):
        ang = 2 * np.pi * k / 10
        # ray-march to the truth boundary
        r = 0.0
        for rr in range(300):
            x, y = int(cx + rr * np.cos(ang)), int(cy + rr * np.sin(ang))
            if not (0 <= x < W and 0 <= y < H) or not truth[y, x]:
                r = rr
                break
        r += (-18 if k % 2 else 22)  # alternate under/overshoot
        pts.append((cx + r * np.cos(ang), cy + r * np.sin(ang)))
    coarse_canvas = Image.new("L", (W, H), 0)
    ImageDraw.Draw(coarse_canvas).polygon(pts, fill=255)
    coarse = np.asarray(coarse_canvas) > 127
    return img, truth, coarse


def test_refine_improves_iou_against_ground_truth():
    from app.wada import refine

    img, truth, coarse = _synthetic()
    iou_coarse = refine.iou(coarse, truth)
    refined, info = refine.refine_mask(img, coarse)
    iou_refined = refine.iou(refined, truth)
    assert iou_coarse < 0.93  # the fixture really is coarse
    assert not info.used_fallback
    assert iou_refined > iou_coarse + 0.03, (iou_coarse, iou_refined)
    assert iou_refined > 0.97, iou_refined  # near-pixel-accurate on a clean edge


def test_refine_is_deterministic():
    from app.wada import refine

    img, _, coarse = _synthetic()
    a, _ = refine.refine_mask(img, coarse)
    b, _ = refine.refine_mask(img, coarse)
    assert np.array_equal(a, b)


def test_refine_falls_back_to_coarse_when_result_diverges():
    """A polygon dropped onto pure background (nothing for the color models to
    latch onto in a flat field) must not hallucinate: the guard keeps coarse
    or returns something still anchored to the polygon."""
    from app.wada import refine

    img, _truth, coarse = _synthetic()
    refined, _info = refine.refine_mask(img, coarse)
    # whatever came back overlaps the polygon substantially — never a wild mask
    assert refine.iou(refined, coarse) >= refine.IOU_FALLBACK_MIN


def test_refine_empty_coarse_is_noop():
    from app.wada import refine

    img, _, _ = _synthetic()
    empty = np.zeros((H, W), bool)
    refined, info = refine.refine_mask(img, empty)
    assert info.used_fallback and not refined.any()


# ── parsing + drop rules (M0 contract: [y,x] polygons, 0–1000) ───────────────

RAW = json.dumps(
    [
        {  # kept
            "box_2d": [100, 100, 900, 900],
            "mask": [[100, 100], [100, 900], [900, 900], [900, 100]],
            "label": "body",
            "confidence": 0.99,
        },
        {  # dropped: confidence
            "box_2d": [0, 0, 500, 500],
            "mask": [[0, 0], [0, 500], [500, 500]],
            "label": "shadow",
            "confidence": 0.2,
        },
        {  # dropped: box area < 0.5%
            "box_2d": [0, 0, 10, 10],
            "mask": [[0, 0], [0, 10], [10, 10]],
            "label": "speck",
            "confidence": 0.9,
        },
        {  # unusable polygon -> not parsed at all
            "box_2d": [0, 0, 800, 800],
            "mask": [],
            "label": "no-poly",
            "confidence": 0.9,
        },
    ]
)


def test_parse_and_drop_rules():
    from app.wada import segmentation

    regions = segmentation.parse_regions("```json\n" + RAW + "\n```")
    assert [r.label for r in regions] == ["body", "shadow", "speck"]
    kept, dropped = segmentation.apply_drop_rules(regions)
    assert [r.label for r in kept] == ["body"]
    assert {r.label for r in dropped} == {"shadow", "speck"}


def test_polygon_is_y_first():
    """M0 finding: polygon vertices are [y, x] despite the prompt saying [x, y]."""
    from app.wada import segmentation

    # a polygon spanning the TOP HALF: y in 0..500, x in 0..1000
    mask = segmentation.polygon_to_mask(
        [[0, 0], [0, 1000], [500, 1000], [500, 0]], width=200, height=100
    )
    assert mask[: 45, :].mean() > 0.9  # top half filled
    assert mask[60:, :].mean() < 0.05  # bottom half empty


# ── endpoint contracts ───────────────────────────────────────────────────────


def _photo_png() -> bytes:
    """A real (non-degenerate) photo-ish PNG the whole pipeline can process.
    Unique bytes per call — sha256 dedupe would otherwise hand a second test
    the FIRST test's media row (which may already have regions)."""
    import os

    img, _, _ = _synthetic()
    img = img.copy()
    img[0, :3] = np.frombuffer(os.urandom(9), np.uint8).reshape(3, 3)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
async def audio_media(authed) -> dict:
    data = b"ID3fake-mp3-bytes" + uuid.uuid4().bytes
    digest = sha256(data)
    r = await authed.post(
        "/media/upload-url",
        json={"sha256": digest, "content_type": "audio/mpeg", "kind": "audio"},
    )
    assert r.status_code == 200, r.text
    u = r.json()
    import httpx

    async with httpx.AsyncClient() as raw:
        pr = await raw.put(u["upload_url"], content=data, headers={"Content-Type": "audio/mpeg"})
        assert pr.status_code == 200
    r = await authed.post(
        "/media/commit", json={"sha256": digest, "r2_key": u["r2_key"], "kind": "audio"}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_segment_requires_auth(client):
    r = await client.post(f"/media/{uuid.uuid4()}/segment")
    assert r.status_code == 401
    r = await client.get(f"/media/{uuid.uuid4()}/regions")
    assert r.status_code == 401


async def test_segment_unknown_media_404(authed):
    r = await authed.post(f"/media/{uuid.uuid4()}/segment")
    assert r.status_code == 404
    r = await authed.get(f"/media/{uuid.uuid4()}/regions")
    assert r.status_code == 404


async def test_segment_audio_422(authed, audio_media):
    r = await authed.post(f"/media/{audio_media['id']}/segment")
    assert r.status_code == 422
    assert "image" in r.json()["detail"]


async def test_segment_pipeline_end_to_end(authed, upload_media, monkeypatch):
    """202 pending -> worker runs (Gemini mocked at the boundary) -> regions
    persisted -> GET serves them -> re-POST returns cached with NO second
    model call -> worker re-run is a cache hit too."""
    from app.config import get_settings
    from app.wada import segmentation
    from app.workers import segment as segment_worker

    media = await upload_media(data=_photo_png())

    calls = {"n": 0}

    def fake_call_gemini(image, api_key, prompt=None):
        calls["n"] += 1
        return RAW, {
            "model": "gemini-3.5-flash", "latency_s": 0.0,
            "input_tokens": 1, "output_tokens": 1, "finish_reason": "STOP",
        }

    monkeypatch.setattr(segmentation, "call_gemini", fake_call_gemini)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")

    r = await authed.post(f"/media/{media['id']}/segment")
    assert r.status_code == 202
    assert r.json() == {"status": "pending", "regions": []}

    # regions are not there yet (the worker hasn't run)
    r = await authed.get(f"/media/{media['id']}/regions")
    assert r.status_code == 200 and r.json() == []

    # a second POST while pending stays pending and does NOT re-enqueue spend
    r = await authed.post(f"/media/{media['id']}/segment")
    assert r.status_code == 202

    # run the worker task in-process (what the celery wada-queue worker does)
    result = segment_worker.segment_media(media["id"])
    assert result == "segmented:1", result
    assert calls["n"] == 1

    # cached from now on: exactly one model call, EVER
    r = await authed.post(f"/media/{media['id']}/segment")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete" and len(body["regions"]) == 1
    region = body["regions"][0]
    assert region["label"] == "body"
    assert region["label_source"] == "gemini"
    assert region["confidence"] == pytest.approx(0.99)
    assert 0 < region["area_fraction"] < 1
    assert len(region["bbox"]) == 4 and len(region["mean_lab"]) == 3
    assert region["mask_key"].startswith("mask/")
    assert "http" in region["mask_url"]

    r = await authed.get(f"/media/{media['id']}/regions")
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [region["id"]]

    # the persisted mask object is a real binary PNG in storage
    from app.config import get_settings as gs
    from app.storage import get_s3

    png = get_s3().get_object(Bucket=gs().s3_bucket, Key=region["mask_key"])["Body"].read()
    mask_img = Image.open(io.BytesIO(png))
    assert mask_img.mode == "L"
    assert set(np.unique(np.asarray(mask_img))) <= {0, 255}

    # worker-level idempotency: a duplicate task is a cache hit, no model call
    result = segment_worker.segment_media(media["id"])
    assert result.startswith("already:")
    assert calls["n"] == 1


async def test_segment_without_key_skips_cleanly(authed, upload_media, monkeypatch):
    """No GEMINI_API_KEY -> the task no-ops with a logged skip, no crash."""
    from app.config import get_settings
    from app.workers import segment as segment_worker

    media = await upload_media(data=_photo_png())
    monkeypatch.setattr(get_settings(), "gemini_api_key", None)
    assert segment_worker.segment_media(media["id"]) == "skipped:no-gemini-key"


# ── SHIP-1 hardening: JSON-parse retry (re-request on malformed output) ──────

_META = {
    "model": "gemini-3.5-flash", "latency_s": 0.0,
    "input_tokens": 1, "output_tokens": 1, "finish_reason": "STOP",
}


async def test_segment_json_parse_retry_recovers(authed, upload_media, monkeypatch):
    """A malformed first response (the prod transient) is re-requested — the
    second, valid response completes the pipeline. ≤2 retries, each a real
    model call."""
    from app.config import get_settings
    from app.wada import segmentation
    from app.workers import segment as segment_worker

    media = await upload_media(data=_photo_png())
    # attempt 1: truncated mid-array (json.JSONDecodeError); attempt 2: valid
    responses = ['[{"box_2d": [100, 100, 900', RAW]
    calls = {"n": 0}

    def fake_call_gemini(image, api_key, prompt=None):
        calls["n"] += 1
        return responses[min(calls["n"] - 1, len(responses) - 1)], _META

    monkeypatch.setattr(segmentation, "call_gemini", fake_call_gemini)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")

    assert (await authed.post(f"/media/{media['id']}/segment")).status_code == 202
    result = segment_worker.segment_media(media["id"])
    assert result == "segmented:1", result
    assert calls["n"] == 2  # exactly one retry

    r = await authed.get(f"/media/{media['id']}/regions")
    assert r.status_code == 200 and len(r.json()) == 1


async def test_segment_json_parse_gives_up_after_two_retries(
    authed, upload_media, monkeypatch
):
    """Persistently malformed output fails LOUDLY after 3 attempts (1 + 2
    retries): no regions persisted, in-flight lock released so a later run
    can succeed."""
    import os

    import redis as redis_sync

    from app.config import get_settings
    from app.wada import segmentation
    from app.workers import segment as segment_worker

    media = await upload_media(data=_photo_png())
    calls = {"n": 0}

    def fake_call_gemini(image, api_key, prompt=None):
        calls["n"] += 1
        return "no json array here at all", _META  # ValueError path

    monkeypatch.setattr(segmentation, "call_gemini", fake_call_gemini)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")

    assert (await authed.post(f"/media/{media['id']}/segment")).status_code == 202
    with pytest.raises(ValueError, match="after 3 attempts"):
        segment_worker.segment_media(media["id"])
    assert calls["n"] == 3

    # nothing persisted, and the in-flight lock is gone (retryable)
    r = await authed.get(f"/media/{media['id']}/regions")
    assert r.status_code == 200 and r.json() == []
    rds = redis_sync.from_url(os.environ["REDIS_URL"])
    assert not rds.exists(segment_worker.seg_lock_key(media["id"]))
    rds.close()

    # the transient clears -> the same media segments fine on the next run
    def fixed_call_gemini(image, api_key, prompt=None):
        calls["n"] += 1
        return RAW, _META

    monkeypatch.setattr(segmentation, "call_gemini", fixed_call_gemini)
    assert segment_worker.segment_media(media["id"]) == "segmented:1"


# ── seg_prompt v3: category-aware part vocabulary (2026-08-06) ───────────────

def test_seg_prompt_matches_category_vocab():
    from app.wada.segmentation import SEG_VOCAB, seg_prompt

    shoes = seg_prompt("Shoes")  # designs.category is title-cased free text
    assert "sole, upper, toe cap" in shoes
    assert "never repeat a label" in shoes
    dresses = seg_prompt("dresses")
    assert "bodice" in dresses and "neckline" in dresses
    # unknown / missing category → the cross-catalogue fallback examples
    for cat in (None, "", "spaceships"):
        p = seg_prompt(cat)
        assert "Examples — bags:" in p
    # distinct vocabularies produce distinct prompts (denim/pants share one)
    assert len({seg_prompt(c) for c in SEG_VOCAB}) == len(set(SEG_VOCAB.values()))
