"""Projects: create + list (+design_count roll-up), validation."""

import uuid


async def test_create_project_201_shape(authed):
    r = await authed.post("/projects", json={"name": "Shape Check", "kicker": "KICK"})
    assert r.status_code == 201
    p = r.json()
    uuid.UUID(p["id"])
    assert p["name"] == "Shape Check"
    assert p["kicker"] == "KICK"
    assert p["design_count"] == 0


async def test_create_project_kicker_optional(authed):
    r = await authed.post("/projects", json={"name": "No Kicker"})
    assert r.status_code == 201
    assert r.json()["kicker"] is None


async def test_list_projects_contains_created(authed, project):
    r = await authed.get("/projects")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert project["name"] in names


async def test_design_count_rolls_up(authed, design_factory):
    r = await authed.post("/projects", json={"name": f"count-{uuid.uuid4().hex[:6]}"})
    pid = r.json()["id"]
    await design_factory(project_id=pid)
    await design_factory(project_id=pid)
    r = await authed.get("/projects")
    row = next(p for p in r.json() if p["id"] == pid)
    assert row["design_count"] == 2


async def test_create_project_missing_name_422(authed):
    r = await authed.post("/projects", json={"kicker": "NO NAME"})
    assert r.status_code == 422
