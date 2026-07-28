"""M5-T2: study lifecycle endpoints (TDD §9) — POST /studies, GET /studies/{id},
PUT /studies/{id}/slots, POST /studies/{id}/estimate.

The estimate numbers are asserted against the §8.4 published table (the same
numbers test_wada_math.py pins on the engine), so these tests tie
endpoint → engine → TDD in one chain.

Region fixture strategy: media goes through the real API (upload → commit →
entry attach, so the design_id trigger fires); the regions themselves are
inserted directly (real mask PNGs in the test bucket + rows shaped exactly
like app/workers/segment.py writes them) — the segmentation pipeline is
covered end-to-end in test_segmentation.py, and direct rows give exact,
deterministic area fractions for the math assertions.
"""

import hashlib
import io
import os
import uuid

import numpy as np
import psycopg
import pytest
import pytest_asyncio
from PIL import Image

# frame + region rectangles (x0, y0, x1, y1) — non-overlapping, exact areas
FRAME_W, FRAME_H = 400, 300
RECTS = {
    "body": (0, 0, 240, 150),  # area 36000/120000 = 0.30
    "panel": (250, 0, 400, 96),  # 14400/120000 = 0.12
    "strap": (0, 160, 80, 220),  # 4800/120000  = 0.04
    "buckle": (300, 200, 340, 215),  # 600/120000   = 0.005 -> is_cosmetic
}


@pytest_asyncio.fixture(scope="session")
async def sanzo(isolated_infra):
    """Seed the corpus into the test DB, once per session (idempotent)."""
    from app.db import SessionLocal
    from app.wada.seed_sanzo import seed

    async with SessionLocal() as session:
        counts = await seed(session)
        await session.commit()
    return counts


def _mask_png(rect: tuple[int, int, int, int]) -> tuple[bytes, float]:
    arr = np.zeros((FRAME_H, FRAME_W), bool)
    x0, y0, x1, y1 = rect
    arr[y0:y1, x0:x1] = True
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8) * 255, "L").save(buf, format="PNG", optimize=True)
    return buf.getvalue(), float(arr.sum() / arr.size)


async def _design_media(authed, design_factory, upload_media) -> tuple[dict, dict]:
    """A design + an image media ATTACHED to it (via the real entry flow, so
    the phase-sync trigger denormalises design_id onto the media row)."""
    design = await design_factory()
    r = await authed.post(
        "/entries", json={"design_id": design["id"], "phase": "final", "body": "study base"}
    )
    assert r.status_code == 201, r.text
    media = await upload_media(entry_id=r.json()["id"])
    return design, media


