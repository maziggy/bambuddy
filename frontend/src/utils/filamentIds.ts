// Bambu filament ids: the printer-side filament_id ("GFL05", reported as an AMS
// slot's tray_info_idx) and the cloud preset's setting_id ("GFSL05_07").
// Mirrors backend/app/utils/filament_ids.py.
//
// For most official presets the setting_id is the filament_id with an "S"
// after "GF" plus a variant suffix. Two kinds are not (#3387): SUNLU's
// setting_id "GFSNLS03_04" is filament_id "GFSNL03", and about thirty P1/X1
// presets carry another filament's id altogether, listed by full setting_id
// in SETTING_ID_FILAMENT_IDS. When Bambu's list hands over the preset's own
// filament_id, use that; these are for ids that come without one.
import { SETTING_ID_FILAMENT_IDS } from './filamentIdTables';

const SUNLU_SETTING_ID = /^GFSNLS(\d+)$/;
const SUNLU_FILAMENT_ID = /^GFSNL\d+$/;

/** The filament_id for a setting_id; any other id comes back without its
 *  variant suffix. Case is kept: user preset ids carry lowercase hex. */
export function settingIdToFilamentId(id: string | null | undefined): string {
  if (!id) return '';
  const listed = SETTING_ID_FILAMENT_IDS[id];
  if (listed) return listed;
  const base = id.split('_')[0];
  const sunlu = SUNLU_SETTING_ID.exec(base);
  if (sunlu) return `GFSNL${sunlu[1]}`;
  if (SUNLU_FILAMENT_ID.test(base)) return base;
  // "GFS" then the family letter is a setting_id. A digit after "GFS" is a
  // support filament_id ("GFS99", Generic PVA), already in its final form.
  if (/^GFS[A-Z]/.test(base)) return 'GF' + base.slice(3);
  return base;
}

/** The base setting_id for a filament_id ("GFA00" → "GFSA00", "GFSNL03" →
 *  "GFSNLS03"); anything that is not a Bambu filament_id is returned as is. */
export function filamentIdToSettingId(id: string): string {
  if (SUNLU_FILAMENT_ID.test(id)) return 'GFSNLS' + id.slice(5);
  if (/^GFS[A-Z]/.test(id)) return id;
  return id.startsWith('GF') ? 'GFS' + id.slice(2) : id;
}

interface ListedPreset {
  setting_id: string;
  filament_id?: string | null;
}

/** A bare official setting_id for which Bambu's list and the rule name two
 *  filaments: "GFSA05" is Bambu PLA Matte @BBL X1 (GFA01) in Bambu's list, and
 *  the id Bambuddy itself builds for Bambu PLA Silk (GFA05). */
function ambiguous(preset: ListedPreset, derived: string): boolean {
  return !!preset.filament_id && preset.filament_id !== derived
    && preset.setting_id.startsWith('GFS') && !preset.setting_id.includes('_');
}

/** A cloud preset's filament_id. Bambu's list carries it, but for an
 *  ambiguous bare id (see above) it says only which filament a preset picked
 *  from the list is; where Bambuddy may have built the id itself, the rule
 *  reads it, as the backend does. ``slotTrayInfoIdx``, for the preset a slot
 *  already holds, settles it by what the slot reports. */
export function cloudPresetFilamentId(
  preset: ListedPreset,
  { pickedFromList = false, slotTrayInfoIdx }: { pickedFromList?: boolean; slotTrayInfoIdx?: string } = {},
): string {
  const derived = settingIdToFilamentId(preset.setting_id);
  if (!ambiguous(preset, derived)) return preset.filament_id || derived;
  if (slotTrayInfoIdx && (slotTrayInfoIdx === preset.filament_id || slotTrayInfoIdx === derived)) return slotTrayInfoIdx;
  return pickedFromList ? preset.filament_id! : derived;
}

/** Every filament_id a cloud preset may stand for, to match a slot against. */
export function cloudPresetFilamentIds(preset: ListedPreset): string[] {
  const derived = settingIdToFilamentId(preset.setting_id);
  return ambiguous(preset, derived) ? [preset.filament_id!, derived] : [preset.filament_id || derived];
}
