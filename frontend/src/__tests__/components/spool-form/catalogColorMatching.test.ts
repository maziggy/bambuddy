/**
 * Pins how the spool form matches catalog colours to brand + material + subtype
 * (#3619). Documents today's behaviour of matchCatalogColors.
 */
import { describe, it, expect } from 'vitest';
import { matchCatalogColors, type CatalogColorEntry } from '../../../components/spool-form/catalogColors';

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
    const r = matchCatalogColors(catalog, 'Polymaker', 'PLA', '');
    expect(names(r)).toEqual(['Plain PLA Teal', 'Untyped Pink']);
  });

  it('PLA with a subtype that matches exactly stays separate from PLA-CF and PLA+', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PLA', 'Basic');
    expect(names(r)).toEqual(['PLA Basic Red']);
  });

  it('PLA without subtype (prefix) today also includes PLA-CF and PLA+ of the brand', () => {
    const r = matchCatalogColors(catalog, 'Bambu Lab', 'PLA', '');
    expect(names(r)).toEqual(['PLA Basic Red', 'PLA CF Black', 'PLA Matte Blue', 'PLA Plus Green', 'Silk Gold']);
  });

  it('exact material "PLA" typed as material only does not match a "PolyTerra PLA" entry', () => {
    const r = matchCatalogColors(catalog, 'Polymaker', 'PLA', '');
    expect(names(r)).not.toContain('PolyTerra Cotton White');
    expect(names(r)).not.toContain('PolyLite Red');
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

    it('does not carry extra_colors and effect_type (current behaviour, fixed next)', () => {
      const r = matchCatalogColors(plus, 'Bambu Lab', 'PLA', 'Silk+');
      const blue = r.find(c => c.name === 'Silk Blue')!;
      expect(blue.extra_colors).toBeUndefined();
      expect(blue.effect_type).toBeUndefined();
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
