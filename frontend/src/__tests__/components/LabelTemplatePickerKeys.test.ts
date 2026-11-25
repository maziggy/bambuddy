import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import en from '../../i18n/locales/en';

// The dialog's tests render without i18next, so a key in the wrong place in
// the locale files only shows as the English fallback in every language and no
// test sees it (#3296 review). Every key the label picker names literally has
// to resolve in en.ts; the parity check carries it to the other locales.
const SOURCES = [
  'components/LabelTemplatePickerModal.tsx',
  'components/LabelSheetLayoutEditor.tsx',
  'utils/labelSheetLayout.ts',
];

function resolveKey(path: string): unknown {
  return path.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], en);
}

describe('label picker translation keys', () => {
  const keys = new Set<string>();
  for (const file of SOURCES) {
    const source = readFileSync(resolve(__dirname, '../..', file), 'utf8');
    for (const match of source.matchAll(/\bt\(\s*'(inventory\.labels\.[\w.]+)'/g)) keys.add(match[1]);
  }
  // The layout problems are looked up as inventory.labels.custom.problems.<key>.
  const utils = readFileSync(resolve(__dirname, '../../utils/labelSheetLayout.ts'), 'utf8');
  for (const match of utils.matchAll(/key: '(\w+)'/g)) keys.add(`inventory.labels.custom.problems.${match[1]}`);

  it('finds the keys it checks', () => {
    expect(keys).toContain('inventory.labels.custom.nameTaken');
    expect(keys).toContain('inventory.labels.custom.problems.tooSmall');
  });

  it.each([...keys])('%s is a string in en.ts', (key) => {
    expect(typeof resolveKey(key)).toBe('string');
  });
});
