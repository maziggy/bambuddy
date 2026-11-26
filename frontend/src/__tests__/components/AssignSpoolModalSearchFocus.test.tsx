/**
 * The Assign Spool search box takes focus when the dialog opens (#3093).
 *
 * Searching is what the dialog is usually opened for, so it should not take
 * a click into the box first. Only with a fine pointer (mouse, trackpad): on a
 * touch screen focusing it would pop the on-screen keyboard unasked.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { AssignSpoolModal } from '../../components/AssignSpoolModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn().mockResolvedValue([]),
    getAssignments: vi.fn().mockResolvedValue([]),
    assignSpool: vi.fn(),
    assignSpoolmanSlot: vi.fn(),
    getSpoolmanInventorySpools: vi.fn(),
    getSpoolmanSlotAssignments: vi.fn().mockResolvedValue([]),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    refreshPrinterStatus: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

const props = {
  isOpen: true,
  onClose: vi.fn(),
  printerId: 1,
  amsId: 0,
  trayId: 0,
  trayInfo: { type: 'PLA', material: 'PLA', profile: 'PLA', color: '000000', location: 'AMS 1 - Slot 1' },
};

const originalMatchMedia = window.matchMedia;

/** Reports a fine primary pointer when `fine` is true, a coarse (touch) one otherwise. */
function stubPointer(fine: boolean) {
  window.matchMedia = ((query: string) => ({
    ...originalMatchMedia(query),
    matches: query === '(pointer: fine)' ? fine : false,
  })) as typeof window.matchMedia;
}

describe('AssignSpoolModal search focus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it('focuses the search box on open with a mouse or trackpad', () => {
    stubPointer(true);
    render(<AssignSpoolModal {...props} />);
    expect(screen.getByPlaceholderText(/search/i)).toHaveFocus();
  });

  it('leaves the search box unfocused on a touch screen', () => {
    stubPointer(false);
    render(<AssignSpoolModal {...props} />);
    expect(screen.getByPlaceholderText(/search/i)).not.toHaveFocus();
  });
});
