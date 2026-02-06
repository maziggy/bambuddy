// Bambu Lab filament hex color to name mapping (from bambu-color-names.csv)
const BAMBU_HEX_COLORS: Record<string, string> = {
  '000000': 'Black', '001489': 'Blue', '002e96': 'Blue', '0047bb': 'Blue', '00482b': 'Pine Green',
  '004ea8': 'Blue', '0056b8': 'Cobalt Blue', '0069b1': 'Lake Blue', '0072ce': 'Blue', '0078bf': 'Marine Blue',
  '0085ad': 'Light Blue', '0086d6': 'Cyan', '008bda': 'Blue', '009639': 'Green', '009bd8': 'Cyan',
  '009fa1': 'Teal', '00a6a0': 'Green', '00ae42': 'Bambu Green', '00b1b7': 'Turquoise', '00bb31': 'Green',
  '018814': 'Candy Green', '042f56': 'Dark Blue', '0a2989': 'Blue', '0a2ca5': 'Blue', '0c2340': 'Navy Blue',
  '0c3b95': 'Blue', '101820': 'Black', '147bd1': 'Blue', '164b35': 'Green', '16b08e': 'Malachite Green',
  '1d7c6a': 'Oxide Green Metallic', '1f79e5': 'Lake Blue', '2140b4': 'Blue', '25282a': 'Black', '2842ad': 'Royal Blue',
  '2d2b28': 'Onyx Black Sparkle', '324585': 'Indigo Blue', '353533': 'Gray', '39541a': 'Forest Green',
  '39699e': 'Cobalt Blue Metallic', '3b665e': 'Green', '3f5443': 'Alpine Green Sparkle', '3f8e43': 'Mistletoe Green',
  '424379': 'Nebulae', '43403d': 'Iron Gray Metallic', '482960': 'Indigo Purple', '483d8b': 'Royal Purple Sparkle',
  '489fdf': 'Azure', '4c241c': 'Rosewood', '4ce4a0': 'Green', '4d3324': 'Dark Chocolate', '4d5054': 'Lava Gray',
  '4dafda': 'Cyan', '4f3f24': 'Black Walnut', '515151': 'Dark Gray', '515a6c': 'Gray', '545454': 'Dark Gray',
  '565656': 'Titan Gray', '56b7e6': 'Sky Blue', '583061': 'Violet Purple', '5898dd': 'Blue', '594177': 'Purple',
  '5b492f': 'Brown', '5b6579': 'Blue Gray', '5c9748': 'Matcha Green', '5e43b7': 'Purple', '5e4b3c': 'Copper',
  '5f6367': 'Titan Gray', '61b0ff': 'Translucent Light Blue', '61bf36': 'Green', '61c680': 'Grass Green',
  '6667ab': 'Lavender Blue', '684a43': 'Brown', '686865': 'Black', '68724d': 'Dark Green', '688197': 'Blue Gray',
  '69398e': 'Iris Purple', '6e88bc': 'Jeans Blue', '6ee53c': 'Lime Green', '6f5034': 'Cocoa Brown', '7248bd': 'Lavender',
  '748c45': 'Translucent Olive', '757575': 'Nardo Gray', '75aed8': 'Blue', '77edd7': 'Translucent Teal', '789d4a': 'Olive',
  '792b36': 'Crimson Red Sparkle', '7ac0e9': 'Glow Blue', '7ae1bf': 'Mint', '7cd82b': 'Lime Green', '7d6556': 'Dark Brown',
  '8344b0': 'Purple', '847d48': 'Bronze', '854ce4': 'Purple', '8671cb': 'Purple', '875718': 'Peanut Brown',
  '87909a': 'Silver', '898d8d': 'Gray', '8a949e': 'Gray', '8e8e8e': 'Translucent Gray', '8e9089': 'Gray',
  '90ff1a': 'Neon Green', '918669': 'Classic Birch', '939393': 'Gray', '950051': 'Plum', '951e23': 'Burgundy Red',
  '959698': 'Silver', '96d8af': 'Light Jade', '96dcb9': 'Mint', '995f11': 'Clay Brown', '999d9d': 'Gray',
  '9b9ea0': 'Ash Gray', '9d2235': 'Maroon Red', '9d432c': 'Brown', '9e007e': 'Purple', '9ea2a2': 'Gray',
  '9f332a': 'Brick Red', 'a1ffac': 'Glow Green', 'a3d8e1': 'Ice Blue', 'a6a9aa': 'Silver', 'a8a8aa': 'Gray',
  'a8c6ee': 'Baby Blue', 'aa6443': 'Copper Brown Metallic', 'ad4e38': 'Red Granite', 'adb1b2': 'Gray',
  'ae835b': 'Caramel', 'ae96d4': 'Lilac Purple', 'af1685': 'Purple', 'afb1ae': 'Gray', 'b15533': 'Terracotta',
  'b28b33': 'Gold', 'b39b84': 'Iridium Gold Metallic', 'b50011': 'Red', 'b8acd6': 'Lavender', 'b8cde9': 'Ice Blue',
  'ba9594': 'Rose Gold', 'bb3d43': 'Dark Red', 'bc0900': 'Red', 'becf00': 'Bright Green', 'c0df16': 'Green',
  'c12e1f': 'Red', 'c2e189': 'Apple Green', 'c3e2d6': 'Light Cyan', 'c5ed48': 'Lime', 'c6001a': 'Red',
  'c6c6c6': 'Gray', 'c8102e': 'Red', 'c8c8c8': 'Silver', 'c98935': 'Ochre Yellow', 'c9a381': 'Translucent Brown',
  'cbc6b8': 'Bone White', 'cdceca': 'Gray', 'cea629': 'Classic Gold Sparkle', 'd02727': 'Candy Red',
  'd1d3d5': 'Light Gray', 'd32941': 'Red', 'd3b7a7': 'Latte Brown', 'd6001c': 'Red', 'd6abff': 'Translucent Purple',
  'd6cca3': 'White Oak', 'dc3a27': 'Orange', 'dd3c22': 'Vermilion Red', 'de4343': 'Scarlet Red', 'dfd1a7': 'Beige',
  'e02928': 'Red', 'e4bd68': 'Gold', 'e5b03d': 'Gold', 'e83100': 'Red', 'e8afcf': 'Sakura Pink', 'e8dbb7': 'Desert Tan',
  'eaeae4': 'White', 'eaeceb': 'Silver', 'ec008c': 'Magenta', 'ed0000': 'Red', 'eeb1c1': 'Pink', 'efe255': 'Yellow',
  'f0f1a8': 'Clear', 'f17b8f': 'Glow Pink', 'f3cfb2': 'Champagne', 'f3e600': 'Yellow', 'f48438': 'Orange',
  'f4a925': 'Gold', 'f4d53f': 'Yellow', 'f4ee2a': 'Yellow', 'f5547c': 'Hot Pink', 'f55a74': 'Pink',
  'f5b6cd': 'Cherry Pink', 'f5dbab': 'Mellow Yellow', 'f5f1dd': 'White', 'f68b1b': 'Neon Orange', 'f74e02': 'Orange',
  'f75403': 'Orange', 'f7ada6': 'Pink', 'f7d959': 'Lemon Yellow', 'f7e6de': 'Beige', 'f7f3f0': 'White Marble',
  'f8ff80': 'Glow Yellow', 'f99963': 'Mandarin Orange', 'f9c1bd': 'Translucent Pink', 'f9dfb9': 'Cream',
  'f9ef41': 'Yellow', 'f9f7f2': 'Nature', 'f9f7f4': 'White', 'fce300': 'Yellow', 'fce900': 'Yellow',
  'fec600': 'Sunflower Yellow', 'fedb00': 'Yellow', 'ff4800': 'Orange', 'ff671f': 'Orange', 'ff6a13': 'Orange',
  'ff7f41': 'Orange', 'ff9016': 'Pumpkin Orange', 'ff911a': 'Translucent Orange', 'ff9d5b': 'Glow Orange',
  'ffb549': 'Sunflower Yellow', 'ffc72c': 'Tangerine Yellow', 'ffce00': 'Yellow', 'ffd00b': 'Yellow',
  'ffe133': 'Yellow', 'fffaf2': 'White', 'ffffff': 'White',
};

