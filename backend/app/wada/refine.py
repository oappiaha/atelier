"""Mask refinement: coarse Gemini polygon -> product-quality binary mask.

Why this exists (M0 finding, now load-bearing for §8.10): Gemini 3.x returns
~15–25-vertex polygons with sloppy borders. Composited recolors through those
masks show base-color ghosting at region boundaries. This module turns the
polygon into a pixel-accurate mask with pure CV — no extra model spend:

1. Seed GrabCut from the polygon: eroded interior = sure-foreground, a dilated
   band = uncertain, everything beyond = sure-background. The GMMs then pull
   the boundary onto the actual color edge.
2. Clean up: keep connected components that the polygon interior claims, drop
   specks, fill pinholes (real enclosed background — a handle loop — is kept).
3. Guard: if the result diverges wildly from the polygon (IoU < 0.30) the
   refinement is judged failed and the coarse mask is returned unchanged —
   a coarse mask is bad, a hallucinated one is worse.

Deterministic for a given input (GrabCut's GMM init is data-driven, no RNG
seeded from time), and runs in well under a second per region at 1536px
because it operates on a padded ROI crop, not the full frame.

Also home to the small numpy color kit the pipeline needs (vectorized
sRGB->CIELAB, same D65 math as app/wada/color.py but per-pixel) and the §8.10
feathered composite used by generation and by the M5 validation harness.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageFilter

# ── vectorized color math (matches app/wada/color.py scalar version, D65) ────

_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])

# 256-entry linearization LUT: image inputs are uint8, and running the **2.4
# power ufunc over multi-megapixel float64 arrays intermittently segfaults
# numpy 2.5 on macOS/Accelerate. 256 pows once at import is also just faster.
_u = np.arange(256) / 255.0
_SRGB_LINEAR_LUT = np.where(_u <= 0.04045, _u / 12.92, ((_u + 0.055) / 1.055) ** 2.4)
del _u


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) uint8 or float [0,1] sRGB -> (..., 3) CIELAB (D65, 2°)."""
    if rgb.dtype == np.uint8:
        c = _SRGB_LINEAR_LUT[rgb]
    else:
        c = rgb.astype(np.float64)
        if c.max() > 1.0:
            c = c / 255.0
        c = _SRGB_LINEAR_LUT[np.clip(np.rint(c * 255), 0, 255).astype(np.uint8)]
    # elementwise instead of `c @ _SRGB_TO_XYZ.T`: Accelerate's BLAS matmul on
    # multi-megapixel arrays is the other intermittent-segfault source here
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    m = _SRGB_TO_XYZ
    xyz = np.stack(
        [m[i, 0] * r + m[i, 1] * g + m[i, 2] * b for i in range(3)], axis=-1
    ) / _WHITE_D65
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    lab = np.empty_like(f)
    lab[..., 0] = 116 * f[..., 1] - 16
    lab[..., 1] = 500 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200 * (f[..., 1] - f[..., 2])
    return lab


def delta_e76(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    """Per-pixel CIE76 ΔE between two (..., 3) Lab arrays."""
    return np.sqrt(((lab_a - lab_b) ** 2).sum(axis=-1))


# ── refinement ───────────────────────────────────────────────────────────────

IOU_FALLBACK_MIN = 0.30  # refined must overlap the polygon this much or we keep coarse
BAND_OUT_PX = 24  # how far outside the polygon the true edge may plausibly be
GRABCUT_ITERS = 5
MIN_COMPONENT_FRAC = 0.005  # specks below 0.5% of the region area are dropped
MAX_HOLE_FRAC = 0.02  # holes below 2% of region area are pinholes -> filled


@dataclass
class RefineInfo:
    used_fallback: bool
    iou_vs_coarse: float
    r_in: int
    band_px: int


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(a, b).sum() / union)


