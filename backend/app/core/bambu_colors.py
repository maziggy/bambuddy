"""Bambu Lab filament color code to color name mapping.

Source: https://github.com/queengooborg/Bambu-Lab-RFID-Library

Maps tray_id_name codes (e.g. "A06-D0") to human-readable color names (e.g. "Titan Gray").
"""

# Full color code → name mapping by material prefix
BAMBU_FILAMENT_COLORS: dict[str, str] = {
    # PLA Basic (A00)
    "A00-W1": "Jade White",
    "A00-P0": "Beige",
    "A00-D2": "Light Gray",
    "A00-Y0": "Yellow",
    "A00-Y2": "Sunflower Yellow",
    "A00-A1": "Pumpkin Orange",
    "A00-A0": "Orange",
    "A00-Y4": "Gold",
    "A00-G3": "Bright Green",
    "A00-G1": "Bambu Green",
    "A00-G2": "Mistletoe Green",
    "A00-R3": "Hot Pink",
    "A00-P6": "Magenta",
    "A00-R0": "Red",
    "A00-R2": "Maroon Red",
    "A00-P5": "Purple",
    "A00-P2": "Indigo Purple",
    "A00-B5": "Turquoise",
    "A00-B8": "Cyan",
    "A00-B3": "Cobalt Blue",
    "A00-N0": "Brown",
    "A00-N1": "Cocoa Brown",
    "A00-Y3": "Bronze",
    "A00-D0": "Gray",
    "A00-D1": "Silver",
    "A00-B1": "Blue Grey",
    "A00-D3": "Dark Gray",
    "A00-K0": "Black",
    # PLA Basic Gradient (A00-M*)
    "A00-M3": "Pink Citrus",
    "A00-M6": "Dusk Glare",
    "A00-M0": "Arctic Whisper",
    "A00-M1": "Solar Breeze",
    "A00-M5": "Blueberry Bubblegum",
    "A00-M4": "Mint Lime",
    "A00-M2": "Ocean to Meadow",
    "A00-M7": "Cotton Candy Cloud",
    # PLA Lite (A18)
    "A18-K0": "Black",
    "A18-D0": "Gray",
    "A18-W0": "White",
    "A18-R0": "Red",
    "A18-Y0": "Yellow",
    "A18-B0": "Cyan",
    "A18-B1": "Blue",
    "A18-P0": "Matte Beige",
    # PLA Matte (A01)
    "A01-W2": "Ivory White",
    "A01-W3": "Bone White",
    "A01-Y2": "Lemon Yellow",
    "A01-A2": "Mandarin Orange",
    "A01-P3": "Sakura Pink",
    "A01-P4": "Lilac Purple",
    "A01-R3": "Plum",
    "A01-R1": "Scarlet Red",
    "A01-R4": "Dark Red",
    "A01-G0": "Apple Green",
    "A01-G1": "Grass Green",
    "A01-G7": "Dark Green",
    "A01-B4": "Ice Blue",
    "A01-B0": "Sky Blue",
    "A01-B3": "Marine Blue",
    "A01-B6": "Dark Blue",
    "A01-Y3": "Desert Tan",
    "A01-N1": "Latte Brown",
    "A01-N3": "Caramel",
    "A01-R2": "Terracotta",
    "A01-N2": "Dark Brown",
    "A01-N0": "Dark Chocolate",
    "A01-D3": "Ash Gray",
    "A01-D0": "Nardo Gray",
    "A01-K1": "Charcoal",
    # PLA Glow (A12)
    "A12-G0": "Green",
    "A12-R0": "Pink",
    "A12-A0": "Orange",
    "A12-Y0": "Yellow",
    "A12-B0": "Blue",
    # PLA Marble (A07)
    "A07-R5": "Red Granite",
    "A07-D4": "White Marble",
    # PLA Aero (A11)
    "A11-W0": "White",
    "A11-K0": "Black",
    # PLA Sparkle (A08)
    "A08-G3": "Alpine Green Sparkle",
    "A08-D5": "Slate Gray Sparkle",
    "A08-B7": "Royal Purple Sparkle",
    "A08-R2": "Crimson Red Sparkle",
    "A08-K2": "Onyx Black Sparkle",
    "A08-Y1": "Classic Gold Sparkle",
    # PLA Metal (A02)
    "A02-B2": "Cobalt Blue Metallic",
    "A02-G2": "Oxide Green Metallic",
    "A02-Y1": "Iridium Gold Metallic",
    "A02-D2": "Iron Gray Metallic",
    # PLA Translucent (A17)
    "A17-B1": "Blue",
    "A17-A0": "Orange",
    "A17-P0": "Purple",
    # PLA Silk+ (A06)
    "A06-Y1": "Gold",
    "A06-D0": "Titan Gray",
    "A06-D1": "Silver",
    "A06-W0": "White",
    "A06-R0": "Candy Red",
    "A06-G0": "Candy Green",
    "A06-G1": "Mint",
    "A06-B1": "Blue",
    "A06-B0": "Baby Blue",
    "A06-P0": "Purple",
    "A06-R1": "Rose Gold",
    "A06-R2": "Pink",
    "A06-Y0": "Champagne",
    # PLA Silk Multi-Color (A05)
    "A05-M8": "Dawn Radiance",
    "A05-M4": "Aurora Purple",
    "A05-M1": "South Beach",
    "A05-T3": "Neon City",
    "A05-T2": "Midnight Blaze",
    "A05-T1": "Gilded Rose",
    "A05-T4": "Blue Hawaii",
    "A05-T5": "Velvet Eclipse",
    # PLA Galaxy (A15)
    "A15-B0": "Purple",
    "A15-G0": "Green",
    "A15-G1": "Nebulae",
    "A15-R0": "Brown",
    # PLA Wood (A16)
    "A16-K0": "Black Walnut",
    "A16-R0": "Rosewood",
    "A16-N0": "Clay Brown",
    "A16-G0": "Classic Birch",
    "A16-W0": "White Oak",
    "A16-Y0": "Ochre Yellow",
    # PLA-CF (A50)
    "A50-D6": "Lava Gray",
    "A50-K0": "Black",
    "A50-B6": "Royal Blue",
    # PLA Tough+ (A10)
    "A10-W0": "White",
    "A10-D0": "Gray",
    # PLA Tough (A09)
    "A09-B5": "Lavender Blue",
    "A09-B4": "Light Blue",
    "A09-A0": "Orange",
    "A09-D1": "Silver",
    "A09-R3": "Vermilion Red",
    "A09-Y0": "Yellow",
    # PETG HF (G02)
    "G02-K0": "Black",
    "G02-W0": "White",
    "G02-R0": "Red",
    "G02-D0": "Gray",
    "G02-D1": "Dark Gray",
    "G02-Y1": "Cream",
    "G02-Y0": "Yellow",
    "G02-A0": "Orange",
    "G02-N1": "Peanut Brown",
    "G02-G1": "Lime Green",
    "G02-G0": "Green",
    "G02-G2": "Forest Green",
    "G02-B1": "Lake Blue",
    "G02-B0": "Blue",
    # PETG Translucent (G01)
    "G01-G1": "Translucent Teal",
    "G01-B0": "Translucent Light Blue",
    "G01-C0": "Clear",
    "G01-D0": "Translucent Gray",
    "G01-G0": "Translucent Olive",
    "G01-N0": "Translucent Brown",
    "G01-A0": "Translucent Orange",
    "G01-P1": "Translucent Pink",
    "G01-P0": "Translucent Purple",
    # PETG-CF (G50)
    "G50-P7": "Violet Purple",
    "G50-K0": "Black",
    # ABS (B00)
    "B00-D1": "Silver",
    "B00-K0": "Black",
    "B00-W0": "White",
    "B00-G6": "Bambu Green",
    "B00-G7": "Olive",
    "B00-Y1": "Tangerine Yellow",
    "B00-A0": "Orange",
    "B00-R0": "Red",
    "B00-B4": "Azure",
    "B00-B0": "Blue",
    "B00-B6": "Navy Blue",
    # ABS-GF (B50)
    "B50-A0": "Orange",
    "B50-K0": "Black",
    # ASA (B01)
    "B01-W0": "White",
    "B01-K0": "Black",
    "B01-D0": "Gray",
    # ASA Aero (B02)
    "B02-W0": "White",
    # PC (C00)
    "C00-C1": "Transparent",
    "C00-C0": "Clear Black",
    "C00-K0": "Black",
    "C00-W0": "White",
    # PC FR (C01)
    "C01-K0": "Black",
    # TPU for AMS (U02)
    "U02-B0": "Blue",
    "U02-D0": "Gray",
    "U02-K0": "Black",
    # PAHT-CF (N04)
    "N04-K0": "Black",
    # PA6-GF (N08)
    "N08-K0": "Black",
    # Support for PLA/PETG (S02, S05)
    "S02-W0": "Nature",
    "S02-W1": "White",
    "S05-C0": "Black",
    # Support for ABS (S06)
    "S06-W0": "White",
    # Support for PA/PET (S03)
    "S03-G1": "Green",
    # PVA (S04)
    "S04-Y0": "Clear",
}

