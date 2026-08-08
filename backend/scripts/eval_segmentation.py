"""Segmentation EVAL (2026-08-06, Beezy: "comprehensive testing on whether the
model expectations are reflected in the scan — this is crucial").

Runs the CURRENT seg_prompt against a sample of real archive images and
scores each scan against explicit expectations — WITHOUT writing regions,
so it never disturbs cached segmentations. Re-run after any prompt change:
this is prompt regression testing with real model calls (≈1 Gemini flash
call per image; cutouts are reused from cache when present).

Run inside the worker/api container (needs GEMINI_API_KEY, DB, storage):

    python -m scripts.eval_segmentation [--per-category 2] [--category shoes]

Expectations scored per image:
  E1  regions_kept >= 2       (a product with one region can't be composed)
  E2  no duplicate labels     (v2 prompt demands distinct labels)
  E3  labels overlap the category vocabulary (>=1 vocab token match)
  E4  polygon-union covers >=55% of the product silhouette (cutout alpha
      when available, else skipped) — coarse polygons undershoot, the
      compositor's coverage pass closes the rest; below 55% the model
      genuinely missed structure
  E5  no region is a near-duplicate blob of another (IoU > 0.85)
"""

import argparse
import sys
from collections import defaultdict

import numpy as np
import psycopg

from app.config import get_settings
from app.storage import get_s3
from app.wada import refine, segmentation


def _sync_dsn() -> str:
    return get_settings().database_url.replace("+asyncpg", "")


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / float(union) if union else 0.0


