/**
 * Swedish strings for the outcome-confirmation stats (#1898).
 *
 * `kasserad` is a participle that agrees with what it describes: "1 kasserad",
 * "2 kasserade". i18next cannot pluralise stats.rejectedPrintsCount for us --
 * en.ts defines no _one/_other variants for the key, and the parity gate (rule
 * 3 in scripts/check-i18n-parity.mjs) forbids a locale introducing an _one key
 * that en lacks. So the Swedish sentence has to be number-neutral: the count
 * goes where no word has to agree with it, the way ru and uk already write the
 * same key.
 *
 * StatsPage renders this whenever rejected_prints > 0, so a count of exactly 1
 * is the first thing anyone sees after their first rejected print.
 */

import { describe, it, expect } from 'vitest';
import i18n from '../../i18n';

const t = i18n.getFixedT('sv');

describe('sv stats.rejectedPrintsCount', () => {
  it('reads grammatically when exactly one print was rejected', () => {
    const rendered = t('stats.rejectedPrintsCount', { rejected: 1 });

    expect(rendered).toContain('1');
    expect(rendered).not.toContain('{{');
    // The regression: a plural participle sitting straight after the count.
    expect(rendered).not.toMatch(/\b1\s+kasserade\b/i);
  });

  it('still reads grammatically for a plural count', () => {
    const rendered = t('stats.rejectedPrintsCount', { rejected: 7 });

    expect(rendered).toContain('7');
    expect(rendered).not.toContain('{{');
    // The mirror image: a singular participle sitting straight after the count.
    expect(rendered).not.toMatch(/\b7\s+kasserad\b/i);
  });
});
