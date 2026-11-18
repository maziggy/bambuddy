/**
 * Pins how the spool form matches catalog colours to brand + material + subtype
 * (#3619). Documents today's behaviour of matchCatalogColors.
 */
import { describe, it, expect } from 'vitest';
import { matchCatalogColors, allBrandCatalogColors, brandAliases, type CatalogColorEntry } from '../../../components/spool-form/catalogColors';

const e = (manufacturer: string, color_name: string, material: string | null, extra: Partial<CatalogColorEntry> = {}): CatalogColorEntry => ({
  manufacturer,
  color_name,
  hex_color: '#112233',
  material,
  ...extra,
});

const names = (r: { name: string }[]) => r.map(c => c.name).sort();

describe('matchCatalogColors (today)', () => {
  const catalog = [
    e('Bambu Lab', 'PLA Basic Red', 'PLA Basic'),
    e('Bambu Lab', 'PLA Matte Blue', 'PLA Matte'),
    e('Bambu Lab', 'PLA CF Black', 'PLA-CF'),
    e('Bambu Lab', 'PLA Plus Green', 'PLA+'),
    e('Bambu Lab', 'PETG Basic White', 'PETG Basic'),
    e('Bambu Lab', 'PETG HF Grey', 'PETG-HF'),
    e('Bambu Lab', 'Silk Gold', 'PLA Silk'),
    e('Polymaker', 'PolyTerra Cotton White', 'PolyTerra PLA'),
    e('Polymaker', 'PolyLite Red', 'PolyLite PLA'),
    e('Polymaker', 'Plain PLA Teal', 'PLA'),
    e('Polymaker', 'Untyped Pink', null),
  ];

  it('returns nothing for an empty catalog or empty brand and material', () => {
    expect(matchCatalogColors([], 'Bambu Lab', 'PLA', '')).toEqual([]);
    expect(matchCatalogColors(catalog, '', '', '')).toEqual([]);
    expect(matchCatalogColors(catalog, undefined, undefined, undefined)).toEqual([]);
    expect(matchCatalogColors(catalog, '  ', ' ', 'Basic')).toEqual([]);
  });

  it('brand only: every colour of the manufacturer', () => {
    const r = matchCatalogColors(catalog, 'Polymaker', '', '');
    expect(names(r)).toEqual(['Plain PLA Teal', 'PolyLite Red', 'PolyTerra Cotton White', 'Untyped Pink']);
  });

  it('brand only without any manufacturer match falls through to nothing', () => {
    expect(matchCatalogColors(catalog, 'Nobody', '', '')).toEqual([]);
  });

  it('brand match is word based and case-insensitive ("mz - Bambu" matches Bambu Lab)', () => {
    const r = matchCatalogColors(catalog, 'mz - bambu', 'PETG', 'Basic');
    expect(names(r)).toEqual(['PETG Basic White']);
  });

  it('exact brand + material + subtype match wins over the prefix fallback', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PETG', 'Basic');
    expect(names(r)).toEqual(['PETG Basic White']);
  });

  it('falls back to the material prefix when there is no exact match', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PETG', 'Nonexistent');
    expect(names(r)).toEqual(['PETG Basic White', 'PETG HF Grey']);
  });

  it('material without subtype uses the prefix rule', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PETG', '');
    expect(names(r)).toEqual(['PETG Basic White', 'PETG HF Grey']);
  });

  it('prefix rule includes entries without a material', () => {
    const r = matchCatalogColors([e('Acme', 'Plain', 'PLA'), e('Acme', 'Untyped', null), e('Acme', 'Other', 'PETG')], 'Acme', 'PLA', '');
    expect(names(r)).toEqual(['Plain', 'Untyped']);
  });

  it('PLA with a subtype that matches exactly stays separate from PLA-CF and PLA+', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PLA', 'Basic');
    expect(names(r)).toEqual(['PLA Basic Red']);
  });

  it('PLA without subtype (prefix) today also includes PLA-CF and PLA+ of the brand', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PLA', '');
    expect(names(r)).toEqual(['PLA Basic Red', 'PLA CF Black', 'PLA Matte Blue', 'PLA Plus Green', 'Silk Gold']);
  });

  it('brand that matches no manufacturer gives nothing when a material is set', () => {
    expect(matchCatalogColors(catalog, 'Nobody', 'PLA', '')).toEqual([]);
  });

  it('material only (no brand) matches across manufacturers', () => {
    const r = matchCatalogColors(catalog, '', 'PETG', '');
    expect(names(r)).toEqual(['PETG Basic White', 'PETG HF Grey', 'Untyped Pink']);
  });

  it('maps hex without # and caps it at 6 chars', () => {
    const r = matchCatalogColors([e('Acme', 'X', 'PLA', { hex_color: '#AABBCCDD' })], 'Acme', 'PLA', '');
    expect(r[0].hex).toBe('AABBCC');
  });

  describe("trailing '+' branch", () => {
    const plus = [
      e('Bambu Lab', 'Silk Blue', 'PLA Silk', { extra_colors: 'aabbcc,ddeeff', effect_type: 'sparkle' }),
      e('Bambu Lab', 'Silk Red', 'PLA Silk'),
    ];

    it('"PLA Silk+" falls back to the "PLA Silk" entries', () => {
      const r = matchCatalogColors(plus, 'Bambu Lab', 'PLA', 'Silk+');
      expect(names(r)).toEqual(['Silk Blue', 'Silk Red']);
    });

    it('exact match takes precedence over the "+" normalisation', () => {
      const withPlus = [...plus, e('Bambu Lab', 'Silk Plus Gold', 'PLA Silk+')];
      expect(names(matchCatalogColors(withPlus, 'Bambu Lab', 'PLA', 'Silk+'))).toEqual(['Silk Plus Gold']);
    });

    it('carries extra_colors and effect_type like the other branches', () => {
      const r = matchCatalogColors(plus, 'Bambu Lab', 'PLA', 'Silk+');
      const blue = r.find(c => c.name === 'Silk Blue')!;
      expect(blue.extra_colors).toBe('aabbcc,ddeeff');
      expect(blue.effect_type).toBe('sparkle');
      const red = r.find(c => c.name === 'Silk Red')!;
      expect(red.extra_colors).toBeNull();
      expect(red.effect_type).toBeNull();
    });
  });

  it('other branches carry extra_colors and effect_type', () => {
    const c = [e('Bambu Lab', 'Galaxy', 'PLA Sparkle', { extra_colors: 'aabbcc', effect_type: 'sparkle' })];
    const exact = matchCatalogColors(c, 'Bambu Lab', 'PLA', 'Sparkle');
    expect(exact[0]).toMatchObject({ extra_colors: 'aabbcc', effect_type: 'sparkle' });
    const prefix = matchCatalogColors(c, 'Bambu Lab', 'PLA', '');
    expect(prefix[0]).toMatchObject({ extra_colors: 'aabbcc', effect_type: 'sparkle' });
    const brandOnly = matchCatalogColors(c, 'Bambu Lab', '', '');
    expect(brandOnly[0]).toMatchObject({ extra_colors: 'aabbcc', effect_type: 'sparkle' });
  });
});

