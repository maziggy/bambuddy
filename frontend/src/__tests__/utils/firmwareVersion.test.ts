import { describe, it, expect } from 'vitest';
import { compareFwVersions } from '../../utils/firmwareVersion';

describe('compareFwVersions', () => {
  it('returns 0 for equal versions', () => {
    expect(compareFwVersions('01.02.03.04', '01.02.03.04')).toBe(0);
  });

  it('returns positive when left is newer (major)', () => {
    expect(compareFwVersions('02.00.00.00', '01.99.99.99')).toBeGreaterThan(0);
  });

  it('returns negative when left is older (minor)', () => {
    expect(compareFwVersions('01.02.03.04', '01.03.00.00')).toBeLessThan(0);
  });

  it('compares patch segments', () => {
    expect(compareFwVersions('01.02.10.00', '01.02.02.00')).toBeGreaterThan(0);
  });

  it('compares build segments', () => {
    expect(compareFwVersions('01.02.03.05', '01.02.03.04')).toBeGreaterThan(0);
  });

  it('treats missing trailing segments as 0', () => {
    expect(compareFwVersions('01.02.03', '01.02.03.00')).toBe(0);
    expect(compareFwVersions('01.02', '01.02.00.00')).toBe(0);
  });

  it('sorts a list newest-first via descending sort', () => {
    const versions = ['01.02.02.00', '01.03.00.00', '01.02.10.00'];
    versions.sort((a, b) => compareFwVersions(b, a));
    expect(versions).toEqual(['01.03.00.00', '01.02.10.00', '01.02.02.00']);
  });

  it('handles the issue #568 ordering correctly', () => {
    // From the issue: current 01.00.05.00 with 01.01.00.00, 01.01.01.00, 01.01.03.00 available.
    expect(compareFwVersions('01.01.00.00', '01.00.05.00')).toBeGreaterThan(0);
    expect(compareFwVersions('01.01.03.00', '01.01.01.00')).toBeGreaterThan(0);
  });
});
