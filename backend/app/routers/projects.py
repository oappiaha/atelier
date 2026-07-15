import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Ctx, get_ctx
from app.db import get_session

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectIn(BaseModel):
    name: str
    kicker: str | None = None


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    kicker: str | None
    design_count: int


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectOut]:
    rows = await session.execute(
        text(
            """
            SELECT p.id, p.name, p.kicker, COUNT(d.id) AS design_count
            FROM projects p
            LEFT JOIN designs d ON d.project_id = p.id
            WHERE p.workspace_id = :ws
            GROUP BY p.id
            ORDER BY p.sort_index, p.created_at
            """
        ),
        {"ws": str(ctx.workspace_id)},
    )
    return [ProjectOut(id=r.id, name=r.name, kicker=r.kicker, design_count=r.design_count) for r in rows]


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO projects (workspace_id, name, kicker)
                VALUES (:ws, :name, :kicker)
                RETURNING id, name, kicker
                """
            ),
            {"ws": str(ctx.workspace_id), "name": body.name, "kicker": body.kicker},
        )
    ).one()
    await session.commit()
    return ProjectOut(id=row.id, name=row.name, kicker=row.kicker, design_count=0)
