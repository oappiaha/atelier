"""Residual-coverage fix (app.wada.coverage + the generate-worker wiring).

The leak this pins: slot unions built from coarse polygons don't cover the
full product silhouette, so the §8.10 composite keeps the ORIGINAL base colour
in the slivers between/outside regions. The fix grows the slot masks with the
PhotoRoom cutout's alpha as ground truth until the union covers the product.

Pure-CV tests drive cover_silhouette on synthetic masks; the worker tests run
the real executor against an injected cutout (media.cutout_key set directly +
an RGBA object in the test bucket — ensure_cutout then answers "already:" with
no PhotoRoom key needed) and assert on the finished colorway artifact.
"""

import hashlib
import io

import numpy as np
import pytest
from PIL import Image

from app.wada.coverage import cover_silhouette
from tests.test_generation import (  # noqa: F401 — fixtures/helpers by import
    FRAME_H,
    FRAME_W,
    _db,
    _run_enqueued,
    _study,
    fal,
    gen_setup,
)
from tests.test_studies import (  # noqa: F401 — sanzo is a fixture import
    RECTS,
    _create_study,
    _slot,
    sanzo,
)

# ── pure CV: cover_silhouette ────────────────────────────────────────────────


def _rect(shape, y0, y1, x0, x1) -> np.ndarray:
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def test_union_covers_silhouette_exactly_after_fix():
    """Two slot masks leaving a sliver + a corner patch uncovered → after the
    fix the union equals the silhouette exactly; no original pixel is lost."""
    shape = (120, 120)
    sil = _rect(shape, 10, 110, 10, 110)
    a = _rect(shape, 10, 110, 10, 60)
    b = _rect(shape, 10, 90, 64, 110)  # leaves sliver x 60:64 + patch y 90:110
    covered, info = cover_silhouette(sil, {"a": a, "b": b})

    union = covered["a"] | covered["b"]
    assert np.array_equal(union, sil)  # masks ⊆ silhouette → exact cover
    assert not (covered["a"] & covered["b"]).any()  # residual split, not shared
    assert info.residual_px == int((sil & ~(a | b)).sum())
    assert sum(info.added_px.values()) == info.residual_px
    # existing mask pixels are never removed
    assert covered["a"][a].all() and covered["b"][b].all()


def test_component_attaches_to_adjacent_slot():
    shape = (100, 100)
    a = _rect(shape, 0, 100, 0, 40)
    b = _rect(shape, 0, 50, 60, 100)
    patch = _rect(shape, 50, 70, 60, 80)  # hangs off b's bottom edge only
    sil = a | b | patch
    covered, info = cover_silhouette(sil, {"a": a, "b": b})
    assert np.array_equal(covered["a"], a)  # not adjacent → unchanged
    assert covered["b"][patch].all()
    assert info.added_px == {"a": 0, "b": int(patch.sum())}


def test_detached_component_goes_to_nearest_slot():
    """A component touching only background still joins the nearest mask."""
    shape = (100, 100)
    a = _rect(shape, 0, 100, 0, 10)
    b = _rect(shape, 0, 100, 90, 100)
    island = _rect(shape, 45, 50, 60, 65)  # ~50px from a, ~25px from b
    sil = a | b | island
    covered, _ = cover_silhouette(sil, {"a": a, "b": b})
    assert covered["b"][island].all()
    assert np.array_equal(covered["a"], a)


def test_lock_attracts_its_residual_and_is_never_swallowed():
    """Locks behave exactly like paint slots for coverage: a sliver hugging
    the locked slot joins the LOCK, and the lock's own area is never handed
    to a recolour slot."""
    shape = (100, 100)
    paint = _rect(shape, 0, 100, 0, 40)
    lock = _rect(shape, 0, 100, 60, 100)
    sliver = _rect(shape, 20, 40, 45, 60)  # adjacent to the lock only
    sil = paint | lock | sliver
    covered, info = cover_silhouette(sil, {"lock": lock, "paint": paint})
    assert covered["lock"][sliver].all()
    assert np.array_equal(covered["paint"], paint)
    assert not (covered["paint"] & lock).any()  # locked area untouched by paint
    assert info.added_px["paint"] == 0


