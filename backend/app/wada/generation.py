"""Wada generation pipeline maths: chains, cache keys, compositing, guards
(TDD §8.8–§8.11 as amended by the M7-T1 chain-drift gate — GO with hard
conditions, <scratch>/dienda/atelier-m7/t1/chain-verdict.md).

Pure w.r.t. the database, storage and the model: everything here operates on
PIL images / numpy arrays / plain dicts, so it is unit-testable without a
worker. The Celery executor (app.workers.generate) is the only caller.

Gate conditions implemented here:
1. composite_step() — refined mask +2px dilation, 3px Gaussian feather,
   applied after EVERY chain step (the trie node artifact IS the composite).
2. verify_lock() / ghost_fraction() / in_region_delta_e() — per-step guards:
   byte-identity outside the feathered mask support, plus the T1 metrics
   logged onto every gen_nodes row.
3. build_prompt() — the per-region rewording validated by the gate, ported
   verbatim from the T1 scripts ("recolor only the {label}; leave every other
   part … exactly as in the input image"). Bumping PROMPT_VERSION to v2
   invalidates nothing: the trie is empty.

The vectorised sRGB→Lab and CIEDE2000 code is ported from the T1 scripts,
where it was validated against app.wada.color's scalar reference
implementation. Implementation notes that are load-bearing on this machine:
linearisation via a 256-entry LUT and einsum instead of matmul (Accelerate-
backed np.power/matmul on large arrays segfault in this environment), and
PIL Max/MinFilter for dilate/erode instead of cv2 (same reason).
"""

import hashlib
import io
import json
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from PIL import Image, ImageFilter

MODEL_ID = "seedream-5.0-pro"  # M0 decision: primary adapter
FAL_ENDPOINT = "bytedance/seedream/v5/pro/edit"  # no fal-ai/ prefix (M0)
PROMPT_VERSION = "v2"  # v2 = the T1-validated per-region template
COST_CENTS_PER_CALL = Decimal("6.75")  # $0.0675 @1536px (TDD §8.4, M0)
DILATE_PX = 2  # M5-T1 handoff, confirmed by the T1 gate
FEATHER_PX = 3  # §8.10: 2–4px; below 2 aliases, above 4 bleeds
RESOLUTION = 1536  # working long edge (TDD §8.1 PREPARE)

# SHIP-1 watchdog heartbeat: the executor refreshes this redis key per chain
# node; the GET /colorways on-read sweep treats its absence as "no active
# task". TTL is generous vs the ~40s/node cadence but far under the 15min
# colorway threshold. Defined here because worker and router both need the
# exact key format and neither may import the other.
HEARTBEAT_TTL = 180


def heartbeat_key(study_id) -> str:
    return f"gen:active:{study_id}"


# guard metric parameters (T1 metrics.py conventions)
E_CORE = 4  # erosion for core-region ΔE stats
E_BAND = 6  # boundary band width for ghost fraction
GHOST_DE = 8.0  # "still base-coloured" threshold
GHOST_ALERT = 0.20  # gate condition 2: threshold alert only, no auto-retry

# The T1-validated per-region prompt (gate condition 3) — ported verbatim
# from t1/scripts/run_calls.py NEW_TMPL. The blanket "keep metal hardware
# silver" line is BANNED: it is what metallised the embroidered crest in M0.
PROMPT_TEMPLATE = (
    "You are recolouring a single region of a product photograph.\n"
    "The second image is a mask: white marks the {phrase}.\n"
    "Recolor only the {phrase} to {color_name} ({color_hex}); leave every "
    "other part of the product exactly as in the input image.\n"
    "Preserve exactly: material texture, grain, stitching, wear, specular "
    "highlights, cast shadows, and all lighting direction and intensity.\n"
    "Change nothing outside the masked region.\n"
    "Photorealistic product photography. No stylisation."
)

CONFIDENCE_FLOOR = 0.6  # §8.9: below this the label cannot hurt us


