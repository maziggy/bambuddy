"""Heuristics that turn OCR'd filament label text (e.g. "SUNLU PLA+ 1.75mm
Black 1KG") into best-effort spool fields, plus a barcode extractor for text
that also contains a printed EAN/UPC/GTIN.

Everything here is a guess from free text — the review form the caller opens
always lets the user correct it.

Ported from the standalone `filament_to_bambuddy` companion app's
`filament_parse.py`.
"""

from __future__ import annotations

import re

# Common filament brands (longest first so multi-word names win).
KNOWN_BRANDS = [
    "Bambu Lab",
    "Polymaker",
    "Prusament",
    "Prusa",
    "Fillamentum",
    "MatterHackers",
    "Protopasta",
    "ColorFabb",
    "Overture",
    "Hatchbox",
    "Inland",
    "Creality",
    "Elegoo",
    "Anycubic",
    "Geeetech",
    "Eryone",
    "Amolen",
    "Duramic",
    "Sunlu",
    "eSUN",
    "Jayo",
    "Atomic",
    "Spectrum",
    "3DJake",
    "Comgrow",
    "Tinmorry",
    "Kingroon",
    "Flashforge",
    "Ziro",
    "Novamaker",
    "GST3D",
    "Iemai",
]

# Base materials -> canonical Bambuddy `material`. Order matters: the most
# specific / longest token must be tried first (PETG before PET, PLA+ before PLA).
BASE_MATERIALS = [
    ("PCTG", "PCTG"),
    ("PETG", "PETG"),
    ("PET-G", "PETG"),
    ("PET G", "PETG"),
    ("PLA+", "PLA"),
    ("PLA PLUS", "PLA"),
    ("PLA", "PLA"),
    ("ABS+", "ABS"),
    ("ABS", "ABS"),
    ("ASA", "ASA"),
    ("TPU", "TPU"),
    ("TPE", "TPE"),
    ("NYLON", "Nylon"),
    ("PA12", "Nylon"),
    ("PA6", "Nylon"),
    ("PA", "Nylon"),
    ("HIPS", "HIPS"),
    ("PVA", "PVA"),
    ("PC", "PC"),
]

# Subtype / finish modifiers (canonical casing on the right).
SUBTYPE_HINTS = [
    ("CARBON FIBER", "Carbon Fiber"),
    ("CARBON FIBRE", "Carbon Fiber"),
    ("CARBON", "Carbon Fiber"),
    ("GLOW IN THE DARK", "Glow"),
    ("GLOW", "Glow"),
    ("SILK", "Silk"),
    ("MATTE", "Matte"),
    ("MARBLE", "Marble"),
    ("WOOD", "Wood"),
    ("METAL", "Metal"),
    ("RAINBOW", "Rainbow"),
    ("GRADIENT", "Gradient"),
    ("DUAL COLOR", "Dual Color"),
    ("DUAL COLOUR", "Dual Color"),
    ("DUAL", "Dual Color"),
    ("TRI COLOR", "Tri Color"),
    ("TRI COLOUR", "Tri Color"),
    ("HIGH SPEED", "High Speed"),
    ("HYPER", "High Speed"),
    ("TOUGH", "Tough"),
    ("GALAXY", "Galaxy"),
    ("SPARKLE", "Sparkle"),
    ("GLITTER", "Glitter"),
    ("LUMINOUS", "Luminous"),
    ("FLUORESCENT", "Fluorescent"),
    ("TRANSLUCENT", "Translucent"),
    ("TRANSPARENT", "Transparent"),
    ("PLUS", "Plus"),
]