describe('matchCatalogColors: product lines (#3619)', () => {
  const catalog = [
    e('Polymaker', 'Cotton White', 'PolyTerra PLA'),
    e('Polymaker', 'Earth Brown', 'PolyTerra PLA'),
    e('Polymaker', 'Lite Red', 'PolyLite PLA'),
    e('Polymaker', 'Lite Carbon', 'PolyLite PLA-CF'),
    e('Polymaker', 'Lite Plus', 'PolyLite PLA+'),
    e('Polymaker', 'Lite Silk', 'PolyLite PLA Silk'),
    e('Polymaker', 'Lite PETG', 'PolyLite PETG'),
    e('Polymaker', 'Panchroma', 'PLA'),
    e('Bambu Lab', 'Bambu Red', 'PLA Basic'),
    e('Other', 'PolyTerraX Fake', 'PolyTerraX PLA'),
  ];

  it('Polymaker + PLA: PolyTerra PLA and PolyLite PLA entries, no PLA-CF / PLA+ / PETG leak', () => {
    const r = matchCatalogColors(catalog, 'Polymaker', 'PLA', '');
    expect(names(r)).toEqual(['Cotton White', 'Earth Brown', 'Lite Red', 'Panchroma']);
  });

  it('PolyTerra + PLA: only PolyTerra PLA entries', () => {
    const r = matchCatalogColors(catalog, 'PolyTerra', 'PLA', '');
    expect(names(r)).toEqual(['Cotton White', 'Earth Brown']);
  });

  it('PolyTerra without a material: everything of the line', () => {
    const r = matchCatalogColors(catalog, 'PolyTerra', '', '');
    expect(names(r)).toEqual(['Cotton White', 'Earth Brown']);
  });

  it('PolyTerra + PETG: nothing (the line has no PETG)', () => {
    expect(matchCatalogColors(catalog, 'PolyTerra', 'PETG', '')).toEqual([]);
  });

  it('brand must be a whole word in the material ("PolyTerra" is not in "PolyTerraX")', () => {
    const r = matchCatalogColors(catalog, 'PolyTerra', 'PLA', '');
    expect(names(r)).not.toContain('PolyTerraX Fake');
  });

  it('subtype is appended: Polymaker + PLA Silk finds "PolyLite PLA Silk"', () => {
    const r = matchCatalogColors(catalog, 'Polymaker', 'PLA', 'Silk');
    expect(names(r)).toEqual(['Lite Silk']);
  });

  it('PolyLite + PLA matches only the PolyLite line, not PLA-CF or PLA+', () => {
    const r = matchCatalogColors(catalog, 'PolyLite', 'PLA', '');
    expect(names(r)).toEqual(['Lite Red']);
  });

  it('no brand: behaviour as before (no product-line widening by manufacturer)', () => {
    const r = matchCatalogColors(catalog, '', 'PLA', '');
    expect(names(r)).toEqual(['Bambu Red', 'Panchroma']);
  });

  it('other manufacturers are unaffected', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PLA', 'Basic');
    expect(names(r)).toEqual(['Bambu Red']);
  });

  describe('brand that is a variant or material word', () => {
    const variantCatalog = [
      e('Bambu Lab', 'Silk Red', 'PLA Silk'),
      e('Bambu Lab', 'Basic Blue', 'PLA Basic'),
      e('Polymaker', 'Silk Gold', 'PolyLite PLA Silk'),
    ];

    it('"Silk" does not pull in every line that contains the word', () => {
      expect(matchCatalogColors(variantCatalog, 'Silk', 'PLA', '')).toEqual([]);
      expect(matchCatalogColors(variantCatalog, 'Silk', '', '')).toEqual([]);
    });

    it('"Basic" does not pull in every line that contains the word', () => {
      expect(matchCatalogColors(variantCatalog, 'Basic', 'PLA', '')).toEqual([]);
    });

    it('"PLA" as a brand does not match by product line either', () => {
      expect(matchCatalogColors(variantCatalog, 'PLA', '', '')).toEqual([]);
    });

    it('a real manufacturer named like a variant still matches by manufacturer', () => {
      const c = [e('Silk Works', 'Gold', 'PLA')];
      expect(names(matchCatalogColors(c, 'Silk', 'PLA', ''))).toEqual(['Gold']);
    });
  });

  describe('end-of-material rule and fibre-filled products', () => {
    const fibre = [
      e('Protopasta', 'Plain Red', 'PLA'),
      e('Protopasta', 'Carbon Black', 'Carbon Fiber PLA'),
      e('Protopasta', 'Glass Clear', 'Glass Fiber PLA'),
      e('Protopasta', 'Conductive Black', 'Conductive PLA'),
      e('Protopasta', 'HT Natural', 'HTPLA'),
      e('3DXTECH', 'CF Black', 'CF PLA'),
      e('3DXTECH', 'GF Grey', 'GF PLA'),
      e('3DXTECH', 'Easy Blue', 'Easy PLA'),
      e('3DXTECH', 'Cobalt', 'Cobalt Edition PLA'),
    ];

    it('Carbon Fiber / Glass Fiber / CF / GF PLA do not count as PLA', () => {
      expect(names(matchCatalogColors(fibre, 'Protopasta', 'PLA', ''))).toEqual(['Plain Red']);
      expect(names(matchCatalogColors(fibre, '3DXTECH', 'PLA', ''))).toEqual(['Easy Blue']);
    });

    it('a variant word in front of PLA does not count either', () => {
      expect(names(matchCatalogColors(fibre, 'Protopasta', 'PLA', ''))).not.toContain('Conductive Black');
    });

    it('they count when the form material or subtype names them', () => {
      expect(names(matchCatalogColors(fibre, 'Protopasta', 'PLA', 'Carbon Fiber'))).toContain('Carbon Black');
      expect(names(matchCatalogColors(fibre, 'Protopasta', 'PLA', 'Carbon Fiber'))).not.toContain('Glass Clear');
      expect(names(matchCatalogColors(fibre, '3DXTECH', 'PLA', 'CF'))).toEqual(['CF Black', 'Easy Blue']);
      expect(names(matchCatalogColors(fibre, '3DXTECH', 'PLA', 'GF'))).toEqual(['Easy Blue', 'GF Grey']);
    });

    it('a single-word product line before PLA is accepted', () => {
      expect(names(matchCatalogColors(fibre, '3DXTECH', 'PLA', ''))).toContain('Easy Blue');
    });

    it('a two-word prefix that is not a modifier is not a single product line', () => {
      expect(names(matchCatalogColors(fibre, '3DXTECH', 'PLA', ''))).not.toContain('Cobalt');
    });
  });

  describe('allBrandCatalogColors', () => {
    it('lists every colour of the brand, matching ones first', () => {
      const matched = matchCatalogColors(catalog, 'Polymaker', 'PLA', '');
      const all = allBrandCatalogColors(catalog, 'Polymaker', matched);
      expect(all.slice(0, matched.length)).toEqual(matched);
      expect(names(all)).toEqual(names(catalog.filter(c => c.manufacturer === 'Polymaker').map(c => ({ name: c.color_name }))));
      expect(new Set(all.map(c => c.name)).size).toBe(all.length);
    });

    it('is empty without a brand', () => {
      expect(allBrandCatalogColors(catalog, '', [])).toEqual([]);
      expect(allBrandCatalogColors(catalog, '  ', [])).toEqual([]);
  });

  it('a one-character brand has no word to match and offers nothing', () => {
    expect(allBrandCatalogColors(catalog, 'X', [])).toEqual([]);
    });

    it('a product-line brand lists the whole line', () => {
      const all = allBrandCatalogColors(catalog, 'PolyTerra', matchCatalogColors(catalog, 'PolyTerra', 'PLA', ''));
      expect(names(all)).toEqual(['Cotton White', 'Earth Brown']);
    });
  });
});