def region_phrase(labels: list[str], min_confidence: float | None) -> str:
    """§8.9 fallback: the slot label(s) when trustworthy, else the neutral
    phrase. A step painting several slots joins their labels."""
    if min_confidence is not None and min_confidence < CONFIDENCE_FLOOR:
        return "highlighted region"
    return " and ".join(labels)


def build_prompt(phrase: str, color_name: str, color_hex: str) -> str:
    return PROMPT_TEMPLATE.format(
        phrase=phrase, color_name=color_name, color_hex=color_hex
    )


# ── chains (§8.8) ────────────────────────────────────────────────────────────

def chain_steps(
    mapping: dict[int, int], darkness: dict[int, tuple[float, int]]
) -> list[tuple[int, tuple[int, ...]]]:
    """The §8.8 call chain for one permutation mapping {slot_idx: color_id}:
    one step per DISTINCT colour, darkest first (L* ascending, id tiebreak —
    the same canonical order the estimate engine uses). Anchored slots are in
    the mapping too: an anchor is a real paint step, shared by every colorway
    in the study (maximum trie sharing). Returns [(color_id, (slot_idx, …))]
    with slot indices sorted."""
    groups: dict[int, list[int]] = {}
    for slot_idx, color_id in mapping.items():
        groups.setdefault(color_id, []).append(slot_idx)
    return [
        (color_id, tuple(sorted(slots)))
        for color_id, slots in sorted(groups.items(), key=lambda kv: darkness[kv[0]])
    ]


def trie_calls(
    mappings: list[dict[int, int]], darkness: dict[int, tuple[float, int]]
) -> int:
    """Distinct chain prefixes (§8.8 trie nodes = model calls) across FULL
    permutation mappings — anchored slots included, exactly as the executor
    walks them. This is the number the estimate must show for anchored
    studies (M8 fix: plan_study() counts only the free-slot space, so it
    undercounts by the anchor steps)."""
    prefixes: set[tuple] = set()
    for mapping in mappings:
        steps = tuple(chain_steps(mapping, darkness))
        for d in range(1, len(steps) + 1):
            prefixes.add(steps[:d])
    return len(prefixes)


