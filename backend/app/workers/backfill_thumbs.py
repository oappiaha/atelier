"""Backfill thumbnails for image media committed before the resize worker
existed. Runs the generation synchronously in-process (no broker needed):

    cd backend && .venv/bin/python -m app.workers.backfill_thumbs

Idempotent: rows with a thumb_key already set are not selected, and the task
itself re-checks before rendering.
"""

import psycopg

from app.workers.thumbs import _sync_dsn, generate_thumbs


def main() -> None:
    with psycopg.connect(_sync_dsn()) as conn:
        ids = [
            str(r[0])
            for r in conn.execute(
                "SELECT id FROM media WHERE kind = 'image' AND thumb_key IS NULL ORDER BY created_at"
            ).fetchall()
        ]
    print(f"backfill: {len(ids)} image media rows without thumb_key")
    counts: dict[str, int] = {}
    for media_id in ids:
        status = generate_thumbs(media_id)  # direct call = synchronous execution
        bucket_name = status.split(":", 1)[0]
        counts[bucket_name] = counts.get(bucket_name, 0) + 1
        print(f"  {media_id} -> {status}")
    print(f"backfill done: {counts or 'nothing to do'}")


if __name__ == "__main__":
    main()