def _insert_regions(media: dict) -> dict[str, dict]:
    """Real mask PNGs into the test bucket + region rows shaped like the
    segment worker writes them. Returns label -> {id, area, mask_key, sha}."""
    from app.config import get_settings
    from app.storage import get_s3

    s3 = get_s3()
    bucket = get_settings().s3_bucket
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    out: dict[str, dict] = {}
    with psycopg.connect(dsn) as conn:
        ws, sha = conn.execute(
            "SELECT workspace_id, sha256 FROM media WHERE id = %s", (media["id"],)
        ).fetchone()
        for idx, (label, rect) in enumerate(RECTS.items()):
            png, area = _mask_png(rect)
            key = f"mask/{ws}/{sha}/{idx}-{label}.png"
            s3.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
            (rid,) = conn.execute(
                """
                INSERT INTO regions (workspace_id, media_id, label, confidence,
                                     mask_key, mask_sha256, area_fraction, bbox, mean_lab)
                VALUES (%s, %s, %s, 0.95, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    ws, media["id"], label, key, hashlib.sha256(png).hexdigest(),
                    area, list(rect), [50.0, 5.0, 5.0],
                ),
            ).fetchone()
            out[label] = {"id": str(rid), "area": area, "mask_key": key, "png": png}
        conn.commit()
    return out


@pytest_asyncio.fixture()
async def study_setup(sanzo, authed, design_factory, upload_media):
    """design + attached media + 4 regions with exact areas."""
    design, media = await _design_media(authed, design_factory, upload_media)
    regions = _insert_regions(media)
    return {"design": design, "media": media, "regions": regions}


async def _create_study(authed, setup, palette_id="c121", policy=None) -> dict:
    body = {
        "design_id": setup["design"]["id"],
        "base_media_id": setup["media"]["id"],
        "palette_id": palette_id,
    }
    if policy is not None:
        body["policy"] = policy
    r = await authed.post("/studies", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _slot(setup, labels: list[str], name: str, kind="paint", anchor=None) -> dict:
    s = {
        "label": name,
        "kind": kind,
        "region_ids": [setup["regions"][label]["id"] for label in labels],
    }
    if anchor is not None:
        s["anchor_color_id"] = anchor
    return s


THREE_SLOTS = [  # K=3: body / panel / strap+buckle
    (["body"], "body"),
    (["panel"], "panel"),
    (["strap", "buckle"], "trim"),
]


async def _three_slot_study(authed, setup, palette_id="c121", policy=None) -> dict:
    study = await _create_study(authed, setup, palette_id=palette_id, policy=policy)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[_slot(setup, labels, name) for labels, name in THREE_SLOTS],
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── auth ─────────────────────────────────────────────────────────────────────

async def test_studies_require_auth(client):
    sid = uuid.uuid4()
    assert (await client.post("/studies", json={})).status_code == 401
    assert (await client.get(f"/studies/{sid}")).status_code == 401
    assert (await client.put(f"/studies/{sid}/slots", json=[])).status_code == 401
    assert (await client.post(f"/studies/{sid}/estimate")).status_code == 401


# ── create ───────────────────────────────────────────────────────────────────

async def test_create_study_draft_with_default_policy(authed, study_setup):
    study = await _create_study(authed, study_setup)
    assert study["status"] == "draft"
    assert study["palette_id"] == "c121"
    assert study["model_id"] == "seedream-5.0-pro"
    assert study["prompt_version"] == "v2"  # M7-T1 gate: per-region template
    assert study["policy"] == {  # §8.6 defaults, verbatim
        "mode": "curated", "cap": 12, "anchors": {}, "prune_twins": True,
        "prune_cosmetic": True, "twin_threshold": 8.0, "diversity": True,
        "eager": 2, "resolution": 1536,
    }
    assert study["slots"] == [] and study["k"] == 0
    assert study["perm_total"] is None and study["est_cost_cents"] is None

    r = await authed.get(f"/studies/{study['id']}")
    assert r.status_code == 200 and r.json()["id"] == study["id"]


async def test_create_study_policy_override(authed, study_setup):
    study = await _create_study(authed, study_setup, policy={"cap": 6, "eager": 1})
    assert study["policy"]["cap"] == 6 and study["policy"]["eager"] == 1
    assert study["policy"]["mode"] == "curated"  # untouched defaults survive


async def test_create_study_negatives(authed, study_setup):
    setup = study_setup
    base = {
        "design_id": setup["design"]["id"],
        "base_media_id": setup["media"]["id"],
        "palette_id": "c121",
    }
    r = await authed.post("/studies", json=base | {"design_id": str(uuid.uuid4())})
    assert r.status_code == 404
    r = await authed.post("/studies", json=base | {"base_media_id": str(uuid.uuid4())})
    assert r.status_code == 404
    r = await authed.post("/studies", json=base | {"palette_id": "c9999"})
    assert r.status_code == 404
    assert "palette" in r.json()["detail"]
    # eager > cap is a contradiction
    r = await authed.post("/studies", json=base | {"policy": {"cap": 2, "eager": 5}})
    assert r.status_code == 422


async def test_create_study_media_must_belong_to_design(
    authed, study_setup, design_factory
):
    other = await design_factory()
    r = await authed.post(
        "/studies",
        json={
            "design_id": other["id"],
            "base_media_id": study_setup["media"]["id"],
            "palette_id": "c121",
        },
    )
    assert r.status_code == 422
    assert "belong" in r.json()["detail"]


async def test_get_study_unknown_404(authed):
    assert (await authed.get(f"/studies/{uuid.uuid4()}")).status_code == 404


# ── slots ────────────────────────────────────────────────────────────────────

async def test_put_slots_composes_union_masks(authed, study_setup):
    from app.config import get_settings
    from app.storage import get_s3

    setup = study_setup
    study = await _create_study(authed, setup)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[
            _slot(setup, ["strap", "panel"], "trim"),  # 2-region union, area 0.16
            _slot(setup, ["body"], "body"),  # area 0.30
            _slot(setup, ["buckle"], "buckle", kind="lock"),
        ],
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["k"] == 2
    # canonical order: paint by area desc first, then locks
    assert [(s["idx"], s["label"], s["kind"]) for s in out["slots"]] == [
        (0, "body", "paint"), (1, "trim", "paint"), (2, "buckle", "lock"),
    ]
    body_slot, trim_slot, lock_slot = out["slots"]
    assert body_slot["area_fraction"] == pytest.approx(0.30, abs=1e-6)
    assert trim_slot["area_fraction"] == pytest.approx(0.16, abs=1e-6)

    # union objects are real 0/255 PNGs at the §3 key, sha-addressed
    s3, bucket = get_s3(), get_settings().s3_bucket
    for slot in out["slots"]:
        assert "/union/" in slot["union_mask_key"]
        assert slot["union_mask_key"].endswith(f"{slot['union_sha256']}.png")
        png = s3.get_object(Bucket=bucket, Key=slot["union_mask_key"])["Body"].read()
        assert hashlib.sha256(png).hexdigest() == slot["union_sha256"]

    # 2-region slot: union == logical OR of the member masks
    png = s3.get_object(Bucket=bucket, Key=trim_slot["union_mask_key"])["Body"].read()
    union = np.asarray(Image.open(io.BytesIO(png)).convert("L")) > 127
    expect = np.zeros((FRAME_H, FRAME_W), bool)
    for label in ("strap", "panel"):
        x0, y0, x1, y1 = RECTS[label]
        expect[y0:y1, x0:x1] = True
    assert np.array_equal(union, expect)

    # single-region slot: union bytes are a deterministic re-encode of the mask
    png = s3.get_object(Bucket=bucket, Key=body_slot["union_mask_key"])["Body"].read()
    assert png == setup["regions"]["body"]["png"]


async def test_put_slots_is_replace_all(authed, study_setup):
    setup = study_setup
    study = await _three_slot_study(authed, setup)
    assert len(study["slots"]) == 3
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[_slot(setup, ["body", "panel", "strap", "buckle"], "everything")],
    )
    assert r.status_code == 200
    out = r.json()
    assert len(out["slots"]) == 1 and out["k"] == 1
    assert out["slots"][0]["area_fraction"] == pytest.approx(0.465, abs=1e-6)


async def test_put_slots_negatives(authed, study_setup, upload_media, design_factory):
    setup = study_setup
    study = await _create_study(authed, setup)
    sid = study["id"]
    body_slot = _slot(setup, ["body"], "body")

    # unknown study
    r = await authed.put(f"/studies/{uuid.uuid4()}/slots", json=[body_slot])
    assert r.status_code == 404
    # empty config / no paint slots
    r = await authed.put(f"/studies/{sid}/slots", json=[])
    assert r.status_code == 422
    r = await authed.put(
        f"/studies/{sid}/slots", json=[_slot(setup, ["body"], "b", kind="lock")]
    )
    assert r.status_code == 422
    # a slot with zero regions is an invalid shape
    r = await authed.put(
        f"/studies/{sid}/slots", json=[{"label": "x", "kind": "paint", "region_ids": []}]
    )
    assert r.status_code == 422
    # bad kind
    r = await authed.put(
        f"/studies/{sid}/slots",
        json=[{"label": "x", "kind": "sparkle", "region_ids": [setup["regions"]["body"]["id"]]}],
    )
    assert r.status_code == 422
    # duplicate region across slots
    r = await authed.put(
        f"/studies/{sid}/slots",
        json=[body_slot, _slot(setup, ["body", "panel"], "again")],
    )
    assert r.status_code == 422
    # unknown region id
    r = await authed.put(
        f"/studies/{sid}/slots",
        json=[{"label": "x", "kind": "paint", "region_ids": [str(uuid.uuid4())]}],
    )
    assert r.status_code == 404
    assert "region" in r.json()["detail"]


async def test_put_slots_rejects_cross_media_regions(
    authed, study_setup, design_factory, upload_media
):
    """Regions must belong to the study's base media."""
    setup = study_setup
    _, other_media = await _design_media(
        authed, design_factory, upload_media
    )
    other_regions = _insert_regions(other_media)
    study = await _create_study(authed, setup)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[
            _slot(setup, ["body"], "body"),
            {"label": "alien", "kind": "paint",
             "region_ids": [other_regions["panel"]["id"]]},
        ],
    )
    assert r.status_code == 422
    assert "base media" in r.json()["detail"]


