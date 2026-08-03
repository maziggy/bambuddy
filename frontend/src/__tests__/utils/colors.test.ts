import { describe, it, expect, beforeEach } from 'vitest';
import {
  colorFamily,
  colorSortKey,
  hexToColorName,
  getColorName,
  resolveSpoolColorName,
  setColorCatalog,
  __resetColorCatalogForTests,
} from '../../utils/colors';

describe('hexToColorName', () => {
  it('returns "Unknown" for null/empty input', () => {
    expect(hexToColorName(null)).toBe('Unknown');
    expect(hexToColorName('')).toBe('Unknown');
    expect(hexToColorName(undefined)).toBe('Unknown');
  });

  it('classifies dark low-saturation colors as Dark Gray', () => {
    // Titan Gray hex (5F6367) — low saturation, lightness < 0.4
    expect(hexToColorName('5F6367')).toBe('Dark Gray');
  });

  it('classifies black hex as Black', () => {
    expect(hexToColorName('000000')).toBe('Black');
  });

  it('classifies white hex as White', () => {
    expect(hexToColorName('FFFFFF')).toBe('White');
  });

  // #1545: transparent filament is reported as `00000000` (alpha=00).
  // Without the alpha-aware short-circuit it would fall through to the HSL
  // bucketing and resolve to "Black" because the RGB happens to be 000000.
  it('classifies any alpha=00 rgba as Clear', () => {
    expect(hexToColorName('00000000')).toBe('Clear');
    expect(hexToColorName('FF000000')).toBe('Clear');
    expect(hexToColorName('#abcdef00')).toBe('Clear');
  });

  it('still classifies fully opaque colors via HSL even when alpha is FF', () => {
    expect(hexToColorName('000000FF')).toBe('Black');
    expect(hexToColorName('FFFFFFFF')).toBe('White');
  });
});

describe('getColorName', () => {
  beforeEach(() => {
    __resetColorCatalogForTests();
  });

  it('looks up the runtime color catalog before HSL fallback', () => {
    setColorCatalog({ '5f6367': 'Titan Gray' });
    expect(getColorName('5f6367')).toBe('Titan Gray');
    expect(getColorName('5F6367')).toBe('Titan Gray');
  });

  it('falls back to HSL when hex is not in the runtime catalog', () => {
    // No catalog entry for 123456; HSL bucketing puts it in Blue.
    expect(getColorName('123456')).toBe('Blue');
  });

  it('returns "Unknown" for empty string', () => {
    expect(getColorName('')).toBe('Unknown');
  });

  it('handles hex with # prefix', () => {
    setColorCatalog({ '5f6367': 'Titan Gray' });
    expect(getColorName('#5f6367')).toBe('Titan Gray');
  });

  it('normalizes catalog keys (strips # and lowercases)', () => {
    // Provider can pass keys in any case / with or without '#'; the utility
    // must normalize so lookups succeed regardless of input shape.
    setColorCatalog({ '#F5B6CD': 'Cherry Pink' });
    expect(getColorName('F5B6CD')).toBe('Cherry Pink');
    expect(getColorName('f5b6cd')).toBe('Cherry Pink');
  });

  it('resolves #857 regression — A17-R1 / F5B6CD is Cherry Pink, not Scarlet Red', () => {
    setColorCatalog({ 'f5b6cd': 'Cherry Pink' });
    expect(getColorName('F5B6CDFF')).toBe('Cherry Pink');
  });

  // #1545: alpha=00 must short-circuit catalog lookup too — otherwise a
  // catalog entry on the underlying RGB would mislabel transparent filament.
  it('returns Clear for transparent rgba regardless of catalog entry', () => {
    setColorCatalog({ '000000': 'Inky Night' });
    expect(getColorName('00000000')).toBe('Clear');
    expect(getColorName('000000FF')).toBe('Inky Night');
  });
});

describe('resolveSpoolColorName', () => {
  beforeEach(() => {
    __resetColorCatalogForTests();
    setColorCatalog({ '5f6367': 'Titan Gray' });
  });

  it('returns readable color name directly', () => {
    expect(resolveSpoolColorName('Titan Gray', '5F6367FF')).toBe('Titan Gray');
  });

  it('looks up hex when color_name is a Bambu code', () => {
    expect(resolveSpoolColorName('A06-D0', '5F6367FF')).toBe('Titan Gray');
  });

  it('returns null when color_name is a code and hex is unknown', () => {
    // Opaque, not in catalog — must not be misread as transparent (#1545).
    expect(resolveSpoolColorName('A99-Z9', '123456FF')).toBeNull();
  });

  // #1545
  it('returns Clear for transparent rgba even when color_name is a code', () => {
    expect(resolveSpoolColorName('A99-Z9', '00000000')).toBe('Clear');
  });
});

