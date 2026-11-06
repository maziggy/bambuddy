/**
 * BatchProjectModal: the "Added N" toast counts what the server added. The
 * server skips archives the user may not change (other teams' printers, other
 * users' prints), so the number selected can be higher.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BatchProjectModal } from '../../components/BatchProjectModal';
import { api } from '../../api/client';

const mockShowToast = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    getProjects: vi.fn(),
    addArchivesToProject: vi.fn(),
    updateArchive: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

describe('BatchProjectModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getProjects as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 7, name: 'Voron', status: 'active', parent_id: null },
    ]);
  });

  it('reports the number of archives the server added, not the number selected', async () => {
    (api.addArchivesToProject as ReturnType<typeof vi.fn>).mockResolvedValue({
      message: 'Added 1 archives to project',
      count: 1,
    });
    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <BatchProjectModal selectedIds={[1, 2, 3]} onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByText('Voron'));

    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Added 1 archive to "Voron"'));
    expect(api.addArchivesToProject).toHaveBeenCalledWith(7, [1, 2, 3]);
  });
});
