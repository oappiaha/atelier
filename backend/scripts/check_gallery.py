"""Run against a disposable migrated gallery_perf_test database; never production."""

import asyncio
import base64
import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from fastapi import FastAPI
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.routers.gallery import router

assert get_settings().database_url.endswith("/gallery_perf_test")


async def main():
    app = FastAPI()
    app.include_router(router)
    ws, other, user, pid, did = [uuid.uuid4() for _ in range(5)]
    async with SessionLocal() as db:
        await db.execute(
            text("INSERT INTO workspaces(id,name) VALUES (:a,'Perf'),(:b,'Other')"),
            {"a": ws, "b": other},
        )
        await db.execute(
            text("INSERT INTO projects(id,workspace_id,name) VALUES (:p,:w,'Perf')"),
            {"p": pid, "w": ws},
        )
        await db.execute(
            text(
                "INSERT INTO designs(id,workspace_id,project_id,name,index_no) VALUES (:d,:w,:p,'Design',1)"
            ),
            {"d": did, "w": ws, "p": pid},
        )
        for i in range(65):
            await db.execute(
                text("""INSERT INTO media(id,workspace_id,design_id,kind,phase,r2_key,thumb_key,sha256,created_at)
   VALUES (:id,:ws,:d,:kind,:phase,:key,:thumb,:sha,'2026-09-01T12:00:00Z')"""),
                {
                    "id": uuid.uuid4(),
                    "ws": ws,
                    "d": did,
                    "kind": "audio" if i == 64 else "image",
                    "phase": "sketch" if i == 63 else "editorial" if i >= 50 else "final",
                    "key": f"perf/{i}.png",
                    "thumb": f"perf/{i}/400.webp",
                    "sha": f"{i:064x}",
                },
            )
        await db.commit()

    def token(w):
        return jwt.encode(
            {"sub": str(user), "ws": str(w), "exp": datetime.now(UTC) + timedelta(hours=1)},
            get_settings().jwt_secret,
            algorithm="HS256",
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/gallery")).status_code == 401
        c.headers["Authorization"] = "Bearer " + token(ws)
        seen = []
        cursor = None
        pages = 0
        while True:
            r = await c.get("/gallery", params={"cursor": cursor} if cursor else {})
            assert r.status_code == 200, r.text
            data = r.json()
            pages += 1
            assert data["counts"] == {"final": 50, "editorial": 13}, data
            assert len(data["items"]) <= 24
            assert all("/400.webp" in it["media"]["thumb_url"] for it in data["items"])
            seen += data["items"]
            cursor = data["next_cursor"]
            if not cursor:
                break
        assert len(seen) == 63 and len({x["media"]["id"] for x in seen}) == 63
        assert all(x["media"]["phase"] == "final" for x in seen[:50])
        for a, b in itertools.pairwise(seen):
            if a["media"]["phase"] == b["media"]["phase"]:
                assert a["media"]["id"] > b["media"]["id"]
        r = await c.get("/gallery?phase=editorial")
        assert r.json()["total"] == 13 and len(r.json()["items"]) == 13
        cursor = (await c.get("/gallery?limit=1")).json()["next_cursor"]
        for params in [
            {"cursor": "!invalid"},
            {"cursor": base64.urlsafe_b64encode(json.dumps(
                [0, "2026-09-26T00:00:00+00:00", 123, "all"]
            ).encode()).decode()},
            {"limit": 0},
            {"limit": 61},
            {"phase": "note"},
            {"cursor": cursor, "phase": "editorial"},
        ]:
            assert (await c.get("/gallery", params=params)).status_code == 422, params
        # Keyset traversal does not duplicate/skip the remaining items after a deletion.
        first = (await c.get("/gallery?limit=1")).json()
        async with SessionLocal() as db:
            await db.execute(
                text("DELETE FROM media WHERE id=:id"),
                {"id": uuid.UUID(first["items"][0]["media"]["id"])},
            )
            await db.commit()
        second = (
            await c.get("/gallery", params={"limit": 1, "cursor": first["next_cursor"]})
        ).json()
        assert second["items"][0]["media"]["id"] == seen[1]["media"]["id"]
        c.headers["Authorization"] = "Bearer " + token(other)
        data = (await c.get("/gallery")).json()
        assert data["items"] == [] and data["projects"] == [] and data["total"] == 0
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM workspaces WHERE id IN (:a,:b)"), {"a": ws, "b": other})
        await db.commit()
    await engine.dispose()
    result = {
        "passed": True,
        "pages": pages,
        "items": 63,
        "checks": [
            "pagination/tied timestamps",
            "phase counts/filter",
            "thumbnail URLs",
            "invalid cursor/limit/filter",
            "delete-between-pages",
            "401",
            "workspace isolation",
        ],
    }
    print(json.dumps(result))


asyncio.run(main())
