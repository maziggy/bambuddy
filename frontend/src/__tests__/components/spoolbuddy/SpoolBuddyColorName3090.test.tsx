/**
 * The kiosk shows the colour the rest of Bambuddy shows (#3090).
 *
 * Reported by @Sawtaytoes: scan a Bambu RFID spool that Bambuddy's inventory
 * calls "Candy Red" and SpoolBuddy's dialog says "Unknown color" — with the
 * right red swatch beside it. The name was never in the spool record. Bambu's tags frequently carry none, so Bambuddy resolves the swatch's
 * own hex against the colour catalog; the kiosk was printing the empty column.
 *
 * These render the real components against a catalog, because the bug was not
 * in the resolver — which was already correct and already used one file away —
 * but in which components called it.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../../utils';
import { TagDetectedModal } from '../../../components/spoolbuddy/TagDetectedModal';
import { SpoolInfoCard } from '../../../components/spoolbuddy/SpoolInfoCard';
import type { MatchedSpool } from '../../../hooks/useSpoolBuddyState';
import { setColorCatalog, __resetColorCatalogForTests } from '../../../utils/colors';

vi.mock('../../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
  spoolbuddyApi: {
    updateSpoolWeight: vi.fn().mockResolvedValue({ status: 'ok', weight_used: 0 }),
    unlinkTag: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

// The reporter's spool: Bambu Lab PLA Silk+, Candy Red, no name on the tag.
const candyRed: MatchedSpool = {
  id: 38,
  tag_uid: '00000100',
  material: 'PLA',
  subtype: 'Silk+',
  color_name: null,
  rgba: 'D02727FF',
  brand: 'Bambu Lab',
  label_weight: 1000,
  core_weight: 250,
  weight_used: 0,
};

const modalProps = {
  isOpen: true,
  onClose: vi.fn(),
  spool: candyRed,
  tagUid: '00000100',
  scaleWeight: 1000,
  weightStable: true,
  onSyncWeight: vi.fn(),
  onAssignToAms: vi.fn(),
  onLinkSpool: vi.fn(),
  onAddToInventory: vi.fn(),
};

describe('SpoolBuddy colour names (#3090)', () => {
  beforeEach(() => {
    __resetColorCatalogForTests();
    setColorCatalog({ d02727: 'Candy Red' });
  });

  it('names the scanned spool from the catalog when the tag carried no name', () => {
    render(<TagDetectedModal {...modalProps} />);

    expect(screen.getByText('Candy Red')).toBeInTheDocument();
    expect(screen.queryByText('Unknown color')).not.toBeInTheDocument();
  });

  it('names it on the dashboard card too, not only in the scan dialog', () => {
    // Four components render a scanned spool. Fixing the dialog alone leaves
    // the same spool nameless on the card behind it.
    render(<SpoolInfoCard spool={candyRed} scaleWeight={1000} weightStable onSyncWeight={vi.fn()} />);

    expect(screen.getByText('Candy Red')).toBeInTheDocument();
  });

  it('prefers the catalog over a name Spoolman never stored', () => {
    // Spoolman has no colour-name field, so the backend sends the subtype with
    // color_name_is_synthesized set. "Silk+" is not a colour.
    render(
      <TagDetectedModal
        {...modalProps}
        spool={{ ...candyRed, color_name: 'Silk+', color_name_is_synthesized: true }}
      />,
    );

    expect(screen.getByText('Candy Red')).toBeInTheDocument();
    expect(screen.queryByText('Silk+')).not.toBeInTheDocument();
  });

  it('keeps a real stored name exactly as stored', () => {
    // The catalog is the fallback, not an override — a user who typed
    // "My Favourite Red" sees it, whatever the hex says.
    render(
      <TagDetectedModal {...modalProps} spool={{ ...candyRed, color_name: 'My Favourite Red' }} />,
    );

    expect(screen.getByText('My Favourite Red')).toBeInTheDocument();
    expect(screen.queryByText('Candy Red')).not.toBeInTheDocument();
  });

  it('falls back to a translated label when nothing can name the colour', () => {
    // Unknown hex, no stored name: the string has to come from i18n, because
    // the kiosk is used in every language Bambuddy ships.
    render(
      <TagDetectedModal {...modalProps} spool={{ ...candyRed, rgba: '123456FF' }} />,
    );

    expect(screen.getByText('Unknown color')).toBeInTheDocument();
  });
});