# Fallback: color code suffix → name (for unknown material prefixes)
BAMBU_COLOR_CODE_FALLBACK: dict[str, str] = {
    "W0": "White",
    "W1": "Jade White",
    "W2": "Ivory White",
    "W3": "Bone White",
    "Y0": "Yellow",
    "Y1": "Gold",
    "Y2": "Sunflower Yellow",
    "Y3": "Bronze",
    "Y4": "Gold",
    "A0": "Orange",
    "A1": "Pumpkin Orange",
    "A2": "Mandarin Orange",
    "R0": "Red",
    "R1": "Scarlet Red",
    "R2": "Maroon Red",
    "R3": "Hot Pink",
    "R4": "Dark Red",
    "R5": "Red Granite",
    "P0": "Beige",
    "P1": "Pink",
    "P2": "Indigo Purple",
    "P3": "Sakura Pink",
    "P4": "Lilac Purple",
    "P5": "Purple",
    "P6": "Magenta",
    "P7": "Violet Purple",
    "B0": "Blue",
    "B1": "Blue Grey",
    "B2": "Cobalt Blue",
    "B3": "Cobalt Blue",
    "B4": "Ice Blue",
    "B5": "Turquoise",
    "B6": "Navy Blue",
    "B7": "Royal Purple",
    "B8": "Cyan",
    "G0": "Green",
    "G1": "Grass Green",
    "G2": "Mistletoe Green",
    "G3": "Bright Green",
    "G6": "Bambu Green",
    "G7": "Dark Green",
    "N0": "Brown",
    "N1": "Peanut Brown",
    "N2": "Dark Brown",
    "N3": "Caramel",
    "D0": "Gray",
    "D1": "Silver",
    "D2": "Light Gray",
    "D3": "Dark Gray",
    "D4": "White Marble",
    "D5": "Slate Gray",
    "D6": "Lava Gray",
    "K0": "Black",
    "K1": "Charcoal",
    "K2": "Onyx Black",
    "C0": "Clear Black",
    "C1": "Transparent",
}


def resolve_bambu_color_name(tray_id_name: str) -> str | None:
    """Resolve a Bambu Lab tray_id_name code to a human-readable color name.

    Tries exact match first, then falls back to color code suffix lookup.
    Returns None if the code cannot be resolved.
    """
    if not tray_id_name:
        return None

    # Exact match
    name = BAMBU_FILAMENT_COLORS.get(tray_id_name)
    if name:
        return name

    # Fallback: use color code suffix (e.g. "D0" from "A06-D0")
    parts = tray_id_name.split("-")
    if len(parts) >= 2:
        return BAMBU_COLOR_CODE_FALLBACK.get(parts[1])

    return None
