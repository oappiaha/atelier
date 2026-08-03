"""PARALLEL-1: colorways generate CONCURRENTLY (one Celery child per colorway).

What has to survive the fan-out, and how it is proved here without a broker:

- No double spend on shared trie prefixes. Two colorways whose chains start
  with the same (colour, mask) node must produce exactly ONE model call for it.
  Proved with real OS threads: the fake model blocks inside the first call until
  the sibling has had time to reach the node, so the loser is *forced* through
  the claim-wait path rather than winning by luck.
- The run closes when the LAST child finishes — not the first — and the study's
  terminal status is recomputed from the colorway rows (studies.gen_pending,
  migration 0005, is only the latch that says "you are last").
- A claim whose owner was killed (no finally, so the claim row survives) must
  not block the trie forever: it goes stale and the next task takes it over.
- "Generate this one" is a fan-out of one, unchanged.
- The §8.4/§8.8 anchor holds under concurrency: the ledger still equals
  round_half_up(6.75¢ × calls) = the estimate the route returned, even though
  N children ledger against the same study at once.

The model is mocked at the same boundary as tests/test_generation.py; the DB,
MinIO and Redis are the real isolated test resources (conftest).
"""

import asyncio
import threading
import time

import pytest

from app.wada import generation as G
from tests.test_generation import (  # noqa: F401 — fixtures
    _db,
    _run_enqueued,
    _study,
    _working_sha,
    fal,
    gen_setup,
)
from tests.test_studies import (  # noqa: F401 — fixtures
    THREE_SLOTS,
    _create_study,
    _slot,
    sanzo,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _darkness(color_ids: list[int]) -> dict[int, tuple[float, int]]:
    with _db() as conn:
        return {
            r[0]: (r[1], r[0])
            for r in conn.execute(
                "SELECT id, lab_l FROM sanzo_colors WHERE id = ANY(%s)", (color_ids,)
            ).fetchall()
        }


def _prefix_sharing_pair(sheet: dict) -> tuple[str, str]:
    """Two colorways of the sheet whose chains share their FIRST node (§8.8:
    with K=3/C=3 every colorway starts on the darkest colour, so the pair is
    the two permutations that put it on the same slot)."""
    colors = sorted({c["color_id"] for cw in sheet["colorways"] for c in cw["mapping"]})
    darkness = _darkness(colors)
    first: dict[tuple, list[str]] = {}
    for cw in sheet["colorways"]:
        mapping = {c["slot_idx"]: c["color_id"] for c in cw["mapping"]}
        step = G.chain_steps(mapping, darkness)[0]
        first.setdefault(step, []).append(cw["id"])
    pair = next(ids for ids in first.values() if len(ids) >= 2)
    return pair[0], pair[1]


def _run_children(fal, colorway_ids: list[str]) -> None:  # noqa: F811
    """Run only the captured children for these colorways (the rest of the
    fan-out stays queued — the study simply doesn't close)."""
    from app.workers.generate import generate_colorway

    for args in list(fal["enqueued"]):
        if args[1] in colorway_ids:
            generate_colorway(*args)


def _study_row(study_id: str):
    with _db() as conn:
        return conn.execute(
            "SELECT status, error, actual_cost_cents, gen_pending FROM studies "
            "WHERE id = %s",
            (study_id,),
        ).fetchone()


def _colorway_rows(study_id: str):
    with _db() as conn:
        return conn.execute(
            "SELECT id, status, cache_hits, cost_cents FROM colorways WHERE study_id = %s",
            (study_id,),
        ).fetchall()


def _live_claims() -> int:
    with _db() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM gen_nodes WHERE image_key = ''"
        ).fetchone()
        return n


# ── (a) the shared prefix node is paid for ONCE ─────────────────────────────

