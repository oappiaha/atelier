"""M7-T2: the generation pipeline — trie executor (wada.generate), colorway
persistence, budget ledger, and the §9 lifecycle gaps.

The model is mocked at the fal boundary (app.workers.generate.call_seedream)
with a deterministic fake that fills the mask with the prompt's target hex
AND drifts every other pixel by +3 — exactly the whole-frame re-render the
M7-T1 gate measured. The per-step compositing discipline must strip that
drift (lock_verified byte-identity), so these tests prove gate conditions
1–2 structurally, not just that some image came back.

TDD-pinned numbers: K=3, C=3, full sheet → 6 colorways, 15 trie nodes
(§8.8), ledgered to exactly 101¢ = the $1.01 the estimate shows (§8.4).
Celery is bypassed: the route's .delay is captured, and tests invoke the
task function in-process against the isolated test DB/bucket.
"""

import io
import os
import re
import uuid

import numpy as np
import psycopg
import pytest
import pytest_asyncio
from PIL import Image

from tests.test_studies import (  # noqa: F401 — sanzo is a fixture import
    THREE_SLOTS,
    _create_study,
    _design_media,
    _insert_regions,
    _slot,
    sanzo,
)

FRAME_W, FRAME_H = 400, 300  # matches the region rects in test_studies


def _db():
    return psycopg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))


