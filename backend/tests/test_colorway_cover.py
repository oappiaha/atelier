"""POST /colorways/{id}/cover (2026-08-06): a colorway becomes the product's
cover — pinned first (covers must not expire), cover points at the pin
bridge's archive media row."""

import uuid

from tests.test_generation import (  # noqa: F401 — fixtures
    _db,
    _run_enqueued,
    fal,
    gen_setup,
)
from tests.test_pin_export import _ready_colorways
from tests.test_pin_timeline import _uniquify_working_object
from tests.test_studies import sanzo  # noqa: F401 — fixture


async def test_cover_requires_auth(client):
    assert (await client.post(f"/colorways/{uuid.uuid4()}/cover")).status_code == 401


async def test_colorway_cover_pins_and_sets_cover(authed, gen_setup, fal):  # noqa: F811
    study, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")
    _uniquify_working_object(cw["id"])
    design_id = gen_setup["design"]["id"]

    r = await authed.post(f"/colorways/{cw['id']}/cover")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["design_id"] == design_id

    # pinned as a side effect; cover points at real archive media
    cws2 = (await authed.get(f"/studies/{study['id']}/colorways")).json()["colorways"]
    assert next(c for c in cws2 if c["id"] == cw["id"])["status"] == "pinned"
    d = (await authed.get(f"/designs/{design_id}")).json()
    assert d["cover_media_id"] == out["cover_media_id"]
    assert d["cover_url"]

    # idempotent re-cover
    r2 = await authed.post(f"/colorways/{cw['id']}/cover")
    assert r2.status_code == 200
    assert r2.json()["cover_media_id"] == out["cover_media_id"]


async def test_planned_ghost_cannot_be_cover(authed, gen_setup, fal):  # noqa: F811
    _, cws = await _ready_colorways(authed, gen_setup, fal)
    ghost = next((c for c in cws if c["status"] == "planned"), None)
    if ghost is None:  # eager default leaves ghosts; guard anyway
        return
    assert (await authed.post(f"/colorways/{ghost['id']}/cover")).status_code == 409
