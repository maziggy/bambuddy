import { hash_fnv1a32, random_mulberry32 } from '../utils/random';

/* Enhanced filament-colour rendering helpers (#1154).
 *
 * Pure (non-component) exports that drive `<FilamentSwatch>` and any caller
 * that needs the same composed background as a CSS string. Lives in its own
 * file so `FilamentSwatch.tsx` can stay component-only and satisfy the
 * `react-refresh/only-export-components` ESLint rule.
 *
 * Inputs the swatch composes:
 *   1. `rgba`        — RRGGBBAA hex (the Bambu/AMS canonical form)
 *   2. `extraColors` — comma-separated 6/8-char hex stops; turns the swatch
 *                      into a gradient. Conic when either `subtype` or
 *                      `effectType` is `multicolor`, otherwise linear.
 *   3. `effectType`  — visual variant. Some carry a CSS overlay (sparkle,
 *                      wood, marble, glow, matte, silk, galaxy, metal),
 *                      others are categorical labels only.
 *
 * Alpha < 0xFF on any layer is shown against a checkerboard so the user can
 * actually see the transparency they configured.
 */

export type FilamentEffect =
  // Surface effects with their own CSS overlay
  | 'sparkle'
  | 'wood'
  | 'marble'
  | 'glow'
  | 'matte'
  // Sheen / finish variants (categorical labels; some carry an overlay)
  | 'silk'
  | 'galaxy'
  | 'rainbow'
  | 'metal'
  | 'translucent'
  // Multi-colour structures (mostly drive the colour-layer choice)
  | 'gradient'
  | 'dual-color'
  | 'tri-color'
  | 'multicolor';

/** Intended target swatch type for effect rendering. */
export type SwatchType = 'table' | 'preview' | 'card' | 'bar' | 'groupheader';
export type EffectLayer = string | string[];

/** Presets for the different swatch types */
export const SWATCH_TYPE_PRESETS: Readonly<Record<SwatchType, {
  dotCount: number;
  dotScale: number;
}>> = {
  table: { dotCount: 5, dotScale: 1 },
  preview: { dotCount: 8, dotScale: 1.5 },
  card: { dotCount: 40, dotScale: 2 },
  bar: { dotCount: 20, dotScale: 2 },
  groupheader: { dotCount: 80, dotScale: 2 },
};

/** Public list of all known effect/variant values, in display order. Shared
 *  by the spool form's ColorSection dropdown and the colour-catalog editor
 *  so the two stay in lockstep. Each value pairs with an i18n key under
 *  `inventory.colorEffect.<value>` (kebab → camel: `dualColor`/`triColor`). */
export const FILAMENT_EFFECT_OPTIONS: ReadonlyArray<{
  value: '' | FilamentEffect;
  labelKey: string;
}> = [
  { value: '', labelKey: 'inventory.colorEffect.none' },
  // Surface effects
  { value: 'sparkle', labelKey: 'inventory.colorEffect.sparkle' },
  { value: 'wood', labelKey: 'inventory.colorEffect.wood' },
  { value: 'marble', labelKey: 'inventory.colorEffect.marble' },
  { value: 'glow', labelKey: 'inventory.colorEffect.glow' },
  { value: 'matte', labelKey: 'inventory.colorEffect.matte' },
  // Sheen / finish
  { value: 'silk', labelKey: 'inventory.colorEffect.silk' },
  { value: 'galaxy', labelKey: 'inventory.colorEffect.galaxy' },
  { value: 'rainbow', labelKey: 'inventory.colorEffect.rainbow' },
  { value: 'metal', labelKey: 'inventory.colorEffect.metal' },
  { value: 'translucent', labelKey: 'inventory.colorEffect.translucent' },
  // Multi-colour structures
  { value: 'gradient', labelKey: 'inventory.colorEffect.gradient' },
  { value: 'dual-color', labelKey: 'inventory.colorEffect.dualColor' },
  { value: 'tri-color', labelKey: 'inventory.colorEffect.triColor' },
  { value: 'multicolor', labelKey: 'inventory.colorEffect.multicolor' },
];

// Checkerboard pattern shown beneath the colour layer so alpha < FF is
// actually visible to the user. Kept as a pure gradient (no position/size)
// so the value parses cleanly inside `background-image:` everywhere.
//
// Density is controlled by ``CHECKERBOARD_TILE_SIZE`` applied as
// ``background-size`` on this layer specifically — without that,
// ``backgroundSize: 'cover'`` would stretch the gradient to the whole
// element and a card-sized swatch would only show 4 huge cells (#1154
// follow-up reporter feedback). Per-layer sizing is supported by every
// modern browser via comma-separated ``background-size``.
export const CHECKERBOARD_BG =
  'repeating-conic-gradient(#979797 0% 25%, #f5f5f5 0% 50%)';