def _base_png() -> bytes:
    """A real 400×300 base photo (unique noise → unique sha → each study gets
    its own trie namespace; no cross-test cache pollution)."""
    rng = np.random.default_rng()
    arr = np.full((FRAME_H, FRAME_W, 3), (120, 110, 100), np.uint8)
    arr[:10, :10] = rng.integers(0, 255, (10, 10, 3), np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()


@pytest_asyncio.fixture()
async def gen_setup(sanzo, authed, design_factory, upload_media):  # noqa: F811
    """design + a REAL 400×300 base media + regions whose masks share that
    frame — the executor asserts mask/base frame agreement."""
    design, media = await _design_media_400(authed, design_factory, upload_media)
    regions = _insert_regions(media)
    return {"design": design, "media": media, "regions": regions}


async def _design_media_400(authed, design_factory, upload_media):
    design = await design_factory()
    r = await authed.post(
        "/entries", json={"design_id": design["id"], "phase": "final", "body": "gen base"}
    )
    assert r.status_code == 201, r.text
    media = await upload_media(data=_base_png(), entry_id=r.json()["id"])
    return design, media


async def _study(authed, setup, policy=None, palette_id="c121") -> dict:
    study = await _create_study(authed, setup, palette_id=palette_id, policy=policy)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[_slot(setup, labels, name) for labels, name in THREE_SLOTS],
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def fal(monkeypatch):
    """Deterministic fake at the fal boundary + captured .delay enqueues."""
    calls: list[dict] = []

    def fake_seedream(input_img, mask_img, prompt, frame):
        hexes = re.findall(r"#[0-9a-fA-F]{6}", prompt)
        assert hexes, f"prompt carries no target hex: {prompt}"
        rgb = tuple(int(hexes[-1][i : i + 2], 16) for i in (1, 3, 5))
        arr = np.asarray(input_img.convert("RGB")).astype(np.int16)
        arr = np.clip(arr + 3, 0, 255).astype(np.uint8)  # whole-frame drift
        mask = np.asarray(mask_img.convert("L")) > 127
        arr[mask] = rgb
        calls.append({"prompt": prompt, "mask_px": int(mask.sum())})
        return Image.fromarray(arr, "RGB"), 0.01

    from app.workers import generate as W

    monkeypatch.setattr(W, "call_seedream", fake_seedream)

    enqueued: list[tuple] = []
    monkeypatch.setattr(
        W.generate_study, "delay", lambda *a, **k: enqueued.append(a) or None
    )
    return {"calls": calls, "enqueued": enqueued}


def _run_enqueued(fal):
    """Execute every captured enqueue in-process (the worker's task fn)."""
    from app.workers.generate import generate_study

    results = [generate_study(*args) for args in fal["enqueued"]]
    fal["enqueued"].clear()
    return results


def _ledger(study_id):
    with _db() as conn:
        return conn.execute(
            "SELECT cost_cents, cache_hit FROM spend_ledger WHERE study_id = %s "
            "ORDER BY id",
            (study_id,),
        ).fetchall()


def _nodes(study_id=None, base_sha=None):
    with _db() as conn:
        if base_sha:
            return conn.execute(
                "SELECT * FROM gen_nodes WHERE base_sha256 = %s ORDER BY depth, created_at",
                (base_sha,),
            ).fetchall()
        return conn.execute("SELECT * FROM gen_nodes ORDER BY created_at").fetchall()


# ── auth ─────────────────────────────────────────────────────────────────────

async def test_generation_routes_require_auth(client):
    sid = uuid.uuid4()
    assert (await client.post(f"/studies/{sid}/generate", json={})).status_code == 401
    assert (await client.post(f"/studies/{sid}/generate-one", json={})).status_code == 401
    assert (await client.get(f"/studies/{sid}/colorways")).status_code == 401
    assert (await client.get("/budget")).status_code == 401
    assert (await client.patch(f"/studies/{sid}", json={})).status_code == 401
    assert (await client.delete(f"/studies/{sid}")).status_code == 401
    assert (await client.get(f"/designs/{sid}/studies")).status_code == 401


async def test_generate_unknown_study_404_and_no_slots_422(authed, gen_setup):
    assert (
        await authed.post(f"/studies/{uuid.uuid4()}/generate", json={})
    ).status_code == 404
    bare = await _create_study(authed, gen_setup)
    r = await authed.post(f"/studies/{bare['id']}/generate", json={})
    assert r.status_code == 422


# ── the eager default ────────────────────────────────────────────────────────

async def test_generate_eager_default(authed, gen_setup, fal):
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    out = r.json()
    assert len(out["requested"]) == 2  # policy default eager=2
    assert out["planned"] == 6  # K=3,C=3 bijective
    assert out["status"] == "generating"

    # §8.12: the FULL planned selection is persisted up front
    r = await authed.get(f"/studies/{study['id']}/colorways")
    cw = r.json()
    assert cw["planned"] == 6 and cw["ready"] == 0
    assert all(c["status"] in ("planned", "generating") for c in cw["colorways"])
    # eager targets are the first N in diversity order (permutation idx)
    idx_of = {c["id"]: c["permutation_idx"] for c in cw["colorways"]}
    assert sorted(idx_of[i] for i in out["requested"]) == [0, 1]

    assert len(fal["enqueued"]) == 1
    _run_enqueued(fal)

    r = await authed.get(f"/studies/{study['id']}/colorways")
    cw = r.json()
    assert cw["ready"] == 2 and cw["study_status"] == "partial"
    ready = [c for c in cw["colorways"] if c["status"] == "ready"]
    assert len(ready) == 2
    for c in ready:
        assert c["lock_verified"] is True
        assert c["image_url"] and c["thumb_url"]
        assert c["cost_cents"] > 0
        assert len(c["mapping"]) == 3  # fingerprint: one chip per slot

    # ledger == fal calls; cents reconcile with the 6.75¢ engine price
    rows = _ledger(study["id"])
    spent = [r for r in rows if not r[1]]
    assert len(spent) == len(fal["calls"])
    from app.wada.generation import alloc_cents

    expected = sum(alloc_cents(i) for i in range(len(spent)))
    assert sum(r[0] for r in spent) == expected
    with _db() as conn:
        (actual,) = conn.execute(
            "SELECT actual_cost_cents FROM studies WHERE id = %s", (study["id"],)
        ).fetchone()
    assert actual == expected


async def test_full_sheet_is_15_nodes_101_cents(authed, gen_setup, fal):
    """The §8.4/§8.8 pinned numbers: 6 colorways × 3 steps = 18 naive calls
    → 15 trie nodes → exactly $1.01 on the ledger."""
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202, r.text
    assert len(r.json()["requested"]) == 6
    _run_enqueued(fal)

    assert len(fal["calls"]) == 15  # trie sharing: 18 naive → 15
    rows = _ledger(study["id"])
    spent = [x for x in rows if not x[1]]
    assert len(spent) == 15
    assert sum(x[0] for x in spent) == 101  # reconciles with est_cost_cents

    r = await authed.get(f"/studies/{study['id']}/colorways")
    cw = r.json()
    assert cw["ready"] == 6 and cw["study_status"] == "complete"
    assert cw["actual_cost_cents"] == 101

    # per-node guards persisted (gate condition 2) + canonical §3 keys
    base_sha = _working_sha(gen_setup)
    with _db() as conn:
        (ws,) = conn.execute(
            "SELECT workspace_id FROM media WHERE id = %s",
            (gen_setup["media"]["id"],),
        ).fetchone()
        nodes = conn.execute(
            "SELECT cache_key, image_key, lock_verified, ghost_fraction, delta_e_in, "
            "depth, parent_key FROM gen_nodes WHERE base_sha256 = %s ORDER BY depth",
            (base_sha,),
        ).fetchall()
        cws = conn.execute(
            "SELECT id, image_key, thumb_key, lock_verified FROM colorways "
            "WHERE study_id = %s",
            (study["id"],),
        ).fetchall()
    assert len(nodes) == 15  # the trie IS the node table
    for key, image_key, lock, ghost, de_in, depth, parent in nodes:
        assert lock is True
        assert ghost is not None and de_in is not None
        assert (parent is None) == (depth == 1)
        assert image_key == f"node/{ws}/{key}.png"
    for cid, image_key, thumb_key, lock in cws:
        assert image_key == f"cw/{ws}/{cid}.png"
        assert thumb_key == f"cw/{ws}/{cid}.400.webp"
        assert lock is True


async def test_lock_guarantee_byte_identity(authed, gen_setup, fal):
    """§8.10 asserted on the artifact itself: the finished colorway is
    byte-identical to the working base outside the slot masks' feathered
    support — even though the fake model drifted EVERY pixel."""
    from app.config import get_settings
    from app.storage import get_s3
    from app.wada import generation as G

    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)

    s3 = get_s3()
    bucket = get_settings().s3_bucket
    with _db() as conn:
        (cw_key,) = conn.execute(
            "SELECT image_key FROM colorways WHERE study_id = %s AND status = 'ready' "
            "LIMIT 1",
            (study["id"],),
        ).fetchone()
    final = Image.open(
        io.BytesIO(s3.get_object(Bucket=bucket, Key=cw_key)["Body"].read())
    ).convert("RGB")
    base = Image.open(io.BytesIO(_png_of_media(gen_setup))).convert("RGB")

    touched = np.zeros((FRAME_H, FRAME_W), bool)
    for slot in study["slots"]:
        mask_png = s3.get_object(Bucket=bucket, Key=slot["union_mask_key"])["Body"].read()
        mask = np.asarray(Image.open(io.BytesIO(mask_png)).convert("L")) > 127
        touched |= G.touched_support(mask)
    outside = ~touched
    assert np.array_equal(np.asarray(final)[outside], np.asarray(base)[outside])
    # and the recolour actually happened inside
    assert not np.array_equal(np.asarray(final)[touched], np.asarray(base)[touched])


