"""Share links (TDD §4 + §9, PRD A8).

POST /share (auth) mints a view-only public link for a project XOR a design.
Idempotent per (target, scope): PRD A8 says "One view-only public URL per
project gallery", so re-minting while a live link exists returns that link
(200) instead of creating another (201).

GET /s/{slug} lives on a SEPARATE router with no auth dependency anywhere
(TDD §4: "Share links are unauthenticated. A separate router with no auth
dependency, resolving by slug, returning a projection that contains no
internal ids. Rate limited to 60 req/min per IP.").

No internal ids: the storage key layout embeds the workspace uuid
(src/{ws}/{sha}…, TDD §3), so presigned URLs cannot appear inline in the
projection without leaking it. Media are therefore addressed by opaque
per-projection indices — /s/{slug}/m/{i} and /s/{slug}/m/{i}/thumb — which
307-redirect to the presigned storage GET. Recipients still fetch bytes
straight from storage with no app auth.

Scope semantics (documented reading of the PRD):
- 'finals' = phases (final, editorial). PRD A7 defines the gallery as a
  "Cross-project view of Finals and Editorial" and A8 shares "the project
  gallery"; "Finals only" in A8 names that gallery subset, not the single
  'final' phase.
- 'full'   = the full timeline: every phase, plus text-note entries.

There is no revoke endpoint — the TDD API table lists only POST /share and
GET /s/{slug}. Revoke via SQL for now:
    UPDATE share_links SET revoked_at = now() WHERE slug = '<slug>';
"""

import re
import secrets
import time
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx, get_redis
from app.config import get_settings
from app.db import get_session
from app.storage import presign_get

router = APIRouter(tags=["share"])

SLUG_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
FINALS_SQL = "AND m.phase IN ('final', 'editorial')"  # PRD A7 gallery = Finals + Editorial


# ── mint (authenticated) ─────────────────────────────────────────────────────

class ShareIn(BaseModel):
    project_id: uuid.UUID | None = None
    design_id: uuid.UUID | None = None
    scope: Literal["finals", "full"] = "finals"


class ShareLinkOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    design_id: uuid.UUID | None
    slug: str
    scope: str
    url: str
    revoked_at: datetime | None
    view_count: int
    created_at: datetime


def _out(row) -> ShareLinkOut:
    return ShareLinkOut(
        id=row.id, project_id=row.project_id, design_id=row.design_id,
        slug=row.slug, scope=row.scope, url=f"/s/{row.slug}",
        revoked_at=row.revoked_at, view_count=row.view_count, created_at=row.created_at,
    )


def _slug_base(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower()) or "atelier"