def test_full_coverage_and_degenerate_inputs_are_noops():
    shape = (60, 60)
    sil = _rect(shape, 10, 50, 10, 50)
    a = _rect(shape, 10, 50, 10, 30)
    b = _rect(shape, 10, 50, 30, 50)
    covered, info = cover_silhouette(sil, {"a": a, "b": b})
    assert info.residual_px == 0 and info.n_components == 0
    assert np.array_equal(covered["a"], a) and np.array_equal(covered["b"], b)
    # empty silhouette / all-empty masks: unchanged
    covered, info = cover_silhouette(np.zeros(shape, bool), {"a": a})
    assert info.residual_px == 0 and np.array_equal(covered["a"], a)
    covered, info = cover_silhouette(sil, {"a": np.zeros(shape, bool)})
    assert info.residual_px == 0 and not covered["a"].any()
    with pytest.raises(ValueError):
        cover_silhouette(sil, {"a": np.zeros((10, 10), bool)})


def test_cover_silhouette_is_deterministic():
    shape = (128, 128)
    rng = np.random.default_rng(7)
    sil = _rect(shape, 8, 120, 8, 120)
    sil |= rng.random(shape) > 0.97  # speckle outside the body too
    a = _rect(shape, 8, 120, 8, 50)
    b = _rect(shape, 8, 100, 70, 120)
    c = _rect(shape, 104, 120, 70, 120)
    one, i1 = cover_silhouette(sil, {"a": a, "b": b, "c": c})
    two, i2 = cover_silhouette(sil, {"a": a, "b": b, "c": c})
    for k in one:
        assert np.array_equal(one[k], two[k])
    assert i1.added_px == i2.added_px and i1.residual_px == i2.residual_px
    # and the union always covers the silhouette
    assert not (sil & ~(one["a"] | one["b"] | one["c"])).any()


GREEN = (7, 253, 11)  # the "original base colour" no palette colour resembles


def test_composite_of_fully_covered_product_changes_no_background():
    """§8.10 pin at the composite level: with the union grown to cover the
    silhouette, the feathered composite leaves everything outside the
    feathered support byte-identical — and no exact base-colour pixel
    survives inside the product. The same scene WITHOUT coverage leaks."""
    from app.wada import generation as G

    shape = (160, 160)
    sil = _rect(shape, 30, 130, 20, 140)
    a = _rect(shape, 30, 130, 20, 55)
    b = _rect(shape, 30, 130, 85, 140)  # 30px-wide sliver x 55:85 uncovered

    base_arr = np.full((*shape, 3), 255, np.uint8)
    base_arr[sil] = GREEN
    base = Image.fromarray(base_arr, "RGB")
    edited = Image.fromarray(np.full((*shape, 3), (240, 120, 20), np.uint8), "RGB")

    def run(masks):
        comp, touched = base, np.zeros(shape, bool)
        for k in sorted(masks):
            comp, untouched = G.composite_step(comp, edited, masks[k])
            touched |= ~untouched
        return np.asarray(comp), touched

    covered, _ = cover_silhouette(sil, {"a": a, "b": b})
    fixed, touched = run(covered)
    # background outside the feathered support is byte-identical…
    assert np.array_equal(fixed[~touched], base_arr[~touched])
    # …including ALL background further than the feather can reach
    far_bg = ~G.dilate(sil, 12)
    assert np.array_equal(fixed[far_bg], base_arr[far_bg])
    # and the original base colour is extinct inside the product
    assert not (np.all(fixed == GREEN, axis=-1) & sil).any()

    # counterfactual: the uncovered sliver leaks the base colour verbatim
    leaky, _ = run({"a": a, "b": b})
    assert (np.all(leaky == GREEN, axis=-1) & sil).any()