def _png_of_media(setup) -> bytes:
    from app.config import get_settings
    from app.storage import get_s3

    with _db() as conn:
        (r2_key,) = conn.execute(
            "SELECT r2_key FROM media WHERE id = %s", (setup["media"]["id"],)
        ).fetchone()
    return get_s3().get_object(Bucket=get_settings().s3_bucket, Key=r2_key)["Body"].read()


def _working_sha(setup) -> str:
    """The executor's base identity: sha256 of the canonical PNG encoding of
    the working image (no cutout in tests — PHOTOROOM_API_KEY is blanked)."""
    import hashlib

    from app.wada import generation as G
    from app.wada import segmentation

    img, _ = segmentation.working_image_and_alpha(_png_of_media(setup))
    return hashlib.sha256(G.png_bytes(img)).hexdigest()


async def test_darkest_first_call_order(authed, gen_setup, fal):
    """The first colorway's chain runs darkest-first (§8.8 canonical order)."""
    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)

    with _db() as conn:
        ranks = {
            hx.strip(): lab_l
            for hx, lab_l in conn.execute(
                "SELECT sc.hex, sc.lab_l FROM sanzo_colors sc "
                "JOIN palettes p ON sc.id = ANY(p.color_ids) WHERE p.id = 'c121'"
            ).fetchall()
        }
    first_chain = fal["calls"][:3]
    lightness = [
        ranks[re.findall(r"#[0-9a-fA-F]{6}", c["prompt"])[-1]] for c in first_chain
    ]
    assert lightness == sorted(lightness)


