import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { getColorName } from '../../utils/colors';

/** Strip a leading brand token (the first whitespace-separated word) from a
 *  resolved filament name so what remains can be matched against the color
 *  catalog's ``material`` column. Examples:
 *    "Bambu PLA Matte"   → "PLA Matte"
 *    "PolyLite ABS"      → "ABS"
 *    "Bambu PLA-CF"      → "PLA-CF"
 *    "PLA"               → "PLA"        (no brand to strip; pass through)
 *    "Devil Design PLA"  → "Design PLA" (won't match catalog → falls back
 *                                        to priority-order answer, no regression)
 *  Never returns ``""`` — the empty-material case is the same priority
 *  fallback as omitting the param.
 */
export function extractMaterialHint(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length <= 1) return name.trim();
  return parts.slice(1).join(' ');
}

export interface FilamentLabel {
  /** Bambu sub-brand from the SKU lookup ("Bambu PLA Matte") falling back to
   *  the raw 3MF ``type`` ("PLA") when the SKU is unknown to both maps. */
  resolvedName: string;
  /** Material-disambiguated catalogue color ("Charcoal") falling back to
   *  ``getColorName(hex)`` when the by-material lookup hasn't resolved yet,
   *  returned null, or errored. Always non-empty. */
  colorLabel: string;
}

interface FilamentReqLike {
  type: string;
  color: string;
  tray_info_idx?: string;
}

/**
 * Resolve per-slot human-readable labels for the schedule modal's filament
 * panels (#1718). Both the model-mode ``FilamentOverride`` and the printer-
 * mode ``FilamentMapping`` consume this so the two panels render the same
 * sub-brand + disambiguated color for the same sliced 3MF. Extracted from
 * the inline implementation in ``FilamentOverride`` so the two callers can't
 * drift.
 *
 * Three queries back the resolution:
 *   - ``/cloud/builtin-filaments`` → Bambu factory SKU → name map (GFA01 →
 *     "Bambu PLA Matte" etc.).
 *   - ``/cloud/filament-id-map``   → user custom cloud preset SKU → name
 *     map (P-prefix). Wins over the builtin entry for the same id.
 *   - ``/inventory/colors/by-material`` (one ``useQuery`` per slot via
 *     ``useQueries``, keyed on hex + material hint) → catalog color name
 *     disambiguated by material context.
 *
 * Output is positional — ``labels[i]`` corresponds to ``reqs[i]``. Returns
 * an empty array when ``reqs`` is undefined / empty so callers can safely
 * index without a length check.
 */
export function useFilamentLabels(reqs: readonly FilamentReqLike[] | undefined): FilamentLabel[] {
  const { data: builtinFilaments } = useQuery({
    queryKey: ['builtin-filaments'],
    queryFn: () => api.getBuiltinFilaments(),
    staleTime: 5 * 60 * 1000,
  });
  const { data: cloudFilamentIdMap } = useQuery({
    queryKey: ['filament-id-map'],
    queryFn: () => api.getFilamentIdMap(),
    staleTime: 5 * 60 * 1000,
  });

  const filamentNameByIdx = useMemo(() => {
    const map: Record<string, string> = {};
    for (const f of builtinFilaments || []) {
      if (f.filament_id) map[f.filament_id] = f.name;
    }
    // Cloud user-preset map wins when both have the same id — the user-
    // authored name is the more specific label.
    for (const [fid, name] of Object.entries(cloudFilamentIdMap || {})) {
      if (fid && name) map[fid] = name;
    }
    return map;
  }, [builtinFilaments, cloudFilamentIdMap]);

  // Compute the per-slot (resolvedName, materialHint) pairs up-front so the
  // ``useQueries`` call below has a stable shape and the render path below
  // can reuse the same resolvedName without recomputing.
  const perSlot = useMemo(() => {
    return (reqs || []).map((req) => {
      const resolvedName = (req.tray_info_idx && filamentNameByIdx[req.tray_info_idx]) || req.type;
      return {
        resolvedName,
        materialHint: extractMaterialHint(resolvedName),
        color: req.color,
      };
    });
  }, [reqs, filamentNameByIdx]);

  const colorQueries = useQueries({
    queries: perSlot.map(({ color, materialHint }) => ({
      queryKey: ['color-by-material', color, materialHint],
      queryFn: () => api.getColorByMaterial(color, materialHint),
      // Treat empty colour as "nothing to look up" so we don't spam the
      // endpoint for entries the 3MF left blank.
      enabled: !!color,
      staleTime: 5 * 60 * 1000,
    })),
  });

  return perSlot.map(({ resolvedName, color }, idx) => {
    const disambiguated = colorQueries[idx]?.data?.color_name ?? null;
    return {
      resolvedName,
      colorLabel: disambiguated || getColorName(color),
    };
  });
}
