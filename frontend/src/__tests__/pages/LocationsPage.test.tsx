import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LocationsPage from '../../pages/LocationsPage';
import { api, ApiError } from '../../api/client';

const mockShowToast = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    getLocations: vi.fn(),
    createLocation: vi.fn(),
    updateLocation: vi.fn(),
    deleteLocation: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

const locations = [
  { id: 1, name: 'Shelf A', identifier: null, spool_count: 2, created_at: '2026-01-01', updated_at: '2026-01-01' },
  { id: 2, name: 'Drawer 1', identifier: null, spool_count: 0, created_at: '2026-01-01', updated_at: '2026-01-01' },
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LocationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('LocationsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getLocations).mockResolvedValue(locations);
  });

  it('renders locations from API', async () => {
    renderPage();
    expect(await screen.findByText('Shelf A')).toBeInTheDocument();
    expect(screen.getByText('Drawer 1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renders empty state when API returns no locations', async () => {
    vi.mocked(api.getLocations).mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/locations\.empty|no storage locations/i)).toBeInTheDocument();
  });

  it('opens create modal and calls createLocation on submit', async () => {
    vi.mocked(api.createLocation).mockResolvedValue({
      id: 3,
      name: 'Garage',
      identifier: null,
      spool_count: 0,
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Shelf A');
    await user.click(screen.getByRole('button', { name: /add location|locations\.add/i }));
    const input = screen.getByLabelText(/name|locations\.name/i);
    await user.type(input, 'Garage');
    await user.click(screen.getByRole('button', { name: /save|common\.save/i }));
    await waitFor(() => {
      expect(api.createLocation).toHaveBeenCalledWith({ name: 'Garage' });
    });
    expect(mockShowToast).toHaveBeenCalledWith(expect.stringMatching(/created|locations\.created/i), 'success');
  });

  it('submits create form on Enter key', async () => {
    vi.mocked(api.createLocation).mockResolvedValue({
      id: 3,
      name: 'Garage',
      identifier: null,
      spool_count: 0,
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Shelf A');
    await user.click(screen.getByRole('button', { name: /add location|locations\.add/i }));
    const input = screen.getByLabelText(/name|locations\.name/i);
    await user.type(input, 'Garage{Enter}');
    await waitFor(() => {
      expect(api.createLocation).toHaveBeenCalledWith({ name: 'Garage' });
    });
  });

  it('closes modal on Escape key', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Shelf A');
    await user.click(screen.getByRole('button', { name: /add location|locations\.add/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('edits a location and calls updateLocation', async () => {
    vi.mocked(api.updateLocation).mockResolvedValue({
      id: 2,
      name: 'Drawer 2',
      identifier: null,
      spool_count: 0,
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Drawer 1');
    const editButtons = screen.getAllByTitle(/edit|common\.edit/i);
    await user.click(editButtons[1]);
    const input = screen.getByLabelText(/name|locations\.name/i);
    await user.clear(input);
    await user.type(input, 'Drawer 2');
    await user.click(screen.getByRole('button', { name: /save|common\.save/i }));
    await waitFor(() => {
      expect(api.updateLocation).toHaveBeenCalledWith(2, { name: 'Drawer 2' });
    });
    expect(mockShowToast).toHaveBeenCalledWith(expect.stringMatching(/updated|locations\.updated/i), 'success');
  });

  it('deletes an empty location after confirmation', async () => {
    vi.mocked(api.deleteLocation).mockResolvedValue({ status: 'deleted' });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Drawer 1');
    const row = screen.getByText('Drawer 1').closest('tr');
    expect(row).not.toBeNull();
    await user.click(within(row!).getByTitle(/^Delete$/i));
    await user.click(screen.getAllByRole('button', { name: /^Delete$/i }).pop()!);
    await waitFor(() => {
      expect(api.deleteLocation).toHaveBeenCalledWith(2);
    });
    expect(mockShowToast).toHaveBeenCalledWith(expect.stringMatching(/deleted|locations\.deleted/i), 'success');
  });

  it('blocks delete when spool_count > 0', async () => {
    renderPage();
    await screen.findByText('Shelf A');
    const blockedDelete = screen.getByTitle(/Remove all spools from this location before deleting/i);
    expect(blockedDelete).toBeDisabled();
  });

  it('shows error toast when create returns 409 duplicate name', async () => {
    vi.mocked(api.createLocation).mockRejectedValue(
      new ApiError('A location with this name already exists', 409),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Shelf A');
    await user.click(screen.getByRole('button', { name: /add location|locations\.add/i }));
    await user.type(screen.getByLabelText(/name|locations\.name/i), 'Shelf A');
    await user.click(screen.getByRole('button', { name: /save|common\.save/i }));
    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('A location with this name already exists', 'error');
    });
  });

  it('shows error toast when delete fails', async () => {
    vi.mocked(api.deleteLocation).mockRejectedValue(new Error('Delete failed'));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Drawer 1');
    const row = screen.getByText('Drawer 1').closest('tr');
    expect(row).not.toBeNull();
    await user.click(within(row!).getByTitle(/^Delete$/i));
    await user.click(screen.getAllByRole('button', { name: /^Delete$/i }).pop()!);
    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Delete failed', 'error');
    });
  });
});
