/**
 * Tests for the ExternalFolderModal component.
 *
 * These tests focus on:
 * - Form rendering and validation
 * - Path validation with debouncing
 * - Submit behavior and error handling
 * - Read-only toggle functionality
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ExternalFolderModal } from '../../components/ExternalFolderModal';
import * as apiClient from '../../api/client';

// Mock the API client
vi.mock('../../api/client', () => ({
  api: {
    validateExternalPath: vi.fn(),
    createExternalFolder: vi.fn(),
  },
}));

describe('ExternalFolderModal', () => {
  const mockOnClose = vi.fn();
  const mockOnSuccess = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders modal with title', () => {
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByText('Mount External Folder')).toBeInTheDocument();
    });

    it('renders path input field', () => {
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByLabelText(/folder path/i)).toBeInTheDocument();
    });

    it('renders name input field', () => {
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByLabelText(/folder name/i)).toBeInTheDocument();
    });

    it('renders readonly toggle', () => {
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByLabelText(/read-only/i)).toBeInTheDocument();
    });

    it('renders cancel button', () => {
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('renders create button', () => {
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      expect(screen.getByRole('button', { name: /mount|create/i })).toBeInTheDocument();
    });
  });

  describe('path validation', () => {
    it('shows validation error when path is invalid', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: false,
        error: 'Path does not exist',
      });

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const pathInput = screen.getByLabelText(/folder path/i);
      await user.type(pathInput, '/nonexistent/path');

      await waitFor(
        () => {
          expect(screen.getByText('Path does not exist')).toBeInTheDocument();
        },
        { timeout: 2000 }
      );
    });

    it('shows validation success when path is valid', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: true,
        file_count: 42,
        directory_size_mb: 512,
      });

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const pathInput = screen.getByLabelText(/folder path/i);
      await user.type(pathInput, '/mnt/external');

      await waitFor(
        () => {
          expect(screen.getByText(/42\s+files?/i)).toBeInTheDocument();
        },
        { timeout: 2000 }
      );
    });

    it('auto-fills name from path when validation succeeds', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: true,
      });

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const pathInput = screen.getByLabelText(/folder path/i);
      await user.type(pathInput, '/mnt/nas-backup');

      await waitFor(
        () => {
          const nameInput = screen.getByLabelText(/folder name/i) as HTMLInputElement;
          expect(nameInput.value).toBe('nas-backup');
        },
        { timeout: 2000 }
      );
    });

    it('does not auto-fill name if already set', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: true,
      });

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const nameInput = screen.getByLabelText(/folder name/i);
      await user.type(nameInput, 'My Custom Name');

      const pathInput = screen.getByLabelText(/folder path/i);
      await user.type(pathInput, '/mnt/some-path');

      await waitFor(() => {
        expect((nameInput as HTMLInputElement).value).toBe('My Custom Name');
      });
    });
  });

  describe('readonly toggle', () => {
    it('is checked by default', () => {
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const readonlyToggle = screen.getByLabelText(/read-only/i) as HTMLInputElement;
      expect(readonlyToggle.checked).toBe(true);
    });

    it('can be toggled', async () => {
      const user = userEvent.setup();
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const readonlyToggle = screen.getByLabelText(/read-only/i) as HTMLInputElement;
      expect(readonlyToggle.checked).toBe(true);

      await user.click(readonlyToggle);
      expect(readonlyToggle.checked).toBe(false);
    });
  });

  describe('form submission', () => {
    it('disables submit button when path is invalid', () => {
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: false,
        error: 'Invalid path',
      });

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const submitButton = screen.getByRole('button', { name: /mount|create/i });
      expect(submitButton).toBeDisabled();
    });

    it('disables submit button when name is empty', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: true,
      });

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const pathInput = screen.getByLabelText(/folder path/i);
      await user.type(pathInput, '/mnt/external');

      await waitFor(() => {
        const nameInput = screen.getByLabelText(/folder name/i) as HTMLInputElement;
        // Clear auto-filled name
        nameInput.value = '';
      });

      const submitButton = screen.getByRole('button', { name: /mount|create/i });
      expect(submitButton).toBeDisabled();
    });

    it('submits form with correct data', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: true,
      });
      vi.mocked(apiClient.api.createExternalFolder).mockResolvedValue({
        id: 1,
        name: 'Test Folder',
        external_path: '/mnt/external',
        is_external: true,
        external_readonly: true,
      } as any);

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const pathInput = screen.getByLabelText(/folder path/i);
      await user.type(pathInput, '/mnt/external');

      await waitFor(() => {
        expect(screen.getByLabelText(/folder name/i)).toHaveValue('external');
      });

      const submitButton = screen.getByRole('button', { name: /mount|create/i });
      await user.click(submitButton);

      await waitFor(() => {
        expect(apiClient.api.createExternalFolder).toHaveBeenCalledWith(
          expect.objectContaining({
            external_path: '/mnt/external',
            name: 'external',
            external_readonly: true,
          })
        );
      });
    });

    it('closes modal on successful submission', async () => {
      const user = userEvent.setup();
      vi.mocked(apiClient.api.validateExternalPath).mockResolvedValue({
        valid: true,
      });
      vi.mocked(apiClient.api.createExternalFolder).mockResolvedValue({} as any);

      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const pathInput = screen.getByLabelText(/folder path/i);
      await user.type(pathInput, '/mnt/external');

      await waitFor(() => {
        expect(screen.getByLabelText(/folder name/i)).toHaveValue('external');
      });

      const submitButton = screen.getByRole('button', { name: /mount|create/i });
      await user.click(submitButton);

      await waitFor(() => {
        expect(mockOnSuccess).toHaveBeenCalled();
        expect(mockOnClose).toHaveBeenCalled();
      });
    });
  });

  describe('cancel button', () => {
    it('closes modal when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(
        <ExternalFolderModal
          parentId={null}
          onClose={mockOnClose}
          onSuccess={mockOnSuccess}
        />
      );

      const cancelButton = screen.getByRole('button', { name: /cancel/i });
      await user.click(cancelButton);

      expect(mockOnClose).toHaveBeenCalled();
    });
  });
});