# ── cache: re-run = free ─────────────────────────────────────────────────────

async def test_regenerate_is_all_cache_hits(authed, gen_setup, fal):
    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    _run_enqueued(fal)
    n_calls = len(fal["calls"])
    assert n_calls == 15

    # colorways are ready → the default re-POST has nothing to do, no enqueue
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202
    assert r.json()["requested"] == [] and r.json()["already_ready"] == 2
    assert fal["enqueued"] == []

    # force a real replay: reset colorways to planned, keep the trie
    with _db() as conn:
        conn.execute(
            "UPDATE colorways SET status = 'planned', image_key = NULL WHERE study_id = %s",
            (study["id"],),
        )
        conn.commit()
    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202
    _run_enqueued(fal)

    assert len(fal["calls"]) == n_calls  # ZERO new model calls
    rows = _ledger(study["id"])
    assert len([x for x in rows if not x[1]]) == 15  # unchanged spend rows
    # 3 hits from the first run (18 naive − 15 nodes) + 18 replay hits, all 0¢
    assert len([x for x in rows if x[1]]) == 21
    assert all(x[0] == 0 for x in rows if x[1])
    with _db() as conn:
        actual, status = conn.execute(
            "SELECT actual_cost_cents, status FROM studies WHERE id = %s",
            (study["id"],),
        ).fetchone()
    assert actual == 101  # $0 spent on the replay
    assert status == "complete"
    r = await authed.get(f"/studies/{study['id']}/colorways")
    assert r.json()["ready"] == 6
    hit_counts = [
        c["cache_hits"] for c in r.json()["colorways"] if c["status"] == "ready"
    ]
    assert all(h == 3 for h in hit_counts)


# ── resume after crash ───────────────────────────────────────────────────────

async def test_resume_after_mid_chain_crash(authed, gen_setup, fal, monkeypatch):
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    requested = r.json()["requested"]

    from app.workers import generate as W

    real = W.call_seedream
    boom = {"n": 0}

    def crashy(*a, **k):
        boom["n"] += 1
        if boom["n"] == 2:
            raise KeyboardInterrupt("simulated worker crash mid-chain")
        return real(*a, **k)

    monkeypatch.setattr(W, "call_seedream", crashy)
    with pytest.raises(KeyboardInterrupt):
        W.generate_study(study["id"], [str(i) for i in requested])

    # one node persisted, colorway stuck 'generating', study still 'generating'
    with _db() as conn:
        (n_spent,) = conn.execute(
            "SELECT COUNT(*) FROM spend_ledger WHERE study_id = %s AND NOT cache_hit",
            (study["id"],),
        ).fetchone()
    assert n_spent == 1

    # a clean re-run resumes: node 1 is a cache hit, the rest generate
    monkeypatch.setattr(W, "call_seedream", real)
    W.generate_study(study["id"], [str(i) for i in requested])
    r = await authed.get(f"/studies/{study['id']}/colorways")
    assert r.json()["ready"] == 2
    rows = _ledger(study["id"])
    assert len([x for x in rows if x[1]]) >= 1  # the resumed prefix was a hit


# ── lock failure = failed colorway, nothing silent ───────────────────────────

async def test_lock_failure_fails_chain(authed, gen_setup, fal, monkeypatch):
    from app.wada import generation as G

    monkeypatch.setattr(G, "verify_lock", lambda *a, **k: False)
    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)

    assert len(fal["calls"]) == 2  # each eager colorway stops at step 1
    r = await authed.get(f"/studies/{study['id']}/colorways")
    cw = r.json()
    failed = [c for c in cw["colorways"] if c["status"] == "failed"]
    assert len(failed) == 2
    assert all("lock" in c["error"] for c in failed)
    assert all(c["lock_verified"] is False for c in failed)
    assert cw["study_status"] == "failed"
    # the bad node is NOT cached (no reuse of a bad artifact)…
    with _db() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM gen_nodes WHERE base_sha256 = %s",
            (_working_sha(gen_setup),),
        ).fetchone()
    assert n == 0
    # …but the spend IS ledgered — the money left regardless
    rows = _ledger(study["id"])
    assert len([x for x in rows if not x[1]]) == 2


