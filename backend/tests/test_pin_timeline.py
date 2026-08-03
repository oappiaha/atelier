"""Pin → timeline bridge (Beezy 2026-07): pinning a colorway also lands it on
the design's development timeline — a phase='study' entry carrying the pinned
image as archive media (source_app='wada', source_url = the study route).

Contract under test:
- pin creates exactly ONE entry + media, idempotent across pin/unpin/pin
  (content-hash dedupe via the media (workspace_id, sha256) unique index);
- the entry is visible through GET /designs/{id}/timeline and the media
  through GET /designs/{id}/media (phase denormalised by the DB trigger);
- the media bytes live at the canonical src/{ws}/{sha}.png key and SURVIVE an
  unpin (which deletes the cw/pinned/ copy) — the entry is history;
- the thumbs enqueue is best-effort: a dead broker never fails the pin.

Model boundary mocked exactly like test_generation (fal fixture); storage
effects asserted against the real isolated MinIO bucket.
"""

from tests.test_generation import (  # noqa: F401 — fixtures
    _base_png,
    _db,
    _run_enqueued,
    _study,
    fal,
    gen_setup,
)
from tests.test_pin_export import _ready_colorways
from tests.test_studies import (  # noqa: F401 — fixtures
    THREE_SLOTS,
    _create_study,
    _slot,
    sanzo,
)


def _exists(key: str) -> bool:
    from app.storage import object_exists

    return object_exists(key)


def _uniquify_working_object(cw_id: str) -> None:
    """Give the colorway's working PNG unique bytes before pinning.

    The suite's flat 400×300 frames make same-palette colorways byte-identical
    across studies, so the bridge's content-hash dedupe would collide with
    pins made by OTHER tests (test_pin_export pins the same c121 sheet). Real
    photos never collide; unique bytes restore that property here."""
    from app.config import get_settings
    from app.storage import get_s3

    with _db() as conn:
        (image_key,) = conn.execute(
            "SELECT image_key FROM colorways WHERE id = %s", (cw_id,)
        ).fetchone()
    get_s3().put_object(
        Bucket=get_settings().s3_bucket, Key=image_key, Body=_base_png(),
        ContentType="image/png",
    )


def _bridge_rows(study_id: str) -> tuple[list, list]:
    """(entries, media) the bridge created for this study."""
    with _db() as conn:
        entries = conn.execute(
            "SELECT id, phase, body FROM entries WHERE study_id = %s", (study_id,)
        ).fetchall()
        media = conn.execute(
            "SELECT m.id, m.r2_key, m.sha256, m.phase, m.source_app, m.source_url, m.design_id "
            "FROM media m JOIN entries e ON e.id = m.entry_id "
            "WHERE e.study_id = %s",
            (study_id,),
        ).fetchall()
    return entries, media


