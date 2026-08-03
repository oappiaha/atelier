"""Wada study lifecycle (TDD §9): create a study, compose slots, estimate.

POST /studies                  Create draft: design, base media, palette.
GET  /studies/{id}             Study + slots read-back. (Not in the §9 route
                               table, but the Slot Composer (M5-T3) must be
                               able to reload a draft; documented as additive.)
PUT  /studies/{id}/slots       Set the slot config. Recomputes union masks.
POST /studies/{id}/estimate    Pure. Returns perm_total, planned, cost, eta.
                               NO SPEND — §9 calls it the most important
                               endpoint in Wada.

All numbers come from the tested engine (app.wada.permutations): the §8.4
regime table, §8.5 pruning (anchors via rule 1, twins via rule 2, the cap via
rule 4), §8.7 ranking + diversity, §8.8 trie call counting. Colour indices
are darkness ranks (0 = darkest, L* ascending) — the canonical chain order.

Documented decisions (the TDD is silent or amended):
- model_id 'seedream-5.0-pro' (M0 decision), prompt_version 'v1'.
- eta uses the M0-measured Seedream latency (~39s/edit), not the §8.13.1
  mock's ~10s/call. M0 findings amend §8 (PROGRESS.md).
- base media must belong to the study's design (422 otherwise) and be an
  image: a study renders under its design, so a foreign base image would be
  incoherent. §9 doesn't state it; enforced as a semantic guard.
- slots idx canonical order: paint slots by area desc first (so paint idx are
  contiguous 0..K-1 — mapping/anchor keys depend on that), then locks by
  area desc. §2.4 pins "canonical order = area desc".
- PUT /slots mirrors per-slot anchors into policy.anchors (§8.6 shape) and
  clears any stale estimate columns.
- §8.5 rule 3 (cosmetic collapse) is not in the engine yet; regions carry
  is_cosmetic and the policy carries prune_cosmetic, but estimates do not
  collapse cosmetic slots. Noted as an M5 gap.
"""

import hashlib
import io
import json
import math
import uuid
from datetime import datetime
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth import Ctx, get_ctx
from app.db import get_session
from app.storage import get_s3, presign_get
from app.wada import generation as G
from app.wada import permutations as perm
from app.wada.generation import MODEL_ID, PROMPT_VERSION

router = APIRouter(tags=["studies"])

SECONDS_PER_CALL = 39  # M0-measured Seedream edit latency
MAX_PAINT_SLOTS = 8  # pragmatic ceiling; §8.3's cautionary tale stops at K=6

DEFAULT_POLICY = {  # §8.6 defaults, verbatim
    "mode": "curated",
    "cap": perm.DEFAULT_CAP,
    "anchors": {},
    "prune_twins": True,
    "prune_cosmetic": True,
    "twin_threshold": perm.DEFAULT_TWIN_THRESHOLD,
    "diversity": True,
    "eager": perm.DEFAULT_EAGER,
    "resolution": 1536,
}


# ── models ───────────────────────────────────────────────────────────────────

class PolicyIn(BaseModel):
    """Partial §8.6 policy override; merged over DEFAULT_POLICY."""

    mode: Literal["all", "curated", "sampled"] | None = None
    cap: int | None = Field(default=None, ge=1, le=100)
    prune_twins: bool | None = None
    prune_cosmetic: bool | None = None
    twin_threshold: float | None = Field(default=None, ge=0.0, le=50.0)
    diversity: bool | None = None
    eager: int | None = Field(default=None, ge=0, le=12)
    resolution: Literal[1024, 1536, 2048] | None = None


class StudyCreate(BaseModel):
    design_id: uuid.UUID
    base_media_id: uuid.UUID
    palette_id: str = Field(min_length=1, max_length=16)
    policy: PolicyIn | None = None


class SlotIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["paint", "lock"] = "paint"
    region_ids: list[uuid.UUID] = Field(min_length=1)
    anchor_color_id: int | None = None


class SlotOut(BaseModel):
    id: uuid.UUID
    idx: int
    label: str
    kind: str
    region_ids: list[uuid.UUID]
    union_mask_key: str
    union_mask_url: str
    union_sha256: str
    area_fraction: float
    anchor_color_id: int | None


