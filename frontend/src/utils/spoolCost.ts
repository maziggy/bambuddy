import type { InventorySpool } from '../api/client';

/**
 * The cost per kg to show for a spool: the rate prints off it are charged
 * (SM-C8). A Spoolman spool's own price wins when it is above 0; otherwise
 * prints pay its filament's catalogue price (``effective_cost_per_kg``), as
 * the charge treats a missing or 0 spool price as "not set". Without one, the
 * spool's own value stands, so a built-in spool priced 0 still shows 0.
 */
export function shownCostPerKg(
  spool: Pick<InventorySpool, 'cost_per_kg' | 'effective_cost_per_kg'>,
): { rate: number | null; fromFilament: boolean } {
  const own = spool.cost_per_kg;
  if (own != null && own > 0) return { rate: own, fromFilament: false };
  const charged = spool.effective_cost_per_kg;
  if (charged != null) return { rate: charged, fromFilament: true };
  return { rate: own ?? null, fromFilament: false };
}
