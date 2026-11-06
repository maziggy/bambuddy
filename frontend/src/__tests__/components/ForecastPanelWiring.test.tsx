/**
 * Wiring tests for ForecastPanel's inventory-changing actions: add to
 * shopping list, status changes, "received" creating spools via
 * bulkCreateSpools, remove / clear, and per-SKU settings upserts.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ForecastPanel } from '../../components/ForecastPanel';
import { api } from '../../api/client';
import type { InventorySpool, ShoppingListItem } from '../../api/client';

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return {
    ...actual,
    useAuth: () => ({ hasPermission: () => true, hasAnyPermission: () => true, authEnabled: false, user: null }),
  };
});

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSettings: vi.fn(),
      getUiSettings: vi.fn(),
      getAuthStatus: vi.fn(),
      getSkuSettings: vi.fn(),
      getAllUsageHistory: vi.fn(),
      getShoppingList: vi.fn(),
      addToShoppingList: vi.fn(),
      removeFromShoppingList: vi.fn(),
      clearShoppingList: vi.fn(),
      updateShoppingListStatus: vi.fn(),
      bulkCreateSpools: vi.fn(),
      upsertSkuSettings: vi.fn(),
      updateSettings: vi.fn(),
    },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const spool = (id: number, over: Partial<InventorySpool> = {}): InventorySpool => ({
  id,
  material: 'PLA',
  subtype: 'Matte',
  brand: 'Polymaker',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 750,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 100,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: 'manual',
  tag_type: null,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  k_profiles: [],
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  weight_locked: false,
  category: 'Active',
  low_stock_threshold_pct: null,
  ...over,
});

const spools = [spool(1), spool(2)];

const listItem = (over: Partial<ShoppingListItem> = {}): ShoppingListItem => ({
  id: 61,
  material: 'PLA',
  subtype: 'Matte',
  brand: 'Polymaker',
  color_name: 'Red',
  quantity_spools: 3,
  note: 'cheap batch',
  status: 'purchased',
  purchased_at: null,
  added_at: '2025-01-01T00:00:00Z',
  ...over,
});

async function openShoppingList(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /Shopping List/ }));
  return screen.findByText('Clear all');
}

describe('ForecastPanel wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getUiSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getAuthStatus.mockResolvedValue({ auth_enabled: false, requires_setup: false });
    mocked.getSkuSettings.mockResolvedValue([]);
    mocked.getAllUsageHistory.mockResolvedValue([]);
    mocked.getShoppingList.mockResolvedValue([listItem()]);
    mocked.addToShoppingList.mockResolvedValue({});
    mocked.removeFromShoppingList.mockResolvedValue({ status: 'ok' });
    mocked.clearShoppingList.mockResolvedValue({ deleted: 1 });
    mocked.updateShoppingListStatus.mockResolvedValue({});
    mocked.bulkCreateSpools.mockResolvedValue([]);
    mocked.upsertSkuSettings.mockResolvedValue({});
    mocked.updateSettings.mockResolvedValue({});
  });

  it('marking a purchased item received creates quantity_spools spools from the item, then removes it', async () => {
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await openShoppingList(user);

    await user.click(screen.getByTitle(/Mark as received/));

    await waitFor(() => expect(mocked.bulkCreateSpools).toHaveBeenCalledTimes(1));
    expect(mocked.updateShoppingListStatus).toHaveBeenCalledWith(61, 'received');
    const [base, qty] = mocked.bulkCreateSpools.mock.calls[0];
    expect(qty).toBe(3);
    expect(base).toMatchObject({
      material: 'PLA',
      subtype: 'Matte',
      brand: 'Polymaker',
      color_name: 'Red',
      note: 'cheap batch',
      // average label weight of the SKU's existing spools
      label_weight: 750,
      weight_used: 0,
      category: 'Stock',
      data_origin: 'manual',
    });
    await waitFor(() => expect(mocked.removeFromShoppingList).toHaveBeenCalledWith(61));
  });

  it('marking a pending item purchased only updates its status (no spools created)', async () => {
    mocked.getShoppingList.mockResolvedValue([listItem({ id: 62, status: 'pending' })]);
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await openShoppingList(user);

    await user.click(screen.getByTitle('Mark as purchased'));

    await waitFor(() => expect(mocked.updateShoppingListStatus).toHaveBeenCalledWith(62, 'purchased'));
    expect(mocked.bulkCreateSpools).not.toHaveBeenCalled();
    expect(mocked.removeFromShoppingList).not.toHaveBeenCalled();
  });

  it('reset to pending sends status pending for that item', async () => {
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await openShoppingList(user);
    await user.click(screen.getByTitle('Reset to pending'));
    await waitFor(() => expect(mocked.updateShoppingListStatus).toHaveBeenCalledWith(61, 'pending'));
    expect(mocked.bulkCreateSpools).not.toHaveBeenCalled();
  });

  it('remove deletes exactly that item id', async () => {
    mocked.getShoppingList.mockResolvedValue([
      listItem({ id: 70, color_name: 'Red' }),
      listItem({ id: 71, brand: 'Sunlu', color_name: 'Blue' }),
    ]);
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await openShoppingList(user);

    const row = screen.getByText('Sunlu PLA Matte Blue').closest('tr') as HTMLElement;
    await user.click(within(row).getByTitle('Remove'));

    await waitFor(() => expect(mocked.removeFromShoppingList).toHaveBeenCalledTimes(1));
    expect(mocked.removeFromShoppingList).toHaveBeenCalledWith(71);
    expect(mocked.clearShoppingList).not.toHaveBeenCalled();
  });

  it('clear all asks first; cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await openShoppingList(user);
    await user.click(screen.getByText('Clear all'));

    const dialog = screen.getByText('Clear shopping list?').closest('div.fixed') as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByText('Clear shopping list?')).toBeNull();
    expect(mocked.clearShoppingList).not.toHaveBeenCalled();
  });

  it('clear all sends clearShoppingList once confirmed', async () => {
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await openShoppingList(user);
    await user.click(screen.getByText('Clear all'));

    const dialog = screen.getByText('Clear shopping list?').closest('div.fixed') as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Clear all' }));

    await waitFor(() => expect(mocked.clearShoppingList).toHaveBeenCalledTimes(1));
    expect(mocked.removeFromShoppingList).not.toHaveBeenCalled();
  });

  it('add to shopping list sends the SKU from the row with the entered quantity and note', async () => {
    mocked.getShoppingList.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);

    await user.click(await screen.findByTitle('Add to shopping list'));
    const qty = await screen.findByRole('spinbutton');
    await user.clear(qty);
    await user.type(qty, '4');
    await user.type(screen.getByPlaceholderText(/./), 'for project X');
    await user.click(screen.getByRole('button', { name: 'Add 4 spools' }));

    await waitFor(() => expect(mocked.addToShoppingList).toHaveBeenCalledTimes(1));
    expect(mocked.addToShoppingList).toHaveBeenCalledWith({
      material: 'PLA',
      subtype: 'Matte',
      brand: 'Polymaker',
      color_name: 'Red',
      quantity_spools: 4,
      note: 'for project X',
    });
  });

  it('cancelling the add-to-list dialog sends nothing', async () => {
    mocked.getShoppingList.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await user.click(await screen.findByTitle('Add to shopping list'));
    await screen.findByText('Add to Shopping List');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Add to Shopping List')).not.toBeInTheDocument());
    expect(mocked.addToShoppingList).not.toHaveBeenCalled();
  });

  it('SKU lead-time override save upserts the SKU settings with the new lead time', async () => {
    mocked.getShoppingList.mockResolvedValue([]);
    mocked.getSkuSettings.mockResolvedValue([
      {
        id: 5, material: 'PLA', subtype: 'Matte', brand: 'Polymaker', color_name: 'Red',
        lead_time_days: 3, safety_margin_value: 20, safety_margin_unit: 'g', alerts_snoozed: false,
      },
    ]);
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);

    // Expand the SKU row.
    await user.click(await screen.findByText('Polymaker PLA Matte Red'));
    const box = (await screen.findByText('Lead Time Override')).closest('div.rounded-lg') as HTMLElement;
    await user.click(within(box).getByRole('button'));
    const input = within(box).getByRole('spinbutton');
    await user.clear(input);
    await user.type(input, '12');
    await user.click(within(box).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocked.upsertSkuSettings).toHaveBeenCalledTimes(1));
    expect(mocked.upsertSkuSettings.mock.calls[0][0]).toEqual({
      material: 'PLA',
      subtype: 'Matte',
      brand: 'Polymaker',
      color_name: 'Red',
      lead_time_days: 12,
      safety_margin_value: 20,
      safety_margin_unit: 'g',
      alerts_snoozed: false,
    });
  });

  it('SKU lead-time edit cancelled sends nothing', async () => {
    mocked.getShoppingList.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<ForecastPanel spools={spools} />);
    await user.click(await screen.findByText('Polymaker PLA Matte Red'));
    const box = (await screen.findByText('Lead Time Override')).closest('div.rounded-lg') as HTMLElement;
    await user.click(within(box).getByRole('button'));
    await user.type(within(box).getByRole('spinbutton'), '9');
    await user.click(within(box).getByRole('button', { name: 'Cancel' }));
    expect(mocked.upsertSkuSettings).not.toHaveBeenCalled();
  });
});
