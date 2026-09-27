"""Bounded, workspace-scoped gallery pages; no per-design media fan-out."""

import base64
import json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx
from app.db import get_session
from app.storage import presign_get

router = APIRouter(prefix="/gallery", tags=["gallery"])

FROM = """
FROM media m
JOIN designs d ON d.id = m.design_id AND d.workspace_id = m.workspace_id
JOIN projects p ON p.id = d.project_id AND p.workspace_id = m.workspace_id
WHERE m.workspace_id = :ws AND m.kind = 'image'
  AND m.phase IN ('final', 'editorial')
"""
RANK = "CASE WHEN m.phase = 'final' THEN 0 ELSE 1 END"


@router.get("")
async def gallery(
    phase: Literal["all", "final", "editorial"] = "all",
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=24, ge=1, le=60),
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> dict:
    params = {"ws": ctx.workspace_id, "limit": limit + 1}
    where = ""
    if phase != "all":
        where += " AND m.phase = :phase"
        params["phase"] = phase
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor).decode())
            if not isinstance(value, list) or len(value) != 4:
                raise ValueError
            rank, date, ident, saved_phase = value
            if (type(rank) is not int or rank not in (0, 1) or saved_phase != phase
                    or not isinstance(date, str) or not isinstance(ident, str)):
                raise ValueError
            parsed = datetime.fromisoformat(date)
            if parsed.tzinfo is None:
                raise ValueError
            params.update(rank=rank, date=parsed, ident=uuid.UUID(ident))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise HTTPException(422, "Invalid gallery cursor") from exc
        where += f""" AND ({RANK} > :rank OR
                     ({RANK} = :rank AND (m.created_at, m.id) < (:date, :ident)))"""
    rows = (
        await session.execute(
            text(f"""
        SELECT m.id, m.phase, m.r2_key, m.thumb_key, m.created_at,
               d.id AS design_id, d.name AS design_name, d.index_no,
               p.id AS project_id, p.name AS project_name
        {FROM} {where}
        ORDER BY {RANK}, m.created_at DESC, m.id DESC LIMIT :limit
    """),
            params,
        )
    ).all()
    counts = {"final": 0, "editorial": 0}
    for row in (
        await session.execute(
            text(f"SELECT m.phase, count(*) AS total {FROM} GROUP BY m.phase"),
            {"ws": ctx.workspace_id},
        )
    ).all():
        counts[row.phase] = row.total
    projects = (
        await session.execute(
            text(
                "SELECT id, name FROM projects WHERE workspace_id = :ws "
                "ORDER BY sort_index, created_at, id"
            ),
            {"ws": ctx.workspace_id},
        )
    ).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                [
                    0 if last.phase == "final" else 1,
                    last.created_at.isoformat(),
                    str(last.id),
                    phase,
                ]
            ).encode()
        ).decode()
    return {
        "items": [
            {
                "media": {
                    "id": r.id,
                    "phase": r.phase,
                    # Gallery only needs a thumbnail. Originals remain in Design/lightbox.
                    "thumb_url": presign_get(r.thumb_key or r.r2_key),
                },
                "design": {"id": r.design_id, "name": r.design_name, "index_no": r.index_no},
                "project": {"id": r.project_id, "name": r.project_name},
            }
            for r in page
        ],
        "counts": counts,
        "total": sum(counts.values()) if phase == "all" else counts[phase],
        "projects": [{"id": p.id, "name": p.name} for p in projects],
        "next_cursor": next_cursor,
    }
