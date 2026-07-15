import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx
from app.db import get_session
from app.storage import presign_get

router = APIRouter(tags=["designs"])

STATUSES = ("developing", "in_production", "final", "archive")
PHASES = (
    "moodboard", "inspiration", "sketch", "note", "voice",
    "manufacturing", "editorial", "final", "study",
)


class DesignIn(BaseModel):
    project_id: uuid.UUID
    name: str
    materials: str | None = None
    started_at: date | None = None


class DesignPatch(BaseModel):
    name: str | None = None
    status: str | None = None
    cover_media_id: uuid.UUID | None = None
    materials: str | None = None


class DesignOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    status: str
    index_no: int
    materials: str | None
    cover_media_id: uuid.UUID | None
    cover_url: str | None = None
    entry_count: int = 0
    media_count: int = 0
    created_at: datetime


def _cover_url(thumb_key: str | None, r2_key: str | None) -> str | None:
    key = thumb_key or r2_key
    return presign_get(key) if key else None


@router.get("/projects/{project_id}/designs", response_model=list[DesignOut])
async def list_designs(
    project_id: uuid.UUID,
    status: str | None = None,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[DesignOut]:
    if status is not None and status not in STATUSES:
        raise HTTPException(422, f"status must be one of {STATUSES}")
    rows = await session.execute(
        text(
            f"""
            SELECT d.*, m.thumb_key AS cover_thumb, m.r2_key AS cover_r2,
                   (SELECT COUNT(*) FROM entries e WHERE e.design_id = d.id) AS entry_count,
                   (SELECT COUNT(*) FROM media md WHERE md.design_id = d.id) AS media_count
            FROM designs d
            LEFT JOIN media m ON m.id = d.cover_media_id
            WHERE d.workspace_id = :ws AND d.project_id = :pid
              {"AND d.status = :status" if status else ""}
            ORDER BY d.index_no
            """
        ),
        {"ws": str(ctx.workspace_id), "pid": str(project_id)}
        | ({"status": status} if status else {}),
    )
    return [
        DesignOut(
            **{k: getattr(r, k) for k in DesignOut.model_fields if k not in ("cover_url",)},
            cover_url=_cover_url(r.cover_thumb, r.cover_r2),
        )
        for r in rows
    ]


@router.post("/designs", response_model=DesignOut, status_code=201)
async def create_design(
    body: DesignIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> DesignOut:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO designs (workspace_id, project_id, name, materials, started_at, index_no)
                SELECT :ws, :pid, :name, :materials, :started_at,
                       COALESCE(MAX(index_no), 0) + 1
                FROM designs WHERE project_id = :pid
                RETURNING *
                """
            ),
            {
                "ws": str(ctx.workspace_id),
                "pid": str(body.project_id),
                "name": body.name,
                "materials": body.materials,
                "started_at": body.started_at,
            },
        )
    ).one()
    await session.commit()
    return DesignOut(
        **{
            k: getattr(row, k)
            for k in DesignOut.model_fields
            if k not in ("cover_url", "entry_count", "media_count")
        }
    )


@router.get("/designs/{design_id}", response_model=DesignOut)
async def get_design(
    design_id: uuid.UUID,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> DesignOut:
    row = (
        await session.execute(
            text(
                """
                SELECT d.*, m.thumb_key AS cover_thumb, m.r2_key AS cover_r2,
                       (SELECT COUNT(*) FROM entries e WHERE e.design_id = d.id) AS entry_count,
                       (SELECT COUNT(*) FROM media md WHERE md.design_id = d.id) AS media_count
                FROM designs d
                LEFT JOIN media m ON m.id = d.cover_media_id
                WHERE d.workspace_id = :ws AND d.id = :id
                """
            ),
            {"ws": str(ctx.workspace_id), "id": str(design_id)},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "design not found")
    return DesignOut(
        **{k: getattr(row, k) for k in DesignOut.model_fields if k not in ("cover_url",)},
        cover_url=_cover_url(row.cover_thumb, row.cover_r2),
    )


@router.patch("/designs/{design_id}", response_model=DesignOut)
async def patch_design(
    design_id: uuid.UUID,
    body: DesignPatch,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> DesignOut:
    if body.status is not None and body.status not in STATUSES:
        raise HTTPException(422, f"status must be one of {STATUSES}")
    sets, params = [], {"ws": str(ctx.workspace_id), "id": str(design_id)}
    for field in ("name", "status", "cover_media_id", "materials"):
        val = getattr(body, field)
        if val is not None:
            sets.append(f"{field} = :{field}")
            params[field] = str(val) if field == "cover_media_id" else val
    if not sets:
        raise HTTPException(422, "nothing to update")
    sets.append("updated_at = now()")
    row = (
        await session.execute(
            text(
                f"""
                UPDATE designs SET {", ".join(sets)}
                WHERE workspace_id = :ws AND id = :id
                RETURNING *
                """
            ),
            params,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "design not found")
    await session.commit()
    return DesignOut(
        **{
            k: getattr(row, k)
            for k in DesignOut.model_fields
            if k not in ("cover_url", "entry_count", "media_count")
        }
    )
