/**
 * Wiring tests for BulkEditSpoolsModal: pin the exact patch handed to
 * onApply (which InventoryPage forwards to bulkUpdateSpools). Only ticked
 * fields may appear, so other spools' values are never overwritten, and
 * Spoolman mode must never emit internal-only keys.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { BulkEditSpoolsModal } from '../../components/BulkEditSpoolsModal';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
      orcaCloudStatus: vi.fn().mockResolvedValue({ connected: false }),
      getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
      getBuiltinFilaments: vi.fn().mockResolvedValue([]),
      getSettings: vi.fn().mockResolvedValue({}),
      getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    },
  };
});

const onApply = vi.fn();
const onClose = vi.fn();

const baseProps = {
  isOpen: true,
  selectedCount: 3,
  isPending: false,
  availableLocations: [{ id: 5, name: 'Shelf A' }],
  availableMaterials: [],
  availableSubtypes: [],
  availableBrands: [],
  availableCategories: [],
  availableMaterialNumbers: [],
  availableSlicerFilaments: [],
  availableSlicerFilamentNames: [],
  onClose,
  onApply,
};

/** The row container for a field, found via its exact label text. */
function row(label: string): HTMLElement {
  const lbl = screen.getByText(label, { selector: 'label' });
  return lbl.closest('div.flex.items-start') as HTMLElement;
}

async function tickAndType(user: ReturnType<typeof userEvent.setup>, label: string, value: string) {
  const r = row(label);
  await user.click(within(r).getByRole('checkbox'));
  const input = r.querySelector('textarea, input[type="text"], input[type="number"]') as HTMLElement;
  await user.type(input, value);
}

describe('BulkEditSpoolsModal wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('only the ticked material field ends up in the patch', async () => {
    const user = userEvent.setup();
    render(<BulkEditSpoolsModal {...baseProps} spoolmanMode={false} />);

    await tickAndType(user, 'Material', 'PETG-CF');
    // Close any open dropdown so the Apply button is reachable.
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: /Apply to 3 spools/ }));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenCalledWith({ material: 'PETG-CF' });
  });

  it('ticked but empty fields are left out; numbers are sent as numbers', async () => {
    const user = userEvent.setup();
    render(<BulkEditSpoolsModal {...baseProps} spoolmanMode={false} />);

    // Ticked, left blank -> must not blank the field on every selected spool.
    await user.click(within(row('Brand')).getByRole('checkbox'));
    await tickAndType(user, 'Cost per kg', '19.5');
    await user.click(screen.getByRole('button', { name: /Apply to 3 spools/ }));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenCalledWith({ cost_per_kg: 19.5 });
  });

  it('internal mode: internal-only fields are sent when ticked', async () => {
    const user = userEvent.setup();
    render(<BulkEditSpoolsModal {...baseProps} spoolmanMode={false} />);

    await tickAndType(user, 'Material No.', 'MN-7');
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: /Apply to 3 spools/ }));

    await waitFor(() => expect(onApply).toHaveBeenCalledWith({ material_number: 'MN-7' }));
  });

  it('Spoolman mode: patch holds only the ticked Spoolman-storable fields, no internal-only keys', async () => {
    const user = userEvent.setup();
    render(<BulkEditSpoolsModal {...baseProps} spoolmanMode />);

    await tickAndType(user, 'Material', 'ASA');
    await user.keyboard('{Escape}');
    await tickAndType(user, 'Note', 'restocked');
    await user.click(screen.getByRole('button', { name: /Apply to 3 spools/ }));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    const patch = onApply.mock.calls[0][0] as Record<string, unknown>;
    expect(patch).toEqual({ material: 'ASA', note: 'restocked' });
    for (const k of ['category', 'low_stock_threshold_pct', 'material_number']) {
      expect(patch).not.toHaveProperty(k);
    }
  });

  it('Spoolman mode: ticking and filling EVERY offered field still yields no internal-only key', async () => {
    const user = userEvent.setup();
    render(<BulkEditSpoolsModal {...baseProps} spoolmanMode />);

    const rows = screen.getAllByRole('checkbox').map((cb) => cb.closest('div.flex.items-start') as HTMLElement);
    for (const r of rows) {
      await user.click(within(r).getByRole('checkbox'));
      const input = r.querySelector('textarea, input[type="text"], input[type="number"]') as HTMLInputElement;
      await user.type(input, input.type === 'number' ? '5' : input.placeholder?.includes('RRGGBB') ? 'FF0000' : 'X');
      await user.keyboard('{Escape}');
    }
    await user.click(screen.getByRole('button', { name: /Apply to 3 spools/ }));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    const patch = onApply.mock.calls[0][0] as Record<string, unknown>;
    // Sanity: the fill reached the patch (material, note, ... are present).
    expect(patch).toHaveProperty('material', 'X');
    expect(patch).toHaveProperty('note', 'X');
    for (const k of ['category', 'low_stock_threshold_pct', 'material_number']) {
      expect(patch).not.toHaveProperty(k);
    }
  });

  it('Cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<BulkEditSpoolsModal {...baseProps} />);
    await tickAndType(user, 'Note', 'x');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(onApply).not.toHaveBeenCalled();
  });
});
