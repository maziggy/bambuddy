"""Color comparison utilities for RFID/firmware color matching."""

import math


def colors_similar(hex_a: str, hex_b: str, threshold: int = 50) -> bool:
    """Compare two RRGGBB(AA) hex colors with tolerance for RFID/firmware variations.

    Uses Euclidean RGB distance. Alpha channel (bytes 7-8) is ignored.
    Default threshold of 50 accommodates typical RFID read variations
    (e.g. 7CC4D5 vs 56B7E6 = distance ~43.6) while rejecting clearly
    different colors (e.g. red vs blue = distance ~360).
    """
    a = hex_a.strip().upper()
    b = hex_b.strip().upper()
    if a == b:
        return True
    if len(a) < 6 or len(b) < 6:
        return False
    try:
        ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    except ValueError:
        return False
    dist = ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5
    return dist <= threshold


# --- Perceptual colour difference (CIEDE2000) ---------------------------------
#
# Ranking spools by RGB distance rates a colour by how far apart the numbers
# are, which is not how far apart they look: RGB overweights blue badly, so a
# required green could take a purple over a green that was numerically further
# away. CIEDE2000 is the CIE's perceptual metric, and small differences — which
# is all this ever sees, since candidates are already inside a narrow tolerance
# — are exactly the regime its predecessors handle worst.
#
# Mirrored in `frontend/src/utils/amsHelpers.ts` (`colorDistance`). The two must
# agree: the dialog must not promise a spool the scheduler would not pick.

_D65_WHITE = (0.95047, 1.0, 1.08883)
_DELTA = 6.0 / 29.0


def _hex_to_lab(hex_color: str) -> tuple[float, float, float] | None:
    """Convert ``RRGGBB(AA)`` to CIE L*a*b* under D65, or None if unusable.

    Alpha is ignored: the alpha a slicer writes for a transparent filament is
    not a colour the user chose, and counting it would stop a transparent
    filament matching itself.
    """
    cleaned = hex_color.replace("#", "").strip().lower()
    if len(cleaned) < 6:
        return None
    try:
        channels = [int(cleaned[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None

    # sRGB gamma -> linear light.
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)

    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > _DELTA**3 else t / (3 * _DELTA * _DELTA) + 4.0 / 29.0

    fx, fy, fz = (f(v / w) for v, w in zip((x, y, z), _D65_WHITE, strict=True))
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def _ciede2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 colour difference between two L*a*b* triples.

    Straight transcription of the CIE formulation, with the parametric weights
    kL = kC = kH = 1. Verified against the Sharma/Wu/Dalal published test set,
    including the hue-discontinuity pairs that catch sign errors.
    """
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2

    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar7 = ((c1 + c2) / 2.0) ** 7
    g = 0.5 * (1.0 - math.sqrt(c_bar7 / (c_bar7 + 25.0**7)))

    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = math.hypot(a1p, b1)
    c2p = math.hypot(a2p, b2)

    def hue(ap: float, bp: float) -> float:
        if ap == 0.0 and bp == 0.0:
            return 0.0
        deg = math.degrees(math.atan2(bp, ap))
        return deg + 360.0 if deg < 0 else deg

    h1p = hue(a1p, b1)
    h2p = hue(a2p, b2)

    dlp = l2 - l1
    dcp = c2p - c1p

    chroma_product = c1p * c2p
    if chroma_product == 0.0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180.0:
            dhp -= 360.0
        elif dhp < -180.0:
            dhp += 360.0
    dhp_big = 2.0 * math.sqrt(chroma_product) * math.sin(math.radians(dhp) / 2.0)

    l_bar = (l1 + l2) / 2.0
    c_bar = (c1p + c2p) / 2.0

    if chroma_product == 0.0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        h_bar = (h1p + h2p) / 2.0
    elif h1p + h2p < 360.0:
        h_bar = (h1p + h2p + 360.0) / 2.0
    else:
        h_bar = (h1p + h2p - 360.0) / 2.0

    t = (
        1.0
        - 0.17 * math.cos(math.radians(h_bar - 30.0))
        + 0.24 * math.cos(math.radians(2.0 * h_bar))
        + 0.32 * math.cos(math.radians(3.0 * h_bar + 6.0))
        - 0.20 * math.cos(math.radians(4.0 * h_bar - 63.0))
    )

    c_bar_p7 = c_bar**7
    rc = 2.0 * math.sqrt(c_bar_p7 / (c_bar_p7 + 25.0**7))
    sl = 1.0 + (0.015 * (l_bar - 50.0) ** 2) / math.sqrt(20.0 + (l_bar - 50.0) ** 2)
    sc = 1.0 + 0.045 * c_bar
    sh = 1.0 + 0.015 * c_bar * t
    rt = -math.sin(math.radians(2.0 * (30.0 * math.exp(-(((h_bar - 275.0) / 25.0) ** 2))))) * rc

    dl_term = dlp / sl
    dc_term = dcp / sc
    dh_term = dhp_big / sh
    return math.sqrt(dl_term**2 + dc_term**2 + dh_term**2 + rt * dc_term * dh_term)


def perceptual_color_distance(color1: str | None, color2: str | None) -> float | None:
    """Perceptual distance between two hex colours, or None if either is unusable.

    Returns a CIEDE2000 delta-E: ~1.0 is the threshold of a just-noticeable
    difference, so the numbers are far smaller than the RGB distances they
    replaced and cannot be compared against an RGB threshold.
    """
    if not color1 or not color2:
        return None
    lab1 = _hex_to_lab(color1)
    lab2 = _hex_to_lab(color2)
    if lab1 is None or lab2 is None:
        return None
    return _ciede2000(lab1, lab2)
