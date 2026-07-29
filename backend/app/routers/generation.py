"""Wada generation routes (TDD §9, per §8.11/§8.12):

POST /studies/{id}/generate      Enqueue the trie executor. 202. Default
                                 scope = the policy's eager-N colorways in
                                 diversity order; body may request specific
                                 colorway ids or the full planned sheet.
POST /studies/{id}/generate-one  Generate a single ghost permutation — a
                                 deferred planned colorway by id, or an
                                 unplanned mapping from the browsable space
                                 (§8.13.2: "$0.27 at a time").
GET  /studies/{id}/colorways     Contact-sheet projection: statuses, image/
                                 thumb URLs, fingerprint mapping, per-cw cost.
GET  /budget                     Today's spend, caps, remaining.

Budget refusals are 402 with a structured detail (§9 defines no shape; this
one carries tier + numbers so the Composer can render the exact copy):
    {"error": "budget_cap", "tier": "study|daily|freeze",
     "cap_cents": n, "spent_cents": n, "estimated_cents": n, "message": str}

The enqueue-time check (§8.11 point 2) prices the request as trie-nodes ×
6.75¢ WITHOUT subtracting cache hits (the route cannot know them cheaply) —
a conservative overestimate; the worker's per-node check is exact.

First POST /generate persists the permutations + colorways rows for the FULL
planned selection (§8.12) — exactly the plan the estimate showed — so the
contact sheet can render deferred colorways as ghost cards immediately.
prompt_version is stamped to the current template version at that moment.
"""

import json
import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx
from app.config import get_settings
from app.db import get_session
from app.routers.studies import _study_or_404, compute_plan
from app.storage import presign_get
from app.wada import generation as G
from app.wada import permutations as perm

router = APIRouter(tags=["generation"])

logger = logging.getLogger(__name__)

# ── stuck-run watchdog (SHIP-1 hardening) ────────────────────────────────────
#
# Mechanism choice: an ON-READ sweep in GET /studies/{id}/colorways, not a
# celery-beat process. The contact sheet polls exactly this endpoint every
# ~3s for as long as anything shows 'generating', so a stuck study self-heals
# the moment anyone is looking at it — which is the only moment stuckness
# matters (the 409 "already in progress" gate and the endless GENERATING…
# banner are the user-visible symptoms). Beat would add a 4th long-running
# process to dev + prod compose to fix a screen nobody is watching.
#
# "No active task" signal: the trie executor refreshes a short-TTL redis
# heartbeat per chain node (gen:active:{study_id}, EX 180 vs ~40s/node). A
# live heartbeat vetoes the sweep entirely — timestamps alone cannot tell a
# long legitimate full-sheet run from a dead worker.

STUDY_STUCK_MINUTES = 30  # generating with no heartbeat this long → partial
COLORWAY_STUCK_MINUTES = 15  # a single colorway paints in ~2min; 15 is dead

WATCHDOG_CW_ERROR = "watchdog: generation task lost mid-paint"
WATCHDOG_STUDY_ERROR = (
    f"watchdog: no generation activity for {STUDY_STUCK_MINUTES}min — "
    "run marked partial, everything generated so far is kept"
)


async def _heartbeat_alive(study_id) -> bool:
    from app.auth import get_redis

    try:
        return bool(await get_redis().exists(G.heartbeat_key(study_id)))
    except Exception:  # noqa: BLE001 — redis down: no worker is alive either
        logger.warning("watchdog: redis unreachable, assuming no active task")
        return False


