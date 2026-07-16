"""Share links (M3-T1): POST /share mint + PUBLIC GET /s/{slug} projection.

Contract under test (TDD §4): "Share links are unauthenticated. A separate
router with no auth dependency, resolving by slug, returning a projection
that contains no internal ids. Rate limited to 60 req/min per IP."

Scope reading (PRD A7/A8, documented in app/routers/share.py): 'finals' =
phases (final, editorial) — the gallery is "Finals and Editorial"; 'full' =
the whole timeline including text notes. Idempotency (PRD A8 "One view-only
public URL per project gallery"): re-mint of the same target+scope returns
the existing live link with 200, not a second link.
"""

import os
import re
import uuid

import httpx
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import text

from tests.util import make_png

SLUG_RE = re.compile(r"^[a-z0-9]+-[a-z0-9]{4}$")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def uuid_strings(obj, path="$") -> list[tuple[str, str]]:
    """Recursively collect every uuid-shaped string anywhere in a JSON value
    (keys included). The public projection must yield ZERO hits."""
    hits: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += uuid_strings(k, f"{path}.{k}")
            hits += uuid_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += uuid_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str) and UUID_RE.search(obj):
        hits.append((path, obj))
    return hits


async def db_exec(sql: str, params: dict):
    from app.db import engine

    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        return result.all() if result.returns_rows else None


async def clear_rate_limit_keys() -> None:
    r = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    async for key in r.scan_iter(match="shr-rl:*"):
        await r.delete(key)
    await r.aclose()


# ── shared gallery fixture (built once, through the real API) ────────────────

_g: dict = {}


@pytest_asyncio.fixture()
async def gallery(authed, project, design_factory, upload_media) -> dict:
    if _g:
        return _g
    design = await design_factory(name="Share Bag")
    for phase in ("final", "editorial", "sketch"):
        r = await authed.post("/entries", json={"design_id": design["id"], "phase": phase})
        assert r.status_code == 201, r.text
        data = make_png()
        media = await upload_media(data, caption=f"{phase} shot", entry_id=r.json()["id"])
        _g[f"media_{phase}"] = media
        _g[f"bytes_{phase}"] = data
    r = await authed.post(
        "/entries",
        json={"design_id": design["id"], "phase": "manufacturing", "body": "brass rivets, 4mm"},
    )
    assert r.status_code == 201, r.text
    empty = await design_factory(name="Nothing To Show")
    _g.update(project=project, design=design, empty_design=empty)
    return _g


async def mint(authed, expect: int | tuple = (200, 201), **body) -> dict:
    """expect=201 pins 'created', expect=200 pins 'reused existing'; the
    default tolerates either so tests don't depend on execution order."""
    expected = (expect,) if isinstance(expect, int) else expect
    r = await authed.post("/share", json=body)
    assert r.status_code in expected, r.text
    return r.json()


# ── mint ─────────────────────────────────────────────────────────────────────

async def test_mint_project_link_shape(authed, gallery):
    out = await mint(authed, expect=201, project_id=gallery["project"]["id"], scope="full")
    assert SLUG_RE.match(out["slug"]), out["slug"]
    assert out["slug"].startswith("pytestproject-")  # slugified target name (TDD 'reibyrei-x7k2')
    assert out["scope"] == "full"
    assert out["project_id"] == gallery["project"]["id"]
    assert out["design_id"] is None
    assert out["url"] == f"/s/{out['slug']}"
    assert out["view_count"] == 0 and out["revoked_at"] is None


async def test_mint_design_link_defaults_to_finals(authed, gallery):
    out = await mint(authed, expect=201, design_id=gallery["design"]["id"])
    assert out["scope"] == "finals"  # schema default, PRD A8 "Finals only, or full timeline"
    assert out["design_id"] == gallery["design"]["id"] and out["project_id"] is None
    assert out["slug"].startswith("sharebag-")


