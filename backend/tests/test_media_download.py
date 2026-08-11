"""GET /media/{id}/download (2026-08-11): presigned attachment URL with a
friendly filename, so wada colorway images — and any other archive item — can
be saved straight from a product's Media page. Real MinIO end to end."""

import uuid
from urllib.parse import parse_qs, urlparse

import httpx

from tests.util import make_png, sha256


async def test_download_requires_auth(client):
    r = await client.get(f"/media/{uuid.uuid4()}/download")
    assert r.status_code == 401


async def test_download_unknown_media_404(authed):
    r = await authed.get(f"/media/{uuid.uuid4()}/download")
    assert r.status_code == 404


async def test_download_happy_path(authed, design_factory, upload_media):
    """Same attach shape as CaptureSheet: upload, then a final-phase entry
    puts the photo on the design (the trigger denormalises media.phase)."""
    data = make_png(rgb=(90, 60, 200))
    design = await design_factory(name="Reis Bellypack")
    media = await upload_media(data=data)
    r = await authed.post(
        "/entries",
        json={"design_id": design["id"], "phase": "final", "media_ids": [media["id"]]},
    )
    assert r.status_code == 201, r.text

    r = await authed.get(f"/media/{media['id']}/download")
    assert r.status_code == 200, r.text
    url = r.json()["download_url"]

    # points at the same object as the media's own presigned GET (the r2 key
    # path — src/{ws}/{sha}.png — with the content sha in it)
    assert urlparse(url).path == urlparse(media["url"]).path
    assert sha256(data) in urlparse(url).path

    # forces a download under a sane slug: design + phase + id short-hash
    disposition = parse_qs(urlparse(url).query)["response-content-disposition"][0]
    short = uuid.UUID(media["id"]).hex[:6]
    assert disposition == f'attachment; filename="reis-bellypack-final-{short}.png"'

    # and the URL actually serves the original bytes with that header
    async with httpx.AsyncClient() as raw:
        got = await raw.get(url)
    assert got.status_code == 200, got.text
    assert got.headers["content-disposition"] == disposition
    assert got.content == data


async def test_download_inbox_media_falls_back_to_atelier_kind(authed, upload_media):
    """No design / no phase yet (Inbox): filename degrades gracefully."""
    media = await upload_media()
    r = await authed.get(f"/media/{media['id']}/download")
    assert r.status_code == 200, r.text
    disposition = parse_qs(urlparse(r.json()["download_url"]).query)[
        "response-content-disposition"
    ][0]
    short = uuid.UUID(media["id"]).hex[:6]
    assert disposition == f'attachment; filename="atelier-image-{short}.png"'