describe('colorSortKey (#2729)', () => {
  // Sorting the swatch column. Ascending puts Red first and the neutrals last.
  const sortNames = (spools: { rgba: string | null; name: string }[]) =>
    [...spools]
      .sort((a, b) => colorSortKey(a.rgba).localeCompare(colorSortKey(b.rgba)))
      .map((s) => s.name);

  it('walks the rainbow, then browns, then neutrals light to dark', () => {
    expect(
      sortNames([
        { rgba: '000000FF', name: 'black' },
        { rgba: 'C8C8C8FF', name: 'silver' },
        { rgba: 'FFFFFFFF', name: 'white' },
        { rgba: '875718FF', name: 'brown' },
        { rgba: '8B00FFFF', name: 'purple' },
        { rgba: '6EE53CFF', name: 'green' },
        { rgba: 'FF0000FF', name: 'red' },
        { rgba: 'FF6A13FF', name: 'orange' },
        { rgba: '56B7E6FF', name: 'cyan' },
      ]),
    ).toEqual(['red', 'orange', 'green', 'cyan', 'purple', 'brown', 'white', 'silver', 'black']);
  });

  it('keeps near-neutral greys out of the colours', () => {
    // The regression the issue's own algorithm would ship. Measured on a real
    // 30-spool inventory: a straight hue sort put Titan Gray (hue 210, sat
    // 0.04) between Sky Blue and Purple, and 8B8889 (hue 340, sat 0.01)
    // between Purple and Burgundy Red. Both must land with the neutrals.
    expect(
      sortNames([
        { rgba: '56B7E6FF', name: 'sky blue' },
        { rgba: '5F6367FF', name: 'titan gray' },
        { rgba: '8B00FFFF', name: 'purple' },
        { rgba: '8B8889FF', name: 'unnamed grey' },
        { rgba: '951E23FF', name: 'burgundy red' },
      ]),
    ).toEqual(['burgundy red', 'sky blue', 'purple', 'unnamed grey', 'titan gray']);
  });

  it('groups browns together instead of splitting the oranges', () => {
    // Dark Chocolate is hue 22 and would otherwise sit between two oranges.
    expect(
      sortNames([
        { rgba: 'FF6A13FF', name: 'orange' },
        { rgba: '4D3324FF', name: 'dark chocolate' },
        { rgba: 'B39B84FF', name: 'iridium gold' },
      ]),
    ).toEqual(['orange', 'iridium gold', 'dark chocolate']);
  });

  it('sorts by hue within a family', () => {
    expect(
      sortNames([
        { rgba: '875718FF', name: 'peanut brown' },
        { rgba: 'B15533FF', name: 'terracotta' },
        { rgba: '4D3324FF', name: 'dark chocolate' },
      ]),
    ).toEqual(['terracotta', 'dark chocolate', 'peanut brown']);
  });

  it('orders neutrals light to dark rather than by their meaningless hue', () => {
    // 3A3A3A and 5F6367 are both Dark Gray; their hues (0 and 210) carry no
    // information, so lightness has to decide.
    expect(
      sortNames([
        { rgba: '3A3A3AFF', name: 'darker' },
        { rgba: '5F6367FF', name: 'lighter' },
      ]),
    ).toEqual(['lighter', 'darker']);
  });

  it('sorts spools with no recorded colour last', () => {
    expect(
      sortNames([
        { rgba: null, name: 'missing' },
        { rgba: '', name: 'empty' },
        { rgba: 'FF0000FF', name: 'red' },
        { rgba: '000000FF', name: 'black' },
      ]),
    ).toEqual(['red', 'black', 'missing', 'empty']);
  });

  it('files a fully transparent colour as Clear, after the neutrals', () => {
    expect(colorFamily('00000000')).toBe('Clear');
    expect(sortNames([
      { rgba: '00000000', name: 'clear' },
      { rgba: '000000FF', name: 'black' },
    ])).toEqual(['black', 'clear']);
  });

  it('produces equal keys for identical colours so sorting stays stable', () => {
    expect(colorSortKey('FFFFFFFF')).toBe(colorSortKey('ffffffff'));
  });
});

describe('colorFamily / hexToColorName agreement', () => {
  it('names a colour with the same family it sorts under', () => {
    // One classifier backs both, so the Color column can never sort a spool
    // into a family the Color Name column disagrees with.
    for (const hex of ['FF0000FF', '875718FF', '5F6367FF', 'FFFFFFFF', '00000000', 'zzz']) {
      expect(hexToColorName(hex)).toBe(colorFamily(hex) ?? 'Unknown');
    }
  });
});
