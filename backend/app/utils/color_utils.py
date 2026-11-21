"""Color comparison utilities for RFID/firmware color matching."""

import math
import re

# Alpha byte that means "fully opaque". Bambu's firmware reports every opaque
# spool as RRGGBBFF, so this is the overwhelmingly common value.
_OPAQUE_ALPHA = "FF"


def spoolman_color_hex(rgba: str | None) -> str | None:
    """Normalise an RRGGBB(AA) value to what Spoolman's ``color_hex`` should hold.

    Eight characters only when the spool is genuinely translucent. Bambuddy used
    to truncate to six unconditionally, which turned a clear spool's ``00000000``
    into opaque black (#2912); passing everything through instead would rewrite
    the ``color_hex`` of every opaque spool on its next touch, churning records in
    people's Spoolman for no benefit. Keeping the opaque case at six characters
    leaves existing data byte-identical.

    Returns ``None`` for a missing value. A value shorter than six characters is
    passed through unchanged so a malformed colour is not reshaped into something
    that looks valid; a value between six and eight is truncated to six, which is
    what the pre-#2912 behaviour did and what the six-character path still means.
    Neither is reachable through ``_validate_rgba``, which admits only 6 or 8.
    """
    if not rgba:
        return None
    clean = rgba.strip().removeprefix("#").upper()
    if len(clean) < 6:
        return clean or None
    if len(clean) >= 8 and clean[6:8] != _OPAQUE_ALPHA:
        return clean[:8]
    return clean[:6]


def color_match_key(color_hex: str | None) -> str:
    """Return the key two colours are compared on: **the shape they would be stored as**.

    Deliberately the same rule as :func:`spoolman_color_hex`, so two colours match
    exactly when storing them would produce the same value. That settles both
    directions of the alpha question at once (#2912):

    ==============  ============  ==================================================
    value           key           consequence
    ==============  ============  ==================================================
    ``000000``      ``000000``    existing six-character data
    ``000000FF``    ``000000``    still matches it — the upgrade guard, without which
                                  the next AMS sync mints a duplicate filament for
                                  every spool on the instance
    ``00000000``    ``00000000``  a clear spool gets its own filament and is never
                                  conflated with the black one, in either direction
    ==============  ============  ==================================================

    Returns ``""`` rather than ``None`` for a missing value so callers can compare
    without guarding, which is the only reason this is not simply an alias.
    """
    return spoolman_color_hex(color_hex) or ""


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


# --- Colour families (#3641) -------------------------------------------------
#
# A port of ``colorFamily`` / ``hexToHsl`` in ``frontend/src/utils/colors.ts``, the
# rules the Inventory page sorts and names spools by, so a notification calls a
# spool the same colour the page does. ``frontend/src/__tests__/utils/
# colorFamilyVectors.json`` holds hex -> family pairs that both sides test against;
# change a rule in one place and the other test fails until it is changed too.

_HEX_PREFIX = re.compile(r"[+-]?[0-9a-fA-F]+")


def _parse_int16(chunk: str) -> int | None:
    """``parseInt(chunk, 16)``: leading whitespace skipped, then an optional sign and the leading hex digits.

    None (NaN) when there are none, which includes a bare "0x" prefix: JS drops
    the prefix and finds nothing after it, where "0" alone would read as 0.
    """
    chunk = chunk.lstrip()
    unsigned = chunk.lstrip("+-") if chunk[:1] in ("+", "-") else chunk
    if unsigned[:2] in ("0x", "0X"):
        match = _HEX_PREFIX.match(unsigned[2:])
        if not match:
            return None
        value = int(match.group(), 16)
        return -value if chunk[0] == "-" else value
    match = _HEX_PREFIX.match(chunk)
    return int(match.group(), 16) if match else None


def _hex_to_hsl(hex_value: str | None) -> tuple[float, float, float] | None:
    """(hue in degrees, saturation, lightness) of an RRGGBB(AA) value, or None if unparseable."""
    if not hex_value or len(hex_value) < 6:
        return None
    clean = hex_value.replace("#", "", 1)
    r = _parse_int16(clean[0:2])
    g = _parse_int16(clean[2:4])
    b = _parse_int16(clean[4:6])
    if r is None or g is None or b is None:
        return None
    r_n, g_n, b_n = r / 255, g / 255, b / 255
    high = max(r_n, g_n, b_n)
    low = min(r_n, g_n, b_n)
    lightness = (high + low) / 2
    hue = 0.0
    saturation = 0.0
    if high != low:
        span = high - low
        saturation = span / (2 - high - low) if lightness > 0.5 else span / (high + low)
        if high == r_n:
            hue = ((g_n - b_n) / span + (6 if g_n < b_n else 0)) / 6
        elif high == g_n:
            hue = ((b_n - r_n) / span + 2) / 6
        else:
            hue = ((r_n - g_n) / span + 4) / 6
    return hue * 360, saturation, lightness


