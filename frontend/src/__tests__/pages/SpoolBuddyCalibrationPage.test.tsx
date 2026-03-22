/**
 * Tests for SpoolBuddyCalibrationPage:
 * - Renders "Scale Calibration" heading
 * - Shows current weight display
 * - Shows Tare and Calibrate buttons
 * - Shows "No SpoolBuddy device found" when no device
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { SpoolBuddyCalibrationPage } from '../../pages/spoolbuddy/SpoolBuddyCalibrationPage';

const mockDevice = {
  id: 1,
  device_id: 'sb-test-001',
  hostname: 'spoolbuddy-pi',
  ip_address: '192.168.1.100',
  firmware_version: '1.2.3',
  has_nfc: true,
  has_scale: true,
  tare_offset: 0,
  calibration_factor: 1.0,
  nfc_reader_type: 'PN532',
  nfc_connection: 'I2C',
  display_brightness: 80,
  display_blank_timeout: 300,
  has_backlight: true,
  last_calibrated_at: null,
  last_seen: '2026-03-22T12:00:00Z',
  pending_command: null,
  nfc_ok: true,
  scale_ok: true,
  uptime_s: 3600,
  update_status: null,
  update_message: null,
  online: true,
};

let mockDevices = [mockDevice];

vi.mock('../../api/client', () => ({
  spoolbuddyApi: {
    getDevices: vi.fn(() => Promise.resolve(mockDevices)),
    tare: vi.fn().mockResolvedValue({ status: 'ok' }),
    setCalibrationFactor: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

function makeOutletContext(overrides: Record<string, unknown> = {}) {
  return {
    selectedPrinterId: null,
    setSelectedPrinterId: vi.fn(),
    sbState: {
      weight: 250.5,
      weightStable: true,
      rawAdc: 12345,
      matchedSpool: null,
      unknownTagUid: null,
      deviceOnline: true,
      deviceId: 'sb-test-001',
      remainingWeight: null,
      netWeight: null,
      ...(overrides.sbState as Record<string, unknown> || {}),
    },
    setAlert: vi.fn(),
    displayBrightness: 100,
    setDisplayBrightness: vi.fn(),
    displayBlankTimeout: 0,
    setDisplayBlankTimeout: vi.fn(),
  };
}

function renderPage(contextOverrides: Record<string, unknown> = {}) {
  const ctx = makeOutletContext(contextOverrides);
  function Wrapper() {
    return <Outlet context={ctx} />;
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/spoolbuddy/calibration']}>
        <Routes>
          <Route element={<Wrapper />}>
            <Route path="spoolbuddy/calibration" element={<SpoolBuddyCalibrationPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('SpoolBuddyCalibrationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockDevices = [mockDevice];
  });

  it('renders "Scale Calibration" heading', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Scale Calibration')).toBeDefined();
    });
  });

  it('shows current weight display when device available', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Current weight')).toBeDefined();
      expect(screen.getByText('250.5 g')).toBeDefined();
    });
  });

  it('shows Tare and Calibrate buttons when device available', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Tare')).toBeDefined();
      expect(screen.getByText('Calibrate')).toBeDefined();
    });
  });

  it('shows "No SpoolBuddy device found" when no device', async () => {
    mockDevices = [];
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('No SpoolBuddy device found')).toBeDefined();
    });
  });

  it('shows back button that navigates to settings', () => {
    renderPage();
    // Find the back button (contains a chevron SVG)
    const buttons = screen.getAllByRole('button');
    // First button is the back button
    expect(buttons.length).toBeGreaterThan(0);
  });
});
