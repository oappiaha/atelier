"""Studio Shot (2026-08-04): POST /media/{id}/studio-shot — PhotoRoom edit
(bg removed + ai.soft shadow + white backdrop) registered as archive media on
an editorial entry. The PhotoRoom HTTP boundary is mocked; everything else is
the real stack.
"""

import io
import uuid

import pytest
from PIL import Image


def _fake_studio_png(color=(250, 250, 250)) -> bytes:
    img = Image.new("RGB", (64, 48), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def photoroom_studio(monkeypatch):
    """Deterministic fake at the PhotoRoom edit boundary + call counter.
    conftest blanks PHOTOROOM_API_KEY for the suite (real-money guard), so the
    route's 503 gate needs a fake key too — same pattern as test_cutout."""
    from app.config import get_settings

    calls: list[int] = []
    out = {"png": _fake_studio_png()}

    def fake(image_bytes: bytes) -> bytes:
        calls.append(len(image_bytes))
        return out["png"]

    import app.workers.cutout as cut

    monkeypatch.setattr(cut, "call_photoroom_studio", fake)
    monkeypatch.setattr(get_settings(), "photoroom_api_key", "test-key")
    return {"calls": calls, "out": out}


async def _photo_on_design(authed, design_factory, upload_media):
    """Real pipeline: upload bytes, then a final-phase entry attaches them to
    the design (the same shape CaptureSheet produces)."""
    design = await design_factory()
    media = await upload_media()
    r = await authed.post(
        "/entries",
        json={"design_id": design["id"], "phase": "final", "media_ids": [media["id"]]},
    )
    assert r.status_code == 201, r.text
    return design, media


async def test_studio_shot_requires_auth(client):
    r = await client.post(f"/media/{uuid.uuid4()}/studio-shot")
    assert r.status_code == 401


async def test_studio_shot_creates_editorial_entry_and_media(
    authed, design_factory, upload_media, photoroom_studio,
):
    design, media = await _photo_on_design(authed, design_factory, upload_media)

    r = await authed.post(f"/media/{media['id']}/studio-shot")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["created"] is True
    m = out["media"]
    assert m["kind"] == "image" and m["source_app"] == "photoroom"
    assert m["source_url"] == f"media:{media['id']}"
    assert photoroom_studio["calls"], "PhotoRoom boundary was not called"

    # on the timeline as an editorial entry with the derivative attached
    tl = (await authed.get(f"/designs/{design['id']}/timeline")).json()
    entry = next(e for e in tl if str(e["id"]) == str(out["entry_id"]))
    assert entry["phase"] == "editorial"
    assert entry["body"].startswith("Studio shot")
    assert any(x["id"] == m["id"] for x in entry["media"])

    # repeat: PhotoRoom's shadow render is NOT byte-deterministic, so make the
    # fake return DIFFERENT bytes — the provenance gate must still dedupe
    # WITHOUT a second model call (found live: sha dedupe alone re-billed)
    photoroom_studio["out"]["png"] = _fake_studio_png(color=(200, 200, 200))
    n_calls = len(photoroom_studio["calls"])
    r2 = await authed.post(f"/media/{media['id']}/studio-shot")
    assert r2.status_code == 201
    assert r2.json()["created"] is False
    assert r2.json()["media"]["id"] == m["id"]
    assert len(photoroom_studio["calls"]) == n_calls  # $0 — model never called
    tl2 = (await authed.get(f"/designs/{design['id']}/timeline")).json()
    assert sum(1 for e in tl2 if (e["body"] or "").startswith("Studio shot")) == 1


async def test_studio_shot_rejects_unknown_and_untriaged(authed, upload_media, photoroom_studio):
    r = await authed.post(f"/media/{uuid.uuid4()}/studio-shot")
    assert r.status_code == 404

    # an inbox photo (no entry → no design) can't carry an editorial entry yet
    inbox_photo = await upload_media()
    r = await authed.post(f"/media/{inbox_photo['id']}/studio-shot")
    assert r.status_code == 422
    assert "triage" in r.json()["detail"]


async def test_studio_shot_photoroom_failure_is_502(
    authed, design_factory, upload_media, monkeypatch,
):
    import httpx

    import app.workers.cutout as cut
    from app.config import get_settings

    def boom(image_bytes: bytes) -> bytes:
        raise httpx.ConnectError("photoroom unreachable")

    monkeypatch.setattr(cut, "call_photoroom_studio", boom)
    monkeypatch.setattr(get_settings(), "photoroom_api_key", "test-key")
    design, media = await _photo_on_design(authed, design_factory, upload_media)
    r = await authed.post(f"/media/{media['id']}/studio-shot")
    assert r.status_code == 502
    # nothing landed on the timeline
    tl = (await authed.get(f"/designs/{design['id']}/timeline")).json()
    assert not any((e["body"] or "").startswith("Studio shot") for e in tl)
