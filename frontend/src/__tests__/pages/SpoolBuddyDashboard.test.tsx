/**
 * Tests for SpoolBuddyDashboard:
 * - Shows stats bar (Spools, Materials, Brands)
 * - Shows "Ready to scan" idle state when no tag detected
 * - Shows device status section
 * - Shows "Device Offline" state when device offline
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { SpoolBuddyDashboard } from '../../pages/spoolbuddy/SpoolBuddyDashboard';

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
  },
  spoolbuddyApi: {
    getDevices: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
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
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/spoolbuddy']}>
        <Routes>
          <Route element={<Wrapper />}>
            <Route path="spoolbuddy" element={<SpoolBuddyDashboard />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
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
});
