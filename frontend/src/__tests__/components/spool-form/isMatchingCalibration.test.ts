/**
 * Tests for K-profile matching helpers (#1688 + #1689).
 *
 * `isMatchingCalibration` is the predicate used by both the spool form's
 * PA-profile suggester and ConfigureAmsSlotModal's K-profile filter. The
 * #1688 enhancement adds a filament_id-first match path with id
 * normalisation; the old name-parsing logic stays as the fallback.
 */

import { describe, it, expect } from 'vitest';
import {
  isMatchingCalibration,
  toFilamentId,
  isGenericFilamentId,
} from '../../../components/spool-form/utils';

describe('toFilamentId', () => {
  it('strips the variant suffix', () => {
    expect(toFilamentId('GFG98_09')).toBe('GFG98');
  });

  it('strips the "S" infix from a setting_id', () => {
    // Spool stores setting_id "GFSG98", K-profile stores filament_id "GFG98".
    // Normalisation has to drop the "S" so both sides agree.
    expect(toFilamentId('GFSG98')).toBe('GFG98');
  });

  it('strips both the "S" infix and the variant suffix', () => {
    expect(toFilamentId('GFSG98_09')).toBe('GFG98');
  });

  it('returns bare filament_id unchanged', () => {
    expect(toFilamentId('GFL05')).toBe('GFL05');
  });

  it('uppercases the result', () => {
    expect(toFilamentId('gfsg98_09')).toBe('GFG98');
  });

  it('returns empty string for null/undefined/empty', () => {
    expect(toFilamentId(null)).toBe('');
    expect(toFilamentId(undefined)).toBe('');
    expect(toFilamentId('')).toBe('');
  });

  it('passes through non-Bambu IDs (numeric local-preset, Orca UUID) without crashing', () => {
    // Numeric local-preset ID — caller falls through to name match, no crash.
    expect(toFilamentId('42')).toBe('42');
    // Orca UUID — same.
    expect(toFilamentId('orca-uuid-abc')).toBe('ORCA-UUID-ABC');
  });
});

describe('isGenericFilamentId', () => {
  it('flags GFx99 patterns as generic', () => {
    expect(isGenericFilamentId('GFL99')).toBe(true);
    expect(isGenericFilamentId('GFG99')).toBe(true);
    expect(isGenericFilamentId('GFB99')).toBe(true);
  });

  it('does not flag non-generic IDs', () => {
    expect(isGenericFilamentId('GFL05')).toBe(false);
    expect(isGenericFilamentId('GFG98')).toBe(false);
  });

  it('returns false for null/undefined/empty', () => {
    expect(isGenericFilamentId(null)).toBe(false);
    expect(isGenericFilamentId(undefined)).toBe(false);
    expect(isGenericFilamentId('')).toBe(false);
  });
});

describe('isMatchingCalibration (#1688)', () => {
  const formData = {
    material: 'PETG',
    brand: 'Generic',
    subtype: '',
    slicer_filament: 'GFSG98_09', // spool's setting_id form
  };

  it('matches by filament_id when ids agree after normalisation', () => {
    // K-profile stores bare filament_id; spool stores setting_id. Both
    // normalise to "GFG98" — match without any name parsing.
    expect(
      isMatchingCalibration(
        { name: 'My Custom K Profile', filament_id: 'GFG98' },
        formData,
      ),
    ).toBe(true);
  });

  it('id-match wins even when the K-profile name would not parse to anything sensible', () => {
    expect(
      isMatchingCalibration(
        { name: 'literal-garbage-no-material', filament_id: 'GFG98' },
        formData,
      ),
    ).toBe(true);
  });

  it('skips id-match for generic GFx99 ids and falls through to name match', () => {
    // GFL99 = generic PLA, shared across many real filaments. Even if the
    // spool stored GFL99, name parsing must drive the decision.
    const result = isMatchingCalibration(
      { name: 'Random thing with no PETG in it', filament_id: 'GFL99' },
      { ...formData, slicer_filament: 'GFL99' },
    );
    expect(result).toBe(false);
  });

  it('falls through to name match when spool has no slicer_filament', () => {
    expect(
      isMatchingCalibration(
        { name: 'Generic PETG', filament_id: 'GFG98' },
        { material: 'PETG', brand: 'Generic', subtype: '' },
      ),
    ).toBe(true);
  });

  it('falls through to name match when K-profile has no filament_id', () => {
    expect(
      isMatchingCalibration(
        { name: 'Generic PETG' },
        formData,
      ),
    ).toBe(true);
  });

  it('falls through to name match when normalised ids differ', () => {
    // Spool says GFG98, K-profile says GFL05 — id-match fails, fall to name.
    expect(
      isMatchingCalibration(
        { name: 'Generic PETG', filament_id: 'GFL05' },
        formData,
      ),
    ).toBe(true);
    expect(
      isMatchingCalibration(
        { name: 'Bambu PLA Basic', filament_id: 'GFL05' },
        formData,
      ),
    ).toBe(false);
  });

  it('rejects calibrations whose material does not match (name fallback path)', () => {
    expect(
      isMatchingCalibration(
        { name: 'Bambu PLA Basic', filament_id: 'GFL05' },
        { material: 'PETG', brand: '', subtype: '' },
      ),
    ).toBe(false);
  });

  it('returns false when formData has no material', () => {
    expect(
      isMatchingCalibration(
        { name: 'PETG', filament_id: 'GFG98' },
        { material: '', brand: '', subtype: '' },
      ),
    ).toBe(false);
  });
});
