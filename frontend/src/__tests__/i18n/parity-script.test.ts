import { describe, it, expect } from 'vitest';
// @ts-expect-error -- .mjs script with no type declarations; pure JS import is fine for tests
import { compareLocales, findHardcodedStrings, findMissingCodeKeys } from '../../../scripts/check-i18n-parity.mjs';

type LocaleMap = Map<string, string>;

const toMap = (obj: Record<string, string>): LocaleMap => new Map(Object.entries(obj));

const hasReport = (
  reports: Array<{ label: string; items: string[] }>,
  labelSubstr: string,
  itemSubstr?: string,
): boolean =>
  reports.some(
    (r) =>
      r.label.includes(labelSubstr) &&
      (itemSubstr === undefined || r.items.some((i) => i.includes(itemSubstr))),
  );

describe('compareLocales (parity-script self-test)', () => {
  it('passes when all locales match en', () => {
    const en = toMap({ 'a.b': 'hello {{name}}', 'count_one': 'one', 'count_other': 'many' });
    const result = compareLocales({
      en,
      'zh-CN': toMap({ 'a.b': '你好 {{name}}', 'count_one': '一', 'count_other': '多' }),
      'zh-TW': toMap({ 'a.b': '你好 {{name}}', 'count_one': '一', 'count_other': '多' }),
    });
    expect(result.failed).toBe(false);
    expect(result.reports).toEqual([]);
  });

  it('flags keys missing from a non-en locale', () => {
    const result = compareLocales({
      en: toMap({ 'a.b': 'x', 'a.c': 'y' }),
      'zh-TW': toMap({ 'a.b': 'x' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'zh-TW: missing keys vs en', 'a.c')).toBe(true);
  });

  it('flags keys that exist in a non-en locale but not in en', () => {
    const result = compareLocales({
      en: toMap({ 'a.b': 'x' }),
      'zh-CN': toMap({ 'a.b': 'x', 'a.stray': 'extra' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'zh-CN: extra keys vs en', 'a.stray')).toBe(true);
  });

  it('flags placeholder mismatch (missing placeholder in translation)', () => {
    const result = compareLocales({
      en: toMap({ greeting: 'Hello {{name}}!' }),
      'zh-CN': toMap({ greeting: '你好!' }), // {{name}} dropped
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'placeholder mismatch', 'greeting')).toBe(true);
  });

  it('flags placeholder mismatch (translation introduces unknown placeholder)', () => {
    // This is the exact class of bug the zh-CN sync caught:
    // fileManager.uploadFailed had a stray {{count}} copied from a sibling key.
    const result = compareLocales({
      en: toMap({ uploadFailed: 'Upload failed' }),
      'zh-CN': toMap({ uploadFailed: '{{count}} 个失败' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'placeholder mismatch', 'uploadFailed')).toBe(true);
  });

  it('flags missing plural suffix keys in a non-en locale', () => {
    const result = compareLocales({
      en: toMap({ item_one: 'item', item_other: 'items' }),
      'zh-TW': toMap({ item_one: '項目' }), // item_other missing
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'plural key mismatch', 'missing _other')).toBe(true);
  });

  it('flags a non-en _one key that does not exist in en', () => {
    const result = compareLocales({
      en: toMap({ item: 'item' }),
      'zh-CN': toMap({ item: '项', item_one: '一项' }), // en never plural-gated this
    });
    expect(result.failed).toBe(true);
    expect(
      hasReport(result.reports, 'plural key mismatch', 'unexpected _one not present in en'),
    ).toBe(true);
  });

  it('throws when the en locale is absent', () => {
    expect(() =>
      compareLocales({ 'zh-CN': toMap({ a: 'x' }) } as Record<string, LocaleMap>),
    ).toThrow(/locales\.en/);
  });
});

describe('findMissingCodeKeys (#3631)', () => {
  const enKeys = new Set(['common.save', 'items.count_one', 'items.count_other', 'tour.steps.first']);
  const check = (text: string) => findMissingCodeKeys([{ file: 'src/X.tsx', text }], enKeys);

  it('passes keys that exist in en.ts', () => {
    expect(check("t('common.save'); i18n.t(\"common.save\")")).toEqual([]);
  });

  it('flags a key missing from en.ts, with or without an inline fallback', () => {
    expect(check("t('common.saved'); t('spoolbuddy.settings.cores', 'Cores')")).toEqual([
      'src/X.tsx: common.saved',
      'src/X.tsx: spoolbuddy.settings.cores',
    ]);
  });

  it('accepts a plural base key and a subtree read with returnObjects', () => {
    expect(check("t('items.count', { count: 2 }); t('tour.steps', { returnObjects: true })")).toEqual([]);
  });

  it('ignores dynamic and undotted keys', () => {
    expect(check('t(`status.${s}`); t(key); t(\'plain\'); format(\'a.b\')')).toEqual([]);
  });
});

describe('findHardcodedStrings (#3638)', () => {
  const scan = (text: string, allowed = new Set<string>()) =>
    findHardcodedStrings([{ file: 'src/X.tsx', text }], allowed);

  it('flags JSX text, shown props and toast messages', () => {
    const found = scan(
      [
        'const A = () => (',
        '  <div title="Zoom out">',
        '    <span>Set Bed Temperature</span>',
        '    <input placeholder={"Search tags"} aria-label="Search" />',
        '  </div>',
        ');',
        "showToast('Saved the file', 'success');",
        'showToast(`Failed: ${e}`, \'error\');',
      ].join('\n'),
    );
    expect(found).toEqual([
      'src/X.tsx:2: title "Zoom out"',
      'src/X.tsx:3: text "Set Bed Temperature"',
      'src/X.tsx:4: aria-label "Search"',
      'src/X.tsx:4: placeholder "Search tags"',
      'src/X.tsx:7: showToast "Saved the file"',
      'src/X.tsx:8: showToast "Failed: …"',
    ]);
  });

  it('passes translated text, technical values and allowlisted strings', () => {
    const text = [
      'const A = () => (',
      "  <div title={t('camera.zoomOut')}>",
      '    {t(\'a.b\')} <span>mm</span> <span>kWh</span> <code>/api/v1/queue</code>',
      '    <input placeholder="192.168.1.100 or printer.local" />',
      '    <input placeholder="sensor.electricity_price" />',
      '    <span>Spoolman</span> <span>PLA</span> <span>X1 Carbon</span>',
      '  </div>',
      ');',
      'showToast(t(\'common.saved\'));',
    ].join('\n');
    expect(scan(text, new Set(['X1 Carbon']))).toEqual([]);
  });

  it('ignores .ts files', () => {
    expect(findHardcodedStrings([{ file: 'src/x.ts', text: "showToast('Saved the file')" }], new Set())).toEqual([]);
  });
});
