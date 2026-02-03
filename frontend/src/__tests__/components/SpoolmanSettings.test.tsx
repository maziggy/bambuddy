/**
 * Tests for the SpoolmanSettings component.
 *
 * Tests the Spoolman integration UI including:
 * - Enable/disable toggle
 * - URL configuration
 * - Connection status
 * - Sync functionality
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
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
  },
}));

// Import mocked module
import { api } from '../../api/client';

describe('SpoolmanSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    // Default API mocks
    vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
      spoolman_enabled: 'false',
      spoolman_url: '',
      spoolman_sync_mode: 'auto',
    });
    vi.mocked(api.updateSpoolmanSettings).mockResolvedValue({
      spoolman_enabled: 'false',
      spoolman_url: '',
      spoolman_sync_mode: 'auto',
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
      // Delay the API response to catch loading state
      vi.mocked(api.getSpoolmanSettings).mockImplementation(() => new Promise(() => {}));
      render(<SpoolmanSettings />);

      // Should show loading spinner
      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });

    it('renders component title', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Spoolman Integration')).toBeInTheDocument();
      });
    });

    it('renders enable toggle', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Enable Spoolman')).toBeInTheDocument();
      });
    });

    it('renders URL input', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Spoolman URL')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('http://192.168.1.100:7912')).toBeInTheDocument();
      });
    });

    it('renders sync mode selector', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Sync Mode')).toBeInTheDocument();
      });
    });

    it('renders info banner about sync', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('How Sync Works')).toBeInTheDocument();
        expect(screen.getByText(/Only official Bambu Lab spools/)).toBeInTheDocument();
      });
    });
  });

  describe('disabled state', () => {
    it('URL input is disabled when Spoolman is disabled', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        const urlInput = screen.getByPlaceholderText('http://192.168.1.100:7912');
        expect(urlInput).toBeDisabled();
      });
    });

    it('sync mode selector is disabled when Spoolman is disabled', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        // Find the select by its display value
        const selectElement = screen.getByDisplayValue('Automatic');
        expect(selectElement).toBeDisabled();
      });
    });

    it('does not show connection status when disabled', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Spoolman Integration')).toBeInTheDocument();
      });

      // Status section should not be visible when disabled
      expect(screen.queryByText('Status:')).not.toBeInTheDocument();
    });
  });

  describe('enabled state', () => {
    beforeEach(() => {
      vi.mocked(api.getSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'auto',
      });
      vi.mocked(api.updateSpoolmanSettings).mockResolvedValue({
        spoolman_enabled: 'true',
        spoolman_url: 'http://localhost:7912',
        spoolman_sync_mode: 'auto',
      });
    });

    it('URL input is enabled when Spoolman is enabled', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        const urlInput = screen.getByPlaceholderText('http://192.168.1.100:7912');
        expect(urlInput).not.toBeDisabled();
      });
    });

    it('shows connection status section when enabled', async () => {
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

    it('shows Connect button when disconnected', async () => {
      vi.mocked(api.getSpoolmanStatus).mockResolvedValue({
        enabled: true,
        connected: false,
        url: 'http://localhost:7912',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByText('Connect')).toBeInTheDocument();
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
        expect(screen.getByText('Sync')).toBeInTheDocument();
      });
    });

    it('shows All Printers option in sync dropdown', async () => {
      vi.mocked(api.getSpoolmanStatus).mockResolvedValue({
        enabled: true,
        connected: true,
        url: 'http://localhost:7912',
      });

      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByRole('option', { name: 'All Printers' })).toBeInTheDocument();
      });
    });
  });

  describe('sync mode options', () => {
    it('shows Automatic option', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByRole('option', { name: 'Automatic' })).toBeInTheDocument();
      });
    });

    it('shows Manual Only option', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(screen.getByRole('option', { name: 'Manual Only' })).toBeInTheDocument();
      });
    });
  });

  describe('info text', () => {
    it('shows URL help text', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(
          screen.getByText('URL of your Spoolman server (e.g., http://localhost:7912)')
        ).toBeInTheDocument();
      });
    });

    it('shows sync mode description for auto mode', async () => {
      render(<SpoolmanSettings />);

      await waitFor(() => {
        expect(
          screen.getByText('AMS data syncs automatically when changes are detected')
        ).toBeInTheDocument();
      });
    });
  });
});
