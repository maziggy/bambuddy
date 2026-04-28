/**
 * Tests for AssignToAmsModal — verifies that spoolmanMode prop
 * routes to assignSpoolmanSlot (not assignSpool) when assigning.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';

vi.mock('../../api/client', () => ({
  api: {
    getPrinterStatus: vi.fn(),
    getPrinter: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    assignSpool: vi.fn(),
    assignSpoolmanSlot: vi.fn(),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

import { AssignToAmsModal } from '../../components/spoolbuddy/AssignToAmsModal';
import { api } from '../../api/client';

const SPOOL = {
  id: 42,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'BrandX',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  weight_used: 0,
  tag_uid: null,
  tray_uuid: null,
  slicer_filament_name: 'PLA',
  data_origin: 'spoolman',
  k_profiles: [],
};

const PRINTER_STATUS_ONLINE = {
  connected: true,
  state: 'idle',
  ams: [
    {
      id: 0,
      trays: [
        { id: 0, type: '', color: '', remain: 100 },
        { id: 1, type: '', color: '', remain: 100 },
        { id: 2, type: '', color: '', remain: 100 },
        { id: 3, type: '', color: '', remain: 100 },
      ],
    },
  ],
  nozzles: [{ nozzle_diameter: '0.4', nozzle_type: 'stainless' }],
  ams_extruder_map: { '0': 0 },
  dual_nozzle: false,
};

describe('AssignToAmsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPrinterStatus).mockResolvedValue(PRINTER_STATUS_ONLINE as never);
    vi.mocked(api.getPrinter).mockResolvedValue({ id: 1, name: 'Test Printer' } as never);
    vi.mocked(api.assignSpool).mockResolvedValue({} as never);
    vi.mocked(api.assignSpoolmanSlot).mockResolvedValue({} as never);
  });

  it('renders modal when open', async () => {
    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={1}
        spoolmanMode={false}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Assign.*to AMS/i)).toBeInTheDocument();
    });
  });

  it('renders nothing when closed', () => {
    render(
      <AssignToAmsModal
        isOpen={false}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={1}
        spoolmanMode={false}
      />
    );

    expect(screen.queryByText(/Assign.*to AMS/i)).not.toBeInTheDocument();
  });

  describe('API routing based on spoolmanMode', () => {
    it('calls assignSpool when spoolmanMode is false', async () => {
      const user = userEvent.setup();
      render(
        <AssignToAmsModal
          isOpen={true}
          onClose={vi.fn()}
          spool={SPOOL as never}
          printerId={1}
          spoolmanMode={false}
        />
      );

      await waitFor(() => {
        expect(screen.queryAllByRole('button').length).toBeGreaterThan(0);
      });

      // Click first available slot button
      const slotButtons = screen.queryAllByTitle(/AMS Slot/i);
      if (slotButtons.length > 0) {
        await user.click(slotButtons[0]);
        await waitFor(() => {
          expect(api.assignSpool).toHaveBeenCalledWith(
            expect.objectContaining({ spool_id: 42, printer_id: 1 })
          );
        });
        expect(api.assignSpoolmanSlot).not.toHaveBeenCalled();
      }
    });

    it('calls assignSpoolmanSlot when spoolmanMode is true', async () => {
      const user = userEvent.setup();
      render(
        <AssignToAmsModal
          isOpen={true}
          onClose={vi.fn()}
          spool={SPOOL as never}
          printerId={1}
          spoolmanMode={true}
        />
      );

      await waitFor(() => {
        expect(screen.queryAllByRole('button').length).toBeGreaterThan(0);
      });

      const slotButtons = screen.queryAllByTitle(/AMS Slot/i);
      expect(slotButtons.length).toBeGreaterThan(0);
      if (slotButtons.length > 0) {
        await user.click(slotButtons[0]);
        await waitFor(() => {
          expect(api.assignSpoolmanSlot).toHaveBeenCalledWith(
            expect.objectContaining({ spoolman_spool_id: 42, printer_id: 1 })
          );
        });
        expect(api.assignSpool).not.toHaveBeenCalled();
      }
    });
  });
});