# ── the worker wiring (real executor, injected cutout) ──────────────────────


def _frame_rect(y0, y1, x0, x1) -> np.ndarray:
    return _rect((FRAME_H, FRAME_W), y0, y1, x0, x1)


def _regions_silhouette() -> np.ndarray:
    sil = np.zeros((FRAME_H, FRAME_W), bool)
    for x0, y0, x1, y1 in RECTS.values():
        sil[y0:y1, x0:x1] = True
    return sil


def _inject_cutout(setup, alpha: np.ndarray) -> bytes:
    """An RGBA cutout in the test bucket + media.cutout_key pointing at it —
    ensure_cutout() then answers 'already:<key>' without PhotoRoom."""
    from app.config import get_settings
    from app.storage import get_s3
    from app.workers.cutout import cutout_key_for

    arr = np.zeros((FRAME_H, FRAME_W, 4), np.uint8)
    arr[..., :3] = GREEN
    rng = np.random.default_rng()  # unique base sha → private trie namespace
    arr[5:15, 5:15, :3] = rng.integers(0, 255, (10, 10, 3), np.uint8)
    arr[..., 3] = alpha.astype(np.uint8) * 255
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, format="PNG")
    png = buf.getvalue()

    with _db() as conn:
        ws, sha = conn.execute(
            "SELECT workspace_id, sha256 FROM media WHERE id = %s",
            (setup["media"]["id"],),
        ).fetchone()
        key = cutout_key_for(str(ws), sha)
        get_s3().put_object(
            Bucket=get_settings().s3_bucket, Key=key, Body=png,
            ContentType="image/png",
        )
        conn.execute(
            "UPDATE media SET cutout_key = %s WHERE id = %s",
            (key, setup["media"]["id"]),
        )
        conn.commit()
    return png


def _final_and_base(study_id: str, cutout_png: bytes):
    from app.config import get_settings
    from app.storage import get_s3
    from app.wada import segmentation

    with _db() as conn:
        (cw_key,) = conn.execute(
            "SELECT image_key FROM colorways WHERE study_id = %s "
            "AND status = 'ready' ORDER BY created_at LIMIT 1",
            (study_id,),
        ).fetchone()
    final = Image.open(io.BytesIO(
        get_s3().get_object(Bucket=get_settings().s3_bucket, Key=cw_key)["Body"].read()
    )).convert("RGB")
    base_img, alpha = segmentation.working_image_and_alpha(cutout_png)
    return np.asarray(final), np.asarray(base_img), alpha


