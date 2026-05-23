import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LocationsPage from '../../pages/LocationsPage';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getLocations: vi.fn(),
    createLocation: vi.fn(),
    updateLocation: vi.fn(),
    deleteLocation: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
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
    vi.mocked(api.getLocations).mockResolvedValue(locations);
  });

  it('renders locations from API', async () => {
    renderPage();
    expect(await screen.findByText('Shelf A')).toBeInTheDocument();
    expect(screen.getByText('Drawer 1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('opens create modal and calls createLocation', async () => {
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
    await user.click(screen.getByRole('button', { name: /add location|додати місце|locations.add/i }));
    const input = screen.getByLabelText(/name|назва/i);
    await user.type(input, 'Garage');
    await user.click(screen.getByRole('button', { name: /save|зберегти/i }));
    await waitFor(() => {
      expect(api.createLocation).toHaveBeenCalledWith({ name: 'Garage' });
    });
  });
});
