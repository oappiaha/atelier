"""Entries + timeline: create with media attach, phase denormalisation,
?phase= filter, patch (incl. the entry->media phase sync trigger)."""

import uuid


async def test_create_entry_attaches_media_and_denormalises(
    authed, design_factory, upload_media
):
    d = await design_factory()
    m1 = await upload_media()
    m2 = await upload_media()
    r = await authed.post(
        "/entries",
        json={
            "design_id": d["id"],
            "phase": "manufacturing",
            "body": "Sample #1 back from the workshop.",
            "media_ids": [m1["id"], m2["id"]],
        },
    )
    assert r.status_code == 201
    e = r.json()
    assert e["phase"] == "manufacturing"
    assert e["is_open"] is False
    assert {m["id"] for m in e["media"]} == {m1["id"], m2["id"]}
    # trigger denormalises phase + design onto media rows
    assert all(m["phase"] == "manufacturing" and m["design_id"] == d["id"] for m in e["media"])

    # counts visible on the design (authoritative read-back)
    r = await authed.get(f"/designs/{d['id']}")
    assert r.json()["entry_count"] == 1
    assert r.json()["media_count"] == 2


async def test_create_entry_bogus_phase_422(authed, design_factory):
    d = await design_factory()
    r = await authed.post("/entries", json={"design_id": d["id"], "phase": "bogus"})
    assert r.status_code == 422


async def test_timeline_ordering_media_and_filter(authed, design_factory, upload_media):
    d = await design_factory()
    m = await upload_media()
    r = await authed.post(
        "/entries",
        json={
            "design_id": d["id"],
            "phase": "moodboard",
            "body": "references",
            "occurred_at": "2026-06-01T10:00:00Z",
            "media_ids": [m["id"]],
        },
    )
    assert r.status_code == 201
    r = await authed.post(
        "/entries",
        json={
            "design_id": d["id"],
            "phase": "sketch",
            "body": "first profile",
            "occurred_at": "2026-06-05T10:00:00Z",
        },
    )
    assert r.status_code == 201

    r = await authed.get(f"/designs/{d['id']}/timeline")
    assert r.status_code == 200
    tl = r.json()
    assert [e["phase"] for e in tl] == ["sketch", "moodboard"]  # occurred_at DESC
    mood = tl[1]
    assert [x["id"] for x in mood["media"]] == [m["id"]]  # media attached

    r = await authed.get(f"/designs/{d['id']}/timeline", params={"phase": "sketch"})
    assert [e["phase"] for e in r.json()] == ["sketch"]

    r = await authed.get(f"/designs/{d['id']}/timeline", params={"phase": "not-a-phase"})
    assert r.status_code == 422


async def test_patch_entry_body_and_is_open(authed, design_factory):
    d = await design_factory()
    r = await authed.post(
        "/entries", json={"design_id": d["id"], "phase": "note", "body": "v1"}
    )
    eid = r.json()["id"]
    r = await authed.patch(f"/entries/{eid}", json={"body": "v2 revised", "is_open": True})
    assert r.status_code == 200
    assert r.json()["body"] == "v2 revised"
    assert r.json()["is_open"] is True

    # read-back via timeline
    r = await authed.get(f"/designs/{d['id']}/timeline")
    e = next(x for x in r.json() if x["id"] == eid)
    assert e["body"] == "v2 revised" and e["is_open"] is True


async def test_patch_entry_phase_syncs_media_phase(authed, design_factory, upload_media):
    """The entry_phase_sync trigger must re-stamp attached media."""
    d = await design_factory()
    m = await upload_media()
    r = await authed.post(
        "/entries",
        json={"design_id": d["id"], "phase": "sketch", "media_ids": [m["id"]]},
    )
    eid = r.json()["id"]
    r = await authed.patch(f"/entries/{eid}", json={"phase": "final"})
    assert r.status_code == 200
    assert r.json()["phase"] == "final"

    r = await authed.get(f"/designs/{d['id']}/media", params={"phase": "final"})
    assert [x["id"] for x in r.json()] == [m["id"]]
    r = await authed.get(f"/designs/{d['id']}/media", params={"phase": "sketch"})
    assert r.json() == []


async def test_patch_entry_bogus_phase_422(authed, design_factory):
    d = await design_factory()
    r = await authed.post("/entries", json={"design_id": d["id"], "phase": "note"})
    r = await authed.patch(f"/entries/{r.json()['id']}", json={"phase": "bogus"})
    assert r.status_code == 422


async def test_patch_entry_empty_body_422(authed, design_factory):
    d = await design_factory()
    r = await authed.post("/entries", json={"design_id": d["id"], "phase": "note"})
    r = await authed.patch(f"/entries/{r.json()['id']}", json={})
    assert r.status_code == 422


async def test_patch_entry_404(authed):
    r = await authed.patch(f"/entries/{uuid.uuid4()}", json={"body": "ghost"})
    assert r.status_code == 404


async def test_create_entry_unknown_design_404(authed):
    """FK violation (design_id) must map to a clean 404, not a 500 (T5)."""
    r = await authed.post(
        "/entries", json={"design_id": str(uuid.uuid4()), "phase": "note", "body": "orphan"}
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "design not found"}
