/**
 * Pure rules for choosing between "Alternatives" and "Separate jobs" when
 * several sliced files are selected (#3462).
 */

import { describe, it, expect } from 'vitest';
import {
  commonModel,
  defaultMultiFileMode,
  duplicateModel,
  filesIncompatibleWith,
  filesNotSlicedFor,
} from '../../components/PrintModal/multiFile';
import type { VariantCandidate } from '../../components/PrintModal/VariantCandidates';

const f = (id: number, model: string | null): VariantCandidate => ({
  id,
  filename: `file${id}.gcode.3mf`,
  sliced_for_model: model,
});

describe('duplicateModel', () => {
  it('returns the spelling of the first occurrence, ignoring case', () => {
    expect(duplicateModel([f(1, 'A1'), f(2, 'a1'), f(3, 'P1S')])).toBe('A1');
  });
  it('returns null when all models differ', () => {
    expect(duplicateModel([f(1, 'A1'), f(2, 'P1S')])).toBeNull();
  });
  it('never counts null models as duplicates', () => {
    expect(duplicateModel([f(1, null), f(2, null)])).toBeNull();
  });
});

describe('defaultMultiFileMode', () => {
  it('is separate when two files share a model', () => {
    expect(defaultMultiFileMode([f(1, 'A1'), f(2, 'A1')])).toBe('separate');
    expect(defaultMultiFileMode([f(1, 'A1'), f(2, 'A1'), f(3, 'X1C')])).toBe('separate');
  });
  it('is separate when a model is unknown', () => {
    expect(defaultMultiFileMode([f(1, 'A1'), f(2, null)])).toBe('separate');
  });
  it('is alternatives when every file has a distinct known model', () => {
    expect(defaultMultiFileMode([f(1, 'A1'), f(2, 'P1S')])).toBe('alternatives');
  });
});

describe('commonModel', () => {
  it('returns the shared model, ignoring case', () => {
    expect(commonModel([f(1, 'A1'), f(2, 'a1')])).toBe('A1');
  });
  it('returns null for differing models', () => {
    expect(commonModel([f(1, 'A1'), f(2, 'P1S')])).toBeNull();
  });
  it('returns null when a model is unknown', () => {
    expect(commonModel([f(1, 'A1'), f(2, null)])).toBeNull();
  });
});

describe('filesIncompatibleWith', () => {
  const files = [f(1, 'A1'), f(2, 'P1S'), f(3, null)];
  it('returns only the files sliced for another model', () => {
    expect(filesIncompatibleWith(files, 'A1').map((x) => x.id)).toEqual([2]);
  });
  it('returns nothing without a target model', () => {
    expect(filesIncompatibleWith(files, null)).toEqual([]);
  });
});

describe('filesNotSlicedFor', () => {
  const files = [f(1, 'A1'), f(2, 'P1S'), f(3, null)];
  it('returns files whose model differs exactly from the printer model', () => {
    expect(filesNotSlicedFor(files, 'A1').map((x) => x.id)).toEqual([2]);
  });
  it('returns nothing without a printer model', () => {
    expect(filesNotSlicedFor(files, null)).toEqual([]);
  });
});
