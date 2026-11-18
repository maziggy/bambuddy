/**
 * Build plate helpers (#1306).
 */

import { describe, it, expect } from 'vitest';
import {
  findPlateOption,
  normalizeBedType,
  parsePlateValue,
  plateLabel,
  plateOptions,
  plateValue,
} from '../../utils/buildPlates';
import type { BuildPlateConfig } from '../../api/client';

const type = (key: string, label: string, owned: boolean) => ({ key, label, owned, installed_on: [], waiting_jobs: 0 });

const config: BuildPlateConfig = {
  types: [
    type('cool_plate', 'Cool Plate', false),
    type('smooth_pei', 'Smooth PEI Plate', true),
    type('textured_pei', 'Textured PEI Plate', true),
    type('engineering', 'Engineering Plate', false),
  ],
  patterns: [
    { base_type: 'smooth_pei', name: 'Carbon Fiber', label: 'Smooth PEI Plate · Carbon Fiber', installed_on: [], waiting_jobs: 0 },
    { base_type: 'engineering', name: 'Gold', label: 'Engineering Plate · Gold', installed_on: [], waiting_jobs: 0 },
  ],
  pattern_suggestions: ['Carbon Fiber'],
};

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

describe('plate values', () => {
  it('round-trips a type with and without a pattern', () => {
    expect(parsePlateValue(plateValue('smooth_pei'))).toEqual({ baseType: 'smooth_pei', pattern: null });
    expect(parsePlateValue(plateValue('smooth_pei', 'Carbon Fiber'))).toEqual({
      baseType: 'smooth_pei',
      pattern: 'Carbon Fiber',
    });
    expect(plateLabel('Smooth PEI Plate', 'Carbon Fiber')).toBe('Smooth PEI Plate · Carbon Fiber');
    expect(plateLabel('Smooth PEI Plate', null)).toBe('Smooth PEI Plate');
  });
});

describe('plateOptions', () => {
  it('lists owned types, each followed by its patterns, in one flat list', () => {
    expect(plateOptions(config).map((o) => o.label)).toEqual([
      'Smooth PEI Plate',
      'Smooth PEI Plate · Carbon Fiber',
      'Textured PEI Plate',
    ]);
  });

  it('keeps the plate already chosen even when its type is unticked', () => {
    expect(plateOptions(config, { keep: { baseType: 'engineering', pattern: 'gold' } }).map((o) => o.label)).toEqual([
      'Smooth PEI Plate',
      'Smooth PEI Plate · Carbon Fiber',
      'Textured PEI Plate',
      'Engineering Plate · Gold',
    ]);
    expect(plateOptions(config, { keep: { baseType: 'cool_plate', pattern: null } })[0].label).toBe('Cool Plate');
  });

  it('limits the list to one type, ticked or not', () => {
    expect(plateOptions(config, { onlyType: 'engineering' }).map((o) => o.label)).toEqual([
      'Engineering Plate',
      'Engineering Plate · Gold',
    ]);
  });

  it('finds the option for a stored plate, ignoring the pattern case', () => {
    const options = plateOptions(config);
    expect(findPlateOption(options, 'smooth_pei', 'carbon fiber')?.label).toBe('Smooth PEI Plate · Carbon Fiber');
    expect(findPlateOption(options, 'smooth_pei', null)?.label).toBe('Smooth PEI Plate');
    expect(findPlateOption(options, null, null)).toBeUndefined();
  });

  it('is empty before the plates load', () => {
    expect(plateOptions(undefined)).toEqual([]);
  });
});
