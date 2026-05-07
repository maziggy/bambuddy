/**
 * Tests for the SpoolmanSettings component.
 *
 * Tests the filament tracking mode selector and Spoolman integration UI:
 * - Mode selector (Built-in Inventory vs Spoolman)
 * - Built-in Inventory info panel
 * - Spoolman URL, sync mode, connection status
 * - Weight sync and partial usage toggles
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SpoolmanSettings } from '../../components/SpoolmanSettings';

// Mock the API client
vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
    getSpoolmanSettings: vi.fn(),
    updateSpoolmanSettings: vi.fn(),
    getSpoolmanStatus: vi.fn(),
    connectSpoolman: vi.fn(),
    disconnectSpoolman: vi.fn(),
    syncAllPrintersAms: vi.fn(),
    syncPrinterAms: vi.fn(),
    getPrinters: vi.fn(),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

// Import mocked module
import { api } from '../../api/client';

describe('SpoolmanSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    // Default API mocks — Spoolman disabled (Built-in Inventory mode)
    vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
      spoolman_enabled: 'false',
      spoolman_url: '',
      spoolman_sync_mode: 'auto',
      spoolman_disable_weight_sync: 'false',
      spoolman_report_partial_usage: 'true',
    });
    vi.mocked(api.updateSpoolmanSettings).mockResolvedValue({
      spoolman_enabled: 'false',
      spoolman_url: '',
      spoolman_sync_mode: 'auto',
      spoolman_disable_weight_sync: 'false',
      spoolman_report_partial_usage: 'true',
    });
    vi.mocked(api.getSpoolmanStatus).mockResolvedValue({
      enabled: false,
      connected: false,
      url: null,
    });
    vi.mocked(api.getPrinters).mockResolvedValue([]);
    vi.mocked(api.connectSpoolman).mockResolvedValue({ success: true, message: 'Connected' });
    vi.mocked(api.disconnectSpoolman).mockResolvedValue({ success: true, message: 'Disconnected' });
    vi.mocked(api.syncAllPrintersAms).mockResolvedValue({
      success: true,
      synced_count: 3,
      skipped_count: 1,
      skipped: [],
      errors: [],
    });
  });

  describe('rendering', () => {
    it('renders loading state initially', () => {
      vi.mocked(api.getSpoolmanSettings).mockImplementation(() => new Promise(() => {}));
      render(<SpoolmanSettings />);

      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });

    it('renders filament tracking title', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Filament Tracking')).toBeInTheDocument();
      });
    });

    it('renders mode selector cards', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Built-in Inventory')).toBeInTheDocument();
        expect(screen.getByText('Spoolman')).toBeInTheDocument();
      });
    });
  });

  describe('built-in inventory mode (default)', () => {
    it('shows built-in inventory as selected by default', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        // Built-in Inventory card should have the active border
        const builtInBtn = screen.getByText('Built-in Inventory').closest('button');
        expect(builtInBtn).toHaveClass('border-bambu-green');
      });
    });

    it('shows built-in info panel when selected', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText(/Automatically detects Bambu Lab RFID spools/)).toBeInTheDocument();
      });
    });

    it('does not show Spoolman URL input', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Filament Tracking')).toBeInTheDocument();
      });

      expect(screen.queryByPlaceholderText('http://192.168.1.100:7912')).not.toBeInTheDocument();
    });
  });

  describe('spoolman mode', () => {
    beforeEach(() => {
      vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'auto',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'true',
      });
      vi.mocked(api.updateSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'auto',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'true',
      });
    });

    it('shows Spoolman card as selected', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        const spoolmanBtn = screen.getByText('Spoolman').closest('button');
        expect(spoolmanBtn).toHaveClass('border-bambu-green');
      });
    });

    it('shows URL input when Spoolman is selected', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText('http://192.168.1.100:7912')).toBeInTheDocument();
      });
    });

    it('shows sync mode selector', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Sync Mode')).toBeInTheDocument();
      });
    });

    it('shows how sync works info', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('How Sync Works')).toBeInTheDocument();
      });
    });

    it('shows connection status section', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Status:')).toBeInTheDocument();
      });
    });

    it('shows Disconnected when not connected', async () => {
      vi.mocked(api.getSpoolmanStatus).mockResolvedValue({
        enabled: true,
        connected: false,
        url: 'http://localhost:7912',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Disconnected')).toBeInTheDocument();
      });
    });

    it('shows Connected and Disconnect button when connected', async () => {
      vi.mocked(api.getSpoolmanStatus).mockResolvedValue({
        enabled: true,
        connected: true,
        url: 'http://localhost:7912',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Connected')).toBeInTheDocument();
        expect(screen.getByText('Disconnect')).toBeInTheDocument();
      });
    });

    it('shows sync section when connected', async () => {
      vi.mocked(api.getSpoolmanStatus).mockResolvedValue({
        enabled: true,
        connected: true,
        url: 'http://localhost:7912',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Sync AMS Data')).toBeInTheDocument();
      });
    });
  });

  describe('weight sync toggle', () => {
    it('shows weight sync toggle when Spoolman enabled and sync mode is auto', async () => {
      vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'auto',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'true',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Disable AMS Estimated Weight Sync')).toBeInTheDocument();
      });
    });

    it('does not show weight sync toggle when sync mode is manual', async () => {
      vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'manual',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'true',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Filament Tracking')).toBeInTheDocument();
      });

      expect(screen.queryByText('Disable AMS Estimated Weight Sync')).not.toBeInTheDocument();
    });
  });

  describe('partial usage toggle', () => {
    it('shows partial usage toggle when weight sync is disabled', async () => {
      vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'auto',
        spoolman_disable_weight_sync: 'true',
        spoolman_report_partial_usage: 'true',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Report Partial Usage for Failed Prints')).toBeInTheDocument();
      });
    });

    it('does not show partial usage toggle when weight sync is enabled', async () => {
      vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'auto',
        spoolman_disable_weight_sync: 'false',
        spoolman_report_partial_usage: 'true',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Filament Tracking')).toBeInTheDocument();
      });

      expect(screen.queryByText('Report Partial Usage for Failed Prints')).not.toBeInTheDocument();
    });
  });

  describe('mode switching', () => {
    it('can switch to Spoolman mode', async () => {
      const user = userEvent.setup();
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Built-in Inventory')).toBeInTheDocument();
      });

      // Click Spoolman card
      await user.click(screen.getByText('Spoolman').closest('button')!);

      // Spoolman settings should now be visible
      await waitFor(() => {
        expect(screen.getByPlaceholderText('http://192.168.1.100:7912')).toBeInTheDocument();
      });
    });
  });
});