def _alpha_byte(hex_value: str) -> str | None:
    """The two alpha characters of an eight-character value (leading ``#`` allowed), else None."""
    clean = hex_value.replace("#", "", 1)
    return clean[6:8].upper() if len(clean) == 8 else None


def color_family(hex_value: str | None) -> str | None:
    """Name the colour family of an RRGGBB(AA) value, or None when it can't be read.

    The same families, in the same words, as ``colorFamily`` in
    ``frontend/src/utils/colors.ts``.
    """
    if not hex_value or len(hex_value) < 6:
        return None
    # Alpha 00 is fully transparent: Bambu's clear code 00000000 must not come out as black (#1545).
    if _alpha_byte(hex_value) == "00":
        return "Clear"
    # JS strips "#" here and again inside hexToHsl, so "##AABBCC" still reads as AABBCC.
    hsl = _hex_to_hsl(hex_value.replace("#", "", 1))
    if hsl is None:
        return None
    h, s, lightness = hsl
    if lightness < 0.15:
        return "Black"
    if lightness > 0.85:
        return "White"
    if s < 0.15:
        if lightness < 0.4:
            return "Dark Gray"
        if lightness > 0.6:
            return "Light Gray"
        return "Gray"
    # Brown is an orange or yellow hue at a lower lightness.
    if 15 <= h < 45 and lightness < 0.45:
        return "Brown"
    if 45 <= h < 70 and lightness < 0.40:
        return "Brown"
    if h < 15 or h >= 345:
        return "Red"
    if h < 45:
        return "Orange"
    if h < 70:
        return "Yellow"
    if h < 150:
        return "Green"
    if h < 200:
        return "Cyan"
    if h < 260:
        return "Blue"
    if h < 290:
        return "Purple"
    return "Pink"


_FAMILY_EMOJI = {
    "Red": "\U0001f534",  # red circle
    "Orange": "\U0001f7e0",  # orange circle
    "Yellow": "\U0001f7e1",  # yellow circle
    "Green": "\U0001f7e2",  # green circle
    "Cyan": "\U0001f535",  # blue circle
    "Blue": "\U0001f535",  # blue circle
    "Purple": "\U0001f7e3",  # purple circle
    "Pink": "\U0001fa77",  # pink heart: Unicode has no pink circle
    "Brown": "\U0001f7e4",  # brown circle
    "White": "⚪",  # white circle
    "Light Gray": "⚪",
    "Dark Gray": "⚫",  # black circle
    "Black": "⚫",
    "Clear": "⚪",
}
_MULTICOLOR_EMOJI = "\U0001f308"  # rainbow


def color_emoji(hex_value: str | None, extra_colors: str | None = None) -> str:
    """The coloured dot for a spool colour, or "" when there is nothing to show.

    Several colours (``extra_colors`` holds the other stops of a gradient or
    multi-colour spool) get a rainbow. Clear is a white dot; a translucent colour
    is shown by its RGB. An unreadable value gets no dot rather than a wrong one.
    """
    if extra_colors and extra_colors.replace(",", "").strip():
        return _MULTICOLOR_EMOJI
    family = color_family(hex_value)
    if family is None:
        return ""
    if family == "Gray":
        hsl = _hex_to_hsl(hex_value)
        return "⚪" if hsl is not None and hsl[2] >= 0.5 else "⚫"
    return _FAMILY_EMOJI[family]


def color_note(hex_value: str | None) -> str:
    """ "clear" for a fully transparent colour, "translucent" for a partly transparent one, else ""."""
    if not hex_value or len(hex_value) < 6:
        return ""
    alpha = _alpha_byte(hex_value)
    if alpha is None or alpha == "FF":
        return ""
    if alpha == "00":
        return "clear"
    return "translucent" if _parse_int16(alpha) is not None else ""
