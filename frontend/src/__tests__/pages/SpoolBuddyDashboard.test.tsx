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
    linkTagToSpool: vi.fn().mockResolvedValue({}),
    linkTagToSpoolmanSpool: vi.fn().mockResolvedValue({}),
    createSpool: vi.fn().mockResolvedValue({ id: 4 }),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 4 }),
    clearPlate: vi.fn().mockResolvedValue({}),
  },
  spoolbuddyApi: {
    getDevices: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Mirrors i18next's (key, defaultValue, options) signature with simple
    // {{var}} interpolation so tests can assert on the rendered text.
    t: (key: string, fallback?: string, options?: Record<string, unknown>) => {
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
      const linkBtn = await waitFor(() => screen.getByText('Link to Spool'));
      fireEvent.click(linkBtn);

      await waitFor(() => {
        // Only the ABS spool (id=21) should appear — the PETG with tray_uuid is excluded
        expect(screen.getByText('Black')).toBeDefined();
        expect(screen.queryByText('Blue')).toBeNull();
      });
    });

    it('calls linkTagToSpoolmanSpool with tray_uuid when linking in Spoolman mode', async () => {
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
        unknownTrayUuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
      });

      const linkBtn = await waitFor(() => screen.getByText('Link to Spool'));
      fireEvent.click(linkBtn);

      const spoolBtn = await waitFor(() => screen.getByText('Orange'));
      fireEvent.click(spoolBtn);

      const confirmBtn = await waitFor(() => screen.getByText('Link Tag'));
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(api.linkTagToSpoolmanSpool).toHaveBeenCalledWith(30, {
          tray_uuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
          tag_uid: undefined,
        });
      });
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

      const linkBtn = await waitFor(() => screen.getByText('Link to Spool'));
      fireEvent.click(linkBtn);

      const spoolBtn = await waitFor(() => screen.getByText('Orange'));
      fireEvent.click(spoolBtn);

      const confirmBtn = await waitFor(() => screen.getByText('Link Tag'));
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(screen.queryByText('Link to Spool')).toBeNull();
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

      const linkBtn = await waitFor(() => screen.getByText('Link to Spool'));
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
        expect(screen.getByText('Link to Spool')).toBeDefined();
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

      // Stateful wrapper so sbState can be updated mid-test
      let setExtSbState: React.Dispatch<React.SetStateAction<typeof mockOutletContext.sbState>>;
      function DynWrapper() {
        const [sbState, setSbState] = React.useState({
          ...mockOutletContext.sbState,
          unknownTagUid: 'AABB1122334455FF',
          unknownTrayUuid: 'DEADBEEFDEADBEEFDEADBEEFDEADBEEF',
        });
        setExtSbState = setSbState;
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
      fireEvent.click(await waitFor(() => screen.getByText('Link to Spool')));
      fireEvent.click(await waitFor(() => screen.getByText('Orange')));
      fireEvent.click(await waitFor(() => screen.getByText('Link Tag')));
      await waitFor(() => expect(screen.getByText('Sync Weight')).toBeDefined());

      // Different tag placed → justLinkedSpool cleared
      act(() => setExtSbState!((prev) => ({ ...prev, unknownTagUid: 'CCDD5566', unknownTrayUuid: null })));

      await waitFor(() => {
        expect(screen.queryByText('Sync Weight')).toBeNull();
        expect(screen.getByText('Link to Spool')).toBeDefined();
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

      const linkBtn = await waitFor(() => screen.getByText('Link to Spool'));
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
});
