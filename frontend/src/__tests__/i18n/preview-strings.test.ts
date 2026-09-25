import { describe, it, expect } from 'vitest';
import en from '../../i18n/locales/en';
import de from '../../i18n/locales/de';
import es from '../../i18n/locales/es';
import fr from '../../i18n/locales/fr';
import it_ from '../../i18n/locales/it';
import ja from '../../i18n/locales/ja';
import ko from '../../i18n/locales/ko';
import nl from '../../i18n/locales/nl';
import ptBR from '../../i18n/locales/pt-BR';
import ru from '../../i18n/locales/ru';
import sv from '../../i18n/locales/sv';
import tr from '../../i18n/locales/tr';
import uk from '../../i18n/locales/uk';
import zhCN from '../../i18n/locales/zh-CN';
import zhTW from '../../i18n/locales/zh-TW';

type Node = Record<string, unknown>;

const locales: Record<string, Node> = {
  en,
  de,
  es,
  fr,
  it: it_,
  ja,
  ko,
  nl,
  'pt-BR': ptBR,
  ru,
  sv,
  tr,
  uk,
  'zh-CN': zhCN,
  'zh-TW': zhTW,
};

const codes = Object.keys(locales);

const get = (locale: Node, path: string): unknown =>
  path.split('.').reduce<unknown>((node, part) => (node as Node | undefined)?.[part], locale);

const placeholders = (value: string): string[] => (value.match(/\{\{\s*\w+\s*\}\}/g) ?? []).sort();

describe('file preview i18n (#2976)', () => {
  // check:i18n compares key sets, placeholders and identical-to-en values; it cannot
  // see that a translation still describes the old STL-only behaviour. These two keys
  // were reworded when the bulk action learned to render PDFs, so every locale has to
  // name both formats.
  it.each(codes)('%s names both STL and PDF in the bulk-thumbnail strings', (code) => {
    const values = [
      get(locales[code], 'fileManager.generateThumbnailsForMissing'),
      get(locales[code], 'fileManager.toast.noStlMissingThumbnails'),
    ];
    for (const value of values) {
      expect(typeof value).toBe('string');
      expect(value as string).toMatch(/STL/);
      expect(value as string).toMatch(/PDF/);
    }
  });

  // PreviewModalShell labels the fullscreen button from fileManager.preview.*, so the
  // modelViewer pair that predated the shared shell is dead weight every locale would
  // otherwise have to keep translating.
  it.each(codes)('%s has no orphaned modelViewer fullscreen labels', (code) => {
    const modelViewer = get(locales[code], 'modelViewer') as Node;
    expect(modelViewer).toBeTruthy();
    expect(modelViewer).not.toHaveProperty('fullscreen');
    expect(modelViewer).not.toHaveProperty('exitFullscreen');
    expect(typeof get(locales[code], 'fileManager.preview.fullscreen')).toBe('string');
    expect(typeof get(locales[code], 'fileManager.preview.exitFullscreen')).toBe('string');
  });

  it.each(codes.filter((c) => c !== 'en'))(
    '%s translates every fileManager.preview key and keeps its interpolations',
    (code) => {
      const reference = get(en, 'fileManager.preview') as Record<string, string>;
      const translated = get(locales[code], 'fileManager.preview') as Record<string, string>;
      expect(Object.keys(translated)).toEqual(Object.keys(reference));
      for (const [key, enValue] of Object.entries(reference)) {
        expect(translated[key], key).toBeTypeOf('string');
        expect(translated[key], key).not.toBe(enValue);
        expect(placeholders(translated[key]), key).toEqual(placeholders(enValue));
      }
    },
  );
});
