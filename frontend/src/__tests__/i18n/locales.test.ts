import { describe, it, expect } from 'vitest';
import en from '../../i18n/locales/en';
import de from '../../i18n/locales/de';
import fr from '../../i18n/locales/fr';
import it_ from '../../i18n/locales/it';
import ja from '../../i18n/locales/ja';
import ptBR from '../../i18n/locales/pt-BR';
import zhCN from '../../i18n/locales/zh-CN';

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

const enKeys = new Set(getKeys(en));

const locales: [string, object][] = [
  ['de', de],
  ['fr', fr],
  ['it', it_],
  ['ja', ja],
  ['pt-BR', ptBR],
  ['zh-CN', zhCN],
];

describe('i18n locale parity', () => {
  locales.forEach(([name, locale]) => {
    const localeKeys = new Set(getKeys(locale));

    it(`${name} locale has all English keys`, () => {
      const missing = [...enKeys].filter((k) => !localeKeys.has(k)).sort();
      expect(missing, `Missing ${missing.length} key(s) in ${name} locale`).toEqual([]);
    });

    it(`English locale has all ${name} keys`, () => {
      const extra = [...localeKeys].filter((k) => !enKeys.has(k)).sort();
      expect(extra, `${extra.length} extra key(s) in ${name} locale not in English`).toEqual([]);
    });

    it(`${name} locale has the same number of keys as English`, () => {
      expect(localeKeys.size).toBe(enKeys.size);
    });
  });
});
