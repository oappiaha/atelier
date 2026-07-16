"""Auth: health, magic-link flow, JWT enforcement across the surface."""

import uuid

import jwt as pyjwt
import pytest

from tests.util import WS


async def test_health_hits_test_db(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_magic_link_flow_yields_workspace_jwt(token):
    """The `token` fixture performed the real flow (link -> redis -> verify).
    Assert the JWT carries sub + the V1 workspace claim."""
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["ws"] == WS
    uuid.UUID(claims["sub"])  # valid user id
    assert claims["exp"] > 0


async def test_verify_bad_token_401(client):
    r = await client.post("/auth/verify", json={"token": "not-a-real-token"})
    assert r.status_code == 401


async def test_magic_link_token_is_one_time(client, authed):
    """A consumed token cannot be replayed."""
    import os

    import redis.asyncio as aioredis

    email = f"replay-{uuid.uuid4().hex[:8]}@atelier-suite.dev"
    r = await client.post("/auth/link", json={"email": email})
    assert r.status_code == 202
    rds = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    magic = None
    async for key in rds.scan_iter(match="magic:*"):
        if await rds.get(key) == email:
            magic = key.removeprefix("magic:")
            break
    await rds.aclose()
    assert magic
    r = await client.post("/auth/verify", json={"token": magic})
    assert r.status_code == 200
    r = await client.post("/auth/verify", json={"token": magic})
    assert r.status_code == 401


async def test_link_invalid_email_422(client):
    r = await client.post("/auth/link", json={"email": "not-an-email"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/projects"),
        ("POST", "/projects"),
        ("POST", "/designs"),
        ("GET", "/designs/00000000-0000-0000-0000-0000000000aa"),
        ("PATCH", "/designs/00000000-0000-0000-0000-0000000000aa"),
        ("GET", "/projects/00000000-0000-0000-0000-0000000000aa/designs"),
        ("POST", "/media/upload-url"),
        ("POST", "/media/commit"),
        ("GET", "/designs/00000000-0000-0000-0000-0000000000aa/media"),
        ("GET", "/inbox"),
        ("POST", "/inbox/00000000-0000-0000-0000-0000000000aa/triage"),
        ("GET", "/designs/00000000-0000-0000-0000-0000000000aa/timeline"),
        ("POST", "/entries"),
        ("PATCH", "/entries/00000000-0000-0000-0000-0000000000aa"),
    ],
)
async def test_missing_bearer_401_everywhere(client, method, path):
    r = await client.request(method, path)
    assert r.status_code == 401


async def test_garbage_bearer_401(client):
    r = await client.get("/projects", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
