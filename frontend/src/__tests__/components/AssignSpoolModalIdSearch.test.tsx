/**
 * Searching "#<number>" in Assign Spool finds that spool whatever the slot
 * holds (#3301).
 *
 * The search ran on what was left after the tray-match filter, so a spool of
 * another material never appeared for its exact number unless "Show all
 * spools" was ticked -- and the empty list read as if the spool were gone.
 * Typing a spool's number is choosing it: "#123" now skips the tray match
 * (the material-mismatch warning still asks on assign). A spool that stays
 * hidden says why: in another slot, archived, or no such spool.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { AssignSpoolModal } from '../../components/AssignSpoolModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn(),
    getAssignments: vi.fn(),
    assignSpool: vi.fn(),
    assignSpoolmanSlot: vi.fn(),
    getSpoolmanInventorySpools: vi.fn(),
    getSpoolmanSlotAssignments: vi.fn().mockResolvedValue([]),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    refreshPrinterStatus: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

const spool = (id: number, material: string, brand: string, extra: Record<string, unknown> = {}) => ({
  id,
  material,
  subtype: 'Basic',
  brand,
  color_name: 'Black',
  rgba: '000000FF',
  label_weight: 1000,
  weight_used: 0,
  tag_uid: null,
  tray_uuid: null,
  slicer_filament_name: material,
  ...extra,
});

const SPOOLS = [
  spool(1, 'PLA', 'Polymaker'),
  spool(7, 'PETG', 'Sunlu'),
  spool(8, 'PLA', 'Elegoo', { archived_at: '2026-10-01T00:00:00Z' }),
  spool(9, 'PLA', 'Overture'),
];

const props = {
  isOpen: true,
  onClose: vi.fn(),
  printerId: 1,
  amsId: 0,
  trayId: 0,
  trayInfo: { type: 'PLA', material: 'PLA', profile: 'PLA', color: '000000', location: 'AMS 1 - Slot 1' },
};

async function search(query: string) {
  const user = userEvent.setup();
  render(<AssignSpoolModal {...props} />);
  await screen.findByText(/Polymaker/);
  await user.type(screen.getByPlaceholderText(/search/i), query);
  return user;
}

describe('AssignSpoolModal "#<number>" search', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue(SPOOLS);
    // Spool 9 sits in another slot of the same printer.
    (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 1, spool_id: 9, printer_id: 1, ams_id: 0, tray_id: 2 },
    ]);
  });

  it('finds a spool of another material than the slot, and only that one', async () => {
    await search('#7');
    expect(await screen.findByText(/Sunlu/)).toBeInTheDocument();
    expect(screen.queryByText(/Polymaker/)).not.toBeInTheDocument();
  });

  it('still asks before assigning it', async () => {
    const user = await search('#7');
    await user.click(await screen.findByText(/Sunlu/));
    await user.click(screen.getByRole('button', { name: /assign spool/i }));
    expect(await screen.findByText('Material mismatch')).toBeInTheDocument();
    expect(api.assignSpool).not.toHaveBeenCalled();
  });

  it('leaves the tray match on for any other search', async () => {
    await search('Sunlu');
    await waitFor(() => expect(screen.queryByText(/Polymaker/)).not.toBeInTheDocument());
    expect(screen.queryByText(/Sunlu/)).not.toBeInTheDocument();
  });

  it('says so when the spool is in another slot, and still does not offer it', async () => {
    await search('#9');
    expect(await screen.findByText('Spool #9 is already assigned to another slot. Unassign it there first.')).toBeInTheDocument();
    expect(screen.queryByText(/Overture/)).not.toBeInTheDocument();
  });

  it('says so when the spool is archived', async () => {
    await search('#8');
    expect(await screen.findByText('Spool #8 is archived. Restore it to assign it.')).toBeInTheDocument();
    expect(screen.queryByText(/Elegoo/)).not.toBeInTheDocument();
  });

  it('says so when there is no such spool', async () => {
    await search('#99');
    expect(await screen.findByText('There is no spool #99.')).toBeInTheDocument();
  });

  it('with "Show all spools" ticked, a spool in another slot is offered as before', async () => {
    const user = await search('#9');
    await user.click(screen.getByLabelText(/show all spools/i));
    expect(await screen.findByText(/Overture/)).toBeInTheDocument();
  });
});
