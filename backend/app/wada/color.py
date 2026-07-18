"""Pure-python colour math for Wada Studio (TDD §2.3, §8.5, §8.7).

CIELAB conversion
    hex → sRGB (IEC 61966-2-1 transfer) → XYZ (sRGB D65 matrix) → CIELAB,
    D65 illuminant, 2° standard observer (Xn=0.95047, Yn=1.0, Zn=1.08883).

    NOTE on the corpus: mattdesl/dictionary-of-colour-combinations ships its
    own `lab` values derived from the CMYK plates through a print profile;
    they diverge from the hex swatches by up to ΔE≈20 (worst: Peacock Blue).
    We derive Lab from the HEX because §8.9 hands the model adapter
    `name + hex + lab` as one unit — the hex is what gets painted, so the
    Lab used for ranking/dedupe must describe the same colour.

ΔE: CIEDE2000 (Sharma, Wu & Dalal 2005 formulation), used by the §8.5
twin rule and the palette max/min internal-contrast columns.

hue_family / temperature are heuristics over LAB hue angle + chroma — the
TDD names the families (red|orange|yellow|green|cyan|blue|purple|neutral)
and warm|cool|neutral but not the boundaries; the bands below are anchored
on the LAB hue angles of the sRGB primaries (red≈40°, yellow≈103°,
green≈136°, cyan≈196°, blue≈306°).
"""

import math

# a colour this muted has no meaningful hue read
NEUTRAL_CHROMA_MAX = 12.0

HUE_FAMILIES = ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "neutral")
TEMPERATURES = ("warm", "cool", "neutral")

# (upper bound in degrees, family) — scanned in order; red wraps through 0
_HUE_BANDS = (
    (20.0, "purple"),  # 315..360 wraps into 0..20 as red; see hue_family()
    (45.0, "red"),
    (75.0, "orange"),
    (115.0, "yellow"),
    (170.0, "green"),
    (250.0, "cyan"),
    (315.0, "blue"),
    (360.0, "purple"),
)


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _srgb_to_linear(u: float) -> float:
    return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4


def hex_to_lab(hex_str: str) -> tuple[float, float, float]:
    """sRGB hex → CIELAB (D65, 2° observer)."""
    r, g, b = (_srgb_to_linear(v / 255.0) for v in hex_to_rgb(hex_str))
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    xn, yn, zn = 0.95047, 1.0, 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def chroma(a: float, b: float) -> float:
    """C*ab = sqrt(a² + b²) (TDD §2.3)."""
    return math.hypot(a, b)


def hue_deg(a: float, b: float) -> float:
    """h_ab = atan2(b, a) in degrees, normalised to [0, 360)."""
    return math.degrees(math.atan2(b, a)) % 360.0


def hue_family(a: float, b: float) -> str:
    c = chroma(a, b)
    if c < NEUTRAL_CHROMA_MAX:
        return "neutral"
    h = hue_deg(a, b)
    if h < 20.0:  # low wrap of the red band
        return "red"
    for upper, family in _HUE_BANDS[1:]:
        if h < upper:
            return family
    return "purple"


def color_temperature(a: float, b: float) -> str:
    """warm = reds through yellows; cool = greens through purples; neutral = muted."""
    c = chroma(a, b)
    if c < NEUTRAL_CHROMA_MAX:
        return "neutral"
    h = hue_deg(a, b)
    return "warm" if h < 115.0 or h >= 340.0 else "cool"


def palette_temperature(labs: list[tuple[float, float, float]]) -> str:
    """Majority vote of the members' temperatures; ties or all-neutral → neutral."""
    votes = [color_temperature(a, b) for (_l, a, b) in labs]
    warm, cool = votes.count("warm"), votes.count("cool")
    if warm > cool:
        return "warm"
    if cool > warm:
        return "cool"
    return "neutral"


def delta_e_2000(
    lab1: tuple[float, float, float], lab2: tuple[float, float, float]
) -> float:
    """CIEDE2000 colour difference (kL = kC = kH = 1)."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)

    def hp(ap: float, bp: float) -> float:
        if ap == 0 and bp == 0:
            return 0.0
        return math.degrees(math.atan2(bp, ap)) % 360.0

    h1p, h2p = hp(a1p, b1), hp(a2p, b2)

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp_deg = 0.0
    else:
        diff = h2p - h1p
        if abs(diff) <= 180:
            dhp_deg = diff
        elif diff > 180:
            dhp_deg = diff - 360
        else:
            dhp_deg = diff + 360
    dhp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp_deg) / 2.0)

    lp_bar = (l1 + l2) / 2.0
    cp_bar = (c1p + c2p) / 2.0
    if c1p * c2p == 0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_bar = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        hp_bar = (h1p + h2p + 360) / 2.0
    else:
        hp_bar = (h1p + h2p - 360) / 2.0

    t = (
        1
        - 0.17 * math.cos(math.radians(hp_bar - 30))
        + 0.24 * math.cos(math.radians(2 * hp_bar))
        + 0.32 * math.cos(math.radians(3 * hp_bar + 6))
        - 0.20 * math.cos(math.radians(4 * hp_bar - 63))
    )
    d_theta = 30 * math.exp(-(((hp_bar - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cp_bar**7 / (cp_bar**7 + 25.0**7))
    sl = 1 + (0.015 * (lp_bar - 50) ** 2) / math.sqrt(20 + (lp_bar - 50) ** 2)
    sc = 1 + 0.045 * cp_bar
    sh = 1 + 0.015 * cp_bar * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc

    return math.sqrt(
        (dlp / sl) ** 2
        + (dcp / sc) ** 2
        + (dhp / sh) ** 2
        + rt * (dcp / sc) * (dhp / sh)
    )