async def test_pin_creates_entry_and_media_exactly_once(authed, gen_setup, fal):  # noqa: F811
    study, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")
    _uniquify_working_object(cw["id"])
    design_id = gen_setup["design"]["id"]

    assert _bridge_rows(study["id"]) == ([], [])

    r = await authed.post(f"/colorways/{cw['id']}/pin")
    assert r.status_code == 200, r.text

    entries, media = _bridge_rows(study["id"])
    assert len(entries) == 1 and len(media) == 1
    _, phase, body = entries[0]
    assert phase == "study"
    # "Wada colorway #N — {palette} ({slot: colour, …})", N = contact-sheet idx
    assert body.startswith(f"Wada colorway #{cw['permutation_idx']} — ")
    for chip in cw["mapping"]:  # fingerprint carries every slot + colour name
        assert f"{chip['slot_label']}: {chip['name']}" in body

    m = media[0]
    _, r2_key, sha, m_phase, source_app, source_url, m_design = m
    ws = r2_key.split("/")[1]
    assert r2_key == f"src/{ws}/{sha}.png"  # canonical media key, not cw/pinned/
    assert _exists(r2_key)
    assert m_phase == "study" and str(m_design) == design_id  # trigger denormalised
    assert source_app == "wada"
    assert source_url == f"/d/{design_id}/study/{study['id']}"

    # idempotent second pin — no second entry/media
    assert (await authed.post(f"/colorways/{cw['id']}/pin")).status_code == 200
    entries2, media2 = _bridge_rows(study["id"])
    assert len(entries2) == 1 and len(media2) == 1

    # unpin leaves the entry alone; the media object survives the pinned-copy
    # deletion because it lives at src/, then a RE-pin creates nothing new
    assert (await authed.post(f"/colorways/{cw['id']}/unpin")).status_code == 200
    assert not _exists(f"cw/{ws}/pinned/{cw['id']}.png")
    assert _exists(r2_key)
    assert len(_bridge_rows(study["id"])[0]) == 1
    assert (await authed.post(f"/colorways/{cw['id']}/pin")).status_code == 200
    entries3, media3 = _bridge_rows(study["id"])
    assert len(entries3) == 1 and len(media3) == 1
    assert entries3[0][0] == entries[0][0]  # the SAME entry, not a lookalike


async def test_pin_entry_visible_on_timeline_and_media_views(authed, gen_setup, fal):  # noqa: F811
    study, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")
    _uniquify_working_object(cw["id"])
    design_id = gen_setup["design"]["id"]
    assert (await authed.post(f"/colorways/{cw['id']}/pin")).status_code == 200

    # timeline endpoint — the §9 read path the Design view renders
    r = await authed.get(f"/designs/{design_id}/timeline")
    assert r.status_code == 200, r.text
    entry = next(e for e in r.json() if e["phase"] == "study")
    assert entry["study_id"] == study["id"]
    assert entry["body"].startswith("Wada colorway #")
    assert len(entry["media"]) == 1
    img = entry["media"][0]
    assert img["kind"] == "image" and img["source_app"] == "wada"
    assert img["url"]  # presigned read of the src/ copy

    # phase filter (the Design view's chips) sees it too
    r = await authed.get(f"/designs/{design_id}/timeline", params={"phase": "study"})
    assert [e["id"] for e in r.json()] == [entry["id"]]

    # media view — the hot media.phase query, no join through entries
    r = await authed.get(f"/designs/{design_id}/media", params={"phase": "study"})
    assert r.status_code == 200
    (mv,) = r.json()
    assert mv["id"] == img["id"] and mv["source_app"] == "wada"


async def test_pin_thumbs_enqueue_is_best_effort(authed, gen_setup, fal, monkeypatch):  # noqa: F811
    """A dead broker must never fail the pin — the bridge entry still lands
    (same negative contract as POST /media/commit)."""
    study, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")
    _uniquify_working_object(cw["id"])

    from app.workers.thumbs import generate_thumbs

    def boom(media_id):
        raise ConnectionError("redis broker unreachable")

    monkeypatch.setattr(generate_thumbs, "delay", boom)
    r = await authed.post(f"/colorways/{cw['id']}/pin")
    assert r.status_code == 200, r.text
    entries, media = _bridge_rows(study["id"])
    assert len(entries) == 1 and len(media) == 1


async def test_pin_enqueues_thumbs_for_bridge_media(authed, gen_setup, fal, monkeypatch):  # noqa: F811
    study, cws = await _ready_colorways(authed, gen_setup, fal)
    cw = next(c for c in cws if c["status"] == "ready")
    _uniquify_working_object(cw["id"])

    from app.workers.thumbs import generate_thumbs

    calls: list[str] = []
    monkeypatch.setattr(generate_thumbs, "delay", lambda media_id: calls.append(media_id))
    assert (await authed.post(f"/colorways/{cw['id']}/pin")).status_code == 200
    _, media = _bridge_rows(study["id"])
    assert calls == [str(media[0][0])]
