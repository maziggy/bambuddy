/**
 * Only English is bundled; the other languages load when used (#3175).
 */
import { describe, it, expect, afterEach } from 'vitest';
import i18n, { localeBackend } from '../../i18n';
import en from '../../i18n/locales/en';
import de from '../../i18n/locales/de';

function read(language: string) {
  return new Promise<{ error: unknown; data: unknown }>((resolve) => {
    localeBackend.read(language, 'translation', (error, data) => resolve({ error, data }));
  });
}

afterEach(async () => {
  await i18n.changeLanguage('en');
});

describe('locale loading', () => {
  it('starts with English only', () => {
    expect(i18n.hasResourceBundle('en', 'translation')).toBe(true);
    expect(i18n.hasResourceBundle('ja', 'translation')).toBe(false);
    expect(i18n.t('nav.printers')).toBe(en.nav.printers);
  });

  it('hands i18next the file of the language asked for', async () => {
    const { error, data } = await read('de');

    expect(error).toBeNull();
    expect(data).toEqual(de);
  });

  it('answers an unknown language with nothing, so English stays the fallback', async () => {
    const { error, data } = await read('xx');

    expect(error).toBeNull();
    expect(data).toEqual({});
  });

  it('loads a language before switching to it', async () => {
    expect(i18n.hasResourceBundle('ja', 'translation')).toBe(false);

    await i18n.changeLanguage('ja');

    expect(i18n.language).toBe('ja');
    expect(i18n.hasResourceBundle('ja', 'translation')).toBe(true);
    expect(i18n.t('nav.printers')).not.toBe(en.nav.printers);
  });

  it('loads a regional language by its full code', async () => {
    await i18n.changeLanguage('pt-BR');

    expect(i18n.hasResourceBundle('pt-BR', 'translation')).toBe(true);
    expect(i18n.t('nav.printers')).not.toBe(en.nav.printers);
  });

  it('maps a browser region to its supported language', async () => {
    await i18n.changeLanguage('de-AT');

    expect(i18n.t('nav.printers')).toBe(de.nav.printers);
  });
});
