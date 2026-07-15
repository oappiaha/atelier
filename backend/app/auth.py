"""Magic-link auth (TDD §4).

POST /auth/link  — email in, one-time token out (delivered by Resend in prod,
                   printed to the API log locally).
POST /auth/verify — token in, 30-day JWT out. JWT carries sub (user_id) and
                    ws (workspace_id); workspace is ALWAYS resolved from the
                    token, never from a request body.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


class LinkIn(BaseModel):
    email: EmailStr


class VerifyIn(BaseModel):
    token: str


@dataclass
class Ctx:
    """Request-scoped identity. Every repository call takes ctx.workspace_id."""

    user_id: uuid.UUID
    workspace_id: uuid.UUID


async def _send_magic_link(email: str, token: str) -> None:
    s = get_settings()
    link = f"{s.app_base_url}/login?token={token}"
    if not s.resend_api_key:
        # local dev: the log IS the inbox
        print(f"\n  ✉  magic link for {email}:\n     {link}\n", flush=True)
        return
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {s.resend_api_key}"},
            json={
                "from": s.mail_from,
                "to": [email],
                "subject": "Sign in to Atelier",
                "text": f"Your sign-in link (valid 15 minutes):\n\n{link}\n",
            },
        )
        resp.raise_for_status()


@router.post("/link", status_code=202)
async def send_link(body: LinkIn) -> dict:
    token = secrets.token_urlsafe(32)
    r = get_redis()
    await r.set(f"magic:{token}", body.email.lower(), ex=get_settings().magic_link_ttl_seconds)
    await _send_magic_link(body.email.lower(), token)
    return {"ok": True}


@router.post("/verify")
async def verify(body: VerifyIn, session: AsyncSession = Depends(get_session)) -> dict:
    s = get_settings()
    r = get_redis()
    key = f"magic:{body.token}"
    email = await r.get(key)
    if email is None:
        raise HTTPException(401, "invalid or expired token")
    await r.delete(key)  # one-time

    row = (
        await session.execute(
            text(
                """
                INSERT INTO users (email) VALUES (:email)
                ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
                RETURNING id
                """
            ),
            {"email": email},
        )
    ).one()
    user_id = row[0]
    await session.execute(
        text(
            """
            INSERT INTO memberships (workspace_id, user_id, role)
            VALUES (:ws, :uid, 'owner')
            ON CONFLICT DO NOTHING
            """
        ),
        {"ws": s.workspace_id, "uid": user_id},
    )
    await session.commit()

    claims = {
        "sub": str(user_id),
        "ws": s.workspace_id,
        "exp": datetime.now(UTC) + timedelta(days=s.jwt_ttl_days),
    }
    return {"jwt": jwt.encode(claims, s.jwt_secret, algorithm="HS256")}


async def get_ctx(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Ctx:
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(401, "invalid token") from e
    return Ctx(user_id=uuid.UUID(claims["sub"]), workspace_id=uuid.UUID(claims["ws"]))
