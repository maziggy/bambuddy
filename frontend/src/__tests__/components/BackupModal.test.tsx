/**
 * Tests for the BackupModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { BackupModal } from '../../components/BackupModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('BackupModal', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.post('/api/v1/settings/backup', () => {
        return new HttpResponse(
          JSON.stringify({ success: true }),
          {
            headers: {
              'Content-Type': 'application/json',
            },
          }
        );
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal title', () => {
      render(<BackupModal onClose={mockOnClose} />);

      expect(screen.getByText(/backup/i)).toBeInTheDocument();
    });

    it('shows backup options', () => {
      render(<BackupModal onClose={mockOnClose} />);

      expect(screen.getByText(/settings/i)).toBeInTheDocument();
    });

    it('shows export button', () => {
      render(<BackupModal onClose={mockOnClose} />);

      expect(screen.getByRole('button', { name: /export/i })).toBeInTheDocument();
    });

    it('shows cancel button', () => {
      render(<BackupModal onClose={mockOnClose} />);

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });
  });

  describe('backup options', () => {
    it('has checkbox for printers', () => {
      render(<BackupModal onClose={mockOnClose} />);

      expect(screen.getByText('Printers')).toBeInTheDocument();
    });

    it('has checkbox for archives', () => {
      render(<BackupModal onClose={mockOnClose} />);

      expect(screen.getByText(/archives/i)).toBeInTheDocument();
    });

    it('has checkbox for projects', () => {
      render(<BackupModal onClose={mockOnClose} />);

      expect(screen.getByText('Projects')).toBeInTheDocument();
    });
  });

  describe('actions', () => {
    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(<BackupModal onClose={mockOnClose} />);

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(mockOnClose).toHaveBeenCalled();
    });
  });
});
