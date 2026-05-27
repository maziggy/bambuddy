/**
 * Tests for SpoolBuddyWriteTagPage:
 * - Renders three workflow tabs
 * - Tab switching works
 * - Search input renders on existing/replace tabs
 * - New spool form renders on new tab
 * - NFC status panel shows correct idle state
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { ToastProvider } from '../../contexts/ToastContext';
import { SpoolBuddyWriteTagPage } from '../../pages/spoolbuddy/SpoolBuddyWriteTagPage';
import { api as mockedApi, spoolbuddyApi as mockedSpoolbuddyApi } from '../../api/client';

// Mock the API modules. spoolman-* mocks default to "spoolman disabled" so
// existing tests run in internal-inventory mode — the new spoolman parity
// test below overrides getSpoolmanSettings before rendering.
vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 1, material: 'PLA' }),
    getSpoolmanSettings: vi.fn().mockResolvedValue({
      spoolman_enabled: 'false',
      spoolman_url: '',
    }),
    getSpoolmanInventorySpools: vi.fn().mockResolvedValue([]),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 1, material: 'PLA' }),
  },
  spoolbuddyApi: {
    getDevices: vi.fn().mockResolvedValue([]),
    writeTag: vi.fn().mockResolvedValue({ status: 'queued' }),
    cancelWrite: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

// Mock i18n
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback: string) => fallback,
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
    deviceOnline: false,
    deviceId: null,
    remainingWeight: null,
    netWeight: null,
  },
  setAlert: vi.fn(),
  displayBrightness: 100,
  setDisplayBrightness: vi.fn(),
  displayBlankTimeout: 0,
  setDisplayBlankTimeout: vi.fn(),
};

function OutletWrapper() {
  return <Outlet context={mockOutletContext} />;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/spoolbuddy/write-tag']}>
          <Routes>
            <Route element={<OutletWrapper />}>
              <Route path="spoolbuddy/write-tag" element={<SpoolBuddyWriteTagPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

describe('SpoolBuddyWriteTagPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Re-pin the default Spoolman-mode settings since previous tests may
    // have overridden the resolved value (clearAllMocks doesn't reset
    // implementations set via mockResolvedValue). Default = Spoolman OFF.
    vi.mocked(mockedApi.getSpoolmanSettings).mockResolvedValue({
      spoolman_enabled: 'false',
      spoolman_url: '',
      spoolman_sync_mode: '',
      spoolman_disable_weight_sync: '',
      spoolman_report_partial_usage: '',
    });
  });

  it('renders three workflow tabs', () => {
    renderPage();
    expect(screen.getByText('Existing Spool')).toBeDefined();
    expect(screen.getByText('New Spool')).toBeDefined();
    expect(screen.getByText('Replace Tag')).toBeDefined();
  });

  it('shows search input on existing spool tab', () => {
    renderPage();
    expect(screen.getByPlaceholderText('Search by material, color, brand...')).toBeDefined();
  });

  it('shows no spools message when list is empty', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('No spools without tags')).toBeDefined();
    });
  });

  it('switches to new spool form on tab click', async () => {
    renderPage();
    fireEvent.click(screen.getByText('New Spool'));
    await waitFor(() => {
      expect(screen.getByText('Material')).toBeDefined();
      expect(screen.getByText('Color Name')).toBeDefined();
      expect(screen.getByText('Brand')).toBeDefined();
      expect(screen.getByText('Weight (g)')).toBeDefined();
      expect(screen.getByText('Create Spool')).toBeDefined();
    });
  });

  it('switches to replace tab and shows appropriate empty message', async () => {
    renderPage();
    fireEvent.click(screen.getByText('Replace Tag'));
    await waitFor(() => {
      expect(screen.getByText('No spools with tags')).toBeDefined();
    });
  });

  it('shows device offline message in NFC panel', () => {
    renderPage();
    expect(screen.getByText('SpoolBuddy is offline')).toBeDefined();
  });

  it('shows idle prompt when device is online but no spool selected', () => {
    mockOutletContext.sbState.deviceOnline = true;
    renderPage();
    expect(screen.getByText('Select a spool, then place a blank NTAG on the reader')).toBeDefined();
    mockOutletContext.sbState.deviceOnline = false; // reset
  });

  it('shows one toast per warning when writeTag returns multiple warnings (B5 / T2)', async () => {
    const spool = {
      id: 99,
      material: 'PLA',
      label_weight: 1000,
      weight_used: 0,
      tag_uid: null,
      tray_uuid: null,
      archived_at: null,
      rgba: 'FF0000FF',
    };
    vi.mocked(mockedApi.getSpools).mockResolvedValue([spool as never]);
    vi.mocked(mockedSpoolbuddyApi.getDevices).mockResolvedValue([
      { device_id: 'sb-test', hostname: 'sb-test.local' } as never,
    ]);
    vi.mocked(mockedSpoolbuddyApi.writeTag).mockResolvedValueOnce({
      status: 'queued',
      warnings: ['color_name not set', 'nozzle_temp_min not set'],
    } as never);

    mockOutletContext.sbState.deviceOnline = true;
    renderPage();

    // Wait for the spool to appear and click it to select it
    await waitFor(() => {
      expect(screen.getByText('PLA')).toBeDefined();
    });
    fireEvent.click(screen.getByText('PLA'));

    // Click the Write Tag button
    await waitFor(() => {
      const writeBtn = screen.getByText('Write Tag');
      expect((writeBtn as HTMLButtonElement).disabled).toBe(false);
    });
    fireEvent.click(screen.getByText('Write Tag'));

    // Both warning messages must appear as separate toasts
    await waitFor(() => {
      expect(screen.getByText('color_name not set')).toBeDefined();
      expect(screen.getByText('nozzle_temp_min not set')).toBeDefined();
    });

    mockOutletContext.sbState.deviceOnline = false; // reset
  });

  it('reads from internal inventory when Spoolman mode is OFF (#1439 parity baseline)', async () => {
    vi.mocked(mockedApi.getSpoolmanSettings).mockResolvedValue({
      spoolman_enabled: 'false',
      spoolman_url: '',
      spoolman_sync_mode: '',
      spoolman_disable_weight_sync: '',
      spoolman_report_partial_usage: '',
    });
    renderPage();

    // Internal API is hit at least once; the Spoolman list endpoint must NOT
    // be called when the user hasn't enabled Spoolman.
    await waitFor(() => {
      expect(vi.mocked(mockedApi.getSpools)).toHaveBeenCalled();
    });
    expect(vi.mocked(mockedApi.getSpoolmanInventorySpools)).not.toHaveBeenCalled();
  });

  it('reads from Spoolman when Spoolman mode is ON (#1439 — the actual bug)', async () => {
    // The bug report: write-tag page hard-coded api.getSpools(false) regardless
    // of inventory backend, so Spoolman-mode users saw internal spools they
    // never created and the write path would have bound the tag to the wrong
    // backend. After the fix, the page must hit the spoolman variant.
    vi.mocked(mockedApi.getSpoolmanSettings).mockResolvedValue({
      spoolman_enabled: 'true',
      spoolman_url: 'http://spoolman.test',
      spoolman_sync_mode: '',
      spoolman_disable_weight_sync: '',
      spoolman_report_partial_usage: '',
    });
    renderPage();

    await waitFor(() => {
      expect(vi.mocked(mockedApi.getSpoolmanInventorySpools)).toHaveBeenCalled();
    });
    // Critical: the internal endpoint must NOT be called in Spoolman mode.
    // A future refactor that re-hardcodes api.getSpools breaks here.
    expect(vi.mocked(mockedApi.getSpools)).not.toHaveBeenCalled();
  });

  it('shows the spool ID next to each spool so multiple identical rolls can be disambiguated (#1439)', async () => {
    const spools = [
      {
        id: 42,
        material: 'PLA',
        label_weight: 1000,
        weight_used: 0,
        tag_uid: null,
        tray_uuid: null,
        archived_at: null,
        rgba: 'FF0000FF',
      },
      {
        id: 43,
        material: 'PLA',
        label_weight: 1000,
        weight_used: 0,
        tag_uid: null,
        tray_uuid: null,
        archived_at: null,
        rgba: 'FF0000FF',
      },
    ];
    vi.mocked(mockedApi.getSpools).mockResolvedValue(spools as never);
    mockOutletContext.sbState.deviceOnline = true;
    renderPage();

    // Both rolls are identical PLA Red but the IDs must let the user tell
    // them apart at the picker. Without #1439 the two rows were visually
    // indistinguishable and an NFC write could bind the tag to the wrong
    // physical spool.
    await waitFor(() => {
      expect(screen.getByText('#42')).toBeDefined();
      expect(screen.getByText('#43')).toBeDefined();
    });

    mockOutletContext.sbState.deviceOnline = false;
  });
});
