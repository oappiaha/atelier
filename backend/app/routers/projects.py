import base64
import binascii
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator
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
    wordmark: str | None = None


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectOut]:
    rows = await session.execute(
        text(
            """
            SELECT p.id, p.name, p.kicker, p.wordmark, COUNT(d.id) AS design_count
            FROM projects p
            LEFT JOIN designs d ON d.project_id = p.id
            WHERE p.workspace_id = :ws
            GROUP BY p.id
            ORDER BY p.sort_index, p.created_at
            """
        ),
        {"ws": str(ctx.workspace_id)},
    )
    return [ProjectOut(id=r.id, name=r.name, kicker=r.kicker, design_count=r.design_count, wordmark=r.wordmark) for r in rows]


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


class WordmarkIn(BaseModel):
    # Small header assets travel with the project, never into the capture Inbox.
    wordmark: str | None = Field(max_length=350_000)

    @field_validator("wordmark")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        prefix = "data:image/png;base64,"
        if not value.startswith(prefix):
            raise ValueError("Wordmark must be a PNG image")
        try:
            raw = base64.b64decode(value[len(prefix):], validate=True)
            with Image.open(io.BytesIO(raw)) as img:
                if img.format != "PNG" or not (0 < img.width <= 1200 and 0 < img.height <= 320):
                    raise ValueError("Wordmark must fit within 1200 × 320 pixels")
                img.verify()
        except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Invalid wordmark image") from exc
        return value


@router.patch("/{project_id}/wordmark", status_code=204)
async def update_wordmark(
    project_id: uuid.UUID,
    body: WordmarkIn,
    ctx: Ctx = Depends(get_ctx),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.execute(
        text("UPDATE projects SET wordmark = :image "
             "WHERE id = :id AND workspace_id = :ws RETURNING id"),
        {"image": body.wordmark, "id": project_id, "ws": ctx.workspace_id},
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(404, "Project not found")
    await session.commit()