describe('variant word as brand does not match inside manufacturer names', () => {
  const catalog = [
    e('MatterHackers', 'Build Red', 'Build PLA'),
    e('Bambu Lab', 'Matte Blue', 'PLA Matte'),
  ];

  it('"Matte" does not pull MatterHackers Build PLA', () => {
    expect(names(matchCatalogColors(catalog, 'Matte', '', ''))).not.toContain('Build Red');
    expect(names(allBrandCatalogColors(catalog, 'Matte', []))).not.toContain('Build Red');
  });

  it('a real manufacturer still matches', () => {
    expect(names(matchCatalogColors(catalog, 'MatterHackers', '', ''))).toEqual(['Build Red']);
  });
});

describe('brandAliases with seed-like materials (#3619)', () => {
  const catalog = [
    e('Bambu Lab', 'PAHT Black', 'PAHT-CF'),
    e('Bambu Lab', 'Basic Red', 'PLA Basic'),
    e('ColorFabb', 'Green', 'PLA'),
    e('ColorFabb', 'Pink', 'PHA'),
    e('ColorFabb', 'Clear', 'XT'),
    e('3DXTECH', 'Natural', 'rPETG/ECOMAX'),
    e('3DXTECH', 'Black', 'ECOMAX PLA'),
    e('MatterHackers', 'Natural', 'NylonX'),
    e('MatterHackers', 'Blue', 'Build PLA'),
  ];

  it('material words are not product lines', () => {
    expect(brandAliases(catalog, 'Bambu Lab')).toEqual([]);
    expect(brandAliases(catalog, 'ColorFabb')).toEqual([]);
    expect(brandAliases(catalog, '3DXTECH')).toEqual(['ECOMAX']);
  });

  it('a product line before the material is still found', () => {
    expect(brandAliases(catalog, 'MatterHackers')).toEqual(['Build']);
  });

  it('XT is not an alias of 3DXTECH', () => {
    expect(brandAliases(catalog, 'XT')).not.toContain('3DXTECH');
  });
});

