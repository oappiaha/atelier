"""Wada trie executor (TDD §8.8/§8.12, gated by the M7-T1 chain verdict).

wada.generate walks the planned trie for a set of colorways: per node it
edits the PARENT node's composited image with Seedream (fal queue), then —
gate condition 1 — composites the raw output back through the step's union
mask (+2px dilation, 3px feather) IMMEDIATELY, never lazily at chain end.
Gate condition 2 guards run per node: byte-identity outside the feathered
support (lock_verified — a failure marks the colorway failed and stops that
chain; no silent bad colorways), plus ghost-fraction and in-region median
ΔE2000 logged onto the gen_nodes row.

Cache semantics (§8.8): a gen_nodes row whose image object still exists IS
the node — the model is never called for it (hit_count++, $0, ledgered with
cache_hit=true). Re-running a study therefore resumes/idempotently replays
for free. A row whose object was evicted is treated as a miss and re-filled.

Budget (§8.11): checked before EVERY model call (study cap → daily cap →
hard freeze; config in app.config). A mid-run cap marks the study 'partial'
and keeps everything generated so far — never rolled back. Every model call
is ledgered in spend_ledger with integer cents allocated cumulatively so the
study total always equals round_half_up(6.75¢ × calls) — the exact number
the estimate showed (alloc_cents docstring).

Working base: the study's base media CUTOUT (the Wada contract since the
2026-07-26 PhotoRoom decision) — ensure_cutout() before the first node,
falling back to the original when no cutout can be produced. The working
frame must equal the region-mask frame; a mismatch fails the run loudly.

Documented deviations:
- Anchored slots are painted as real chain steps (shared by every colorway).
  plan_study() excludes anchors from the assignment space, so estimates for
  anchored studies undercount by the anchor steps — flagged for M8.
- colorways.thumb_key = cw/{ws}/{colorway_id}.400.webp (§3 defines no cw
  thumb prefix; kept under cw/ so the pin/export lifecycle finds it).
"""

import hashlib
import io
import json
import logging
import os
import time
import urllib.request
from datetime import UTC, datetime

import numpy as np
import psycopg
from PIL import Image

from app.config import get_settings
from app.storage import get_s3
from app.wada import generation as G
from app.wada import segmentation
from app.workers import cutout
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

LEDGER_KIND = "edit"
RETRIES = 3  # §8.12: 3 attempts, backoff, on transport/5xx errors


class NodeFailed(Exception):
    """A node that must fail its colorway without retry (§8.12)."""


def _sync_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


# ── the fal boundary (tests mock THIS) ───────────────────────────────────────

def call_seedream(
    input_img: Image.Image, mask_img: Image.Image, prompt: str,
    frame: tuple[int, int],
) -> tuple[Image.Image, float]:
    """ONE Seedream edit via the fal queue (subscribe = submit + poll).
    Ported from the T1 gate scripts verbatim (endpoint, argument shape,
    dimension coercion). Returns (RGB image in `frame`, latency_s). Retries
    transport errors with backoff; never retries an unusable frame — the
    caller checks that (§8.12)."""
    import fal_client

    settings = get_settings()
    if settings.fal_api_key:
        os.environ.setdefault("FAL_KEY", settings.fal_api_key)

    def upload(img: Image.Image) -> str:
        return fal_client.upload(G.png_bytes(img), "image/png")

    last_err: Exception | None = None
    for attempt in range(RETRIES):
        t0 = time.time()
        try:
            res = fal_client.subscribe(
                G.FAL_ENDPOINT,
                arguments={
                    "prompt": prompt,
                    "image_urls": [upload(input_img), upload(mask_img)],
                    "image_size": {"width": frame[0], "height": frame[1]},
                    "num_images": 1,
                    "output_format": "png",
                    "enable_safety_checker": False,
                },
                with_logs=False,
            )
            data = urllib.request.urlopen(  # noqa: S310 — fal returns https URLs
                res["images"][0]["url"], timeout=120
            ).read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            latency = time.time() - t0
            if img.size != frame:
                logger.info("seedream returned %s, resizing to frame %s", img.size, frame)
                img = img.resize(frame, Image.Resampling.LANCZOS)
            return img, latency
        except Exception as e:  # noqa: BLE001 — transport/5xx: retry with backoff
            last_err = e
            logger.warning("seedream attempt %d failed: %s", attempt, e)
            time.sleep(2 * (attempt + 1))
    raise NodeFailed(f"model call failed after {RETRIES} attempts: {last_err}")


