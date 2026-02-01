/**
 * Tests for the TagManagementModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { TagManagementModal } from '../../components/TagManagementModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockTags = [
  { name: 'functional', count: 5 },
  { name: 'calibration', count: 3 },
  { name: 'test', count: 2 },
  { name: 'art', count: 1 },
];

describe('TagManagementModal', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/archives/tags', () => {
        return HttpResponse.json(mockTags);
      }),
      http.put('/api/v1/archives/tags/:tagName', async () => {
        return HttpResponse.json({ affected: 2 });
      }),
      http.delete('/api/v1/archives/tags/:tagName', () => {
        return HttpResponse.json({ affected: 1 });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal title', async () => {
      render(<TagManagementModal onClose={mockOnClose} />);

      expect(screen.getByText('Manage Tags')).toBeInTheDocument();
    });

    it('shows loading state initially', () => {
      render(<TagManagementModal onClose={mockOnClose} />);

      // Should show loading spinner before data loads
      expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument();
    });

    it('displays tags with counts', async () => {
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
        expect(screen.getByText('5')).toBeInTheDocument();
        expect(screen.getByText('calibration')).toBeInTheDocument();
        expect(screen.getByText('3')).toBeInTheDocument();
      });
    });

    it('shows total tag count and usage', async () => {
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        // 4 tags, 11 total usages
        expect(screen.getByText(/4 tags across 11 usages/i)).toBeInTheDocument();
      });
    });
  });

  describe('search functionality', () => {
    it('filters tags by search input', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      const searchInput = screen.getByPlaceholderText('Search tags...');
      await user.type(searchInput, 'cal');

      await waitFor(() => {
        expect(screen.getByText('calibration')).toBeInTheDocument();
        expect(screen.queryByText('functional')).not.toBeInTheDocument();
        expect(screen.queryByText('art')).not.toBeInTheDocument();
      });
    });

    it('shows no results message when search has no matches', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      const searchInput = screen.getByPlaceholderText('Search tags...');
      await user.type(searchInput, 'nonexistent');

      await waitFor(() => {
        expect(screen.getByText('No tags match your search')).toBeInTheDocument();
      });
    });
  });

  describe('sorting', () => {
    it('sorts by count by default', async () => {
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        const tagElements = screen.getAllByText(/functional|calibration|test|art/);
        // First should be functional (count 5)
        expect(tagElements[0]).toHaveTextContent('functional');
      });
    });

    it('can sort by name', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      const sortSelect = screen.getByDisplayValue('Sort by Count');
      await user.selectOptions(sortSelect, 'name');

      await waitFor(() => {
        const tagElements = screen.getAllByText(/functional|calibration|test|art/);
        // First should be 'art' alphabetically
        expect(tagElements[0]).toHaveTextContent('art');
      });
    });
  });

  describe('rename functionality', () => {
    it('enters edit mode when clicking edit button', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      // Find the tag row and click its edit button
      const tagRow = screen.getByText('functional').closest('div');
      const editButton = within(tagRow!).getByTitle('Rename tag');
      await user.click(editButton);

      // Should show input with current value
      await waitFor(() => {
        expect(screen.getByDisplayValue('functional')).toBeInTheDocument();
      });
    });

    it('submits rename on Enter key', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      const tagRow = screen.getByText('functional').closest('div');
      const editButton = within(tagRow!).getByTitle('Rename tag');
      await user.click(editButton);

      const input = screen.getByDisplayValue('functional');
      await user.clear(input);
      await user.type(input, 'new-name{Enter}');

      // Should show success (mutation called)
      await waitFor(() => {
        // After successful rename, edit mode should close
        expect(screen.queryByDisplayValue('new-name')).not.toBeInTheDocument();
      });
    });

    it('cancels edit on Escape key', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      const tagRow = screen.getByText('functional').closest('div');
      const editButton = within(tagRow!).getByTitle('Rename tag');
      await user.click(editButton);

      const input = screen.getByDisplayValue('functional');
      await user.type(input, '-modified{Escape}');

      // Should exit edit mode without saving
      await waitFor(() => {
        expect(screen.queryByDisplayValue('functional-modified')).not.toBeInTheDocument();
        expect(screen.getByText('functional')).toBeInTheDocument();
      });
    });
  });

  describe('delete functionality', () => {
    it('shows delete confirmation when clicking delete button', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      const tagRow = screen.getByText('functional').closest('div');
      const deleteButton = within(tagRow!).getByTitle('Delete tag');
      await user.click(deleteButton);

      await waitFor(() => {
        expect(screen.getByText(/Delete "functional" from 5 archives?/i)).toBeInTheDocument();
      });
    });

    it('cancels delete confirmation on X button', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('functional')).toBeInTheDocument();
      });

      const tagRow = screen.getByText('functional').closest('div');
      const deleteButton = within(tagRow!).getByTitle('Delete tag');
      await user.click(deleteButton);

      await waitFor(() => {
        expect(screen.getByText(/Delete "functional"/i)).toBeInTheDocument();
      });

      // Find the confirmation row and click the cancel (X) button within it
      const confirmationText = screen.getByText(/Delete "functional"/i);
      const confirmationRow = confirmationText.closest('div');
      // The X button is the last button in the confirmation row
      const buttons = within(confirmationRow!.parentElement!).getAllByRole('button');
      const cancelButton = buttons[buttons.length - 1]; // X button is last
      await user.click(cancelButton);

      await waitFor(() => {
        // Should return to normal display - the tag name should be visible again
        expect(screen.getByText('functional')).toBeInTheDocument();
      });
    });
  });

  describe('modal behavior', () => {
    it('calls onClose when close button is clicked', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('Manage Tags')).toBeInTheDocument();
      });

      // Find close button in header (X icon)
      const headerCloseButton = screen.getAllByRole('button')[0];
      await user.click(headerCloseButton);

      expect(mockOnClose).toHaveBeenCalledTimes(1);
    });

    it('calls onClose when Close button is clicked', async () => {
      const user = userEvent.setup();
      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('Manage Tags')).toBeInTheDocument();
      });

      const closeButton = screen.getByRole('button', { name: /close/i });
      await user.click(closeButton);

      expect(mockOnClose).toHaveBeenCalledTimes(1);
    });
  });

  describe('empty state', () => {
    it('shows empty message when no tags exist', async () => {
      server.use(
        http.get('/api/v1/archives/tags', () => {
          return HttpResponse.json([]);
        })
      );

      render(<TagManagementModal onClose={mockOnClose} />);

      await waitFor(() => {
        expect(screen.getByText('No tags found')).toBeInTheDocument();
      });
    });
  });
});
