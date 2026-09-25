/**
 * The Safari 16.0 guard, at the seam where the two halves meet (#2976).
 *
 * `vite.config.ts` lowers emitted script *assets* (pdf.js's `?url` worker) to
 * `build.target` before they are written, and `scripts/check-browser-baseline.mjs`
 * then greps the written files for syntax the baseline cannot parse. The
 * lowering is what removes `static {}` blocks; the grep is what catches regex
 * lookbehind, which no target lowers.
 *
 * The risk the two halves create together is that the lowering *hides* a hit
 * from the grep: oxc rewrites an unsupported regex literal into a
 * `RegExp("...")` call, which would still throw on iOS 16.0-16.3 — at
 * construction time instead of parse time. It does not hide it today, because
 * the scanner is a text grep and the pattern survives verbatim inside the
 * string argument. These tests pin exactly that, so a future toolchain that
 * escapes or rewrites the pattern fails here instead of shipping a green tick
 * over a broken bundle.
 */

import { describe, it, expect } from 'vitest';
import { minify, transformWithOxc } from 'vite';

// Kept in step with FORBIDDEN in scripts/check-browser-baseline.mjs.
const LOOKBEHIND = /\(\?<[=!]/;
const STATIC_BLOCK = /\bstatic\s*\{/;

/** The exact pipeline `lowerEmittedScriptAssets` puts an emitted asset through. */
async function lowerAsset(source: string): Promise<string> {
  const fileName = 'pdf.worker.min.mjs';
  const lowered = await transformWithOxc(source, fileName, { target: 'safari16' });
  const minified = await minify(fileName, lowered.code, { module: true });
  return minified.code;
}

describe('Safari 16.0 baseline guard', () => {
  it('lowers a class static initialisation block out of an emitted asset', async () => {
    const out = await lowerAsset('export class K { static { K.registry = new Map(); } }');

    expect(STATIC_BLOCK.test(out)).toBe(false);
  });

  it('leaves a lookbehind assertion visible to the scanner after lowering', async () => {
    const out = await lowerAsset('export const re = /(?<=a)b/g; export const neg = /(?<!x)y/;');

    // Lowered to a constructor call rather than dropped...
    expect(out).toContain('RegExp');
    // ...with the pattern still spelled out, which is what the grep reads.
    expect(LOOKBEHIND.test(out)).toBe(true);
    expect(out.match(new RegExp(LOOKBEHIND, 'g'))).toHaveLength(2);
  });
});
