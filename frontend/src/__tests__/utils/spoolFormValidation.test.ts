import { describe, it, expect } from 'vitest';
import { validateForm, defaultFormData } from '../../components/spool-form/types';

describe('validateForm', () => {
  describe('standard mode', () => {
    it('requires slicer_filament, material, brand, and subtype', () => {
      const result = validateForm(defaultFormData);
      expect(result.isValid).toBe(false);
      expect(result.errors.slicer_filament).toBeDefined();
      expect(result.errors.material).toBeDefined();
      expect(result.errors.brand).toBeDefined();
      expect(result.errors.subtype).toBeDefined();
    });

    it('passes when all required fields are filled', () => {
      const data = {
        ...defaultFormData,
        slicer_filament: 'Bambu PLA Basic @BBL',
        material: 'PLA',
        brand: 'Bambu Lab',
        subtype: 'Basic',
      };
      const result = validateForm(data);
      expect(result.isValid).toBe(true);
      expect(Object.keys(result.errors)).toHaveLength(0);
    });
  });

  describe('quickAdd mode', () => {
    it('only requires material', () => {
      const result = validateForm(defaultFormData, true);
      expect(result.isValid).toBe(false);
      expect(result.errors.material).toBeDefined();
      expect(result.errors.slicer_filament).toBeUndefined();
      expect(result.errors.brand).toBeUndefined();
    });

    it('passes with only material set', () => {
      const data = { ...defaultFormData, material: 'PETG' };
      const result = validateForm(data, true);
      expect(result.isValid).toBe(true);
    });
  });

  describe('spoolmanMode', () => {
    it('only requires material (same as quickAdd)', () => {
      const result = validateForm(defaultFormData, false, true);
      expect(result.isValid).toBe(false);
      expect(result.errors.material).toBeDefined();
      expect(result.errors.slicer_filament).toBeUndefined();
      expect(result.errors.brand).toBeUndefined();
      expect(result.errors.subtype).toBeUndefined();
    });

    it('passes with only material set', () => {
      const data = { ...defaultFormData, material: 'PLA' };
      const result = validateForm(data, false, true);
      expect(result.isValid).toBe(true);
      expect(Object.keys(result.errors)).toHaveLength(0);
    });

    it('does not require slicer_filament even when present', () => {
      const data = { ...defaultFormData, material: 'ABS' };
      const result = validateForm(data, false, true);
      expect(result.isValid).toBe(true);
    });

    it('fails when material is empty string', () => {
      const data = { ...defaultFormData, material: '' };
      const result = validateForm(data, false, true);
      expect(result.isValid).toBe(false);
      expect(result.errors.material).toBeDefined();
    });

    it('quickAdd takes precedence over spoolmanMode', () => {
      const data = { ...defaultFormData, material: 'PLA' };
      const result = validateForm(data, true, true);
      expect(result.isValid).toBe(true);
    });
  });
});