class StudyOut(BaseModel):
    id: uuid.UUID
    design_id: uuid.UUID
    base_media_id: uuid.UUID
    palette_id: str
    status: str
    policy: dict
    perm_total: int | None
    perm_planned: int | None
    est_cost_cents: int | None
    actual_cost_cents: int
    model_id: str
    prompt_version: str
    created_at: datetime
    k: int  # count of paint slots
    slots: list[SlotOut]


class EstimateOut(BaseModel):
    """Everything the §8.13.1 readout line shows, from the tested engine."""

    k: int  # paint slots (before anchors)
    c: int  # palette colours (before anchors)
    anchors: int  # §8.5 rule 1: effective space is (k-anchors, c-anchors)
    regime: Literal["bijective", "injective", "surjective"]
    perm_total: int  # full space after anchors, before dedupe/cap
    after_dedupe: int  # after §8.5 rule 2 twin collapse
    planned: int  # what generate would actually produce (≤ cap)
    steps_per_colorway: int
    naive_calls: int
    naive_cost: str  # dollars, '7.29'
    trie_calls_full: int  # §8.8 trie over the FULL space
    trie_cost_full: str
    saved_pct: int
    planned_calls: int  # trie nodes over the PLANNED (capped) selection
    est_cost: str  # dollars for planned_calls — the '~$1.01' number
    est_cost_cents: int
    eager: int
    eta_seconds: int
    cap: int
    cap_exceeded: bool
    cap_message: str | None  # §8.13.1 amber copy
    message: str  # §8.13.1 / PRD §4.2 plain-language regime copy


# ── helpers ──────────────────────────────────────────────────────────────────

