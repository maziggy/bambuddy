/** Colour order of the catalog colours in the spool form (#3633). */
import { describe, it, expect } from 'vitest';
import { sortCatalogColors, matchCatalogColors, type CatalogColorEntry } from '../../../components/spool-form/catalogColors';

const e = (color_name: string, hex_color: string, extra: Partial<CatalogColorEntry> = {}): CatalogColorEntry => ({
  manufacturer: 'Polymaker',
  color_name,
  hex_color,
  material: 'PolyTerra PLA',
  ...extra,
});

const sorted = (entries: CatalogColorEntry[], order: 'name' | 'color') =>
  sortCatalogColors(matchCatalogColors(entries, 'Polymaker', 'PLA', ''), order).map(c => c.name);

describe('sortCatalogColors', () => {
  it("'name' keeps the input order", () => {
    const entries = [e('Zebra', '#0000ff'), e('Apple', '#ff0000'), e('Mango', '#00ff00')];
    expect(sorted(entries, 'name')).toEqual(['Zebra', 'Apple', 'Mango']);
  });

  it('orders chromatic families as the colour wheel, brown after pink', () => {
    const entries = [
      e('Pink', '#ff69b4'), e('Brown', '#8b4513'), e('Blue', '#0000ff'), e('Green', '#00ff00'),
      e('Yellow', '#ffff00'), e('Orange', '#ff8000'), e('Red', '#ff0000'), e('Purple', '#8000ff'), e('Cyan', '#00ffff'),
    ];
    expect(sorted(entries, 'color')).toEqual(['Red', 'Orange', 'Yellow', 'Green', 'Cyan', 'Blue', 'Purple', 'Pink', 'Brown']);
  });

  it('puts achromatic colours white, grey, black after the chromatic ones', () => {
    const entries = [e('Black', '#000000'), e('Grey', '#808080'), e('Red', '#ff0000'), e('White', '#ffffff')];
    expect(sorted(entries, 'color')).toEqual(['Red', 'White', 'Grey', 'Black']);
  });

  it('sorts Clear (alpha 00) last, although its RGB is white or black', () => {
    const entries = [
      e('Clear', '#ffffff00'), e('Black', '#000000'), e('Red', '#ff0000'), e('Clear Black', '#00000000'), e('White', '#ffffff'),
    ];
    expect(sorted(entries, 'color')).toEqual(['Red', 'White', 'Black', 'Clear', 'Clear Black']);
  });

  it('sorts a multicolour entry by its first colour', () => {
    const entries = [
      e('Blue', '#0000ff'),
      e('Rainbow', '#ff0000', { extra_colors: '00ff00,0000ff' }),
      e('Green', '#00ff00'),
    ];
    expect(sorted(entries, 'color')).toEqual(['Rainbow', 'Green', 'Blue']);
  });

  it('keeps the input order of equal colours (stable)', () => {
    const entries = [e('B twin', '#ff0000'), e('Blue', '#0000ff'), e('A twin', '#ff0000'), e('C twin', '#ff0000')];
    expect(sorted(entries, 'color')).toEqual(['B twin', 'A twin', 'C twin', 'Blue']);
  });

  it('does not change the input array', () => {
    const colors = matchCatalogColors([e('Blue', '#0000ff'), e('Red', '#ff0000')], 'Polymaker', 'PLA', '');
    sortCatalogColors(colors, 'color');
    expect(colors.map(c => c.name)).toEqual(['Blue', 'Red']);
  });
});
