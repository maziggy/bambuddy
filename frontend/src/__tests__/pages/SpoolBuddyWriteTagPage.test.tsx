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

// Mock the API modules
vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 1, material: 'PLA' }),
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
});