async def test_put_slots_anchor_validation(authed, study_setup):
    setup = study_setup
    study = await _create_study(authed, setup)  # c121 colors {32,50,108}
    sid = study["id"]
    # anchor colour not in the palette
    r = await authed.put(
        f"/studies/{sid}/slots", json=[_slot(setup, ["body"], "body", anchor=1)]
    )
    assert r.status_code == 422 and "palette" in r.json()["detail"]
    # duplicate anchor colour
    r = await authed.put(
        f"/studies/{sid}/slots",
        json=[
            _slot(setup, ["body"], "body", anchor=32),
            _slot(setup, ["panel"], "panel", anchor=32),
        ],
    )
    assert r.status_code == 422
    # anchor on a lock slot
    r = await authed.put(
        f"/studies/{sid}/slots",
        json=[
            _slot(setup, ["body"], "body"),
            _slot(setup, ["panel"], "p", kind="lock", anchor=32),
        ],
    )
    assert r.status_code == 422
    # valid anchor lands in the slot AND the §8.6 policy mirror
    r = await authed.put(
        f"/studies/{sid}/slots",
        json=[
            _slot(setup, ["body"], "body", anchor=32),
            _slot(setup, ["panel"], "panel"),
        ],
    )
    assert r.status_code == 200
    out = r.json()
    assert out["slots"][0]["anchor_color_id"] == 32
    assert out["policy"]["anchors"] == {"0": 32}


