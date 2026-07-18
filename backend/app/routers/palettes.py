"""Palette Library API (TDD §9, §8.13.3).

GET /palettes            faceted: ?count=&temp=&hue=&contains=&q=
GET /palettes/{id}       detail + similar

Facet semantics (TDD §9 names them; §8.13.3 + PRD W3 give the meaning):
- count     colour count, 2|3|4
- temp      palette temperature, warm|cool|neutral
- hue       palettes containing at least one colour of that hue family
- contains  palettes containing sanzo colour id X ('contains Prussian Blue')
- q         text search over the palette name and member colour names

Optional ?slots=K adds the §8.13.3 permutation preview to every card:
"with your 3 slots: 6 perms, ~$1.01" — pure math, no spend.

Similarity ("Detail + similar"): the TDD expresses every ranking rule in LAB
but does not pin the palette-level rule, so we use the documented choice of
nearest-by-mean-Lab — euclidean distance between the palettes' mean member
(L*, a*, b*) vectors — filtered to the same color_count, closest 6.

Auth: workspace-scoped bearer, same as every non-public router. The corpus
is global reference data (no workspace column) — auth gates access, not rows.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx
from app.db import get_session
from app.wada import permutations as perm
from app.wada.color import HUE_FAMILIES, TEMPERATURES

router = APIRouter(tags=["palettes"])

MAX_PREVIEW_SLOTS = 6  # §8.3's own cautionary tale stops at K=6


class ColorOut(BaseModel):
    id: int
    name: str
    hex: str
    lab_l: float
    lab_a: float
    lab_b: float
    chroma: float
    hue_deg: float
    hue_family: str


class PermPreview(BaseModel):
    slots: int
    perms: int
    naive_calls: int
    trie_calls: int
    est_cost: str  # trie dollars — the '~$1.01' number


class PaletteOut(BaseModel):
    id: str
    name: str
    color_count: int
    color_ids: list[int]
    colors: list[ColorOut]
    mean_lab_l: float
    mean_chroma: float
    temperature: str
    max_delta_e: float
    min_delta_e: float
    volume: int | None
    plate: int | None
    preview: PermPreview | None = None


class SimilarOut(BaseModel):
    palette: PaletteOut
    distance: float  # euclidean over mean member Lab


class PaletteDetailOut(PaletteOut):
    similar: list[SimilarOut]


async def _color_index(session: AsyncSession) -> dict[int, ColorOut]:
    rows = await session.execute(text("SELECT * FROM sanzo_colors"))
    return {
        r.id: ColorOut(**{k: getattr(r, k) for k in ColorOut.model_fields}) for r in rows
    }


def _preview(color_count: int, slots: int | None) -> PermPreview | None:
    if slots is None:
        return None
    return PermPreview(
        slots=slots,
        perms=perm.perm_count(slots, color_count),
        naive_calls=perm.naive_call_count(slots, color_count),
        trie_calls=perm.trie_call_count(slots, color_count),
        est_cost=str(perm.cost_dollars(perm.trie_call_count(slots, color_count))),
    )


def _palette_out(
    row, colors: dict[int, ColorOut], slots: int | None = None
) -> PaletteOut:
    return PaletteOut(
        **{
            k: getattr(row, k)
            for k in PaletteOut.model_fields
            if k not in ("colors", "preview")
        },
        colors=[colors[cid] for cid in row.color_ids],
        preview=_preview(row.color_count, slots),
    )


@router.get("/palettes", response_model=list[PaletteOut])
async def list_palettes(
    count: int | None = Query(default=None),
    temp: str | None = Query(default=None),
    hue: str | None = Query(default=None),
    contains: int | None = Query(default=None),
    q: str | None = Query(default=None),
    slots: int | None = Query(default=None),
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[PaletteOut]:
    if count is not None and count not in (2, 3, 4):
        raise HTTPException(422, "count must be 2, 3 or 4")
    if temp is not None and temp not in TEMPERATURES:
        raise HTTPException(422, f"temp must be one of {TEMPERATURES}")
    if hue is not None and hue not in HUE_FAMILIES:
        raise HTTPException(422, f"hue must be one of {HUE_FAMILIES}")
    if slots is not None and not (1 <= slots <= MAX_PREVIEW_SLOTS):
        raise HTTPException(422, f"slots must be 1..{MAX_PREVIEW_SLOTS}")

    where, params = [], {}
    if count is not None:
        where.append("p.color_count = :count")
        params["count"] = count
    if temp is not None:
        where.append("p.temperature = :temp")
        params["temp"] = temp
    if hue is not None:
        where.append(
            "EXISTS (SELECT 1 FROM sanzo_colors c"
            "        WHERE c.id = ANY(p.color_ids) AND c.hue_family = :hue)"
        )
        params["hue"] = hue
    if contains is not None:
        where.append(":contains = ANY(p.color_ids)")
        params["contains"] = contains
    if q is not None:
        where.append(
            "(p.name ILIKE :q OR EXISTS (SELECT 1 FROM sanzo_colors c2"
            "  WHERE c2.id = ANY(p.color_ids) AND c2.name ILIKE :q))"
        )
        params["q"] = f"%{q}%"

    rows = (
        await session.execute(
            text(
                f"""
                SELECT p.* FROM palettes p
                {"WHERE " + " AND ".join(where) if where else ""}
                ORDER BY substring(p.id from 2)::int
                """
            ),
            params,
        )
    ).all()
    colors = await _color_index(session)
    return [_palette_out(r, colors, slots) for r in rows]


@router.get("/palettes/{palette_id}", response_model=PaletteDetailOut)
async def get_palette(
    palette_id: str,
    slots: int | None = Query(default=None),
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> PaletteDetailOut:
    if slots is not None and not (1 <= slots <= MAX_PREVIEW_SLOTS):
        raise HTTPException(422, f"slots must be 1..{MAX_PREVIEW_SLOTS}")
    rows = (await session.execute(text("SELECT * FROM palettes"))).all()
    target = next((r for r in rows if r.id == palette_id), None)
    if target is None:
        raise HTTPException(404, "palette not found")
    colors = await _color_index(session)

    def mean_lab(row) -> tuple[float, float, float]:
        member = [colors[cid] for cid in row.color_ids]
        n = len(member)
        return (
            sum(c.lab_l for c in member) / n,
            sum(c.lab_a for c in member) / n,
            sum(c.lab_b for c in member) / n,
        )

    tl, ta, tb = mean_lab(target)

    def distance(row) -> float:
        ml, ma, mb = mean_lab(row)
        return ((ml - tl) ** 2 + (ma - ta) ** 2 + (mb - tb) ** 2) ** 0.5

    ranked = sorted(
        (
            (distance(r), r)
            for r in rows
            if r.id != palette_id and r.color_count == target.color_count
        ),
        key=lambda pair: (pair[0], pair[1].id),
    )
    similar = [
        SimilarOut(palette=_palette_out(r, colors), distance=round(d, 3))
        for d, r in ranked[:6]
    ]
    base = _palette_out(target, colors, slots)
    return PaletteDetailOut(**base.model_dump(), similar=similar)
