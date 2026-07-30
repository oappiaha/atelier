"""Residual coverage: make the slot-mask union cover the full product silhouette.

Why this exists: Gemini returns coarse ~20-vertex polygons, refined per region
by app.wada.refine — but each region is refined INDEPENDENTLY, so nothing
guarantees the union of all slot masks covers the whole product. Slivers
between regions and at flap corners stay outside every mask, and the §8.10
post-hoc composite keeps the ORIGINAL base colour there: a green bag recoloured
orange leaks green at the edges.

The fix: the PhotoRoom cutout's alpha channel is the ground-truth product
silhouette (segmentation.working_image_and_alpha). Every silhouette pixel
outside EVERY slot union is residual; each 8-connected residual component is
assigned WHOLLY to one slot — the slot it shares the most boundary with, or,
for components adjacent to no mask at all (edge fuzz, detached islands), the
slot nearest by Euclidean distance. Existing mask pixels are never removed or
moved: locked slots participate exactly like paint slots, so a residual sliver
hugging a locked slot joins the LOCK (stays unpainted) instead of being
swallowed by a recolour slot — and a lock's own area is never residual, so it
can never be reassigned.

Pure CV (cv2/numpy), no model calls, deterministic: assignment is judged
against the ORIGINAL masks (never order-dependent intermediate state) and ties
break on sorted key order. Runs at the working resolution in milliseconds.
"""

from collections.abc import Hashable, Mapping
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class CoverageInfo:
    residual_px: int = 0  # silhouette pixels outside every input mask
    n_components: int = 0  # 8-connected residual components assigned
    added_px: dict[Hashable, int] = field(default_factory=dict)  # per-mask gain


def cover_silhouette(
    silhouette: np.ndarray,
    masks: Mapping[Hashable, np.ndarray],
) -> tuple[dict[Hashable, np.ndarray], CoverageInfo]:
    """Expand `masks` so their union covers `silhouette`. Returns (new, info).

    silhouette: (H, W) bool — True = product. masks: key -> (H, W) bool.
    Only pixels inside the silhouette but outside EVERY mask are (re)assigned;
    mask pixels outside the silhouette are kept as-is. All-empty masks attract
    nothing (they have no boundary and no distance field worth trusting).
    """
    sil = silhouette.astype(bool)
    keys = sorted(masks)
    orig = {k: masks[k].astype(bool) for k in keys}
    for k in keys:
        if orig[k].shape != sil.shape:
            raise ValueError(
                f"mask {k!r} shape {orig[k].shape} disagrees with silhouette {sil.shape}"
            )
    result = {k: orig[k].copy() for k in keys}
    info = CoverageInfo(added_px=dict.fromkeys(keys, 0))
    donors = [k for k in keys if orig[k].any()]
    if not donors or not sil.any():
        return result, info

    union = np.zeros_like(sil)
    for k in donors:
        union |= orig[k]
    residual = sil & ~union
    info.residual_px = int(residual.sum())
    if info.residual_px == 0:
        return result, info

    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        residual.astype(np.uint8), connectivity=8
    )
    info.n_components = n - 1
    h, w = sil.shape
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dist: dict[Hashable, np.ndarray] = {}  # lazy distance-to-mask fields

    def dist_to(k: Hashable) -> np.ndarray:
        if k not in dist:
            dist[k] = cv2.distanceTransform((~orig[k]).astype(np.uint8), cv2.DIST_L2, 5)
        return dist[k]

    for i in range(1, n):
        x, y = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        cw, ch = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
        sl = (
            slice(max(0, y - 1), min(h, y + ch + 1)),
            slice(max(0, x - 1), min(w, x + cw + 1)),
        )
        comp = labels[sl] == i
        ring = (cv2.dilate(comp.astype(np.uint8), kernel) > 0) & ~comp
        # boundary shared with each ORIGINAL mask; most-adjacent wins,
        # ties break on sorted key order
        adjacency = [int((ring & orig[k][sl]).sum()) for k in donors]
        best = max(adjacency)
        if best > 0:
            target = donors[adjacency.index(best)]
        else:  # touches nothing (background-only component): nearest mask
            dmin = [float(dist_to(k)[sl][comp].min()) for k in donors]
            target = donors[dmin.index(min(dmin))]
        result[target][sl][comp] = True
        info.added_px[target] += int(comp.sum())
    return result, info