# Colour name -> RRGGBB (opaque; the review form lets the user tweak).
COLOR_HEX = {
    "Black": "000000",
    "White": "FFFFFF",
    "Gray": "808080",
    "Grey": "808080",
    "Silver": "C0C0C0",
    "Red": "FF0000",
    "Orange": "FF7F00",
    "Yellow": "FFFF00",
    "Green": "00A000",
    "Blue": "0050FF",
    "Navy": "001F5C",
    "Cyan": "00FFFF",
    "Teal": "008080",
    "Purple": "800080",
    "Violet": "7F00FF",
    "Pink": "FF69B4",
    "Magenta": "FF00FF",
    "Brown": "7B3F00",
    "Beige": "F5F5DC",
    "Gold": "D4AF37",
    "Bronze": "CD7F32",
    "Copper": "B87333",
    "Natural": "EDE6D6",
    "Clear": "EEEEEE",
    "Transparent": "EEEEEE",
    "Skin": "FFCDA0",
    "Olive": "808000",
    "Lime": "BFFF00",
    "Maroon": "800000",
    "Turquoise": "40E0D0",
    "Ivory": "FFFFF0",
    "Cream": "FFFDD0",
    "Tan": "D2B48C",
    "Khaki": "C3B091",
}
# Match longer colour names first (multi-word handling is future-proofed by
# sorting on length).
_COLOR_WORDS = sorted(COLOR_HEX.keys(), key=len, reverse=True)


def _find_brand(text_upper: str, extra_brands=None) -> str | None:
    """Find a brand in the text. Tries the built-in list plus any extra brands
    (e.g. the OFD brand list), longest name first so a specific brand wins.
    Substring match (not word-boundary) so it still catches OCR text where
    words run together (e.g. 'OfPanchroma')."""
    brands = list(KNOWN_BRANDS) + list(extra_brands or [])
    seen, uniq = set(), []
    for b in brands:
        b = (b or "").strip()
        if b and b.upper() not in seen:
            seen.add(b.upper())
            uniq.append(b)
    uniq.sort(key=len, reverse=True)
    # Prefer specific (>=4 char) matches; fall back to shorter exact ones.
    for b in uniq:
        if len(b) >= 4 and b.upper() in text_upper:
            return b
    for b in uniq:
        if b.upper() in text_upper:
            return b
    return None


def _find_material(text_upper: str) -> tuple[str | None, str | None]:
    """Return (material, subtype_from_plus). Detects PLA+ -> material PLA, subtype Plus.

    Uses letter boundaries so short tokens (PA, PC, PET...) don't false-match inside
    brand names — e.g. 'PA' must not fire on 'PAnchroma'.
    """
    for token, canonical in BASE_MATERIALS:
        if re.search(r"(?<![A-Z])" + re.escape(token) + r"(?![A-Z])", text_upper):
            sub = "Plus" if token.endswith("+") or token.endswith("PLUS") else None
            return canonical, sub
    return None, None


def _find_subtypes(text_upper: str) -> list[str]:
    found = []
    for token, canonical in SUBTYPE_HINTS:
        if token in text_upper and canonical not in found:
            found.append(canonical)
    return found


def _find_color(text: str) -> tuple[str | None, str | None]:
    # Dual colour first: "Grey-Orange", "Black/White" -> keep both names; rgba
    # from the first colour (a single swatch can't show two).
    cw = "|".join(re.escape(w) for w in _COLOR_WORDS)
    m = re.search(rf"\b({cw})\s*[-/]\s*({cw})\b", text, re.IGNORECASE)
    if m:
        c1, c2 = m.group(1).title(), m.group(2).title()
        rgba = COLOR_HEX.get(c1)
        return f"{c1}-{c2}", (rgba + "FF") if rgba else None
    for word in _COLOR_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE):
            return word, COLOR_HEX[word] + "FF"  # RRGGBBAA, opaque
    return None, None


