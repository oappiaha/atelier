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


async def test_wordmark_save_read_remove(authed, project):
    import base64

    from tests.util import make_png

    value = "data:image/png;base64," + base64.b64encode(make_png()).decode()
    path = f"/projects/{project['id']}/wordmark"
    assert (await authed.patch(path, json={"wordmark": value})).status_code == 204
    rows = (await authed.get("/projects")).json()
    saved = next(p for p in rows if p["id"] == project["id"])
    assert saved["wordmark"] == value
    assert saved["name"] == project["name"]
    assert (await authed.patch(path, json={"wordmark": None})).status_code == 204
    rows = (await authed.get("/projects")).json()
    assert next(p for p in rows if p["id"] == project["id"])["wordmark"] is None


async def test_wordmark_rejects_bad_payload(authed, project):
    path = f"/projects/{project['id']}/wordmark"
    for value in ["https://example.com/image.png", "data:image/png;base64,bad",
                  "data:image/svg+xml;base64,PHN2Zz4=", "x" * 350_001]:
        assert (await authed.patch(path, json={"wordmark": value})).status_code == 422
    assert (await authed.patch(path, json={})).status_code == 422
    assert (await authed.patch(f"/projects/{uuid.uuid4()}/wordmark",
                              json={"wordmark": None})).status_code == 404