async def _watchdog_sweep(study, session: AsyncSession) -> bool:
    """Mark obviously-dead generation state; returns True if anything changed
    (the caller re-reads the study). Ready/pinned/rejected rows are never
    touched — partial means partial, results are kept."""
    n_generating = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM colorways "
                "WHERE study_id = :id AND status = 'generating'"
            ),
            {"id": str(study.id)},
        )
    ).scalar_one()
    if study.status != "generating" and not n_generating:
        return False
    if await _heartbeat_alive(study.id):
        return False

    changed = False
    # rule 1: a colorway mid-paint for >15min with no live task is dead
    stuck_cws = (
        await session.execute(
            text(
                "UPDATE colorways SET status = 'failed', error = :err "
                "WHERE study_id = :id AND status = 'generating' "
                "AND COALESCE(generation_started_at, created_at) "
                "    < now() - make_interval(mins => :mins) "
                "RETURNING id"
            ),
            {"err": WATCHDOG_CW_ERROR, "id": str(study.id),
             "mins": COLORWAY_STUCK_MINUTES},
        )
    ).all()
    changed = bool(stuck_cws)

    # rule 2: the study itself — >30min of 'generating' with no heartbeat
    if study.status == "generating":
        study_stuck = (
            await session.execute(
                text(
                    "SELECT COALESCE(generation_started_at, created_at) "
                    "       < now() - make_interval(mins => :mins) "
                    "FROM studies WHERE id = :id"
                ),
                {"mins": STUDY_STUCK_MINUTES, "id": str(study.id)},
            )
        ).scalar_one()
        if study_stuck:
            # no task exists, so whatever is still mid-paint is dead too
            await session.execute(
                text(
                    "UPDATE colorways SET status = 'failed', error = :err "
                    "WHERE study_id = :id AND status = 'generating'"
                ),
                {"err": WATCHDOG_CW_ERROR, "id": str(study.id)},
            )
            await session.execute(
                text(
                    "UPDATE studies SET status = 'partial', error = :err "
                    "WHERE id = :id"
                ),
                {"err": WATCHDOG_STUDY_ERROR, "id": str(study.id)},
            )
            changed = True

    if changed:
        await session.commit()
        logger.warning(
            "watchdog swept study %s (%d stuck colorways)", study.id, len(stuck_cws)
        )
    return changed


# ── models ───────────────────────────────────────────────────────────────────

class GenerateIn(BaseModel):
    colorway_ids: list[uuid.UUID] | None = None  # explicit subset
    all: bool = False  # the full planned sheet


class GenerateOut(BaseModel):
    study_id: uuid.UUID
    status: str
    requested: list[uuid.UUID]  # colorways this run will generate
    already_ready: int
    planned: int  # colorways in the planned sheet
    estimated_cents: int  # conservative (no cache-hit discount)


class GenerateOneIn(BaseModel):
    colorway_id: uuid.UUID | None = None
    mapping: dict[int, int] | None = None  # slot idx -> sanzo color id


class FingerprintChip(BaseModel):
    slot_idx: int
    slot_label: str
    color_id: int
    hex: str
    name: str


class ColorwayOut(BaseModel):
    id: uuid.UUID
    permutation_idx: int
    mapping: list[FingerprintChip]  # canonical slot order = fingerprint
    status: str
    image_url: str | None
    thumb_url: str | None
    cost_cents: int
    cache_hits: int
    lock_verified: bool | None
    latency_ms: int | None
    rank_score: float
    is_representative: bool
    error: str | None


class ColorwaysOut(BaseModel):
    study_id: uuid.UUID
    study_status: str
    actual_cost_cents: int
    est_cost_cents: int | None
    planned: int
    ready: int
    colorways: list[ColorwayOut]


class BudgetOut(BaseModel):
    today_spent_cents: int
    daily_cap_cents: int
    study_cap_cents: int
    hard_freeze_cents: int
    remaining_today_cents: int
    frozen: bool


# ── helpers ──────────────────────────────────────────────────────────────────

async def _spent_today(session: AsyncSession, ws: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(cost_cents), 0) FROM spend_ledger "
                    "WHERE workspace_id = :ws "
                    "AND occurred_at >= date_trunc('day', now())"
                ),
                {"ws": ws},
            )
        ).scalar_one()
    )


def _refuse(tier: str, cap: int, spent: int, estimated: int) -> None:
    raise HTTPException(
        402,
        {
            "error": "budget_cap",
            "tier": tier,
            "cap_cents": cap,
            "spent_cents": spent,
            "estimated_cents": estimated,
            "message": (
                f"the {tier} budget cap ({cap}¢) refuses this run: "
                f"{spent}¢ already spent, ~{estimated}¢ requested"
            ),
        },
    )


