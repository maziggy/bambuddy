/**
 * Tests for SpoolBuddyDashboard:
 * - Shows stats bar (Spools, Materials, Brands)
 * - Shows "Ready to scan" idle state when no tag detected
 * - Shows device status section
 * - Shows "Device Offline" state when device offline
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { SpoolBuddyDashboard } from '../../pages/spoolbuddy/SpoolBuddyDashboard';
import { ToastProvider } from '../../contexts/ToastContext';

vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn().mockResolvedValue([
      { id: 1, material: 'PLA', brand: 'Bambu', tag_uid: 'AA:BB', archived_at: null, color_name: 'Red', rgba: 'FF0000FF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 100 },
      { id: 2, material: 'PETG', brand: 'Bambu', tag_uid: 'CC:DD', archived_at: null, color_name: 'Blue', rgba: '0000FFFF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 200 },
      { id: 3, material: 'ABS', brand: 'Polymaker', tag_uid: null, archived_at: null, color_name: 'White', rgba: 'FFFFFFFF', subtype: null, label_weight: 1000, core_weight: 250, weight_used: 0 },
    ]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue({ connected: false }),
    linkTagToSpool: vi.fn().mockResolvedValue({}),
    createSpool: vi.fn().mockResolvedValue({ id: 4 }),
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
    t: (_key: string, fallback: string, options?: Record<string, unknown>) => {
      if (!options) return fallback;
      return fallback.replace(/\{\{(\w+)\}\}/g, (_m, k) => String(options[k] ?? ''));
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
});
