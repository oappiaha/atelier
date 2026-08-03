"""Workspace-wide Studies gallery — GET /studies (additive; §9's table stops
at the per-design list).

One row per study across ALL designs, newest first, carrying what a gallery
card renders: design name, base-media thumb, palette (name + member hexes in
color_ids order), status, colorway counts (planned / ready incl. pinned /
pinned), actual spend, and a hero image — the first PINNED colorway's thumb
in permutation order, else the first ready one, else null.

Colorway fixture strategy mirrors test_studies' region strategy: the study
config goes through the real API; the colorway rows themselves are inserted
directly, shaped exactly like _persist_plan + the executor write them — the
generation pipeline is covered end-to-end in test_generation.py, and direct
rows give exact, deterministic statuses/thumb keys for the projection.

Workspace isolation: V1 seeds exactly one workspace and auth always resolves
to it, so a second workspace is inserted directly and its JWT minted with the
app's secret — get_ctx trusts the claims; the queries must filter on ws.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import psycopg
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.test_studies import (  # noqa: F401 — sanzo is a fixture import
    THREE_SLOTS,
    _create_study,
    _design_media,
    _insert_regions,
    _slot,
    sanzo,
)


def _db():
    return psycopg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))


@pytest_asyncio.fixture()
async def gallery_setup(sanzo, authed, design_factory, upload_media):  # noqa: F811
    """design + attached media + regions — test_studies' study_setup shape."""
    design, media = await _design_media(authed, design_factory, upload_media)
    return {"design": design, "media": media, "regions": _insert_regions(media)}


async def _three_slot_study(authed, setup, palette_id="c121") -> dict:
    study = await _create_study(authed, setup, palette_id=palette_id)
    r = await authed.put(
        f"/studies/{study['id']}/slots",
        json=[_slot(setup, labels, name) for labels, name in THREE_SLOTS],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _insert_colorways(study_id: str, statuses: list[tuple[str, bool]]) -> list[str | None]:
    """Permutation + colorway rows in permutation (idx) order; each entry is
    (status, has_thumb). Returns the thumb keys (None where has_thumb=False)."""
    thumbs: list[str | None] = []
    with _db() as conn:
        (ws,) = conn.execute(
            "SELECT workspace_id FROM studies WHERE id = %s", (study_id,)
        ).fetchone()
        for idx, (status, has_thumb) in enumerate(statuses):
            (pid,) = conn.execute(
                """
                INSERT INTO permutations (study_id, idx, mapping, rank_score, signature)
                VALUES (%s, %s, %s::jsonb, %s, %s::real[])
                RETURNING id
                """,
                (study_id, idx, json.dumps({"0": 32, "1": 50, "2": 108}),
                 1.0 - idx * 0.01, [1.0, 2.0]),
            ).fetchone()
            thumb = f"cw/{ws}/gallery-{uuid.uuid4().hex}.400.webp" if has_thumb else None
            conn.execute(
                """
                INSERT INTO colorways (workspace_id, study_id, permutation_id,
                                       status, image_key, thumb_key)
                VALUES (%s, %s, %s, %s::colorway_status, %s, %s)
                """,
                (ws, study_id, pid, status,
                 thumb and thumb.replace(".400.webp", ".png"), thumb),
            )
            thumbs.append(thumb)
    return thumbs


@pytest_asyncio.fixture()
async def ws2(isolated_infra):
    """(client, workspace_id) for a SECOND workspace."""
    from app.config import get_settings
    from app.main import app

    with _db() as conn:
        (ws,) = conn.execute(
            "INSERT INTO workspaces (name) VALUES ('elsewhere') RETURNING id"
        ).fetchone()
        (uid,) = conn.execute(
            "INSERT INTO users (email) VALUES (%s) RETURNING id",
            (f"ws2-{uuid.uuid4().hex[:10]}@atelier-suite.dev",),
        ).fetchone()
        conn.execute(
            "INSERT INTO memberships (workspace_id, user_id) VALUES (%s, %s)",
            (ws, uid),
        )
    claims = {
        "sub": str(uid), "ws": str(ws),
        "exp": datetime.now(UTC) + timedelta(days=1),
    }
    token = jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c, str(ws)


def _insert_foreign_study(ws: str) -> str:
    """A full project→design→media→study chain owned by another workspace."""
    with _db() as conn:
        (proj,) = conn.execute(
            "INSERT INTO projects (workspace_id, name) VALUES (%s, 'theirs') RETURNING id",
            (ws,),
        ).fetchone()
        (design,) = conn.execute(
            """
            INSERT INTO designs (workspace_id, project_id, name, index_no)
            VALUES (%s, %s, 'foreign duffle', 1) RETURNING id
            """,
            (ws, proj),
        ).fetchone()
        (media,) = conn.execute(
            """
            INSERT INTO media (workspace_id, design_id, kind, r2_key, sha256)
            VALUES (%s, %s, 'image', %s, %s) RETURNING id
            """,
            (ws, design, f"src/{ws}/{uuid.uuid4().hex}.png", uuid.uuid4().hex * 2),
        ).fetchone()
        (study,) = conn.execute(
            """
            INSERT INTO studies (workspace_id, design_id, base_media_id, palette_id,
                                 policy, model_id, prompt_version)
            VALUES (%s, %s, %s, 'c121', '{}'::jsonb, 'seedream-5.0-pro', 'v2')
            RETURNING id
            """,
            (ws, design, media),
        ).fetchone()
    return str(study)


# ── auth + empty case ────────────────────────────────────────────────────────

async def test_gallery_requires_auth(client):
    assert (await client.get("/studies")).status_code == 401


async def test_empty_workspace_is_an_empty_list(ws2):
    """A workspace with no studies gets [] — regardless of what every other
    test has minted in the main workspace."""
    c, _ = ws2
    r = await c.get("/studies")
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── multi-design projection ──────────────────────────────────────────────────

async def test_gallery_spans_designs_newest_first(
    authed, gallery_setup, design_factory, upload_media
):
    setup_a = gallery_setup
    study_a = await _three_slot_study(authed, setup_a)
    r = await authed.post(f"/studies/{study_a['id']}/estimate")
    assert r.status_code == 200, r.text

    design_b, media_b = await _design_media(authed, design_factory, upload_media)
    setup_b = {"design": design_b, "media": media_b, "regions": _insert_regions(media_b)}
    study_b = await _create_study(authed, setup_b)  # bare draft, no slots

    r = await authed.get("/studies")
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()}
    assert {study_a["id"], study_b["id"]} <= rows.keys()

    # newest first — b was created after a
    order = [row["id"] for row in r.json()]
    assert order.index(study_b["id"]) < order.index(study_a["id"])

    a, b = rows[study_a["id"]], rows[study_b["id"]]
    assert a["design_id"] == setup_a["design"]["id"]
    assert a["design_name"] == setup_a["design"]["name"]
    assert b["design_name"] == design_b["name"]

    # palette projection: name + member hexes in color_ids order
    palette = (await authed.get("/palettes/c121")).json()
    for row in (a, b):
        assert row["palette_id"] == "c121"
        assert row["palette_name"] == palette["name"]
        assert row["palette_hexes"] == [c["hex"] for c in palette["colors"]]
        assert row["status"] == "draft"
        assert row["actual_cost_cents"] == 0
        assert row["hero_thumb_url"] is None  # nothing generated yet
        assert row["base_thumb_url"] is None  # thumbs worker never runs here

    # counts: estimated study falls back to perm_planned; bare draft is 0
    assert (a["planned"], a["ready"], a["pinned"]) == (6, 0, 0)
    assert (b["planned"], b["ready"], b["pinned"]) == (0, 0, 0)


