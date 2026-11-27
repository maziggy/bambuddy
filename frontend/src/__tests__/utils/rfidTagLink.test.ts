import { describe, it, expect } from 'vitest';
import type { InventorySpool } from '../../api/client';
import { linkChoices, movedTagBody, rfidLinkBody } from '../../utils/rfidTagLink';

const spool = (over: Partial<InventorySpool>): InventorySpool =>
  ({
    id: 1,
    material: 'PETG',
    subtype: 'HF',
    brand: 'Bambu Lab',
    color_name: 'Teal',
    rgba: '179CB0FF',
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
    spool({ id: 61 }),
    spool({ id: 64 }),
    spool({ id: 73, weight_used: 175 }),
    spool({ id: 94, rgba: 'FFFFFFFF', color_name: 'White' }),
    spool({ id: 41, material: 'ABS' }),
    spool({ id: 87, material: 'PETG-CF' }),
    spool({ id: 89, material: 'PETG+' }),
    spool({ id: 42, tray_uuid: 'A'.repeat(32) }),
    spool({ id: 43, archived_at: '2026-01-01T00:00:00Z' }),
  ];

  it('puts the matcher candidates first, in its order, then the rest of the material', () => {
    const choices = linkChoices(spools, { material: 'PETG', colorHex: '179CB0FF', candidateIds: [73, 61] });

    expect(choices.map((c) => [c.spool.id, c.matches])).toEqual([
      [73, true],
      [61, true],
      [64, false],
      [94, false],
    ]);
  });

  it('ranks similar colours without calling them matcher candidates', () => {
    const choices = linkChoices(spools, { material: 'petg', colorHex: '#179CB0' });

    expect(choices.map((c) => [c.spool.id, c.matches])).toEqual([
      [61, false],
      [64, false],
      [73, false],
      [94, false],
    ]);
  });

  it('leaves out tagged, archived and excluded spools', () => {
    const ids = linkChoices(spools, { material: 'PETG', excludeId: 61 }).map((c) => c.spool.id);

    expect(ids).toEqual([64, 73, 94]);
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
      data_origin: 'nfc_link',
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
    expect(rfidLinkBody({ tag_uid: 'AABB', tray_info_idx: 'GFG00' }).tag_type).toBe('bambulab');
    const nfc = spool({ tray_uuid: '0'.repeat(32), tag_uid: '04ECE673D32A81', tag_type: 'ntag', data_origin: 'nfc_link' });
    expect(movedTagBody(nfc)).toEqual({
      tag_uid: '04ECE673D32A81',
      tray_uuid: undefined,
      tag_type: 'ntag',
      data_origin: 'nfc_link',
    });
  });
});