async def _study_or_404(study_id: uuid.UUID, ctx: Ctx, session: AsyncSession):
    row = (
        await session.execute(
            text("SELECT * FROM studies WHERE workspace_id = :ws AND id = :id"),
            {"ws": str(ctx.workspace_id), "id": str(study_id)},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "study not found")
    return row


async def _slots_for(study_id: uuid.UUID, session: AsyncSession) -> list[SlotOut]:
    rows = await session.execute(
        text("SELECT * FROM slots WHERE study_id = :id ORDER BY idx"),
        {"id": str(study_id)},
    )
    return [
        SlotOut(
            id=r.id, idx=r.idx, label=r.label, kind=r.kind,
            region_ids=list(r.region_ids), union_mask_key=r.union_mask_key,
            union_mask_url=presign_get(r.union_mask_key), union_sha256=r.union_sha256,
            area_fraction=r.area_fraction, anchor_color_id=r.anchor_color_id,
        )
        for r in rows
    ]


async def _study_out(row, session: AsyncSession) -> StudyOut:
    slots = await _slots_for(row.id, session)
    policy = row.policy if isinstance(row.policy, dict) else json.loads(row.policy)
    return StudyOut(
        id=row.id, design_id=row.design_id, base_media_id=row.base_media_id,
        palette_id=row.palette_id, status=row.status, policy=policy,
        perm_total=row.perm_total, perm_planned=row.perm_planned,
        est_cost_cents=row.est_cost_cents, actual_cost_cents=row.actual_cost_cents,
        model_id=row.model_id, prompt_version=row.prompt_version,
        created_at=row.created_at,
        k=sum(1 for s in slots if s.kind == "paint"), slots=slots,
    )


async def _palette_or_404(palette_id: str, session: AsyncSession):
    row = (
        await session.execute(
            text("SELECT * FROM palettes WHERE id = :id"), {"id": palette_id}
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "palette not found")
    return row


async def compute_plan(study_row, session: AsyncSession) -> dict:
    """The §8.4→§8.7 plan for a study, shared verbatim by POST /estimate and
    POST /generate (M7): the generate route MUST persist exactly the
    permutations the estimate showed, or the user paid for a different sheet
    than they approved.

    Returns the engine plan plus the pieces generate needs to persist rows:
    `mappings` — one {slot_idx: sanzo_color_id} per selected assignment, in
    diversity order (eager-first), anchored slots included — and the score/
    signature per assignment for the permutations columns."""
    slots = await _slots_for(study_row.id, session)
    paint = [s for s in slots if s.kind == "paint"]
    if not paint:
        raise HTTPException(422, "configure slots before estimating")

    palette = await _palette_or_404(study_row.palette_id, session)
    colors = (
        await session.execute(
            text(
                """
                SELECT id, lab_l, lab_a, lab_b FROM sanzo_colors
                WHERE id = ANY(CAST(:ids AS int[]))
                """
            ),
            {"ids": list(palette.color_ids)},
        )
    ).all()

    policy = (
        study_row.policy
        if isinstance(study_row.policy, dict)
        else json.loads(study_row.policy)
    )
    k, c = len(paint), palette.color_count
    anchored = [s for s in paint if s.anchor_color_id is not None]
    k_eff, c_eff = perm.apply_anchors(k, c, len(anchored))
    if k_eff < 1 or c_eff < 1:
        raise HTTPException(
            422, "every slot or colour is anchored — nothing left to permute"
        )

    # colour indices are darkness ranks: 0 = darkest (L* ascending, id tiebreak)
    anchored_ids = {s.anchor_color_id for s in anchored}
    free_colors = [
        col
        for col in sorted(colors, key=lambda col: (col.lab_l, col.id))
        if col.id not in anchored_ids
    ]
    labs = [(col.lab_l, col.lab_a, col.lab_b) for col in free_colors]
    free_slots = [s for s in paint if s.anchor_color_id is None]  # idx order
    areas = [s.area_fraction for s in free_slots]

    twin_threshold = (
        policy["twin_threshold"] if policy.get("prune_twins", True) else None
    )
    plan = perm.plan_study(
        labs, areas, cap=policy["cap"], eager=policy["eager"],
        twin_threshold=twin_threshold,
    )

    mappings: list[dict[int, int]] = []
    scores: list[float] = []
    signatures: list[tuple[float, ...]] = []
    for a in plan["selected"]:
        mapping = {s.idx: s.anchor_color_id for s in anchored}
        for pos, rank_i in enumerate(a):
            mapping[free_slots[pos].idx] = free_colors[rank_i].id
        mappings.append(mapping)
        scores.append(perm.score(a, labs, areas))
        signatures.append(perm.signature(a, labs, areas))

    # Anchored maths (M8 fix): the executor paints anchors as REAL chain
    # steps interleaved by darkness, so trie-call counts must come from the
    # FULL mappings (anchors included) — plan_study()'s free-space count
    # undercounts anchored studies. darkness spans the whole palette.
    darkness = {col.id: (col.lab_l, col.id) for col in colors}

    return {
        "slots": slots,
        "paint": paint,
        "palette": palette,
        "policy": policy,
        "k": k,
        "c": c,
        "anchored": anchored,
        "k_eff": k_eff,
        "c_eff": c_eff,
        "plan": plan,
        "mappings": mappings,
        "scores": scores,
        "signatures": signatures,
        "free_slots": free_slots,
        "free_colors": free_colors,
        "darkness": darkness,
        # authoritative model-call count for the PLANNED sheet — matches the
        # executor's chain walk exactly (anchor steps included)
        "planned_calls": G.trie_calls(mappings, darkness),
    }


def _union_mask_key(workspace_id: str, media_sha: str, union_sha: str) -> str:
    """TDD §3 key layout: mask/{ws}/{media_sha}/union/{sha}.png."""
    return f"mask/{workspace_id}/{media_sha}/union/{union_sha}.png"


def _compute_unions(
    workspace_id: str,
    media_sha: str,
    slot_mask_keys: list[list[str]],
    bucket: str,
) -> list[tuple[str, str, bytes]]:
    """For each slot (a list of member region mask keys): download the member
    masks, OR them into one union mask, PNG-encode, and upload to the §3 key.
    Returns (union_key, union_sha256, png_bytes) per slot. Sync — call via
    threadpool. Idempotent: identical groupings hit identical keys (§8.8:
    'Two different studies that happen to group the same regions the same
    way hit the same cache. This is free money.')."""
    s3 = get_s3()
    cache: dict[str, np.ndarray] = {}

    def load(key: str) -> np.ndarray:
        if key not in cache:
            png = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            cache[key] = np.asarray(Image.open(io.BytesIO(png)).convert("L")) > 127
        return cache[key]

    out: list[tuple[str, str, bytes]] = []
    for keys in slot_mask_keys:
        union = load(keys[0]).copy()
        for key in keys[1:]:
            member = load(key)
            if member.shape != union.shape:
                raise HTTPException(500, "region masks disagree on frame size")
            union |= member
        buf = io.BytesIO()
        Image.fromarray(union.astype(np.uint8) * 255, "L").save(
            buf, format="PNG", optimize=True
        )
        png = buf.getvalue()
        sha = hashlib.sha256(png).hexdigest()
        key = _union_mask_key(workspace_id, media_sha, sha)
        s3.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
        out.append((key, sha, png))
    return out


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("/studies", response_model=StudyOut, status_code=201)
async def create_study(
    body: StudyCreate,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> StudyOut:
    design = (
        await session.execute(
            text("SELECT id FROM designs WHERE workspace_id = :ws AND id = :id"),
            {"ws": str(ctx.workspace_id), "id": str(body.design_id)},
        )
    ).one_or_none()
    if design is None:
        raise HTTPException(404, "design not found")

    media = (
        await session.execute(
            text("SELECT id, kind, design_id FROM media WHERE workspace_id = :ws AND id = :id"),
            {"ws": str(ctx.workspace_id), "id": str(body.base_media_id)},
        )
    ).one_or_none()
    if media is None:
        raise HTTPException(404, "media not found")
    if media.kind != "image":
        raise HTTPException(422, f"a study needs an image base, not {media.kind}")
    if media.design_id != body.design_id:
        raise HTTPException(422, "base media does not belong to the design")

    await _palette_or_404(body.palette_id, session)

    policy = dict(DEFAULT_POLICY)
    if body.policy is not None:
        policy |= body.policy.model_dump(exclude_none=True)
    if policy["eager"] > policy["cap"]:
        raise HTTPException(422, "policy.eager cannot exceed policy.cap")

    row = (
        await session.execute(
            text(
                """
                INSERT INTO studies (workspace_id, design_id, base_media_id, palette_id,
                                     policy, model_id, prompt_version)
                VALUES (:ws, :design_id, :media_id, :palette_id,
                        CAST(:policy AS jsonb), :model_id, :prompt_version)
                RETURNING *
                """
            ),
            {
                "ws": str(ctx.workspace_id), "design_id": str(body.design_id),
                "media_id": str(body.base_media_id), "palette_id": body.palette_id,
                "policy": json.dumps(policy), "model_id": MODEL_ID,
                "prompt_version": PROMPT_VERSION,
            },
        )
    ).one()
    await session.commit()
    return await _study_out(row, session)


@router.get("/studies/{study_id}", response_model=StudyOut)
async def get_study(
    study_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> StudyOut:
    row = await _study_or_404(study_id, ctx, session)
    return await _study_out(row, session)


@router.put("/studies/{study_id}/slots", response_model=StudyOut)
async def set_slots(
    study_id: uuid.UUID,
    body: list[SlotIn],
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> StudyOut:
    study = await _study_or_404(study_id, ctx, session)
    if study.status != "draft":
        raise HTTPException(409, f"slots are frozen once a study is {study.status}")
    if not body:
        raise HTTPException(422, "at least one slot is required")

    paint = [s for s in body if s.kind == "paint"]
    if not paint:
        raise HTTPException(422, "at least one paint slot is required")
    if len(paint) > MAX_PAINT_SLOTS:
        raise HTTPException(422, f"at most {MAX_PAINT_SLOTS} paint slots are supported")

    all_ids = [rid for s in body for rid in s.region_ids]
    if len(all_ids) != len(set(all_ids)):
        raise HTTPException(422, "a region cannot appear in more than one slot")

    rows = (
        await session.execute(
            text(
                """
                SELECT id, media_id, mask_key, area_fraction FROM regions
                WHERE workspace_id = :ws AND id = ANY(CAST(:ids AS uuid[]))
                """
            ),
            {"ws": str(ctx.workspace_id), "ids": [str(i) for i in all_ids]},
        )
    ).all()
    regions = {r.id: r for r in rows}
    missing = [str(i) for i in all_ids if i not in regions]
    if missing:
        raise HTTPException(404, f"region not found: {missing[0]}")
    foreign = [r for r in regions.values() if r.media_id != study.base_media_id]
    if foreign:
        raise HTTPException(422, "all regions must belong to the study's base media")

    # anchors: paint-only, distinct, members of the study's palette (§8.5 rule 1)
    palette = await _palette_or_404(study.palette_id, session)
    anchor_colors = [s.anchor_color_id for s in body if s.anchor_color_id is not None]
    if any(s.kind == "lock" and s.anchor_color_id is not None for s in body):
        raise HTTPException(422, "lock slots cannot carry an anchor colour")
    if len(anchor_colors) != len(set(anchor_colors)):
        raise HTTPException(422, "two slots cannot anchor the same colour")
    if any(c not in palette.color_ids for c in anchor_colors):
        raise HTTPException(422, "anchor colour is not in the study's palette")

    # canonical order (§2.4: area desc): paint first — keeps paint idx
    # contiguous 0..K-1, which mapping/anchor keys depend on — then locks
    def area(s: SlotIn) -> float:
        return sum(regions[rid].area_fraction for rid in s.region_ids)

    ordered = sorted(paint, key=lambda s: (-area(s), s.label)) + sorted(
        (s for s in body if s.kind == "lock"), key=lambda s: (-area(s), s.label)
    )

    media_sha = (
        await session.execute(
            text("SELECT sha256 FROM media WHERE id = :id"),
            {"id": str(study.base_media_id)},
        )
    ).scalar_one()

    from app.config import get_settings

    unions = await run_in_threadpool(
        _compute_unions,
        str(ctx.workspace_id),
        media_sha,
        [[regions[rid].mask_key for rid in s.region_ids] for s in ordered],
        get_settings().s3_bucket,
    )

    await session.execute(
        text("DELETE FROM slots WHERE study_id = :id"), {"id": str(study.id)}
    )
    for idx, (slot, (union_key, union_sha, _)) in enumerate(zip(ordered, unions)):
        await session.execute(
            text(
                """
                INSERT INTO slots (study_id, idx, label, kind, region_ids,
                                   union_mask_key, union_sha256, area_fraction,
                                   anchor_color_id)
                VALUES (:study_id, :idx, :label, :kind, CAST(:region_ids AS uuid[]),
                        :union_key, :union_sha, :area, :anchor)
                """
            ),
            {
                "study_id": str(study.id), "idx": idx, "label": slot.label,
                "kind": slot.kind, "region_ids": [str(r) for r in slot.region_ids],
                "union_key": union_key, "union_sha": union_sha, "area": area(slot),
                "anchor": slot.anchor_color_id,
            },
        )

    # mirror anchors into the §8.6 policy object and drop any stale estimate
    policy = study.policy if isinstance(study.policy, dict) else json.loads(study.policy)
    policy["anchors"] = {
        str(idx): slot.anchor_color_id
        for idx, slot in enumerate(ordered)
        if slot.anchor_color_id is not None
    }
    await session.execute(
        text(
            """
            UPDATE studies SET policy = CAST(:policy AS jsonb),
                   perm_total = NULL, perm_planned = NULL, est_cost_cents = NULL
            WHERE id = :id
            """
        ),
        {"policy": json.dumps(policy), "id": str(study.id)},
    )
    await session.commit()
    row = await _study_or_404(study_id, ctx, session)
    return await _study_out(row, session)


@router.post("/studies/{study_id}/estimate", response_model=EstimateOut)
async def estimate_study(
    study_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> EstimateOut:
    study = await _study_or_404(study_id, ctx, session)
    p = await compute_plan(study, session)
    policy, plan = p["policy"], p["plan"]
    k, c, k_eff, c_eff, anchored = p["k"], p["c"], p["k_eff"], p["c_eff"], p["anchored"]
    est = perm.estimate(k_eff, c_eff, cap=policy["cap"])

    # Anchored maths (M8 fix, flagged in M7-T2): the executor paints anchored
    # slots as real chain steps, so every call/cost figure counts them. With
    # zero anchors all of these reduce to the pure-engine numbers.
    n_anch = len(anchored)
    steps = est["steps_per_colorway"] + n_anch
    naive_calls = est["perms"] * steps
    if n_anch:
        anchor_map = {s.idx: s.anchor_color_id for s in anchored}
        free_slots, free_colors = p["free_slots"], p["free_colors"]
        full_mappings = [
            anchor_map
            | {free_slots[pos].idx: free_colors[rank].id for pos, rank in enumerate(a)}
            for a in perm.enumerate_assignments(k_eff, c_eff)
        ]
        trie_full = G.trie_calls(full_mappings, p["darkness"])
    else:
        trie_full = est["trie_calls"]

    planned = len(plan["selected"])
    planned_calls = p["planned_calls"]  # full-mapping trie, anchors included
    est_cost = perm.cost_dollars(planned_calls)
    est_cost_cents = int(est_cost * 100)

    regime = (
        "bijective" if k_eff == c_eff else "injective" if k_eff < c_eff else "surjective"
    )
    perms = est["perms"]
    if regime == "surjective":  # §8.13.1 copy, verbatim shape
        message = (
            f"You have {k_eff} slots and a {c_eff}-colour palette, so slots will "
            f"share colours ({perms} permutations). Pick a {k_eff}-colour palette "
            f"for a clean one-to-one mapping ({math.factorial(k_eff)}), or merge "
            f"two slots ({perm.perm_count(k_eff - 1, c_eff)})."
        )
    elif regime == "injective":
        message = (
            f"You have {k_eff} slots and a {c_eff}-colour palette, so not every "
            f"colour will be used — the engine chooses which ({perms} permutations)."
        )
    else:
        message = (
            f"You have {k_eff} slots and a {c_eff}-colour palette — a clean "
            f"one-to-one mapping ({perms} permutations)."
        )

    cap_exceeded = perms > policy["cap"]
    cap_message = (
        (
            f"{perms} permutations exceeds the cap of {policy['cap']}. The "
            f"{policy['cap']} strongest and most varied will be generated "
            f"(~${perm.cost_dollars(min(perms, policy['cap']) * steps)}). "
            f"Merge slots or anchor a colour to explore the full space."
        )
        if cap_exceeded
        else None
    )

    await session.execute(
        text(
            """
            UPDATE studies SET perm_total = :total, perm_planned = :planned,
                   est_cost_cents = :cents
            WHERE id = :id
            """
        ),
        {"total": perms, "planned": planned, "cents": est_cost_cents, "id": str(study.id)},
    )
    await session.commit()

    return EstimateOut(
        k=k, c=c, anchors=len(anchored), regime=regime,
        perm_total=perms, after_dedupe=plan["after_dedupe"], planned=planned,
        steps_per_colorway=steps,
        naive_calls=naive_calls, naive_cost=str(perm.cost_dollars(naive_calls)),
        trie_calls_full=trie_full, trie_cost_full=str(perm.cost_dollars(trie_full)),
        saved_pct=round((1 - trie_full / naive_calls) * 100),
        planned_calls=planned_calls, est_cost=str(est_cost), est_cost_cents=est_cost_cents,
        eager=len(plan["eager"]), eta_seconds=planned_calls * SECONDS_PER_CALL,
        cap=policy["cap"], cap_exceeded=cap_exceeded, cap_message=cap_message,
        message=message,
    )


# ── lifecycle (M7-T2: the §9 gaps) ───────────────────────────────────────────
#
# PATCH /studies/{id}      palette swap / policy update IN PLACE — kills the
#                          Composer's mint-a-new-draft-per-palette workaround
#                          (M7-T3 must switch the palette browser to PATCH).
# DELETE /studies/{id}     draft-only; generated studies are spend records.
# GET /designs/{id}/studies  the list the §9 table implies but never states.


class StudyPatch(BaseModel):
    palette_id: str | None = Field(default=None, min_length=1, max_length=16)
    policy: PolicyIn | None = None


class StudySummary(BaseModel):
    id: uuid.UUID
    palette_id: str
    status: str
    k: int
    perm_total: int | None
    perm_planned: int | None
    est_cost_cents: int | None
    actual_cost_cents: int
    created_at: datetime
    completed_at: datetime | None


@router.patch("/studies/{study_id}", response_model=StudyOut)
async def patch_study(
    study_id: uuid.UUID,
    body: StudyPatch,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> StudyOut:
    study = await _study_or_404(study_id, ctx, session)
    if study.status != "draft":
        raise HTTPException(
            409, f"a {study.status} study is frozen — palette/policy are part of "
            "what was generated; create a new study instead"
        )
    if body.palette_id is None and body.policy is None:
        raise HTTPException(422, "nothing to update")

    policy = study.policy if isinstance(study.policy, dict) else json.loads(study.policy)
    palette_id = study.palette_id
    if body.palette_id is not None:
        palette = await _palette_or_404(body.palette_id, session)
        anchor_ids = [
            r.anchor_color_id
            for r in (
                await session.execute(
                    text(
                        "SELECT anchor_color_id FROM slots "
                        "WHERE study_id = :id AND anchor_color_id IS NOT NULL"
                    ),
                    {"id": str(study.id)},
                )
            ).all()
        ]
        stale = [a for a in anchor_ids if a not in palette.color_ids]
        if stale:
            raise HTTPException(
                422, "an anchored colour is not in the new palette — clear the "
                "anchor first"
            )
        palette_id = body.palette_id
    if body.policy is not None:
        policy |= body.policy.model_dump(exclude_none=True)
        if policy["eager"] > policy["cap"]:
            raise HTTPException(422, "policy.eager cannot exceed policy.cap")

    # any change invalidates the shown numbers — re-estimate before generating
    await session.execute(
        text(
            """
            UPDATE studies SET palette_id = :palette_id,
                   policy = CAST(:policy AS jsonb),
                   perm_total = NULL, perm_planned = NULL, est_cost_cents = NULL
            WHERE id = :id
            """
        ),
        {"palette_id": palette_id, "policy": json.dumps(policy), "id": str(study.id)},
    )
    await session.commit()
    row = await _study_or_404(study_id, ctx, session)
    return await _study_out(row, session)


@router.delete("/studies/{study_id}", status_code=204)
async def delete_study(
    study_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> None:
    study = await _study_or_404(study_id, ctx, session)
    if study.status != "draft":
        raise HTTPException(
            409, f"only drafts can be deleted (this study is {study.status}); "
            "generated colorways are spend records"
        )
    await session.execute(
        text("DELETE FROM studies WHERE id = :id"), {"id": str(study.id)}
    )
    await session.commit()


@router.post("/studies/{study_id}/duplicate", response_model=StudyOut, status_code=201)
async def duplicate_study(
    study_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> StudyOut:
    """SHIP-1 "duplicate & tweak": a generated study is frozen (palette/slots/
    policy are part of what was paid for), so iterating on it means a NEW
    draft. This copies the study's whole config — base media, palette, policy
    (anchors mirror included) and every slot row verbatim (region groupings,
    locks, anchors, union masks — the §3 union keys are content-addressed and
    deliberately shared: "two studies that group the same regions the same
    way hit the same cache") — into a fresh draft on the same design.

    Estimate columns start NULL (the §8.13.1 numbers are recomputed on the
    first estimate, and must match the source's while the config is untouched
    — pinned by test). model_id/prompt_version are stamped to the CURRENT
    versions, not the source's: the new study will be generated with today's
    template. Works on a study of any status; drafts too (a cheap fork)."""
    src = await _study_or_404(study_id, ctx, session)
    policy = src.policy if isinstance(src.policy, dict) else json.loads(src.policy)

    row = (
        await session.execute(
            text(
                """
                INSERT INTO studies (workspace_id, design_id, base_media_id, palette_id,
                                     policy, model_id, prompt_version)
                VALUES (:ws, :design_id, :media_id, :palette_id,
                        CAST(:policy AS jsonb), :model_id, :prompt_version)
                RETURNING *
                """
            ),
            {
                "ws": str(ctx.workspace_id), "design_id": str(src.design_id),
                "media_id": str(src.base_media_id), "palette_id": src.palette_id,
                "policy": json.dumps(policy), "model_id": MODEL_ID,
                "prompt_version": PROMPT_VERSION,
            },
        )
    ).one()
    await session.execute(
        text(
            """
            INSERT INTO slots (study_id, idx, label, kind, region_ids,
                               union_mask_key, union_sha256, area_fraction,
                               anchor_color_id)
            SELECT :new_id, idx, label, kind, region_ids,
                   union_mask_key, union_sha256, area_fraction, anchor_color_id
            FROM slots WHERE study_id = :src_id
            """
        ),
        {"new_id": str(row.id), "src_id": str(src.id)},
    )
    await session.commit()
    return await _study_out(row, session)


# ── workspace-wide Studies gallery ───────────────────────────────────────────
#
# GET /studies — every study in the workspace, across all designs, newest
# first: the browse-everything view the per-design list cannot give. Additive
# (§9's table stops at the per-design list). Each row carries what a gallery
# card needs — design name, base-media thumb, palette (name + member hexes),
# status, colorway counts, spend — plus a hero image: the first PINNED
# colorway's thumb (permutation order), else the first ready one, else null
# (the card falls back to palette blocks).


class StudyGalleryOut(BaseModel):
    id: uuid.UUID
    design_id: uuid.UUID
    design_name: str
    base_thumb_url: str | None  # base media thumb (null until thumbs land)
    palette_id: str
    palette_name: str
    palette_hexes: list[str]  # member colours, palette (color_ids) order
    status: str
    created_at: datetime
    planned: int  # colorway rows; falls back to perm_planned pre-generate
    ready: int  # ready + pinned (the ColorwaysOut 'ready' convention)
    pinned: int
    actual_cost_cents: int
    hero_thumb_url: str | None  # first pinned thumb, else first ready, else null


@router.get("/studies", response_model=list[StudyGalleryOut])
async def list_workspace_studies(
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[StudyGalleryOut]:
    rows = (
        await session.execute(
            text(
                """
                SELECT s.id, s.design_id, d.name AS design_name,
                       m.thumb_key AS base_thumb_key,
                       s.palette_id, pal.name AS palette_name, pal.color_ids,
                       s.status, s.created_at, s.perm_planned, s.actual_cost_cents,
                       cw.total AS cw_total, cw.ready AS cw_ready,
                       cw.pinned AS cw_pinned,
                       hero.thumb_key AS hero_thumb_key
                FROM studies s
                JOIN designs d ON d.id = s.design_id
                JOIN media m ON m.id = s.base_media_id
                JOIN palettes pal ON pal.id = s.palette_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE c.status IN ('ready', 'pinned'))
                               AS ready,
                           COUNT(*) FILTER (WHERE c.status = 'pinned') AS pinned
                    FROM colorways c WHERE c.study_id = s.id
                ) cw ON true
                LEFT JOIN LATERAL (
                    SELECT c.thumb_key
                    FROM colorways c
                    JOIN permutations p ON p.id = c.permutation_id
                    WHERE c.study_id = s.id
                          AND c.status IN ('pinned', 'ready')
                          AND c.thumb_key IS NOT NULL
                    ORDER BY (c.status <> 'pinned'), p.idx
                    LIMIT 1
                ) hero ON true
                WHERE s.workspace_id = :ws
                ORDER BY s.created_at DESC
                """
            ),
            {"ws": str(ctx.workspace_id)},
        )
    ).all()

    color_ids = sorted({cid for r in rows for cid in r.color_ids})
    hexes: dict[int, str] = {}
    if color_ids:
        hexes = {
            r.id: r.hex.strip()
            for r in (
                await session.execute(
                    text("SELECT id, hex FROM sanzo_colors WHERE id = ANY(CAST(:ids AS int[]))"),
                    {"ids": color_ids},
                )
            ).all()
        }

    return [
        StudyGalleryOut(
            id=r.id, design_id=r.design_id, design_name=r.design_name,
            base_thumb_url=presign_get(r.base_thumb_key) if r.base_thumb_key else None,
            palette_id=r.palette_id, palette_name=r.palette_name,
            palette_hexes=[hexes[cid] for cid in r.color_ids],
            status=r.status, created_at=r.created_at,
            # pre-generate there are no colorway rows — the estimate's planned
            # count (if any) is the honest 'M' for the card's N/M line
            planned=r.cw_total or r.perm_planned or 0,
            ready=r.cw_ready, pinned=r.cw_pinned,
            actual_cost_cents=r.actual_cost_cents,
            hero_thumb_url=presign_get(r.hero_thumb_key) if r.hero_thumb_key else None,
        )
        for r in rows
    ]


@router.get("/designs/{design_id}/studies", response_model=list[StudySummary])
async def list_design_studies(
    design_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[StudySummary]:
    design = (
        await session.execute(
            text("SELECT id FROM designs WHERE workspace_id = :ws AND id = :id"),
            {"ws": str(ctx.workspace_id), "id": str(design_id)},
        )
    ).one_or_none()
    if design is None:
        raise HTTPException(404, "design not found")
    rows = (
        await session.execute(
            text(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM slots
                        WHERE study_id = s.id AND kind = 'paint') AS k
                FROM studies s
                WHERE s.workspace_id = :ws AND s.design_id = :id
                ORDER BY s.created_at DESC
                """
            ),
            {"ws": str(ctx.workspace_id), "id": str(design_id)},
        )
    ).all()
    return [
        StudySummary(
            id=r.id, palette_id=r.palette_id, status=r.status, k=r.k,
            perm_total=r.perm_total, perm_planned=r.perm_planned,
            est_cost_cents=r.est_cost_cents, actual_cost_cents=r.actual_cost_cents,
            created_at=r.created_at, completed_at=r.completed_at,
        )
        for r in rows
    ]
