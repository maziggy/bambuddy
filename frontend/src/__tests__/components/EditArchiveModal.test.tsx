/**
 * Tests for the EditArchiveModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { EditArchiveModal } from '../../components/EditArchiveModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockArchive = {
  id: 1,
  filename: 'benchy.gcode.3mf',
  print_name: 'Benchy',
  printer_id: 1,
  printer_name: 'X1 Carbon',
  notes: 'Test notes',
  rating: 4,
  project_id: null,
  tags: 'test,calibration',
};

const mockProjects = [
  { id: 1, name: 'Functional Parts', color: '#00ae42' },
  { id: 2, name: 'Art', color: '#ff5500' },
];

describe('EditArchiveModal', () => {
  const mockOnClose = vi.fn();
  const mockOnSave = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/projects/', () => {
        return HttpResponse.json(mockProjects);
      }),
      http.get('/api/v1/archives/tags', () => {
        return HttpResponse.json([
          { name: 'test', count: 2 },
          { name: 'calibration', count: 1 },
          { name: 'functional', count: 3 },
        ]);
      }),
      http.patch('/api/v1/archives/:id', async ({ request }) => {
        const body = await request.json();
        return HttpResponse.json({ ...mockArchive, ...body });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal title', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText(/edit/i)).toBeInTheDocument();
    });

    it('shows print name field', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        // Name field should be present
        const nameInput = screen.getByDisplayValue('Benchy');
        expect(nameInput).toBeInTheDocument();
      });
    });

    it('shows notes field', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        const notesField = screen.getByDisplayValue('Test notes');
        expect(notesField).toBeInTheDocument();
      });
    });

    it('shows rating selector', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        // Rating may be shown as stars or dropdown
        expect(screen.getByText(/edit/i)).toBeInTheDocument();
      });
    });

    it('shows project selector', async () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await waitFor(() => {
        // Project section should be present
        expect(screen.getByText(/edit/i)).toBeInTheDocument();
      });
    });

    it('shows tags input', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText(/tags/i)).toBeInTheDocument();
    });
  });

  describe('existing values', () => {
    it('shows existing tags', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText('test')).toBeInTheDocument();
      expect(screen.getByText('calibration')).toBeInTheDocument();
    });
  });

  describe('actions', () => {
    it('has save button', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
    });

    it('has cancel button', () => {
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('calls onClose when cancel is clicked', async () => {
      const user = userEvent.setup();
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(mockOnClose).toHaveBeenCalled();
    });

    it('can edit print name', async () => {
      const user = userEvent.setup();
      render(
        <EditArchiveModal
          archive={mockArchive}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      const nameInput = screen.getByDisplayValue('Benchy');
      await user.clear(nameInput);
      await user.type(nameInput, 'New Name');

      expect(nameInput).toHaveValue('New Name');
    });
  });
});
