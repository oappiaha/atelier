import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx
from app.db import get_session
from app.routers.designs import PHASES
from app.routers.media import MediaOut, _media_out

router = APIRouter(tags=["entries"])


class EntryIn(BaseModel):
    design_id: uuid.UUID
    phase: str
    body: str | None = None
    occurred_at: datetime | None = None
    is_open: bool = False
    media_ids: list[uuid.UUID] = []


class EntryPatch(BaseModel):
    body: str | None = None
    phase: str | None = None
    occurred_at: datetime | None = None
    is_open: bool | None = None


class EntryOut(BaseModel):
    id: uuid.UUID
    design_id: uuid.UUID
    phase: str
    body: str | None
    study_id: uuid.UUID | None
    occurred_at: datetime
    is_open: bool
    media: list[MediaOut] = []


async def _entry_media(session: AsyncSession, entry_ids: list[str]) -> dict[str, list]:
    if not entry_ids:
        return {}
    rows = await session.execute(
        text(
            """
            SELECT * FROM media WHERE entry_id = ANY(CAST(:eids AS uuid[]))
            ORDER BY sort_index, created_at
            """
        ),
        {"eids": entry_ids},
    )
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(str(r.entry_id), []).append(_media_out(r))
    return out


@router.get("/designs/{design_id}/timeline", response_model=list[EntryOut])
async def timeline(
    design_id: uuid.UUID,
    phase: str | None = None,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[EntryOut]:
    if phase is not None and phase not in PHASES:
        raise HTTPException(422, f"phase must be one of {PHASES}")
    rows = (
        await session.execute(
            text(
                f"""
                SELECT * FROM entries
                WHERE workspace_id = :ws AND design_id = :did
                  {"AND phase = :phase" if phase else ""}
                ORDER BY occurred_at DESC
                """
            ),
            {"ws": str(ctx.workspace_id), "did": str(design_id)}
            | ({"phase": phase} if phase else {}),
        )
    ).all()
    media_by_entry = await _entry_media(session, [str(r.id) for r in rows])
    return [
        EntryOut(
            id=r.id, design_id=r.design_id, phase=r.phase, body=r.body,
            study_id=r.study_id, occurred_at=r.occurred_at, is_open=r.is_open,
            media=media_by_entry.get(str(r.id), []),
        )
        for r in rows
    ]


@router.post("/entries", response_model=EntryOut, status_code=201)
async def create_entry(
    body: EntryIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> EntryOut:
    if body.phase not in PHASES:
        raise HTTPException(422, f"phase must be one of {PHASES}")
    row = (
        await session.execute(
            text(
                """
                INSERT INTO entries (workspace_id, design_id, phase, body, occurred_at, is_open)
                VALUES (:ws, :did, :phase, :body,
                        COALESCE(CAST(:occurred_at AS timestamptz), now()), :is_open)
                RETURNING *
                """
            ),
            {
                "ws": str(ctx.workspace_id),
                "did": str(body.design_id),
                "phase": body.phase,
                "body": body.body,
                "occurred_at": body.occurred_at,
                "is_open": body.is_open,
            },
        )
    ).one()
    if body.media_ids:
        # attach; the trigger denormalises phase + design_id onto media
        await session.execute(
            text(
                """
                UPDATE media SET entry_id = :eid
                WHERE workspace_id = :ws AND id = ANY(CAST(:mids AS uuid[]))
                """
            ),
            {
                "eid": str(row.id),
                "ws": str(ctx.workspace_id),
                "mids": [str(m) for m in body.media_ids],
            },
        )
    await session.commit()
    media_by_entry = await _entry_media(session, [str(row.id)])
    return EntryOut(
        id=row.id, design_id=row.design_id, phase=row.phase, body=row.body,
        study_id=row.study_id, occurred_at=row.occurred_at, is_open=row.is_open,
        media=media_by_entry.get(str(row.id), []),
    )


@router.patch("/entries/{entry_id}", response_model=EntryOut)
async def patch_entry(
    entry_id: uuid.UUID,
    body: EntryPatch,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> EntryOut:
    if body.phase is not None and body.phase not in PHASES:
        raise HTTPException(422, f"phase must be one of {PHASES}")
    sets, params = [], {"ws": str(ctx.workspace_id), "id": str(entry_id)}
    for field in ("body", "phase", "occurred_at", "is_open"):
        val = getattr(body, field)
        if val is not None:
            cast = "CAST(:occurred_at AS timestamptz)" if field == "occurred_at" else f":{field}"
            sets.append(f"{field} = {cast}")
            params[field] = val
    if not sets:
        raise HTTPException(422, "nothing to update")
    row = (
        await session.execute(
            text(
                f"""
                UPDATE entries SET {", ".join(sets)}
                WHERE workspace_id = :ws AND id = :id
                RETURNING *
                """
            ),
            params,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "entry not found")
    await session.commit()
    media_by_entry = await _entry_media(session, [str(row.id)])
    return EntryOut(
        id=row.id, design_id=row.design_id, phase=row.phase, body=row.body,
        study_id=row.study_id, occurred_at=row.occurred_at, is_open=row.is_open,
        media=media_by_entry.get(str(row.id), []),
    )
