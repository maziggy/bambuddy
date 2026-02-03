import { describe, it, expect } from 'vitest';
import en from '../../i18n/locales/en';
import de from '../../i18n/locales/de';

/**
 * Recursively extracts all keys from a nested object as dot-notation paths.
 * Example: { foo: { bar: 'baz' } } => ['foo.bar']
 */
const getKeys = (obj: object, prefix = ''): string[] => {
  return Object.entries(obj).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof value === 'object' && value !== null
      ? getKeys(value, path)
      : [path];
  });
};

describe('i18n locale parity', () => {
  const enKeys = new Set(getKeys(en));
  const deKeys = new Set(getKeys(de));

  it('German locale has all English keys', () => {
    const missingInGerman = [...enKeys].filter((k) => !deKeys.has(k)).sort();
    expect(missingInGerman, `Missing ${missingInGerman.length} key(s) in German locale`).toEqual([]);
  });

  it('English locale has all German keys', () => {
    const missingInEnglish = [...deKeys].filter((k) => !enKeys.has(k)).sort();
    expect(missingInEnglish, `Missing ${missingInEnglish.length} key(s) in English locale`).toEqual([]);
  });

  it('both locales have the same number of keys', () => {
    expect(enKeys.size).toBe(deKeys.size);
  });
});