def eval_media(conn, s3, bucket: str, media_id: str, category: str | None) -> dict:
    row = conn.execute(
        "SELECT r2_key, cutout_key FROM media WHERE id = %s", (media_id,)
    ).fetchone()
    r2_key, cutout_key = row
    source = s3.get_object(Bucket=bucket, Key=cutout_key or r2_key)["Body"].read()
    img, alpha = segmentation.working_image_and_alpha(source)

    # THE FULL FLOW, mirrored stage by stage (same functions the worker runs):
    # cutout-aware PREPARE -> category prompt -> model -> parse -> drop rules
    # -> per-region GrabCut refinement. Only persistence is skipped.
    prompt = segmentation.seg_prompt(category)
    regions = None
    subject = None
    for _attempt in range(3):  # mirror the worker: 1 + SEG_PARSE_RETRIES
        raw_text, meta = segmentation.call_gemini(
            img, get_settings().gemini_api_key, prompt=prompt
        )
        try:
            subject, regions = segmentation.parse_response(raw_text)
            break
        except Exception:  # noqa: BLE001, S112 — retries, like SEG_PARSE_RETRIES
            continue
    if regions is None:
        raise RuntimeError("model output unparseable after retries")
    kept, dropped = segmentation.apply_drop_rules(regions)

    image_arr = np.asarray(img)
    masks = {}
    refine_fallbacks = 0
    for r in kept:
        coarse = segmentation.polygon_to_mask(r.polygon, img.width, img.height)
        refined, info = refine.refine_mask(image_arr, coarse)
        if info.used_fallback:
            refine_fallbacks += 1
        masks[id(r)] = refined
    # mirror the worker: symmetric clones get ' 2' suffixes at persist time
    for r, lb in zip(kept, segmentation.dedupe_labels([r.label for r in kept])):
        r.label = lb
    labels = [r.label.strip().lower() for r in kept]

    vocab = segmentation.SEG_VOCAB.get((category or "").strip().lower(), "")
    vocab_tokens = {t.strip() for part in vocab.split(",") for t in part.split()}
    label_tokens = {t for lb in labels for t in lb.split()}

    union = None
    for m in masks.values():
        union = m if union is None else np.logical_or(union, m)
    coverage = None
    if alpha is not None and union is not None and alpha.sum():
        coverage = float(np.logical_and(union, alpha).sum()) / float(alpha.sum())

    dup_blobs = 0
    ms = list(masks.values())
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            if _iou(ms[i], ms[j]) > 0.85:
                dup_blobs += 1

    checks = {
        "E1_multi_region": len(kept) >= 2,
        "E2_distinct_labels": len(set(labels)) == len(labels),
        "E3_vocab_match": (not vocab) or bool(vocab_tokens & label_tokens),
        "E4_coverage": (coverage is None) or coverage >= 0.70,
        "E5_no_dup_blobs": dup_blobs == 0,
    }
    return {
        "media_id": media_id,
        "category": category,
        "subject": subject,
        "kept": len(kept),
        "dropped": len(dropped),
        "labels": labels,
        "coverage": coverage,
        "latency_s": meta.get("latency_s"),
        "refine_fallbacks": refine_fallbacks,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=2)
    ap.add_argument("--category", default=None, help="limit to one category")
    ap.add_argument("--images", default=None, help="eval local <category>__<name>.jpg files instead of archive media")
    args = ap.parse_args()

    if args.images:
        return main_images(args.images)

    s3 = get_s3()
    bucket = get_settings().s3_bucket
    results = []
    with psycopg.connect(_sync_dsn()) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (d.id) m.id, d.category, d.name, m.phase
            FROM media m
            JOIN designs d ON d.id = m.design_id
            WHERE m.kind = 'image' AND m.source_app IS DISTINCT FROM 'wada'
              AND (%(cat)s::text IS NULL OR lower(d.category) = lower(%(cat)s::text))
            ORDER BY d.id,
              -- study bases are PRODUCT shots: final/editorial/manufacturing
              -- first; moodboard scraps (imports) only when nothing better
              CASE m.phase WHEN 'final' THEN 0 WHEN 'editorial' THEN 1
                           WHEN 'manufacturing' THEN 2 ELSE 3 END,
              m.created_at ASC
            """,
            {"cat": args.category},
        ).fetchall()
        by_cat: dict[str, list] = defaultdict(list)
        for media_id, category, name, phase in rows:
            key = (category or "uncategorized").lower()
            if len(by_cat[key]) < args.per_category:
                by_cat[key].append((str(media_id), category, name, phase))

        for cat in sorted(by_cat):
            for media_id, category, name, phase in by_cat[cat]:
                try:
                    r = eval_media(conn, s3, bucket, media_id, category)
                except Exception as exc:  # noqa: BLE001 — eval keeps going
                    r = {"media_id": media_id, "category": category,
                         "error": str(exc)[:120], "passed": False, "checks": {}}
                # cohort: product shots are gradeable on coverage; moodboard
                # scraps are references — E4 does not apply to them
                cohort = "product" if phase in ("final", "editorial", "manufacturing") else "moodboard"
                if cohort == "moodboard":
                    r["checks"].pop("E4_coverage", None)
                    r["passed"] = all(r["checks"].values()) if r["checks"] else False
                r["design"] = name
                r["cohort"] = cohort
                results.append(r)
                status = "PASS" if r["passed"] else "FAIL"
                print(f"[{status}] {cohort[:4]:<5} {cat:<24} {name[:30]:<30} "
                      f"regions={r.get('kept', '?')} labels={r.get('labels', '?')} "
                      f"cov={f'{r['coverage']:.0%}' if r.get('coverage') is not None else 'n/a'}")

    print("\n===== SCORECARD =====")
    n_pass = sum(1 for r in results if r["passed"])
    print(f"{n_pass}/{len(results)} images meet ALL expectations")
    fail_counts: dict[str, int] = defaultdict(int)
    for r in results:
        for check, ok in r.get("checks", {}).items():
            if not ok:
                fail_counts[check] += 1
    for check, n in sorted(fail_counts.items()):
        print(f"  {check}: {n} failure(s)")
    for r in results:
        if not r["passed"]:
            bad = [c for c, ok in r.get("checks", {}).items() if not ok]
            print(f"  FAIL {r['design']}: {', '.join(bad) or r.get('error', '?')}")
    return 0 if n_pass == len(results) else 1




# ── local-images mode: the corpus harness (SSENSE flats etc.) ────────────────
# Runs the COMPLETE flow from raw file bytes: PhotoRoom cutout (real call,
# ~$0.02/img, cached to <file>.cutout.png beside the source) -> category
# prompt -> Gemini -> parse/dedupe/drop -> refinement -> coverage vs the
# cutout alpha. Category comes from the filename: <category>__<slug>.jpg
# ('fun_stuff' -> 'fun stuff').

#: Corpus ground truth (verified by eye 2026-08-08): SSENSE photographs
#: clothing ON MODELS — only bags/shoes/objects are true flats. The subject
#: expectation and the coverage bar follow the truth, not a blanket guess.
CORPUS_TRUTH: dict[str, str] = {
    "adidas-f50-sneaker": "single-product",
    "tabi-bianchetto-boots": "single-product",
    "heel-less-heels": "single-product",
    "elydea-pump": "single-product",
    "rick-owens-clutch": "single-product",
    "ferragamo-sofia": "single-product",
    "jazz-midi-pink": "worn-on-model",
    "jazz-midi-black": "worn-on-model",
    "elmo-jeans": "worn-on-model",
    "raw-jeans": "worn-on-model",
    "fonda-leather": "worn-on-model",
    "draculimo-down": "worn-on-model",
    "military-shorts": "worn-on-model",
    "mugler-tights": "worn-on-model",
    "mouflage-tee": "worn-on-model",
    "cicci-cap": "worn-on-model",
}


def eval_file(path, cutout_bytes: bytes, category: str | None) -> dict:
    del path  # category and bytes carry everything needed
    img, alpha = segmentation.working_image_and_alpha(cutout_bytes)
    prompt = segmentation.seg_prompt(category)
    regions = None
    subject = None
    for _attempt in range(3):  # mirror the worker: 1 + SEG_PARSE_RETRIES
        raw_text, meta = segmentation.call_gemini(
            img, get_settings().gemini_api_key, prompt=prompt
        )
        try:
            subject, regions = segmentation.parse_response(raw_text)
            break
        except Exception:  # noqa: BLE001, S112
            continue
    if regions is None:
        raise RuntimeError("model output unparseable after retries")
    kept, dropped = segmentation.apply_drop_rules(regions)
    image_arr = np.asarray(img)
    masks = {}
    refine_fallbacks = 0
    for r in kept:
        coarse = segmentation.polygon_to_mask(r.polygon, img.width, img.height)
        refined, info = refine.refine_mask(image_arr, coarse)
        if info.used_fallback:
            refine_fallbacks += 1
        masks[id(r)] = refined
    for r, lb in zip(kept, segmentation.dedupe_labels([r.label for r in kept])):
        r.label = lb
    labels = [r.label.strip().lower() for r in kept]
    vocab = segmentation.SEG_VOCAB.get((category or "").strip().lower(), "")
    vocab_tokens = {t.strip() for part in vocab.split(",") for t in part.split()}
    label_tokens = {t for lb in labels for t in lb.split()}
    union = None
    for m in masks.values():
        union = m if union is None else np.logical_or(union, m)
    coverage = None
    if alpha is not None and union is not None and alpha.sum():
        coverage = float(np.logical_and(union, alpha).sum()) / float(alpha.sum())
    dup_blobs = 0
    ms = list(masks.values())
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            if _iou(ms[i], ms[j]) > 0.85:
                dup_blobs += 1
    checks = {
        "E1_multi_region": len(kept) >= 2,
        "E2_distinct_labels": len(set(labels)) == len(labels),
        "E3_vocab_match": (not vocab) or bool(vocab_tokens & label_tokens),
        "E4_coverage": (coverage is None) or coverage >= 0.70,
        "E5_no_dup_blobs": dup_blobs == 0,
    }
    return {
        "kept": len(kept), "dropped": len(dropped), "labels": labels,
        "subject": subject,
        "coverage": coverage, "latency_s": meta.get("latency_s"),
        "refine_fallbacks": refine_fallbacks, "checks": checks,
        "passed": all(checks.values()),
    }


def main_images(images_dir: str) -> int:
    from pathlib import Path

    from app.workers.cutout import call_photoroom

    results = []
    for f in sorted(Path(images_dir).glob("*.jpg")):
        category = f.stem.split("__")[0].replace("_", " ")
        cut_cache = f.with_suffix(".cutout.png")
        try:
            if cut_cache.exists():
                cutout = cut_cache.read_bytes()
            else:
                cutout = call_photoroom(f.read_bytes())
                cut_cache.write_bytes(cutout)
            r = eval_file(f, cutout, category)
        except Exception as exc:  # noqa: BLE001
            r = {"error": str(exc)[:120], "passed": False, "checks": {}}
        slug = f.stem.split("__", 1)[-1]
        truth = CORPUS_TRUTH.get(slug)
        if truth and "checks" in r and r["checks"]:
            r["checks"]["E6_subject"] = r.get("subject") == truth
            if truth == "worn-on-model":
                # coverage over a person+garment silhouette is not a model
                # failure — the guard (correctly) never fills these
                r["checks"].pop("E4_coverage", None)
            r["passed"] = all(r["checks"].values())
        r["design"] = slug
        r["category"] = category
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"[{status}] {category:<12} {r['design'][:28]:<28} "
              f"subject={r.get('subject', '?')} regions={r.get('kept', '?')} "
              f"labels={r.get('labels', '?')} "
              f"cov={f'{r['coverage']:.0%}' if r.get('coverage') is not None else 'n/a'}")
    print("\n===== CORPUS SCORECARD =====")
    n_pass = sum(1 for r in results if r["passed"])
    print(f"{n_pass}/{len(results)} images meet ALL expectations")
    for r in results:
        if not r["passed"]:
            bad = [c for c, ok in r.get("checks", {}).items() if not ok]
            print(f"  FAIL {r['category']}/{r['design']}: {', '.join(bad) or r.get('error', '?')}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