async def _budget_gate(
    session: AsyncSession, ws: str, study_actual: int, estimated: int
) -> None:
    """§8.11 check #2 (enqueue): freeze refuses everything; study/daily refuse
    when the conservative estimate would cross."""
    s = get_settings()
    today = await _spent_today(session, ws)
    if today >= s.budget_hard_freeze_cents:
        _refuse("freeze", s.budget_hard_freeze_cents, today, estimated)
    if study_actual + estimated > s.budget_study_cap_cents:
        _refuse("study", s.budget_study_cap_cents, study_actual, estimated)
    if today + estimated > s.budget_daily_cap_cents:
        _refuse("daily", s.budget_daily_cap_cents, today, estimated)


async def _freeze_gate(session: AsyncSession, ws: str) -> None:
    """§8.11: at the hard freeze, refuse EVERYTHING — before any other
    validation or row-minting side effects."""
    s = get_settings()
    today = await _spent_today(session, ws)
    if today >= s.budget_hard_freeze_cents:
        _refuse("freeze", s.budget_hard_freeze_cents, today, 0)


def _estimate_cents_for(target_mappings: list[dict[int, int]], darkness: dict) -> int:
    """Distinct chain prefixes across the target colorways × 6.75¢ — the §8.8
    node count if nothing were cached (G.trie_calls: anchors included)."""
    total = G.COST_CENTS_PER_CALL * G.trie_calls(target_mappings, darkness)
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def _darkness_for(session: AsyncSession, color_ids: list[int]) -> dict:
    rows = (
        await session.execute(
            text("SELECT id, lab_l FROM sanzo_colors WHERE id = ANY(CAST(:ids AS int[]))"),
            {"ids": color_ids},
        )
    ).all()
    return {r.id: (r.lab_l, r.id) for r in rows}


async def _persist_plan(study, p: dict, session: AsyncSession) -> None:
    """First generate: INSERT permutations + colorways(status='planned') for
    the FULL planned selection (§8.12). Idempotent: once rows exist the plan
    is frozen (slots/palette/policy are all draft-only edits)."""
    n = (
        await session.execute(
            text("SELECT COUNT(*) FROM permutations WHERE study_id = :id"),
            {"id": str(study.id)},
        )
    ).scalar_one()
    if n:
        return
    for idx, (mapping, score, sig) in enumerate(
        zip(p["mappings"], p["scores"], p["signatures"])
    ):
        pid = (
            await session.execute(
                text(
                    """
                    INSERT INTO permutations (study_id, idx, mapping, rank_score,
                                              signature)
                    VALUES (:study_id, :idx, CAST(:mapping AS jsonb), :score,
                            CAST(:sig AS real[]))
                    RETURNING id
                    """
                ),
                {
                    "study_id": str(study.id), "idx": idx,
                    "mapping": json.dumps({str(k): v for k, v in mapping.items()}),
                    "score": score, "sig": list(sig),
                },
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO colorways (workspace_id, study_id, permutation_id) "
                "VALUES (:ws, :study_id, :pid)"
            ),
            {"ws": str(study.workspace_id), "study_id": str(study.id), "pid": str(pid)},
        )
    await session.execute(
        text(
            """
            UPDATE studies SET perm_total = :total, perm_planned = :planned,
                   prompt_version = :pv
            WHERE id = :id
            """
        ),
        {
            "total": perm.perm_count(p["k_eff"], p["c_eff"]),
            "planned": len(p["mappings"]),
            "pv": G.PROMPT_VERSION,
            "id": str(study.id),
        },
    )


async def _study_colorways(session: AsyncSession, study_id) -> list:
    return (
        await session.execute(
            text(
                """
                SELECT c.*, p.idx AS perm_idx, p.mapping AS perm_mapping,
                       p.rank_score, p.is_representative
                FROM colorways c JOIN permutations p ON p.id = c.permutation_id
                WHERE c.study_id = :id ORDER BY p.idx
                """
            ),
            {"id": str(study_id)},
        )
    ).all()


