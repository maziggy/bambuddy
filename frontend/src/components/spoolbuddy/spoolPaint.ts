import type { CSSProperties } from 'react';
import { buildFilamentBackground, parseStops, type SwatchType } from '../filamentSwatchHelpers';

/** The colour data a spool carries, as the Filament page reads it. */
export interface SpoolPaintSource {
  rgba: string | null;
  extra_colors?: string | null;
  effect_type?: string | null;
  subtype?: string | null;
}

/** True when the spool has something a flat fill cannot show: extra colour
 *  stops or a surface effect. Plain spools keep the flat SVG disc (#3033). */
export function hasFilamentPaint(spool: SpoolPaintSource | null | undefined): spool is SpoolPaintSource {
  return !!spool && (parseStops(spool.extra_colors).length > 0 || !!spool.effect_type);
}

/** Background for a flat colour dot, painted with the spool's extra colours
 *  and effect; `null` for a plain spool, so the caller keeps its own style. */
export function spoolSwatchStyle(
  spool: SpoolPaintSource | null | undefined,
  effectSize: SwatchType = 'table',
): CSSProperties | null {
  if (!hasFilamentPaint(spool)) return null;
  return {
    ...buildFilamentBackground({
      effectSize,
      rgba: spool.rgba,
      extraColors: spool.extra_colors,
      effectType: spool.effect_type,
      subtype: spool.subtype,
    }),
    backgroundPosition: 'center',
  };
}
