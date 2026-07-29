"""Test harness for the Atelier API.

Runs the FastAPI app IN-PROCESS (httpx ASGITransport — no port is bound)
against REAL isolated infra on the local docker-compose stack:

- Postgres :5433, database `atelier_test` (dropped + recreated + migrated
  to head at session start; the dev `atelier` DB is never written)
- MinIO :9000, bucket `atelier-test` (created if missing, objects wiped at
  session start; the dev `atelier` bucket is never written)
- Redis :6380, logical db 1 (dev uses db 0)

Isolation mechanism: the env vars below are exported BEFORE anything imports
`app.*`, so pydantic-settings (app/config.py, lru_cached) and the module-level
SQLAlchemy engine (app/db.py) bind to the test resources. No app source is
modified.

Preservation guard: dev DB row counts (projects/designs/entries/media) and the
dev bucket's object count are snapshotted before the first test and re-checked
after the last one; any drift fails the session.
"""

import os
from pathlib import Path

# ── isolation env — MUST run before any `app.*` import ──────────────────────
TEST_DB = "atelier_test"
TEST_BUCKET = "atelier-test"
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://atelier:atelier@localhost:5433/{TEST_DB}"
os.environ["S3_BUCKET"] = TEST_BUCKET
os.environ["REDIS_URL"] = "redis://localhost:6380/1"
# never let the suite spend: backend/.env carries a real PhotoRoom key, and
# env vars override the .env file — blank means cutout tasks skip unless a
# test monkeypatches the setting and mocks the HTTP boundary
os.environ["PHOTOROOM_API_KEY"] = ""
# .env flipped to PHOTOROOM_SANDBOX=false when billing went live (2026-07-29)
# — the suite pins the config DEFAULT (sandbox) so test behaviour never
# tracks prod-mode toggles in .env (test_cutout asserts the sandbox_ prefix)
os.environ["PHOTOROOM_SANDBOX"] = "true"

import uuid  # noqa: E402

import httpx  # noqa: E402
import psycopg  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from tests.util import make_png, sha256  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
ADMIN_DSN = "postgresql://atelier:atelier@localhost:5433/postgres"
DEV_DSN = "postgresql://atelier:atelier@localhost:5433/atelier"
DEV_BUCKET = "atelier"


# ── dev-resource preservation snapshot ───────────────────────────────────────

def _dev_snapshot() -> dict:
    snap: dict = {}
    with psycopg.connect(DEV_DSN) as conn:
        for table in ("projects", "designs", "entries", "media"):
            snap[f"db:{table}"] = conn.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 — fixed identifiers
            ).fetchone()[0]
    from app.storage import get_s3

    s3 = get_s3()
    count = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=DEV_BUCKET):
        count += page.get("KeyCount", 0)
    snap["bucket:objects"] = count
    return snap


# ── session infra: fresh test DB (migrated) + fresh test bucket ─────────────

@pytest.fixture(scope="session", autouse=True)
def isolated_infra():
    # sanity: never let the suite point at the dev DB/bucket
    assert TEST_DB in os.environ["DATABASE_URL"]
    assert os.environ["S3_BUCKET"] == TEST_BUCKET != DEV_BUCKET

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")  # env.py reads settings -> atelier_test

    from app.storage import get_s3

    s3 = get_s3()
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if TEST_BUCKET not in existing:
        s3.create_bucket(Bucket=TEST_BUCKET)
    else:  # wipe leftovers from previous runs
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=TEST_BUCKET):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                s3.delete_objects(Bucket=TEST_BUCKET, Delete={"Objects": keys})

    before = _dev_snapshot()
    yield
    after = _dev_snapshot()
    assert after == before, (
        f"PRESERVATION VIOLATION: dev DB/bucket changed during the suite: {before} -> {after}"
    )


# ── app clients (in-process ASGI; no port bound) ─────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def client(isolated_infra):
    """Unauthenticated client."""
    from app.db import engine
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def token(client) -> str:
    """A real JWT obtained through the actual magic-link flow: POST /auth/link
    stores a one-time token in Redis; we read it back (the log is the inbox in
    dev — Redis is the authoritative store) and exchange it via /auth/verify."""
    import redis.asyncio as aioredis

    # NB: pydantic EmailStr rejects special-use TLDs (.test, example.com)
    email = f"pytest-{uuid.uuid4().hex[:10]}@atelier-suite.dev"
    r = await client.post("/auth/link", json={"email": email})
    assert r.status_code == 202, r.text

    rds = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    magic = None
    async for key in rds.scan_iter(match="magic:*"):
        if await rds.get(key) == email:
            magic = key.removeprefix("magic:")
            break
    await rds.aclose()
    assert magic, "magic-link token not found in redis"

    r = await client.post("/auth/verify", json={"token": magic})
    assert r.status_code == 200, r.text
    return r.json()["jwt"]


@pytest_asyncio.fixture(scope="session")
async def authed(token):
    """Authenticated client."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


# ── data factories (everything goes through the real API) ───────────────────

@pytest_asyncio.fixture(scope="session")
async def project(authed) -> dict:
    r = await authed.post("/projects", json={"name": "pytest project", "kicker": "TESTS"})
    assert r.status_code == 201, r.text
    return r.json()


@pytest_asyncio.fixture()
async def design_factory(authed, project):
    async def _make(name: str | None = None, project_id: str | None = None, **extra) -> dict:
        r = await authed.post(
            "/designs",
            json={
                "project_id": project_id or project["id"],
                "name": name or f"design-{uuid.uuid4().hex[:8]}",
                **extra,
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    return _make


@pytest_asyncio.fixture()
async def upload_media(authed):
    """Full real pipeline: presigned upload-url -> PUT actual bytes to MinIO
    over the network -> POST /media/commit. Returns the MediaOut dict."""

    async def _upload(
        data: bytes | None = None,
        caption: str | None = None,
        entry_id: str | None = None,
    ) -> dict:
        data = data if data is not None else make_png()
        digest = sha256(data)
        r = await authed.post(
            "/media/upload-url",
            json={"sha256": digest, "content_type": "image/png", "kind": "image"},
        )
        assert r.status_code == 200, r.text
        u = r.json()
        if u["upload_url"]:
            async with httpx.AsyncClient() as raw:
                pr = await raw.put(
                    u["upload_url"], content=data, headers={"Content-Type": "image/png"}
                )
                assert pr.status_code == 200, pr.text
        body = {
            "sha256": digest,
            "r2_key": u["r2_key"],
            "kind": "image",
            "width": 8,
            "height": 8,
        }
        if caption is not None:
            body["caption"] = caption
        if entry_id is not None:
            body["entry_id"] = entry_id
        r = await authed.post("/media/commit", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    return _upload
