/**
 * Does a spool fit the AMS slot it is being assigned to? The material check
 * the Assign Spool dialog and the QR assignment both ask before assigning.
 */

import type { TFunction } from 'i18next';

export const normalizeValue = (value: string | undefined | null) => (value ?? '').trim().toUpperCase();

// Bambu Studio / OrcaSlicer profile names carry a printer/nozzle/variant qualifier after
// `@` (e.g. "Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)"), while the tray's
// profile is typically the bare base name. Strip the qualifier before comparing so identical
// base profiles don't trigger a mismatch warning (#1047).
export const stripProfileQualifier = (value: string) => value.split('@')[0].trim();

function checkMaterialMatch(
  spoolMaterial: string | undefined | null,
  trayMaterial: string | undefined | null,
): 'exact' | 'partial' | 'none' {
  const normalizedSpool = normalizeValue(spoolMaterial);
  const normalizedTray = normalizeValue(trayMaterial);

  if (!normalizedSpool || !normalizedTray) return 'none';
  if (normalizedSpool === normalizedTray) return 'exact';
  if (normalizedTray.includes(normalizedSpool) || normalizedSpool.includes(normalizedTray)) {
    return 'partial';
  }

  return 'none';
}

function checkProfileMatch(spoolProfile: string | undefined | null, trayProfile: string | undefined | null): boolean {
  const normalizedSpoolProfile = stripProfileQualifier(normalizeValue(spoolProfile));
  const normalizedTrayProfile = stripProfileQualifier(normalizeValue(trayProfile));

  if (!normalizedSpoolProfile || !normalizedTrayProfile) return false;

  return normalizedSpoolProfile === normalizedTrayProfile;
}

export interface SlotFilament {
  type: string;
  material?: string;
  profile?: string;
}

export interface MaterialMismatch {
  type: 'material' | 'partial' | 'material_profile' | 'partial_profile';
  spoolMaterial: string;
  trayMaterial: string;
  spoolProfile?: string;
  trayProfile?: string;
}

/**
 * The material mismatch to confirm before assigning `spool` to the slot, or
 * null when there is none to ask about.
 *
 * Profile-only mismatch no longer triggers the popup — the backend's
 * `apply_spool_to_slot_via_mqtt` pushes the spool's slicer profile to the AMS
 * slot on every assign anyway, so warning the user about a profile delta then
 * "fixing" it during the same action was friction without benefit (#1552).
 * Material mismatch still warns because the firmware can refuse a print when
 * type doesn't match; combined material+profile mismatches keep the profile
 * detail in the same popup as the material warning.
 *
 * A slot reporting no material — empty, or a spool the printer can't identify
 * — has nothing to mismatch: the question would read 'does not match the tray
 * material ""'.
 */
export function findMaterialMismatch(
  spool: { material?: string | null; slicer_filament_name?: string | null; slicer_filament?: string | null },
  slot: SlotFilament,
): MaterialMismatch | null {
  const trayMaterial = slot.material || slot.type;
  if (!normalizeValue(trayMaterial)) return null;
  const materialMatchResult = checkMaterialMatch(spool.material, trayMaterial);
  if (materialMatchResult === 'exact') return null;

  const spoolProfile = spool.slicer_filament_name || spool.slicer_filament;
  const trayProfile = slot.profile || slot.type;
  const profileMatches = checkProfileMatch(spoolProfile, trayProfile);
  let type: MaterialMismatch['type'];
  if (materialMatchResult === 'none' && !profileMatches) {
    type = 'material_profile';
  } else if (materialMatchResult === 'partial' && !profileMatches) {
    type = 'partial_profile';
  } else if (materialMatchResult === 'none') {
    type = 'material';
  } else {
    type = 'partial';
  }
  return {
    type,
    spoolMaterial: spool.material || '',
    trayMaterial: trayMaterial || '',
    spoolProfile: spoolProfile || undefined,
    trayProfile: trayProfile || undefined,
  };
}

/** The confirm's text for a mismatch at `location`. */
export function materialMismatchMessage(t: TFunction, mismatch: MaterialMismatch, location: string): string {
  const materials = { spoolMaterial: mismatch.spoolMaterial, trayMaterial: mismatch.trayMaterial, location };
  const profiles = () =>
    t('inventory.assignProfileMismatchMessage', {
      spoolProfile: mismatch.spoolProfile || t('common.unknown'),
      trayProfile: mismatch.trayProfile || t('common.unknown'),
      location,
    });
  let message =
    mismatch.type === 'material' || mismatch.type === 'material_profile'
      ? t('inventory.assignMismatchMessage', materials)
      : t('inventory.assignPartialMismatchMessage', materials);
  if (mismatch.type === 'material_profile' || mismatch.type === 'partial_profile') {
    message = `${message}\n\n${profiles()}`;
  }
  // Always tell the user the AMS slot is going to be reconfigured — the
  // existing wording made "Assign Anyway" sound like the popup was a no-op
  // confirmation, when the backend in fact pushes the spool's profile to the
  // slot on every assign (#1552).
  return `${message}\n\n${t('inventory.assignReconfigureNote')}`;
}
