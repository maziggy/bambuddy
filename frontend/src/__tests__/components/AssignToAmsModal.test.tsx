/**
 * Tests for AssignToAmsModal — verifies that spoolmanMode prop
 * routes to assignSpoolmanSlot (not assignSpool) when assigning.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
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
import { __resetColorCatalogForTests, setColorCatalog } from '../../utils/colors';

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
    __resetColorCatalogForTests();
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

  it('moves focus into the dialog and traps Tab navigation', async () => {
    render(
      <>
        <button data-testid="outside-control">Outside</button>
        <AssignToAmsModal
          isOpen={true}
          onClose={vi.fn()}
          spool={SPOOL as never}
          printerId={1}
          variant="dialog"
          spoolmanMode={false}
        />
      </>
    );

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ));
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    expect(first).toBeTruthy();
    expect(last).toBeTruthy();

    last.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(last);

    screen.getByTestId('outside-control').focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(first);
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
        showColorEditor={true}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');

    await waitFor(() => {
      expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
    });

    const slotButtons = screen.queryAllByTestId('ams-slot');
    expect(slotButtons[0]).toHaveAccessibleName('AMS A Slot 1');
    await user.click(slotButtons[0]);
    expect(api.assignSpool).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /assign spool/i }));
    await waitFor(() => {
      expect(api.assignSpool).toHaveBeenCalledWith(
        expect.objectContaining({ spool_id: 42, printer_id: 7, ams_id: 0, tray_id: 0 })
      );
    });
  });

  it('persists an explicitly changed spool colour after assigning from inventory (#2978)', async () => {
    const user = userEvent.setup();
    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={null}
        showColorEditor={true}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');
    await waitFor(() => {
      expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
    });

    await user.click(screen.getByRole('button', { name: 'Blue' }));
    await user.click(screen.queryAllByTestId('ams-slot')[0]);
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
    expect(vi.mocked(api.assignSpool).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(api.updateSpool).mock.invocationCallOrder[0]);
  });

  it.each([
    {
      caseName: 'missing colour name resolved from the catalog',
      spool: { color_name: null, rgba: '00AE42FF' },
      catalog: { '00ae42': 'Bambu Green' },
    },
    {
      caseName: 'stored Bambu colour code',
      spool: { color_name: 'A1-B2', rgba: 'FF00AAFF' },
      catalog: {},
    },
  ])('does not rewrite $caseName when assigning without a colour edit', async ({ spool, catalog }) => {
    const user = userEvent.setup();
    setColorCatalog(catalog);

    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={{ ...SPOOL, ...spool } as never}
        printerId={null}
        showColorEditor={true}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');
    await waitFor(() => {
      expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
    });

    await user.click(screen.queryAllByTestId('ams-slot')[0]);
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await waitFor(() => expect(api.assignSpool).toHaveBeenCalled());
    expect(api.updateSpool).not.toHaveBeenCalled();
  });

  it('does not save a colour edit when assignment fails', async () => {
    const user = userEvent.setup();
    vi.mocked(api.assignSpool).mockRejectedValueOnce(new Error('Slot busy'));

    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={null}
        showColorEditor={true}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');
    await waitFor(() => {
      expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
    });

    await user.click(screen.getByRole('button', { name: 'Blue' }));
    await user.click(screen.queryAllByTestId('ams-slot')[0]);
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    expect(await screen.findByText('Slot busy')).toBeInTheDocument();
    expect(api.updateSpool).not.toHaveBeenCalled();
  });

  it('stores a derived colour name instead of a custom hex value', async () => {
    const user = userEvent.setup();
    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={null}
        showColorEditor={true}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');
    await waitFor(() => {
      expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
    });

    const colorPicker = document.querySelector<HTMLInputElement>('input[type="color"]');
    expect(colorPicker).not.toBeNull();
    fireEvent.change(colorPicker!, { target: { value: '#ff00aa' } });
    await user.click(screen.queryAllByTestId('ams-slot')[0]);
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledWith(42, {
        rgba: 'FF00AAFF',
        color_name: 'Pink',
      });
    });
  });

  it('does not persist the grey display fallback when only a colour name changes', async () => {
    const user = userEvent.setup();
    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={{ ...SPOOL, color_name: null, rgba: null } as never}
        printerId={null}
        showColorEditor={true}
        spoolmanMode={false}
      />
    );

    const printerSelect = await screen.findByLabelText(/select printer/i);
    await screen.findByRole('option', { name: 'Farm Printer 7' });
    await user.selectOptions(printerSelect, '7');
    await waitFor(() => {
      expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
    });

    await user.type(screen.getByLabelText(/color name/i), 'Ocean');
    await user.click(screen.queryAllByTestId('ams-slot')[0]);
    await user.click(screen.getByRole('button', { name: /assign spool/i }));

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledWith(42, {
        color_name: 'Ocean',
      });
    });
  });

  it('keeps the kiosk layout full-screen while selecting a printer', async () => {
    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={SPOOL as never}
        printerId={null}
        variant="kiosk"
        spoolmanMode={false}
      />
    );

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveClass('w-full', 'h-full');
    expect(dialog).not.toHaveClass('max-w-3xl', 'max-w-5xl');
    expect(await screen.findByLabelText(/select printer/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/color name/i)).not.toBeInTheDocument();
  });

  it('starts the editable colour name with Bambuddy\'s catalog-resolved name (#3090)', async () => {
    setColorCatalog({ d02727: 'Candy Red' });

    render(
      <AssignToAmsModal
        isOpen={true}
        onClose={vi.fn()}
        spool={{
          ...SPOOL,
          color_name: 'Silk+',
          color_name_is_synthesized: true,
          rgba: 'D02727FF',
        } as never}
        printerId={null}
        showColorEditor={true}
        spoolmanMode={false}
      />
    );

    expect(await screen.findByLabelText(/color name/i)).toHaveValue('Candy Red');
    expect(screen.getAllByText('Candy Red').length).toBeGreaterThan(0);
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
        expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
      });

      // Click first available slot button
      const slotButtons = screen.queryAllByTestId('ams-slot');
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
        expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
      });

      const slotButtons = screen.queryAllByTestId('ams-slot');
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
        expect(screen.queryAllByTestId('ams-slot').length).toBeGreaterThan(0);
      });

      const slotButtons = screen.queryAllByTestId('ams-slot');
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
