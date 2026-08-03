// Tiered filament-preset list, shared by every picker that has to offer "all
// the filaments this install knows about".
//
// Lookup order is fixed across the app: local imported > Orca Cloud > Bambu
// Cloud > hardcoded built-in table. It mirrors ConfigureAmsSlotModal's picker
// and SliceModal's tier groups, so a filament the user sees in one place is
// named and ranked the same way in the others.
//
// The built-in table is the floor, not an equal source: it is a static list
// compiled into the backend, so it is the only tier that can never be empty
// and the only one that works with no cloud account and nothing imported.

import type { BuiltinFilament, LocalPreset, OrcaProfileMeta, SlicerSetting } from '../api/client';
import { parsePresetName, toFilamentId } from '../components/spool-form/utils';

export type FilamentPresetSource = 'local' | 'orca_cloud' | 'cloud' | 'builtin';

export interface FilamentPresetOption {
  /** Opaque, source-prefixed handle: ``local_12`` / ``orca_<uuid>`` / a Bambu
   *  cloud setting_id / ``builtin_GFA00``. Prefixes match the convention
   *  ConfigureAmsSlotModal already uses so the two can share resolvers. */
  id: string;
  name: string;
  source: FilamentPresetSource;
  /** The Bambu filament id this preset resolves to, when it is derivable
   *  without a network round trip. Empty for Bambu Cloud *user* presets, whose
   *  real filament_id only exists in the cloud detail — see
   *  resolveFilamentId. */
  filamentId: string;
  /** Material as the preset itself declares it, used to derive a generic
   *  filament id for tiers that carry no Bambu id of their own. */
  filamentType: string;
}

export interface FilamentPresetSources {
  localPresets?: LocalPreset[];
  orcaProfiles?: OrcaProfileMeta[];
  cloudSettings?: SlicerSetting[];
  builtinFilaments?: BuiltinFilament[];
}

/** Generic Bambu filament ids by material. Local and Orca Cloud presets carry
 *  no Bambu filament id, but the printer's calibration table is indexed by
 *  one, so the closest generic is what a calibration for such a preset has to
 *  be filed under. Same table and same fallback chain as the AMS slot
 *  configure flow — the two must agree or a profile created here won't match
 *  the slot configured there. */
const GENERIC_FILAMENT_IDS: Record<string, string> = {
  'PLA': 'GFL99', 'PLA-CF': 'GFL98', 'PLA SILK': 'GFL96', 'PLA HIGH SPEED': 'GFL95',
  'PETG': 'GFG99', 'PETG HF': 'GFG96', 'PETG-CF': 'GFG98', 'PCTG': 'GFG97',
  'ABS': 'GFB99', 'ASA': 'GFB98',
  'PC': 'GFC99',
  'PA': 'GFN99', 'PA-CF': 'GFN98', 'NYLON': 'GFN99',
  'TPU': 'GFU99',
  'PVA': 'GFS99', 'HIPS': 'GFS98',
  'PE': 'GFP99', 'PP': 'GFP97',
};

/** Resolve a material string to a generic Bambu filament id, trying the exact
 *  spelling before progressively stripping the suffixes slicer presets add
 *  ("-CF", "+", " HF"). Returns '' when nothing matches, which callers must
 *  treat as "not calibratable" rather than substituting a default — filing a
 *  calibration under the wrong material is worse than refusing. */
export function genericFilamentIdForMaterial(material: string | null | undefined): string {
  const m = (material || '').toUpperCase().trim();
  if (!m) return '';
  return GENERIC_FILAMENT_IDS[m]
    || GENERIC_FILAMENT_IDS[m.replace(/[-\s]?CF$/, '')]
    || GENERIC_FILAMENT_IDS[m.replace(/\+$/, '')]
    || GENERIC_FILAMENT_IDS[m.split(/[-\s]/)[0]]
    || '';
}

