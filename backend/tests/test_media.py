"""Media pipeline: presigned upload of REAL bytes to MinIO, commit (incl. the
T1-fixed 422/409 guards), sha256 dedupe, ?phase= filter, inbox + triage, cover."""

import os
import uuid

import httpx

from tests.util import WS, make_png, sha256


async def test_upload_commit_inbox_roundtrip(authed, upload_media):
    """Real bytes -> presigned PUT -> commit -> object in atelier-test bucket ->
    presigned GET returns the identical bytes."""
    data = make_png()
    m = await upload_media(data, caption="inbox roundtrip")
    assert m["design_id"] is None and m["entry_id"] is None  # untriaged -> Inbox
    assert m["kind"] == "image" and m["caption"] == "inbox roundtrip"

    # object really exists in the ISOLATED bucket (authoritative storage read)
    from app.storage import get_s3

    expected_key = f"src/{WS}/{sha256(data)}.png"
    head = get_s3().head_object(Bucket=os.environ["S3_BUCKET"], Key=expected_key)
    assert head["ContentLength"] == len(data)

    # presigned GET round-trips the exact bytes
    async with httpx.AsyncClient() as raw:
        rb = await raw.get(m["url"])
    assert rb.status_code == 200
    assert rb.content == data

    # and it shows up in the inbox
    r = await authed.get("/inbox")
    assert r.status_code == 200
    assert any(x["id"] == m["id"] for x in r.json())


async def test_upload_url_key_layout(authed):
    data = make_png()
    r = await authed.post(
        "/media/upload-url",
        json={"sha256": sha256(data), "content_type": "image/png", "kind": "image"},
    )
    assert r.status_code == 200
    u = r.json()
    assert u["r2_key"] == f"src/{WS}/{sha256(data)}.png"
    assert u["duplicate_of"] is None
    assert u["upload_url"].startswith("http")


async def test_upload_url_unsupported_content_type_422(authed):
    r = await authed.post(
        "/media/upload-url",
        json={"sha256": "a" * 64, "content_type": "application/pdf", "kind": "image"},
    )
    assert r.status_code == 422


async def test_upload_url_malformed_sha_422(authed):
    r = await authed.post(
        "/media/upload-url",
        json={"sha256": "zz", "content_type": "image/png", "kind": "image"},
    )
    assert r.status_code == 422


async def test_commit_key_sha_mismatch_422(authed):
    """T1 fix: the key must embed the claimed sha for this workspace."""
    data = make_png()
    r = await authed.post(
        "/media/commit",
        json={
            "sha256": sha256(data),
            "r2_key": f"src/{uuid.uuid4()}/{sha256(data)}.png",  # wrong workspace
            "kind": "image",
        },
    )
    assert r.status_code == 422


async def test_commit_never_uploaded_object_409(authed):
    """T1 fix: commit registers an object — bytes must actually be in storage."""
    data = make_png()
    r = await authed.post(
        "/media/commit",
        json={"sha256": sha256(data), "r2_key": f"src/{WS}/{sha256(data)}.png", "kind": "image"},
    )
    assert r.status_code == 409


async def test_sha256_dedupe(authed, upload_media):
    data = make_png()
    first = await upload_media(data, caption="original")

    # second upload-url for identical bytes: no URL, points at the existing row
    r = await authed.post(
        "/media/upload-url",
        json={"sha256": sha256(data), "content_type": "image/png", "kind": "image"},
    )
    assert r.status_code == 200
    u = r.json()
    assert u["upload_url"] is None
    assert u["duplicate_of"] == first["id"]
    assert u["r2_key"] == f"src/{WS}/{sha256(data)}.png"

    # committing the same sha again returns the existing row, not a new one
    dup = await upload_media(data, caption="dup attempt")
    assert dup["id"] == first["id"]

    # no duplicate row materialised anywhere (inbox holds it exactly once)
    r = await authed.get("/inbox")
    assert sum(1 for x in r.json() if x["id"] == first["id"]) == 1