async def test_mint_requires_auth(client):
    r = await client.post("/share", json={"project_id": str(uuid.uuid4())})
    assert r.status_code == 401


async def test_mint_rejects_both_and_neither(authed, gallery):
    r = await authed.post(
        "/share",
        json={"project_id": gallery["project"]["id"], "design_id": gallery["design"]["id"]},
    )
    assert r.status_code == 422
    r = await authed.post("/share", json={})
    assert r.status_code == 422


async def test_mint_rejects_bad_scope(authed, gallery):
    r = await authed.post(
        "/share", json={"project_id": gallery["project"]["id"], "scope": "everything"}
    )
    assert r.status_code == 422


async def test_mint_unknown_target_404(authed):
    r = await authed.post("/share", json={"project_id": str(uuid.uuid4())})
    assert r.status_code == 404
    r = await authed.post("/share", json={"design_id": str(uuid.uuid4())})
    assert r.status_code == 404


async def test_mint_idempotent_per_target_and_scope(authed, gallery):
    first = await mint(authed, expect=201, project_id=gallery["project"]["id"], scope="finals")
    again = await mint(authed, expect=200, project_id=gallery["project"]["id"], scope="finals")
    assert again["id"] == first["id"] and again["slug"] == first["slug"]
    other_scope = await mint(authed, expect=200, project_id=gallery["project"]["id"], scope="full")
    # 'full' already minted in test_mint_project_link_shape — still ONE link per (target, scope)
    assert other_scope["slug"] != first["slug"]


# ── public projection (NO auth) ──────────────────────────────────────────────

async def test_public_projection_no_auth_finals_scope(client, authed, gallery):
    link = await mint(authed, expect=200, design_id=gallery["design"]["id"], scope="finals")
    r = await client.get(f"/s/{link['slug']}")  # unauthenticated client, no bearer
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "finals" and body["target"] == "design"
    assert body["project"]["name"] == gallery["project"]["name"]
    [d] = body["designs"]
    assert d["name"] == "Share Bag"
    # PRD A7: the gallery is Finals AND Editorial — sketch stays private
    assert {m["phase"] for m in d["media"]} == {"final", "editorial"}
    assert all(m["url"] == f"/s/{link['slug']}/m/{m['index']}" for m in d["media"])
    assert d["notes"] == []  # text notes belong to the full timeline only


async def test_public_projection_full_scope(client, authed, gallery):
    link = await mint(authed, expect=200, project_id=gallery["project"]["id"], scope="full")
    r = await client.get(f"/s/{link['slug']}")
    assert r.status_code == 200, r.text
    body = r.json()
    d = next(x for x in body["designs"] if x["name"] == "Share Bag")
    assert {"final", "editorial", "sketch"} <= {m["phase"] for m in d["media"]}
    assert any(n["body"] == "brass rivets, 4mm" for n in d["notes"])  # full timeline incl. notes
    # a design with no media has nothing to show in a gallery
    assert "Nothing To Show" not in [x["name"] for x in body["designs"]]


async def test_projection_contains_zero_uuid_shaped_strings(client, authed, gallery):
    for scope in ("finals", "full"):
        link = await mint(authed, expect=200, project_id=gallery["project"]["id"], scope=scope)
        r = await client.get(f"/s/{link['slug']}")
        assert r.status_code == 200
        hits = uuid_strings(r.json())
        assert hits == [], f"internal ids leaked in {scope} projection: {hits}"


async def test_unknown_slug_404(client):
    r = await client.get("/s/doesnotexist-zzzz")
    assert r.status_code == 404


async def test_revoked_slug_404(client, authed, gallery):
    link = await mint(authed, design_id=gallery["design"]["id"], scope="full")
    slug = link["slug"]
    assert (await client.get(f"/s/{slug}")).status_code == 200
    # no revoke endpoint in the TDD API table — revoked_at is set via SQL
    await db_exec(
        "UPDATE share_links SET revoked_at = now() WHERE slug = :slug", {"slug": slug}
    )
    assert (await client.get(f"/s/{slug}")).status_code == 404
    assert (await client.get(f"/s/{slug}/m/0")).status_code == 404