# ── hero pick + counts over real colorway rows ───────────────────────────────

async def test_hero_prefers_pinned_over_ready(authed, gallery_setup):
    """The pinned colorway wins even when a ready one comes first in
    permutation order; counts follow the ColorwaysOut convention."""
    study = await _three_slot_study(authed, gallery_setup)
    thumbs = _insert_colorways(
        study["id"],
        [("ready", True), ("ready", True), ("pinned", True), ("planned", False)],
    )
    with _db() as conn:
        conn.execute(
            "UPDATE studies SET status = 'partial', actual_cost_cents = 101 "
            "WHERE id = %s",
            (study["id"],),
        )

    row = next(
        r for r in (await authed.get("/studies")).json() if r["id"] == study["id"]
    )
    assert row["status"] == "partial"
    assert row["actual_cost_cents"] == 101
    assert (row["planned"], row["ready"], row["pinned"]) == (4, 3, 1)
    assert thumbs[2] in row["hero_thumb_url"]  # the pinned one, not idx 0


async def test_hero_falls_back_to_first_ready(authed, gallery_setup):
    """No pin → the first ready colorway in permutation order is the hero;
    generating/failed/rejected rows never supply it."""
    study = await _three_slot_study(authed, gallery_setup)
    thumbs = _insert_colorways(
        study["id"],
        [("failed", False), ("rejected", True), ("ready", True), ("ready", True)],
    )
    row = next(
        r for r in (await authed.get("/studies")).json() if r["id"] == study["id"]
    )
    assert (row["planned"], row["ready"], row["pinned"]) == (4, 2, 0)
    assert thumbs[2] in row["hero_thumb_url"]  # first READY, not the rejected


# ── workspace isolation ──────────────────────────────────────────────────────

async def test_gallery_is_workspace_scoped(authed, ws2, gallery_setup):
    ours = await _create_study(authed, gallery_setup)
    c2, ws2_id = ws2
    theirs = _insert_foreign_study(ws2_id)

    mine = (await authed.get("/studies")).json()
    assert ours["id"] in {r["id"] for r in mine}
    assert theirs not in {r["id"] for r in mine}

    other = (await c2.get("/studies")).json()
    assert [r["id"] for r in other] == [theirs]
    assert other[0]["design_name"] == "foreign duffle"