# ── budget: §8.11 at all three tiers ─────────────────────────────────────────

async def test_route_refuses_at_each_tier(authed, gen_setup, fal, monkeypatch):
    from app.config import get_settings

    study = await _study(authed, gen_setup)

    monkeypatch.setattr(get_settings(), "budget_study_cap_cents", 5)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 402
    d = r.json()["detail"]
    assert d["error"] == "budget_cap" and d["tier"] == "study"
    assert {"cap_cents", "spent_cents", "estimated_cents", "message"} <= set(d)

    monkeypatch.setattr(get_settings(), "budget_study_cap_cents", 1000)
    monkeypatch.setattr(get_settings(), "budget_daily_cap_cents", 5)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 402
    assert r.json()["detail"]["tier"] == "daily"

    monkeypatch.setattr(get_settings(), "budget_daily_cap_cents", 2500)
    monkeypatch.setattr(get_settings(), "budget_hard_freeze_cents", 1)
    with _db() as conn:  # freeze compares against actual ledgered spend
        conn.execute(
            "INSERT INTO spend_ledger (workspace_id, study_id, kind, model_id, "
            "cost_cents) SELECT workspace_id, id, 'edit', 'seedream-5.0-pro', 2 "
            "FROM studies WHERE id = %s",
            (study["id"],),
        )
        conn.commit()
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 402
    assert r.json()["detail"]["tier"] == "freeze"
    # freeze refuses ghosts too
    r = await authed.post(
        f"/studies/{study['id']}/generate-one", json={"colorway_id": str(uuid.uuid4())}
    )
    assert r.status_code == 402


async def test_worker_stops_mid_run_at_study_cap(authed, gen_setup, fal, monkeypatch):
    """§8.11 check #3: a run crosses the cap mid-walk → stop, study 'partial',
    everything generated so far is kept."""
    from app.config import get_settings

    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202  # 6 × 6.75¢ × trie ≈ 101¢ > 30¢ would refuse…
    # …so the route gate must use a cap that admits the run, then shrink it
    monkeypatch.setattr(get_settings(), "budget_study_cap_cents", 30)
    _run_enqueued(fal)

    with _db() as conn:
        (status, error, actual) = conn.execute(
            "SELECT status, error, actual_cost_cents FROM studies WHERE id = %s",
            (study["id"],),
        ).fetchone()
    assert status == "partial"
    assert "study budget cap" in error
    assert actual <= 30
    assert len(fal["calls"]) <= 5  # 30¢ // 6.75¢
    r = await authed.get(f"/studies/{study['id']}/colorways")
    cw = r.json()
    assert cw["ready"] >= 1  # partial results kept, never rolled back
    assert all(c["status"] in ("ready", "planned") for c in cw["colorways"])


# ── ghosts (§8.13.2: pay $0.27 at a time) ───────────────────────────────────

async def test_generate_one_deferred_colorway(authed, gen_setup, fal):
    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)
    before = len(fal["calls"])

    r = await authed.get(f"/studies/{study['id']}/colorways")
    ghost = next(c for c in r.json()["colorways"] if c["status"] == "planned")
    r = await authed.post(
        f"/studies/{study['id']}/generate-one", json={"colorway_id": ghost["id"]}
    )
    assert r.status_code == 202, r.text
    assert [str(u) for u in r.json()["requested"]] == [ghost["id"]]
    _run_enqueued(fal)

    r = await authed.get(f"/studies/{study['id']}/colorways")
    got = next(c for c in r.json()["colorways"] if c["id"] == ghost["id"])
    assert got["status"] == "ready" and got["lock_verified"] is True
    assert len(fal["calls"]) - before <= 3  # ≤ one chain; prefix hits are free


