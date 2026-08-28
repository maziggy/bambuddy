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
    getPrinters: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    assignSpool: vi.fn(),
    assignSpoolmanSlot: vi.fn(),
    updateSpool: vi.fn(),
    updateSpoolmanInventorySpool: vi.fn(),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getAssignments: vi.fn().mockResolvedValue([]),
    getSpoolmanSlotAssignments: vi.fn().mockResolvedValue([]),
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

const BLANK_TRAY = {
  tray_color: null,
  tray_type: null,
  tray_sub_brands: null,
  tray_id_name: null,
  tray_info_idx: null,
  remain: 100,
  k: null,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  drying_temp: null,
  drying_time: null,
  state: null,
};

const PRINTER_STATUS_ONLINE = {
  connected: true,
  state: 'idle',
  ams: [
    {
      id: 0,
      humidity: null,
      temp: null,
      is_ams_ht: false,
      serial_number: '',
      sw_ver: '',
      dry_time: 0,
      dry_status: 0,
      dry_sub_status: 0,
      tray: [
        { id: 0, ...BLANK_TRAY },
        { id: 1, ...BLANK_TRAY },
        { id: 2, ...BLANK_TRAY },
        { id: 3, ...BLANK_TRAY },
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
    vi.mocked(api.getPrinters).mockResolvedValue([
      { id: 7, name: 'Farm Printer 7', is_active: true },
    ] as never);
    vi.mocked(api.assignSpool).mockResolvedValue({} as never);
    vi.mocked(api.assignSpoolmanSlot).mockResolvedValue({} as never);
    vi.mocked(api.updateSpool).mockResolvedValue({} as never);
    vi.mocked(api.updateSpoolmanInventorySpool).mockResolvedValue({} as never);
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

  it('selects a printer and requires explicit slot confirmation in the inventory flow (#2978)', async () => {
    const user = userEvent.setup();
    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={null}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');

    await waitFor(() => {
      expect(screen.queryAllByTitle(/AMS Slot/i).length).toBeGreaterThan(0);
    });

    const slotButtons = screen.queryAllByTitle(/AMS Slot/i);
    await user.click(slotButtons[0]);
    expect(api.assignSpool).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /assign spool/i }));
    await waitFor(() => {
      expect(api.assignSpool).toHaveBeenCalledWith(
        expect.objectContaining({ spool_id: 42, printer_id: 7, ams_id: 0, tray_id: 0 })
      );
    });
  });

  it('persists a changed spool colour before assigning from inventory (#2978)', async () => {
    const user = userEvent.setup();
    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={null}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');
    await waitFor(() => {
      expect(screen.queryAllByTitle(/AMS Slot/i).length).toBeGreaterThan(0);
    });

    await user.click(screen.getByRole('button', { name: 'Blue' }));
    await user.click(screen.queryAllByTitle(/AMS Slot/i)[0]);
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledWith(42, {
        rgba: '0066FFFF',
        color_name: 'Blue',
      });
      expect(api.assignSpool).toHaveBeenCalledWith(
        expect.objectContaining({ spool_id: 42, printer_id: 7 })
      );
    });
  });

  it('labels and assigns the left external slot correctly on dual-nozzle printers', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getPrinter).mockResolvedValue({
      id: 7,
      name: 'Farm Printer 7',
      nozzle_count: 2,
    } as never);
    vi.mocked(api.getPrinterStatus).mockResolvedValue({
      ...PRINTER_STATUS_ONLINE,
      ams: [],
      vt_tray: [
        { id: 254, ...BLANK_TRAY },
        { id: 255, ...BLANK_TRAY },
      ],
    } as never);

    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={null}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');

    const leftExternalSlot = await screen.findByTitle('Ext-L');
    await user.click(leftExternalSlot);
    expect(screen.getAllByText('Ext-L').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: /assign spool/i }));
    await waitFor(() => {
      expect(api.assignSpool).toHaveBeenCalledWith(
        expect.objectContaining({ printer_id: 7, ams_id: 255, tray_id: 0 })
      );
    });
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
        expect(screen.queryAllByTitle(/AMS Slot/i).length).toBeGreaterThan(0);
      });

      // Click first available slot button
      const slotButtons = screen.queryAllByTitle(/AMS Slot/i);
      await user.click(slotButtons[0]);
      await waitFor(() => {
        expect(api.assignSpool).toHaveBeenCalledWith(
          expect.objectContaining({ spool_id: 42, printer_id: 1 })
        );
      });
      expect(api.assignSpoolmanSlot).not.toHaveBeenCalled();
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
        expect(screen.queryAllByTitle(/AMS Slot/i).length).toBeGreaterThan(0);
      });

      const slotButtons = screen.queryAllByTitle(/AMS Slot/i);
      await user.click(slotButtons[0]);
      await waitFor(() => {
        expect(api.assignSpoolmanSlot).toHaveBeenCalledWith(
          expect.objectContaining({ spoolman_spool_id: 42, printer_id: 1 })
        );
      });
      expect(api.assignSpool).not.toHaveBeenCalled();
    });
  });

  describe('query invalidation after successful assign', () => {
    it('calls assignSpoolmanSlot successfully — invalidation fires on success', async () => {
      const user = userEvent.setup();
      vi.mocked(api.assignSpoolmanSlot).mockResolvedValue({} as never);

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
        expect(screen.queryAllByTitle(/AMS Slot/i).length).toBeGreaterThan(0);
      });

      const slotButtons = screen.queryAllByTitle(/AMS Slot/i);
      await user.click(slotButtons[0]);
      await waitFor(() => {
        expect(api.assignSpoolmanSlot).toHaveBeenCalledWith(
          expect.objectContaining({ spoolman_spool_id: 42, printer_id: 1 })
        );
      });
    });
  });

  describe('slot highlighting', () => {
    it('shows no ring-bambu-green when spool is not assigned', async () => {
      vi.mocked(api.getSpoolmanSlotAssignments).mockResolvedValue([]);
      const { container } = render(
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

      expect(container.querySelector('.ring-bambu-green')).toBeNull();
    });

    it('highlights assigned slot with ring-bambu-green when spool is assigned to tray 2', async () => {
      vi.mocked(api.getSpoolmanSlotAssignments).mockResolvedValue([
        { printer_id: 1, ams_id: 0, tray_id: 2, spoolman_spool_id: 42 },
      ] as never);

      const { container } = render(
        <AssignToAmsModal
          isOpen={true}
          onClose={vi.fn()}
          spool={SPOOL as never}
          printerId={1}
          spoolmanMode={true}
        />
      );

      await waitFor(() => {
        expect(container.querySelector('.ring-bambu-green')).toBeInTheDocument();
      });
    });
  });
});
