/**
 * Tests for `resolveArchiveSlicerAmsMapping` (#2700).
 *
 * This is the gate between an archive's saved slicer AMS pick and the print
 * modal offering it. The saved tray IDs are *global tray IDs*, which only mean
 * something against the AMS layout of the one printer they were resolved
 * against — tray 5 on printer A can hold a completely different spool than
 * tray 5 on printer B. Everything here exists to make sure the mapping is only
 * ever offered for its own printer.
 */

import { describe, it, expect } from 'vitest';
import { resolveArchiveSlicerAmsMapping } from '../../components/PrintModal/archiveAmsMapping';

const SAVED = { slicer_ams_mapping: { mapping: [4, -1, 12, -1], printer_id: 7 } };

describe('resolveArchiveSlicerAmsMapping', () => {
  it('returns the mapping when the selected printer is the one it was saved for', () => {
    expect(resolveArchiveSlicerAmsMapping(SAVED, 7)).toEqual([4, -1, 12, -1]);
  });

  it('refuses the mapping on a different printer', () => {
    // The whole point of storing printer_id. Tray 4 on printer 9 is not the
    // spool the slicer picked on printer 7.
    expect(resolveArchiveSlicerAmsMapping(SAVED, 9)).toBeUndefined();
  });

  it('refuses the mapping when no printer is selected yet', () => {
    // Guards the `undefined === undefined` reading as a match: with no printer
    // chosen there is nothing to compare against, so nothing may be offered.
    expect(resolveArchiveSlicerAmsMapping(SAVED, null)).toBeUndefined();
    expect(resolveArchiveSlicerAmsMapping(SAVED, undefined)).toBeUndefined();
  });

  it('returns undefined for archives with no extra_data at all', () => {
    expect(resolveArchiveSlicerAmsMapping(null, 7)).toBeUndefined();
    expect(resolveArchiveSlicerAmsMapping(undefined, 7)).toBeUndefined();
    expect(resolveArchiveSlicerAmsMapping({}, 7)).toBeUndefined();
  });

  it('ignores unrelated extra_data keys', () => {
    // The common case: archives carry plenty of metadata but no saved mapping.
    expect(resolveArchiveSlicerAmsMapping({ printable_objects: { '1': 'part' } }, 7)).toBeUndefined();
  });

  it('rejects a stored value that is missing its printer_id', () => {
    // A mapping saved without knowing which printer it came from can't be
    // safely reused on any printer, including the one it actually came from —
    // there'd be no way to tell.
    expect(resolveArchiveSlicerAmsMapping({ slicer_ams_mapping: { mapping: [4, 12] } }, 7)).toBeUndefined();
  });

  it('rejects malformed stored values instead of throwing', () => {
    // extra_data is free-form JSON off the wire; a bad shape must degrade to
    // "no saved mapping", never crash the modal.
    expect(resolveArchiveSlicerAmsMapping({ slicer_ams_mapping: 'nope' }, 7)).toBeUndefined();
    expect(resolveArchiveSlicerAmsMapping({ slicer_ams_mapping: null }, 7)).toBeUndefined();
    expect(
      resolveArchiveSlicerAmsMapping({ slicer_ams_mapping: { mapping: 'nope', printer_id: 7 } }, 7),
    ).toBeUndefined();
    expect(
      resolveArchiveSlicerAmsMapping({ slicer_ams_mapping: { mapping: [], printer_id: 7 } }, 7),
    ).toBeUndefined();
  });

  it('does not coerce printer ids', () => {
    // A string "7" from a hand-edited record is not printer 7.
    expect(
      resolveArchiveSlicerAmsMapping({ slicer_ams_mapping: { mapping: [4], printer_id: '7' } }, 7),
    ).toBeUndefined();
  });
});
