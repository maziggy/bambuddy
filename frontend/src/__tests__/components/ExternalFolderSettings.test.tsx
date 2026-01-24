/**
 * Tests for the ExternalFolderSettings component.
 *
 * These tests focus on:
 * - Settings loading and display
 * - Toggle controls for enable/disable
 * - Text input fields for paths and scan depth
 * - Error handling and loading states
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ExternalFolderSettings } from '../../components/ExternalFolderSettings';
import * as apiClient from '../../api/client';

// Mock the API client
vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn(),
    updateSettings: vi.fn(),
  },
}));

const mockSettings = {
  external_library_enabled: true,
  external_library_allowed_paths: '/mnt/external,/mnt/nas',
  external_library_max_scan_depth: 10,
};

describe('ExternalFolderSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('shows loading state initially', () => {
      vi.mocked(apiClient.api.getSettings).mockImplementation(
        () => new Promise(() => {}) // Never resolves
      );

      render(<ExternalFolderSettings />);

      expect(screen.getByText(/loading/i)).toBeInTheDocument();
    });

    it('renders settings title', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByText(/external folder settings/i)).toBeInTheDocument();
      });
    });

    it('renders description text', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByText(/configure how bambuddy mounts/i)).toBeInTheDocument();
      });
    });

    it('renders enable toggle', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByLabelText(/enable external folders/i)).toBeInTheDocument();
      });
    });

    it('renders allowed paths textarea', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByLabelText(/allowed base paths/i)).toBeInTheDocument();
      });
    });

    it('renders max scan depth input', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByLabelText(/maximum scan depth/i)).toBeInTheDocument();
      });
    });
  });

  describe('enable/disable toggle', () => {
    it('displays current enabled state', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue({
        ...mockSettings,
        external_library_enabled: true,
      } as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        const toggle = screen.getByLabelText(/enable external folders/i) as HTMLInputElement;
        expect(toggle.checked).toBe(true);
      });
    });

    it('displays disabled state', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue({
        ...mockSettings,
        external_library_enabled: false,
      } as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        const toggle = screen.getByLabelText(/enable external folders/i) as HTMLInputElement;
        expect(toggle.checked).toBe(false);
      });
    });

    it('calls API when toggle is changed', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.getSettings).mockResolvedValue({
        ...mockSettings,
        external_library_enabled: true,
      } as any);
      vi.mocked(apiClient.api.updateSettings).mockResolvedValue({} as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByLabelText(/enable external folders/i)).toBeInTheDocument();
      });

      const toggle = screen.getByLabelText(/enable external folders/i);
      await user.click(toggle);

      await waitFor(() => {
        expect(apiClient.api.updateSettings).toHaveBeenCalledWith(
          expect.objectContaining({
            external_library_enabled: false,
          })
        );
      });
    });
  });

  describe('allowed paths textarea', () => {
    it('displays current paths', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue({
        ...mockSettings,
        external_library_allowed_paths: '/mnt/external,/mnt/nas,/mnt/models',
      } as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        const textarea = screen.getByLabelText(/allowed base paths/i) as HTMLTextAreaElement;
        expect(textarea.value).toContain('/mnt/external');
        expect(textarea.value).toContain('/mnt/nas');
        expect(textarea.value).toContain('/mnt/models');
      });
    });

    it('calls API when paths are changed', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);
      vi.mocked(apiClient.api.updateSettings).mockResolvedValue({} as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByLabelText(/allowed base paths/i)).toBeInTheDocument();
      });

      const textarea = screen.getByLabelText(/allowed base paths/i);
      await user.clear(textarea);
      await user.type(textarea, '/mnt/new-path');

      await waitFor(() => {
        expect(apiClient.api.updateSettings).toHaveBeenCalledWith(
          expect.objectContaining({
            external_library_allowed_paths: '/mnt/new-path',
          })
        );
      });
    });

    it('shows security note about paths', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByText(/security note/i)).toBeInTheDocument();
        expect(screen.getByText(/container paths/i)).toBeInTheDocument();
      });
    });
  });

  describe('max scan depth input', () => {
    it('displays current max scan depth', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue({
        ...mockSettings,
        external_library_max_scan_depth: 15,
      } as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        const input = screen.getByLabelText(/maximum scan depth/i) as HTMLInputElement;
        expect(input.value).toBe('15');
      });
    });

    it('calls API when max scan depth is changed', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);
      vi.mocked(apiClient.api.updateSettings).mockResolvedValue({} as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByLabelText(/maximum scan depth/i)).toBeInTheDocument();
      });

      const input = screen.getByLabelText(/maximum scan depth/i);
      await user.clear(input);
      await user.type(input, '20');

      await waitFor(() => {
        expect(apiClient.api.updateSettings).toHaveBeenCalledWith(
          expect.objectContaining({
            external_library_max_scan_depth: 20,
          })
        );
      });
    });

    it('shows range hint', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByText(/1-20/i)).toBeInTheDocument();
      });
    });
  });

  describe('description text', () => {
    it('shows helpful information about external folders', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByText(/NAS|USB|network/i)).toBeInTheDocument();
      });
    });
  });

  describe('help text', () => {
    it('shows help text for allowed paths field', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByText(/comma-separated list/i)).toBeInTheDocument();
        expect(screen.getByText(/\/mnt\/nas,\/mnt\/external/i)).toBeInTheDocument();
      });
    });

    it('shows help text for scan depth field', async () => {
      vi.mocked(apiClient.api.getSettings).mockResolvedValue(mockSettings as any);

      render(<ExternalFolderSettings />);

      await waitFor(() => {
        expect(screen.getByText(/number of directory levels/i)).toBeInTheDocument();
      });
    });
  });
});