/** Strip the printer/nozzle suffix and the "# " custom-preset marker a preset
 *  name may carry, e.g. "Elegoo PLA+ @BBL X1C 0.4 nozzle" → "Elegoo PLA+". */
export function presetDisplayName(name: string): string {
  const withoutSuffix = name.replace(/@.+$/, '').trim();
  return withoutSuffix.startsWith('# ') ? withoutSuffix.slice(2).trim() : withoutSuffix;
}

const SOURCE_ORDER: Record<FilamentPresetSource, number> = {
  local: 0,
  orca_cloud: 1,
  cloud: 2,
  builtin: 3,
};

/**
 * Merge every filament source into one ranked list.
 *
 * Deduplication is deliberately asymmetric, because "the same name in two
 * tiers" means different things depending on which tiers:
 *
 *  - *Within* a tier, by resolved filament id or display name. This is what
 *    collapses the per-printer-model copies a cloud account carries —
 *    "Bambu PLA Basic @BBL X1C", "@BBL P1S", "@BBL A1" are one name once the
 *    suffix is stripped — and repeated imports of one filament for several
 *    printers.
 *
 *  - *Across* tiers, by id only. Two entries carrying the same id really are
 *    one record reached by two routes; two entries merely sharing a name are
 *    not. Imported presets and an Orca Cloud library overlap heavily by name
 *    (they are usually the same profiles, synced), and suppressing one for the
 *    other empties a tier the user curated on purpose. The heading says where
 *    each came from, which is the point of having tiers at all.
 *
 *  - *Into the built-in tier*, by name as well as by id. That tier is a static
 *    table of the same Bambu catalogue every other source also ships, so
 *    without a name check it echoes back everything above it. It exists to
 *    guarantee the list is never empty, not to be a fourth copy.
 */
