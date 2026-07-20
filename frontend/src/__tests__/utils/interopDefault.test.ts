/**
 * Unit tests for resolveInteropDefault (#2616).
 *
 * The browser build resolved react-simple-keyboard's CommonJS default import to
 * the module namespace object ({ KeyboardReact, default }) instead of the
 * component, so <Keyboard> threw React #130 ("got: object"). vitest's own interop
 * happens to hand back the component, so a render test can't catch the
 * regression — these assert the resolver directly against both shapes.
 */

import { describe, it, expect } from 'vitest';
import { resolveInteropDefault } from '../../utils/interopDefault';

const Comp = function Keyboard() {
  return null;
};

describe('resolveInteropDefault', () => {
  it('returns a bare function component unchanged', () => {
    expect(resolveInteropDefault(Comp)).toBe(Comp);
  });

  it('unwraps the CJS interop namespace object via .default (the #2616 shape)', () => {
    const moduleObject = { default: Comp, KeyboardReact: Comp };
    expect(resolveInteropDefault(moduleObject, ['KeyboardReact'])).toBe(Comp);
  });

  it('falls back to a named export when there is no .default', () => {
    const moduleObject = { KeyboardReact: Comp };
    expect(resolveInteropDefault(moduleObject, ['KeyboardReact'])).toBe(Comp);
  });

  it('leaves a forwardRef/memo object (with $$typeof) untouched', () => {
    const forwardRefLike = { $$typeof: Symbol.for('react.forward_ref'), render: Comp };
    expect(resolveInteropDefault(forwardRefLike)).toBe(forwardRefLike);
  });

  it('returns a string tag unchanged', () => {
    expect(resolveInteropDefault('div')).toBe('div');
  });

  it('returns the value unchanged when nothing usable is found', () => {
    const opaque = { something: 1 };
    expect(resolveInteropDefault(opaque, ['KeyboardReact'])).toBe(opaque);
  });
});
