/**
 * Tests for LibraryTagsModal (#1268).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LibraryTagsModal } from '../../components/LibraryTagsModal';
import { api } from '../../api/client';

const mockShowToast = vi.fn();
const mockOnClose = vi.fn();
const mockOnPick = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    getLibraryTags: vi.fn(),
    createLibraryTag: vi.fn(),
    updateLibraryTag: vi.fn(),
    deleteLibraryTag: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

const tags = [
  { id: 1, name: 'toy', file_count: 4, created_at: '2026-01-01', updated_at: '2026-01-01' },
  { id: 2, name: 'kid-safe', file_count: 0, created_at: '2026-01-01', updated_at: '2026-01-01' },
];

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LibraryTagsModal open onClose={mockOnClose} onPickTag={mockOnPick} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('LibraryTagsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getLibraryTags as ReturnType<typeof vi.fn>).mockResolvedValue(tags);
  });

  it('renders the catalog with file counts', async () => {
    renderModal();
    expect(await screen.findByText('toy')).toBeInTheDocument();
    expect(screen.getByText('kid-safe')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('opens the editor and calls createLibraryTag on save', async () => {
    (api.createLibraryTag as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 3,
      name: 'new',
      file_count: 0,
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    });
    const user = userEvent.setup();
    renderModal();
    await screen.findByText('toy');
    // Header has both "New tag" and "Manage tag catalog" — click the one
    // that opens the editor (the button with Plus icon).
    await user.click(screen.getByRole('button', { name: /New tag/i }));
    const input = await screen.findByLabelText(/Name/i);
    await user.type(input, 'new');
    await user.click(screen.getByRole('button', { name: /Save/i }));
    await waitFor(() => {
      expect(api.createLibraryTag).toHaveBeenCalledWith('new');
    });
  });

  it('clicking a row invokes onPickTag and closes the modal', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(await screen.findByText('toy'));
    expect(mockOnPick).toHaveBeenCalledWith(1);
    expect(mockOnClose).toHaveBeenCalled();
  });

  it('confirm dialog warns when deleting an in-use tag', async () => {
    const user = userEvent.setup();
    renderModal();
    await screen.findByText('toy');
    await user.click(screen.getByLabelText('Delete toy'));
    // In-use message — substring match keeps the test robust to whitespace.
    expect(await screen.findByText(/{{count}}|on 4|4 file/i)).toBeInTheDocument();
  });
});
