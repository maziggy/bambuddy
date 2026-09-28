import { useMemo } from 'react';
import { buildFilamentBackground, type SwatchType } from '../filamentSwatchHelpers';
import { hasFilamentPaint, type SpoolPaintSource } from './spoolPaint';

/** A spool disc painted with the spool's colours, absolutely placed inside a
 *  square box. `inset` is the fraction of the box left clear on each side. */
export function SpoolPaint({
  spool,
  inset,
  size,
  border,
}: {
  spool: SpoolPaintSource;
  inset: number;
  size: number;
  border?: string;
}) {
  // The effect overlays are tuned per swatch size; a kiosk disc of 80px and
  // up reads like a card banner, anything smaller like the preview swatch.
  const effectSize: SwatchType = size >= 80 ? 'card' : 'preview';
  const background = useMemo(
    () =>
      buildFilamentBackground({
        effectSize,
        rgba: spool.rgba,
        extraColors: spool.extra_colors,
        effectType: spool.effect_type,
        subtype: spool.subtype,
      }),
    [effectSize, spool.rgba, spool.extra_colors, spool.effect_type, spool.subtype],
  );
  const offset = `${inset * 100}%`;
  return (
    <div
      data-testid="spool-paint"
      className="absolute rounded-full box-border"
      style={{
        top: offset,
        left: offset,
        right: offset,
        bottom: offset,
        ...background,
        backgroundPosition: 'center',
        border,
      }}
    />
  );
}

/** The darker inner ring of a disc. A 15% black overlay is what
 *  `brightness(0.85)` does to the flat disc, and it lets the colours run
 *  continuously under both rings instead of repeating each ring's own copy. */
export function SpoolShade({ inset }: { inset: number }) {
  const offset = `${inset * 100}%`;
  return (
    <div
      data-testid="spool-shade"
      className="absolute rounded-full"
      style={{ top: offset, left: offset, right: offset, bottom: offset, backgroundColor: 'rgba(0, 0, 0, 0.15)' }}
    />
  );
}

interface SpoolIconProps {
  color: string;
  isEmpty: boolean;
  size?: number;
  /** The spool itself. When it has extra colours or an effect, the disc is
   *  painted with them instead of the flat `color`. */
  spool?: SpoolPaintSource | null;
}

export function SpoolIcon({ color, isEmpty, size = 32, spool }: SpoolIconProps) {
  if (isEmpty) {
    return (
      <div
        className="rounded-full border-2 border-dashed border-zinc-500 flex items-center justify-center"
        style={{ width: size, height: size }}
      >
        <div className="w-2 h-2 rounded-full bg-zinc-600" />
      </div>
    );
  }

  if (hasFilamentPaint(spool)) {
    // Same geometry as the SVG below (viewBox 32: outer r=14, inner r=11).
    return (
      <div className="relative shrink-0" style={{ width: size, height: size }}>
        <SpoolPaint
          spool={spool}
          size={size}
          inset={2 / 32}
          border={`${(1.5 * size) / 32}px solid rgba(255, 255, 255, 0.7)`}
        />
        <SpoolShade inset={5 / 32} />
      </div>
    );
  }

  return (
    <svg width={size} height={size} viewBox="0 0 32 32">
      {/* Outer ring with white stroke for visibility */}
      <circle cx="16" cy="16" r="14" fill={color} stroke="white" strokeWidth="1.5" strokeOpacity="0.7" />
      {/* Inner shadow/depth */}
      <circle cx="16" cy="16" r="11" fill={color} style={{ filter: 'brightness(0.85)' }} />
    </svg>
  );
}
