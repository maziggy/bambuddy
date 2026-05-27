/**
 * Tests for the SpoolBuddySettings component.
 *
 * Covers:
 * - Lists all devices (not just the first), including stale duplicates
 * - Shows a duplicate warning when more than one device is registered
 * - Unregister button opens a confirm modal and calls the delete API
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SpoolBuddySettings } from '../../components/SpoolBuddySettings';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    spoolbuddyApi: {
      ...actual.spoolbuddyApi,
      getDevices: vi.fn(),
      deleteDevice: vi.fn(),
    },
  };
});

import { spoolbuddyApi } from '../../api/client';

const baseDevice = {
  id: 1,
  device_id: 'sb-0001',
  hostname: 'spoolbuddy-kitchen',
  ip_address: '10.0.0.11',
  backend_url: null,
  firmware_version: '1.2.0',
  has_nfc: true,
  has_scale: true,
  tare_offset: 0,
  calibration_factor: 1.0,
  nfc_reader_type: 'pn532',
  nfc_connection: 'i2c',
  display_brightness: 100,
  display_blank_timeout: 0,
  has_backlight: true,
  last_calibrated_at: null,
  last_seen: new Date().toISOString(),
  pending_command: null,
  nfc_ok: true,
  scale_ok: true,
  uptime_s: 3600,
  update_status: null,
  update_message: null,
  system_stats: {
    os: { os: 'Raspbian', kernel: '6.1', arch: 'aarch64', python: '3.11' },
    cpu_temp_c: 45.2,
    memory: { total_mb: 4000, available_mb: 2500, used_mb: 1500, percent: 37 },
    disk: { total_gb: 32, used_gb: 8, free_gb: 24, percent: 25 },
    system_uptime_s: 86400,
  },
  online: true,
};

describe('SpoolBuddySettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(spoolbuddyApi.deleteDevice).mockResolvedValue({ status: 'deleted', device_id: 'sb-0002' });
  });

  it('renders every registered device, not just the first', async () => {
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([
      { ...baseDevice, id: 1, device_id: 'sb-0001', hostname: 'spoolbuddy-kitchen' },
      { ...baseDevice, id: 2, device_id: 'sb-0002', hostname: 'spoolbuddy-ghost', online: false },
    ]);

    render(<SpoolBuddySettings />);

    expect(await screen.findByText('spoolbuddy-kitchen')).toBeInTheDocument();
    expect(await screen.findByText('spoolbuddy-ghost')).toBeInTheDocument();
    expect(screen.getByText('sb-0001')).toBeInTheDocument();
    expect(screen.getByText('sb-0002')).toBeInTheDocument();
  });

  it('shows duplicate warning when multiple devices registered', async () => {
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([
      { ...baseDevice, id: 1, device_id: 'sb-0001', hostname: 'spoolbuddy-kitchen' },
      { ...baseDevice, id: 2, device_id: 'sb-0002', hostname: 'spoolbuddy-ghost' },
    ]);

    render(<SpoolBuddySettings />);

    // Warning text mentions device count
    expect(await screen.findByText(/2 devices registered/i)).toBeInTheDocument();
  });

  it('does not show duplicate warning with a single device', async () => {
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([
      { ...baseDevice, id: 1, device_id: 'sb-0001', hostname: 'spoolbuddy-kitchen' },
    ]);

    render(<SpoolBuddySettings />);

    await screen.findByText('spoolbuddy-kitchen');
    expect(screen.queryByText(/devices registered/i)).not.toBeInTheDocument();
  });

  it('opens confirm modal and unregisters device on confirm', async () => {
    const user = userEvent.setup();
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([
      { ...baseDevice, id: 1, device_id: 'sb-0001', hostname: 'spoolbuddy-kitchen' },
      { ...baseDevice, id: 2, device_id: 'sb-0002', hostname: 'spoolbuddy-ghost', online: false },
    ]);

    render(<SpoolBuddySettings />);

    // Wait for both devices to render
    await screen.findByText('spoolbuddy-ghost');

    // Click the unregister button on the ghost device card
    const unregisterButtons = screen.getAllByRole('button', { name: /unregister/i });
    // Two unregister buttons (one per card) — click the second one (ghost)
    await user.click(unregisterButtons[1]);

    // Confirm modal opens with title
    expect(await screen.findByText(/unregister spoolbuddy device/i)).toBeInTheDocument();

    // Click the confirm button inside the modal
    const confirmButtons = screen.getAllByRole('button', { name: /^unregister$/i });
    // Last one will be the modal's confirm button
    await user.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(spoolbuddyApi.deleteDevice).toHaveBeenCalledWith('sb-0002');
    });
  });

  it('does not call delete API when user cancels confirm modal', async () => {
    const user = userEvent.setup();
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([
      { ...baseDevice, id: 1, device_id: 'sb-0001', hostname: 'spoolbuddy-kitchen' },
    ]);

    render(<SpoolBuddySettings />);

    await screen.findByText('spoolbuddy-kitchen');
    const unregisterButton = screen.getByRole('button', { name: /unregister/i });
    await user.click(unregisterButton);

    const cancelButton = await screen.findByRole('button', { name: /cancel/i });
    await user.click(cancelButton);

    expect(spoolbuddyApi.deleteDevice).not.toHaveBeenCalled();
  });

  it('shows empty state when no devices are registered', async () => {
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([]);

    render(<SpoolBuddySettings />);

    expect(await screen.findByText(/no spoolbuddy devices/i)).toBeInTheDocument();
  });
});