async def test_shared_prefix_node_is_paid_for_once_under_concurrency(
    authed, gen_setup, fal, monkeypatch,  # noqa: F811
):
    """Two colorways sharing their first node, running in real threads: one
    model call for that node, the loser records an ordinary §8.8 cache hit."""
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202, r.text
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    cw_a, cw_b = _prefix_sharing_pair(sheet)

    from app.workers import generate as W

    model = W.call_seedream
    gate = threading.Lock()
    entered, release = threading.Event(), threading.Event()
    state = {"gated": False}

    def blocking_model(*args, **kwargs):
        with gate:
            first = not state["gated"]
            state["gated"] = True
        if first:  # hold the shared node open while the sibling arrives
            entered.set()
            release.wait(timeout=30)
        return model(*args, **kwargs)

    monkeypatch.setattr(W, "call_seedream", blocking_model)

    def child(cw_id: str) -> None:
        W.generate_colorway(study["id"], cw_id)

    ta = threading.Thread(target=child, args=(cw_a,))
    ta.start()
    assert entered.wait(timeout=30), "the first child never reached the model"
    tb = threading.Thread(target=child, args=(cw_b,))
    tb.start()
    await asyncio.sleep(0.3)  # B is now parked on the claim (CLAIM_POLL_S = 20ms)
    release.set()
    ta.join(timeout=60)
    tb.join(timeout=60)
    assert not ta.is_alive() and not tb.is_alive()

    # 3 steps each, one node shared: 5 model calls, never 6
    assert len(fal["calls"]) == 5
    with _db() as conn:
        rows = conn.execute(
            "SELECT cost_cents, cache_hit FROM spend_ledger WHERE study_id = %s",
            (study["id"],),
        ).fetchall()
        nodes = conn.execute(
            "SELECT COUNT(*) FROM gen_nodes WHERE base_sha256 = %s",
            (_working_sha(gen_setup),),
        ).fetchone()[0]
    spent = [x for x in rows if not x[1]]
    hits = [x for x in rows if x[1]]
    assert len(spent) == 5 and nodes == 5
    assert len(hits) == 1 and hits[0][0] == 0  # the loser: one $0 hit row
    # cumulative allocation still reconciles despite concurrent ledgering
    expected = sum(G.alloc_cents(i) for i in range(5))
    assert sum(x[0] for x in spent) == expected
    assert _study_row(study["id"])[2] == expected

    by_id = {str(r[0]): r for r in _colorway_rows(study["id"])}
    assert by_id[cw_a][1] == "ready" and by_id[cw_b][1] == "ready"
    assert sorted([by_id[cw_a][2], by_id[cw_b][2]]) == [0, 1]  # exactly one hit
    with _db() as conn:
        (hit_count,) = conn.execute(
            "SELECT MAX(hit_count) FROM gen_nodes WHERE base_sha256 = %s",
            (_working_sha(gen_setup),),
        ).fetchone()
    assert hit_count == 1
    assert _live_claims() == 0


# ── (b) the LAST child closes the run ───────────────────────────────────────

async def test_run_closes_only_when_the_last_child_finishes(
    authed, gen_setup, fal,  # noqa: F811
):
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    requested = [str(i) for i in r.json()["requested"]]
    assert len(fal["enqueued"]) == 2
    assert _study_row(study["id"])[3] == 2  # gen_pending = children owed

    _run_children(fal, [requested[0]])
    status, _err, _cost, pending = _study_row(study["id"])
    assert status == "generating"  # one child done, the sheet keeps spinning
    assert pending == 1
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    assert sheet["ready"] == 1 and sheet["study_status"] == "generating"

    _run_children(fal, [requested[1]])
    status, _err, _cost, pending = _study_row(study["id"])
    assert (status, pending) == ("partial", 0)  # 2 of 6 ready = partial
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    assert sheet["ready"] == 2 and sheet["study_status"] == "partial"


async def test_one_failing_child_leaves_the_run_partial(
    authed, gen_setup, fal, monkeypatch,  # noqa: F811
):
    """A child that fails its chain fails only its own colorway; the last child
    still closes the run, and 'partial' means results are kept."""
    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})

    from app.workers import generate as W

    model = W.call_seedream

    def dies_after_the_first_chain(*args, **kwargs):
        if len(fal["calls"]) >= 3:  # the first colorway is done: kill the next
            raise W.NodeFailed("simulated fal outage")
        return model(*args, **kwargs)

    monkeypatch.setattr(W, "call_seedream", dies_after_the_first_chain)
    _run_enqueued(fal)

    status, error, _cost, pending = _study_row(study["id"])
    assert (status, pending) == ("partial", 0)
    assert error is None
    statuses = sorted(r[1] for r in _colorway_rows(study["id"]))
    assert statuses.count("ready") == 1 and statuses.count("failed") == 1
    assert _live_claims() == 0  # the failed node released its claim


async def test_all_children_failing_closes_the_run_failed(
    authed, gen_setup, fal, monkeypatch,  # noqa: F811
):
    from app.workers import generate as W

    monkeypatch.setattr(
        W, "call_seedream",
        lambda *a, **k: (_ for _ in ()).throw(W.NodeFailed("fal is down")),
    )
    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)

    status, error, _cost, pending = _study_row(study["id"])
    assert (status, pending) == ("failed", 0)
    assert error == "all requested colorways failed"
    assert _live_claims() == 0


# ── (c) a dead claim owner must not block the trie ──────────────────────────

async def test_stale_claim_is_taken_over_when_the_winner_dies(
    authed, gen_setup, fal, monkeypatch,  # noqa: F811
):
    """SIGKILL/OOM leaves a claim row nobody will ever fill (no finally runs).
    The next task waits, declares it stale and takes it over — it never hangs."""
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    victim = str(r.json()["requested"][0])

    from app.workers import generate as W

    # NB: restore by hand, never monkeypatch.undo() — undo() would also revert
    # the `fal` fixture's mock and put the REAL fal queue (and real money) back
    # under the second half of this test.
    fake_model, real_release = W.call_seedream, W.release_claim
    monkeypatch.setattr(W, "release_claim", lambda *a, **k: None)  # = killed
    monkeypatch.setattr(
        W, "call_seedream",
        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("SIGKILL")),
    )
    with pytest.raises(KeyboardInterrupt):
        W.generate_colorway(study["id"], victim)
    assert _live_claims() == 1  # the orphaned claim survived the "crash"

    monkeypatch.setattr(W, "release_claim", real_release)
    monkeypatch.setattr(W, "call_seedream", fake_model)
    monkeypatch.setattr(W, "CLAIM_POLL_S", 0.02)
    monkeypatch.setattr(W, "CLAIM_STALE_S", 0.5)
    t0 = time.monotonic()
    assert W.generate_colorway(study["id"], victim) == "done:1"
    assert time.monotonic() - t0 < 30  # not CLAIM_STALE_S's production window

    assert len(fal["calls"]) == 3  # the taker regenerated the orphaned node
    assert _live_claims() == 0
    by_id = {str(x[0]): x for x in _colorway_rows(study["id"])}
    assert by_id[victim][1] == "ready"