export function buildFilamentPresetOptions(sources: FilamentPresetSources): FilamentPresetOption[] {
  const { localPresets, orcaProfiles, cloudSettings, builtinFilaments } = sources;
  const options: FilamentPresetOption[] = [];

  const nameKey = (name: string) => name.trim().toLowerCase();

  // Ids seen anywhere: a cloud setting_id, an Orca profile id, a resolved
  // filament id. Shared across tiers — an id collision is true identity.
  const claimedIds = new Set<string>();
  // Names seen, scoped to one tier, so two tiers can each list "Elegoo PLA+".
  const namesInTier = new Set<string>();
  // Every name any real source offered, consulted only by the built-in tier.
  const namesOffered = new Set<string>();

  const take = (source: FilamentPresetSource, name: string, ...ids: (string | undefined)[]): boolean => {
    const usableIds = ids.filter((k): k is string => !!k);
    if (usableIds.some(k => claimedIds.has(k))) return false;
    const scoped = `${source}|${nameKey(name)}`;
    if (namesInTier.has(scoped)) return false;
    usableIds.forEach(k => claimedIds.add(k));
    namesInTier.add(scoped);
    namesOffered.add(nameKey(name));
    return true;
  };

  // 1. Local imported presets. filament_id lives in the preset's setting JSON,
  // which the list endpoint doesn't return, so the generic material id is what
  // we can offer without a per-preset detail fetch.
  for (const lp of localPresets ?? []) {
    const name = presetDisplayName(lp.name);
    const material = lp.filament_type || parsePresetName(name).material;
    // No id is claimed here: the generic id an import maps to is shared by
    // every filament of that material, so claiming it would let the first
    // imported PLA swallow every other PLA in the list.
    if (!take('local', name)) continue;
    options.push({
      id: `local_${lp.id}`,
      name,
      source: 'local',
      filamentId: genericFilamentIdForMaterial(material),
      filamentType: material || '',
    });
  }

  // 2. Orca Cloud. setting_ids are UUIDs a Bambu printer can't resolve, so
  // these also fall back to the generic id for their material.
  for (const op of orcaProfiles ?? []) {
    const name = presetDisplayName(op.name);
    const material = parsePresetName(name).material;
    // Same reasoning as the local tier for the generic id. The Orca profile id
    // is claimed, so a Bambu Cloud row carrying that same id is recognised as
    // the same record — a shared *name* is not, since an Orca library and an
    // imported bundle are usually the same profiles reached two ways and both
    // are worth showing under their own heading.
    if (!take('orca_cloud', name, op.setting_id)) continue;
    options.push({
      id: `orca_${op.setting_id}`,
      name,
      source: 'orca_cloud',
      filamentId: genericFilamentIdForMaterial(material),
      filamentType: material,
    });
  }

  // 3. Bambu Cloud. Official presets (GFS…) carry their filament id in the
  // setting_id itself; user presets (PFUS… / PFCN…) do not, and toFilamentId
  // would hand back the raw cloud id, which the printer rejects. Leave those
  // empty here and let resolveFilamentId fetch the detail on selection.
  for (const cp of cloudSettings ?? []) {
    const name = presetDisplayName(cp.name);
    // Cloud setting_ids carry a variant suffix ("GFSA00_01"); claim the bare
    // filament id as well, or the built-in tier won't recognise the filament
    // as covered and will list it again under its own heading.
    const filamentId = cp.setting_id.startsWith('GFS') ? toFilamentId(cp.setting_id) : '';
    if (!take('cloud', name, cp.setting_id, filamentId || undefined)) continue;
    options.push({
      id: cp.setting_id,
      name,
      source: 'cloud',
      filamentId,
      filamentType: parsePresetName(name).material,
    });
  }

  // 4. Hardcoded fallback. Always present, so the picker is never empty even
  // with no cloud account and nothing imported — but only for filaments none
  // of the tiers above already offered.
  for (const bf of builtinFilaments ?? []) {
    // Cloud setting_ids insert an "S" after "GF" ("GFA00" → "GFSA00"); check
    // both spellings so a filament a cloud tier already offered isn't listed
    // a second time under a slightly different id.
    const asSettingId = bf.filament_id.startsWith('GF') ? `GFS${bf.filament_id.slice(2)}` : bf.filament_id;
    // Unlike the tiers above, a name match is enough to skip: this table is a
    // static copy of the same catalogue, not a library of its own.
    if (namesOffered.has(nameKey(bf.name))) continue;
    if (!take('builtin', bf.name, bf.filament_id, asSettingId)) continue;
    options.push({
      id: `builtin_${bf.filament_id}`,
      name: bf.name,
      source: 'builtin',
      filamentId: bf.filament_id,
      filamentType: parsePresetName(bf.name).material,
    });
  }

  return options.sort((a, b) => {
    if (a.source !== b.source) return SOURCE_ORDER[a.source] - SOURCE_ORDER[b.source];
    return a.name.localeCompare(b.name);
  });
}

/**
 * The Bambu filament id to file a calibration under for a chosen preset.
 *
 * Everything except a Bambu Cloud *user* preset is already resolved by
 * buildFilamentPresetOptions; those need the cloud detail, because the
 * PFUS/PFCN setting_id is not a filament id and the printer's calibration
 * table is indexed by filament id. ``fetchDetail`` is injected so the pure
 * cases stay testable without a network stub.
 */
export async function resolveFilamentId(
  option: FilamentPresetOption,
  fetchDetail?: (settingId: string) => Promise<{ filament_id?: string | null }>,
): Promise<string> {
  if (option.filamentId) return option.filamentId;
  if (option.source !== 'cloud' || !fetchDetail) return '';
  try {
    const detail = await fetchDetail(option.id);
    // Never fall back to the preset's base_id: that collapses a custom preset
    // onto the generic it inherits from, and the printer then resolves the
    // calibration to "Generic …" instead of the user's filament (#1053).
    return detail.filament_id || '';
  } catch {
    return '';
  }
}
