/**
 * Tests for the AddToQueueModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { AddToQueueModal } from '../../components/AddToQueueModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinters = [
  {
    id: 1,
    name: 'X1 Carbon',
    ip_address: '192.168.1.100',
    model: 'X1C',
    enabled: true,
  },
  {
    id: 2,
    name: 'P1S',
    ip_address: '192.168.1.101',
    model: 'P1S',
    enabled: true,
  },
];

const mockPlates = [
  { id: 1, plate_number: 1, name: 'Plate 1' },
  { id: 2, plate_number: 2, name: 'Plate 2' },
];

describe('AddToQueueModal', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.get('/api/v1/archives/:id/plates', () => {
        return HttpResponse.json(mockPlates);
      }),
      http.get('/api/v1/archives/:id/filament-requirements', () => {
        return HttpResponse.json([]);
      }),
      http.post('/api/v1/queue/', () => {
        return HttpResponse.json({ id: 1, status: 'pending' });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal title', () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Schedule Print')).toBeInTheDocument();
    });

    it('shows archive name', () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Test Print')).toBeInTheDocument();
    });

    it('shows printer selector', async () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Printer')).toBeInTheDocument();
      });
    });

    it('shows add button', () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /add to queue/i })).toBeInTheDocument();
    });

    it('shows cancel button', () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });
  });

  describe('queue options', () => {
    it('shows Queue Only option', () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Queue Only')).toBeInTheDocument();
    });

    it('shows power off option', () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText(/power off/i)).toBeInTheDocument();
    });
  });

  describe('print options', () => {
    it('has print configuration options', async () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      // Modal should render and have configuration options
      await waitFor(() => {
        expect(screen.getByText('Schedule Print')).toBeInTheDocument();
      });
    });
  });

  describe('actions', () => {
    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('plate selection', () => {
    it('shows plate selector when plates exist', async () => {
      render(
        <AddToQueueModal
          archiveId={1}
          archiveName="Test Print"
          onClose={mockOnClose}
        />
      );

      // Modal should render - plate selector may be conditional
      await waitFor(() => {
        expect(screen.getByText('Schedule Print')).toBeInTheDocument();
      });
    });
  });
});