# ── estimate: the §8.4 table through the endpoint ────────────────────────────

async def test_estimate_bijective_k3_c3_is_the_101_study(authed, study_setup):
    """§8.4 row K=3,C=3 and the §8.13.1 readout: 6 permutations -> 15 model
    calls -> ~$1.01. The exact numbers test_wada_math pins on the engine."""
    study = await _three_slot_study(authed, study_setup)  # c121: no twins (ΔE 24.5)
    r = await authed.post(f"/studies/{study['id']}/estimate")
    assert r.status_code == 200, r.text
    est = r.json()
    assert (est["k"], est["c"], est["regime"]) == (3, 3, "bijective")
    assert est["perm_total"] == 6
    assert est["after_dedupe"] == 6
    assert est["planned"] == 6
    assert est["steps_per_colorway"] == 3
    assert est["naive_calls"] == 18 and est["naive_cost"] == "1.22"
    assert est["trie_calls_full"] == 15 and est["trie_cost_full"] == "1.01"
    assert est["saved_pct"] == 17
    assert est["planned_calls"] == 15
    assert est["est_cost"] == "1.01" and est["est_cost_cents"] == 101
    assert est["eager"] == 2
    assert est["cap"] == 12 and est["cap_exceeded"] is False and est["cap_message"] is None
    assert "one-to-one mapping (6 permutations)" in est["message"]

    # NO SPEND, but the estimate persists onto the study row
    r = await authed.get(f"/studies/{study['id']}")
    s = r.json()
    assert s["perm_total"] == 6 and s["perm_planned"] == 6 and s["est_cost_cents"] == 101
    assert s["actual_cost_cents"] == 0 and s["status"] == "draft"


