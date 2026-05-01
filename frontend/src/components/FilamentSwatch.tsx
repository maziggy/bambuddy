import React, { useMemo } from 'react';
import {
  CHECKERBOARD_BG,
  CHECKERBOARD_TILE_SIZE,
  EFFECT_OVERLAYS,
  buildColorLayer,
  parseStops,
  type FilamentEffect,
} from './filamentSwatchHelpers';

/** Shared filament-colour swatch. See `filamentSwatchHelpers.ts` for the
 *  pure helpers + constants this component composes. */

export interface FilamentSwatchProps {
  /** RRGGBBAA hex without `#` (Bambu/AMS canonical). Falls back to grey when null. */
  rgba?: string | null;
  /** Comma-separated 6/8-char hex tokens (no `#`). Empty/undefined → solid. */
  extraColors?: string | null;
  /** Visual effect overlay. */
  effectType?: FilamentEffect | string | null;
  /** When `Multicolor`, a conic gradient is used instead of linear. */
  subtype?: string | null;
  /** Tailwind size token applied to width/height (e.g. `w-5 h-5`). Default: `w-5 h-5`. */
  className?: string;
  /** Override the rounded shape — defaults to `rounded-full` (circular). */
  shape?: 'circle' | 'pill' | 'square';
  /** Optional inline style overrides (e.g. height of a card banner). */
  style?: React.CSSProperties;
  /** Native title attribute for hover tooltip. */
  title?: string;
}

export function FilamentSwatch({
  rgba,
  extraColors,
  effectType,
  subtype,
  className = 'w-5 h-5',
  shape = 'circle',
  style,
  title,
}: FilamentSwatchProps) {
  const stops = useMemo(() => parseStops(extraColors), [extraColors]);
  const colorLayer = useMemo(
    () => buildColorLayer(rgba, stops, subtype, effectType),
    [rgba, stops, subtype, effectType],
  );

  const effectKey =
    typeof effectType === 'string' && effectType in EFFECT_OVERLAYS
      ? (effectType as FilamentEffect)
      : null;
  const effectLayer = effectKey ? EFFECT_OVERLAYS[effectKey] ?? null : null;

  // Layer order (top → bottom): effect overlay → colour layer → checkerboard.
  // Per-layer background-size: 'cover' on the painted layers, fixed tile on
  // the checkerboard so its cell density doesn't scale with element size
  // (a card-sized swatch with `cover` checker would render only 4 huge
  // cells; #1154 follow-up).
  const layers: { image: string; size: string }[] = [];
  if (effectLayer) layers.push({ image: effectLayer, size: 'cover' });
  layers.push({ image: colorLayer, size: 'cover' });
  layers.push({ image: CHECKERBOARD_BG, size: CHECKERBOARD_TILE_SIZE });
  const backgroundImage = layers.map((l) => l.image).join(', ');
  const backgroundSize = layers.map((l) => l.size).join(', ');

  const shapeClass =
    shape === 'circle' ? 'rounded-full' : shape === 'pill' ? 'rounded-full' : 'rounded';

  // Compute a sensible title fallback — solid hex or gradient summary.
  const computedTitle =
    title ??
    (stops.length > 0
      ? stops.join(', ')
      : rgba
        ? `#${rgba.substring(0, 6)}`
        : undefined);

  return (
    <span
      data-testid="filament-swatch"
      className={`${className} ${shapeClass} border border-black/20 inline-block flex-shrink-0`}
      style={{ backgroundImage, backgroundSize, ...style }}
      title={computedTitle}
    />
  );
}

export default FilamentSwatch;
