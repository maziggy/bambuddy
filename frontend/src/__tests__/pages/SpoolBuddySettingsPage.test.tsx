/**
 * Tests for SpoolBuddySettingsPage:
 * - Renders 4 tabs (Device, Display, Scale, Updates)
 * - Device tab shows hostname, IP, NFC status
 * - Updates tab shows "Check for Updates" button
 * - Tab switching works
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { SpoolBuddySettingsPage } from '../../pages/spoolbuddy/SpoolBuddySettingsPage';

vi.mock('../../api/client', () => ({
  spoolbuddyApi: {
    getDevices: vi.fn().mockResolvedValue([{
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
    }]),
    updateDisplay: vi.fn().mockResolvedValue({ status: 'ok' }),
    tare: vi.fn().mockResolvedValue({ status: 'ok' }),
    setCalibrationFactor: vi.fn().mockResolvedValue({ status: 'ok' }),
    checkDaemonUpdate: vi.fn().mockResolvedValue({
      current_version: '1.2.3',
      latest_version: '1.2.3',
      update_available: false,
    }),
    triggerUpdate: vi.fn().mockResolvedValue({ status: 'ok', message: '' }),
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
    weight: 250.0,
    weightStable: true,
    rawAdc: 12345,
    matchedSpool: null,
    unknownTagUid: null,
    deviceOnline: true,
    deviceId: 'sb-test-001',
    remainingWeight: null,
    netWeight: null,
  },
  setAlert: vi.fn(),
  displayBrightness: 80,
  setDisplayBrightness: vi.fn(),
  displayBlankTimeout: 300,
  setDisplayBlankTimeout: vi.fn(),
};

function OutletWrapper() {
  return <Outlet context={mockOutletContext} />;
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/spoolbuddy/settings']}>
        <Routes>
          <Route element={<OutletWrapper />}>
            <Route path="spoolbuddy/settings" element={<SpoolBuddySettingsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('SpoolBuddySettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders 4 tabs', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Device')).toBeDefined();
      expect(screen.getByText('Display')).toBeDefined();
      expect(screen.getByText('Scale')).toBeDefined();
      expect(screen.getByText('Updates')).toBeDefined();
    });
  });

  it('device tab shows hostname and IP', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('spoolbuddy-pi')).toBeDefined();
      expect(screen.getByText('192.168.1.100')).toBeDefined();
    });
  });

  it('device tab shows NFC reader type', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('PN532')).toBeDefined();
    });
  });

  it('device tab shows NFC status as Ready', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Ready')).toBeDefined();
    });
  });

  it('switching to Updates tab shows Check for Updates button', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Updates')).toBeDefined();
    });
    fireEvent.click(screen.getByText('Updates'));
    await waitFor(() => {
      expect(screen.getByText('Check for Updates')).toBeDefined();
    });
  });

  it('switching to Display tab shows Brightness', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Display')).toBeDefined();
    });
    fireEvent.click(screen.getByText('Display'));
    await waitFor(() => {
      expect(screen.getByText('Brightness')).toBeDefined();
    });
  });

  it('switching to Scale tab shows Tare and Calibrate buttons', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Scale')).toBeDefined();
    });
    fireEvent.click(screen.getByText('Scale'));
    await waitFor(() => {
      expect(screen.getByText('Tare')).toBeDefined();
      expect(screen.getByText('Calibrate')).toBeDefined();
    });
  });
});
