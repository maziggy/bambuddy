/**
 * SKU labels name the colour once (#3400). In Spoolman mode a filament with no
 * colour name gets its subtype as one, so subtype and colour are the same word.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type React from 'react';
import { screen } from '@testing-library/react';
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

// The chart measures its container, which jsdom has none of: give it a size so
// the legend renders.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>();
  const { cloneElement } = await import('react');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement<{ width?: number; height?: number }> }) =>
      cloneElement(children, { width: 800, height: 220 }),
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

// What _map_spoolman_spool makes of a filament named "Black", material "PLA Basic".
const spoolman = spool(1, { brand: 'Bambu Lab', material: 'PLA Basic', subtype: 'Black', color_name: 'Black', color_name_is_synthesized: true });
// Nearly empty: 10 g left at ~99 g/day, so the SKU is in alert.
const low = { ...spoolman, weight_used: 990, label_weight: 1000, created_at: new Date(Date.now() - 10 * 86400000).toISOString() };
const named = spool(2, { brand: 'Polymaker', material: 'PLA', subtype: 'Matte', color_name: 'Red' });

const listItem: ShoppingListItem = {
  id: 61, material: 'PLA Basic', subtype: 'Black', brand: 'Bambu Lab', color_name: 'Black',
  quantity_spools: 1, note: null, status: 'pending', purchased_at: null, added_at: '2025-01-01T00:00:00Z',
};

describe('ForecastPanel SKU labels (#3400)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getUiSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getAuthStatus.mockResolvedValue({ auth_enabled: false, requires_setup: false });
    mocked.getSkuSettings.mockResolvedValue([]);
    mocked.getAllUsageHistory.mockResolvedValue([]);
    mocked.getShoppingList.mockResolvedValue([listItem]);
  });

  it('names a colour that is only the subtype once, and a real colour as before', async () => {
    render(<ForecastPanel spools={[spoolman, named]} />);

    expect(await screen.findAllByText('Bambu Lab PLA Basic Black')).not.toHaveLength(0);
    expect(screen.getAllByText('Polymaker PLA Matte Red')).not.toHaveLength(0);
    expect(screen.queryByText(/Black Black/)).toBeNull();
  });

  it('does the same in the alert banner and the add-to-list dialog', async () => {
    const user = userEvent.setup();
    render(<ForecastPanel spools={[low]} />);

    await user.click(await screen.findByRole('button', { name: /1 alert/ }));
    const banners = screen.getAllByText('Bambu Lab PLA Basic Black');
    await user.click(screen.getAllByTitle('Add to shopping list')[0]);

    expect(screen.getAllByText('Bambu Lab PLA Basic Black').length).toBeGreaterThan(banners.length);
    expect(screen.queryByText(/Black Black/)).toBeNull();
  });

  it('does the same in the chart legend', async () => {
    const { container } = render(<ForecastPanel spools={[low]} />);

    await screen.findAllByText('Bambu Lab PLA Basic Black');
    const legend = container.querySelector('.recharts-legend-wrapper');
    expect(legend).not.toBeNull();
    expect(legend).toHaveTextContent(/^Bambu Lab PLA Basic Black$/);
  });

  it('does the same in the shopping list logistics view', async () => {
    const user = userEvent.setup();
    render(<ForecastPanel spools={[spoolman]} />);
    await user.click(await screen.findByRole('button', { name: /Shopping List/ }));
    await user.click(await screen.findByRole('button', { name: /Logistics/ }));

    expect(await screen.findAllByText('Bambu Lab PLA Basic Black')).not.toHaveLength(0);
    expect(screen.queryByText(/Black Black/)).toBeNull();
  });

  it('does the same in the shopping list', async () => {
    const user = userEvent.setup();
    render(<ForecastPanel spools={[spoolman]} />);
    await user.click(await screen.findByRole('button', { name: /Shopping List/ }));

    expect(await screen.findAllByText('Bambu Lab PLA Basic Black')).not.toHaveLength(0);
    expect(screen.queryByText(/Black Black/)).toBeNull();
  });
});
