import type { TFunction } from 'i18next';
import { ApiError, type InventorySpool } from '../api/client';

/**
 * Linking an AMS RFID tag to a spool the user picks, in built-in inventory
 * (#3658): from the unknown-tag prompt, from the slot card, and when moving a
 * tag that went to the wrong spool.
 */

export interface RfidTag {
  tray_info_idx?: string | null;
  tag_uid?: string | null;
  tray_uuid?: string | null;
}

export interface TagLinkBody {
  tag_uid?: string;
  tray_uuid?: string;
  tag_type?: string;
  data_origin?: string;
}

function realId(value?: string | null) {
  const hex = value?.replace(/[^0-9a-f]/gi, '').toUpperCase();
  return hex && !/^0+$/.test(hex) ? hex : undefined;
}

export function hasRfidTag(tag?: RfidTag | null) {
  return !!(realId(tag?.tag_uid) || realId(tag?.tray_uuid));
}

export function tagLinkError(error: Error, t: TFunction, fallback: string) {
  if (error instanceof ApiError) {
    if (error.code === 'tag_already_linked' && typeof error.detail?.spool_id === 'number') {
      return t('inventory.tagAlreadyLinked', { id: error.detail.spool_id });
    }
    if (error.status === 403) return t('inventory.rfidLink.permission');
    if (error.status === 409) return t('inventory.rfidLink.changed');
  }
  return fallback;
}

/**
 * The link-tag body for a tag the AMS read. Recorded the way an automatic
 * RFID link is (``rfid_linked`` / ``bambulab``), so the spool reads the same
 * whether Bambuddy or the user picked it.
 */
export function rfidLinkBody(tag: RfidTag): TagLinkBody {
  const tray_uuid = realId(tag.tray_uuid);
  const tag_uid = realId(tag.tag_uid);
  const bambu = !!(tray_uuid || (tag_uid && tag.tray_info_idx));
  return {
    tag_uid,
    tray_uuid,
    tag_type: bambu ? 'bambulab' : tag_uid ? 'generic' : undefined,
    data_origin: bambu ? 'rfid_linked' : 'nfc_link',
  };
}

/** The link-tag body that gives a spool's tag to another spool, as it is recorded on the first. */
export function movedTagBody(holder: InventorySpool): TagLinkBody {
  const tray_uuid = realId(holder.tray_uuid);
  const fromRfid = holder.data_origin === 'rfid_auto' || holder.data_origin === 'rfid_linked';
  return {
    tag_uid: realId(holder.tag_uid),
    tray_uuid,
    tag_type: holder.tag_type ?? (tray_uuid ? 'bambulab' : undefined),
    data_origin: fromRfid ? 'rfid_linked' : (holder.data_origin ?? 'nfc_link'),
  };
}

export interface LinkChoice {
  spool: InventorySpool;
  /** The backend matcher named this spool in the prompt. */
  matches: boolean;
}

const norm = (value?: string | null) => (value ?? '').trim().toUpperCase();
const hex6 = (value?: string | null) => norm(value).replace(/^#/, '').slice(0, 6);

/**
 * The spools a tag can be linked to: active and untagged, of the slot's
 * material. Spools the matcher named come first in its order (oldest first),
 * then spools of the slot's colour, then the rest, each by id.
 */
export function linkChoices(
  spools: InventorySpool[] | undefined,
  options: { material?: string | null; colorHex?: string | null; candidateIds?: number[]; excludeId?: number | null },
): LinkChoice[] {
  const candidateIds = options.candidateIds ?? [];
  const material = norm(options.material);
  const colour = hex6(options.colorHex);
  const choices = (spools ?? []).filter((spool) => {
    if (spool.id === options.excludeId || spool.archived_at || hasRfidTag(spool)) return false;
    if (candidateIds.includes(spool.id) || !material) return true;
    return norm(spool.material) === material;
  });
  const rank = (spool: InventorySpool) => {
    const candidate = candidateIds.indexOf(spool.id);
    if (candidate >= 0) return candidate;
    return candidateIds.length + (colour && hex6(spool.rgba) === colour ? 0 : 1);
  };
  return choices
    .sort((a, b) => rank(a) - rank(b) || a.id - b.id)
    .map((spool) => ({
      spool,
      matches: candidateIds.includes(spool.id),
    }));
}