def _enqueue(study_id, colorway_ids: list[str]) -> None:
    from app.workers.generate import generate_study as task

    try:
        task.delay(str(study_id), colorway_ids)
    except Exception as e:  # broker down: generation is the point — surface it
        raise HTTPException(503, f"cannot enqueue generation: {e}") from e


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("/studies/{study_id}/generate", response_model=GenerateOut, status_code=202)
async def generate(
    study_id: uuid.UUID,
    body: GenerateIn | None = None,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> GenerateOut:
    body = body or GenerateIn()
    study = await _study_or_404(study_id, ctx, session)
    if study.status == "generating":
        raise HTTPException(409, "a generation run is already in progress")
    await _freeze_gate(session, str(ctx.workspace_id))

    p = await compute_plan(study, session)
    await _persist_plan(study, p, session)
    rows = await _study_colorways(session, study.id)

    if body.colorway_ids is not None:
        by_id = {r.id: r for r in rows}
        missing = [str(i) for i in body.colorway_ids if i not in by_id]
        if missing:
            raise HTTPException(404, f"colorway not found in this study: {missing[0]}")
        chosen = [by_id[i] for i in body.colorway_ids]
    elif body.all:
        chosen = list(rows)
    else:  # default: the policy's eager-N, in the plan's diversity order
        chosen = rows[: p["policy"]["eager"]]

    ready = [r for r in chosen if r.status in ("ready", "pinned", "rejected")]
    todo = [r for r in chosen if r.status not in ("ready", "pinned", "rejected")]

    darkness = await _darkness_for(
        session, sorted({c for r in todo for c in _mapping_of(r).values()})
    )
    estimated = _estimate_cents_for([_mapping_of(r) for r in todo], darkness) if todo else 0
    await _budget_gate(session, str(ctx.workspace_id), study.actual_cost_cents, estimated)

    if todo:
        await session.execute(
            text(
                "UPDATE studies SET status = 'generating', error = NULL, "
                "generation_started_at = now() WHERE id = :id"
            ),
            {"id": str(study.id)},
        )
    await session.commit()
    if todo:
        _enqueue(study.id, [str(r.id) for r in todo])

    return GenerateOut(
        study_id=study.id,
        status="generating" if todo else study.status,
        requested=[r.id for r in todo],
        already_ready=len(ready),
        planned=len(rows),
        estimated_cents=estimated,
    )


def _mapping_of(row) -> dict[int, int]:
    m = row.perm_mapping if not isinstance(row.perm_mapping, str) else json.loads(row.perm_mapping)
    return {int(k): v for k, v in m.items()}


@router.post(
    "/studies/{study_id}/generate-one", response_model=GenerateOut, status_code=202
)
async def generate_one(
    study_id: uuid.UUID,
    body: GenerateOneIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> GenerateOut:
    if (body.colorway_id is None) == (body.mapping is None):
        raise HTTPException(422, "provide exactly one of colorway_id or mapping")
    study = await _study_or_404(study_id, ctx, session)
    if study.status == "generating":
        raise HTTPException(409, "a generation run is already in progress")
    await _freeze_gate(session, str(ctx.workspace_id))

    p = await compute_plan(study, session)
    await _persist_plan(study, p, session)
    rows = await _study_colorways(session, study.id)

    if body.colorway_id is not None:
        row = next((r for r in rows if r.id == body.colorway_id), None)
        if row is None:
            raise HTTPException(404, "colorway not found in this study")
        if row.status in ("ready", "pinned"):
            raise HTTPException(409, f"colorway is already {row.status}")
        target_id, mapping = row.id, _mapping_of(row)
    else:
        mapping = _validate_ghost_mapping(body.mapping, p)
        existing = next((r for r in rows if _mapping_of(r) == mapping), None)
        if existing is not None:
            if existing.status in ("ready", "pinned"):
                raise HTTPException(409, f"colorway is already {existing.status}")
            target_id = existing.id
        else:
            target_id = await _insert_ghost(study, p, mapping, session)

    darkness = await _darkness_for(session, sorted(set(mapping.values())))
    estimated = _estimate_cents_for([mapping], darkness)
    await _budget_gate(session, str(ctx.workspace_id), study.actual_cost_cents, estimated)

    await session.execute(
        text(
            "UPDATE studies SET status = 'generating', error = NULL, "
            "generation_started_at = now() WHERE id = :id"
        ),
        {"id": str(study.id)},
    )
    await session.commit()
    _enqueue(study.id, [str(target_id)])

    planned = (
        await session.execute(
            text("SELECT COUNT(*) FROM colorways WHERE study_id = :id"),
            {"id": str(study.id)},
        )
    ).scalar_one()
    return GenerateOut(
        study_id=study.id, status="generating", requested=[target_id],
        already_ready=0, planned=planned, estimated_cents=estimated,
    )


def _validate_ghost_mapping(raw: dict[int, int], p: dict) -> dict[int, int]:
    """An unplanned permutation must still be a member of the study's
    permutation space: every paint slot mapped, colours from the palette,
    anchors honoured, and the regime respected (injective when K≤C, every
    colour used when K>C)."""
    mapping = {int(k): v for k, v in raw.items()}
    paint_idx = [s.idx for s in p["paint"]]
    if sorted(mapping) != paint_idx:
        raise HTTPException(422, "mapping must cover exactly the paint slots")
    palette_ids = set(p["palette"].color_ids)
    if not set(mapping.values()) <= palette_ids:
        raise HTTPException(422, "mapping uses a colour outside the study's palette")
    for s in p["anchored"]:
        if mapping[s.idx] != s.anchor_color_id:
            raise HTTPException(422, f"slot {s.idx} is anchored to colour {s.anchor_color_id}")
    anchored_ids = {s.anchor_color_id for s in p["anchored"]}
    free_vals = [v for i, v in mapping.items()
                 if i not in {s.idx for s in p["anchored"]}]
    if anchored_ids & set(free_vals):
        raise HTTPException(422, "an anchored colour cannot repeat on a free slot")
    k_eff, c_eff = p["k_eff"], p["c_eff"]
    if k_eff <= c_eff and len(set(free_vals)) != len(free_vals):
        raise HTTPException(422, "K≤C: each slot needs a distinct colour")
    if k_eff > c_eff and len(set(free_vals)) != c_eff:
        raise HTTPException(422, "K>C: every palette colour must be used")
    return mapping


async def _insert_ghost(study, p: dict, mapping: dict[int, int], session) -> uuid.UUID:
    """Persist an unplanned permutation (idx after the planned block) + its
    colorway. Score/signature computed with the same engine maths."""
    free_slots = [s for s in p["paint"] if s.anchor_color_id is None]
    anchored_ids = {s.anchor_color_id for s in p["anchored"]}
    palette_colors = (
        await session.execute(
            text(
                "SELECT id, lab_l, lab_a, lab_b FROM sanzo_colors "
                "WHERE id = ANY(CAST(:ids AS int[]))"
            ),
            {"ids": list(p["palette"].color_ids)},
        )
    ).all()
    free_colors = [
        c for c in sorted(palette_colors, key=lambda c: (c.lab_l, c.id))
        if c.id not in anchored_ids
    ]
    rank_of = {c.id: i for i, c in enumerate(free_colors)}
    labs = [(c.lab_l, c.lab_a, c.lab_b) for c in free_colors]
    areas = [s.area_fraction for s in free_slots]
    assignment = tuple(rank_of[mapping[s.idx]] for s in free_slots)

    next_idx = (
        await session.execute(
            text("SELECT COALESCE(MAX(idx), -1) + 1 FROM permutations WHERE study_id = :id"),
            {"id": str(study.id)},
        )
    ).scalar_one()
    pid = (
        await session.execute(
            text(
                """
                INSERT INTO permutations (study_id, idx, mapping, rank_score, signature)
                VALUES (:study_id, :idx, CAST(:mapping AS jsonb), :score,
                        CAST(:sig AS real[]))
                RETURNING id
                """
            ),
            {
                "study_id": str(study.id), "idx": next_idx,
                "mapping": json.dumps({str(k): v for k, v in mapping.items()}),
                "score": perm.score(assignment, labs, areas),
                "sig": list(perm.signature(assignment, labs, areas)),
            },
        )
    ).scalar_one()
    return (
        await session.execute(
            text(
                "INSERT INTO colorways (workspace_id, study_id, permutation_id) "
                "VALUES (:ws, :study_id, :pid) RETURNING id"
            ),
            {"ws": str(study.workspace_id), "study_id": str(study.id), "pid": str(pid)},
        )
    ).scalar_one()


@router.get("/studies/{study_id}/colorways", response_model=ColorwaysOut)
async def list_colorways(
    study_id: uuid.UUID,
    status: str | None = None,
    body: int | None = None,  # §8.13.2: filter by the colour on slot 0
    sort: str = "idx",  # idx (diversity order, default) | rank
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> ColorwaysOut:
    study = await _study_or_404(study_id, ctx, session)
    if await _watchdog_sweep(study, session):
        study = await _study_or_404(study_id, ctx, session)
    rows = await _study_colorways(session, study.id)

    slots = (
        await session.execute(
            text(
                "SELECT idx, label FROM slots WHERE study_id = :id AND kind = 'paint' "
                "ORDER BY idx"
            ),
            {"id": str(study.id)},
        )
    ).all()
    slot_label = {r.idx: r.label for r in slots}
    color_ids = sorted({c for r in rows for c in _mapping_of(r).values()})
    colors = {
        r.id: r
        for r in (
            await session.execute(
                text(
                    "SELECT id, name, hex FROM sanzo_colors "
                    "WHERE id = ANY(CAST(:ids AS int[]))"
                ),
                {"ids": color_ids},
            )
        ).all()
    }

    out = []
    for r in rows:
        mapping = _mapping_of(r)
        if status is not None and r.status != status:
            continue
        if body is not None and mapping.get(0) != body:
            continue
        out.append(
            ColorwayOut(
                id=r.id,
                permutation_idx=r.perm_idx,
                mapping=[
                    FingerprintChip(
                        slot_idx=i, slot_label=slot_label.get(i, str(i)),
                        color_id=cid, hex=colors[cid].hex.strip(),
                        name=colors[cid].name,
                    )
                    for i, cid in sorted(mapping.items())
                ],
                status=r.status,
                image_url=presign_get(r.image_key) if r.image_key else None,
                thumb_url=presign_get(r.thumb_key) if r.thumb_key else None,
                cost_cents=r.cost_cents,
                cache_hits=r.cache_hits,
                lock_verified=r.lock_verified,
                latency_ms=r.latency_ms,
                rank_score=r.rank_score,
                is_representative=r.is_representative,
                error=r.error,
            )
        )
    if sort == "rank":
        out.sort(key=lambda c: -c.rank_score)

    return ColorwaysOut(
        study_id=study.id,
        study_status=study.status,
        actual_cost_cents=study.actual_cost_cents,
        est_cost_cents=study.est_cost_cents,
        planned=len(rows),
        ready=sum(1 for r in rows if r.status in ("ready", "pinned")),
        colorways=out,
    )


@router.get("/budget", response_model=BudgetOut)
async def budget(
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> BudgetOut:
    s = get_settings()
    today = await _spent_today(session, str(ctx.workspace_id))
    return BudgetOut(
        today_spent_cents=today,
        daily_cap_cents=s.budget_daily_cap_cents,
        study_cap_cents=s.budget_study_cap_cents,
        hard_freeze_cents=s.budget_hard_freeze_cents,
        remaining_today_cents=max(0, s.budget_daily_cap_cents - today),
        frozen=today >= s.budget_hard_freeze_cents,
    )
