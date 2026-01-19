/**
 * Tests for the RestoreModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { RestoreModal } from '../../components/RestoreModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('RestoreModal', () => {
  const mockOnClose = vi.fn();
  const mockOnRestore = vi.fn();
  const mockOnSuccess = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.post('/api/v1/settings/restore', () => {
        return HttpResponse.json({ success: true });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal title', () => {
      render(<RestoreModal onClose={mockOnClose} onRestore={mockOnRestore} onSuccess={mockOnSuccess} />);

      // Title is "Restore Backup"
      expect(screen.getByText('Restore Backup')).toBeInTheDocument();
    });

    it('shows file upload area', () => {
      render(<RestoreModal onClose={mockOnClose} onRestore={mockOnRestore} onSuccess={mockOnSuccess} />);

      expect(screen.getByText(/select.*file/i)).toBeInTheDocument();
    });

    it('shows cancel button', () => {
      render(<RestoreModal onClose={mockOnClose} onRestore={mockOnRestore} onSuccess={mockOnSuccess} />);

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });
  });

  describe('file input', () => {
    it('accepts backup files', () => {
      render(<RestoreModal onClose={mockOnClose} onRestore={mockOnRestore} onSuccess={mockOnSuccess} />);

      const fileInput = document.querySelector('input[type="file"]');
      expect(fileInput).toBeInTheDocument();
    });
  });

  describe('actions', () => {
    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(<RestoreModal onClose={mockOnClose} onRestore={mockOnRestore} onSuccess={mockOnSuccess} />);

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('overwrite option', () => {
    it('has overwrite toggle', () => {
      render(<RestoreModal onClose={mockOnClose} onRestore={mockOnRestore} onSuccess={mockOnSuccess} />);

      // The modal has toggle for replacing existing data
      expect(screen.getByText('Keep existing data')).toBeInTheDocument();
    });

    it('shows warning when overwrite is enabled', async () => {
      const user = userEvent.setup();
      render(<RestoreModal onClose={mockOnClose} onRestore={mockOnRestore} onSuccess={mockOnSuccess} />);

      // Find and click the toggle (uses role="switch")
      const toggle = screen.getByRole('switch');
      await user.click(toggle);

      await waitFor(() => {
        expect(screen.getByText(/Caution/)).toBeInTheDocument();
      });
    });
  });
});