# ── (d) "generate this one" is a fan-out of one ─────────────────────────────

async def test_generate_one_is_a_fanout_of_one(authed, gen_setup, fal):  # noqa: F811
    study = await _study(authed, gen_setup)
    await authed.post(f"/studies/{study['id']}/generate", json={})
    _run_enqueued(fal)

    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    ghost = next(c for c in sheet["colorways"] if c["status"] == "planned")
    r = await authed.post(
        f"/studies/{study['id']}/generate-one", json={"colorway_id": ghost["id"]}
    )
    assert r.status_code == 202, r.text
    assert len(fal["enqueued"]) == 1
    assert fal["enqueued"][0] == (study["id"], ghost["id"])
    assert _study_row(study["id"])[3] == 1  # one child owed

    _run_enqueued(fal)
    status, _err, _cost, pending = _study_row(study["id"])
    assert (status, pending) == ("partial", 0)  # 3 of 6 ready
    by_id = {str(x[0]): x for x in _colorway_rows(study["id"])}
    assert by_id[ghost["id"]][1] == "ready"


# ── the broker failing mid-fan-out ──────────────────────────────────────────

async def test_broker_down_refuses_and_partial_fanout_still_closes(
    authed, gen_setup, fal, monkeypatch,  # noqa: F811
):
    """No child queued → 503 (unchanged). Some children queued → the request
    stands and gen_pending is trimmed to what was dispatched, so the run can
    still close instead of spinning until the watchdog."""
    from app.workers import generate as W

    study = await _study(authed, gen_setup)
    monkeypatch.setattr(
        W.generate_colorway, "delay",
        lambda *a, **k: (_ for _ in ()).throw(OSError("broker is down")),
    )
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 503
    assert "cannot enqueue" in r.json()["detail"]

    sent: list[tuple] = []

    def flaky(*args, **kwargs):
        if sent:
            raise OSError("broker died mid-fan-out")
        sent.append(args)

    monkeypatch.setattr(W.generate_colorway, "delay", flaky)
    with _db() as conn:  # the 503 left the study 'generating' (watchdog's job)
        conn.execute(
            "UPDATE studies SET status = 'draft' WHERE id = %s", (study["id"],)
        )
        conn.commit()
    r = await authed.post(f"/studies/{study['id']}/generate", json={})
    assert r.status_code == 202, r.text
    assert len(sent) == 1
    assert _study_row(study["id"])[3] == 1  # 2 requested, 1 dispatched

    W.generate_colorway(*sent[0])
    status, _err, _cost, pending = _study_row(study["id"])
    assert (status, pending) == ("partial", 0)


# ── (e) the §8.4 anchor survives the fan-out ────────────────────────────────

async def test_full_sheet_still_costs_the_estimate_when_run_concurrently(
    authed, gen_setup, fal,  # noqa: F811
):
    """The pinned numbers (6 colorways → 15 trie nodes → 101¢ = the estimate)
    with all six children racing in a thread pool: the claim protocol removes
    every duplicate call, and the locked ledger keeps the cents reconciling."""
    study = await _study(authed, gen_setup)
    r = await authed.post(f"/studies/{study['id']}/generate", json={"all": True})
    assert r.status_code == 202, r.text
    estimated = r.json()["estimated_cents"]
    assert estimated == 101

    from app.workers.generate import generate_colorway

    threads = [
        threading.Thread(target=generate_colorway, args=args)
        for args in fal["enqueued"]
    ]
    fal["enqueued"].clear()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert all(not t.is_alive() for t in threads)

    assert len(fal["calls"]) == 15  # never 18, never 16
    status, _err, actual, pending = _study_row(study["id"])
    assert (status, pending) == ("complete", 0)
    assert actual == estimated == 101
    with _db() as conn:
        spent = conn.execute(
            "SELECT COALESCE(SUM(cost_cents), 0), COUNT(*) FROM spend_ledger "
            "WHERE study_id = %s AND cache_hit = false",
            (study["id"],),
        ).fetchone()
    assert spent == (101, 15)
    assert _live_claims() == 0
    sheet = (await authed.get(f"/studies/{study['id']}/colorways")).json()
    assert sheet["ready"] == 6
    assert all(c["lock_verified"] for c in sheet["colorways"])
