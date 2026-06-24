/**
 * Tests for BulkTagsPickerModal (#1268).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BulkTagsPickerModal } from '../../components/BulkTagsPickerModal';
import { api } from '../../api/client';

const mockShowToast = vi.fn();
const mockOnClose = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    getLibraryTags: vi.fn(),
    createLibraryTag: vi.fn(),
    bulkAssignLibraryTags: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

const tags = [
  { id: 1, name: 'toy', file_count: 2, created_at: '2026-01-01', updated_at: '2026-01-01' },
  { id: 2, name: 'petg', file_count: 7, created_at: '2026-01-01', updated_at: '2026-01-01' },
];

function renderModal(fileIds: number[] = [10, 11, 12]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BulkTagsPickerModal open fileIds={fileIds} onClose={mockOnClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('BulkTagsPickerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getLibraryTags as ReturnType<typeof vi.fn>).mockResolvedValue(tags);
  });

  it('lists existing tags from the catalog', async () => {
    renderModal();
    expect(await screen.findByText('toy')).toBeInTheDocument();
    expect(screen.getByText('petg')).toBeInTheDocument();
  });

  it('checking a tag and clicking Add applies it via bulkAssignLibraryTags', async () => {
    (api.bulkAssignLibraryTags as ReturnType<typeof vi.fn>).mockResolvedValue({
      files_updated: 3,
      associations_added: 3,
      associations_removed: 0,
    });
    const user = userEvent.setup();
    renderModal([10, 11, 12]);
    await screen.findByText('toy');

    const toyCheckbox = screen
      .getAllByRole('checkbox')
      .find((el) => el.parentElement?.textContent?.includes('toy'));
    expect(toyCheckbox).toBeDefined();
    await user.click(toyCheckbox!);

    await user.click(screen.getByRole('button', { name: /Add tags/i }));
    await waitFor(() => {
      expect(api.bulkAssignLibraryTags).toHaveBeenCalledWith([10, 11, 12], [1], 'add');
    });
  });

  it('switching to Remove changes the apply action', async () => {
    (api.bulkAssignLibraryTags as ReturnType<typeof vi.fn>).mockResolvedValue({
      files_updated: 3,
      associations_added: 0,
      associations_removed: 3,
    });
    const user = userEvent.setup();
    renderModal([10, 11, 12]);
    await screen.findByText('toy');

    // Pick the Remove radio.
    await user.click(screen.getByRole('radio', { name: /Remove from selected files/i }));

    const petgCheckbox = screen
      .getAllByRole('checkbox')
      .find((el) => el.parentElement?.textContent?.includes('petg'));
    await user.click(petgCheckbox!);

    await user.click(screen.getByRole('button', { name: /Remove tags/i }));
    await waitFor(() => {
      expect(api.bulkAssignLibraryTags).toHaveBeenCalledWith([10, 11, 12], [2], 'remove');
    });
  });

  it('apply is disabled when no tag is selected', async () => {
    renderModal();
    await screen.findByText('toy');
    expect(screen.getByRole('button', { name: /Add tags/i })).toBeDisabled();
  });
});
