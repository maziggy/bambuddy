/**
 * A stored slot preset must not outlive the spool it describes.
 *
 * `slot_preset_mappings` remembers what a slot was last configured with, and
 * the AMS slot card puts that name ahead of the filament id the printer is
 * reporting -- which is how a hand-picked name survives on the card. It also
 * meant that pulling a Bambu ABS Orange and inserting a PLA Matte Dark Blue
 * left "Bambu ABS" on the card against the new colour, because the row is
 * fetched over REST while everything else arrives on the socket.
 *
 * The check is deliberately narrow. Official Bambu presets differ between the
 * two id forms by one letter and can be compared; a user preset carries two
 * genuinely unrelated ids and a local preset has no printer-side id at all, so
 * neither can be judged here and both keep their name.
 */

import { describe, it, expect } from 'vitest';
import { slotPresetDescribesTray } from '../../utils/amsHelpers';

describe('slotPresetDescribesTray', () => {
  describe('official Bambu presets, where the ids can be compared', () => {
    it('accepts the setting_id / filament_id pair for one filament', () => {
      expect(slotPresetDescribesTray('GFSA01', 'GFA01')).toBe(true);
    });

    it('rejects the row left behind by the previous spool', () => {
      // The reported swap: ABS Basic out, PLA Matte in.
      expect(slotPresetDescribesTray('GFSB00', 'GFA01')).toBe(false);
    });

    it('rejects a different filament in the same family', () => {
      // PLA Matte row, PLA Basic roll -- same GFA prefix, still not the same
      // filament, and the card would name the wrong one.
      expect(slotPresetDescribesTray('GFSA01', 'GFA00')).toBe(false);
    });

    it('ignores the version suffix on either side', () => {
      expect(slotPresetDescribesTray('GFSA01_07', 'GFA01')).toBe(true);
      expect(slotPresetDescribesTray('GFSA01', 'GFA01_07')).toBe(true);
    });

    it('is case-insensitive, since the printer is not consistent about it', () => {
      expect(slotPresetDescribesTray('gfsa01', 'GFA01')).toBe(true);
    });
  });

  describe('rows that cannot be judged keep their name', () => {
    it('a user preset, whose two ids are unrelated', () => {
      // Verbatim from a live slot: ams_filament_setting sent
      // setting_id=PFUSa3b8b0c664c142 and the tray reports tray_info_idx=P8a85d5a.
      // Comparing those would blank a correctly configured slot.
      expect(slotPresetDescribesTray('PFUSa3b8b0c664c142', 'P8a85d5a')).toBe(true);
    });

    it('a local preset, which has no printer-side id', () => {
      expect(slotPresetDescribesTray('local_68', 'GFA00')).toBe(true);
    });

    it('a slot reporting no filament id at all', () => {
      // Generic filament with no tag -- the case the stored row exists for.
      expect(slotPresetDescribesTray('GFSA01', '')).toBe(true);
      expect(slotPresetDescribesTray('GFSA01', null)).toBe(true);
    });

    it('no stored row', () => {
      expect(slotPresetDescribesTray(null, 'GFA01')).toBe(true);
      expect(slotPresetDescribesTray(undefined, undefined)).toBe(true);
    });
  });
});
