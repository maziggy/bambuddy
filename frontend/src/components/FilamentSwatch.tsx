import React, { useMemo } from 'react';
import {
  CHECKERBOARD_BG,
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
  // Set as `background-image` (not the `background` shorthand) so the value
  // remains a pure list-of-images that browsers and test runners parse cleanly.
  const backgroundImage = [effectLayer, colorLayer, CHECKERBOARD_BG]
    .filter((layer): layer is string => Boolean(layer))
    .join(', ');

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
      style={{ backgroundImage, backgroundSize: 'cover', ...style }}
      title={computedTitle}
    />
  );
}

export default FilamentSwatch;
