/**
 * Tests for the SuppliersModal (#2988) — the supplier master list opened from the Inventory toolbar.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SuppliersModal } from '../../components/SuppliersModal';
import { api, ApiError } from '../../api/client';

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

describe('SuppliersModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue(suppliers);
  });

  it('lists suppliers with their spool usage counts', async () => {
    render(<SuppliersModal open onClose={() => {}} />);
    expect(await screen.findByText('Filament24')).toBeInTheDocument();
    expect(screen.getByText('PrintStore')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('C-1042')).toBeInTheDocument();
  });

  it('creates a supplier through the add form', async () => {
    (api.createSupplier as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 3,
      name: 'NewShop',
      website: null,
      customer_number: null,
      note: null,
      spool_count: 0,
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    });
    const user = userEvent.setup();
    render(<SuppliersModal open onClose={() => {}} />);
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

  it('surfaces the delete guard when the supplier is still referenced', async () => {
    (api.deleteSupplier as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError('Supplier is assigned to 3 spool(s)', 409),
    );
    const user = userEvent.setup();
    render(<SuppliersModal open onClose={() => {}} />);
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
