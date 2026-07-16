"""Designs: create (auto index_no, defaults), get, list + ?status=, patch."""

import uuid


async def test_create_design_defaults_and_auto_index_no(authed, design_factory):
    r = await authed.post("/projects", json={"name": f"idx-{uuid.uuid4().hex[:6]}"})
    pid = r.json()["id"]
    d1 = await design_factory(project_id=pid, materials="calf leather", started_at="2026-06-01")
    d2 = await design_factory(project_id=pid)
    assert d1["status"] == "developing"  # DB default
    assert (d1["index_no"], d2["index_no"]) == (1, 2)  # MAX(index_no)+1 per project
    assert d1["materials"] == "calf leather"
    assert d1["cover_media_id"] is None


async def test_index_no_is_per_project(authed, design_factory):
    r1 = await authed.post("/projects", json={"name": f"pp1-{uuid.uuid4().hex[:6]}"})
    r2 = await authed.post("/projects", json={"name": f"pp2-{uuid.uuid4().hex[:6]}"})
    a = await design_factory(project_id=r1.json()["id"])
    b = await design_factory(project_id=r2.json()["id"])
    assert a["index_no"] == 1
    assert b["index_no"] == 1


async def test_get_design_shape(authed, design_factory):
    d = await design_factory()
    r = await authed.get(f"/designs/{d['id']}")
    assert r.status_code == 200
    body = r.json()
    assert {
        "id", "project_id", "name", "status", "index_no", "materials",
        "cover_media_id", "cover_url", "entry_count", "media_count", "created_at",
    } <= set(body)
    assert body["entry_count"] == 0
    assert body["media_count"] == 0
    assert body["cover_url"] is None


async def test_get_design_404(authed):
    r = await authed.get(f"/designs/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_list_designs_ordered_and_status_filter(authed, design_factory):
    r = await authed.post("/projects", json={"name": f"filt-{uuid.uuid4().hex[:6]}"})
    pid = r.json()["id"]
    d1 = await design_factory(project_id=pid)
    d2 = await design_factory(project_id=pid)
    pr = await authed.patch(f"/designs/{d2['id']}", json={"status": "in_production"})
    assert pr.status_code == 200

    r = await authed.get(f"/projects/{pid}/designs")
    assert r.status_code == 200
    assert [d["id"] for d in r.json()] == [d1["id"], d2["id"]]  # ORDER BY index_no

    r = await authed.get(f"/projects/{pid}/designs", params={"status": "in_production"})
    assert [d["id"] for d in r.json()] == [d2["id"]]

    r = await authed.get(f"/projects/{pid}/designs", params={"status": "archive"})
    assert r.json() == []


async def test_list_designs_bogus_status_422(authed, project):
    r = await authed.get(f"/projects/{project['id']}/designs", params={"status": "bogus"})
    assert r.status_code == 422


async def test_patch_design_fields(authed, design_factory):
    d = await design_factory()
    r = await authed.patch(
        f"/designs/{d['id']}", json={"name": "Renamed", "materials": "brass + twill"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert r.json()["materials"] == "brass + twill"
    # read-back through GET (authoritative)
    r = await authed.get(f"/designs/{d['id']}")
    assert r.json()["name"] == "Renamed"


async def test_patch_design_status(authed, design_factory):
    d = await design_factory()
    r = await authed.patch(f"/designs/{d['id']}", json={"status": "final"})
    assert r.status_code == 200
    assert r.json()["status"] == "final"


async def test_patch_design_invalid_status_422(authed, design_factory):
    d = await design_factory()
    r = await authed.patch(f"/designs/{d['id']}", json={"status": "shipped"})
    assert r.status_code == 422


async def test_patch_design_empty_body_422(authed, design_factory):
    d = await design_factory()
    r = await authed.patch(f"/designs/{d['id']}", json={})
    assert r.status_code == 422


async def test_patch_design_404(authed):
    r = await authed.patch(f"/designs/{uuid.uuid4()}", json={"name": "ghost"})
    assert r.status_code == 404


async def test_create_design_unknown_project_404(authed):
    """FK violation (project_id) must map to a clean 404, not a 500 (T5)."""
    r = await authed.post(
        "/designs", json={"project_id": str(uuid.uuid4()), "name": "orphan"}
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "project not found"}


async def test_patch_design_unknown_cover_media_404(authed, design_factory):
    """FK violation (cover_media_id) must map to a clean 404, not a 500 (T5)."""
    d = await design_factory()
    r = await authed.patch(f"/designs/{d['id']}", json={"cover_media_id": str(uuid.uuid4())})
    assert r.status_code == 404
    assert r.json() == {"detail": "media not found"}
    # preservation: the design is untouched (cover not set)
    r = await authed.get(f"/designs/{d['id']}")
    assert r.status_code == 200
    assert r.json()["cover_media_id"] is None