async def test_view_count_increments(client, authed, gallery):
    link = await mint(authed, expect=200, design_id=gallery["design"]["id"], scope="finals")
    [(before,)] = await db_exec(
        "SELECT view_count FROM share_links WHERE slug = :slug", {"slug": link["slug"]}
    )
    for _ in range(2):
        assert (await client.get(f"/s/{link['slug']}")).status_code == 200
    [(after,)] = await db_exec(
        "SELECT view_count FROM share_links WHERE slug = :slug", {"slug": link["slug"]}
    )
    assert after == before + 2


async def test_media_redirect_serves_presigned_bytes(client, authed, gallery):
    link = await mint(authed, expect=200, design_id=gallery["design"]["id"], scope="finals")
    r = await client.get(f"/s/{link['slug']}")
    [d] = r.json()["designs"]
    final = next(m for m in d["media"] if m["phase"] == "final")
    rr = await client.get(final["url"])
    assert rr.status_code == 307
    location = rr.headers["location"]
    assert location.startswith(os.environ.get("S3_ENDPOINT", "http://localhost:9000"))
    assert "X-Amz-Signature" in location  # presigned GET — no app auth needed
    async with httpx.AsyncClient() as raw:
        got = await raw.get(location)
    assert got.status_code == 200
    assert got.content == gallery["bytes_final"]  # the exact uploaded object


async def test_thumb_redirect_and_absence(client, authed, gallery):
    from app.workers.thumbs import generate_thumbs

    link = await mint(authed, expect=200, design_id=gallery["design"]["id"], scope="finals")
    r = await client.get(f"/s/{link['slug']}")
    [d] = r.json()["designs"]
    target = d["media"][0]
    assert target["thumb_url"] is None  # no worker ran yet
    assert (await client.get(f"/s/{link['slug']}/m/{target['index']}/thumb")).status_code == 404

    media_id = gallery[f"media_{target['phase']}"]["id"]
    generate_thumbs(media_id)  # Celery local-execution path, isolated bucket
    r = await client.get(f"/s/{link['slug']}")
    [d] = r.json()["designs"]
    refreshed = next(m for m in d["media"] if m["index"] == target["index"])
    assert refreshed["thumb_url"] == f"/s/{link['slug']}/m/{target['index']}/thumb"
    rr = await client.get(refreshed["thumb_url"])
    assert rr.status_code == 307 and "X-Amz-Signature" in rr.headers["location"]


async def test_media_index_out_of_range_404(client, authed, gallery):
    link = await mint(authed, expect=200, design_id=gallery["design"]["id"], scope="finals")
    assert (await client.get(f"/s/{link['slug']}/m/999")).status_code == 404


# ── rate limit (TDD §4: 60 req/min per IP; knob lowered for the test) ────────

async def test_rate_limit_429_past_limit(client, authed, gallery, monkeypatch):
    from app.config import get_settings

    link = await mint(authed, expect=200, design_id=gallery["design"]["id"], scope="finals")
    monkeypatch.setattr(get_settings(), "share_rate_limit_per_min", 5)
    await clear_rate_limit_keys()
    try:
        statuses = [(await client.get(f"/s/{link['slug']}")).status_code for _ in range(6)]
        assert statuses[:5] == [200] * 5
        r = await client.get(f"/s/{link['slug']}")
        assert statuses[5] == 429 and r.status_code == 429
        assert r.headers.get("retry-after") == "60"
        # authenticated surfaces are NOT rate limited by the public router
        assert (await authed.get(f"/designs/{gallery['design']['id']}")).status_code == 200
    finally:
        await clear_rate_limit_keys()  # don't poison later public-router tests
