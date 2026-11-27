import type { InventorySpool } from '../api/client';

// Synthesize a spool-shaped object for a group's collapsed header row: the
// quantity fields (label_weight, weight_used, core_weight) are summed across
// members so the header shows group totals (#1368), while identity fields are
// carried from the first member — all members share them, they are the group
// key. The expanded per-spool rows keep using the real per-member spools.
// A scale reading belongs to one spool, so the header has none: compared with
// the summed weight, the first member's reading made every weighed group show
// a mismatch, and its Sync button re-synced only that first spool.
export function aggregateGroupSpool(spools: InventorySpool[]): InventorySpool {
  const base = spools[0];
  let labelWeight = 0;
  let weightUsed = 0;
  let coreWeight = 0;
  for (const s of spools) {
    labelWeight += s.label_weight;
    weightUsed += s.weight_used;
    coreWeight += s.core_weight;
  }
  return {
    ...base,
    label_weight: labelWeight,
    weight_used: weightUsed,
    core_weight: coreWeight,
    last_scale_weight: null,
    last_weighed_at: null,
  };
}