async def test_worker_grows_masks_no_base_colour_leak(authed, gen_setup, fal):  # noqa: F811
    """End-to-end: a silhouette larger than the slot unions (a sliver bridging
    body↔panel + a tab under the body) → the worker grows the masks, the
    finished colorway carries NO original-base pixel inside the product, the
    grown masks live under their own sha, and a replay is all cache hits."""
    from app.config import get_settings
    from app.storage import get_s3
    from app.wada import generation as G

    sil = _regions_silhouette()
    sil |= _frame_rect(0, 96, 240, 250)  # sliver in NO region: body↔panel gap
    sil |= _frame_rect(150, 162, 0, 40)  # tab hanging off the body's bottom
    cutout_png = _inject_cutout(gen_setup, sil)

    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    _run_enqueued(fal)

    r = await authed.get(f"/studies/{study['id']}/colorways")
    ready = [c for c in r.json()["colorways"] if c["status"] == "ready"]
    assert len(ready) == 2
    assert all(c["lock_verified"] is True for c in ready)

    final, base, alpha = _final_and_base(study["id"], cutout_png)
    assert alpha is not None and np.array_equal(alpha, sil)

    # the original base colour is extinct inside the product silhouette
    assert not (np.all(final == GREEN, axis=-1) & sil).any()

    # the step masks are the GROWN ones, re-addressed by their own sha,
    # and their union covers the silhouette
    s3, bucket = get_s3(), get_settings().s3_bucket
    base_sha = hashlib.sha256(G.png_bytes(Image.fromarray(base, "RGB"))).hexdigest()
    with _db() as conn:
        shas = [s for (s,) in conn.execute(
            "SELECT DISTINCT step_mask_sha FROM gen_nodes WHERE base_sha256 = %s",
            (base_sha,),
        ).fetchall()]
        (media_sha,) = conn.execute(
            "SELECT sha256 FROM media WHERE id = %s", (gen_setup["media"]["id"],)
        ).fetchone()
        (ws,) = conn.execute(
            "SELECT workspace_id FROM media WHERE id = %s", (gen_setup["media"]["id"],)
        ).fetchone()
    assert shas
    slot_shas = {s["union_sha256"] for s in study["slots"]}
    assert set(shas) != slot_shas  # at least one mask grew
    touched = np.zeros((FRAME_H, FRAME_W), bool)
    union = np.zeros((FRAME_H, FRAME_W), bool)
    for sha in shas:
        png = s3.get_object(
            Bucket=bucket, Key=f"mask/{ws}/{media_sha}/union/{sha}.png"
        )["Body"].read()
        m = np.asarray(Image.open(io.BytesIO(png)).convert("L")) > 127
        union |= m
        touched |= G.touched_support(m)
    assert not (sil & ~union).any()  # full silhouette coverage
    # fully covered product: the composite changed no pixel outside the
    # feathered support — the alpha-outside background is untouched
    assert np.array_equal(final[~touched], base[~touched])

    # deterministic growth → identical shas → a replay is 100% cache hits
    n_calls = len(fal["calls"])
    with _db() as conn:
        conn.execute(
            "UPDATE colorways SET status = 'planned', image_key = NULL "
            "WHERE study_id = %s",
            (study["id"],),
        )
        conn.commit()
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    _run_enqueued(fal)
    assert len(fal["calls"]) == n_calls  # ZERO new model calls


async def test_worker_lock_slot_keeps_its_residual_unpainted(authed, gen_setup, fal):  # noqa: F811
    """A residual sliver hugging a LOCKED slot joins the lock: it stays
    byte-identical to the base (a lock means 'don't recolor'), instead of
    being swallowed by the nearest recolour slot."""
    sil = _regions_silhouette()
    sliver = _frame_rect(200, 215, 340, 348)  # hugs the buckle's right edge
    sil |= sliver
    cutout_png = _inject_cutout(gen_setup, sil)

    study = await _create_study(authed, gen_setup)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[
            _slot(gen_setup, ["body"], "body"),
            _slot(gen_setup, ["panel"], "panel"),
            _slot(gen_setup, ["strap"], "strap"),
            _slot(gen_setup, ["buckle"], "hardware", kind="lock"),
        ],
    )
    assert r.status_code == 200, r.text
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    _run_enqueued(fal)

    r = await authed.get(f"/studies/{study['id']}/colorways")
    ready = [c for c in r.json()["colorways"] if c["status"] == "ready"]
    assert ready and all(c["lock_verified"] is True for c in ready)

    final, base, _ = _final_and_base(study["id"], cutout_png)
    # the lock-adjacent sliver was NOT recoloured — byte-identical to base
    assert np.array_equal(final[sliver], base[sliver])
    # and the locked buckle itself is untouched
    x0, y0, x1, y1 = RECTS["buckle"]
    buckle = _frame_rect(y0, y1, x0, x1)
    assert np.array_equal(final[buckle], base[buckle])
    # the paint slots WERE recoloured (the run really painted)
    x0, y0, x1, y1 = RECTS["body"]
    body = _frame_rect(y0 + 20, y1 - 20, x0 + 20, x1 - 20)
    assert not np.array_equal(final[body], base[body])
