/**
 * Tests for the spool dialog's supplier multi-select (#2988).
 */

import { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SupplierSection, type SupplierLinkDraft } from '../../components/spool-form/SupplierSection';
import { api } from '../../api/client';

const mockShowToast = vi.fn();

vi.mock('../../api/client', () => ({
  api: {
    getSuppliers: vi.fn(),
    createSupplier: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

const suppliers = [
  { id: 1, name: 'Filament24', website: null, customer_number: null, note: null, spool_count: 0, created_at: '', updated_at: '' },
  { id: 2, name: 'PrintStore', website: null, customer_number: null, note: null, spool_count: 0, created_at: '', updated_at: '' },
];

function Harness({ initial = [] as SupplierLinkDraft[], onChange = (_: SupplierLinkDraft[]) => {} }) {
  const [links, setLinks] = useState<SupplierLinkDraft[]>(initial);
  return (
    <SupplierSection
      links={links}
      onChange={(next) => {
        setLinks(next);
        onChange(next);
      }}
      currencySymbol="€"
    />
  );
}

describe('SupplierSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSuppliers as ReturnType<typeof vi.fn>).mockResolvedValue(suppliers);
  });

  it('adds a supplier from the dropdown as a chip', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Harness onChange={onChange} />);

    await user.click(screen.getByRole('button', { name: /Add supplier/ }));
    await user.click(await screen.findByRole('button', { name: 'Filament24' }));

    expect(screen.getByText('Filament24')).toBeInTheDocument();
    expect(onChange).toHaveBeenCalledWith([
      expect.objectContaining({ supplier_id: 1, supplier_name: 'Filament24', is_purchase_source: false }),
    ]);
  });

  it('creates a new supplier inline without leaving the dialog', async () => {
    (api.createSupplier as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 3, name: 'NewShop', website: null, customer_number: null, note: null, spool_count: 0, created_at: '', updated_at: '',
    });
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: /Add supplier/ }));
    await user.type(await screen.findByPlaceholderText(/Search suppliers/), 'NewShop');
    await user.click(screen.getByRole('button', { name: /Create "NewShop"/ }));

    await waitFor(() => {
      expect(api.createSupplier).toHaveBeenCalledWith({ name: 'NewShop' });
    });
    expect(screen.getByText('NewShop')).toBeInTheDocument();
  });

  it('keeps at most one purchase source across assignments', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Harness
        initial={[
          { supplier_id: 1, supplier_name: 'Filament24', supplier_article_number: '', quoted_price_per_kg: null, is_purchase_source: true },
          { supplier_id: 2, supplier_name: 'PrintStore', supplier_article_number: '', quoted_price_per_kg: null, is_purchase_source: false },
        ]}
        onChange={onChange}
      />,
    );

    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[1]);

    const last = onChange.mock.calls.at(-1)?.[0] as SupplierLinkDraft[];
    expect(last.find((l) => l.supplier_id === 2)?.is_purchase_source).toBe(true);
    // The previous purchase source was demoted automatically.
    expect(last.find((l) => l.supplier_id === 1)?.is_purchase_source).toBe(false);
  });

  it('removes an assignment via the chip', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Harness
        initial={[
          { supplier_id: 1, supplier_name: 'Filament24', supplier_article_number: '', quoted_price_per_kg: null, is_purchase_source: false },
        ]}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole('button', { name: /Remove/i }));
    expect(onChange).toHaveBeenLastCalledWith([]);
    expect(screen.queryByText('Filament24')).not.toBeInTheDocument();
  });
});
