/**
 * A control's label is not selectable text (#3577): on a phone a long or
 * moving tap selected it instead of pressing the control. jsdom applies no
 * stylesheet, so the rule is checked in the source.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');

const rule = (selector: RegExp) => {
  const m = css.match(new RegExp(`${selector.source}\\s*\\{([^}]*)\\}`));
  return m?.[1] ?? '';
};

describe('button text selection', () => {
  it('turns selection and the iOS callout off on buttons and button-like elements', () => {
    const body = rule(/button,\s*\[role="button"\]/);
    expect(body).toMatch(/(^|[^-])user-select:\s*none/);
    expect(body).toMatch(/-webkit-user-select:\s*none/);
    expect(body).toMatch(/-webkit-touch-callout:\s*none/);
  });

  it('keeps fields inside a button-like row selectable', () => {
    const body = rule(/\[role="button"\] :is\(input, textarea, \[contenteditable="true"\]\)/);
    expect(body).toMatch(/(^|[^-])user-select:\s*text/);
    expect(body).toMatch(/-webkit-user-select:\s*text/);
  });
});
