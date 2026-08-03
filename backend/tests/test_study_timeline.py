"""Study closure → design timeline (2026-08-03, Beezy: "why aren't these part
of the timeline of explorations?").

When a run closes complete/partial, close_study writes ONE 'Wada study' entry
(phase='study') on the design with up to STUDY_ENTRY_MEDIA_MAX ready colorways
copied to src/ keys as real archive media. Idempotent per study; best-effort
(a bridge failure never un-closes the run). Model boundary mocked via the fal
fixture, exactly like test_generation.
"""

from tests.test_generation import (  # noqa: F401 — fixtures
    _db,
    _run_enqueued,
    _study,
    fal,
    gen_setup,
)
from tests.test_pin_export import _ready_colorways
from tests.test_studies import sanzo  # noqa: F401 — fixture


def _study_rows(study_id: str) -> tuple[list, list]:
    with _db() as conn:
        entries = conn.execute(
            "SELECT id, phase, body FROM entries "
            "WHERE study_id = %s AND body LIKE 'Wada study%%'",
            (study_id,),
        ).fetchall()
        media = conn.execute(
            "SELECT m.id, m.r2_key, m.sha256, m.source_app, m.source_url "
            "FROM media m JOIN entries e ON e.id = m.entry_id "
            "WHERE e.study_id = %s AND e.body LIKE 'Wada study%%'",
            (study_id,),
        ).fetchall()
    return entries, media


async def test_run_close_creates_study_entry_with_media(authed, gen_setup, fal):  # noqa: F811
    # a palette no other test paints with (c200): the deterministic fake model
    # derives bytes from the prompt's hexes, so this study's colorways cannot
    # sha-collide with media earlier suite tests already attached to entries
    study = await _study(authed, gen_setup, palette_id="c200")
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    _run_enqueued(fal)
    cws = (await authed.get(f"/studies/{study['id']}/colorways")).json()["colorways"]
    ready = [c for c in cws if c["status"] == "ready"]
    assert len(ready) == 2  # eager default

    entries, media = _study_rows(study["id"])
    assert len(entries) == 1
    _, phase, body = entries[0]
    assert phase == "study"
    assert body.startswith("Wada study — ")
    assert "2 colorways ready" in body
    # both ready colorways attached at canonical src/ keys
    assert len(media) == 2
    for _, r2_key, sha, source_app, source_url in media:
        ws = r2_key.split("/")[1]
        assert r2_key == f"src/{ws}/{sha}.png"
        assert source_app == "wada"
        assert source_url == f"/d/{gen_setup['design']['id']}/study/{study['id']}"

    # visible through the §9 timeline read path
    r = await authed.get(f"/designs/{gen_setup['design']['id']}/timeline")
    assert r.status_code == 200
    entry = next(e for e in r.json() if e["body"] and e["body"].startswith("Wada study"))
    assert entry["study_id"] == study["id"]
    assert len(entry["media"]) == len(media)


async def test_study_entry_is_idempotent(authed, gen_setup, fal):  # noqa: F811
    from app.workers.generate import study_timeline_entry

    study, _ = await _ready_colorways(authed, gen_setup, fal)
    entries, media = _study_rows(study["id"])
    assert len(entries) == 1

    # a second closure (double decrement, watchdog re-close) changes nothing
    with _db() as conn:
        assert study_timeline_entry(conn, study["id"]) is False
    entries2, media2 = _study_rows(study["id"])
    assert [e[0] for e in entries2] == [e[0] for e in entries]
    assert len(media2) == len(media)


async def test_draft_or_empty_study_gets_no_entry(authed, gen_setup, fal):  # noqa: F811
    from app.workers.generate import study_timeline_entry

    study = await _study(authed, gen_setup)  # draft — nothing generated
    with _db() as conn:
        assert study_timeline_entry(conn, study["id"]) is False
    assert _study_rows(study["id"]) == ([], [])
