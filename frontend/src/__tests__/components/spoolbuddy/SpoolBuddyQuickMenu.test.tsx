/**
 * Tests for SpoolBuddyQuickMenu component:
 * - Renders nothing when closed
 * - Shows printer power section with smart plugs
 * - Shows system control buttons
 * - Confirmation dialogs for destructive actions
 * - Handles system commands
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../../utils';
import { SpoolBuddyQuickMenu } from '../../../components/spoolbuddy/SpoolBuddyQuickMenu';
import { api, spoolbuddyApi } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  api: {
    getPrinters: vi.fn().mockResolvedValue([]),
    getSmartPlugs: vi.fn().mockResolvedValue([]),
    getSmartPlugStatus: vi.fn().mockResolvedValue({ state: 'OFF', reachable: true, device_name: null, energy: null }),
    controlSmartPlug: vi.fn().mockResolvedValue({ success: true, action: 'toggle' }),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
  spoolbuddyApi: {
    systemCommand: vi.fn().mockResolvedValue({ status: 'queued', command: 'reboot' }),
  },
}));

const defaultProps = {
  isOpen: true,
  onClose: vi.fn(),
  deviceId: 'sb-0001',
  deviceOnline: true,
};

const mockPrinter = {
  id: 1,
  name: 'Test P1S',
  model: 'P1S',
  serial: 'SERIAL001',
  ip_address: '10.0.0.1',
};

const mockSmartPlug = {
  id: 10,
  name: 'P1S Plug',
  plug_type: 'tasmota' as const,
  ip_address: '10.0.0.100',
  printer_id: 1,
  enabled: true,
  ha_entity_id: null,
  ha_power_entity: null,
  ha_energy_today_entity: null,
  ha_energy_total_entity: null,
  mqtt_topic: null,
  mqtt_multiplier: 1,
  mqtt_power_topic: null,
  mqtt_power_multiplier: 1,
  mqtt_energy_topic: null,
  mqtt_energy_multiplier: 1,
  mqtt_state_topic: null,
  rest_on_url: null,
  rest_off_url: null,
  rest_status_url: null,
  rest_status_path: null,
  rest_on_value: null,
  rest_off_value: null,
  rest_method: null,
  rest_power_url: null,
  rest_power_path: null,
  rest_power_multiplier: 1,
  rest_energy_url: null,
  rest_energy_path: null,
  rest_energy_multiplier: 1,
  auto_on: false,
  auto_off: false,
  auto_off_persistent: false,
  off_delay_mode: 'time' as const,
  off_delay_minutes: 5,
  off_temp_threshold: 50,
  username: null,
  password: null,
  power_alert_enabled: false,
  power_alert_threshold: 0,
  power_alert_duration: 0,
  schedule_enabled: false,
  schedule_on_time: null,
  schedule_off_time: null,
  last_state: null,
  last_checked: null,
  auto_off_executed: false,
  auto_off_pending: false,
};

describe('SpoolBuddyQuickMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.getSmartPlugs as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  it('renders nothing when closed', () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} isOpen={false} />);
    expect(screen.queryByText('System')).not.toBeInTheDocument();
  });

  it('shows system control buttons when open', () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);
    expect(screen.getByText('Restart Daemon')).toBeInTheDocument();
    expect(screen.getByText('Restart Browser')).toBeInTheDocument();
    expect(screen.getByText('Reboot')).toBeInTheDocument();
    expect(screen.getByText('Shutdown')).toBeInTheDocument();
  });

  it('shows system section header', () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);
    expect(screen.getByText('System')).toBeInTheDocument();
  });

  it('shows swipe hint', () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);
    expect(screen.getByText('Swipe down to close')).toBeInTheDocument();
  });

  it('shows printer power section when printers have smart plugs', async () => {
    (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValue([mockPrinter]);
    (api.getSmartPlugs as ReturnType<typeof vi.fn>).mockResolvedValue([mockSmartPlug]);

    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Printer Power')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText('Test P1S')).toBeInTheDocument();
    });
  });

  it('does not show printer power section when no smart plugs', () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);
    expect(screen.queryByText('Printer Power')).not.toBeInTheDocument();
  });

  it('shows confirmation dialog for reboot', async () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    fireEvent.click(screen.getByText('Reboot'));

    await waitFor(() => {
      expect(screen.getByText('Are you sure you want to reboot the SpoolBuddy?')).toBeInTheDocument();
    });
  });

  it('shows confirmation dialog for shutdown with warning', async () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    fireEvent.click(screen.getByText('Shutdown'));

    await waitFor(() => {
      expect(screen.getByText(/physical access/)).toBeInTheDocument();
    });
  });

  it('shows confirmation dialog for restart daemon', async () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    fireEvent.click(screen.getByText('Restart Daemon'));

    await waitFor(() => {
      expect(screen.getByText(/NFC and scale will be temporarily unavailable/)).toBeInTheDocument();
    });
  });

  it('shows confirmation dialog for restart browser', async () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    fireEvent.click(screen.getByText('Restart Browser'));

    await waitFor(() => {
      expect(screen.getByText(/display will briefly go blank/)).toBeInTheDocument();
    });
  });

  it('cancels confirmation dialog', async () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    fireEvent.click(screen.getByText('Reboot'));
    await waitFor(() => {
      expect(screen.getByText('Are you sure you want to reboot the SpoolBuddy?')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => {
      expect(screen.queryByText('Are you sure you want to reboot the SpoolBuddy?')).not.toBeInTheDocument();
    });
  });

  it('sends system command on confirm', async () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    fireEvent.click(screen.getByText('Reboot'));
    await waitFor(() => {
      expect(screen.getByText('Are you sure you want to reboot the SpoolBuddy?')).toBeInTheDocument();
    });

    // Click the Confirm button (not the title)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => {
      expect(spoolbuddyApi.systemCommand).toHaveBeenCalledWith('sb-0001', 'reboot');
    });
  });

  it('shows confirmation when toggling printer plug', async () => {
    (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValue([mockPrinter]);
    (api.getSmartPlugs as ReturnType<typeof vi.fn>).mockResolvedValue([mockSmartPlug]);

    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Test P1S')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Test P1S'));

    await waitFor(() => {
      expect(screen.getByText(/Turn on Test P1S/)).toBeInTheDocument();
    });
  });

  it('toggles plug after confirming', async () => {
    (api.getPrinters as ReturnType<typeof vi.fn>).mockResolvedValue([mockPrinter]);
    (api.getSmartPlugs as ReturnType<typeof vi.fn>).mockResolvedValue([mockSmartPlug]);

    render(<SpoolBuddyQuickMenu {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText('Test P1S')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Test P1S'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Turn On' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Turn On' }));

    await waitFor(() => {
      expect(api.controlSmartPlug).toHaveBeenCalledWith(10, 'toggle');
    });
  });

  it('disables system buttons when device offline', () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} deviceOnline={false} />);

    const rebootBtn = screen.getByText('Reboot').closest('button');
    expect(rebootBtn).toBeDisabled();
  });

  it('disables system buttons when no device ID', () => {
    render(<SpoolBuddyQuickMenu {...defaultProps} deviceId={null} />);

    const shutdownBtn = screen.getByText('Shutdown').closest('button');
    expect(shutdownBtn).toBeDisabled();
  });
});