async def test_estimate_surjective_k4_c3_caps_at_12(authed, study_setup):
    """§8.4 row K=4,C=3: 36 perms, 108 naive ($7.29), 82 trie ($5.54), 24%.
    Cap 12 -> the §8.13.1 amber copy quotes the capped upper bound ~$2.43,
    and the regime line is the PRD §4.2 pedagogy sentence."""
    setup = study_setup
    study = await _create_study(authed, setup)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[_slot(setup, [label], label) for label in RECTS],  # 4 paint slots
    )
    assert r.status_code == 200
    r = await authed.post(f"/studies/{study['id']}/estimate")
    assert r.status_code == 200, r.text
    est = r.json()
    assert (est["k"], est["c"], est["regime"]) == (4, 3, "surjective")
    assert est["perm_total"] == 36
    assert est["steps_per_colorway"] == 3
    assert est["naive_calls"] == 108 and est["naive_cost"] == "7.29"
    assert est["trie_calls_full"] == 82 and est["trie_cost_full"] == "5.54"
    assert est["saved_pct"] == 24
    assert est["planned"] == 12  # rule 4: the cap
    assert est["cap_exceeded"] is True
    assert est["cap_message"] == (
        "36 permutations exceeds the cap of 12. The 12 strongest and most "
        "varied will be generated (~$2.43). Merge slots or anchor a colour "
        "to explore the full space."
    )
    assert est["message"] == (
        "You have 4 slots and a 3-colour palette, so slots will share colours "
        "(36 permutations). Pick a 4-colour palette for a clean one-to-one "
        "mapping (24), or merge two slots (6)."
    )


async def test_estimate_injective_k2_c3(authed, study_setup):
    """§8.4 row K=2,C=3: 6 perms, 12 naive ($0.81), 10 trie ($0.68), 17%."""
    setup = study_setup
    study = await _create_study(authed, setup)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[
            _slot(setup, ["body"], "body"),
            _slot(setup, ["panel", "strap", "buckle"], "rest"),
        ],
    )
    assert r.status_code == 200
    r = await authed.post(f"/studies/{study['id']}/estimate")
    est = r.json()
    assert (est["k"], est["c"], est["regime"]) == (2, 3, "injective")
    assert est["perm_total"] == 6
    assert est["steps_per_colorway"] == 2
    assert est["naive_calls"] == 12 and est["naive_cost"] == "0.81"
    assert est["trie_calls_full"] == 10 and est["trie_cost_full"] == "0.68"
    assert est["saved_pct"] == 17
    assert "not every colour will be used" in est["message"]


