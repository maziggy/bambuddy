/**
 * Tests for SpoolBuddySettingsPage:
 * - Renders 5 tabs (Device, Display, Scale, Updates, System)
 * - Device tab shows hostname, IP, NFC status
 * - Updates tab shows "Check for Updates" button
 * - System tab shows OS stats
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
      system_stats: {
        os: { os: 'Raspbian GNU/Linux 12', kernel: '6.1.0-rpi7', arch: 'aarch64', python: '3.11.2' },
        cpu_temp_c: 52.1,
        cpu_count: 4,
        load_avg: [0.15, 0.22, 0.18],
        memory: { total_mb: 1024, available_mb: 512, used_mb: 512, percent: 50.0 },
        disk: { total_gb: 29.7, used_gb: 8.2, free_gb: 21.5, percent: 27.6 },
        system_uptime_s: 86400,
      },
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
    getSSHPublicKey: vi.fn().mockResolvedValue({ public_key: 'ssh-ed25519 AAAA test-key' }),
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

  it('renders 5 tabs', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Device')).toBeDefined();
      expect(screen.getByText('Display')).toBeDefined();
      expect(screen.getByText('Scale')).toBeDefined();
      expect(screen.getByText('Updates')).toBeDefined();
      expect(screen.getByText('System')).toBeDefined();
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

  it('switching to Updates tab shows Check for Updates and Force Update buttons', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Updates')).toBeDefined();
    });
    fireEvent.click(screen.getByText('Updates'));
    await waitFor(() => {
      expect(screen.getByText('Check for Updates')).toBeDefined();
      expect(screen.getByText('Force Update')).toBeDefined();
    });
  });

  it('Updates tab shows current version', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Updates')).toBeDefined();
    });
    fireEvent.click(screen.getByText('Updates'));
    await waitFor(() => {
      expect(screen.getByText('1.2.3')).toBeDefined();
    });
  });

  it('Updates tab shows SSH Setup section', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Updates')).toBeDefined();
    });
    fireEvent.click(screen.getByText('Updates'));
    await waitFor(() => {
      expect(screen.getByText('SSH Setup')).toBeDefined();
    });
  });

  it('Updates tab shows Apply Update when update is available', async () => {
    const { spoolbuddyApi } = await import('../../api/client');
    vi.mocked(spoolbuddyApi.checkDaemonUpdate).mockResolvedValue({
      current_version: '1.2.3',
      latest_version: '1.3.0',
      update_available: true,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Updates')).toBeDefined();
    });
    fireEvent.click(screen.getByText('Updates'));
    await waitFor(() => {
      expect(screen.getByText('Apply Update')).toBeDefined();
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

  it('System tab shows CPU info', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('System')).toBeDefined();
    });
    fireEvent.click(screen.getByText('System'));
    await waitFor(() => {
      expect(screen.getByText('CPU')).toBeDefined();
      expect(screen.getByText('4')).toBeDefined(); // cpu_count
      expect(screen.getByText('0.15 / 0.22 / 0.18')).toBeDefined(); // load_avg
    });
  });

  it('System tab shows memory stats', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('System')).toBeDefined();
    });
    fireEvent.click(screen.getByText('System'));
    await waitFor(() => {
      expect(screen.getByText('Memory')).toBeDefined();
      expect(screen.getByText('512 / 1024 MB')).toBeDefined();
    });
  });

  it('System tab shows disk stats', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('System')).toBeDefined();
    });
    fireEvent.click(screen.getByText('System'));
    await waitFor(() => {
      expect(screen.getByText('Disk')).toBeDefined();
      expect(screen.getByText('8.2 / 29.7 GB')).toBeDefined();
    });
  });

  it('System tab shows OS info', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('System')).toBeDefined();
    });
    fireEvent.click(screen.getByText('System'));
    await waitFor(() => {
      expect(screen.getByText('Raspbian GNU/Linux 12')).toBeDefined();
      expect(screen.getByText('aarch64')).toBeDefined();
      expect(screen.getByText('3.11.2')).toBeDefined();
    });
  });

  it('System tab shows waiting message when no stats', async () => {
    const { spoolbuddyApi } = await import('../../api/client');
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([{
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
      system_stats: null,
      online: true,
    }]);
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('System')).toBeDefined();
    });
    fireEvent.click(screen.getByText('System'));
    await waitFor(() => {
      expect(screen.getByText('Waiting for system stats...')).toBeDefined();
    });
  });
});
