import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import en from '../../i18n/locales/en';

// The pages' tests render without i18next, so a key missing from the locale
// files only shows as its fallback (or the bare key) and no test sees it;
// the parity check passes too when every locale lacks it. Every billing key
// these files name literally has to resolve in en.ts (#3581 review: the
// notification's keys were once removed with similarly named settings keys).
const SOURCES = [
  'pages/SettingsPage.tsx',
  'pages/FinancePage.tsx',
  'components/AddNotificationModal.tsx',
  'components/NotificationProviderCard.tsx',
];

function resolveKey(path: string): unknown {
  return path.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], en);
}

describe('billing translation keys', () => {
  const keys = new Set<string>();
  for (const file of SOURCES) {
    const source = readFileSync(resolve(__dirname, '../..', file), 'utf8');
    for (const match of source.matchAll(/\bt\(\s*'((?:settings|notifications)\.billing\w+)'/g)) keys.add(match[1]);
  }

  it('finds the keys it checks', () => {
    expect(keys).toContain('settings.billingChargePolicy');
    expect(keys).toContain('settings.billingFailedFilamentProportional');
    expect(keys).toContain('notifications.billingChargeFailedLabel');
    expect(keys).toContain('notifications.billingChargeFailedDescription');
  });

  it.each([...keys])('%s is a string in en.ts', (key) => {
    expect(typeof resolveKey(key)).toBe('string');
  });
});