async def test_estimate_anchor_cuts_24_to_6(authed, study_setup):
    """§8.5 rule 1 through the API: K=4,C=4 -> 24; one anchor -> K=3,C=3 -> 6."""
    setup = study_setup
    study = await _create_study(authed, setup, palette_id="c241")  # 4 colours
    slots = [_slot(setup, [label], label) for label in RECTS]
    r = await authed.put(f"/studies/{study['id']}/slots", json=slots)
    assert r.status_code == 200
    est = (await authed.post(f"/studies/{study['id']}/estimate")).json()
    assert (est["k"], est["c"], est["anchors"]) == (4, 4, 0)
    assert est["perm_total"] == 24 and est["regime"] == "bijective"

    anchor_color = (await authed.get("/palettes/c241")).json()["color_ids"][0]
    slots[0]["anchor_color_id"] = anchor_color
    r = await authed.put(f"/studies/{study['id']}/slots", json=slots)
    assert r.status_code == 200, r.text
    est = (await authed.post(f"/studies/{study['id']}/estimate")).json()
    assert (est["k"], est["c"], est["anchors"]) == (4, 4, 1)
    assert est["perm_total"] == 6  # effective K=3,C=3
    # M8: the anchor is a REAL chain step (the executor paints it), interleaved
    # by darkness — c241's anchor (Red Orange, L*50.9) is second-darkest, so
    # the full trie is 3+3+6+6 = 18 nodes, not the free-space 15 the pre-M8
    # estimate showed (the undercount flagged in M7-T2).
    assert est["steps_per_colorway"] == 4
    assert est["trie_calls_full"] == 18 and est["est_cost"] == "1.22"
    assert est["naive_calls"] == 24 and est["saved_pct"] == 25


async def test_estimate_matches_engine_plan_exactly(authed, study_setup):
    """Endpoint == plan_study for the same shapes: labs darkest-first from the
    palette, areas from the composed slots. A raised twin_threshold (policy
    §8.6) makes rule-2 dedupe bite on a real palette (c124, min ΔE 14.7), so
    the parity covers the twin path too."""
    from app.wada import permutations as perm

    setup = study_setup
    study = await _three_slot_study(
        authed, setup, palette_id="c124", policy={"twin_threshold": 20.0}
    )
    est = (await authed.post(f"/studies/{study['id']}/estimate")).json()

    palette = (await authed.get("/palettes/c124")).json()
    colors = sorted(palette["colors"], key=lambda c: (c["lab_l"], c["id"]))
    labs = [(c["lab_l"], c["lab_a"], c["lab_b"]) for c in colors]
    areas = [
        s["area_fraction"]
        for s in (await authed.get(f"/studies/{study['id']}")).json()["slots"]
        if s["kind"] == "paint"
    ]
    plan = perm.plan_study(labs, areas, cap=12, eager=2, twin_threshold=20.0)

    assert est["perm_total"] == 6
    assert est["after_dedupe"] == plan["after_dedupe"] < 6  # twins collapsed
    assert est["planned"] == len(plan["selected"])
    assert est["planned_calls"] == plan["trie_calls_selected"]
    assert est["eager"] == len(plan["eager"])
    assert est["est_cost"] == str(perm.cost_dollars(plan["trie_calls_selected"]))


async def test_estimate_negatives(authed, study_setup):
    setup = study_setup
    r = await authed.post(f"/studies/{uuid.uuid4()}/estimate")
    assert r.status_code == 404
    # no slots yet
    study = await _create_study(authed, setup)
    r = await authed.post(f"/studies/{study['id']}/estimate")
    assert r.status_code == 422
    assert "slots" in r.json()["detail"]
    # fully anchored: nothing left to permute
    palette = (await authed.get("/palettes/c121")).json()
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[
            _slot(setup, [label], label, anchor=palette["color_ids"][i])
            for i, label in enumerate(("body", "panel", "strap"))
        ],
    )
    assert r.status_code == 200, r.text
    r = await authed.post(f"/studies/{study['id']}/estimate")
    assert r.status_code == 422
    assert "anchored" in r.json()["detail"]


async def test_put_slots_resets_stale_estimate(authed, study_setup):
    """A config change invalidates the persisted numbers until re-estimated."""
    setup = study_setup
    study = await _three_slot_study(authed, setup)
    await authed.post(f"/studies/{study['id']}/estimate")
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[_slot(setup, ["body", "panel", "strap", "buckle"], "all")],
    )
    s = r.json()
    assert s["perm_total"] is None and s["perm_planned"] is None
    assert s["est_cost_cents"] is None
