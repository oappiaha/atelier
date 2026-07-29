"""SHIP-1 stuck-run watchdog: the on-read sweep in GET /studies/{id}/colorways.

A worker crash (OOM, deploy restart, broker loss) used to leave a study in
'generating' forever: the contact sheet spun, and POST /generate 409'd. The
sweep marks a study 'generating' >30min with no live task heartbeat as
'partial' (results KEPT) and colorways mid-paint >15min as 'failed'.

Clock injection, not sleeps: staleness comes from backdating the new
generation_started_at columns (migration 0004); "no active task" comes from
the redis heartbeat key (gen:active:{study_id}) the executor refreshes per
chain node — tests set/delete that key directly.
"""

import os

import redis as redis_sync

from app.wada.generation import heartbeat_key
from tests.test_generation import (  # noqa: F401 — fixtures
    _db,
    _run_enqueued,
    _study,
    fal,
    gen_setup,
)
from tests.test_studies import (  # noqa: F401 — fixtures
    THREE_SLOTS,
    _create_study,
    _slot,
    sanzo,
)


def _backdate(table: str, row_id: str, minutes: int) -> None:
    assert table in ("studies", "colorways")
    with _db() as conn:
        conn.execute(
            f"UPDATE {table} SET generation_started_at = "  # noqa: S608
            "now() - make_interval(mins => %s) WHERE id = %s",
            (minutes, row_id),
        )
        conn.commit()


def _set_status(table: str, row_id: str, status: str) -> None:
    assert table in ("studies", "colorways")
    with _db() as conn:
        conn.execute(
            f"UPDATE {table} SET status = %s WHERE id = %s",  # noqa: S608
            (status, row_id),
        )
        conn.commit()


def _redis():
    return redis_sync.from_url(os.environ["REDIS_URL"])


async def _stuck_study(authed, setup, fal):  # noqa: F811
    """Eager-2 generated for real (mocked model) -> 2 ready; then a 'generate
    the rest' run whose worker never executes (the crash)."""
    study = await _study(authed, setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    _run_enqueued(fal)
    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202, r.text
    fal["enqueued"].clear()  # the worker dies before doing anything
    return study


async def test_watchdog_marks_stuck_study_partial_keeping_results(
    authed, gen_setup, fal,  # noqa: F811
):
    study = await _stuck_study(authed, gen_setup, fal)
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    assert sheet["study_status"] == "generating"  # fresh: NOT swept
    assert sheet["ready"] == 2

    # one colorway was mid-paint when the worker died (young — 5min)
    victim = next(c for c in sheet["colorways"] if c["status"] == "planned")
    _set_status("colorways", victim["id"], "generating")
    _backdate("colorways", victim["id"], 5)

    # clock injection: the run started 31 minutes ago, no heartbeat exists
    _backdate("studies", study["id"], 31)
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    assert sheet["study_status"] == "partial"
    by_id = {c["id"]: c for c in sheet["colorways"]}
    assert by_id[victim["id"]]["status"] == "failed"
    assert "watchdog" in by_id[victim["id"]]["error"]
    assert sheet["ready"] == 2  # results KEPT
    assert sum(1 for c in sheet["colorways"] if c["status"] == "planned") == 3

    # authoritative read-back + the 409 gate is released: generate works again
    with _db() as conn:
        st, err = conn.execute(
            "SELECT status, error FROM studies WHERE id = %s", (study["id"],)
        ).fetchone()
    assert st == "partial" and "watchdog" in err
    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202, r.text
    fal["enqueued"].clear()


async def test_watchdog_fails_stuck_colorway_before_study_threshold(
    authed, gen_setup, fal,  # noqa: F811
):
    """>15min mid-paint colorway dies even while the study itself is under
    the 30min line — the study keeps 'generating' (rule 2 not yet due)."""
    study = await _stuck_study(authed, gen_setup, fal)
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    victim = next(c for c in sheet["colorways"] if c["status"] == "planned")
    _set_status("colorways", victim["id"], "generating")
    _backdate("colorways", victim["id"], 16)
    _backdate("studies", study["id"], 20)

    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    by_id = {c["id"]: c for c in sheet["colorways"]}
    assert by_id[victim["id"]]["status"] == "failed"
    assert sheet["study_status"] == "generating"
    # cleanup: let the study settle so later fixtures are unaffected
    _set_status("studies", study["id"], "partial")


async def test_watchdog_heartbeat_vetoes_the_sweep(authed, gen_setup, fal):  # noqa: F811
    """A live heartbeat = a real long run: nothing is touched, however old
    the timestamps look. Preservation case: the same read AFTER the heartbeat
    expires does sweep."""
    study = await _stuck_study(authed, gen_setup, fal)
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    victim = next(c for c in sheet["colorways"] if c["status"] == "planned")
    _set_status("colorways", victim["id"], "generating")
    # heartbeat FIRST, then backdate — the veto must hold against timestamps
    # that would otherwise sweep instantly
    r = _redis()
    try:
        r.set(heartbeat_key(study["id"]), "1", ex=60)
        _backdate("studies", study["id"], 45)
        _backdate("colorways", victim["id"], 45)
        sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
        assert sheet["study_status"] == "generating"  # untouched
        by_id = {c["id"]: c for c in sheet["colorways"]}
        assert by_id[victim["id"]]["status"] == "generating"
    finally:
        r.delete(heartbeat_key(study["id"]))
        r.close()

    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    assert sheet["study_status"] == "partial"
    assert {c["id"]: c for c in sheet["colorways"]}[victim["id"]]["status"] == "failed"


async def test_watchdog_leaves_fresh_runs_alone(authed, gen_setup, fal):  # noqa: F811
    """A run that started moments ago is NOT stuck, heartbeat or not — the
    thresholds alone protect against sweeping a task that is merely slow to
    start."""
    study = await _stuck_study(authed, gen_setup, fal)
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    assert sheet["study_status"] == "generating"
    assert all(c["status"] != "failed" for c in sheet["colorways"])
    _set_status("studies", study["id"], "partial")  # settle for teardown