export const CHECKERBOARD_TILE_SIZE = '12px 12px';

/** Optional CSS overlay layer for variants that have a visual treatment.
 *  Variants without an entry are categorical labels only — they don't paint
 *  an overlay, just sit in the data. `multicolor` is special: its visual
 *  effect is to switch the colour layer to a conic-gradient (see
 *  `buildColorLayer`), not to add an overlay layer. */
export const EFFECT_OVERLAYS: Partial<
  Record<FilamentEffect, (effectSeed?: number, effectSize?: SwatchType) => EffectLayer>
> = {
  // Sparkle: bright flecks — positions seeded from spool color+extracolors+subtype+effectType.
  // to give identical spools the same sparkle pattern while different spools get different patterns. 
  sparkle: (spoolSeed = 0, effectSize = 'table') => {
    const rand = random_mulberry32(spoolSeed);
    const preset = SWATCH_TYPE_PRESETS[effectSize] ?? SWATCH_TYPE_PRESETS.table;
    const sparks: string[] = [];
    for (let i = 0; i < preset.dotCount; i++) {
      const x = rand.intBetween(1, 99);
      const y = rand.intBetween(1, 99);
      const s = rand.floatBetween(1.0, preset.dotScale);
      const a = rand.floatBetween(0.65, 1.0);
      sparks.push(`radial-gradient(circle at ${x}% ${y}%, rgba(255,248,220,${a}) 0 ${s/2}px, transparent ${s}px)`);
    }
    return sparks;
  },
  // Wood: subtle horizontal banding to mimic grain.
  wood: () =>
    'repeating-linear-gradient(90deg, ' +
    'rgba(0,0,0,0.18) 0 1px, transparent 1px 6px, ' +
    'rgba(0,0,0,0.08) 6px 7px, transparent 7px 12px)',
  // Marble: soft diagonal swirls.
  marble: () =>
    'repeating-linear-gradient(135deg, rgba(255,255,255,0.18) 0 2px, transparent 2px 8px), ' +
    'repeating-linear-gradient(45deg, rgba(0,0,0,0.10) 0 1px, transparent 1px 7px)',
  // Glow: bright center fade — visual hint for glow-in-the-dark filaments.
  glow: () =>
    'radial-gradient(circle at 50% 50%, rgba(255,255,255,0.35) 0%, rgba(255,255,255,0) 70%)',
  // Matte: very subtle inset shadow to flatten the highlight.
  matte: () =>
    'linear-gradient(180deg, rgba(0,0,0,0.10) 0%, rgba(0,0,0,0) 50%, rgba(0,0,0,0.10) 100%)',
  // Silk / Galaxy: diagonal sheen to suggest the lustrous finish those
  // filaments have. Galaxy uses a slightly stronger highlight.
  silk: () =>
    'linear-gradient(110deg, rgba(255,255,255,0) 30%, rgba(255,255,255,0.30) 50%, rgba(255,255,255,0) 70%)',
  galaxy: () =>
    'linear-gradient(110deg, rgba(255,255,255,0) 25%, rgba(255,255,255,0.40) 50%, rgba(255,255,255,0) 75%)',
  // Metal: brushed-metal look via tight horizontal striations + soft sheen.
  metal: () =>
    'repeating-linear-gradient(90deg, rgba(255,255,255,0.10) 0 1px, transparent 1px 3px), ' +
    'linear-gradient(180deg, rgba(255,255,255,0.18) 0%, rgba(0,0,0,0.18) 100%)',
};

