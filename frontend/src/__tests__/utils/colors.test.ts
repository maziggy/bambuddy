import { describe, it, expect, beforeEach } from 'vitest';
import {
  disambiguateColorNames,
  getSwatchStyle,
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

describe('getColorName with a material (#2875)', () => {
  beforeEach(() => {
    __resetColorCatalogForTests();
    // What the backend ships for a real Bambu catalog: the flat map can only
    // keep one name for #FFFFFF, and the qualified map carries the rest.
    setColorCatalog(
      { ffffff: 'Jade White', '000000': 'Black' },
      { 'pla matte|ffffff': 'Ivory White', 'pla matte|000000': 'Charcoal' },
    );
  });

  it('returns the material-specific name when the caller knows the material', () => {
    expect(getColorName('FFFFFFFF', 'PLA Matte')).toBe('Ivory White');
    expect(getColorName('000000FF', 'PLA Matte')).toBe('Charcoal');
  });

  it('still returns the flat name for a material with no entry of its own', () => {
    expect(getColorName('FFFFFFFF', 'PLA Basic')).toBe('Jade White');
    expect(getColorName('FFFFFFFF', 'Some Third Party Filament')).toBe('Jade White');
  });

  it('ignores case and padding in the material, as tray_sub_brands is not normalized', () => {
    expect(getColorName('FFFFFFFF', '  pla MATTE ')).toBe('Ivory White');
  });

  it('falls back to the flat name when no material is passed', () => {
    expect(getColorName('FFFFFFFF')).toBe('Jade White');
    expect(getColorName('FFFFFFFF', null)).toBe('Jade White');
    expect(getColorName('FFFFFFFF', '')).toBe('Jade White');
  });

  it('keeps Clear ahead of any catalog lookup for a transparent spool', () => {
    expect(getColorName('FFFFFF00', 'PLA Matte')).toBe('Clear');
  });

  it('falls back to HSL when neither map knows the hex', () => {
    expect(getColorName('5F6367', 'PLA Matte')).toBe('Dark Gray');
  });

  it('survives a catalog with no qualified map at all', () => {
    __resetColorCatalogForTests();
    setColorCatalog({ ffffff: 'Jade White' });
    expect(getColorName('FFFFFFFF', 'PLA Matte')).toBe('Jade White');
  });

  it('reads the hex from the last separator, so a material may contain one', () => {
    // Material is free text -- users edit the colour catalog.
    __resetColorCatalogForTests();
    setColorCatalog({ ffffff: 'Jade White' }, { 'pla|matte|ffffff': 'Ivory White' });
    expect(getColorName('FFFFFFFF', 'PLA|Matte')).toBe('Ivory White');
  });

  it('ignores malformed qualified keys instead of poisoning the map', () => {
    __resetColorCatalogForTests();
    setColorCatalog(
      { ffffff: 'Jade White' },
      { 'pla matte|nothex': 'Nope', '|ffffff': 'Nope', 'pla matte': 'Nope', 'pla matte|ffffff': 'Ivory White' },
    );
    expect(getColorName('FFFFFFFF', 'PLA Matte')).toBe('Ivory White');
    expect(getColorName('FFFFFFFF')).toBe('Jade White');
  });
});

describe('getColorName: nearest Bambu colour (#3279)', () => {
  // A colour picked on the printer's touchscreen arrives as a hex a step or
  // two off Bambu Studio's: Blue Grey is 5B6579 in the catalogue, the printer
  // reports 5A657B. The exact lookup missed it and the hue bucket said "Blue".
  const BAMBU = [
    { hex: '5b6579', name: 'Blue Grey', material: 'PLA Basic' },
    { hex: 'ff9016', name: 'Pumpkin Orange', material: 'PLA Basic' },
    { hex: 'ff911a', name: 'Translucent Orange', material: 'PETG Translucent' },
    { hex: 'a6a9aa', name: 'Silver', material: 'PLA Basic' },
  ];

  beforeEach(() => {
    __resetColorCatalogForTests();
    setColorCatalog({}, {}, BAMBU);
  });

  it('names a printer-palette hex by the Bambu colour it is a step away from', () => {
    expect(getColorName('5A657BFF')).toBe('Blue Grey');
    expect(getColorName('5A657B', 'PLA Basic')).toBe('Blue Grey');
  });

  it('keeps the hue bucket for a colour no Bambu entry is close to', () => {
    expect(getColorName('123456')).toBe('Blue');
  });

  it('prefers the colour of the material the caller names', () => {
    // FF9017 is nearer Pumpkin Orange (PLA Basic) than Translucent Orange (PETG).
    expect(getColorName('FF9017')).toBe('Pumpkin Orange');
    expect(getColorName('FF9017', 'PETG Translucent')).toBe('Translucent Orange');
    expect(getColorName('FF9017', ' petg TRANSLUCENT ')).toBe('Translucent Orange');
  });

  it('looks in every Bambu range when the material is not one of them', () => {
    expect(getColorName('5A657B', 'Generic PETG')).toBe('Blue Grey');
  });

  it('does not borrow another range\'s name when the material is a Bambu range', () => {
    expect(getColorName('5A657B', 'PETG Translucent')).toBe('Blue');
  });

  it('never moves a different Bambu colour onto the file colour', () => {
    // #3279's own pair: the file's A4AAAC is Silver, the tray's 5A657B Blue Grey.
    expect(getColorName('A4AAAC')).toBe('Silver');
  });

  it('lets an exact catalogue name win over a nearer-sounding guess', () => {
    __resetColorCatalogForTests();
    setColorCatalog({ '5a657b': 'Slate' }, {}, BAMBU);
    expect(getColorName('5A657B')).toBe('Slate');
  });

  it('forgets the old answers when the catalogue is replaced', () => {
    expect(getColorName('5A657B')).toBe('Blue Grey');
    setColorCatalog({}, {}, [{ hex: '5b6579', name: 'Renamed Grey', material: 'PLA Basic' }]);
    expect(getColorName('5A657B')).toBe('Renamed Grey');
  });

  it('takes the first listed of two equally near colours', () => {
    __resetColorCatalogForTests();
    setColorCatalog({}, {}, [
      { hex: '5b6579', name: 'Blue Grey', material: 'PLA Basic' },
      { hex: '5b6579', name: 'Slate Grey', material: 'PLA Matte' },
    ]);
    expect(getColorName('5A657B')).toBe('Blue Grey');
  });

  it('stays within the material when it has Bambu colours of its own', () => {
    // A PLA Basic slot at 8F8F8F is not Translucent Gray (PETG Translucent,
    // 0.3 away) just because PLA Basic's Gray is further than the threshold.
    __resetColorCatalogForTests();
    setColorCatalog({}, {}, [
      { hex: '8e8e8e', name: 'Translucent Gray', material: 'PETG Translucent' },
      { hex: '8e9089', name: 'Gray', material: 'PLA Basic' },
    ]);
    expect(getColorName('8F8F8F', 'PLA Basic')).toBe('Gray');
    expect(getColorName('8F8F8F', 'PLA Basic')).not.toBe('Translucent Gray');
    expect(getColorName('8F8F8F', 'Some Third Party PLA')).toBe('Translucent Gray');
  });

  it("finds the material's own colour before another range's exact hex", () => {
    // Pumpkin Orange (PLA Basic) owns FF9016 in the flat map; a PETG
    // Translucent slot one step away from it is still Translucent Orange.
    __resetColorCatalogForTests();
    setColorCatalog({ ff9016: 'Pumpkin Orange' }, {}, BAMBU);
    expect(getColorName('FF9016', 'PETG Translucent')).toBe('Translucent Orange');
    expect(getColorName('FF9016')).toBe('Pumpkin Orange');
  });

  it("takes the material from the slot's preset when the printer sends none", () => {
    // Without RFID the printer leaves tray_sub_brands empty but still reports
    // the preset picked on its screen (GFA00 = Bambu PLA Basic).
    __resetColorCatalogForTests();
    setColorCatalog({}, {}, BAMBU, { GFA00: 'PLA Basic', GFG01: 'PETG Translucent' });
    expect(getColorName('FF9017', '', 'GFG01')).toBe('Translucent Orange');
    expect(getColorName('FF9017', null, 'gfa00')).toBe('Pumpkin Orange');
    // tray_sub_brands, when there is one, is the more direct answer.
    expect(getColorName('FF9017', 'PLA Basic', 'GFG01')).toBe('Pumpkin Orange');
  });

  it('keeps Clear for a transparent spool', () => {
    expect(getColorName('5A657B00')).toBe('Clear');
  });

  it('ignores malformed entries', () => {
    __resetColorCatalogForTests();
    setColorCatalog({}, {}, [
      { hex: 'zzzzzz', name: 'Nope', material: 'PLA Basic' },
      { hex: '5b6579', name: '', material: 'PLA Basic' },
    ]);
    expect(getColorName('5A657B')).toBe('Blue');
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

  // #3090 — Spoolman has no colour-name field, so every Spoolman-backed spool
  // arrives with its subtype sitting in color_name. It reads like a name and
  // is not one.
  describe('a name the backend synthesised from the subtype', () => {
    it('loses to the catalog', () => {
      expect(resolveSpoolColorName('Silk+', '5F6367FF', true)).toBe('Titan Gray');
    });

    it('still wins over nothing when the hex is unknown', () => {
      // Better than "-": it at least says what is on the spool. The catalog
      // only covers what someone put in it.
      expect(resolveSpoolColorName('Silk+', '123456FF', true)).toBe('Silk+');
    });

    it('is not consulted when the flag is absent', () => {
      // The flag defaults to false, so every existing caller keeps the old
      // behaviour: a stored name is the user's and is used as given.
      expect(resolveSpoolColorName('Silk+', '5F6367FF')).toBe('Silk+');
    });

    it('does not resurrect a Bambu internal code', () => {
      expect(resolveSpoolColorName('A99-Z9', '123456FF', true)).toBeNull();
    });
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

describe('disambiguateColorNames', () => {
  // #2941: a slicer profile asked for a near-pure blue while the AMS slot held
  // Bambu's navy Blue. Both resolve to the name "Blue", so the mismatch warning
  // sat between two identical labels and read as a contradiction.
  const SLICER_BLUE = '#0028FF';
  const BAMBU_BLUE = '#0A2989';

  it('qualifies both sides with hex when the names collide', () => {
    expect(
      disambiguateColorNames({ name: 'Blue', hex: SLICER_BLUE }, { name: 'Blue', hex: BAMBU_BLUE }),
    ).toEqual(['Blue (#0028FF)', 'Blue (#0A2989)']);
  });

  it('treats names as colliding regardless of case', () => {
    expect(disambiguateColorNames({ name: 'blue', hex: SLICER_BLUE }, { name: 'Blue', hex: BAMBU_BLUE })).toEqual([
      'blue (#0028FF)',
      'Blue (#0A2989)',
    ]);
  });

  it('leaves distinct names alone', () => {
    // Once the words separate them the hex is noise, not information.
    expect(disambiguateColorNames({ name: 'Blue', hex: SLICER_BLUE }, { name: 'Navy', hex: BAMBU_BLUE })).toEqual([
      'Blue',
      'Navy',
    ]);
  });

  it('falls back to the hex for a side with no name', () => {
    expect(disambiguateColorNames({ hex: SLICER_BLUE }, { name: 'Blue', hex: BAMBU_BLUE })).toEqual([
      '#0028FF',
      'Blue',
    ]);
  });

  it('keeps the bare names when neither side has a usable hex', () => {
    // Better a repeated name than "Blue ()" twice.
    expect(disambiguateColorNames({ name: 'Blue', hex: 'nonsense' }, { name: 'Blue' })).toEqual(['Blue', 'Blue']);
  });

  it('qualifies only the side that has a hex', () => {
    expect(disambiguateColorNames({ name: 'Blue', hex: SLICER_BLUE }, { name: 'Blue' })).toEqual([
      'Blue (#0028FF)',
      'Blue',
    ]);
  });

  it('normalizes hex form, so an 8-char rgba and a bare hex read alike', () => {
    expect(disambiguateColorNames({ name: 'Blue', hex: '0028ffff' }, { name: 'Blue', hex: '#0a2989' })).toEqual([
      'Blue (#0028FF)',
      'Blue (#0A2989)',
    ]);
  });

  it('returns empty labels when there is nothing to name', () => {
    expect(disambiguateColorNames({}, {})).toEqual(['', '']);
  });
});


describe('getSwatchStyle (#1545, #2912)', () => {
  const CHECKERBOARD = 'repeating-conic-gradient(#979797 0% 25%, #f5f5f5 0% 50%)';

  it('falls back to neutral grey for missing or unparseable input', () => {
    expect(getSwatchStyle(null)).toEqual({ backgroundColor: '#808080' });
    expect(getSwatchStyle(undefined)).toEqual({ backgroundColor: '#808080' });
    expect(getSwatchStyle('')).toEqual({ backgroundColor: '#808080' });
    expect(getSwatchStyle('ABC')).toEqual({ backgroundColor: '#808080' });
  });

  it('paints an opaque colour flat, with or without the FF byte', () => {
    expect(getSwatchStyle('FF0000')).toEqual({ backgroundColor: '#FF0000' });
    expect(getSwatchStyle('FF0000FF')).toEqual({ backgroundColor: '#FF0000' });
    expect(getSwatchStyle('#FF0000FF')).toEqual({ backgroundColor: '#FF0000' });
  });

  it('shows the checkerboard alone for a fully transparent colour', () => {
    expect(getSwatchStyle('00000000')).toEqual({
      backgroundImage: CHECKERBOARD,
      backgroundSize: '8px 8px',
    });
  });

  it('layers a partly translucent colour over the checkerboard (#2912)', () => {
    // Regression: this used to fall through to the RGB prefix, so a 50%-alpha
    // spool rendered identically to an opaque one. Spoolman mode can now store
    // any non-FF alpha, so the in-between case is reachable in normal use.
    const style = getSwatchStyle('FF000080');
    expect(style.backgroundColor).toBeUndefined();
    expect(style.backgroundImage).toBe(
      `linear-gradient(#FF000080, #FF000080), ${CHECKERBOARD}`,
    );
    expect(style.backgroundSize).toBe('100% 100%, 8px 8px');
  });

  it('treats the alpha byte case-insensitively', () => {
    expect(getSwatchStyle('ff0000ff')).toEqual({ backgroundColor: '#ff0000' });
    expect(getSwatchStyle('ff000000')).toEqual({
      backgroundImage: CHECKERBOARD,
      backgroundSize: '8px 8px',
    });
  });
});
