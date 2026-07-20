/**
 * Unwrap a default import that a bundler's CommonJS->ESM interop may have
 * wrapped in a module namespace object.
 *
 * Some CommonJS packages set `module.exports = { default: X, Named: X }`.
 * Depending on the bundler (and differing between the browser build, the test
 * runner, and Node's own ESM loader), `import X from 'pkg'` can hand you that
 * whole object instead of `X`. Rendering such an object as a React component
 * throws "Element type is invalid ... got: object" (React error #130) — see
 * #2616, where react-simple-keyboard's default import arrived as the namespace
 * object and crashed every SpoolBuddy screen on input focus.
 *
 * This returns the value unchanged when it is already a usable React element
 * type (a function/class component, a tag string, or an object carrying a React
 * `$$typeof` marker such as forwardRef/memo/lazy). Otherwise it tries `.default`
 * and then each of `fallbackKeys` in order, returning the first usable one, and
 * finally falls back to the original value.
 */
export function resolveInteropDefault<T = unknown>(value: unknown, fallbackKeys: string[] = []): T {
  if (isRenderableType(value)) return value as T;

  if (value !== null && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (isRenderableType(obj.default)) return obj.default as T;
    for (const key of fallbackKeys) {
      if (isRenderableType(obj[key])) return obj[key] as T;
    }
  }

  return value as T;
}

/** True when `v` is something React can render as an element type. */
function isRenderableType(v: unknown): boolean {
  if (typeof v === 'function' || typeof v === 'string') return true;
  // forwardRef / memo / lazy / context objects are valid element types and are
  // distinguished from a plain interop wrapper by their React `$$typeof` marker.
  return typeof v === 'object' && v !== null && '$$typeof' in v;
}
