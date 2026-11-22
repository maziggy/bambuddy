import { describe, it, expect } from 'vitest';
import type { InventorySpool } from '../../api/client';
import { linkChoices, movedTagBody, rfidLinkBody } from '../../utils/rfidTagLink';

const spool = (over: Partial<InventorySpool>): InventorySpool =>
  ({
    id: 1,
    material: 'PLA',
    subtype: 'Matte',
    brand: 'Bambu Lab',
    color_name: 'Charcoal',
    rgba: '333333FF',
    label_weight: 1000,
    weight_used: 0,
    note: null,
    tag_uid: null,
    tray_uuid: null,
    tag_type: null,
    data_origin: null,
    archived_at: null,
    ...over,
  }) as InventorySpool;

describe('linkChoices (#3658)', () => {
  const spools = [
    spool({ id: 12 }),
    spool({ id: 13 }),
    spool({ id: 30, weight_used: 664 }),
    spool({ id: 40, rgba: 'FFFFFFFF', color_name: 'White' }),
    spool({ id: 41, material: 'PETG' }),
    spool({ id: 42, tray_uuid: 'A'.repeat(32) }),
    spool({ id: 43, archived_at: '2026-01-01T00:00:00Z' }),
  ];

  it('puts the matcher candidates first, in its order, then the rest of the material', () => {
    const choices = linkChoices(spools, { material: 'PLA', colorHex: '333333FF', candidateIds: [30, 12] });

    expect(choices.map((c) => [c.spool.id, c.matches])).toEqual([
      [30, true],
      [12, true],
      [13, false],
      [40, false],
    ]);
  });

  it('without candidates, ranks spools of the slot colour first and marks them', () => {
    const choices = linkChoices(spools, { material: 'pla', colorHex: '#333333' });

    expect(choices.map((c) => [c.spool.id, c.matches])).toEqual([
      [12, true],
      [13, true],
      [30, true],
      [40, false],
    ]);
  });

  it('leaves out tagged, archived and excluded spools', () => {
    const ids = linkChoices(spools, { material: 'PLA', excludeId: 12 }).map((c) => c.spool.id);

    expect(ids).toEqual([13, 30, 40]);
  });
});

describe('link-tag bodies', () => {
  it('records a tag the AMS read like an automatic link', () => {
    expect(rfidLinkBody({ tag_uid: 'AABBCCDD11223344', tray_uuid: 'B'.repeat(32) })).toEqual({
      tag_uid: 'AABBCCDD11223344',
      tray_uuid: 'B'.repeat(32),
      tag_type: 'bambulab',
      data_origin: 'rfid_linked',
    });
    expect(rfidLinkBody({ tag_uid: '0000000000000000', tray_uuid: '0'.repeat(32) })).toEqual({
      tag_uid: undefined,
      tray_uuid: undefined,
      tag_type: undefined,
      data_origin: 'rfid_linked',
    });
  });

  it("moves a spool's tag as it is recorded there", () => {
    const bambu = spool({ tag_uid: 'AABB', tray_uuid: 'C'.repeat(32), tag_type: 'bambulab', data_origin: 'rfid_auto' });
    expect(movedTagBody(bambu)).toEqual({
      tag_uid: 'AABB',
      tray_uuid: 'C'.repeat(32),
      tag_type: 'bambulab',
      data_origin: 'rfid_linked',
    });
    const nfc = spool({ tag_uid: '04ECE673D32A81', tag_type: 'ntag', data_origin: 'nfc_link' });
    expect(movedTagBody(nfc)).toEqual({
      tag_uid: '04ECE673D32A81',
      tray_uuid: undefined,
      tag_type: 'ntag',
      data_origin: 'nfc_link',
    });
  });
});
