import type { InventorySpool } from '../api/client';

/**
 * Linking an AMS RFID tag to a spool the user picks, in built-in inventory
 * (#3658): from the unknown-tag prompt, from the slot card, and when moving a
 * tag that went to the wrong spool.
 */

export interface RfidTag {
  tag_uid?: string | null;
  tray_uuid?: string | null;
}

export interface TagLinkBody {
  tag_uid?: string;
  tray_uuid?: string;
  tag_type?: string;
  data_origin?: string;
}

const realId = (value?: string | null): string | undefined =>
  value && !/^0+$/.test(value) ? value : undefined;

/**
 * The link-tag body for a tag the AMS read. Recorded the way an automatic
 * RFID link is (``rfid_linked`` / ``bambulab``), so the spool reads the same
 * whether Bambuddy or the user picked it.
 */
export function rfidLinkBody(tag: RfidTag): TagLinkBody {
  const tray_uuid = realId(tag.tray_uuid);
  return {
    tag_uid: realId(tag.tag_uid),
    tray_uuid,
    tag_type: tray_uuid ? 'bambulab' : undefined,
    data_origin: 'rfid_linked',
  };
}

/** The link-tag body that gives a spool's tag to another spool, as it is recorded on the first. */
export function movedTagBody(holder: InventorySpool): TagLinkBody {
  const fromRfid = holder.data_origin === 'rfid_auto' || holder.data_origin === 'rfid_linked';
  return {
    tag_uid: holder.tag_uid ?? undefined,
    tray_uuid: holder.tray_uuid ?? undefined,
    tag_type: holder.tag_type ?? (holder.tray_uuid ? 'bambulab' : undefined),
    data_origin: fromRfid || holder.tray_uuid ? 'rfid_linked' : (holder.data_origin ?? 'nfc_link'),
  };
}

export interface LinkChoice {
  spool: InventorySpool;
  /** A spool the tag matches: one the matcher found, or one of the slot's colour. */
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
    if (spool.id === options.excludeId || spool.archived_at || spool.tag_uid || spool.tray_uuid) return false;
    if (candidateIds.includes(spool.id) || !material) return true;
    return norm(spool.material).startsWith(material);
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
      matches: candidateIds.length > 0 ? candidateIds.includes(spool.id) : !!colour && hex6(spool.rgba) === colour,
    }));
}