describe('brandAliases (#3619)', () => {
  const catalog = [
    e('Polymaker', 'Cotton White', 'PolyTerra PLA'),
    e('Polymaker', 'Lite Red', 'PolyLite PLA'),
    e('Polymaker', 'Lite Carbon', 'PolyLite PLA-CF'),
    e('Polymaker', 'Lite PETG', 'PolyLite PETG'),
    e('Polymaker', 'Silk Gold', 'PLA Silk'),
    e('Polymaker', 'Carbon', 'Carbon Fiber PLA'),
    e('Polymaker', 'Plain', 'PLA'),
    e('Bambu Lab', 'Basic Blue', 'PLA Basic'),
    e('Other', 'Fake', 'PolyTerraX PLA'),
  ];

  it('a manufacturer goes by its product lines, minus variant and material words', () => {
    expect(brandAliases(catalog, 'Polymaker').sort()).toEqual(['PolyLite', 'PolyTerra']);
  });

  it('a product line goes by the manufacturer whose materials contain it', () => {
    expect(brandAliases(catalog, 'PolyTerra')).toEqual(['Polymaker']);
  });

  it('whole word only', () => {
    expect(brandAliases(catalog, 'PolyTerraX')).toEqual(['Other']);
    expect(brandAliases(catalog, 'Terra')).toEqual([]);
  });

  it('variant and material words as a brand have no aliases', () => {
    expect(brandAliases(catalog, 'Silk')).toEqual([]);
    expect(brandAliases(catalog, 'Basic')).toEqual([]);
    expect(brandAliases(catalog, 'PLA')).toEqual([]);
  });

  it('no brand or no catalog: none', () => {
    expect(brandAliases(catalog, '')).toEqual([]);
    expect(brandAliases([], 'Polymaker')).toEqual([]);
  });

  it('a manufacturer without product lines has none', () => {
    expect(brandAliases(catalog, 'Bambu Lab')).toEqual([]);
  });
});