def mask_bbox(mask: np.ndarray) -> list[int]:
    """[x0, y0, x1, y1] (exclusive right/bottom) in mask pixel space."""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return [0, 0, 0, 0]
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def _disk(r: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def _cleanup(refined: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """Keep components claimed by the seed, drop specks, fill pinholes."""
    region_area = max(int(refined.sum()), 1)
    n, labels = cv2.connectedComponents(refined.astype(np.uint8), connectivity=8)
    keep = np.zeros_like(refined)
    for i in range(1, n):
        comp = labels == i
        area = int(comp.sum())
        if area < MIN_COMPONENT_FRAC * region_area:
            continue
        # a component nowhere near the polygon interior is GrabCut wandering off
        if not np.logical_and(comp, seed).any():
            continue
        keep |= comp
    # fill pinholes: background components NOT connected to the ROI border
    inv = (~keep).astype(np.uint8)
    n_bg, bg_labels = cv2.connectedComponents(inv, connectivity=4)
    border = np.zeros_like(keep)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    border_ids = set(np.unique(bg_labels[border]))
    keep_area = max(int(keep.sum()), 1)
    for i in range(1, n_bg):
        if i in border_ids:
            continue
        hole = bg_labels == i
        if hole.sum() <= MAX_HOLE_FRAC * keep_area:
            keep |= hole
    return keep


def _gate_by_edge_evidence(
    refined: np.ndarray, coarse: np.ndarray, image_rgb: np.ndarray
) -> np.ndarray:
    """Revert refinement wherever it isn't backed by an actual image edge.

    GrabCut snaps beautifully onto color/texture edges (silhouette, hardware,
    contrasting parts) but WANDERS where two same-material regions meet (a
    body/top-panel boundary in identical leather): there it replaces Gemini's
    straight semantic line with an organic wobble that reads as an error in
    the composite. Per connected component of (refined XOR coarse), keep the
    change only if the new boundary it creates sits on stronger gradient than
    the polygon boundary it replaces; otherwise the polygon was the only
    truth available — keep it.
    """
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx**2 + gy**2)

    contour_new = refined & ~(cv2.erode(refined.astype(np.uint8), _disk(1)) > 0)
    contour_old = coarse & ~(cv2.erode(coarse.astype(np.uint8), _disk(1)) > 0)

    # The XOR diff is typically ONE connected ring hugging the whole boundary,
    # so judging whole components would let strong silhouette segments outvote
    # a same-material wobble elsewhere. Judge locally: component ∩ grid tile.
    out = refined.copy()
    diff = refined ^ coarse
    h, w = diff.shape
    tile = 48
    n, labels = cv2.connectedComponents(diff.astype(np.uint8), connectivity=8)
    for ty in range(0, h, tile):
        for tx in range(0, w, tile):
            sl = (slice(ty, min(ty + tile, h)), slice(tx, min(tx + tile, w)))
            tdiff = diff[sl]
            if not tdiff.any():
                continue
            for i in np.unique(labels[sl][tdiff]):
                if i == 0:
                    continue
                comp = np.zeros_like(diff)
                comp[sl] = labels[sl] == i
                if comp.sum() < 24:  # tiny slivers: keep refined
                    continue
                near = cv2.dilate(comp.astype(np.uint8), _disk(3)) > 0
                new_seg = contour_new & near
                old_seg = contour_old & near
                if not new_seg.any() or not old_seg.any():
                    continue
                if float(grad[new_seg].mean()) < 1.05 * float(grad[old_seg].mean()):
                    out[comp] = coarse[comp]  # no edge evidence -> the polygon
    # soften the notches where accepted and reverted tiles meet — but a 3px
    # open would erase thin regions (zippers, piping), so scale to thickness
    thickness = float(cv2.distanceTransform(coarse.astype(np.uint8), cv2.DIST_L2, 5).max())
    r = 3 if thickness >= 8 else 1
    sm = cv2.morphologyEx(out.astype(np.uint8), cv2.MORPH_CLOSE, _disk(r))
    sm = cv2.morphologyEx(sm, cv2.MORPH_OPEN, _disk(r))
    return sm > 0


def refine_mask(
    image_rgb: np.ndarray,
    coarse: np.ndarray,
    *,
    band_px: int = BAND_OUT_PX,
) -> tuple[np.ndarray, RefineInfo]:
    """Refine a coarse polygon mask against the image. Returns (mask, info).

    image_rgb: (H, W, 3) uint8 in the working frame; coarse: (H, W) bool.
    """
    coarse = coarse.astype(bool)
    if not coarse.any():
        return coarse, RefineInfo(True, 1.0, 0, band_px)
    h, w = coarse.shape

    # ROI crop — GrabCut cost scales with pixels, and a far-away background
    # teaches the BG model nothing about the local edge.
    x0, y0, x1, y1 = mask_bbox(coarse)
    pad = 2 * band_px
    rx0, ry0 = max(0, x0 - pad), max(0, y0 - pad)
    rx1, ry1 = min(w, x1 + pad), min(h, y1 + pad)
    roi_img = np.ascontiguousarray(image_rgb[ry0:ry1, rx0:rx1])
    roi_coarse = coarse[ry0:ry1, rx0:rx1].astype(np.uint8)

    # Seed radii from the region's thickness so thin regions (straps, zippers)
    # keep a sure-foreground core.
    dist = cv2.distanceTransform(roi_coarse, cv2.DIST_L2, 5)
    max_dist = float(dist.max())
    r_in = int(np.clip(0.35 * max_dist, 2, 20))
    sure_fg = dist >= r_in
    if not sure_fg.any():
        sure_fg = dist >= 0.5 * max_dist

    dilated = cv2.dilate(roi_coarse, _disk(band_px)) > 0

    gc = np.full(roi_coarse.shape, cv2.GC_BGD, np.uint8)
    gc[dilated] = cv2.GC_PR_BGD
    gc[roi_coarse > 0] = cv2.GC_PR_FGD
    gc[sure_fg] = cv2.GC_FGD

    # Feed GrabCut chroma-weighted Lab instead of RGB: on product photos a
    # specular highlight along a rim reads (in RGB) as closer to the white
    # background than to its own material, and the GMM carves it out of the
    # region. Halving L makes material chroma dominate the models.
    lab_img = cv2.cvtColor(roi_img, cv2.COLOR_RGB2Lab)
    gc_input = lab_img.copy()
    gc_input[..., 0] = lab_img[..., 0] // 2

    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(gc_input, gc, None, bgd, fgd, GRABCUT_ITERS, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return coarse, RefineInfo(True, 1.0, r_in, band_px)

    roi_refined = np.isin(gc, (cv2.GC_FGD, cv2.GC_PR_FGD))
    roi_refined = _cleanup(roi_refined, sure_fg | (roi_coarse > 0))
    roi_refined = _gate_by_edge_evidence(roi_refined, roi_coarse > 0, roi_img)

    refined = np.zeros_like(coarse)
    refined[ry0:ry1, rx0:rx1] = roi_refined

    overlap = iou(refined, coarse)
    if overlap < IOU_FALLBACK_MIN:
        return coarse, RefineInfo(True, overlap, r_in, band_px)
    return refined, RefineInfo(False, overlap, r_in, band_px)


# ── §8.10 feathered composite (the correctness guarantee) ────────────────────


def feather_composite(
    base: Image.Image, edited: Image.Image, mask: np.ndarray, feather_px: int = 3
) -> Image.Image:
    """base everywhere EXCEPT the mask; edited only inside it. Feather 2–4px."""
    if edited.size != base.size:
        edited = edited.resize(base.size, Image.Resampling.LANCZOS)
    m = Image.fromarray((mask.astype(np.uint8)) * 255, "L")
    m = m.filter(ImageFilter.GaussianBlur(feather_px))
    return Image.composite(edited, base, m)


# ── boundary quality metrics (used by tests + the M5 validation harness) ─────


def boundary_band(mask: np.ndarray, px: int, side: str = "both") -> np.ndarray:
    """Pixels within `px` of the mask boundary: 'in', 'out', or 'both'."""
    m = mask.astype(np.uint8)
    dil = cv2.dilate(m, _disk(px)) > 0
    ero = cv2.erode(m, _disk(px)) > 0
    if side == "in":
        return mask & ~ero
    if side == "out":
        return dil & ~mask
    return dil & ~ero


def boundary_edge_strength(image_rgb: np.ndarray, mask: np.ndarray) -> float:
    """Mean image-gradient magnitude along the mask contour. A boundary that
    hugs the true product edge sits on strong gradients; a sloppy polygon
    cuts through flat leather. Higher is better."""
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx**2 + gy**2)
    contour = mask & ~(cv2.erode(mask.astype(np.uint8), _disk(1)) > 0)
    if not contour.any():
        return 0.0
    return float(grad[contour].mean())


def boundary_color_separation(image_rgb: np.ndarray, mask: np.ndarray, px: int = 4) -> float:
    """ΔE76 between mean colors just inside vs just outside the boundary.
    A correct region boundary separates two materials -> large ΔE."""
    inner = boundary_band(mask, px, "in")
    outer = boundary_band(mask, px, "out")
    if not inner.any() or not outer.any():
        return 0.0
    lab = srgb_to_lab(image_rgb)
    return float(delta_e76(lab[inner].mean(axis=0), lab[outer].mean(axis=0)))
