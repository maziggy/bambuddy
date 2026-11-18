/**
 * The archive card's print count in languages with more plural forms (#1634).
 *
 * ru, uk and pl pick _few for 2-4 and _many for 5+. With only _one/_other,
 * i18next finds no key for those counts and falls back to English.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import i18n from '../../i18n';

const LANGS = ['ru', 'uk', 'pl'] as const;

describe('archives.card.runsBadge plurals', () => {
  // Only English is bundled (#3175); load the others first.
  beforeAll(async () => {
    await i18n.loadLanguages([...LANGS]);
  });

  for (const lng of LANGS) {
    it(`${lng} renders its own text for 1, 3, 5 and 21 prints`, () => {
      const t = i18n.getFixedT(lng);
      for (const count of [1, 3, 5, 21]) {
        const badge = t('archives.card.runsBadge', { count });
        const title = t('archives.card.runsBadgeTitle', { count, successful: count, failed: 0 });
        expect(badge).toContain(String(count));
        expect(badge).not.toMatch(/\bprints?\b/);
        expect(title).not.toMatch(/\bprints?\b/);
      }
    });
  }
});
