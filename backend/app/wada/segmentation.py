"""Gemini segmentation (TDD §8.1–8.2, as amended by the M0 findings).

The M0 bake-off established the real contract for Gemini 3.x:

- model: ``gemini-3.5-flash`` (the 2.5 family 404s on our key),
- output: polygon masks ``[[y, x], ...]`` normalized 0–1000 (y-first, like
  box_2d), ~15–25 vertices — NEVER request base64-PNG masks (the model loops
  to MAX_TOKENS),
- latency 10–13s per photo at 1536px.

The polygons are coarse, so persisting them raw would break §8.10's
compositing guarantee — every kept region goes through ``app.wada.refine``
before it becomes a row in ``regions``. This module owns the model call,
response parsing, the §8.2 drop rules, and polygon rasterization; it is pure
w.r.t. the database and storage.
"""

import json
import re
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageOps

SEG_MODEL = "gemini-3.5-flash"
WORKING_LONG_EDGE = 1536  # the generation frame (TDD §8.1 PREPARE; M0 convention)

# §8.2 drop rules: spurious regions are filtered pre-persist.
CONF_MIN = 0.35
AREA_FRAC_MIN = 0.005

# M0's proven prompt, v2 (2026-08-06): the original example vocabulary was
# bag anatomy only — on footwear the model returned either one whole-shoe
# blob or clone regions all labeled "body" (Rei 1's: 5×"body"). v2 spans
# product types, demands DISTINCT labels, and forces construction/texture
# splits on monochrome products (a single-colour sneaker still has suede
# overlays / mesh panels / rubber sole as separate colourable parts).
SEG_PROMPT = (
    "Give the segmentation masks for the distinct physical parts of this product. "
    "Examples — bags: body, flap, straps, handles, hardware, zipper, pouch, trim; "
    "footwear: sole, upper, toe cap, heel, laces, tongue, mesh panel, overlay, lining; "
    "garments: body panel, sleeves, collar, cuffs, pockets, waistband, hood. "
    "Split parts by construction and material/texture boundaries (leather vs suede "
    "vs mesh vs rubber vs metal) even when the whole product is a single colour. "
    "Give every region a DISTINCT, specific label — never repeat a label. "
    "At most 8 regions, no background.\n"
    'Output a JSON list of segmentation masks where each entry contains the 2D '
    'bounding box in the key "box_2d" ([ymin, xmin, ymax, xmax] normalized to '
    '0-1000), the segmentation mask in the key "mask" as a polygon of [x, y] '
    "coordinates normalized to 0-1000, the text label in the key \"label\", and "
    'your labeling confidence 0.0-1.0 in the key "confidence".'
)


@dataclass
class RawRegion:
    """One region as returned by Gemini, before refinement."""

    label: str
    confidence: float
    box_2d: list[int]  # [ymin, xmin, ymax, xmax], 0–1000
    polygon: list[list[float]]  # [[y, x], ...], 0–1000 (y-first — M0 finding)


def working_image(original: bytes) -> Image.Image:
    """TDD §8.1 PREPARE: EXIF-transpose, RGB, resize to 1536px long edge."""
    import io

    img = Image.open(io.BytesIO(original))
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    long_edge = max(img.size)
    if long_edge > WORKING_LONG_EDGE:
        scale = WORKING_LONG_EDGE / long_edge
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return img


def working_image_and_alpha(source: bytes) -> tuple[Image.Image, "np.ndarray | None"]:
    """Cutout-aware PREPARE: the RGB working image plus, when the source has
    an alpha channel (a PhotoRoom cutout), the boolean product-coverage mask
    in the same working frame (True = opaque/product). Sources without alpha
    (originals) return (working_image(source), None).

    Transparent background is flattened onto WHITE — a clean studio sweep is
    the look Gemini segments best, and it avoids feeding the model whatever
    RGB values happen to sit under the transparent pixels.
    """
    import io

    img = Image.open(io.BytesIO(source))
    img = ImageOps.exif_transpose(img)
    if "A" not in img.getbands() and "transparency" not in img.info:
        return working_image(source), None
    img = img.convert("RGBA")
    long_edge = max(img.size)
    if long_edge > WORKING_LONG_EDGE:
        scale = WORKING_LONG_EDGE / long_edge
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    alpha = np.asarray(img)[..., 3] > 8
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, mask=img.getchannel("A"))
    return flat, alpha


def call_gemini(image: Image.Image, api_key: str) -> tuple[str, dict]:
    """ONE segmentation call (TDD §8.1: once per image, ever — the caller
    enforces the caching). Returns (raw_text, usage/latency metadata).

    This is the model boundary tests mock. Deliberately thin: no parsing.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    t0 = time.time()
    resp = client.models.generate_content(
        model=SEG_MODEL,
        contents=[SEG_PROMPT, image],
        config=types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=16384,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        ),
    )
    usage = resp.usage_metadata
    meta = {
        "model": SEG_MODEL,
        "latency_s": round(time.time() - t0, 2),
        "input_tokens": usage.prompt_token_count if usage else None,
        "output_tokens": usage.candidates_token_count if usage else None,
        "finish_reason": str(resp.candidates[0].finish_reason) if resp.candidates else None,
    }
    return resp.text or "", meta


def parse_regions(raw_text: str) -> list[RawRegion]:
    """Parse the model's JSON (possibly fenced) into RawRegions."""
    m = re.search(r"\[.*\]", raw_text, re.DOTALL)
    if m is None:
        raise ValueError(f"no JSON array in segmentation response: {raw_text[:200]!r}")
    out: list[RawRegion] = []
    for entry in json.loads(m.group(0)):
        poly = entry.get("mask")
        if not isinstance(poly, list) or len(poly) < 3:
            continue  # a region without a usable polygon can't become a mask
        out.append(
            RawRegion(
                label=str(entry.get("label", "region")),
                confidence=float(entry.get("confidence", 1.0)),
                box_2d=[int(v) for v in entry["box_2d"]],
                polygon=poly,
            )
        )
    return out


def box_area_fraction(box_2d: list[int]) -> float:
    y0, x0, y1, x1 = (v / 1000 for v in box_2d)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def apply_drop_rules(regions: list[RawRegion]) -> tuple[list[RawRegion], list[RawRegion]]:
    """§8.2: drop confidence < 0.35 or (box) area < 0.5%. Returns (kept, dropped)."""
    kept, dropped = [], []
    for r in regions:
        if r.confidence < CONF_MIN or box_area_fraction(r.box_2d) < AREA_FRAC_MIN:
            dropped.append(r)
        else:
            kept.append(r)
    return kept, dropped


def polygon_to_mask(polygon: list[list[float]], width: int, height: int) -> np.ndarray:
    """Rasterize a Gemini polygon ([y, x] 0–1000) to a bool mask at (H, W)."""
    pts = [(p[1] / 1000 * width, p[0] / 1000 * height) for p in polygon]
    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).polygon(pts, fill=255)
    return np.asarray(canvas) > 127


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "region"
