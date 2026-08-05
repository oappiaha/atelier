"""DELETE /designs/{id} (2026-08-05): a product and its whole subtree —
entries, media, studies — go together (§2 ON DELETE CASCADE)."""

import os
import uuid

from psycopg import connect


def _counts(design_id: str) -> dict:
    with connect(os.environ["DATABASE_URL"].replace("+asyncpg", "")) as conn:
        return {
            "designs": conn.execute(
                "SELECT count(*) FROM designs WHERE id = %s", (design_id,)
            ).fetchone()[0],
            "entries": conn.execute(
                "SELECT count(*) FROM entries WHERE design_id = %s", (design_id,)
            ).fetchone()[0],
            "media": conn.execute(
                "SELECT count(*) FROM media WHERE design_id = %s", (design_id,)
            ).fetchone()[0],
            "studies": conn.execute(
                "SELECT count(*) FROM studies WHERE design_id = %s", (design_id,)
            ).fetchone()[0],
        }


async def test_delete_requires_auth(client):
    assert (await client.delete(f"/designs/{uuid.uuid4()}")).status_code == 401


async def test_delete_unknown_is_404(authed):
    assert (await authed.delete(f"/designs/{uuid.uuid4()}")).status_code == 404


async def test_delete_cascades_the_whole_subtree(authed, design_factory, upload_media):
    design = await design_factory()
    media = await upload_media()
    r = await authed.post(
        "/entries",
        json={"design_id": design["id"], "phase": "final", "media_ids": [media["id"]]},
    )
    assert r.status_code == 201

    before = _counts(design["id"])
    assert before == {"designs": 1, "entries": 1, "media": 1, "studies": 0}

    assert (await authed.delete(f"/designs/{design['id']}")).status_code == 204
    assert _counts(design["id"]) == {"designs": 0, "entries": 0, "media": 0, "studies": 0}
    assert (await authed.get(f"/designs/{design['id']}")).status_code == 404

    # idempotence-of-absence: a second delete is a clean 404, not a 500
    assert (await authed.delete(f"/designs/{design['id']}")).status_code == 404