async def test_generate_one_unplanned_mapping(authed, gen_setup, fal):
    """cap=2 leaves 4 of 6 perms unplanned; a ghost mapping inserts one."""
    study = await _study(authed, gen_setup, policy={"cap": 2, "eager": 1})
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)

    r = await authed.get(f"/studies/{study['id']}/colorways")
    planned_mappings = [
        tuple(chip["color_id"] for chip in c["mapping"]) for c in r.json()["colorways"]
    ]
    assert len(planned_mappings) == 2
    color_ids = sorted(set(planned_mappings[0]))
    from itertools import permutations as iperm

    unplanned = next(
        p for p in iperm(color_ids) if p not in planned_mappings
    )
    mapping = {str(i): c for i, c in enumerate(unplanned)}
    r = await authed.post(
        f"/studies/{study['id']}/generate-one", json={"mapping": mapping}
    )
    assert r.status_code == 202, r.text
    assert r.json()["planned"] == 3  # the ghost joined the sheet
    _run_enqueued(fal)
    r = await authed.get(f"/studies/{study['id']}/colorways")
    ghost = next(c for c in r.json()["colorways"] if c["permutation_idx"] == 2)
    assert ghost["status"] == "ready"


async def test_generate_one_validation(authed, gen_setup, fal):
    study = await _study(authed, gen_setup)
    sid = study["id"]
    # exactly one of colorway_id | mapping
    assert (await authed.post(f"/studies/{sid}/generate-one", json={})).status_code == 422
    # wrong slots
    r = await authed.post(f"/studies/{sid}/generate-one", json={"mapping": {"0": 1}})
    assert r.status_code == 422
    # colour outside the palette
    r = await authed.post(
        f"/studies/{sid}/generate-one", json={"mapping": {"0": 1, "1": 2, "2": 3}}
    )
    assert r.status_code == 422
    # duplicate colour in a K≤C study
    with _db() as conn:
        (ids,) = conn.execute("SELECT color_ids FROM palettes WHERE id = 'c121'").fetchone()
    dup = {"0": ids[0], "1": ids[0], "2": ids[1]}
    r = await authed.post(f"/studies/{sid}/generate-one", json={"mapping": dup})
    assert r.status_code == 422


async def test_generate_conflicts_while_generating(authed, gen_setup, fal):
    study = await _study(authed, gen_setup)
    with _db() as conn:
        conn.execute(
            "UPDATE studies SET status = 'generating' WHERE id = %s", (study["id"],)
        )
        conn.commit()
    assert (
        await authed.post(f"/studies/{study['id']}/generate", json={})
    ).status_code == 409


# ── lifecycle: PATCH / DELETE / list (§9 gaps) ──────────────────────────────

async def test_patch_swaps_palette_in_place(authed, gen_setup):
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/estimate")
    assert r.status_code == 200
    assert r.json()["est_cost_cents"] == 101

    r = await authed.patch(f"/studies/{study['id']}", json={"palette_id": "c125"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["palette_id"] == "c125"
    assert out["est_cost_cents"] is None  # stale numbers cleared
    assert out["perm_total"] is None
    # slots survived the swap — this is the point vs re-minting a draft
    assert len(out["slots"]) == 3

    # unknown palette 404; empty patch 422
    assert (
        await authed.patch(f"/studies/{study['id']}", json={"palette_id": "nope"})
    ).status_code == 404
    assert (await authed.patch(f"/studies/{study['id']}", json={})).status_code == 422


async def test_patch_policy_and_frozen_after_generate(authed, gen_setup, fal):
    study = await _study(authed, gen_setup)
    r = await authed.patch(
        f"/studies/{study['id']}", json={"policy": {"eager": 3, "cap": 8}}
    )
    assert r.status_code == 200
    assert r.json()["policy"]["eager"] == 3 and r.json()["policy"]["cap"] == 8

    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)
    r = await authed.patch(f"/studies/{study['id']}", json={"palette_id": "c125"})
    assert r.status_code == 409


async def test_delete_draft_only(authed, gen_setup, fal):
    study = await _study(authed, gen_setup)
    r = await authed.delete(f"/studies/{study['id']}")
    assert r.status_code == 204
    assert (await authed.get(f"/studies/{study['id']}")).status_code == 404

    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)
    r = await authed.delete(f"/studies/{study['id']}")
    assert r.status_code == 409


