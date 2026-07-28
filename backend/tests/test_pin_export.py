"""M8: pin/unpin (cw/pinned/ lifecycle protection), 2K export (free upscale +
paid re-generation), and the anchored-estimate fix.

Model boundary mocked exactly like test_generation (fal fixture); pin/export
storage effects asserted against the real isolated MinIO bucket.
"""

import io
import os
import uuid

from PIL import Image

from tests.test_generation import (  # noqa: F401 — fixtures
    _db,
    _run_enqueued,
    _study,
    fal,
    gen_setup,
)
from tests.test_studies import (  # noqa: F401 — fixtures
    THREE_SLOTS,
    _create_study,
    _slot,
    sanzo,
)


def _s3():
    from app.storage import get_s3

    return get_s3(), os.environ["S3_BUCKET"]


def _exists(key: str) -> bool:
    from app.storage import object_exists

    return object_exists(key)


async def _ready_colorways(authed, setup, fal, policy=None) -> tuple[dict, list[dict]]:  # noqa: F811
    """Study → generate the eager default → the ready colorways."""
    study = await _study(authed, setup, policy=policy)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    _run_enqueued(fal)
    r = await authed.get(f"/studies/{study['id']}/colorways")
    assert r.status_code == 200
    return study, r.json()["colorways"]


# ── auth ─────────────────────────────────────────────────────────────────────

async def test_colorway_routes_require_auth(client):
    cid = uuid.uuid4()
    assert (await client.post(f"/colorways/{cid}/pin")).status_code == 401
    assert (await client.post(f"/colorways/{cid}/unpin")).status_code == 401
    assert (await client.post(f"/colorways/{cid}/export")).status_code == 401


# ── pin / unpin ──────────────────────────────────────────────────────────────

async def test_pin_copies_to_pinned_prefix_and_is_idempotent(authed, gen_setup, fal):  # noqa: F811
    _, cws = await _ready_colorways(authed, gen_setup, fal)
    ready = [c for c in cws if c["status"] == "ready"]
    assert len(ready) == 2  # eager default
    cw = ready[0]

    r = await authed.post(f"/colorways/{cw['id']}/pin")
    assert r.status_code == 200, r.text
    out = r.json()
    ws = out["image_key"].split("/")[1]
    assert out["status"] == "pinned"
    assert out["image_key"] == f"cw/{ws}/pinned/{cw['id']}.png"
    assert out["thumb_key"] == f"cw/{ws}/pinned/{cw['id']}.400.webp"
    # §3: the object is physically COPIED, original left in place
    assert _exists(out["image_key"]) and _exists(out["thumb_key"])
    assert _exists(f"cw/{ws}/{cw['id']}.png")

    # idempotent second pin
    r2 = await authed.post(f"/colorways/{cw['id']}/pin")
    assert r2.status_code == 200 and r2.json()["image_key"] == out["image_key"]

    # authoritative read-back: contact sheet shows pinned, still counts ready
    with _db() as conn:
        (st,) = conn.execute(
            "SELECT status FROM colorways WHERE id = %s", (cw["id"],)
        ).fetchone()
    assert st == "pinned"


async def test_pin_requires_ready(authed, gen_setup, fal):  # noqa: F811
    _, cws = await _ready_colorways(authed, gen_setup, fal)
    planned = next(c for c in cws if c["status"] == "planned")
    r = await authed.post(f"/colorways/{planned['id']}/pin")
    assert r.status_code == 409
    assert (await authed.post(f"/colorways/{uuid.uuid4()}/pin")).status_code == 404
    assert (await authed.post(f"/colorways/{uuid.uuid4()}/unpin")).status_code == 404


