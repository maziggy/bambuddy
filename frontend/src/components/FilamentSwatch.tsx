import React, { useMemo } from 'react';
import {
  parseStops,
  buildFilamentBackground,
  type FilamentEffect,
  type SwatchType,
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
  /** Tune effect appearance based on target div size. */
  effectSize: SwatchType;
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
  effectSize,
}: FilamentSwatchProps) {
  const stops = useMemo(() => parseStops(extraColors), [extraColors]);

  const filamentBackground = useMemo(
    () => buildFilamentBackground({ effectSize, rgba, extraColors, effectType, subtype }),
    [effectSize, rgba, extraColors, effectType, subtype]
  );
  const backgroundImage = filamentBackground.backgroundImage;
  const backgroundSize = filamentBackground.backgroundSize;

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