async def test_list_design_studies(authed, gen_setup):
    a = await _study(authed, gen_setup)
    b = await _create_study(authed, gen_setup, palette_id="c125")
    r = await authed.get(f"/designs/{gen_setup['design']['id']}/studies")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert b["id"] in ids and a["id"] in ids
    assert ids.index(b["id"]) < ids.index(a["id"])  # newest first
    by_id = {s["id"]: s for s in r.json()}
    assert by_id[a["id"]]["k"] == 3 and by_id[b["id"]]["k"] == 0
    assert (await authed.get(f"/designs/{uuid.uuid4()}/studies")).status_code == 404


async def test_budget_endpoint(authed):
    r = await authed.get("/budget")
    assert r.status_code == 200
    b = r.json()
    assert b["daily_cap_cents"] == 2500 and b["study_cap_cents"] == 1000
    assert b["hard_freeze_cents"] == 5000
    assert b["remaining_today_cents"] == max(0, 2500 - b["today_spent_cents"])
    assert b["frozen"] is False


# ── pure maths (no DB) ───────────────────────────────────────────────────────

def test_alloc_cents_reconciles_with_estimates():
    from app.wada.generation import alloc_cents

    for n, total in [(15, 101), (64, 432), (1, 7), (2, 14), (12, 81)]:
        assert sum(alloc_cents(i) for i in range(n)) == total


def test_cache_key_is_canonical_and_order_sensitive():
    from app.wada.generation import cache_key

    a = cache_key("b" * 64, [("#111111", "m" * 64)])
    assert a == cache_key("b" * 64, [("#111111", "m" * 64)])
    assert a != cache_key("c" * 64, [("#111111", "m" * 64)])
    assert a != cache_key("b" * 64, [("#111111", "n" * 64)])
    two = cache_key("b" * 64, [("#111111", "m" * 64), ("#222222", "n" * 64)])
    swapped = cache_key("b" * 64, [("#222222", "n" * 64), ("#111111", "m" * 64)])
    assert two != swapped  # chain ORDER is part of the identity
    assert a != cache_key("b" * 64, [("#111111", "m" * 64)], prompt_version="v9")


def test_chain_steps_groups_and_orders_darkest_first():
    from app.wada.generation import chain_steps

    darkness = {7: (20.0, 7), 8: (55.0, 8), 9: (90.0, 9)}
    # colour 8 shared by slots 0 and 2 → one step painting both
    steps = chain_steps({0: 8, 1: 9, 2: 8}, darkness)
    assert steps == [(8, (0, 2)), (9, (1,))]
    steps = chain_steps({0: 9, 1: 7, 2: 8}, darkness)
    assert [c for c, _ in steps] == [7, 8, 9]


def test_region_phrase_confidence_fallback():
    from app.wada.generation import region_phrase

    assert region_phrase(["body"], 0.95) == "body"
    assert region_phrase(["strap", "trim"], 0.8) == "strap and trim"
    assert region_phrase(["body"], 0.4) == "highlighted region"


def test_composite_guards_on_synthetic_frames():
    from app.wada import generation as G

    base = Image.new("RGB", (64, 64), (100, 100, 100))
    mask = np.zeros((64, 64), bool)
    mask[20:40, 20:40] = True
    edited = np.full((64, 64, 3), (30, 60, 90), np.uint8)  # drifts EVERYWHERE
    comp, untouched = G.composite_step(base, Image.fromarray(edited), mask)
    assert G.verify_lock(comp, base, untouched) is True
    # a doctored composite must fail the lock
    bad = np.asarray(comp).copy()
    bad[0, 0] = (255, 0, 0)
    assert G.verify_lock(Image.fromarray(bad), base, untouched) is False
    # ghost: the band was fully recoloured → ~0; an unchanged comp → 1.0
    assert G.ghost_fraction(comp, base, mask) < 0.2
    assert G.ghost_fraction(base, base, mask) == 1.0
    assert G.unusable(Image.new("RGB", (8, 8), (5, 5, 5))) is True
    assert G.unusable(comp) is False