/**
 * Convert hex color to basic color name using HSL analysis.
 * Used as fallback when hex is not in Bambu database.
 */
export function hexToColorName(hex: string | null | undefined): string {
  if (!hex || hex.length < 6) return 'Unknown';
  const cleanHex = hex.replace('#', '');
  const r = parseInt(cleanHex.substring(0, 2), 16);
  const g = parseInt(cleanHex.substring(2, 4), 16);
  const b = parseInt(cleanHex.substring(4, 6), 16);

  const max = Math.max(r, g, b) / 255;
  const min = Math.min(r, g, b) / 255;
  const l = (max + min) / 2;

  let h = 0;
  let s = 0;

  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    const rNorm = r / 255, gNorm = g / 255, bNorm = b / 255;
    if (max === rNorm) h = ((gNorm - bNorm) / d + (gNorm < bNorm ? 6 : 0)) / 6;
    else if (max === gNorm) h = ((bNorm - rNorm) / d + 2) / 6;
    else h = ((rNorm - gNorm) / d + 4) / 6;
  }
  h = h * 360;

  if (l < 0.15) return 'Black';
  if (l > 0.85) return 'White';
  if (s < 0.15) {
    if (l < 0.4) return 'Dark Gray';
    if (l > 0.6) return 'Light Gray';
    return 'Gray';
  }
  if (h < 15 || h >= 345) return 'Red';
  if (h < 45) return 'Orange';
  if (h < 70) return 'Yellow';
  if (h < 150) return 'Green';
  if (h < 200) return 'Cyan';
  if (h < 260) return 'Blue';
  if (h < 290) return 'Purple';
  return 'Pink';
}

/**
 * Get color name from hex color.
 * First tries Bambu Lab color database lookup, then falls back to HSL-based name.
 */
export function getColorName(hexColor: string): string {
  const hex = hexColor.replace('#', '').toLowerCase().substring(0, 6);
  if (BAMBU_HEX_COLORS[hex]) {
    return BAMBU_HEX_COLORS[hex];
  }
  return hexToColorName(hexColor);
}
