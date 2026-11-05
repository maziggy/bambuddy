import { describe, it, expect } from 'vitest';
import type { TFunction } from 'i18next';
import { findMaterialMismatch, materialMismatchMessage } from '../../utils/assignMismatch';

const pla = { type: 'PLA', material: 'PLA', profile: 'Bambu PLA Basic' };

describe('findMaterialMismatch', () => {
  it('has nothing to ask for the same material, whatever the profile', () => {
    expect(findMaterialMismatch({ material: 'pla', slicer_filament_name: 'Generic PLA' }, pla)).toBeNull();
  });

  it('asks about a partial match, with the profiles when they differ too', () => {
    expect(findMaterialMismatch({ material: 'PLA', slicer_filament_name: 'Bambu PLA Basic @BBL X1C' }, { type: 'PLA Silk' })).toMatchObject({
      type: 'partial_profile',
      spoolMaterial: 'PLA',
      trayMaterial: 'PLA Silk',
    });
    expect(findMaterialMismatch({ material: 'PLA', slicer_filament_name: 'PLA Silk' }, { type: 'PLA Silk' })).toMatchObject({
      type: 'partial',
    });
  });

  it('asks about another material', () => {
    expect(findMaterialMismatch({ material: 'PETG', slicer_filament_name: 'Generic PETG' }, pla)).toEqual({
      type: 'material_profile',
      spoolMaterial: 'PETG',
      trayMaterial: 'PLA',
      spoolProfile: 'Generic PETG',
      trayProfile: 'Bambu PLA Basic',
    });
  });

  it('has nothing to compare against a slot that reports no material', () => {
    expect(findMaterialMismatch({ material: 'PETG' }, { type: '' })).toBeNull();
    expect(findMaterialMismatch({ material: 'PETG' }, { type: '  ', profile: 'Bambu PLA Basic' })).toBeNull();
  });
});

describe('materialMismatchMessage', () => {
  const t = ((key: string, vars?: Record<string, string>) => `${key}${vars ? JSON.stringify(vars) : ''}`) as unknown as TFunction;

  it('puts the material warning, the profile one when it applies, and the reconfigure note together', () => {
    const mismatch = findMaterialMismatch({ material: 'PETG', slicer_filament_name: 'Generic PETG' }, pla)!;
    const message = materialMismatchMessage(t, mismatch, 'AMS-A Slot 1').split('\n\n');

    expect(message).toHaveLength(3);
    expect(message[0]).toContain('inventory.assignMismatchMessage');
    expect(message[0]).toContain('"location":"AMS-A Slot 1"');
    expect(message[1]).toContain('inventory.assignProfileMismatchMessage');
    expect(message[2]).toBe('inventory.assignReconfigureNote');
  });

  it('leaves the profile line out when only the material differs', () => {
    const mismatch = findMaterialMismatch({ material: 'PLA', slicer_filament_name: 'PLA Silk' }, { type: 'PLA Silk' })!;
    const message = materialMismatchMessage(t, mismatch, 'Ext').split('\n\n');

    expect(message).toHaveLength(2);
    expect(message[0]).toContain('inventory.assignPartialMismatchMessage');
  });
});