@router.post("/share", response_model=ShareLinkOut, status_code=201)
async def mint(
    body: ShareIn,
    response: Response,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> ShareLinkOut:
    if (body.project_id is None) == (body.design_id is None):
        raise HTTPException(422, "provide exactly one of project_id or design_id")

    table = "projects" if body.project_id else "designs"
    target_id = body.project_id or body.design_id
    name_row = (
        await session.execute(
            text(f"SELECT name FROM {table} WHERE workspace_id = :ws AND id = :id"),  # noqa: S608
            {"ws": str(ctx.workspace_id), "id": str(target_id)},
        )
    ).one_or_none()
    if name_row is None:
        raise HTTPException(404, f"{table[:-1]} not found")

    # PRD A8: ONE view-only public URL per gallery — reuse the live link
    existing = (
        await session.execute(
            text(
                """
                SELECT * FROM share_links
                WHERE workspace_id = :ws AND scope = :scope AND revoked_at IS NULL
                  AND project_id IS NOT DISTINCT FROM CAST(:pid AS uuid)
                  AND design_id  IS NOT DISTINCT FROM CAST(:did AS uuid)
                ORDER BY created_at
                LIMIT 1
                """
            ),
            {
                "ws": str(ctx.workspace_id), "scope": body.scope,
                "pid": str(body.project_id) if body.project_id else None,
                "did": str(body.design_id) if body.design_id else None,
            },
        )
    ).one_or_none()
    if existing is not None:
        response.status_code = 200
        return _out(existing)

    base = _slug_base(name_row.name)
    for _ in range(20):  # slug collisions are ~impossible; loop for safety anyway
        suffix = "".join(secrets.choice(SLUG_SUFFIX_ALPHABET) for _ in range(4))
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO share_links (workspace_id, project_id, design_id, slug, scope)
                    VALUES (:ws, :pid, :did, :slug, :scope)
                    ON CONFLICT (slug) DO NOTHING
                    RETURNING *
                    """
                ),
                {
                    "ws": str(ctx.workspace_id),
                    "pid": str(body.project_id) if body.project_id else None,
                    "did": str(body.design_id) if body.design_id else None,
                    "slug": f"{base}-{suffix}",
                    "scope": body.scope,
                },
            )
        ).one_or_none()
        if row is not None:
            await session.commit()
            return _out(row)
    raise HTTPException(500, "could not allocate a unique slug")  # pragma: no cover


# ── public gallery projection (NO auth) ──────────────────────────────────────

async def _rate_limit(request: Request) -> None:
    """Fixed 60s window per IP in Redis (TDD §4: 60 req/min per IP)."""
    limit = get_settings().share_rate_limit_per_min
    ip = request.client.host if request.client else "unknown"
    key = f"shr-rl:{ip}:{int(time.time() // 60)}"
    r = get_redis()
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 90)
    if count > limit:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "60"})


public_router = APIRouter(tags=["public"], dependencies=[Depends(_rate_limit)])


async def _live_link(session: AsyncSession, slug: str):
    """Unknown and revoked slugs are indistinguishable to the public: 404."""
    row = (
        await session.execute(
            text("SELECT * FROM share_links WHERE slug = :slug AND revoked_at IS NULL"),
            {"slug": slug},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "not found")
    return row


def _target_sql(link) -> tuple[str, dict]:
    if link.project_id is not None:
        return "d.project_id = :tid", {"tid": str(link.project_id)}
    return "d.id = :tid", {"tid": str(link.design_id)}


async def _ordered_media(session: AsyncSession, link) -> list:
    """The projection's media, in the ONE deterministic order that the opaque
    /s/{slug}/m/{index} addresses are defined over."""
    cond, params = _target_sql(link)
    rows = await session.execute(
        text(
            f"""
            SELECT m.kind, m.phase, m.r2_key, m.thumb_key, m.width, m.height,
                   m.duration_ms, m.caption, m.created_at,
                   d.id AS d_id, d.name AS d_name, d.index_no AS d_index_no,
                   d.status AS d_status
            FROM media m JOIN designs d ON d.id = m.design_id
            WHERE d.workspace_id = :ws AND {cond}
              {FINALS_SQL if link.scope == "finals" else ""}
            ORDER BY d.index_no, d.id, m.created_at, m.id
            """
        ),
        {"ws": str(link.workspace_id)} | params,
    )
    return list(rows)


@public_router.get("/s/{slug}")
async def public_gallery(slug: str, session: AsyncSession = Depends(get_session)) -> dict:
    link = await _live_link(session, slug)

    cond, params = _target_sql(link)
    project = (
        await session.execute(
            text(
                f"""
                SELECT DISTINCT p.name, p.kicker FROM projects p
                JOIN designs d ON d.project_id = p.id
                WHERE {cond}
                """
                if link.project_id is None
                else "SELECT name, kicker FROM projects p WHERE p.id = :tid"
            ),
            params,
        )
    ).one_or_none()

    media = await _ordered_media(session, link)
    designs: dict = {}  # d_id -> projection dict, insertion-ordered like media
    for i, m in enumerate(media):
        d = designs.setdefault(
            m.d_id,
            {"name": m.d_name, "index_no": m.d_index_no, "status": m.d_status,
             "media": [], "notes": []},
        )
        d["media"].append(
            {
                "index": i,
                "kind": m.kind,
                "phase": m.phase,
                "url": f"/s/{slug}/m/{i}",
                "thumb_url": f"/s/{slug}/m/{i}/thumb" if m.thumb_key else None,
                "width": m.width,
                "height": m.height,
                "duration_ms": m.duration_ms,
                "caption": m.caption,
                "created_at": m.created_at,
            }
        )

    if link.scope == "full":  # the timeline's text notes belong to the full view
        notes = await session.execute(
            text(
                f"""
                SELECT e.design_id AS d_id, e.phase, e.body, e.occurred_at
                FROM entries e JOIN designs d ON d.id = e.design_id
                WHERE d.workspace_id = :ws AND {cond} AND e.body IS NOT NULL
                ORDER BY e.occurred_at, e.id
                """
            ),
            {"ws": str(link.workspace_id)} | params,
        )
        for n in notes:
            if n.d_id in designs:  # notes never resurrect an empty design card
                designs[n.d_id]["notes"].append(
                    {"phase": n.phase, "body": n.body, "occurred_at": n.occurred_at}
                )

    await session.execute(
        text("UPDATE share_links SET view_count = view_count + 1 WHERE id = :id"),
        {"id": str(link.id)},
    )
    await session.commit()

    return {
        "slug": slug,
        "scope": link.scope,
        "target": "project" if link.project_id is not None else "design",
        "project": {"name": project.name, "kicker": project.kicker} if project else None,
        "designs": list(designs.values()),
    }


async def _redirect(session: AsyncSession, slug: str, index: int, thumb: bool):
    link = await _live_link(session, slug)
    media = await _ordered_media(session, link)
    if index < 0 or index >= len(media):
        raise HTTPException(404, "not found")
    key = media[index].thumb_key if thumb else media[index].r2_key
    if key is None:
        raise HTTPException(404, "not found")
    return RedirectResponse(presign_get(key), status_code=307)


@public_router.get("/s/{slug}/m/{index}")
async def public_media(
    slug: str, index: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    return await _redirect(session, slug, index, thumb=False)


@public_router.get("/s/{slug}/m/{index}/thumb")
async def public_thumb(
    slug: str, index: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    return await _redirect(session, slug, index, thumb=True)