def cache_key(
    base_sha: str,
    chain: list[tuple[str, str]],  # [(color_hex, union_mask_sha), …] so far
    model_id: str = MODEL_ID,
    prompt_version: str = PROMPT_VERSION,
    feather: int = FEATHER_PX,
    dilate_px: int = DILATE_PX,
    res: int = RESOLUTION,
) -> str:
    """§8.8 cache key: sha256 of the canonical JSON of everything that
    determines the node's pixels. The mask is identified by the sha of the
    union mask, not by slot id — two studies grouping the same regions the
    same way share nodes ("free money"). Deviation from the §8.8 sketch,
    documented: `dilate` is included alongside `feather` because the M5/T1
    compositing discipline is +2px dilation THEN 3px feather, and both change
    the artifact."""
    payload = {
        "base": base_sha,
        "chain": [{"color": c, "mask": m} for c, m in chain],
        "model": model_id,
        "prompt_v": prompt_version,
        "feather": feather,
        "dilate": dilate_px,
        "res": res,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── compositing + guards (§8.10 / gate conditions 1–2) ──────────────────────

def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    """Square-kernel dilation via PIL MaxFilter (cv2 segfaults alongside
    large numpy ops on this machine; at 2–8px the corner-pixel difference vs
    an ellipse kernel is immaterial — T1 note)."""
    if px <= 0:
        return mask
    m = Image.fromarray(mask.astype(np.uint8) * 255, "L")
    return np.asarray(m.filter(ImageFilter.MaxFilter(2 * px + 1))) > 127


def erode(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask
    m = Image.fromarray(mask.astype(np.uint8) * 255, "L")
    return np.asarray(m.filter(ImageFilter.MinFilter(2 * px + 1))) > 127


def composite_step(
    parent: Image.Image,
    edited: Image.Image,
    mask: np.ndarray,
    dilate_px: int = DILATE_PX,
    feather_px: int = FEATHER_PX,
) -> tuple[Image.Image, np.ndarray]:
    """Gate condition 1: composite through the refined mask +2px dilation,
    3px Gaussian feather — after EVERY chain step, never lazily at chain end
    (§2 of the verdict: the model re-renders the whole frame every pass; only
    per-step compositing keeps drift out of the chain).

    Returns (composite, untouched) where `untouched` marks the pixels the
    feathered mask cannot have influenced (weight exactly 0) — the region
    verify_lock() asserts byte-identity over."""
    m8 = Image.fromarray(dilate(mask, dilate_px).astype(np.uint8) * 255, "L")
    feathered = m8.filter(ImageFilter.GaussianBlur(feather_px))
    comp = Image.composite(edited, parent, feathered)
    untouched = np.asarray(feathered) == 0
    return comp, untouched


def touched_support(
    mask: np.ndarray, dilate_px: int = DILATE_PX, feather_px: int = FEATHER_PX
) -> np.ndarray:
    """The pixels a composite through this mask MAY influence (feathered
    weight > 0) — the complement of composite_step's `untouched`. Needed for
    cache-hit nodes, whose support must still count towards the colorway's
    final whole-chain lock assertion."""
    m8 = Image.fromarray(dilate(mask, dilate_px).astype(np.uint8) * 255, "L")
    return np.asarray(m8.filter(ImageFilter.GaussianBlur(feather_px))) > 0


def verify_lock(comp: Image.Image, parent: Image.Image, untouched: np.ndarray) -> bool:
    """§8.10: locked pixels are byte-identical to the source. Not 'visually
    identical' — identical. Asserted per step (gate condition 2); a False here
    is a P1 bug in the compositor, not a model-quality issue."""
    a = np.asarray(comp.convert("RGB"))
    b = np.asarray(parent.convert("RGB"))
    return bool(np.array_equal(a[untouched], b[untouched]))


# vectorised colour maths (T1 common.py, validated against app.wada.color) ──

_LIN_LUT = np.array(
    [
        (v / 255.0) / 12.92
        if (v / 255.0) <= 0.04045
        else (((v / 255.0) + 0.055) / 1.055) ** 2.4
        for v in range(256)
    ],
    dtype=np.float64,
)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """uint8 (..., 3) → Lab float (..., 3). D65, same pipeline as
    app.wada.color.hex_to_lab (LUT + einsum: see module docstring)."""
    lin = _LIN_LUT[np.ascontiguousarray(rgb)]
    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    xyz = np.einsum("...k,jk->...j", lin, m)
    xyz /= np.array([0.95047, 1.0, 1.08883])
    d = 6 / 29
    f = np.where(xyz > d**3, np.cbrt(xyz), xyz / (3 * d * d) + 4 / 29)
    lab = np.empty_like(xyz)
    lab[..., 0] = 116 * f[..., 1] - 16
    lab[..., 1] = 500 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200 * (f[..., 1] - f[..., 2])
    return lab


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Vectorised CIEDE2000 (kL=kC=kH=1), elementwise over (..., 3)."""
    l1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    l2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    c1, c2 = np.hypot(a1, b1), np.hypot(a2, b2)
    cb = (c1 + c2) / 2.0
    g = 0.5 * (1 - np.sqrt(cb**7 / (cb**7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    h1p = np.where((a1p == 0) & (b1 == 0), 0.0, h1p)
    h2p = np.where((a2p == 0) & (b2 == 0), 0.0, h2p)
    dlp = l2 - l1
    dcp = c2p - c1p
    diff = h2p - h1p
    dhp_deg = np.where(
        np.abs(diff) <= 180, diff, np.where(diff > 180, diff - 360, diff + 360)
    )
    dhp_deg = np.where(c1p * c2p == 0, 0.0, dhp_deg)
    dhp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp_deg) / 2.0)
    lpb = (l1 + l2) / 2.0
    cpb = (c1p + c2p) / 2.0
    hsum = h1p + h2p
    habs = np.abs(h1p - h2p)
    hpb = np.where(
        c1p * c2p == 0,
        hsum,
        np.where(
            habs <= 180,
            hsum / 2.0,
            np.where(hsum < 360, (hsum + 360) / 2.0, (hsum - 360) / 2.0),
        ),
    )
    t = (
        1
        - 0.17 * np.cos(np.radians(hpb - 30))
        + 0.24 * np.cos(np.radians(2 * hpb))
        + 0.32 * np.cos(np.radians(3 * hpb + 6))
        - 0.20 * np.cos(np.radians(4 * hpb - 63))
    )
    dtheta = 30 * np.exp(-(((hpb - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cpb**7 / (cpb**7 + 25.0**7))
    sl = 1 + (0.015 * (lpb - 50) ** 2) / np.sqrt(20 + (lpb - 50) ** 2)
    sc = 1 + 0.045 * cpb
    sh = 1 + 0.015 * cpb * t
    rt = -np.sin(np.radians(2 * dtheta)) * rc
    return np.sqrt(
        (dlp / sl) ** 2
        + (dcp / sc) ** 2
        + (dhp / sh) ** 2
        + rt * (dcp / sc) * (dhp / sh)
    )


def ghost_fraction(comp: Image.Image, base: Image.Image, mask: np.ndarray) -> float:
    """Share of the boundary band (mask & ~erode(mask, 6)) of the COMPOSITE
    still at the original base colour (ΔE00 < 8) — M5's seam-ghost measure,
    logged per node (gate condition 2; alert only above GHOST_ALERT)."""
    band = mask & ~erode(mask, E_BAND)
    if not band.any():
        return 0.0
    la = srgb_to_lab(np.asarray(comp.convert("RGB"))[band])
    lb = srgb_to_lab(np.asarray(base.convert("RGB"))[band])
    return float((ciede2000(la, lb) < GHOST_DE).mean())


def in_region_delta_e(
    comp: Image.Image, mask: np.ndarray, target_lab: tuple[float, float, float]
) -> float:
    """Median ΔE2000 vs the target colour inside erode(mask, 4) — core-region
    fidelity per node. Lighting/shading inflates this vs the flat hex (T1:
    medians 5–11.5 are the healthy ballpark); observability, not a gate."""
    core = erode(mask, E_CORE)
    if not core.any():
        core = mask
    if not core.any():
        return 0.0
    lab = srgb_to_lab(np.asarray(comp.convert("RGB"))[core])
    tgt = np.broadcast_to(np.array(target_lab, dtype=np.float64), lab.shape)
    return float(np.median(ciede2000(lab, tgt)))


# ── cost allocation (§8.11 reconciliation) ───────────────────────────────────

def alloc_cents(n_calls_before: int, per_call: Decimal = COST_CENTS_PER_CALL) -> int:
    """Integer cents for the (n+1)-th model call of a study, allocated so the
    running ledger sum is always round_half_up(per_call × n) — the same
    rounding the estimate endpoint applies to its total. 15 Seedream calls
    ledger to exactly 101¢, matching the fixture's $1.01 estimate; a flat
    7¢/call would drift to 105¢ and never reconcile."""

    def cum(n: int) -> int:
        return int((per_call * n).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return cum(n_calls_before + 1) - cum(n_calls_before)


# ── misc ─────────────────────────────────────────────────────────────────────

def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def mask_png_bytes(mask: np.ndarray) -> bytes:
    """PNG-encode a boolean mask exactly like the slot-union writer does
    (optimize=True) so identical unions produce identical bytes → shas."""
    buf = io.BytesIO()
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(
        buf, format="PNG", optimize=True
    )
    return buf.getvalue()


def unusable(img: Image.Image) -> bool:
    """§8.12: an all-black / all-white frame fails the node WITHOUT retry."""
    extrema = img.convert("L").getextrema()
    return extrema[0] == extrema[1]
