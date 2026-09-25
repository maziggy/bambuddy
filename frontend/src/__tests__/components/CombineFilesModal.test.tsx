/**
 * Tests for CombineFilesModal: STLs -> one multi-object 3MF (#2999).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CombineFilesModal } from '../../components/CombineFilesModal';
import { api, type LibraryFileListItem } from '../../api/client';

const mockShowToast = vi.fn();
const mockOnClose = vi.fn();
const mockOnCombined = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    combineLibraryFiles: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

function file(id: number, filename: string): LibraryFileListItem {
  return { id, filename, file_type: 'stl' } as LibraryFileListItem;
}

function renderModal(files: LibraryFileListItem[], canSlice = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CombineFilesModal
        files={files}
        folderId={7}
        canSlice={canSlice}
        onClose={mockOnClose}
        onCombined={mockOnCombined}
      />
    </QueryClientProvider>,
  );
}

describe('CombineFilesModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('suggests a name from the first file and lists every model', () => {
    renderModal([file(1, 'bracket.stl'), file(2, 'clip.stl'), file(3, 'knob.stl')]);
    expect(screen.getByLabelText('File name')).toHaveValue('bracket + 2 more');
    expect(screen.getByText('clip.stl')).toBeInTheDocument();
    expect(screen.getByText('Objects on the plate: 3')).toBeInTheDocument();
  });

  it('sends copies, name and folder, then hands the new file on for slicing', async () => {
    const result = { id: 99, filename: 'bracket x4.3mf', file_type: '3mf', file_size: 1, thumbnail_path: null, duplicate_of: null, metadata: null };
    (api.combineLibraryFiles as ReturnType<typeof vi.fn>).mockResolvedValue(result);
    const user = userEvent.setup();
    renderModal([file(1, 'bracket.stl')]);

    const copies = screen.getByLabelText('Copies of bracket.stl');
    await user.clear(copies);
    await user.type(copies, '4');
    expect(screen.getByLabelText('File name')).toHaveValue('bracket x4');

    await user.click(screen.getByRole('button', { name: 'Combine' }));

    await waitFor(() => {
      expect(api.combineLibraryFiles).toHaveBeenCalledWith([{ file_id: 1, copies: 4 }], 'bracket x4', 7);
    });
    expect(mockOnCombined).toHaveBeenCalledWith(result, true);
  });

  it('blocks more objects than one plate allows', async () => {
    const user = userEvent.setup();
    renderModal([file(1, 'a.stl'), file(2, 'b.stl')]);

    const copies = screen.getByLabelText('Copies of a.stl');
    await user.clear(copies);
    await user.type(copies, '100');

    expect(screen.getByText('At most 100 objects per plate')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Combine' })).toBeDisabled();
  });

  it('hides the slice option when in-app slicing is unavailable', async () => {
    (api.combineLibraryFiles as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 5, filename: 'x.3mf' });
    const user = userEvent.setup();
    renderModal([file(1, 'a.stl')], false);

    expect(screen.queryByText('Open the slicer when done')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Combine' }));
    await waitFor(() => expect(mockOnCombined).toHaveBeenCalledWith({ id: 5, filename: 'x.3mf' }, false));
  });
});