def _find_diameter(text: str) -> float | None:
    m = re.search(r"\b(1\.75|2\.85|3\.00|3\.0|3)\s*mm\b", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\b(1\.75|2\.85)\b", text)
    return float(m.group(1)) if m else None


def _find_nozzle_temps(text: str) -> tuple[int, int] | None:
    """Pull the nozzle/printing temperature (deg C) out of label text.

    Distinguishes nozzle from bed by VALUE, not by adjacent label — OCR often
    scrambles multi-column spec blocks. Nozzle/hotend temps are >=140C; bed
    temps are well below that. Returns (min, max), or None.
    """
    for lo, hi in re.findall(r"(\d{2,3})\s*[-–~]\s*(\d{2,3})\s*°?\s*[cC]\b", text):
        lo, hi = int(lo), int(hi)
        if lo >= 140 and hi <= 360 and lo <= hi:
            return lo, hi
    # Single value fallback (e.g. "Nozzle 210C").
    for m in re.finditer(r"(\d{2,3})\s*°?\s*[cC]\b", text):
        t = int(m.group(1))
        if 140 <= t <= 360:
            return t, t
    # Bare range in the nozzle band — OCR often drops the "C" (turning it into
    # a quote/garbage). Constrain to a plausible printing-temp window + spread.
    for lo, hi in re.findall(r"(?<!\d)(\d{2,3})\s*[-–~]\s*(\d{2,3})(?!\d)", text):
        lo, hi = int(lo), int(hi)
        if 150 <= lo <= hi <= 320 and 5 <= hi - lo <= 120:
            return lo, hi
    return None


def _find_hex(text: str) -> str | None:
    """First #RRGGBB in the text -> RRGGBBAA (opaque). Labels often print it."""
    m = re.search(r"#([0-9A-Fa-f]{6})\b", text)
    return m.group(1).upper() + "FF" if m else None


def _find_weight_grams(text: str) -> int | None:
    """Net filament weight in grams from tokens like '1KG', '1000g', '0.5kg', '500 g'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kgs|kilograms?)\b", text, re.IGNORECASE)
    if m:
        return int(round(float(m.group(1)) * 1000))
    m = re.search(r"(\d{3,5})\s*(g|grams?)\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def parse_title(title: str, extra_brands=None) -> dict:
    """Best-effort parse of label/product text into Bambuddy spool fields.

    ``extra_brands`` augments the built-in brand list (e.g. the OFD brand
    list). Returns a dict with any of: brand, material, subtype, color_name,
    rgba, diameter_mm, label_weight, nozzle_temp_min, nozzle_temp_max.
    Missing fields are absent.
    """
    if not title:
        return {}
    text = title.strip()
    upper = text.upper()
    out: dict = {}

    brand = _find_brand(upper, extra_brands)
    if brand:
        out["brand"] = brand

    material, plus_sub = _find_material(upper)
    if material:
        out["material"] = material

    subtypes = _find_subtypes(upper)
    if plus_sub and plus_sub not in subtypes:
        subtypes.insert(0, plus_sub)
    if subtypes:
        out["subtype"] = " ".join(subtypes)

    color_name, rgba = _find_color(text)
    if color_name:
        out["color_name"] = color_name
        out["rgba"] = rgba
    # An explicit hex on the label is authoritative for the colour swatch.
    hex_rgba = _find_hex(text)
    if hex_rgba:
        out["rgba"] = hex_rgba

    temps = _find_nozzle_temps(text)
    if temps:
        out["nozzle_temp_min"], out["nozzle_temp_max"] = temps

    diameter = _find_diameter(text)
    if diameter:
        out["diameter_mm"] = diameter

    weight = _find_weight_grams(text)
    if weight:
        out["label_weight"] = weight

    return out


def extract_barcode(text: str) -> str | None:
    """Pull a barcode (UPC/EAN/GTIN) out of free text, e.g. label OCR.

    Prefers a labelled number ("EAN: 6938936716785"); falls back to any bare
    12-14 digit run. Returns digits only, or None.
    """
    m = re.search(r"(?:EAN|UPC|GTIN|BARCODE)\s*[:#]?\s*(\d[\d\s]{6,16}\d)", text, re.IGNORECASE)
    cand = re.sub(r"\D", "", m.group(1)) if m else None
    if not cand:
        m = re.search(r"(?<!\d)(\d{12,14})(?!\d)", text)
        cand = m.group(1) if m else None
    return cand if cand and 8 <= len(cand) <= 14 else None