async def test_unpin_restores_even_after_working_object_evicted(authed, gen_setup, fal):  # noqa: F811
    _, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")
    r = await authed.post(f"/colorways/{cw['id']}/pin")
    assert r.status_code == 200
    pinned_key = r.json()["image_key"]
    ws = pinned_key.split("/")[1]
    work_key = f"cw/{ws}/{cw['id']}.png"

    # simulate the 30-day lifecycle evicting the working object while pinned
    s3, bucket = _s3()
    s3.delete_object(Bucket=bucket, Key=work_key)
    assert not _exists(work_key)

    r = await authed.post(f"/colorways/{cw['id']}/unpin")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ready" and out["image_key"] == work_key
    assert _exists(work_key)  # restored from the pinned copy
    assert not _exists(pinned_key)  # protection removed with the pin

    # idempotent unpin of a ready colorway
    assert (await authed.post(f"/colorways/{cw['id']}/unpin")).status_code == 200
    # unpin of a planned colorway is a conflict, not a silent no-op
    planned = next(c for c in cws if c["status"] == "planned")
    assert (await authed.post(f"/colorways/{planned['id']}/unpin")).status_code == 409


# ── 2K export ────────────────────────────────────────────────────────────────

async def test_export_free_upscale_2048(authed, gen_setup, fal):  # noqa: F811
    study, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")

    r = await authed.post(f"/colorways/{cw['id']}/export", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["method"] == "upscale" and out["cost_cents"] == 0
    assert max(out["width"], out["height"]) == 2048
    assert (out["width"], out["height"]) == (2048, 1536)  # 400×300 base aspect
    ws = out["key"].split("/")[1]
    assert out["key"] == f"export/{ws}/{cw['id']}.png"  # §3, 7d lifecycle
    assert "response-content-disposition" in out["download_url"].lower()

    s3, bucket = _s3()
    png = s3.get_object(Bucket=bucket, Key=out["key"])["Body"].read()
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (2048, 1536)

    # free path never ledgers
    assert not [
        row for row in _export_ledger(study["id"])
    ], "free upscale must not write spend_ledger rows"


def _export_ledger(study_id):
    with _db() as conn:
        return conn.execute(
            "SELECT cost_cents, cache_hit, model_id FROM spend_ledger "
            "WHERE study_id = %s AND kind = 'export'",
            (study_id,),
        ).fetchall()


async def test_export_regenerate_ledgers_and_returns_2048(
    authed, gen_setup, fal, monkeypatch  # noqa: F811
):
    study, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")

    regen_calls: list[tuple] = []

    def fake_2k(input_img, mask_img, prompt, frame):
        # the export path asks for a faithful re-render at the 2K frame
        assert frame == (2048, 1536)
        assert "higher resolution" in prompt
        regen_calls.append(frame)
        return input_img.resize(frame, Image.Resampling.LANCZOS), 0.01

    from app.workers import generate as W

    monkeypatch.setattr(W, "call_seedream", fake_2k)

    with _db() as conn:
        (actual_before,) = conn.execute(
            "SELECT actual_cost_cents FROM studies WHERE id = %s", (study["id"],)
        ).fetchone()

    r = await authed.post(
        f"/colorways/{cw['id']}/export", json={"regenerate": True}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["method"] == "regenerate" and out["cost_cents"] == 14
    assert (out["width"], out["height"]) == (2048, 1536)
    assert regen_calls == [(2048, 1536)]

    rows = _export_ledger(study["id"])
    assert rows == [(14, False, "seedream-5.0-pro")]
    with _db() as conn:  # export spend never pollutes the generation total
        (actual_after,) = conn.execute(
            "SELECT actual_cost_cents FROM studies WHERE id = %s", (study["id"],)
        ).fetchone()
    assert actual_after == actual_before

    s3, bucket = _s3()
    png = s3.get_object(Bucket=bucket, Key=out["key"])["Body"].read()
    assert Image.open(io.BytesIO(png)).size == (2048, 1536)


async def test_export_requires_generated_and_respects_caps(
    authed, gen_setup, fal, monkeypatch  # noqa: F811
):
    _, cws = await _ready_colorways(authed, gen_setup, fal)
    planned = next(c for c in cws if c["status"] == "planned")
    r = await authed.post(f"/colorways/{planned['id']}/export", json={})
    assert r.status_code == 409

    # the paid path refuses at the daily cap with the structured 402
    from app.config import get_settings

    cw = next(c for c in cws if c["status"] == "ready")
    monkeypatch.setattr(get_settings(), "budget_daily_cap_cents", 1)
    r = await authed.post(f"/colorways/{cw['id']}/export", json={"regenerate": True})
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["error"] == "budget_cap"
    # …but the FREE path still works at the cap (no spend, no gate)
    r = await authed.post(f"/colorways/{cw['id']}/export", json={})
    assert r.status_code == 200 and r.json()["cost_cents"] == 0


# ── anchored estimates (the M7-T2 flag) ──────────────────────────────────────

async def test_anchored_estimate_matches_executor_call_count(
    authed, gen_setup, fal  # noqa: F811
):
    """§8.5 rule 1 × §8.8: anchored slots are REAL chain steps in the
    executor, so the estimate must count them. K=3, C=3 (c121), anchor
    body→32: k_eff=c_eff=2 → 2 permutations, 3 steps each (anchor included);
    the estimate's planned_calls must equal the mocked executor's call count
    and the ledgered total must equal est_cost_cents."""
    setup = gen_setup
    study = await _create_study(authed, setup, palette_id="c121")
    slots = [_slot(setup, labels, name) for labels, name in THREE_SLOTS]
    slots[0]["anchor_color_id"] = 32  # darkest of c121 {32, 50, 108}
    r = await authed.put(f"/studies/{study['id']}/slots", json=slots)
    assert r.status_code == 200, r.text

    r = await authed.post(f"/studies/{study['id']}/estimate")
    assert r.status_code == 200, r.text
    est = r.json()
    assert est["anchors"] == 1 and est["regime"] == "bijective"
    assert est["perm_total"] == 2
    assert est["steps_per_colorway"] == 3  # 2 free + the anchor step
    assert est["naive_calls"] == 6
    # anchor 32 is c121's darkest (L* 34.6 < 108's 36.3 < 50's 71.0) so both
    # chains share the depth-1 anchor node: 1 + 2 + 2 = 5 trie calls.
    # plan_study()'s free-space count said 4 — the undercount this fixes.
    planned_calls = est["planned_calls"]
    assert planned_calls == 5 and est["trie_calls_full"] == 5
    assert est["est_cost_cents"] == 34  # round_half_up(5 × 6.75¢)
    assert est["eta_seconds"] == 5 * 39

    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202, r.text
    # enqueue-time conservative estimate agrees with the pure estimate
    assert r.json()["estimated_cents"] == est["est_cost_cents"]
    _run_enqueued(fal)

    assert len(fal["calls"]) == planned_calls, (
        f"estimate promised {planned_calls} model calls, executor made "
        f"{len(fal['calls'])}"
    )
    with _db() as conn:
        (actual, status) = conn.execute(
            "SELECT actual_cost_cents, status FROM studies WHERE id = %s",
            (study["id"],),
        ).fetchone()
    assert status == "complete"
    assert actual == est["est_cost_cents"]

    # every colorway is ready and honours the anchor on slot 0
    r = await authed.get(f"/studies/{study['id']}/colorways")
    cws = r.json()["colorways"]
    assert len(cws) == 2 and all(c["status"] == "ready" for c in cws)
    for c in cws:
        chip0 = next(ch for ch in c["mapping"] if ch["slot_idx"] == 0)
        assert chip0["color_id"] == 32


async def test_unanchored_estimate_numbers_unchanged(authed, gen_setup):  # noqa: F811
    """Preservation: with zero anchors the anchored-maths path must reproduce
    the pure-engine numbers exactly (the TDD-pinned $1.01 fixture)."""
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/estimate")
    assert r.status_code == 200
    est = r.json()
    assert est["anchors"] == 0
    assert est["steps_per_colorway"] == 3
    assert est["perm_total"] == 6 and est["planned"] == 6
    assert est["naive_calls"] == 18 and est["trie_calls_full"] == 15
    assert est["planned_calls"] == 15 and est["est_cost"] == "1.01"
    assert est["saved_pct"] == 17
