/**
 * The page names the language it is shown in and opts out of browser
 * translation, which crashes React when it rewrites text nodes (#3647).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, afterEach } from 'vitest';
import i18n from '../../i18n';

afterEach(async () => {
  await i18n.changeLanguage('en');
});

describe('html lang (#3647)', () => {
  it('follows the language in use', async () => {
    await i18n.changeLanguage('de');
    expect(document.documentElement.lang).toBe('de');

    await i18n.changeLanguage('pt-BR');
    expect(document.documentElement.lang).toBe('pt-BR');

    await i18n.changeLanguage('en');
    expect(document.documentElement.lang).toBe('en');
  });

  it('opts the page out of browser translation', () => {
    const html = readFileSync(resolve(__dirname, '../../../index.html'), 'utf-8');
    expect(html).toMatch(/<html[^>]*\btranslate="no"/);
    expect(html).toContain('<meta name="google" content="notranslate" />');
  });
});