async def test_design_media_phase_filter(authed, design_factory, upload_media):
    d = await design_factory()
    m_mood = await upload_media()
    m_sketch = await upload_media()
    for phase, media in (("moodboard", m_mood), ("sketch", m_sketch)):
        r = await authed.post(
            "/entries",
            json={"design_id": d["id"], "phase": phase, "media_ids": [media["id"]]},
        )
        assert r.status_code == 201

    r = await authed.get(f"/designs/{d['id']}/media")
    assert r.status_code == 200
    assert {m["id"] for m in r.json()} == {m_mood["id"], m_sketch["id"]}

    r = await authed.get(f"/designs/{d['id']}/media", params={"phase": "moodboard"})
    assert [m["id"] for m in r.json()] == [m_mood["id"]]
    assert r.json()[0]["phase"] == "moodboard"

    r = await authed.get(f"/designs/{d['id']}/media", params={"phase": "not-a-phase"})
    assert r.status_code == 422


async def test_inbox_triage_flow(authed, design_factory, upload_media):
    d = await design_factory()
    m = await upload_media(caption="supplier find")

    r = await authed.post(
        f"/inbox/{m['id']}/triage", json={"design_id": d["id"], "phase": "inspiration"}
    )
    assert r.status_code == 200
    t = r.json()
    assert t["design_id"] == d["id"]
    assert t["phase"] == "inspiration"  # denormalised by trigger
    assert t["entry_id"] is not None

    # left the inbox
    r = await authed.get("/inbox")
    assert all(x["id"] != m["id"] for x in r.json())

    # triage created a timeline entry carrying the media + caption as body
    r = await authed.get(f"/designs/{d['id']}/timeline")
    entry = next(e for e in r.json() if e["id"] == t["entry_id"])
    assert entry["phase"] == "inspiration"
    assert entry["body"] == "supplier find"
    assert [x["id"] for x in entry["media"]] == [m["id"]]

    # already triaged -> no longer in inbox -> 404 on re-triage
    r = await authed.post(
        f"/inbox/{m['id']}/triage", json={"design_id": d["id"], "phase": "sketch"}
    )
    assert r.status_code == 404


async def test_triage_bogus_phase_422(authed, design_factory, upload_media):
    d = await design_factory()
    m = await upload_media()
    r = await authed.post(
        f"/inbox/{m['id']}/triage", json={"design_id": d["id"], "phase": "bogus"}
    )
    assert r.status_code == 422


async def test_triage_unknown_media_404(authed, design_factory):
    d = await design_factory()
    r = await authed.post(
        f"/inbox/{uuid.uuid4()}/triage", json={"design_id": d["id"], "phase": "sketch"}
    )
    assert r.status_code == 404


async def test_triage_unknown_design_404(authed, upload_media):
    """FK violation (design_id) must map to a clean 404, not a 500 (T5)."""
    m = await upload_media()
    r = await authed.post(
        f"/inbox/{m['id']}/triage", json={"design_id": str(uuid.uuid4()), "phase": "sketch"}
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "design not found"}
    # preservation: the failed triage left the media untriaged, still in the inbox
    r = await authed.get("/inbox")
    assert any(x["id"] == m["id"] for x in r.json())


async def test_design_cover_via_media(authed, design_factory, upload_media):
    d = await design_factory()
    m = await upload_media()
    r = await authed.post(
        "/entries", json={"design_id": d["id"], "phase": "final", "media_ids": [m["id"]]}
    )
    assert r.status_code == 201

    r = await authed.patch(f"/designs/{d['id']}", json={"cover_media_id": m["id"]})
    assert r.status_code == 200
    assert r.json()["cover_media_id"] == m["id"]

    r = await authed.get(f"/designs/{d['id']}")
    body = r.json()
    assert body["cover_media_id"] == m["id"]
    assert body["cover_url"]  # presigned
    assert body["media_count"] == 1
