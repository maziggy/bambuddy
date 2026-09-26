/**
 * Tests for the SuppliersModal (#2988) — the supplier master list opened from the Inventory toolbar.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SuppliersModal } from '../../components/SuppliersModal';
import { api, ApiError } from '../../api/client';
import { inventorySuppliersQueryKey } from '../../utils/inventoryQueries';

const mockShowToast = vi.fn();

vi.mock('../../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../api/client')>();
  return {
    ApiError: original.ApiError,
    api: {
      getSuppliers: vi.fn(),
      createSupplier: vi.fn(),
      updateSupplier: vi.fn(),
      deleteSupplier: vi.fn(),
    },
  };
});

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

const suppliers = [
  {
    id: 1,
    name: 'Filament24',
    website: 'https://filament24.example',
    customer_number: 'C-1042',
    note: null,
    spool_count: 3,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
  },
  {
    id: 2,
    name: 'PrintStore',
    website: null,
    customer_number: null,
    note: 'B2B only',
    spool_count: 0,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
  },
];

// The list lives in react-query under the shared inventory-suppliers key, so
// the modal needs a client the same way LocationsModal's tests give it one.
function renderModal(open = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <SuppliersModal open={open} onClose={() => {}} />
    </QueryClientProvider>,
  );
  return { ...result, client };
}

describe('SuppliersModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue(suppliers);
  });

  it('lists suppliers with their spool usage counts', async () => {
    renderModal();
    expect(await screen.findByText('Filament24')).toBeInTheDocument();
    expect(screen.getByText('PrintStore')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('C-1042')).toBeInTheDocument();
  });

  it('creates a supplier through the add form and re-reads the list', async () => {
    const newShop = {
      id: 3,
      name: 'NewShop',
      website: null,
      customer_number: null,
      note: null,
      spool_count: 0,
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    };
    (api.createSupplier as ReturnType<typeof vi.fn>).mockResolvedValue(newShop);
    // The row appears because the shared query is invalidated and refetched,
    // not because the component pushed it into a private copy.
    (api.getSuppliers as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(suppliers)
      .mockResolvedValue([...suppliers, newShop]);
    const user = userEvent.setup();
    renderModal();
    await screen.findByText('Filament24');

    await user.click(screen.getByRole('button', { name: /Add/i }));
    await user.type(screen.getByPlaceholderText(/Name \(e\.g\./), 'NewShop');
    const addButtons = screen.getAllByRole('button', { name: /Add/i });
    await user.click(addButtons[addButtons.length - 1]);

    await waitFor(() => {
      expect(api.createSupplier).toHaveBeenCalledWith({
        name: 'NewShop',
        website: null,
        customer_number: null,
        note: null,
      });
    });
    expect(await screen.findByText('NewShop')).toBeInTheDocument();
  });

  it('picks up a supplier created elsewhere when the shared key is invalidated', async () => {
    // What the inventory_changed broadcast does: useWebSocket invalidates
    // inventory-suppliers, and every consumer of that key re-reads (#2988).
    const { client } = renderModal();
    await screen.findByText('Filament24');
    expect(screen.queryByText('Elsewhere')).not.toBeInTheDocument();

    (api.getSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue([
      ...suppliers,
      { ...suppliers[1], id: 9, name: 'Elsewhere' },
    ]);
    await client.invalidateQueries({ queryKey: inventorySuppliersQueryKey });

    expect(await screen.findByText('Elsewhere')).toBeInTheDocument();
  });

  it('says the load failed instead of showing an empty list', async () => {
    // An empty list reads as "no suppliers yet" and invites the user to
    // create a duplicate of one they cannot see.
    (api.getSuppliers as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'));
    renderModal();
    expect(await screen.findByText(/Failed to load suppliers/i)).toBeInTheDocument();
  });

  it('surfaces the delete guard when the supplier is still referenced', async () => {
    (api.deleteSupplier as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError('Supplier is assigned to 3 spool(s)', 409),
    );
    const user = userEvent.setup();
    renderModal();
    await screen.findByText('Filament24');

    // Open the confirm for the referenced supplier (first row).
    const deleteButtons = document.querySelectorAll('button.text-red-500');
    await user.click(deleteButtons[0] as HTMLElement);
    // The confirm message warns about the existing assignments.
    expect(await screen.findByText(/assigned to 3 spool/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Delete/i }));

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith(expect.stringContaining('3'), 'error');
    });
    // Row stays — nothing was deleted.
    expect(screen.getByText('Filament24')).toBeInTheDocument();
  });
});