# ── budget (§8.11, checked before every call) ────────────────────────────────

def spent_today_cents(conn, workspace_id) -> int:
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(cost_cents), 0) FROM spend_ledger "
        "WHERE workspace_id = %s AND occurred_at >= date_trunc('day', now())",
        (workspace_id,),
    ).fetchone()
    return int(total)


def blocked_tier(conn, workspace_id, study_actual_cents: int, next_cents: int) -> str | None:
    """None, or which cap refuses the next call: 'study' | 'daily' | 'freeze'."""
    s = get_settings()
    today = spent_today_cents(conn, workspace_id)
    if today >= s.budget_hard_freeze_cents:
        return "freeze"
    if study_actual_cents + next_cents > s.budget_study_cap_cents:
        return "study"
    if today + next_cents > s.budget_daily_cap_cents:
        return "daily"
    return None


# ── the executor ─────────────────────────────────────────────────────────────

@celery_app.task(name="wada.generate")
def generate_study(study_id: str, colorway_ids: list[str] | None = None) -> str:
    settings = get_settings()
    s3 = get_s3()
    bucket = settings.s3_bucket

    with psycopg.connect(_sync_dsn()) as conn:
        study = conn.execute(
            "SELECT workspace_id, base_media_id, palette_id, status, policy, "
            "       actual_cost_cents, model_id, prompt_version "
            "FROM studies WHERE id = %s",
            (study_id,),
        ).fetchone()
        if study is None:
            return "skipped:missing-study"
        ws, media_id, _palette_id, _status, _policy, _actual, _model, _pv = study

        slots = conn.execute(
            "SELECT idx, label, region_ids, union_mask_key, union_sha256 "
            "FROM slots WHERE study_id = %s AND kind = 'paint' ORDER BY idx",
            (study_id,),
        ).fetchall()
        if not slots:
            return "skipped:no-slots"
        slot_by_idx = {r[0]: {"label": r[1], "region_ids": r[2],
                              "mask_key": r[3], "mask_sha": r[4]} for r in slots}

        params: list = [study_id]
        id_filter = ""
        if colorway_ids is not None:  # [] means "nothing to do", not "everything"
            id_filter = " AND c.id = ANY(%s)"
            params.append(colorway_ids)
        targets = [
            (cw_id, status, idx,
             {int(k): v for k, v in
              (json.loads(m) if isinstance(m, str) else m).items()})
            for cw_id, status, idx, m in conn.execute(
                "SELECT c.id, c.status, p.idx, p.mapping FROM colorways c "
                "JOIN permutations p ON p.id = c.permutation_id "
                f"WHERE c.study_id = %s{id_filter} ORDER BY p.idx",
                params,
            ).fetchall()
        ]
        if not targets:
            return "skipped:no-colorways"

        color_ids = sorted({cid for _, _, _, m in targets for cid in m.values()})
        colors = {
            r[0]: {"name": r[1], "hex": r[2].strip(), "lab": (r[3], r[4], r[5])}
            for r in conn.execute(
                "SELECT id, name, hex, lab_l, lab_a, lab_b FROM sanzo_colors "
                "WHERE id = ANY(%s)",
                (color_ids,),
            ).fetchall()
        }
        darkness = {cid: (c["lab"][0], cid) for cid, c in colors.items()}

        # per-slot min region confidence (§8.9 label fallback)
        min_conf: dict[int, float] = {}
        for idx, s in slot_by_idx.items():
            (mc,) = conn.execute(
                "SELECT MIN(confidence) FROM regions WHERE id = ANY(%s)",
                (list(s["region_ids"]),),
            ).fetchone()
            min_conf[idx] = float(mc) if mc is not None else 1.0

        media = conn.execute(
            "SELECT r2_key, sha256 FROM media WHERE id = %s", (media_id,)
        ).fetchone()
        if media is None:
            return "skipped:missing-media"
        r2_key, media_sha = media

    # ── working base: the CUTOUT is the Wada contract; original is fallback ──
    cutout_key = cutout.ensure_cutout(str(media_id))
    source_key = cutout_key or r2_key
    source = s3.get_object(Bucket=bucket, Key=source_key)["Body"].read()
    base_img, _alpha = segmentation.working_image_and_alpha(source)
    base_png = G.png_bytes(base_img)
    base_sha = hashlib.sha256(base_png).hexdigest()
    frame = base_img.size
    logger.info("study=%s base=%s sha=%s frame=%s", study_id, source_key,
                base_sha[:12], frame)

    # slot masks, verified against the working frame
    slot_mask: dict[int, np.ndarray] = {}
    for idx, s in slot_by_idx.items():
        png = s3.get_object(Bucket=bucket, Key=s["mask_key"])["Body"].read()
        arr = np.asarray(Image.open(io.BytesIO(png)).convert("L")) > 127
        if arr.shape != (frame[1], frame[0]):
            raise RuntimeError(
                f"mask frame {arr.shape} disagrees with working frame {frame} — "
                "masks and base must share the 1536 frame"
            )
        slot_mask[idx] = arr

    # distinct step masks: single-slot steps reuse the slot union sha; multi-
    # slot steps (a colour shared by slots) union on the fly and upload to the
    # §3 union key so the sha is durable and shareable across studies
    step_masks: dict[tuple[int, ...], tuple[np.ndarray, str]] = {}

    def step_mask(slot_idxs: tuple[int, ...]) -> tuple[np.ndarray, str]:
        if slot_idxs in step_masks:
            return step_masks[slot_idxs]
        if len(slot_idxs) == 1:
            got = (slot_mask[slot_idxs[0]], slot_by_idx[slot_idxs[0]]["mask_sha"])
        else:
            union = slot_mask[slot_idxs[0]].copy()
            for i in slot_idxs[1:]:
                union |= slot_mask[i]
            png = G.mask_png_bytes(union)
            sha = hashlib.sha256(png).hexdigest()
            key = f"mask/{ws}/{media_sha}/union/{sha}.png"
            s3.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
            got = (union, sha)
        step_masks[slot_idxs] = got
        return got

    node_imgs: dict[str, Image.Image] = {}  # in-run memo
    done = failed_n = 0
    stop: str | None = None  # 'budget:<tier>' | 'cancelled'

    with psycopg.connect(_sync_dsn()) as conn:
        for cw_id, cw_status, perm_idx, mapping in targets:
            if stop:
                break
            if cw_status in ("ready", "pinned", "rejected"):
                continue
            steps = G.chain_steps(mapping, darkness)

            conn.execute(
                "UPDATE colorways SET status = 'generating', error = NULL WHERE id = %s",
                (cw_id,),
            )
            conn.commit()

            parent = base_img
            chain: list[tuple[str, str]] = []
            touched = np.zeros((frame[1], frame[0]), bool)
            new_cents = hits = 0
            fail: str | None = None
            t0 = time.time()

            for color_id, slot_idxs in steps:
                # cancellation is checked between nodes (§8.12)
                (st,) = conn.execute(
                    "SELECT status FROM studies WHERE id = %s", (study_id,)
                ).fetchone()
                if st == "cancelled":
                    stop = "cancelled"
                    break

                mask, mask_sha = step_mask(slot_idxs)
                color = colors[color_id]
                chain.append((color["hex"], mask_sha))
                key = G.cache_key(base_sha, chain)

                node = conn.execute(
                    "SELECT image_key FROM gen_nodes WHERE cache_key = %s", (key,)
                ).fetchone()
                cached = node_imgs.get(key)
                if node is not None and cached is None:
                    try:
                        obj = s3.get_object(Bucket=bucket, Key=node[0])["Body"].read()
                        cached = Image.open(io.BytesIO(obj)).convert("RGB")
                    except Exception:  # noqa: BLE001 — evicted object = cache miss
                        logger.info("node %s object evicted; regenerating", key[:12])
                        cached = None
                if node is not None and cached is not None:
                    conn.execute(
                        "UPDATE gen_nodes SET hit_count = hit_count + 1, "
                        "last_used_at = now() WHERE cache_key = %s",
                        (key,),
                    )
                    conn.execute(
                        "INSERT INTO spend_ledger (workspace_id, study_id, kind, "
                        "model_id, cost_cents, cache_hit) "
                        "VALUES (%s, %s, %s, %s, 0, true)",
                        (ws, study_id, LEDGER_KIND, G.MODEL_ID),
                    )
                    conn.commit()
                    node_imgs[key] = cached
                    parent = cached
                    touched |= G.touched_support(mask)
                    hits += 1
                    continue

                # §8.11 check #3: before EVERY model call
                (calls_before,) = conn.execute(
                    "SELECT COUNT(*) FROM spend_ledger "
                    "WHERE study_id = %s AND cache_hit = false",
                    (study_id,),
                ).fetchone()
                next_cents = G.alloc_cents(calls_before)
                (study_actual,) = conn.execute(
                    "SELECT actual_cost_cents FROM studies WHERE id = %s", (study_id,)
                ).fetchone()
                tier = blocked_tier(conn, ws, study_actual, next_cents)
                if tier is not None:
                    conn.execute(
                        "UPDATE colorways SET status = 'planned' WHERE id = %s",
                        (cw_id,),
                    )
                    conn.commit()
                    stop = f"budget:{tier}"
                    break

                labels = [slot_by_idx[i]["label"] for i in slot_idxs]
                phrase = G.region_phrase(labels, min(min_conf[i] for i in slot_idxs))
                prompt = G.build_prompt(phrase, color["name"], color["hex"])
                try:
                    raw, latency = call_seedream(
                        parent, Image.fromarray(mask.astype(np.uint8) * 255, "L"),
                        prompt, frame,
                    )
                except NodeFailed as e:
                    fail = str(e)
                    break
                if G.unusable(raw):
                    fail = "model returned an unusable frame (uniform image)"
                    break

                comp, untouched = G.composite_step(parent, raw, mask)
                lock = G.verify_lock(comp, parent, untouched)
                ghost = G.ghost_fraction(comp, base_img, mask)
                de_in = G.in_region_delta_e(comp, mask, color["lab"])
                if ghost > G.GHOST_ALERT:
                    logger.warning(
                        "study=%s node=%s ghost fraction %.3f > %.2f (alert only)",
                        study_id, key[:12], ghost, G.GHOST_ALERT,
                    )

                # money was spent whether or not the node is usable: ledger it
                cents = next_cents
                conn.execute(
                    "INSERT INTO spend_ledger (workspace_id, study_id, kind, "
                    "model_id, cost_cents, cache_hit) VALUES (%s, %s, %s, %s, %s, false)",
                    (ws, study_id, LEDGER_KIND, G.MODEL_ID, cents),
                )
                conn.execute(
                    "UPDATE studies SET actual_cost_cents = actual_cost_cents + %s "
                    "WHERE id = %s",
                    (cents, study_id),
                )
                new_cents += cents

                if not lock:
                    # gate condition 2: P1 — no silent bad colorways, and the
                    # node is NOT cached (a later run must not reuse it)
                    logger.error(
                        "study=%s node=%s LOCK ASSERT FAILED — chain stopped",
                        study_id, key[:12],
                    )
                    conn.commit()
                    fail = "lock_verified assertion failed (P1: composite touched locked pixels)"
                    break

                image_key = f"node/{ws}/{key}.png"
                s3.put_object(
                    Bucket=bucket, Key=image_key, Body=G.png_bytes(comp),
                    ContentType="image/png",
                )
                conn.execute(
                    "INSERT INTO gen_nodes (cache_key, workspace_id, parent_key, "
                    "depth, base_sha256, step_color_id, step_mask_sha, model_id, "
                    "prompt_version, image_key, cost_cents, lock_verified, "
                    "ghost_fraction, delta_e_in, latency_ms) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (cache_key) DO UPDATE SET image_key = EXCLUDED.image_key, "
                    "lock_verified = EXCLUDED.lock_verified, "
                    "ghost_fraction = EXCLUDED.ghost_fraction, "
                    "delta_e_in = EXCLUDED.delta_e_in, "
                    "latency_ms = EXCLUDED.latency_ms, last_used_at = now()",
                    (
                        key, ws,
                        G.cache_key(base_sha, chain[:-1]) if len(chain) > 1 else None,
                        len(chain), base_sha, color_id, mask_sha, G.MODEL_ID,
                        G.PROMPT_VERSION, image_key, cents, lock, ghost, de_in,
                        int(latency * 1000),
                    ),
                )
                conn.commit()
                node_imgs[key] = comp
                parent = comp
                touched |= ~untouched

            if stop:
                if stop == "cancelled":
                    conn.execute(
                        "UPDATE colorways SET status = 'planned' WHERE id = %s "
                        "AND status = 'generating'",
                        (cw_id,),
                    )
                    conn.commit()
                break
            if fail is not None:
                conn.execute(
                    "UPDATE colorways SET status = 'failed', error = %s, "
                    "cost_cents = cost_cents + %s, cache_hits = %s, "
                    "lock_verified = false, model_id = %s WHERE id = %s",
                    (fail, new_cents, hits, G.MODEL_ID, cw_id),
                )
                conn.commit()
                failed_n += 1
                continue

            # leaf: the whole-chain §8.10 assertion vs the BASE, then persist
            final_lock = bool(
                np.array_equal(
                    np.asarray(parent.convert("RGB"))[~touched],
                    np.asarray(base_img.convert("RGB"))[~touched],
                )
            )
            cw_key = f"cw/{ws}/{cw_id}.png"
            s3.put_object(Bucket=bucket, Key=cw_key, Body=G.png_bytes(parent),
                          ContentType="image/png")
            thumb = parent.copy()
            thumb.thumbnail((400, 400), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            thumb.save(buf, format="WEBP", quality=82)
            thumb_key = f"cw/{ws}/{cw_id}.400.webp"
            s3.put_object(Bucket=bucket, Key=thumb_key, Body=buf.getvalue(),
                          ContentType="image/webp")
            conn.execute(
                "UPDATE colorways SET status = 'ready', image_key = %s, "
                "thumb_key = %s, model_id = %s, cost_cents = %s, cache_hits = %s, "
                "lock_verified = %s, latency_ms = %s, error = NULL WHERE id = %s",
                (cw_key, thumb_key, G.MODEL_ID, new_cents, hits, final_lock,
                 int((time.time() - t0) * 1000), cw_id),
            )
            conn.commit()
            done += 1
            logger.info(
                "study=%s colorway=%s (perm %d) ready: %d¢, %d cache hits, lock=%s",
                study_id, cw_id, perm_idx, new_cents, hits, final_lock,
            )

        # study terminal status
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM colorways WHERE study_id = %s GROUP BY status",
            (study_id,),
        ).fetchall()
        counts = {r[0]: r[1] for r in rows}
        terminal = sum(counts.get(s, 0) for s in ("ready", "pinned", "rejected"))
        total = sum(counts.values())
        if stop == "cancelled":
            status, error = "cancelled", None
        elif stop and stop.startswith("budget:"):
            tier = stop.split(":", 1)[1]
            status = "partial"
            error = f"stopped by the {tier} budget cap (§8.11); partial results kept"
        elif total and terminal == total:
            status, error = "complete", None
        elif terminal > 0:
            status, error = "partial", None
        elif failed_n:
            status, error = "failed", "all requested colorways failed"
        else:
            status, error = "partial", None
        conn.execute(
            "UPDATE studies SET status = %s, error = %s, completed_at = %s "
            "WHERE id = %s AND status != 'cancelled'",
            (status, error,
             datetime.now(UTC) if status == "complete" else None, study_id),
        )
        conn.commit()

    return f"done:{done} failed:{failed_n} stop:{stop or 'none'}"
