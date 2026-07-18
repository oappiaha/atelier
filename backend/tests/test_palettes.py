"""Sanzo seed (idempotency, TDD §2.3 counts) + Palette Library API (TDD §9).

The seed runs against the isolated `atelier_test` DB (conftest exports
DATABASE_URL before any app import); the dev DB is covered by the session
preservation guard.
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.wada.seed_sanzo import CORPUS_PATH, build_rows

EXPECTED_COLORS = 159
EXPECTED_PALETTES = 348


@pytest_asyncio.fixture(scope="session")
async def sanzo(isolated_infra):
    """Seed the corpus into the test DB, once per session."""
    from app.db import SessionLocal
    from app.wada.seed_sanzo import seed

    async with SessionLocal() as session:
        counts = await seed(session)
        await session.commit()
    return counts


# ── seed ─────────────────────────────────────────────────────────────────────

async def test_seed_counts_match_tdd(sanzo):
    """TDD §2.3: 159 colours, 348 combinations."""
    assert sanzo == (EXPECTED_COLORS, EXPECTED_PALETTES)


async def test_seed_is_idempotent(sanzo):
    """Re-running the seed converges — no duplicates, same counts."""
    from app.db import SessionLocal
    from app.wada.seed_sanzo import seed

    async with SessionLocal() as session:
        counts = await seed(session)
        await session.commit()
    assert counts == (EXPECTED_COLORS, EXPECTED_PALETTES)
    async with SessionLocal() as session:
        dupes = (
            await session.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM (SELECT id FROM sanzo_colors"
                    "        GROUP BY id HAVING COUNT(*) > 1) d),"
                    "       (SELECT COUNT(*) FROM (SELECT id FROM palettes"
                    "        GROUP BY id HAVING COUNT(*) > 1) d2)"
                )
            )
        ).one()
    assert tuple(dupes) == (0, 0)


async def test_seeded_rows_match_corpus_spot_checks(sanzo):
    """Known Wada colours: DB name+hex match the source JSON, ids are corpus
    order (1-based), and derived columns are self-consistent."""
    corpus = json.loads(CORPUS_PATH.read_text())
    from app.db import SessionLocal

    picks = [0, 46, 158]  # first, middle (Cream Yellow), last
    async with SessionLocal() as session:
        for idx in picks:
            row = (
                await session.execute(
                    text("SELECT * FROM sanzo_colors WHERE id = :id"), {"id": idx + 1}
                )
            ).one()
            assert row.name == corpus[idx]["name"]
            assert row.hex.strip() == corpus[idx]["hex"].lower()
            assert row.chroma == pytest.approx(
                (row.lab_a**2 + row.lab_b**2) ** 0.5, rel=1e-4
            )
        # a known combination: every colour listing combo 176 is in c176
        member_ids = [i + 1 for i, c in enumerate(corpus) if 176 in c["combinations"]]
        pal = (
            await session.execute(
                text("SELECT * FROM palettes WHERE id = 'c176'")
            )
        ).one()
        assert list(pal.color_ids) == member_ids
        assert pal.color_count == len(member_ids)
        assert pal.min_delta_e <= pal.max_delta_e


def test_build_rows_is_pure_and_complete():
    corpus = json.loads(CORPUS_PATH.read_text())
    colors, palettes = build_rows(corpus)
    assert len(colors) == EXPECTED_COLORS
    assert len(palettes) == EXPECTED_PALETTES
    # TDD §2.3: combos are 2..4 colours — corpus: 120×2, 120×3, 108×4
    from collections import Counter

    sizes = Counter(p["color_count"] for p in palettes)
    assert sizes == {2: 120, 3: 120, 4: 108}
    assert all(2 <= p["color_count"] <= 4 for p in palettes)
    assert {c["hue_family"] for c in colors} <= {
        "red", "orange", "yellow", "green", "cyan", "blue", "purple", "neutral"
    }
    assert {p["temperature"] for p in palettes} <= {"warm", "cool", "neutral"}


# ── GET /palettes facets ─────────────────────────────────────────────────────

async def test_list_all_palettes(sanzo, authed):
    r = await authed.get("/palettes")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == EXPECTED_PALETTES
    first = body[0]
    assert first["id"] == "c1"
    assert len(first["colors"]) == first["color_count"]
    assert first["colors"][0]["hex"].startswith("#")


async def test_facet_count(sanzo, authed):
    r = await authed.get("/palettes", params={"count": 3})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 120
    assert all(p["color_count"] == 3 for p in body)


async def test_facet_temp(sanzo, authed):
    r = await authed.get("/palettes", params={"temp": "warm"})
    assert r.status_code == 200
    assert all(p["temperature"] == "warm" for p in r.json())
    # the three temps partition the corpus
    total = 0
    for t in ("warm", "cool", "neutral"):
        total += len((await authed.get("/palettes", params={"temp": t})).json())
    assert total == EXPECTED_PALETTES


async def test_facet_contains(sanzo, authed):
    corpus = json.loads(CORPUS_PATH.read_text())
    expected = {f"c{n}" for n in corpus[0]["combinations"]}  # colour id 1
    r = await authed.get("/palettes", params={"contains": 1})
    assert r.status_code == 200
    body = r.json()
    assert {p["id"] for p in body} == expected
    assert all(1 in p["color_ids"] for p in body)


async def test_facet_hue(sanzo, authed):
    r = await authed.get("/palettes", params={"hue": "blue"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) > 0
    assert all(
        any(c["hue_family"] == "blue" for c in p["colors"]) for p in body
    )


async def test_facet_q_matches_member_colour_name(sanzo, authed):
    r = await authed.get("/palettes", params={"q": "cream yellow"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) > 0
    assert all(
        any("cream yellow" in c["name"].lower() for c in p["colors"])
        or "cream yellow" in p["name"].lower()
        for p in body
    )


async def test_facets_combine(sanzo, authed):
    r = await authed.get("/palettes", params={"count": 2, "temp": "cool"})
    assert r.status_code == 200
    assert all(
        p["color_count"] == 2 and p["temperature"] == "cool" for p in r.json()
    )


async def test_slots_preview_is_the_8_13_3_readout(sanzo, authed):
    """§8.13.3: 'with your 3 slots: 6 perms, ~$1.01' on a 3-colour palette."""
    r = await authed.get("/palettes", params={"count": 3, "slots": 3})
    assert r.status_code == 200
    p = r.json()[0]
    assert p["preview"] == {
        "slots": 3,
        "perms": 6,
        "naive_calls": 18,
        "trie_calls": 15,
        "est_cost": "1.01",
    }


async def test_invalid_facets_are_422(sanzo, authed):
    for params in (
        {"count": 5},
        {"temp": "tepid"},
        {"hue": "mauve"},
        {"slots": 0},
        {"slots": 99},
    ):
        r = await authed.get("/palettes", params=params)
        assert r.status_code == 422, params


# ── GET /palettes/{id} ───────────────────────────────────────────────────────

async def test_palette_detail_with_similar(sanzo, authed):
    r = await authed.get("/palettes/c102")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "c102"
    assert len(body["colors"]) == body["color_count"]
    similar = body["similar"]
    assert 0 < len(similar) <= 6
    assert all(s["palette"]["id"] != "c102" for s in similar)
    # documented rule: same colour count, nearest mean-Lab first
    assert all(s["palette"]["color_count"] == body["color_count"] for s in similar)
    distances = [s["distance"] for s in similar]
    assert distances == sorted(distances)


async def test_palette_detail_unknown_is_404(sanzo, authed):
    r = await authed.get("/palettes/c9999")
    assert r.status_code == 404


# ── auth negatives ───────────────────────────────────────────────────────────

async def test_palettes_require_auth(sanzo, client):
    assert (await client.get("/palettes")).status_code == 401
    assert (await client.get("/palettes/c102")).status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert (await client.get("/palettes", headers=bad)).status_code == 401