/** Normalize a hex token (with or without `#`, 6 or 8 chars) → CSS hex string. */
export function toCssHex(token: string): string | null {
  const t = token.trim().replace(/^#/, '');
  if (t.length !== 6 && t.length !== 8) return null;
  if (!/^[0-9a-fA-F]+$/.test(t)) return null;
  return `#${t}`;
}

/** Parse extra_colors string into an array of CSS hex strings. */
export function parseStops(extra: string | null | undefined): string[] {
  if (!extra) return [];
  return extra
    .split(',')
    .map((s) => toCssHex(s))
    .filter((s): s is string => Boolean(s));
}

/** Build the colour layer (gradient or solid) given rgba + stops + subtype/effect.
 *  - ``multicolor`` (subtype OR effect): conic gradient — the swatch reads as
 *    a colour wheel pie, distinct from a stripe.
 *  - ``dual-color`` / ``tri-color`` (effect): hard-split horizontal bars with
 *    no diagonal blend. A 2-stop Dual Color renders as left/right halves of
 *    distinct colour, matching the way real dual-colour spools look on the
 *    reel — without this, Gradient and Dual Color produced the same diagonal
 *    blend and were visually indistinguishable (#1154 follow-up).
 *  - everything else (``gradient`` and the default): a smooth 135° linear
 *    gradient across the stops, the original visual for blended-stop spools.
 */
export function buildColorLayer(
  rgba: string | null | undefined,
  stops: string[],
  subtype: string | null | undefined,
  effectType?: string | null,
): string {
  const baseHex = rgba ? toCssHex(rgba) : null;
  // No stops → solid colour (or default grey when nothing is set at all).
  if (stops.length === 0) {
    return `linear-gradient(${baseHex ?? '#808080'}, ${baseHex ?? '#808080'})`;
  }
  // With stops we ignore the single rgba and gradient across the stops.
  const allStops = stops.length === 1 ? [stops[0], stops[0]] : stops;
  const subtypeLower = (subtype ?? '').toLowerCase();
  const effectLower = (effectType ?? '').toLowerCase();
  if (subtypeLower === 'multicolor' || effectLower === 'multicolor') {
    const n = allStops.length;
    const segments = allStops
      .map((c, i) => {
        const start = ((i / n) * 360).toFixed(3);
        const end = (((i + 1) / n) * 360).toFixed(3);
        return `${c} ${start}deg ${end}deg`;
      })
      .join(', ');
    return `conic-gradient(from 0deg, ${segments})`;
  }
  if (effectLower === 'dual-color' || effectLower === 'tri-color') {
    // Equal-width hard-split bars: each stop occupies its own contiguous
    // segment with no blend across the boundary. CSS double-position stops
    // (``c X% Y%``) collapse the transition zone to zero so the colour
    // change is a hard vertical line rather than a diagonal smear.
    const n = allStops.length;
    const segments = allStops
      .map((c, i) => {
        const start = ((i / n) * 100).toFixed(3);
        const end = (((i + 1) / n) * 100).toFixed(3);
        return `${c} ${start}% ${end}%`;
      })
      .join(', ');
    return `linear-gradient(to right, ${segments})`;
  }
  return `linear-gradient(135deg, ${allStops.join(', ')})`;
}

/** Resolve the CSS overlay string for an effect key. */
export function resolveEffectOverlay(
  effectKey: string,
  effectSize: SwatchType,
  effectSeed?: number,
): EffectLayer | null {
  const fn = EFFECT_OVERLAYS[effectKey as FilamentEffect];
  return fn ? fn(effectSeed, effectSize) : null;
}

/** Public helper: produce a CSS background-image value (list of layered
 *  <image>s) for a filament, for callers that want to paint a banner or
 *  large area instead of using the swatch element. Returns a
 *  ``CSSProperties``-compatible object with ``backgroundImage`` and
 *  ``backgroundSize`` so the checkerboard underlayer keeps a fixed tile
 *  density regardless of element size — without per-layer sizing, a
 *  card-sized banner only shows 4 huge checker cells.
 */
export function buildFilamentBackground(opts: {
  effectSize: SwatchType;
  rgba?: string | null;
  extraColors?: string | null;
  effectType?: FilamentEffect | string | null;
  subtype?: string | null;
}): { backgroundImage: string; backgroundSize: string } {
  const stops = parseStops(opts.extraColors);
  const colorLayer = buildColorLayer(opts.rgba, stops, opts.subtype, opts.effectType);
  const effectSeed = hash_fnv1a32(opts.rgba, opts.extraColors, opts.subtype, opts.effectType);
  const effectLayer =
    typeof opts.effectType === 'string'
      ? resolveEffectOverlay(opts.effectType, opts.effectSize, effectSeed)
      : null;
  // Layer order (top → bottom): effect overlay → colour layer → checkerboard.
  // Per-layer background-size: 'cover' on the painted layers (so they fill
  // the element) and the fixed tile size on the checkerboard so the cell
  // count scales with the element rather than the element scaling the cells.
  const layers: { image: string; size: string }[] = [];
  if (effectLayer) {
    const effectImages = Array.isArray(effectLayer) ? effectLayer : [effectLayer];
    effectImages.forEach((image) => {
      layers.push({ image, size: 'cover' });
    });
  }
  layers.push({ image: colorLayer, size: 'cover' });
  layers.push({ image: CHECKERBOARD_BG, size: CHECKERBOARD_TILE_SIZE });

  return {
    backgroundImage: layers.map((l) => l.image).join(', '),
    backgroundSize: layers.map((l) => l.size).join(', '),
  };
}
