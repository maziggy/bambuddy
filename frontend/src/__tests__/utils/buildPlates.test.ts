/**
 * Build plate helpers (#1306).
 */

import { describe, it, expect } from 'vitest';
import { groupPlatesByBaseType, normalizeBedType, plateImage } from '../../utils/buildPlates';
import type { BuildPlate } from '../../api/client';

const plate = (over: Partial<BuildPlate>): BuildPlate => ({
  id: 1,
  builtin_key: null,
  is_builtin: false,
  name: 'Plate',
  base_type: 'smooth_pei',
  base_type_label: 'Smooth PEI / High Temp Plate',
  pattern: null,
  image: null,
  notes: null,
  enabled: true,
  sort_order: 0,
  installed_on: [],
  ...over,
});

describe('normalizeBedType', () => {
  it('maps slicer names to the same base types as the backend', () => {
    expect(normalizeBedType('High Temp Plate')).toBe('smooth_pei');
    expect(normalizeBedType('Textured PEI Plate')).toBe('textured_pei');
    expect(normalizeBedType('Supertack Plate')).toBe('supertack');
    expect(normalizeBedType('Textured Cool Plate')).toBe('textured_cool_plate');
    expect(normalizeBedType('  cool plate ')).toBe('cool_plate');
    expect(normalizeBedType('smooth_pei')).toBe('smooth_pei');
  });

  it('treats unknown and default plates as no constraint', () => {
    expect(normalizeBedType(null)).toBeNull();
    expect(normalizeBedType('')).toBeNull();
    expect(normalizeBedType('Default Plate')).toBeNull();
    expect(normalizeBedType('Glass Plate')).toBeNull();
  });
});

describe('plateImage', () => {
  it('prefers the plate photo and falls back to the base type icon', () => {
    expect(plateImage(plate({ image: '/img/plates/x.jpg' }))).toBe('/img/plates/x.jpg');
    expect(plateImage(plate({ base_type: 'textured_pei' }))).toBe('/img/bed/bed_pei.png');
  });
});

describe('groupPlatesByBaseType', () => {
  it('groups patterned plates under their base type, keeping order', () => {
    const groups = groupPlatesByBaseType([
      plate({ id: 1, name: 'Textured', base_type: 'textured_pei', base_type_label: 'Textured PEI Plate' }),
      plate({ id: 2, name: 'Smooth' }),
      plate({ id: 3, name: 'Carbon Fiber', pattern: 'Carbon Fiber' }),
    ]);
    expect(groups.map((g) => g.baseType)).toEqual(['textured_pei', 'smooth_pei']);
    expect(groups[1].plates.map((p) => p.name)).toEqual(['Smooth', 'Carbon Fiber']);
  });
});
