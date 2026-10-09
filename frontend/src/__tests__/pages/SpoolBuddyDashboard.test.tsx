/**
 * Tests for SpoolBuddyDashboard:
 * - Shows stats bar (Spools, Materials, Brands)
 * - Shows "Ready to scan" idle state when no tag detected
 * - Shows device status section
 * - Shows "Device Offline" state when device offline
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, act } from '@testing-library/react';
import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { SpoolBuddyDashboard } from '../../pages/spoolbuddy/SpoolBuddyDashboard';
import { ToastProvider } from '../../contexts/ToastContext';

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn().mockResolvedValue([
      { id: 1, material: 'PLA', brand: 'Bambu', tag_uid: 'AA:BB', tray_uuid: null, archived_at: null, color_name: 'Red', rgba: 'FF0000FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 100 },
      { id: 2, material: 'PETG', brand: 'Bambu', tag_uid: 'CC:DD', tray_uuid: null, archived_at: null, color_name: 'Blue', rgba: '0000FFFF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 200 },
      { id: 3, material: 'ABS', brand: 'Polymaker', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'White', rgba: 'FFFFFFFF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
    ]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue({ connected: false }),
    getSpoolmanSettings: vi.fn().mockResolvedValue({ spoolman_enabled: 'false', spoolman_url: '', spoolman_sync_mode: 'off', spoolman_disable_weight_sync: 'false', spoolman_report_partial_usage: 'false' }),
    getSpoolmanInventorySpools: vi.fn().mockResolvedValue([]),
    getSpoolmanSlotAssignments: vi.fn().mockResolvedValue([]),
    getAssignments: vi.fn().mockResolvedValue([]),
    linkTagToSpool: vi.fn().mockResolvedValue({}),
    linkTagToSpoolmanSpool: vi.fn().mockResolvedValue({}),
    createSpool: vi.fn().mockResolvedValue({ id: 4 }),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 4 }),
    clearPlate: vi.fn().mockResolvedValue({}),
  },
  spoolbuddyApi: {
    getDevices: vi.fn().mockResolvedValue([]),
  },
  // Real class, not a stub: the link handler branches on `instanceof ApiError`
  // to decide whether a failure carries a structured code (#3110).
  ApiError: class ApiError extends Error {
    status: number;
    code: string | null;
    detail: Record<string, unknown> | null;
    constructor(message: string, status: number, code: string | null = null, detail: Record<string, unknown> | null = null) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
      this.detail = detail;
    }
  },
}));

// Hoisted so the react-i18next factory can reach it: what the toast shows is
// only half the contract -- the other half is that the spool id reaches the
// interpolation bag (#3110), and the key-as-text mock cannot show that.
const i18nSpy = vi.hoisted(() => ({ t: vi.fn() }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Mirrors i18next's overloaded (key, defaultValue?, options?) signature --
    // including the (key, options) form, where the second argument is the
    // interpolation bag and there is no default value -- with simple {{var}}
    // interpolation so tests can assert on the rendered text.
    t: (key: string, fallbackOrOptions?: string | Record<string, unknown>, maybeOptions?: Record<string, unknown>) => {
      i18nSpy.t(key, fallbackOrOptions, maybeOptions);
      const fallback = typeof fallbackOrOptions === 'string' ? fallbackOrOptions : undefined;
      const options = typeof fallbackOrOptions === 'object' ? fallbackOrOptions : maybeOptions;
      const text = fallback ?? key;
      if (!options) return text;
      return text.replace(/\{\{(\w+)\}\}/g, (_m, k) => String(options[k] ?? ''));
    },
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

const mockOutletContext = {
  selectedPrinterId: null,
  setSelectedPrinterId: vi.fn(),
  sbState: {
    weight: null,
    weightStable: false,
    rawAdc: null,
    matchedSpool: null,
    unknownTagUid: null,
    unknownTrayUuid: null,
    deviceOnline: true,
    deviceId: 'dev-1',
    remainingWeight: null,
    netWeight: null,
  },
  setAlert: vi.fn(),
  displayBrightness: 100,
  setDisplayBrightness: vi.fn(),
  displayBlankTimeout: 0,
  setDisplayBlankTimeout: vi.fn(),
};

function renderPage(overrides: Partial<typeof mockOutletContext['sbState']> = {}) {
  const ctx = {
    ...mockOutletContext,
    sbState: { ...mockOutletContext.sbState, ...overrides },
  };
  function Wrapper() {
    return <Outlet context={ctx} />;
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <ToastProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/spoolbuddy']}>
          <Routes>
            <Route element={<Wrapper />}>
              <Route path="spoolbuddy" element={<SpoolBuddyDashboard />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ToastProvider>
  );
}

describe('SpoolBuddyDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows stats bar with spool count, materials, and brands', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Spools')).toBeDefined();
      expect(screen.getByText('Materials')).toBeDefined();
      expect(screen.getByText('Brands')).toBeDefined();
      // Check that the stats numbers are rendered (3 spools, 3 materials, 2 brands)
      const statNumbers = screen.getAllByText(/^[0-9]+$/);
      expect(statNumbers.length).toBeGreaterThanOrEqual(3);
    });
  });

  it('shows "Ready to scan" idle state when device online with no tag', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Ready to scan')).toBeDefined();
      expect(screen.getByText('Place a spool on the scale to identify it')).toBeDefined();
    });
  });

  it('shows device status section', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Device')).toBeDefined();
    });
  });

  it('shows "Online" when device is online', async () => {
    renderPage({ deviceOnline: true });
    await waitFor(() => {
      expect(screen.getByText('Online')).toBeDefined();
    });
  });

  it('shows "Device Offline" state when device offline', async () => {
    renderPage({ deviceOnline: false });
    await waitFor(() => {
      expect(screen.getByText('Device Offline')).toBeDefined();
      expect(screen.getByText('Connect the SpoolBuddy display to scan spools')).toBeDefined();
    });
  });

  it('shows current spool section heading', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Current Spool')).toBeDefined();
    });
  });

  describe('plate-clear row', () => {
    // We re-mock api.getPrinters / getPrinterStatus per test so each scenario
    // controls exactly which printers report awaiting_plate_clear.
    it('does not render the plate-clear button when no printer needs it', async () => {
      const { api } = await import('../../api/client');
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 1, name: 'X1C' },
      ]);
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
        connected: true,
        awaiting_plate_clear: false,
      });
      renderPage();
      await waitFor(() => {
        expect(screen.getByText('X1C')).toBeDefined();
      });
      expect(screen.queryByTestId('plate-clear-section')).toBeNull();
    });

    it('renders a plate-clear pill only for printers with awaiting_plate_clear=true', async () => {
      const { api } = await import('../../api/client');
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 1, name: 'X1C' },
        { id: 2, name: 'P1S' },
      ]);
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockImplementation((printerId: number) =>
        Promise.resolve({
          connected: true,
          awaiting_plate_clear: printerId === 2,
        })
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId('plate-clear-button-2')).toBeDefined();
      });
      expect(screen.queryByTestId('plate-clear-button-1')).toBeNull();
      // Pill content: printer name + "Clear" label, plus full "Plate ready: P1S" in title attr.
      const pill = screen.getByTestId('plate-clear-button-2');
      expect(pill.getAttribute('title')).toBe('Plate ready: P1S');
      expect(pill.textContent).toContain('P1S');
      expect(pill.textContent).toContain('Clear');
    });

    it('renders multiple plate-clear pills inline when several printers are pending', async () => {
      const { api } = await import('../../api/client');
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 1, name: 'A' },
        { id: 2, name: 'B' },
        { id: 3, name: 'C' },
      ]);
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
        connected: true,
        awaiting_plate_clear: true,
      });
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId('plate-clear-button-1')).toBeDefined();
        expect(screen.getByTestId('plate-clear-button-2')).toBeDefined();
        expect(screen.getByTestId('plate-clear-button-3')).toBeDefined();
      });
      // Pills sit in the same flex-wrap container so they flow inline.
      const section = screen.getByTestId('plate-clear-section');
      expect(section.className).toContain('flex-wrap');
    });

    it('calls api.clearPlate with the printer id when clicked', async () => {
      const { api } = await import('../../api/client');
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 7, name: 'H2D' },
      ]);
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
        connected: true,
        awaiting_plate_clear: true,
      });
      renderPage();
      const btn = await waitFor(() => screen.getByTestId('plate-clear-button-7'));
      fireEvent.click(btn);
      await waitFor(() => {
        expect(api.clearPlate).toHaveBeenCalledWith(7);
      });
    });

    it('hides the row optimistically after a successful click without a refetch', async () => {
      const { api } = await import('../../api/client');
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 9, name: 'X1E' },
      ]);
      // Stable resolve — even if refetch happens it would still report pending,
      // so a disappearing row proves the optimistic cache write worked.
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
        connected: true,
        awaiting_plate_clear: true,
      });
      renderPage();
      const btn = await waitFor(() => screen.getByTestId('plate-clear-button-9'));
      fireEvent.click(btn);
      await waitFor(() => {
        expect(screen.queryByTestId('plate-clear-button-9')).toBeNull();
      });
    });
  });


  describe('assign-spool row', () => {
    const occupiedAmsStatus = {
      connected: true,
      awaiting_plate_clear: false,
      ams: [
        {
          id: 0,
          is_ams_ht: false,
          tray: [
            {
              id: 0,
              state: 10,
              tray_type: 'PLA',
              tray_color: 'FF0000FF',
              tray_sub_brands: null,
              tray_id_name: null,
              tray_info_idx: null,
              remain: 95,
              k: null,
              cali_idx: null,
              tag_uid: null,
              tray_uuid: null,
              nozzle_temp_min: null,
              nozzle_temp_max: null,
              drying_temp: null,
              drying_time: null,
            },
          ],
        },
      ],
    };

    it('shows Assign pill for an occupied unassigned slot in internal inventory mode', async () => {
      const { api } = await import('../../api/client');

      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'false',
        spoolman_url: '',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 1, name: 'A1-A' },
      ]);
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockResolvedValue(occupiedAmsStatus);
      (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([]);

      renderPage();

      const btn = await waitFor(() => screen.getByTestId('spool-assign-button-1'));
      expect(btn.textContent).toContain('A1-A');
      expect(btn.textContent).toContain('Assign 1');
    });

    it('shows Assign pill for an occupied unassigned slot in Spoolman mode', async () => {
      const { api } = await import('../../api/client');

      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 2, name: 'P1S-A' },
      ]);
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockResolvedValue(occupiedAmsStatus);
      (api.getSpoolmanSlotAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([]);

      renderPage();

      await waitFor(() => {
        expect(screen.getByTestId('spool-assign-section')).toBeDefined();
      });
      expect(screen.getByText(/Assign\s*1/)).toBeDefined();
    });

    it('does not show Assign pill when occupied slot already has an inventory assignment', async () => {
      const { api } = await import('../../api/client');

      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'false',
        spoolman_url: '',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
        { id: 3, name: 'H2D-A' },
      ]);
      (api.getPrinterStatus as ReturnType<typeof vi.fn>).mockResolvedValue(occupiedAmsStatus);
      (api.getAssignments as ReturnType<typeof vi.fn>).mockResolvedValue([
        {
          id: 99,
          spool_id: 10,
          printer_id: 3,
          printer_name: 'H2D-A',
          ams_id: 0,
          tray_id: 0,
          fingerprint_color: null,
          fingerprint_type: null,
          spool: null,
          configured: true,
          created_at: '2026-01-01T00:00:00Z',
        },
      ]);

      renderPage();

      await waitFor(() => {
        expect(screen.getByText('H2D-A')).toBeDefined();
      });
      expect(screen.queryByTestId('spool-assign-button-3')).toBeNull();
    });
  });

  describe('Spoolman mode', () => {
    it('fetches from getSpoolmanInventorySpools when Spoolman is enabled', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 10, material: 'PLA', brand: 'Bambu', tag_uid: 'SM:01', tray_uuid: null, archived_at: null, color_name: 'Green', rgba: '00FF00FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);

      renderPage();

      await waitFor(() => {
        expect(api.getSpoolmanInventorySpools).toHaveBeenCalled();
      });
    });

    it('still uses getSpools when Spoolman is disabled', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'false',
        spoolman_url: '',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });

      renderPage();

      await waitFor(() => {
        expect(api.getSpools).toHaveBeenCalled();
      });
    });

    it('excludes tray_uuid spools from the untagged list in Spoolman mode', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      // One spool has tray_uuid (linked via Bambu) → excluded from untagged
      // One spool has neither tag_uid nor tray_uuid → included
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 20, material: 'PETG', brand: 'Bambu', tag_uid: null, tray_uuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF', archived_at: null, color_name: 'Blue', rgba: '0000FFFF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
        { id: 21, material: 'ABS', brand: 'Polymaker', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Black', rgba: '000000FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);

      renderPage({ unknownTagUid: 'AABB1122', unknownTrayUuid: 'CAFEBABECAFEBABECAFEBABECAFEBABE' });

      // Open the link modal
      const linkBtn = await waitFor(() => screen.getByText('Assign Spool'));
      fireEvent.click(linkBtn);

      await waitFor(() => {
        // Only the ABS spool (id=21) should appear — the PETG with tray_uuid is excluded
        expect(screen.getByText('Black')).toBeDefined();
        expect(screen.queryByText('Blue')).toBeNull();
      });
    });

    it('sends the tray UUID when linking a Bambu tag in Spoolman mode (#984)', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 30, material: 'TPU', brand: 'Bambu', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Orange', rgba: 'FF6600FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);

      renderPage({
        unknownTagUid: 'AABB1122334455FF',
        unknownTrayUuid: '9E0B0717BEE94D7887EB1D8DFD1A14F3',
      });

      const linkBtn = await waitFor(() => screen.getByText('Assign Spool'));
      fireEvent.click(linkBtn);

      const spoolBtn = await waitFor(() => screen.getByText('Orange'));
      fireEvent.click(spoolBtn);

      const confirmBtn = await waitFor(() => screen.getByText('Link Tag'));
      fireEvent.click(confirmBtn);

      // The route stores tray_uuid over tag_uid -- the value the AMS keeps in
      // extra.tag, and the same on both tags of the spool.
      await waitFor(() => {
        expect(api.linkTagToSpoolmanSpool).toHaveBeenCalledWith(30, {
          tag_uid: 'AABB1122334455FF',
          tray_uuid: '9E0B0717BEE94D7887EB1D8DFD1A14F3',
        });
      });
    });

    it('links by tag_uid alone in Spoolman mode when the tag has no tray UUID', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 30, material: 'TPU', brand: 'Bambu', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Orange', rgba: 'FF6600FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);

      renderPage({ unknownTagUid: 'AABB1122334455FF' });

      fireEvent.click(await waitFor(() => screen.getByText('Assign Spool')));
      fireEvent.click(await waitFor(() => screen.getByText('Orange')));
      fireEvent.click(await waitFor(() => screen.getByText('Link Tag')));

      await waitFor(() => {
        expect(api.linkTagToSpoolmanSpool).toHaveBeenCalledWith(30, {
          tag_uid: 'AABB1122334455FF',
          tray_uuid: undefined,
        });
      });
    });

    it('shows the spool card for a Spoolman spool stored under the tag\'s tray UUID (#984)', async () => {
      // After quick-add or link, Spoolman's extra.tag holds the tray UUID and the
      // spool comes back with tray_uuid set and tag_uid null. The card must still
      // find it, or the kiosk keeps offering "Add to Inventory" until a re-scan.
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 40, material: 'PLA', brand: 'Bambu', tag_uid: null, tray_uuid: '9E0B0717BEE94D7887EB1D8DFD1A14F3', archived_at: null, color_name: 'Teal', rgba: '008080FF', subtype: 'Matte', label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);

      renderPage({ unknownTagUid: 'AABB1122', unknownTrayUuid: '9E0B0717BEE94D7887EB1D8DFD1A14F3' });

      await waitFor(() => expect(screen.getByText('Sync Weight')).toBeDefined());
      expect(screen.queryByText('Add to Inventory')).toBeNull();
    });

    it('quick-adds a Spoolman spool linked by its tray UUID (#984)', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });

      renderPage({ unknownTagUid: 'AABB1122334455FF', unknownTrayUuid: '9E0B0717BEE94D7887EB1D8DFD1A14F3' });

      fireEvent.click(await waitFor(() => screen.getAllByText('Add to Inventory')[0]));
      fireEvent.click(await waitFor(() => screen.getByText('Add Anyway')));

      await waitFor(() => {
        expect(api.linkTagToSpoolmanSpool).toHaveBeenCalledWith(4, {
          tag_uid: 'AABB1122334455FF',
          tray_uuid: '9E0B0717BEE94D7887EB1D8DFD1A14F3',
        });
      });
    });

    it('quick-adds a Spoolman spool without a tare, so it inherits the filament type\'s (#2908)', async () => {
      // The quick-create has no tare input. The 250 it used to send was a
      // placeholder, and since #2908 a sent value lands on the spool's own
      // spool_weight -- every kiosk-created spool would stop inheriting.
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });

      renderPage({ unknownTagUid: 'AABB1122334455FF' });

      fireEvent.click(await waitFor(() => screen.getAllByText('Add to Inventory')[0]));
      fireEvent.click(await waitFor(() => screen.getByText('Add Anyway')));

      await waitFor(() => {
        expect(api.createSpoolmanInventorySpool).toHaveBeenCalledTimes(1);
      });
      const payload = (api.createSpoolmanInventorySpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(payload).not.toHaveProperty('core_weight');
      expect(payload).not.toHaveProperty('core_weight_catalog_id');
      expect(api.createSpool).not.toHaveBeenCalled();
    });

    it('switches to SpoolInfoCard and hides UnknownTagCard after successful Spoolman link', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      const linkedSpool = {
        id: 30, material: 'TPU', brand: 'Bambu', tag_uid: null,
        tray_uuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF', archived_at: null,
        color_name: 'Orange', rgba: 'FF6600FF', subtype: null,
        label_weight: 1000, core_weight: 250, weight_used: 0,
        created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z',
      };
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { ...linkedSpool, tag_uid: null, tray_uuid: null },
      ]);
      (api.linkTagToSpoolmanSpool as ReturnType<typeof vi.fn>).mockResolvedValue(linkedSpool);

      renderPage({
        unknownTagUid: 'AABB1122334455FF',
        unknownTrayUuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
      });

      const linkBtn = await waitFor(() => screen.getByText('Assign Spool'));
      fireEvent.click(linkBtn);

      const spoolBtn = await waitFor(() => screen.getByText('Orange'));
      fireEvent.click(spoolBtn);

      const confirmBtn = await waitFor(() => screen.getByText('Link Tag'));
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(screen.queryByText('Assign Spool')).toBeNull();
        expect(screen.getByText('Sync Weight')).toBeDefined();
        expect(mockShowToast).toHaveBeenCalledWith('spoolman.linkSuccess', 'success');
      });
    });

    it('shows error toast and closes modal when Spoolman link fails', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 30, material: 'TPU', brand: 'Bambu', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Orange', rgba: 'FF6600FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);
      (api.linkTagToSpoolmanSpool as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('503'));

      renderPage({
        unknownTagUid: 'AABB1122334455FF',
        unknownTrayUuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
      });

      const linkBtn = await waitFor(() => screen.getByText('Assign Spool'));
      fireEvent.click(linkBtn);
      const spoolBtn = await waitFor(() => screen.getByText('Orange'));
      fireEvent.click(spoolBtn);
      fireEvent.click(await waitFor(() => screen.getByText('Link Tag')));

      await waitFor(() => {
        // Error toast shown
        expect(mockShowToast).toHaveBeenCalledWith('spoolman.linkFailed', 'error');
        // Modal closed via finally
        expect(screen.queryByText('Link Tag')).toBeNull();
        // UnknownTagCard still visible — no card switch on failure
        expect(screen.getByText('Assign Spool')).toBeDefined();
      });
    });

    it('names the spool holding the tag when Spoolman refuses the link (#3110)', async () => {
      const { api, ApiError } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 30, material: 'TPU', brand: 'Bambu', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Orange', rgba: 'FF6600FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);
      (api.linkTagToSpoolmanSpool as ReturnType<typeof vi.fn>).mockRejectedValue(
        new ApiError('Tray UUID is already linked to spool 42', 409, 'tag_already_linked', {
          code: 'tag_already_linked',
          message: 'Tray UUID is already linked to spool 42',
          spool_id: 42,
          field: 'tray_uuid',
        }),
      );

      renderPage({
        unknownTagUid: 'AABB1122334455FF',
        unknownTrayUuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
      });

      const linkBtn = await waitFor(() => screen.getByText('Assign Spool'));
      fireEvent.click(linkBtn);
      fireEvent.click(await waitFor(() => screen.getByText('Orange')));
      fireEvent.click(await waitFor(() => screen.getByText('Link Tag')));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith('inventory.tagAlreadyLinked', 'error');
      });
      // The operator can only walk to the other spool if the id is in the
      // sentence, so assert it reached the interpolation bag rather than
      // trusting the key-as-text mock's output.
      expect(i18nSpy.t).toHaveBeenCalledWith('inventory.tagAlreadyLinked', { id: 42 }, undefined);
    });

    it('keeps the generic toast for a link failure that carries no code', async () => {
      const { api, ApiError } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 30, material: 'TPU', brand: 'Bambu', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Orange', rgba: 'FF6600FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);
      // A 409 from something other than a tag conflict, and a plain-string
      // detail, must not be dressed up as one.
      (api.linkTagToSpoolmanSpool as ReturnType<typeof vi.fn>).mockRejectedValue(
        new ApiError('Spoolman unavailable', 503),
      );

      renderPage({
        unknownTagUid: 'AABB1122334455FF',
        unknownTrayUuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
      });

      const linkBtn = await waitFor(() => screen.getByText('Assign Spool'));
      fireEvent.click(linkBtn);
      fireEvent.click(await waitFor(() => screen.getByText('Orange')));
      fireEvent.click(await waitFor(() => screen.getByText('Link Tag')));

      await waitFor(() => {
        expect(mockShowToast).toHaveBeenCalledWith('spoolman.linkFailed', 'error');
      });
    });

    it('clears justLinkedSpool and shows new UnknownTagCard when a different tag is placed', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      const linkedSpool = {
        id: 30, material: 'TPU', brand: 'Bambu', tag_uid: null,
        tray_uuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF', archived_at: null,
        color_name: 'Orange', rgba: 'FF6600FF', subtype: null,
        label_weight: 1000, core_weight: 250, weight_used: 0,
        created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z',
      };
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { ...linkedSpool, tag_uid: null, tray_uuid: null },
        { id: 31, material: 'PLA', brand: 'Bambu', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Green', rgba: '00FF00FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);
      (api.linkTagToSpoolmanSpool as ReturnType<typeof vi.fn>).mockResolvedValue(linkedSpool);

      // Stateful wrapper so sbState can be updated mid-test. Stash the setter
      // on a ref-shaped object instead of a bare `let` reassigned during
      // render — react-hooks/globals (eslint-plugin-react-hooks v5) flags
      // that as a side effect during render. Mutating a property on a
      // pre-allocated object is fine because the object identity doesn't
      // change.
      const setterRef: { current: React.Dispatch<React.SetStateAction<typeof mockOutletContext.sbState>> | null } = { current: null };
      function DynWrapper() {
        const [sbState, setSbState] = React.useState({
          ...mockOutletContext.sbState,
          unknownTagUid: 'AABB1122334455FF',
          unknownTrayUuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
        });
        setterRef.current = setSbState;
        return <Outlet context={{ ...mockOutletContext, sbState }} />;
      }
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
      render(
        <ToastProvider>
          <QueryClientProvider client={qc}>
            <MemoryRouter initialEntries={['/spoolbuddy']}>
              <Routes>
                <Route element={<DynWrapper />}>
                  <Route path="spoolbuddy" element={<SpoolBuddyDashboard />} />
                </Route>
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        </ToastProvider>
      );

      // Link spool — SpoolInfoCard appears via justLinkedSpool
      fireEvent.click(await waitFor(() => screen.getByText('Assign Spool')));
      fireEvent.click(await waitFor(() => screen.getByText('Orange')));
      fireEvent.click(await waitFor(() => screen.getByText('Link Tag')));
      await waitFor(() => expect(screen.getByText('Sync Weight')).toBeDefined());

      // Different tag placed → justLinkedSpool cleared
      act(() => setterRef.current!((prev) => ({ ...prev, unknownTagUid: 'CCDD5566', unknownTrayUuid: null })));

      await waitFor(() => {
        expect(screen.queryByText('Sync Weight')).toBeNull();
        expect(screen.getByText('Assign Spool')).toBeDefined();
      });
    });

    it('calls linkTagToSpool (local) when Spoolman is disabled — no regression', async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'false',
        spoolman_url: '',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 3, material: 'ABS', brand: 'Polymaker', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'White', rgba: 'FFFFFFFF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);

      renderPage({ unknownTagUid: 'AABB9999' });

      const linkBtn = await waitFor(() => screen.getByText('Assign Spool'));
      fireEvent.click(linkBtn);

      const spoolBtn = await waitFor(() => screen.getByText('White'));
      fireEvent.click(spoolBtn);

      const confirmBtn = await waitFor(() => screen.getByText('Link Tag'));
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(api.linkTagToSpool).toHaveBeenCalledWith(3, {
          tag_uid: 'AABB9999',
          tag_type: 'generic',
          data_origin: 'nfc_link',
        });
        expect(api.linkTagToSpoolmanSpool).not.toHaveBeenCalled();
        // Local path never sets justLinkedSpool → no Spoolman success toast
        expect(mockShowToast).not.toHaveBeenCalled();
        // Modal closes via finally, UnknownTagCard still absent (tag still present but no SpoolInfoCard)
        expect(screen.queryByText('Link Tag')).toBeNull();
      });
    });
  });

  describe('Bambu tray UUID in local mode (#984)', () => {
    const TRAY_UUID = '9E0B0717BEE94D7887EB1D8DFD1A14F3';

    beforeEach(async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'false',
        spoolman_url: '',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 3, material: 'ABS', brand: 'Polymaker', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'White', rgba: 'FFFFFFFF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);
    });

    it('links the tag together with its tray UUID as a Bambu Lab tag', async () => {
      const { api } = await import('../../api/client');
      renderPage({ unknownTagUid: 'AABB9999', unknownTrayUuid: TRAY_UUID });

      fireEvent.click(await waitFor(() => screen.getByText('Assign Spool')));
      fireEvent.click(await waitFor(() => screen.getByText('White')));
      fireEvent.click(await waitFor(() => screen.getByText('Link Tag')));

      await waitFor(() => {
        expect(api.linkTagToSpool).toHaveBeenCalledWith(3, {
          tag_uid: 'AABB9999',
          tray_uuid: TRAY_UUID,
          tag_type: 'bambulab',
          data_origin: 'nfc_link',
        });
      });
    });

    it('quick-adds the spool with its tray UUID, so the other tag and the AMS find it', async () => {
      const { api } = await import('../../api/client');
      renderPage({ unknownTagUid: 'AABB9999', unknownTrayUuid: TRAY_UUID });

      fireEvent.click(await waitFor(() => screen.getAllByText('Add to Inventory')[0]));
      fireEvent.click(await waitFor(() => screen.getByText('Add Anyway')));

      await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
      const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(payload.tag_uid).toBe('AABB9999');
      expect(payload.tray_uuid).toBe(TRAY_UUID);
      expect(payload.tag_type).toBe('bambulab');
    });

    it('quick-adds a tag without a tray UUID as a generic tag, as before', async () => {
      const { api } = await import('../../api/client');
      renderPage({ unknownTagUid: 'AABB9999' });

      fireEvent.click(await waitFor(() => screen.getAllByText('Add to Inventory')[0]));
      fireEvent.click(await waitFor(() => screen.getByText('Add Anyway')));

      await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
      const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(payload.tray_uuid).toBeNull();
      expect(payload.tag_type).toBe('generic');
    });

    it('still saves the tray UUID when the spool is lifted off the reader while quick-add is open', async () => {
      const { api } = await import('../../api/client');
      const setterRef: { current: React.Dispatch<React.SetStateAction<typeof mockOutletContext.sbState>> | null } = { current: null };
      function DynWrapper() {
        const [sbState, setSbState] = React.useState({
          ...mockOutletContext.sbState,
          unknownTagUid: 'AABB9999',
          unknownTrayUuid: TRAY_UUID,
        });
        setterRef.current = setSbState;
        return <Outlet context={{ ...mockOutletContext, sbState }} />;
      }
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
      render(
        <ToastProvider>
          <QueryClientProvider client={qc}>
            <MemoryRouter initialEntries={['/spoolbuddy']}>
              <Routes>
                <Route element={<DynWrapper />}>
                  <Route path="spoolbuddy" element={<SpoolBuddyDashboard />} />
                </Route>
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        </ToastProvider>
      );
      fireEvent.click(await waitFor(() => screen.getAllByText('Add to Inventory')[0]));
      await waitFor(() => screen.getByText('Add Anyway'));

      // Tag removed: the dialog stays open, the live tag state is gone.
      act(() => setterRef.current!((prev) => ({ ...prev, unknownTagUid: null, unknownTrayUuid: null })));

      fireEvent.click(await waitFor(() => screen.getByText('Add Anyway')));

      await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
      const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(payload.tag_uid).toBe('AABB9999');
      expect(payload.tray_uuid).toBe(TRAY_UUID);
    });
  });

  describe('Spoolman mode, spool lifted while a dialog is open', () => {
    // The reader state is cleared when the tag leaves the reader; the card and
    // its dialogs stay. Linking must use the tag the card shows.
    function renderLiftable() {
      const setterRef: { current: React.Dispatch<React.SetStateAction<typeof mockOutletContext.sbState>> | null } = { current: null };
      function DynWrapper() {
        const [sbState, setSbState] = React.useState({ ...mockOutletContext.sbState, unknownTagUid: 'AABB1122334455FF' });
        setterRef.current = setSbState;
        return <Outlet context={{ ...mockOutletContext, sbState }} />;
      }
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
      render(
        <ToastProvider>
          <QueryClientProvider client={qc}>
            <MemoryRouter initialEntries={['/spoolbuddy']}>
              <Routes>
                <Route element={<DynWrapper />}>
                  <Route path="spoolbuddy" element={<SpoolBuddyDashboard />} />
                </Route>
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        </ToastProvider>
      );
      return () => act(() => setterRef.current!((prev) => ({ ...prev, unknownTagUid: null, unknownTrayUuid: null })));
    }

    beforeEach(async () => {
      const { api } = await import('../../api/client');
      (api.getSpoolmanSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      });
      (api.getSpoolmanInventorySpools as ReturnType<typeof vi.fn>).mockResolvedValue([
        { id: 30, material: 'TPU', brand: 'Polymaker', tag_uid: null, tray_uuid: null, archived_at: null, color_name: 'Orange', rgba: 'FF6600FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
      ]);
    });

    it('links the tag shown on the card', async () => {
      const { api } = await import('../../api/client');
      const lift = renderLiftable();

      fireEvent.click(await waitFor(() => screen.getByText('Assign Spool')));
      fireEvent.click(await waitFor(() => screen.getByText('Orange')));
      await waitFor(() => screen.getByText('Link Tag'));
      lift();
      fireEvent.click(screen.getByText('Link Tag'));

      await waitFor(() => {
        expect(api.linkTagToSpoolmanSpool).toHaveBeenCalledWith(30, {
          tag_uid: 'AABB1122334455FF',
          tray_uuid: undefined,
        });
      });
    });

    it('quick-adds the spool with the tag shown on the card', async () => {
      const { api } = await import('../../api/client');
      const lift = renderLiftable();

      fireEvent.click(await waitFor(() => screen.getAllByText('Add to Inventory')[0]));
      await waitFor(() => screen.getByText('Add Anyway'));
      lift();
      fireEvent.click(screen.getByText('Add Anyway'));

      await waitFor(() => {
        expect(api.linkTagToSpoolmanSpool).toHaveBeenCalledWith(4, {
          tag_uid: 'AABB1122334455FF',
          tray_uuid: undefined,
        });
      });
    });
  });

  describe('Spoolman mode', () => {
    const SPOOLMAN_SPOOL = {
      id: 42, material: 'PLA', subtype: null, brand: 'Bambu',
      color_name: 'Red', rgba: 'FF0000FF', extra_colors: null, effect_type: null,
      label_weight: 1000, core_weight: 250, core_weight_catalog_id: null,
      weight_used: 200, tag_uid: 'AABB11223344', tray_uuid: null,
      slicer_filament: null, slicer_filament_name: null, nozzle_temp_min: null,
      nozzle_temp_max: null, note: null, added_full: null, last_used: null,
      encode_time: null, data_origin: 'spoolman', tag_type: null, archived_at: null,
      created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z',
      cost_per_kg: null, last_scale_weight: null, last_weighed_at: null,
      category: null, low_stock_threshold_pct: null, k_profiles: [], storage_location: null,
    };

    it('disables "Assign to AMS" button when spool is already assigned', async () => {
      const { api } = await import('../../api/client');
      vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true', spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'off', spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'false',
      } as never);
      vi.mocked(api.getSpoolmanInventorySpools).mockResolvedValue([SPOOLMAN_SPOOL] as never);
      vi.mocked(api.getSpoolmanSlotAssignments).mockResolvedValue([
        { printer_id: 1, ams_id: 0, tray_id: 0, spoolman_spool_id: 42 },
      ] as never);

      renderPage({
        deviceOnline: true,
        matchedSpool: {
          id: 42, tag_uid: 'AABB11223344', material: 'PLA', subtype: null,
          color_name: 'Red', rgba: 'FF0000FF', brand: 'Bambu',
          label_weight: 1000, core_weight: 250, weight_used: 200,
        },
      });

      await waitFor(() => {
        const btn = screen.queryByText('Assign to AMS');
        expect(btn).toBeTruthy();
        expect(btn).toBeDisabled();
      });
    });
  });
});
